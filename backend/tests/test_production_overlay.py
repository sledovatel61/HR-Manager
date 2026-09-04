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


# --- phase 7: backup service, release images, proxy overlay --------------------

PROXY_OVERLAY = REPO_ROOT / "infra" / "docker-compose.proxy.yml"
NGINX_TEMPLATE = REPO_ROOT / "infra" / "nginx" / "default.conf.template"
BACKUP_DOCKERFILE = REPO_ROOT / "backend" / "Dockerfile.backup"
SCRIPTS_DIR = REPO_ROOT / "infra" / "scripts"
DEV_BACKUP_KEY = "ZGV2LW9ubHktYmFja3VwLWtleS0wMDAwMDAwMDAwMDA="


@pytest.fixture(scope="module")
def proxy_overlay() -> dict[str, Any]:
    with PROXY_OVERLAY.open() as fh:
        return yaml.load(fh, Loader=_ComposeLoader)


def test_backup_service_present_in_dev_and_prod(
    dev_compose: dict[str, Any], prod_overlay: dict[str, Any]
) -> None:
    dev_backup = dev_compose["services"]["backup"]
    assert dev_backup["environment"]["BACKUP_ENC_KEY"] == DEV_BACKUP_KEY
    assert dev_backup["environment"]["APP_ENV"] == "development"
    assert "/var/backups/hr-manager" in str(dev_backup["volumes"])
    # The scheduler healthcheck must not depend on a backup existing.
    assert "backup-scheduler-ready" in str(dev_backup["healthcheck"])
    assert "backups" in dev_compose["volumes"]
    # Production replaces the dev key from the environment.
    prod_backup_env = prod_overlay["services"]["backup"]["environment"]
    assert prod_backup_env["BACKUP_ENC_KEY"] == "${BACKUP_ENC_KEY:-}"
    assert prod_backup_env["BACKUP_KEY_ID"] == "${BACKUP_KEY_ID:-}"
    assert prod_backup_env["APP_ENV"] == "${APP_ENV:-production}"
    assert DEV_BACKUP_KEY not in str(prod_overlay)


def test_backup_service_has_no_ports_in_production(prod_overlay: dict[str, Any]) -> None:
    assert prod_overlay["services"]["backup"]["ports"] == []


def test_prod_backend_does_not_auto_migrate(prod_overlay: dict[str, Any]) -> None:
    command = prod_overlay["services"]["backend"]["command"]
    assert "uvicorn" in str(command)
    assert "alembic" not in str(command)


def test_prod_images_are_release_tagged(prod_overlay: dict[str, Any]) -> None:
    assert (
        prod_overlay["services"]["backend"]["image"] == "hr-manager-backend:${RELEASE_TAG:-local}"
    )
    assert (
        prod_overlay["services"]["frontend"]["image"] == "hr-manager-frontend:${RELEASE_TAG:-local}"
    )
    assert prod_overlay["services"]["backup"]["image"] == "hr-manager-backup:${RELEASE_TAG:-local}"


def test_prod_backend_exposes_release_sha(prod_overlay: dict[str, Any]) -> None:
    backend_env = prod_overlay["services"]["backend"]["environment"]
    assert backend_env["RELEASE_SHA"] == "${RELEASE_SHA:-}"


def test_proxy_overlay_publishes_only_http_ports(
    proxy_overlay: dict[str, Any],
) -> None:
    for name, definition in proxy_overlay["services"].items():
        assert name == "proxy"
        for port in definition["ports"]:
            container_port = port.split(":")[-1]
            assert container_port in ("80", "443")
    # The application containers themselves stay portless in production.
    assert "backend" not in proxy_overlay["services"]
    assert "frontend" not in proxy_overlay["services"]


def test_nginx_template_has_tls_and_security_headers() -> None:
    template = NGINX_TEMPLATE.read_text()
    assert "listen 443 ssl;" in template
    assert "ssl_certificate" in template and "ssl_certificate_key" in template
    assert "return 301 https://" in template
    assert "X-Content-Type-Options" in template
    assert "X-Frame-Options" in template
    assert "Referrer-Policy" in template
    assert "Strict-Transport-Security" in template
    assert "Content-Security-Policy" in template
    assert "client_max_body_size" in template
    # The config must not embed secrets or claim a concrete hostname/cert.
    assert "server.crt" in template  # mounted from the operator dir
    assert "BEGIN " not in template
    assert "hr-manager.example.com" not in template


def test_backup_dockerfile_is_nonroot_and_parity_with_backend() -> None:
    backend = (REPO_ROOT / "backend" / "Dockerfile").read_text()
    backup = BACKUP_DOCKERFILE.read_text()
    # Same base image pin and same application install path (the backend
    # Dockerfile starts with a comment line).
    assert "FROM python:3.12-slim" in backend
    assert "FROM python:3.12-slim" in backup
    assert "COPY backend/requirements.txt ./" in backup
    assert "pip install --no-cache-dir -r requirements.txt" in backup
    assert "USER app" in backup
    # Pinned, checksum-verified toolchain sources.
    assert "POSTGRES_VERSION=16.15" in backup
    assert "POSTGRES_SHA256=c1afb748" in backup
    assert "ZLIB_VERSION=1.3.2" in backup
    assert "sha256sum -c -" in backup
    # PostgreSQL release tags use underscores (REL_16_15), not dots — the
    # download tag is pinned explicitly or the image build fails with 404.
    assert "POSTGRES_TAG=REL_16_15" in backup
    assert "refs/tags/${POSTGRES_TAG}" in backup
    # The sandbox-validated build toolchain (no libssl-dev keeps the recipe
    # identical to the verified one), and the build-stage library path that
    # makes the pg_dump/pg_restore version self-checks runnable.
    assert "build-essential ca-certificates curl perl pkg-config" in backup
    assert "ENV BISON=/bin/true FLEX=/bin/true LD_LIBRARY_PATH=/usr/local/pgtools/lib" in backup
    # No secrets in the image layers.
    for forbidden in ("hr_manager_dev_password", "dev-only-secret-key", DEV_BACKUP_KEY):
        assert forbidden not in backup


