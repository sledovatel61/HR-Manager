"""Integration tests for external channels (real PostgreSQL + stub servers).

Telegram is stubbed with a real HTTP server (same Bot API wire format);
SMTP is stubbed with a real TCP server speaking minimal SMTP. No real
chats, credentials or personal data are used anywhere here.
"""

import email
import json
import os
import socketserver
import threading
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from app.config import Settings
from app.main import create_app
from app.models import (
    DeliveryChannel,
    DeliveryStatus,
    NotificationDeliveryAttempt,
    NotificationOutbox,
    NotificationPreference,
    NotificationType,
    TelegramLink,
    User,
    UserEmail,
    UserRole,
)
from app.notification_service import schedule, schedule_fan_out
from app.worker import claim_batch, process_external_row, process_row, recover_stale_leases
from tests.conftest import FIXTURE_PASSWORD, make_user

RUN_INTEGRATION = os.environ.get("TEST_DATABASE_URL") is not None
NOW = datetime(2026, 9, 4, 12, 0, 0, tzinfo=UTC)

pytestmark = pytest.mark.integration


# --- Telegram HTTP stub --------------------------------------------------------


class TelegramStub:
    """Scripted Bot API stub (real HTTP, ephemeral port)."""

    def __init__(self) -> None:
        self.scripts: dict[str, list[tuple[int, dict]]] = {}
        self.requests: list[tuple[str, dict]] = []
        self._lock = threading.Lock()
        handler = self._handler()
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def _handler(self) -> type[BaseHTTPRequestHandler]:
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", "0"))
                raw = self.rfile.read(length) if length else b"{}"
                try:
                    payload = json.loads(raw.decode("utf-8"))
                except ValueError:
                    payload = {}
                method = self.path.rsplit("/", 1)[-1]
                with stub._lock:
                    stub.requests.append((method, payload if isinstance(payload, dict) else {}))
                    queue = stub.scripts.get(method, [])
                    status, body = queue.pop(0) if queue else (200, {"ok": True, "result": {}})
                data = json.dumps(body).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args: object) -> None:
                pass

        return Handler

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def script(self, method: str, responses: list[tuple[int, dict]]) -> None:
        with self._lock:
            self.scripts[method] = list(responses)

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)


@pytest.fixture()
def telegram_stub() -> Iterator[TelegramStub]:
    stub = TelegramStub()
    yield stub
    stub.close()


# --- SMTP TCP stub -------------------------------------------------------------


class SmtpStub:
    """Minimal real-socket SMTP stub (plaintext, scriptable RCPT verdict)."""

    def __init__(self, *, rcpt_code: int = 250) -> None:
        self.rcpt_code = rcpt_code
        self.mail_from: list[str] = []
        self.rcpt_to: list[str] = []
        self.data_blocks: list[bytes] = []
        self.connections = 0
        self._lock = threading.Lock()
        stub = self

        class Handler(socketserver.StreamRequestHandler):
            def handle(self) -> None:
                with stub._lock:
                    stub.connections += 1
                self.wfile.write(b"220 stub ESMTP ready\r\n")
                self.wfile.flush()
                data_mode = False
                data_lines: list[bytes] = []
                while True:
                    line = self.rfile.readline(65536)
                    if not line:
                        return
                    if data_mode:
                        if line in (b".\r\n", b".\n"):
                            with stub._lock:
                                stub.data_blocks.append(b"".join(data_lines))
                            data_mode = False
                            data_lines = []
                            self.wfile.write(b"250 OK queued\r\n")
                            self.wfile.flush()
                        else:
                            data_lines.append(line)
                        continue
                    upper = line[:4].upper()
                    if upper == b"EHLO" or upper == b"HELO":
                        self.wfile.write(b"250-stub greets\r\n250 8BITMIME\r\n")
                    elif upper == b"MAIL":
                        with stub._lock:
                            stub.mail_from.append(line.decode("utf-8", "replace"))
                        self.wfile.write(b"250 OK\r\n")
                    elif upper == b"RCPT":
                        with stub._lock:
                            stub.rcpt_to.append(line.decode("utf-8", "replace"))
                        if stub.rcpt_code == 250:
                            self.wfile.write(b"250 OK\r\n")
                        else:
                            self.wfile.write(b"550 No such mailbox\r\n")
                    elif upper == b"DATA":
                        data_mode = True
                        self.wfile.write(b"354 End with .\r\n")
                    elif upper == b"RSET" or upper == b"NOOP":
                        self.wfile.write(b"250 OK\r\n")
                    elif upper == b"QUIT":
                        self.wfile.write(b"221 Bye\r\n")
                        self.wfile.flush()
                        return
                    else:
                        self.wfile.write(b"502 Unimplemented\r\n")
                    self.wfile.flush()

        self._server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)


