"""Static safety checks for the Compose production overlay.

These are plain unit tests (no Docker required): they validate the overlay
file itself so that a broken merge or a wrong port/secret cannot silently
reach production.

The tests parse the YAML with a loader that understands the Docker Compose
``!reset`` tag. They are intentionally kept syntax-based so they run
everywhere, including SQLite-only CI jobs; the full behavioural Compose
validation runs in the `stack` CI job.
"""

from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
DEV_COMPOSE = REPO_ROOT / "infra" / "docker-compose.yml"
PROD_OVERLAY = REPO_ROOT / "infra" / "compose.prod.yml"


class _ComposeLoader(yaml.SafeLoader):
    pass


def _construct_reset(loader: yaml.Loader, node: yaml.Node) -> Any:
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node)
    if isinstance(node, yaml.ScalarNode) and loader.construct_scalar(node) == "":
        return None
    return None


_ComposeLoader.add_constructor("!reset", _construct_reset)


@pytest.fixture(scope="module")
def prod_overlay() -> dict[str, Any]:
    with PROD_OVERLAY.open() as fh:
        return yaml.load(fh, Loader=_ComposeLoader)


@pytest.fixture(scope="module")
def dev_compose() -> dict[str, Any]:
    with DEV_COMPOSE.open() as fh:
        return yaml.load(fh, Loader=_ComposeLoader)


def test_overlay_resets_all_external_ports(prod_overlay: dict[str, Any]) -> None:
    services = prod_overlay["services"]
    assert {"db", "backend", "frontend"} <= set(services)
    for name, definition in services.items():
        assert definition.get("ports") in (None, []), (
            f"service '{name}' must not publish external ports in production"
        )


def test_overlay_uses_only_environment_secrets(prod_overlay: dict[str, Any]) -> None:
    assert "hr_manager_dev_password" not in str(prod_overlay)
    assert "dev-only-secret-key-not-for-production" not in str(prod_overlay)
    backend_env: dict[str, str] = prod_overlay["services"]["backend"]["environment"]
    for variable in ("SECRET_KEY", "DATABASE_URL"):
        assert "${" in backend_env[variable], f"{variable} must come from the environment"


def test_overlay_forces_production_backend(prod_overlay: dict[str, Any]) -> None:
    backend_env: dict[str, str] = prod_overlay["services"]["backend"]["environment"]
    assert backend_env["APP_ENV"] == "${APP_ENV:-production}"
    assert backend_env["APP_DEBUG"] == "false"


def test_dev_compose_ports_are_loopback_only(dev_compose: dict[str, Any]) -> None:
    for name, definition in dev_compose["services"].items():
        for port in definition.get("ports", []):
            host_part, _container_part = port.split(":", 1)
            assert host_part == "127.0.0.1", (
                f"dev service '{name}' must bind to 127.0.0.1, got {host_part!r}"
            )


def test_dev_compose_credentials_are_development_only(dev_compose: dict[str, Any]) -> None:
    db_env = dev_compose["services"]["db"]["environment"]
    assert db_env["POSTGRES_PASSWORD"] == "hr_manager_dev_password"
    backend_env = dev_compose["services"]["backend"]["environment"]
    assert backend_env["SECRET_KEY"] == "dev-only-secret-key-not-for-production"
    assert backend_env["APP_ENV"] == "development"


def test_dev_compose_build_contexts_point_to_repo_dirs(dev_compose: dict[str, Any]) -> None:
    # Build contexts are resolved relative to infra/docker-compose.yml.
    assert dev_compose["services"]["backend"]["build"]["context"] == "../backend"
    assert dev_compose["services"]["frontend"]["build"]["context"] == "../frontend"
    assert (REPO_ROOT / "backend" / "Dockerfile").is_file()
    assert (REPO_ROOT / "frontend" / "Dockerfile").is_file()
