"""Static safety contract of the local-pilot Compose profile (phase 12).

These tests parse `infra/compose.pilot.yml` (with a loader that understands
the Compose ``!reset`` tag) and prove the invariants that make the profile
safe to run on a Windows machine: loopback-only publication, production
guards with explicit secrets, disabled bootstrap, disabled integrations and
named volumes for durability. Behavioural compose/HTTP proofs run in the CI
``stack`` job; the intent here is the file contract itself.
"""

from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
PILOT_OVERLAY = REPO_ROOT / "infra" / "compose.pilot.yml"


class _ComposeLoader(yaml.SafeLoader):
    pass


def _construct_reset(loader: yaml.Loader, node: yaml.Node) -> Any:
    return []


_ComposeLoader.add_constructor("!reset", _construct_reset)


@pytest.fixture(scope="module")
def pilot_overlay() -> dict[str, Any]:
    with PILOT_OVERLAY.open(encoding="utf-8") as fh:
        return yaml.load(fh, Loader=_ComposeLoader)


def _env(service: dict[str, Any], name: str) -> str:
    return str(service.get("environment", {}).get(name, ""))


def test_project_name_is_stable_and_distinct(pilot_overlay: dict[str, Any]) -> None:
    # A stable project name is what keeps named volumes across stop/update and
    # separates the pilot stack from development stacks on the same machine.
    assert pilot_overlay["name"] == "hr-manager-pilot"


def test_only_frontend_is_published_on_loopback(pilot_overlay: dict[str, Any]) -> None:
    services = pilot_overlay["services"]
    for name in ("db", "backend", "worker", "backup", "mailpit"):
        assert services[name].get("ports") == [], f"{name} must publish nothing"
    frontend_ports = services["frontend"]["ports"]
    assert frontend_ports == ["127.0.0.1:${HRMGR_PILOT_PORT:-8081}:8080"]
    assert str(frontend_ports[0]).startswith("127.0.0.1:")


def test_production_guards_are_active(pilot_overlay: dict[str, Any]) -> None:
    services = pilot_overlay["services"]
    for name in ("backend", "worker", "backup"):
        env = services[name]["environment"]
        assert env["APP_ENV"] == "production"
        assert env["APP_DEBUG"] == "false"
        assert str(env["APP_DEBUG"]).lower() != "true"
    backend_env = services["backend"]["environment"]
    # The loopback-trust model is EXPLICIT: Secure-off is tied to the pilot
    # guard and the backend refuses non-loopback Hosts with it.
    assert backend_env["SESSION_COOKIE_SECURE"] == "false"
    assert backend_env["HRMGR_PILOT_LOCAL_TRUSTED"] == "true"


def test_secrets_come_from_the_environment_with_fail_fast(pilot_overlay: dict[str, Any]) -> None:
    text = PILOT_OVERLAY.read_text(encoding="utf-8")
    for secret in ("SECRET_KEY", "POSTGRES_PASSWORD", "BACKUP_ENC_KEY", "BACKUP_KEY_ID"):
        required = f"{secret}: ${{{secret}:?"
        assert required in text, f"{secret} must be required with the :?fail-fast interpolation"
    for dev_only in (
        "hr_manager_dev_password",
        "dev-only-secret-key",
        "AdminAdmin123",
        "ZGV2LW9ubHktYmFja3VwLWtleS0wMDAwMDAwMDAwMDA=",
    ):
        assert dev_only not in text, (
            f"development secret {dev_only!r} leaked into the pilot profile"
        )


def test_bootstrap_admin_is_disabled(pilot_overlay: dict[str, Any]) -> None:
    # An explicitly EMPTY password disables the weak bootstrap account; the
    # single owner arrives via the first-run pairing instead.
    assert _env(pilot_overlay["services"]["backend"], "BOOTSTRAP_ADMIN_PASSWORD") == ""


def test_integrations_off_by_default(pilot_overlay: dict[str, Any]) -> None:
    for name in ("backend", "worker"):
        env = pilot_overlay["services"][name]["environment"]
        assert env["TELEGRAM_ENABLED"] == "false"
        assert env["SMTP_ENABLED"] == "false"


def test_mailpit_is_profile_disabled(pilot_overlay: dict[str, Any]) -> None:
    mailpit = pilot_overlay["services"]["mailpit"]
    assert mailpit.get("profiles") == ["pilot-disabled"]


def test_data_and_backups_use_named_volumes(pilot_overlay: dict[str, Any]) -> None:
    # The base file owns the volume declarations; the pilot profile must not
    # downgrade them to host paths or tmpfs (durability across updates).
    text = PILOT_OVERLAY.read_text(encoding="utf-8")
    assert "tmpfs" not in text
    assert "volumes:" not in text  # no service-level volume overrides at all
    base = (REPO_ROOT / "infra" / "docker-compose.yml").read_text(encoding="utf-8")
    assert "pgdata:" in base and "backups:" in base


def test_images_are_versioned_for_update_and_rollback(pilot_overlay: dict[str, Any]) -> None:
    services = pilot_overlay["services"]
    for name in ("backend", "worker", "frontend", "backup"):
        image = services[name]["image"]
        assert "pilot-${HRMGR_RELEASE_VERSION:?" in image, (
            f"{name} must run a version-pinned image so update can roll back"
        )


def test_backend_never_auto_migrates_on_start(pilot_overlay: dict[str, Any]) -> None:
    command = pilot_overlay["services"]["backend"].get("command")
    assert command == ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
    assert "alembic" not in " ".join(command)
