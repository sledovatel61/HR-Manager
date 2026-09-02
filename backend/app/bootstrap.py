"""Initial administrator bootstrap.

On startup, if the ``users`` table is empty, an initial administrator is
created so a fresh deployment is usable:

* in **development/test-like local stacks** the well-known development
  credentials are used (``admin`` / ``AdminAdmin123``) and printed to the
  logs — they are development-only and rejected by the production guard;
* in **production** the password must be provided through
  ``BOOTSTRAP_ADMIN_PASSWORD`` (the production config validator rejects the
  development default). If the table is empty in production without a
  configured password, no weak account is created; the operator can run
  ``python -m app.cli create-admin`` to create the administrator.

The bootstrap never touches an existing user table.
"""

import logging

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import DEVELOPMENT_BOOTSTRAP_ADMIN_PASSWORD, Settings
from app.models import User, UserRole
from app.security import hash_password
from app.utils import utc_now

logger = logging.getLogger(__name__)


def bootstrap_admin(db: Session, settings: Settings) -> User | None:
    """Create the initial administrator when no users exist.

    Returns the created user, or ``None`` when no bootstrap was performed
    (users already exist, or production without a configured password).
    """
    existing = db.scalar(select(func.count()).select_from(User))
    if existing:
        return None

    username = settings.bootstrap_admin_username.strip()
    password = settings.bootstrap_admin_password

    if settings.is_production and password == DEVELOPMENT_BOOTSTRAP_ADMIN_PASSWORD:
        logger.error(
            "no users exist and BOOTSTRAP_ADMIN_PASSWORD is not set; refusing to create a "
            "weak administrator. Create one with: python -m app.cli create-admin"
        )
        return None

    admin = User(
        username=username,
        full_name=settings.bootstrap_admin_full_name,
        role=UserRole.ADMIN,
        password_hash=hash_password(password),
        is_active=True,
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    db.add(admin)
    db.commit()
    db.refresh(admin)

    if settings.is_production:
        logger.info("initial administrator '%s' created from BOOTSTRAP_ADMIN_* settings", username)
    else:
        logger.warning(
            "development administrator created: username=%s password=%s "
            "(DEVELOPMENT ONLY — change it or create users via the admin API)",
            username,
            DEVELOPMENT_BOOTSTRAP_ADMIN_PASSWORD,
        )
    return admin
