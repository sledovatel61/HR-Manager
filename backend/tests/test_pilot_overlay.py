"""Static safety checks for the Phase 12 pilot Compose overlay.

Plain unit tests (no Docker): they validate ``infra/compose.pilot.yml`` the
same way ``test_production_overlay.py`` validates the production overlay, so a
broken merge can never silently publish the database or leak a development
secret into the Windows pilot. Behavioural Compose validation runs in CI.
"""

from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
PILOT_OVERLAY = REPO_ROOT / "infra" / "compose.pilot.yml"
DEV_BACKUP_KEY = "ZGV2LW9ubHktYmFja3VwLWtleS0wMDAwMDAwMDAwMDA="


class _ComposeLoader(yaml.SafeLoader):
    pass


def _construct_reset(loader: yaml.Loader, node: yaml.Node) -> Any:
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node)
    return None


_ComposeLoader.add_constructor("!reset", _construct_reset)


@pytest.fixture(scope="module")
def pilot_overlay() -> dict[str, Any]:
    with PILOT_OVERLAY.open() as fh:
        return yaml.load(fh, Loader=_ComposeLoader)


def test_pilot_project_name_is_stable_and_distinct(pilot_overlay: dict[str, Any]) -> None:
    assert pilot_overlay["name"] == "hr-manager-pilot"


def test_pilot_publishes_only_loopback_frontend(pilot_overlay: dict[str, Any]) -> None:
    services = pilot_overlay["services"]
    published: dict[str, list[str]] = {}
    for name, definition in services.items():
        ports = definition.get("ports") or []
        if ports:
            published[name] = ports
    assert set(published) == {"frontend"}, (
        f"only the frontend may publish ports in the pilot, got {sorted(published)}"
    )
    for port in published["frontend"]:
        host_part, _container_part = port.split(":", 1)
        assert host_part == "127.0.0.1"


def test_pilot_backend_is_hardened(pilot_overlay: dict[str, Any]) -> None:
    backend_env = pilot_overlay["services"]["backend"]["environment"]
    assert backend_env["APP_ENV"] == "pilot"
    assert backend_env["APP_DEBUG"] == "false"
    assert backend_env["SESSION_COOKIE_SECURE"] == "false"  # loopback HTTP model
    assert backend_env["SECRET_KEY"].startswith("${SECRET_KEY")
    assert backend_env["DATABASE_URL"].startswith("postgresql+psycopg://")
    assert "TELEGRAM_ENABLED" in backend_env and backend_env["TELEGRAM_ENABLED"] == "false"
    assert backend_env["SMTP_ENABLED"] == "false"


def test_pilot_has_no_development_secrets(pilot_overlay: dict[str, Any]) -> None:
    text = str(pilot_overlay)
    for forbidden in (
        "hr_manager_dev_password",
        "dev-only-secret-key-not-for-production",
        "AdminAdmin123",
        DEV_BACKUP_KEY,
    ):
        assert forbidden not in text


def test_pilot_backend_does_not_auto_migrate(pilot_overlay: dict[str, Any]) -> None:
    command = pilot_overlay["services"]["backend"]["command"]
    assert "uvicorn" in str(command)
    assert "alembic" not in str(command)


def test_pilot_first_run_env_is_present(pilot_overlay: dict[str, Any]) -> None:
    backend_env = pilot_overlay["services"]["backend"]["environment"]
    for variable in (
        "FIRST_RUN_TOKEN",
        "FIRST_RUN_EXPIRES_AT",
        "PILOT_SURNAME",
        "PILOT_WORKING_MODE",
    ):
        assert variable in backend_env
    assert backend_env["FIRST_RUN_TOKEN"] == "${FIRST_RUN_TOKEN:-}"
    assert backend_env["PILOT_WORKING_MODE"] == "${PILOT_WORKING_MODE:-hr}"


def test_pilot_backup_encryption_key_is_required(pilot_overlay: dict[str, Any]) -> None:
    backup_env = pilot_overlay["services"]["backup"]["environment"]
    assert "BACKUP_ENC_KEY" in backup_env
    assert ":?" in backup_env["BACKUP_ENC_KEY"]  # fail-fast when missing
    assert "BACKUP_KEY_ID" in backup_env
    assert backup_env["APP_ENV"] == "pilot"
    assert backup_env["BACKUP_ON_START"] == "1"  # update gate has a fresh copy
    assert backup_env["TZ"] == "UTC"


def test_pilot_images_are_release_tagged(pilot_overlay: dict[str, Any]) -> None:
    assert (
        pilot_overlay["services"]["backend"]["image"]
        == "hr-manager-backend:${PILOT_RELEASE_TAG:-pilot-current}"
    )
    assert (
        pilot_overlay["services"]["frontend"]["image"]
        == "hr-manager-frontend:${PILOT_RELEASE_TAG:-pilot-current}"
    )
    assert (
        pilot_overlay["services"]["backup"]["image"]
        == "hr-manager-backup:${PILOT_RELEASE_TAG:-pilot-current}"
    )


def test_pilot_mailpit_is_disabled(pilot_overlay: dict[str, Any]) -> None:
    mailpit = pilot_overlay["services"]["mailpit"]
    assert "disabled" in mailpit.get("profiles", [])


def test_pilot_worker_reuses_backend_image_without_ports(
    pilot_overlay: dict[str, Any],
) -> None:
    worker = pilot_overlay["services"]["worker"]
    assert worker["image"] == "hr-manager-backend:${PILOT_RELEASE_TAG:-pilot-current}"
    assert worker.get("ports") in (None, [])
    assert worker["environment"]["APP_ENV"] == "pilot"


def test_pilot_backend_exposes_release_sha(pilot_overlay: dict[str, Any]) -> None:
    backend_env = pilot_overlay["services"]["backend"]["environment"]
    assert backend_env["RELEASE_SHA"] == "${RELEASE_SHA:-}"
