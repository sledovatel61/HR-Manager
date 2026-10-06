"""Isolated visual acceptance fixture. Synthetic data, in-memory SQLite, real API."""

import os

os.environ.update(
    APP_ENV="test",
    SECRET_KEY="isolated-visual-test-not-production",
    DATABASE_URL="sqlite+pysqlite://",
)
from datetime import date
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from app.config import Settings
from app.main import create_app
from app.models import (
    Base,
    CandidateStage,
    Notification,
    NotificationType,
    NotificationSource,
    UserRole,
)
from tests.conftest import make_user, make_candidate
from tests.attachment_fixtures import build_docx, build_pdf
from pathlib import Path
from tempfile import gettempdir

engine = create_engine(
    "sqlite+pysqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
Base.metadata.create_all(engine)
with Session(engine) as db:
    users = {
        role: make_user(
            db, username=f"visual-{role}", role=role, full_name=f"ТЕСТ · {role}"
        )
        for role in [UserRole.ADMIN, UserRole.HR, UserRole.MANAGER]
    }
    for stage in CandidateStage:
        for index in range(3 if stage == CandidateStage.NEW else 1):
            candidate = make_candidate(
                db,
                owner=users[UserRole.HR],
                full_name=f"ТЕСТ · Кандидат {stage.value} {index + 1}",
                stage=stage,
                position="Тестовая вакансия",
            )
            candidate.start_date = date(2026, 10, 6)
            candidate.start_organization = "ТЕСТ · Организация"
            candidate.start_department = "Тестовый отдел"
            candidate.shift = "Дневная"
    for user in users.values():
        for index in range(5):
            db.add(
                Notification(
                    user_id=user.id,
                    type=NotificationType.SYSTEM_ALERT,
                    source=NotificationSource.SYSTEM,
                    title=f"ТЕСТ · Уведомление {index + 1}: проверка переноса длинного заголовка",
                    body="Тестовая запись для проверки интерфейса. Не является производственным уведомлением.",
                )
            )
    db.commit()
artifacts = Path(
    os.environ.get(
        "HR_ACCEPTANCE_ARTIFACTS", str(Path(gettempdir()) / "hrm-acceptance")
    )
)
artifacts.mkdir(parents=True, exist_ok=True)
(artifacts / "test-form.docx").write_bytes(build_docx())
(artifacts / "test-form.pdf").write_bytes(build_pdf())
app = create_app(Settings(), engine)
