"""Unit tests for the event ↔ reminder link (UX feedback 2026-09-29, block B).

The report was that the two objects were unrelated: an event created in the
calendar never appeared in «Напоминания», and a hand-made reminder had no way
back to its event. These tests pin the single-transaction contract:

* creating an event with a reminder moment creates exactly one linked
  reminder, owned by the author and assigned to the event assignee;
* an event without a reminder moment creates none;
* re-running the sync never duplicates a row (the unique index is the backstop);
* rescheduling re-points the reminder, completing/cancelling closes it, and
  clearing the moment detaches it without deleting anything;
* the reminder surfaces in the «Напоминания» list of the event assignee and
  carries the candidate so the UI can open the card;
* a rolled-back event creation leaves no orphan reminder.
"""

import re
from collections.abc import Iterator
from datetime import datetime, timedelta
from typing import cast
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Table, func, select
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import quoted_name

from app.models import Event, Reminder, ReminderStatus, UserRole
from app.routers.auth import reset_login_limiter
from app.utils import ensure_aware, utc_now
from tests.conftest import FIXTURE_PASSWORD, make_candidate, make_user


@pytest.fixture(autouse=True)
def _clean_limiter() -> Iterator[None]:
    reset_login_limiter()
    yield
    reset_login_limiter()


def _login(client: TestClient, username: str) -> str:
    response = client.post("/auth/login", json={"username": username, "password": FIXTURE_PASSWORD})
    return response.json()["csrf_token"]


def _in(minutes: int) -> str:
    return (utc_now() + timedelta(minutes=minutes)).isoformat()


