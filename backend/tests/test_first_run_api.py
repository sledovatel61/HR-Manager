"""First-run pairing tests for the phase-12 pilot (unit, SQLite).

Covers: public honest state, the exactly-once claim flow (session issued, no
password in any response), attempt budget and cancellation, expiry, the
loopback/same-origin guards, the IP rate limit, single-pilot takeover
refusal, the one-shot password finalization and CSRF on it, plus the audit
trail. Races/partial-unique-index behavior is proven on real PostgreSQL in
``test_integration_first_run.py``.
"""

from collections.abc import Iterator
from datetime import timedelta
from uuid import UUID

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.main import create_app
from app.models import (
    AccessGrant,
    AccessGrantScope,
    AuditAction,
    AuditEvent,
    PilotPairing,
    PilotPairingStatus,
    User,
    UserRole,
    WorkRole,
)
from app.routers.first_run import MAX_CLAIM_ATTEMPTS, hash_pairing_code
from app.utils import utc_now

PAIRING_CODE = "K7M2QF"


def issue_pairing(
    db: Session,
    *,
    code: str = PAIRING_CODE,
    work_role: WorkRole = WorkRole.HR,
    surname: str = "Иванова",
    ttl_minutes: int = 15,
) -> PilotPairing:
    pairing = PilotPairing(
        code_hash=hash_pairing_code(code),
        work_role=work_role,
        surname=surname,
        status=PilotPairingStatus.PENDING,
        created_at=utc_now(),
        expires_at=utc_now() + timedelta(minutes=ttl_minutes),
    )
    db.add(pairing)
    db.commit()
    db.refresh(pairing)
    return pairing


@pytest.fixture()
def loopback_client(unit_settings: Settings, unit_engine: Engine) -> Iterator[TestClient]:
    """TestClient with a loopback base URL (Host: 127.0.0.1).

    The claim endpoint refuses non-loopback Hosts; the shared ``client``
    fixture would make every claim test accidentally hit that guard.
    """
    app = create_app(unit_settings, engine=unit_engine)
    with TestClient(app, base_url="http://127.0.0.1") as test_client:
        yield test_client


def _claim(client: TestClient, code: str = PAIRING_CODE) -> httpx.Response:
    return client.post(
        "/setup/first-run/claim",
        json={"code": code},
        headers={"Origin": "http://127.0.0.1"},
    )


def test_state_honest_empty_and_pending(loopback_client: TestClient, db_session: Session) -> None:
    body = loopback_client.get("/setup/first-run/state").json()
    assert body == {
        "pending": False,
        "pending_work_role": None,
        "pending_expires_in_seconds": None,
        "fresh_install": True,
        "pilot_owner_exists": False,
    }
    issue_pairing(db_session, work_role=WorkRole.MANAGER)
    body = loopback_client.get("/setup/first-run/state").json()
    assert body["pending"] is True
    assert body["pending_work_role"] == "manager"
    assert 0 < body["pending_expires_in_seconds"] <= 900
    assert body["fresh_install"] is True
    # The state never carries code material or the surname.
    assert "surname" not in body
    assert "code" not in body


def test_claim_creates_single_pilot_owner_with_session(
    loopback_client: TestClient, db_session: Session
) -> None:
    pairing = issue_pairing(db_session, work_role=WorkRole.ADMIN, surname="Петров")
    response = _claim(loopback_client)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["must_set_password"] is True
    assert body["user"]["full_name"] == "Петров"
    assert body["user"]["work_role"] == "admin"
    assert body["user"]["role"] == "admin"
    assert body["user"]["password_is_bootstrap"] is True
    assert body["user"]["username"].startswith("petrov-pilot-")
    # No secret material anywhere in the response.
    assert "password" not in body
    assert "code" not in body and "hash" not in response.text

    pilot = db_session.get(User, UUID(body["user"]["id"]))
    assert pilot is not None and pilot.role == UserRole.ADMIN
    assert pilot.password_is_bootstrap is True
    # The pairing is consumed exactly once and its PII copy is erased.
    db_session.refresh(pairing)
    assert pairing.status == PilotPairingStatus.CLAIMED
    assert pairing.surname == ""

    grant = db_session.execute(
        select(AccessGrant).where(AccessGrant.user_id == pilot.id)
    ).scalar_one()
    assert grant.scope == AccessGrantScope.PILOT_FULL_ACCESS
    assert grant.granted_by_user_id is None  # self-service issuance, audited

    # The session works: /auth/me is authorized and carries the CSRF token.
    me = loopback_client.get("/auth/me")
    assert me.status_code == 200
    assert me.json()["user"]["id"] == str(pilot.id)

    actions = [
        event.action
        for event in db_session.execute(select(AuditEvent).order_by(AuditEvent.created_at))
        .scalars()
        .all()
    ]
    assert AuditAction.PILOT_PAIRING_CLAIMED in actions
    assert AuditAction.PILOT_USER_CREATED in actions
    assert AuditAction.PILOT_ACCESS_GRANTED in actions
    # Audit detail never carries the surname or the code.
    for event in db_session.execute(select(AuditEvent)).scalars():
        assert "Петров" not in (event.details or "")
        assert PAIRING_CODE not in (event.details or "")