def test_backup_image_does_not_bundle_backups_or_secrets() -> None:
    backup = BACKUP_DOCKERFILE.read_text()
    assert "COPY backend/app ./app" in backup
    assert ".pgdump" not in backup
    # The backups directory appears only as the volume mount point owned by
    # the app user — never as copied backup content.
    assert "/var/backups/hr-manager" in backup
    assert "COPY" not in [line for line in backup.splitlines() if "/var/backups" in line]


def test_scripts_are_valid_bash() -> None:
    import subprocess

    for script in sorted(SCRIPTS_DIR.glob("*.sh")):
        proc = subprocess.run(["bash", "-n", str(script)], capture_output=True, text=True)
        assert proc.returncode == 0, f"{script.name}: {proc.stderr}"


def test_migrate_script_has_no_automatic_downgrade() -> None:
    text = (SCRIPTS_DIR / "migrate.sh").read_text()
    assert "alembic upgrade head" in text
    assert "downgrade" in text
    assert "exit 2" in text


def test_deploy_script_smoke_and_rollback() -> None:
    text = (SCRIPTS_DIR / "deploy.sh").read_text()
    assert "--release <full-git-sha>" in text
    assert "release-prev" in text
    assert "rollback" in text
    assert "release_sha" in text  # smoke verifies the served SHA
    assert "release-broken" in text  # failure drill in the test contour
    assert "release-notes" in text


def test_check_env_backup_semantics() -> None:
    import base64
    import subprocess

    script = str(SCRIPTS_DIR / "check_env.sh")
    strong = base64.b64encode(b"k" * 32).decode()
    base_env = {
        "PATH": "/usr/bin:/bin",
        "APP_ENV": "production",
        "SECRET_KEY": "s" * 40,
        "POSTGRES_PASSWORD": "p" * 20,
        "BOOTSTRAP_ADMIN_PASSWORD": "b" * 16,
    }

    def run(extra: dict[str, str]) -> subprocess.CompletedProcess:
        env = dict(base_env, **extra)
        return subprocess.run(["bash", script], env=env, capture_output=True, text=True)

    # Without BACKUP_ENABLED, missing keys are warnings and the preflight passes.
    ok_warn = run({})
    assert ok_warn.returncode == 0
    assert "warning:" in ok_warn.stderr
    # With BACKUP_ENABLED=true, missing/weak/dev keys are hard failures.
    assert run({"BACKUP_ENABLED": "true"}).returncode != 0
    assert run({"BACKUP_ENABLED": "true", "BACKUP_KEY_ID": "k1"}).returncode != 0
    assert (
        run(
            {"BACKUP_ENABLED": "true", "BACKUP_KEY_ID": "k1", "BACKUP_ENC_KEY": DEV_BACKUP_KEY}
        ).returncode
        != 0
    )
    assert (
        run({"BACKUP_ENABLED": "true", "BACKUP_KEY_ID": "k1", "BACKUP_ENC_KEY": "short"}).returncode
        != 0
    )
    # A real 32-byte key is accepted.
    ok = run({"BACKUP_ENABLED": "true", "BACKUP_KEY_ID": "k1", "BACKUP_ENC_KEY": strong})
    assert ok.returncode == 0, ok.stderr


def test_scheduler_script_is_utc_and_retries() -> None:
    text = (SCRIPTS_DIR / "backup_scheduler.sh").read_text()
    assert "SCHEDULE_UTC" in text
    assert "date -u" in text
    assert "RETRY_ATTEMPTS" in text
    assert "BACKOFF_SECONDS" in text
    assert "backup-drill" in text
    assert "backup-check" in text


def test_frontend_npm_audit_ci_shim_pins_npm_11() -> None:
    # The npm registry retired the quick-audit endpoint; npm 10.x still posts
    # there and fails. The CI-only postinstall shim upgrades to npm 11, which
    # uses the bulk-advisory endpoint, so `npm audit` on GitHub runners works.
    import json

    package_json = json.loads((REPO_ROOT / "frontend" / "package.json").read_text())
    assert package_json["packageManager"] == "npm@11"
    assert package_json["scripts"]["postinstall"] == "node scripts/ci-upgrade-npm.mjs"
    shim = (REPO_ROOT / "frontend" / "scripts" / "ci-upgrade-npm.mjs").read_text()
    assert 'spawnSync("npm", ["install", "-g", "npm@11"]' in shim
    assert "process.env.CI" in shim  # local installs are never touched
    # The Docker build runs `npm ci` before `COPY . .`, so the shim must be
    # copied explicitly or the postinstall hook fails the image build.
    dockerfile = (REPO_ROOT / "frontend" / "Dockerfile").read_text()
    assert dockerfile.index("COPY scripts ./scripts") < dockerfile.index("RUN npm ci")