@pytest.fixture()
def smtp_stub() -> Iterator[SmtpStub]:
    stub = SmtpStub()
    yield stub
    stub.close()


# --- fixtures ------------------------------------------------------------------


def _clean_channels(db: Session) -> None:
    db.execute(
        text(
            "TRUNCATE TABLE telegram_start_events, telegram_poll_state, telegram_link_tokens, "
            "telegram_links, user_emails, notification_delivery_attempts, notification_outbox, "
            "notifications, notification_preferences, reminders, access_grants CASCADE"
        )
    )
    db.commit()


@pytest.fixture()
def channel_settings(
    integration_url: str, telegram_stub: TelegramStub, smtp_stub: SmtpStub
) -> Settings:
    return Settings.model_validate(
        {
            "APP_ENV": "test",
            "SECRET_KEY": "integration-test-secret-key",
            "DATABASE_URL": integration_url,
            "WORKER_MAX_ATTEMPTS": "3",
            "WORKER_BACKOFF_BASE_S": "60",
            "WORKER_BACKOFF_CAP_S": "3600",
            "WORKER_LEASE_SECONDS": "120",
            "TELEGRAM_ENABLED": "true",
            "TELEGRAM_BOT_TOKEN": "stub-token",
            "TELEGRAM_BOT_USERNAME": "hr_test_bot",
            "TELEGRAM_API_BASE_URL": telegram_stub.base_url,
            "SMTP_ENABLED": "true",
            "SMTP_HOST": "127.0.0.1",
            "SMTP_PORT": str(smtp_stub.port),
            "SMTP_ENCRYPTION": "none",
            "SMTP_FROM_ADDRESS": "noreply@example.com",
            "SMTP_FROM_NAME": "HR Manager",
            "INTEGRATION_RATE_LIMIT": "1000",
        }
    )


@pytest.fixture()
def channel_client(pg_engine: Engine, channel_settings: Settings) -> Iterator[TestClient]:
    with pg_engine.begin() as connection:
        connection.execute(
            text(
                "TRUNCATE TABLE audit_log, event_history, events, candidate_transfers, "
                "candidate_interactions, candidates, user_sessions, users, "
                "telegram_start_events, telegram_poll_state, telegram_link_tokens, "
                "telegram_links, user_emails, notification_delivery_attempts, "
                "notification_outbox, notifications, notification_preferences, "
                "reminders, access_grants RESTART IDENTITY CASCADE"
            )
        )
    app = create_app(channel_settings, engine=pg_engine)
    with TestClient(app) as test_client:
        yield test_client


def _login(client: TestClient, username: str) -> str:
    response = client.post("/auth/login", json={"username": username, "password": FIXTURE_PASSWORD})
    assert response.status_code == 200, response.text
    return response.json()["csrf_token"]


def _link_telegram(db: Session, username: str, chat_id: int = 777000111) -> User:
    user = make_user(db, username=username, role=UserRole.HR)
    db.add(TelegramLink(user_id=user.id, chat_id=chat_id, linked_at=NOW))
    db.add(
        NotificationPreference(
            user_id=user.id,
            timezone="Europe/Moscow",
            quiet_hours_start="21:00",
            quiet_hours_end="08:00",
            workdays=[1, 2, 3, 4, 5],
            enabled_types=["system_alert"],
            enabled_channels=["in_app", "telegram"],
            telegram_opt_in=True,
            telegram_consent_granted=True,
            telegram_consent_at=NOW,
            telegram_consent_source="web-ui",
            telegram_consent_policy_version="phase9-v1",
        )
    )
    db.commit()
    return user


def _verify_email(db: Session, username: str, address: str = "hr@example.com") -> User:
    user = make_user(db, username=username, role=UserRole.HR)
    db.add(UserEmail(user_id=user.id, email=address, verified_at=NOW))
    db.add(
        NotificationPreference(
            user_id=user.id,
            timezone="Europe/Moscow",
            quiet_hours_start="21:00",
            quiet_hours_end="08:00",
            workdays=[1, 2, 3, 4, 5],
            enabled_types=["system_alert"],
            enabled_channels=["in_app", "email"],
            email_opt_in=True,
            email_consent_granted=True,
            email_consent_at=NOW,
            email_consent_source="web-ui",
            email_consent_policy_version="phase9-v1",
        )
    )
    db.commit()
    return user