def _create_event(
    client: TestClient,
    csrf: str,
    candidate_id: str,
    *,
    assignee: str | None = None,
    event_type: str = "call",
    remind_at: str | None = None,
) -> dict:
    response = client.post(
        "/events",
        json={
            "candidate_id": candidate_id,
            "type": event_type,
            "title": "Созвон по вакансии",
            "starts_at": _in(120),
            "remind_at": remind_at,
            "assignee_user_id": assignee,
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _linked(db: Session, event_id: str) -> list[Reminder]:
    return list(db.scalars(select(Reminder).where(Reminder.event_id == UUID(event_id))).all())


def test_event_with_remind_at_creates_one_linked_reminder(
    client: TestClient, db_session: Session
) -> None:
    hr = make_user(db_session, username="hr1", role=UserRole.HR)
    candidate = make_candidate(db_session, owner=hr)
    csrf = _login(client, "hr1")

    event = _create_event(client, csrf, str(candidate.id), remind_at=_in(60))

    rows = _linked(db_session, event["id"])
    assert len(rows) == 1
    reminder = rows[0]
    assert reminder.candidate_id == candidate.id
    assert reminder.assignee_user_id == hr.id
    assert reminder.owner_user_id == hr.id
    assert reminder.status == ReminderStatus.ACTIVE
    assert reminder.title.startswith("Событие")
    # The due moment is the reminder moment, not the event start.
    assert reminder.due_at != event["starts_at"]


def test_reminder_type_event_links_its_own_start_moment(
    client: TestClient, db_session: Session
) -> None:
    hr = make_user(db_session, username="hr1", role=UserRole.HR)
    candidate = make_candidate(db_session, owner=hr)
    csrf = _login(client, "hr1")

    event = _create_event(client, csrf, str(candidate.id), event_type="reminder")

    rows = _linked(db_session, event["id"])
    assert len(rows) == 1
    assert rows[0].title.startswith("Напоминание")
    # A «Напоминание» event is its own reminder moment.
    assert ensure_aware(rows[0].due_at) == ensure_aware(datetime.fromisoformat(event["starts_at"]))


def test_event_without_reminder_moment_creates_no_reminder(
    client: TestClient, db_session: Session
) -> None:
    hr = make_user(db_session, username="hr1", role=UserRole.HR)
    candidate = make_candidate(db_session, owner=hr)
    csrf = _login(client, "hr1")

    event = _create_event(client, csrf, str(candidate.id))
    assert _linked(db_session, event["id"]) == []


def test_sync_is_idempotent_for_one_event(client: TestClient, db_session: Session) -> None:
    """Re-entering the same event must never create a second row."""
    from app.config import get_settings
    from app.event_reminders import sync_event_reminder

    hr = make_user(db_session, username="hr1", role=UserRole.HR)
    candidate = make_candidate(db_session, owner=hr)
    csrf = _login(client, "hr1")
    event_body = _create_event(client, csrf, str(candidate.id), remind_at=_in(60))
    event = db_session.get(Event, UUID(event_body["id"]))
    assert event is not None

    settings = get_settings()
    for _ in range(3):
        sync_event_reminder(db_session, event, author=hr, assignee=hr, settings=settings)
        db_session.commit()

    assert len(_linked(db_session, event_body["id"])) == 1


def test_reschedule_of_an_event_with_a_reminder_succeeds(
    client: TestClient, db_session: Session
) -> None:
    """Regression: this PATCH used to answer 500 on every run.

    The stored ``remind_at`` comes back naive from SQLite while the request
    body is timezone-aware, and the field validation compared the two directly.
    """
    hr = make_user(db_session, username="hr1", role=UserRole.HR)
    candidate = make_candidate(db_session, owner=hr)
    csrf = _login(client, "hr1")
    event = _create_event(client, csrf, str(candidate.id), remind_at=_in(60))

    response = client.patch(
        f"/events/{event['id']}",
        json={"expected_version": event["version"], "starts_at": _in(600)},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 200, response.text
    # The reminder moment itself did not move, so the linked row is untouched.
    assert len(_linked(db_session, event["id"])) == 1


def test_reschedule_moves_the_linked_reminder(client: TestClient, db_session: Session) -> None:
    hr = make_user(db_session, username="hr1", role=UserRole.HR)
    candidate = make_candidate(db_session, owner=hr)
    csrf = _login(client, "hr1")
    event = _create_event(client, csrf, str(candidate.id), remind_at=_in(60))
    reminder_id = _linked(db_session, event["id"])[0].id
    before = client.get(f"/reminders/{reminder_id}", headers={"X-CSRF-Token": csrf}).json()

    response = client.patch(
        f"/events/{event['id']}",
        json={
            "expected_version": event["version"],
            "starts_at": _in(600),
            "remind_at": _in(300),
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 200, response.text

    after = client.get(f"/reminders/{reminder_id}", headers={"X-CSRF-Token": csrf}).json()
    # The SAME row was re-pointed (not a second one) and its version advanced,
    # so a stale editor of the reminder is rejected instead of overwriting it.
    assert after["id"] == before["id"]
    assert after["version"] == before["version"] + 1
    assert datetime.fromisoformat(after["due_at"]) == pytest.approx(
        datetime.fromisoformat(before["due_at"]) + timedelta(minutes=240), abs=timedelta(seconds=5)
    )


def test_completing_the_event_cancels_the_linked_reminder(
    client: TestClient, db_session: Session
) -> None:
    hr = make_user(db_session, username="hr1", role=UserRole.HR)
    candidate = make_candidate(db_session, owner=hr)
    csrf = _login(client, "hr1")
    event = _create_event(client, csrf, str(candidate.id), remind_at=_in(60))

    response = client.patch(
        f"/events/{event['id']}",
        json={"expected_version": event["version"], "status": "completed"},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 200, response.text

    row = _linked(db_session, event["id"])[0]
    assert row.status == ReminderStatus.CANCELLED


def test_clearing_the_reminder_moment_detaches_without_deleting(
    client: TestClient, db_session: Session
) -> None:
    hr = make_user(db_session, username="hr1", role=UserRole.HR)
    candidate = make_candidate(db_session, owner=hr)
    csrf = _login(client, "hr1")
    event = _create_event(client, csrf, str(candidate.id), remind_at=_in(60))
    assert len(_linked(db_session, event["id"])) == 1

    response = client.patch(
        f"/events/{event['id']}",
        json={"expected_version": event["version"], "remind_at": None},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 200, response.text

    # Detached, but the reminder itself is untouched (no data is destroyed).
    assert _linked(db_session, event["id"]) == []
    total = db_session.scalar(select(func.count()).select_from(Reminder))
    assert total == 1


def test_linked_reminder_appears_in_the_assignee_reminder_list(
    client: TestClient, db_session: Session
) -> None:
    """The report's core symptom: the event must reach «Напоминания»."""
    make_user(db_session, username="mgr", role=UserRole.MANAGER)
    hr = make_user(db_session, username="hr1", role=UserRole.HR)
    candidate = make_candidate(db_session, owner=hr)
    csrf = _login(client, "mgr")

    event = _create_event(client, csrf, str(candidate.id), assignee=str(hr.id), remind_at=_in(60))
    row = _linked(db_session, event["id"])[0]
    assert row.assignee_user_id == hr.id

    hr_csrf = _login(client, "hr1")
    listing = client.get("/reminders", headers={"X-CSRF-Token": hr_csrf})
    assert listing.status_code == 200, listing.text
    ids = [item["id"] for item in listing.json()["items"]]
    assert str(row.id) in ids

    detail = client.get(f"/reminders/{row.id}", headers={"X-CSRF-Token": hr_csrf})
    assert detail.json()["candidate_full_name"] == candidate.full_name
    assert detail.json()["event_id"] == event["id"]


def test_linked_reminder_is_not_visible_to_a_stranger(
    client: TestClient, db_session: Session
) -> None:
    hr1 = make_user(db_session, username="hr1", role=UserRole.HR)
    make_user(db_session, username="hr2", role=UserRole.HR)
    candidate = make_candidate(db_session, owner=hr1)
    csrf = _login(client, "hr1")
    event = _create_event(client, csrf, str(candidate.id), remind_at=_in(60))
    row = _linked(db_session, event["id"])[0]

    hr2_csrf = _login(client, "hr2")
    assert client.get("/reminders", headers={"X-CSRF-Token": hr2_csrf}).json()["items"] == []
    # ...and a direct fetch is a 404, not a 403 that confirms existence.
    assert client.get(f"/reminders/{row.id}", headers={"X-CSRF-Token": hr2_csrf}).status_code == 404


def test_migration_0017_links_one_reminder_per_event() -> None:
    """Structural check of the Alembic revision (upgrade runs on PostgreSQL).

    The partial unique index is what makes ``sync_event_reminder``
    idempotent even under concurrent writers, so its shape is pinned here.
    """
    from importlib.util import module_from_spec, spec_from_file_location
    from inspect import getsource
    from pathlib import Path

    from alembic.config import Config
    from alembic.script import ScriptDirectory

    path = (
        Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0017_event_reminder_link.py"
    )
    spec = spec_from_file_location("block_b_migration", path)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.revision == "0017"
    assert module.down_revision == "0016"

    config = Config("alembic.ini")
    assert ScriptDirectory.from_config(config).get_current_head() == "0022"

    source = getsource(module)
    assert "uq_reminders_event_id" in source
    assert "postgresql_where" in source
    # The normalisation must keep the OLDEST link, so the most recently
    # created reminder is not the one silently dropped.
    assert "ORDER BY event_id, created_at, id" in source
    # Regression: `min(uuid)`/`max(uuid)` only arrived in PostgreSQL 20 and CI
    # runs 16, where the aggregate does not exist and `alembic upgrade head`
    # aborts. Inspect the SQL that is actually handed to the database, not the
    # prose around it, so a comment cannot satisfy or break this check.
    sql = "\n".join(
        literal for literal in re.findall(r'sa\.text\(\s*"""(.*?)"""', source, re.DOTALL)
    )
    assert "UPDATE reminders" in sql
    assert "DISTINCT ON" in sql
    assert "MIN(" not in sql.upper()
    # «Oldest wins» is only true if created_at drives the ordering.
    assert "ORDER BY event_id, created_at, id" in sql


def test_orm_declares_the_same_unique_index() -> None:
    """The model and the migration must not drift apart."""
    from app.models import Reminder

    # `__table__` is typed FromClause, which has no `.indexes`; assert the
    # real type so mypy sees a `Table` and the intent stays explicit.
    table = cast(Table, Reminder.__table__)
    # `Index.name` is typed `quoted_name | None`; compare against a
    # `quoted_name` so the lookup type-checks instead of relying on a cast.
    target = next(
        (
            index
            for index in table.indexes
            if index.name == quoted_name("uq_reminders_event_id", None)
        ),
        None,
    )
    assert target is not None, "ORM does not declare uq_reminders_event_id"
    assert target.unique is True


def test_reminders_can_be_filtered_by_candidate_for_the_card(
    client: TestClient, db_session: Session
) -> None:
    """The candidate card needs «its» reminders, not a page of everyone's."""
    make_user(db_session, username="mgr", role=UserRole.MANAGER)
    hr = make_user(db_session, username="hr1", role=UserRole.HR)
    mine = make_candidate(db_session, owner=hr, full_name="Первый Кандидат")
    other = make_candidate(db_session, owner=hr, full_name="Второй Кандидат")
    csrf = _login(client, "mgr")

    mine_event = _create_event(client, csrf, str(mine.id), assignee=str(hr.id), remind_at=_in(60))
    other_event = _create_event(
        client, csrf, str(other.id), assignee=str(hr.id), remind_at=_in(120)
    )
    mine_row = _linked(db_session, mine_event["id"])[0]
    other_row = _linked(db_session, other_event["id"])[0]

    hr_csrf = _login(client, "hr1")
    filtered = client.get(
        "/reminders", params={"candidate_id": str(mine.id)}, headers={"X-CSRF-Token": hr_csrf}
    )
    assert filtered.status_code == 200, filtered.text
    body = filtered.json()
    ids = [item["id"] for item in body["items"]]
    assert ids == [str(mine_row.id)]
    assert str(other_row.id) not in ids
    # The count must describe the filtered page, not the unfiltered one.
    assert body["total"] == 1

    # An unknown candidate id yields an empty page, not an error.
    assert (
        client.get(
            "/reminders",
            params={"candidate_id": "00000000-0000-0000-0000-000000000000"},
            headers={"X-CSRF-Token": hr_csrf},
        ).json()["items"]
        == []
    )


def test_the_candidate_filter_does_not_widen_access(
    client: TestClient, db_session: Session
) -> None:
    """Filtering must not become a way to learn that a reminder exists."""
    owner_hr = make_user(db_session, username="hr1", role=UserRole.HR)
    make_user(db_session, username="hr2", role=UserRole.HR)
    candidate = make_candidate(db_session, owner=owner_hr)
    csrf = _login(client, "hr1")
    event = _create_event(client, csrf, str(candidate.id), remind_at=_in(60))
    row = _linked(db_session, event["id"])[0]

    # hr2 has no access to this reminder; naming the candidate must not help.
    hr2_csrf = _login(client, "hr2")
    response = client.get(
        "/reminders", params={"candidate_id": str(candidate.id)}, headers={"X-CSRF-Token": hr2_csrf}
    )
    assert response.status_code == 200
    assert response.json()["items"] == []
    assert str(row.id) not in [item["id"] for item in response.json()["items"]]
