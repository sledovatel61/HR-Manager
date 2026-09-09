"""Static safety checks for the local pilot Compose overlay (phase 12).

Plain unit tests (no Docker required): the overlay must not be able to
publish anything beyond loopback, must not carry development secrets, and
must force APP_ENV=pilot with APP_DEBUG=false. The behavioural Compose
validation runs in the `stack` CI job.
"""

from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
DEV_COMPOSE = REPO_ROOT / "infra" / "docker-compose.yml"
PILOT_OVERLAY = REPO_ROOT / "infra" / "compose.pilot.yml"
PROD_OVERLAY = REPO_ROOT / "infra" / "compose.prod.yml"


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


@pytest.fixture(scope="module")
def dev_compose() -> dict[str, Any]:
    with DEV_COMPOSE.open() as fh:
        return yaml.load(fh, Loader=_ComposeLoader)


@pytest.fixture(scope="module")
def prod_overlay() -> dict[str, Any]:
    with PROD_OVERLAY.open() as fh:
        return yaml.load(fh, Loader=_ComposeLoader)


def _env_map(service: dict[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    raw = service.get("environment", {})
    if isinstance(raw, dict):
        for key, value in raw.items():
            result[str(key)] = "" if value is None else str(value)
        return result
    for entry in raw:
        if isinstance(entry, str):
            key, _, value = entry.partition("=")
            result[key] = value
        elif isinstance(entry, dict):
            result.update({str(k): "" if v is None else str(v) for k, v in entry.items()})
    return result


def test_pilot_overlay_publishes_only_loopback_frontend(pilot_overlay: dict[str, Any]) -> None:
    services = pilot_overlay["services"]
    published: dict[str, list[str]] = {}
    for name, service in services.items():
        ports = service.get("ports") or []
        if ports:
            published[name] = [str(port) for port in ports]
    assert set(published) == {"frontend"}, published
    for port in published["frontend"]:
        assert port.startswith("127.0.0.1:"), port


def test_pilot_overlay_project_name_is_stable(pilot_overlay: dict[str, Any]) -> None:
    assert pilot_overlay["name"] == "hr-manager-pilot"


def test_pilot_overlay_uses_dedicated_named_volumes(pilot_overlay: dict[str, Any]) -> None:
    volumes = pilot_overlay["volumes"]
    assert "pilot_pgdata" in volumes
    assert "pilot_backups" in volumes
    db_volumes = pilot_overlay["services"]["db"]["volumes"]
    assert any("pilot_pgdata" in str(entry) for entry in db_volumes)
    backup_volumes = pilot_overlay["services"]["backup"]["volumes"]
    assert any("pilot_backups" in str(entry) for entry in backup_volumes)


def test_pilot_overlay_has_no_development_secrets(pilot_overlay: dict[str, Any]) -> None:
    rendered = yaml.dump(pilot_overlay, allow_unicode=True)
    for secret in (
        "hr_manager_dev_password",
        "dev-only-secret-key",
        "AdminAdmin123",
        "dev-backup-key",
        "ZGV2LW9ubHktYmFja3VwLWtleS0wMDAwMDAwMDAwMDA=",
    ):
        assert secret not in rendered, secret


def test_pilot_overlay_forces_pilot_environment_without_debug(
    pilot_overlay: dict[str, Any],
) -> None:
    for service in ("backend", "worker", "backup"):
        env = _env_map(pilot_overlay["services"][service])
        assert env.get("APP_ENV") == "pilot", service
        assert env.get("APP_DEBUG") == "false", service


def test_pilot_overlay_requires_generated_secrets(pilot_overlay: dict[str, Any]) -> None:
    rendered = yaml.dump(pilot_overlay)
    for required in (
        "${HRM_POSTGRES_PASSWORD:?",
        "${HRM_SIGNING_KEY:?",
        "${HRM_BOOTSTRAP_ADMIN_PASSWORD:?",
        "${HRM_EXCHANGE_TOKEN:?",
        "${HRM_BACKUP_KEY:?",
        "${HRM_BACKUP_KEY_ID:?",
    ):
        assert required in rendered, required


def test_pilot_overlay_disables_external_channels_by_default(
    pilot_overlay: dict[str, Any],
) -> None:
    backend_env = _env_map(pilot_overlay["services"]["backend"])
    assert backend_env.get("SMTP_ENABLED") == "false"
    assert backend_env.get("TELEGRAM_ENABLED") == "false"


def test_pilot_overlay_excludes_mailpit(pilot_overlay: dict[str, Any]) -> None:
    assert "profiles" in pilot_overlay["services"]["mailpit"]


def test_pilot_overlay_declares_explicit_cookie_trust_model(
    pilot_overlay: dict[str, Any],
) -> None:
    backend_env = _env_map(pilot_overlay["services"]["backend"])
    # Loopback trust model: non-Secure cookies are declared EXPLICITLY (the
    # overlay documents why); production still refuses that value.
    assert backend_env.get("SESSION_COOKIE_SECURE") == "false"


def test_pilot_overlay_needs_compose_v2_24_like_production() -> None:
    text = PILOT_OVERLAY.read_text()
    assert "!reset" in text
    assert "2.24" in text


def test_production_overlay_untouched_by_pilot(prod_overlay: dict[str, Any]) -> None:
    """The pilot must not weaken the production overlay: Secure cookies stay
    required and no ports are published there."""
    backend_env = _env_map(prod_overlay["services"]["backend"])
    assert backend_env.get("SESSION_COOKIE_SECURE") == "true"
    for service in prod_overlay["services"].values():
        assert (service.get("ports") or []) == [], service


def test_dev_compose_unchanged_mailpit_defaults(dev_compose: dict[str, Any]) -> None:
    # The development stack keeps its local SMTP sink: the pilot excludes it
    # only via the overlay profile, not by touching the dev file.
    assert "mailpit" in dev_compose["services"]