def _queue(
    db: Session, user_id: UUID, channel: DeliveryChannel, scheduled_at: datetime = NOW
) -> NotificationOutbox:
    row = schedule(
        db,
        recipient_user_id=user_id,
        channel=channel,
        type_=NotificationType.SYSTEM_ALERT,
        title="Проверка канала",
        body="Тело проверки",
        dedupe_key=f"pg-ext:{uuid4().hex}",
        scheduled_at=scheduled_at,
    )
    assert row is not None
    db.commit()
    return row


# --- Telegram delivery -----------------------------------------------------------


def test_telegram_delivery_end_to_end_via_stub(
    pg_db: Session, channel_settings: Settings, telegram_stub: TelegramStub
) -> None:
    _clean_channels(pg_db)
    telegram_stub.script("sendMessage", [(200, {"ok": True, "result": {"message_id": 4242}})])
    user = _link_telegram(pg_db, "pg-tg-ok")
    _queue(pg_db, user.id, DeliveryChannel.TELEGRAM)
    claimed = claim_batch(pg_db, now=NOW, batch_size=5, lease_seconds=120)
    assert len(claimed) == 1
    assert process_row(pg_db, claimed[0], settings=channel_settings, now=NOW) == "accepted"

    row = pg_db.get(NotificationOutbox, claimed[0].id)
    assert row is not None and row.status == DeliveryStatus.ACCEPTED
    assert row.provider_message_id == "4242"
    assert row.delivered_at is None  # accepted is never delivered/read
    attempts = (
        pg_db.execute(
            NotificationDeliveryAttempt.__table__.select().where(
                NotificationDeliveryAttempt.outbox_id == row.id
            )
        )
        .mappings()
        .all()
    )
    assert len(attempts) == 1 and attempts[0]["outcome"] == "accepted"
    assert len(telegram_stub.requests) == 1
    method, payload = telegram_stub.requests[0]
    assert method == "sendMessage"
    assert payload["chat_id"] == 777000111
    assert "Проверка канала" in payload["text"]
    assert "не означает прочтение" in payload["text"]


def test_telegram_429_then_success_via_stub(
    pg_db: Session, channel_settings: Settings, telegram_stub: TelegramStub
) -> None:
    _clean_channels(pg_db)
    telegram_stub.script(
        "sendMessage",
        [
            (429, {"ok": False, "error_code": 429, "parameters": {"retry_after": 5}}),
            (200, {"ok": True, "result": {"message_id": 9}}),
        ],
    )
    user = _link_telegram(pg_db, "pg-tg-429")
    _queue(pg_db, user.id, DeliveryChannel.TELEGRAM)
    claimed = claim_batch(pg_db, now=NOW, batch_size=5, lease_seconds=120)
    assert process_row(pg_db, claimed[0], settings=channel_settings, now=NOW) == "queued"
    row = pg_db.get(NotificationOutbox, claimed[0].id)
    assert row is not None and row.next_attempt_at == NOW + timedelta(seconds=5)

    later = NOW + timedelta(seconds=6)
    claimed = claim_batch(pg_db, now=later, batch_size=5, lease_seconds=120)
    assert len(claimed) == 1
    assert process_row(pg_db, claimed[0], settings=channel_settings, now=later) == "accepted"
    pg_db.refresh(row)
    assert row.attempts == 2 and row.provider_message_id == "9"


def test_telegram_confirm_end_to_end_via_stub(
    channel_client: TestClient, pg_db: Session, telegram_stub: TelegramStub
) -> None:
    from urllib.parse import parse_qs, urlparse

    make_user(pg_db, username="pg-link", role=UserRole.HR)
    csrf = _login(channel_client, "pg-link")
    deep_link = channel_client.post(
        "/integrations/telegram/link-code", headers={"X-CSRF-Token": csrf}
    ).json()["deep_link"]
    raw = parse_qs(urlparse(deep_link).query)["start"][0]
    telegram_stub.script(
        "getUpdates",
        [
            (
                200,
                {
                    "ok": True,
                    "result": [
                        {
                            "update_id": 9001,
                            "message": {"chat": {"id": 314159}, "text": f"/start {raw}"},
                        }
                    ],
                },
            )
        ],
    )
    response = channel_client.post("/integrations/telegram/confirm", headers={"X-CSRF-Token": csrf})
    assert response.status_code == 200, response.text
    assert response.json()["linked"] is True