def test_claim_wrong_code_attempts_and_cancel(
    loopback_client: TestClient, db_session: Session
) -> None:
    pairing = issue_pairing(db_session)
    for _ in range(MAX_CLAIM_ATTEMPTS):
        response = _claim(loopback_client, code="ZZZZZZ")
        assert response.status_code == 403
    db_session.refresh(pairing)
    assert pairing.attempts == MAX_CLAIM_ATTEMPTS
    # One more wrong try cancels the pairing outright; a later CORRECT code
    # then finds no active installation (410) — the installer must re-issue.
    response = _claim(loopback_client, code="ZZZZZZ")
    assert response.status_code == 429
    db_session.refresh(pairing)
    assert pairing.status == PilotPairingStatus.CANCELLED
    response = _claim(loopback_client)
    assert response.status_code == 410


def test_claim_lowercase_code_is_normalized(
    loopback_client: TestClient, db_session: Session
) -> None:
    issue_pairing(db_session, code="aB3k9f")
    assert _claim(loopback_client, code="AB3K9F").status_code == 200


def test_claim_expired_pairing_is_410_and_cancelled(
    loopback_client: TestClient, db_session: Session
) -> None:
    pairing = issue_pairing(db_session, ttl_minutes=-1)
    response = _claim(loopback_client)
    assert response.status_code == 410
    db_session.refresh(pairing)
    assert pairing.status == PilotPairingStatus.CANCELLED


def test_claim_refused_when_installation_has_users(
    loopback_client: TestClient, db_session: Session
) -> None:
    from tests.conftest import make_user

    make_user(db_session, username="someone", role=UserRole.HR)
    issue_pairing(db_session)
    response = _claim(loopback_client)
    assert response.status_code == 409
    body = loopback_client.get("/setup/first-run/state").json()
    assert body["fresh_install"] is False
    assert body["pilot_owner_exists"] is False


def test_second_claim_never_takes_over(loopback_client: TestClient, db_session: Session) -> None:
    issue_pairing(db_session)
    assert _claim(loopback_client).status_code == 200
    # A fresh pairing could not create a second owner: users exist now.
    issue_pairing(db_session, code="QQQQQQ")
    response = _claim(loopback_client, code="QQQQQQ")
    assert response.status_code == 409


def test_claim_requires_loopback_host_and_same_origin(
    loopback_client: TestClient, db_session: Session
) -> None:
    issue_pairing(db_session)
    # Non-loopback Host is refused even with a correct code.
    response = loopback_client.post(
        "/setup/first-run/claim",
        json={"code": PAIRING_CODE},
        headers={"Origin": "http://127.0.0.1", "Host": "evil.example.com"},
    )
    assert response.status_code == 403
    # Cross-site Origin is refused (drive-by CSRF without preflight).
    response = loopback_client.post(
        "/setup/first-run/claim",
        json={"code": PAIRING_CODE},
        headers={"Origin": "http://evil.example.com"},
    )
    assert response.status_code == 403
    # The same-origin path still works after both rejections.
    assert _claim(loopback_client).status_code == 200


def test_claim_rate_limited_per_ip(loopback_client: TestClient, db_session: Session) -> None:
    # No pending pairing: every attempt returns 410 but still counts against
    # the per-IP window (10 per 15 minutes by default).
    for _ in range(10):
        assert _claim(loopback_client).status_code == 410
    response = _claim(loopback_client)
    assert response.status_code == 429


def test_password_finalization_one_shot_and_csrf(
    loopback_client: TestClient, db_session: Session
) -> None:
    issue_pairing(db_session)
    claim = _claim(loopback_client)
    csrf = claim.json()["csrf_token"]

    weak = loopback_client.put(
        "/setup/first-run/password",
        json={"password": "short"},
        headers={"X-CSRF-Token": csrf},
    )
    assert weak.status_code == 422

    # CSRF is enforced for the mutation (session exists after the claim).
    no_csrf = loopback_client.put(
        "/setup/first-run/password",
        json={"password": "Владелец-2026-Пароль"},
    )
    assert no_csrf.status_code == 403

    done = loopback_client.put(
        "/setup/first-run/password",
        json={"password": "Владелец-2026-Пароль"},
        headers={"X-CSRF-Token": csrf},
    )
    assert done.status_code == 200, done.text
    assert done.json()["password_is_bootstrap"] is False

    # The endpoint is closed after the first successful set.
    again = loopback_client.put(
        "/setup/first-run/password",
        json={"password": "Владелец-2026-Другой"},
        headers={"X-CSRF-Token": csrf},
    )
    assert again.status_code == 403

    # The new password authenticates via the normal login (the generated one
    # was never returned anywhere).
    login = loopback_client.post(
        "/auth/login",
        json={"username": done.json()["username"], "password": "Владелец-2026-Пароль"},
    )
    assert login.status_code == 200, login.text


def test_setup_state_reports_mode_and_password_need(
    loopback_client: TestClient, db_session: Session
) -> None:
    issue_pairing(db_session, work_role=WorkRole.HR)
    claim = _claim(loopback_client)
    pilot_user_id = claim.json()["user"]["id"]
    state = loopback_client.get("/setup/state").json()
    assert state["pilot_exists"] is True
    assert state["work_role"] == "hr"
    assert state["needs_password"] is True

    csrf = claim.json()["csrf_token"]
    loopback_client.put(
        "/setup/first-run/password",
        json={"password": "Владелец-2026-Пароль"},
        headers={"X-CSRF-Token": csrf},
    )
    state = loopback_client.get("/setup/state").json()
    assert state["needs_password"] is False
    assert state["pilot_grant_active"] is True
    assert pilot_user_id


def test_unknown_endpoints_never_leak(loopback_client: TestClient) -> None:
    response = loopback_client.post("/setup/first-run/claim", json={"code": "ABC"})
    assert response.status_code == 422  # schema validation, no internals
    assert PAIRING_CODE not in response.text