def test_confirm_single_use_race_two_threads(
    channel_client: TestClient, pg_db: Session, telegram_stub: TelegramStub
) -> None:
    from urllib.parse import parse_qs, urlparse

    make_user(pg_db, username="pg-race", role=UserRole.HR)
    csrf = _login(channel_client, "pg-race")
    deep_link = channel_client.post(
        "/integrations/telegram/link-code", headers={"X-CSRF-Token": csrf}
    ).json()["deep_link"]
    raw = parse_qs(urlparse(deep_link).query)["start"][0]
    telegram_stub.script(
        "getUpdates",
        [
            (
                200,
                {
                    "ok": True,
                    "result": [
                        {
                            "update_id": 9100,
                            "message": {"chat": {"id": 271828}, "text": f"/start {raw}"},
                        }
                    ],
                },
            )
        ]
        * 4,
    )
    cookies = dict(channel_client.cookies)
    app = channel_client.app
    results: list[int] = []

    def confirm_once() -> None:
        with TestClient(app) as thread_client:
            thread_client.cookies.update(cookies)
            response = thread_client.post(
                "/integrations/telegram/confirm", headers={"X-CSRF-Token": csrf}
            )
            results.append(response.status_code)

    threads = [threading.Thread(target=confirm_once) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert sorted(results) == [200, 409]


def test_concurrent_external_delivery_no_duplicate(
    pg_db: Session, pg_engine: Engine, channel_settings: Settings, telegram_stub: TelegramStub
) -> None:
    from sqlalchemy.orm import Session as OrmSession

    _clean_channels(pg_db)
    telegram_stub.script("sendMessage", [(200, {"ok": True, "result": {"message_id": 1}})])
    user = _link_telegram(pg_db, "pg-dedupe")
    queued = _queue(pg_db, user.id, DeliveryChannel.TELEGRAM)
    claimed = claim_batch(pg_db, now=NOW, batch_size=5, lease_seconds=120)
    assert len(claimed) == 1
    results: list[str] = []

    def process_once() -> None:
        with OrmSession(pg_engine) as session:
            results.append(
                process_external_row(session, queued.id, settings=channel_settings, now=NOW)
            )

    threads = [threading.Thread(target=process_once) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert sorted(results) == ["accepted", "sending"]
    # Exactly one provider call and one attempt — no duplicate delivery.
    assert len(telegram_stub.requests) == 1
    attempts = (
        pg_db.execute(
            NotificationDeliveryAttempt.__table__.select().where(
                NotificationDeliveryAttempt.outbox_id == queued.id
            )
        )
        .mappings()
        .all()
    )
    assert len(attempts) == 1


def test_external_lease_recovery(
    pg_db: Session, channel_settings: Settings, telegram_stub: TelegramStub
) -> None:
    _clean_channels(pg_db)
    telegram_stub.script("sendMessage", [(200, {"ok": True, "result": {"message_id": 3}})])
    user = _link_telegram(pg_db, "pg-lease")
    _queue(pg_db, user.id, DeliveryChannel.TELEGRAM)
    claimed = claim_batch(pg_db, now=NOW, batch_size=5, lease_seconds=120)
    assert len(claimed) == 1
    # Crash: lease expires without processing.
    stale_now = NOW + timedelta(seconds=200)
    assert recover_stale_leases(pg_db, now=stale_now, lease_seconds=120) == 1
    claimed = claim_batch(pg_db, now=stale_now, batch_size=5, lease_seconds=120)
    assert len(claimed) == 1
    assert process_row(pg_db, claimed[0], settings=channel_settings, now=stale_now) == "accepted"


def test_cancel_race_wins_over_external_send(
    pg_db: Session, channel_settings: Settings, telegram_stub: TelegramStub
) -> None:
    _clean_channels(pg_db)
    user = _link_telegram(pg_db, "pg-cancelrace")
    queued = _queue(pg_db, user.id, DeliveryChannel.TELEGRAM)
    claimed = claim_batch(pg_db, now=NOW, batch_size=5, lease_seconds=120)
    assert len(claimed) == 1
    row = pg_db.get(NotificationOutbox, queued.id)
    assert row is not None
    row.status = DeliveryStatus.CANCELLED
    row.cancelled_at = NOW
    row.lease_expires_at = None
    pg_db.commit()
    assert process_external_row(pg_db, queued.id, settings=channel_settings, now=NOW) == "cancelled"
    assert telegram_stub.requests == []


# --- SMTP delivery ---------------------------------------------------------------


def test_smtp_delivery_end_to_end_via_stub(
    pg_db: Session, channel_settings: Settings, smtp_stub: SmtpStub
) -> None:
    _clean_channels(pg_db)
    user = _verify_email(pg_db, "pg-smtp-ok")
    _queue(pg_db, user.id, DeliveryChannel.EMAIL)
    claimed = claim_batch(pg_db, now=NOW, batch_size=5, lease_seconds=120)
    assert process_row(pg_db, claimed[0], settings=channel_settings, now=NOW) == "accepted"
    row = pg_db.get(NotificationOutbox, claimed[0].id)
    assert row is not None and row.status == DeliveryStatus.ACCEPTED
    assert smtp_stub.connections == 1
    assert len(smtp_stub.data_blocks) == 1
    parsed = email.message_from_bytes(smtp_stub.data_blocks[0], policy=email.policy.default)
    assert "Проверка канала" in str(parsed["Subject"])
    assert parsed["To"] == "hr@example.com"
    assert "noreply@example.com" in str(parsed["From"])
    part = parsed.get_body(preferencelist=("plain",))
    assert part is not None
    assert "Тело проверки" in part.get_content()
    assert "не означает прочтение" in part.get_content()


def test_smtp_recipient_refused_is_permanent(pg_db: Session, channel_settings: Settings) -> None:
    refusing = SmtpStub(rcpt_code=550)
    try:
        _clean_channels(pg_db)
        user = _verify_email(pg_db, "pg-smtp-550")
        _queue(pg_db, user.id, DeliveryChannel.EMAIL)
        claimed = claim_batch(pg_db, now=NOW, batch_size=5, lease_seconds=120)
        settings = Settings.model_validate(
            {
                "APP_ENV": "test",
                "SECRET_KEY": "integration-test-secret-key",
                "DATABASE_URL": os.environ["TEST_DATABASE_URL"],
                "WORKER_MAX_ATTEMPTS": "3",
                "SMTP_ENABLED": "true",
                "SMTP_HOST": "127.0.0.1",
                "SMTP_PORT": str(refusing.port),
                "SMTP_ENCRYPTION": "none",
                "SMTP_FROM_ADDRESS": "noreply@example.com",
            }
        )
        assert process_row(pg_db, claimed[0], settings=settings, now=NOW) == "failed"
        row = pg_db.get(NotificationOutbox, claimed[0].id)
        assert row is not None and row.error_class == "smtp_recipient_refused"
    finally:
        refusing.close()


def test_admin_smtp_check_against_stub(
    channel_client: TestClient, pg_db: Session, smtp_stub: SmtpStub
) -> None:
    make_user(pg_db, username="pg-admin", role=UserRole.ADMIN)
    csrf = _login(channel_client, "pg-admin")
    response = channel_client.post("/admin/integrations/smtp/check", headers={"X-CSRF-Token": csrf})
    assert response.status_code == 200, response.text
    assert response.json()["ok"] is True
    assert smtp_stub.connections >= 1
    assert smtp_stub.data_blocks == []  # the check sends no mail


def test_fan_out_on_postgres_creates_all_channels(
    pg_db: Session, channel_settings: Settings
) -> None:
    _clean_channels(pg_db)
    user = _link_telegram(pg_db, "pg-fanout")
    pg_db.add(UserEmail(user_id=user.id, email="fanout@example.com", verified_at=NOW))
    from sqlalchemy import update as _update

    pg_db.execute(
        _update(NotificationPreference)
        .where(NotificationPreference.user_id == user.id)
        .values(email_opt_in=True, email_consent_granted=True)
    )
    pg_db.commit()
    rows = schedule_fan_out(
        pg_db,
        type_=NotificationType.SYSTEM_ALERT,
        recipient_user_id=user.id,
        dedupe_key="pg-fanout:1",
        scheduled_at=NOW,
        settings=channel_settings,
    )
    pg_db.commit()
    assert len(rows) == 3
    channels = sorted(r.channel.value for r in rows if r is not None)
    assert channels == ["email", "in_app", "telegram"]
    # Repeat is deduplicated on PostgreSQL unique indexes.
    again = schedule_fan_out(
        pg_db,
        type_=NotificationType.SYSTEM_ALERT,
        recipient_user_id=user.id,
        dedupe_key="pg-fanout:1",
        scheduled_at=NOW,
        settings=channel_settings,
    )
    assert all(r is None for r in again)
