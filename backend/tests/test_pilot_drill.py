"""Инварианты автоматизированного pilot drill (Phase 14, CI-контур).

Сам drill исполняется в CI с живым Docker Compose (см. джобу pilot-drill в
ci.yml и infra/scripts/pilot-drill.sh). Здесь проверяются структурные
инварианты, которые обязаны выполняться всегда:

* drill использует ТОЛЬКО fixture-ключи и генерирует эфемерные секреты
  (никаких production secret-имён в скрипте/overlay);
* drill-стек изолирован от реального пилота (отдельное имя проекта,
* канал drill не публикует портов);
* overlay корректен по схеме и добавляет только описанные переменные;
* HTTPS-сервер канала — валидный Python и не содержит сетевых сюрпризов.
"""

from __future__ import annotations

import ast
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
DRILL = REPO / "infra" / "scripts" / "pilot-drill.sh"
SERVER = REPO / "infra" / "scripts" / "drill_channel_server.py"
OVERLAY = REPO / "infra" / "compose.drill.yml"

# GitHub App сессии не имеет права `workflows`: ci.yml с джобой pilot-drill
# публикуется в review-artifacts/ci.phase14.yml и переносится владельцем.
# Пока переноса нет — тесты проверяют артефакт; после переноса — in-tree.
_CI_IN_TREE = REPO / ".github" / "workflows" / "ci.yml"
_CI_P14 = (
    _CI_IN_TREE
    if _CI_IN_TREE.exists() and "pilot-drill:" in _CI_IN_TREE.read_text(encoding="utf-8")
    else REPO / "review-artifacts" / "ci.phase14.yml"
)

PRODUCTION_SECRET_NAMES = (
    "UPDATE_CHANNEL_SIGNING_KEY",
    "INSTALLER_AUTHENTICODE_PFX",
    "HRM_SIGNING_PFX",
)


def test_ci_has_pilot_drill_job() -> None:
    """Джоба pilot-drill обязана быть в CI (артефакт до переноса владельцем,
    in-tree после); без production secrets; артефакты отчётов загружаются."""
    data = yaml.safe_load(_CI_P14.read_text(encoding="utf-8"))
    jobs = data["jobs"]
    assert "pilot-drill" in jobs, "в CI нет джобы pilot-drill"
    job = jobs["pilot-drill"]
    assert job["runs-on"] == "ubuntu-latest"
    text = str(job)
    for name in PRODUCTION_SECRET_NAMES:
        assert name not in text, f"production secret {name} в CI-джобе drill"
    # Отчёты drill публикуются как артефакты (машиночитаемый JSON + MD).
    upload = [s for s in job["steps"] if s.get("uses", "").startswith("actions/upload-artifact")]
    assert upload, "джоба pilot-drill должна загружать отчёты drill"


def test_drill_script_exists_and_is_executable() -> None:
    assert DRILL.exists(), "infra/scripts/pilot-drill.sh должен существовать"
    assert DRILL.stat().st_mode & 0o111, "drill должен быть исполняемым"
    text = DRILL.read_text(encoding="utf-8")
    assert text.startswith("#!"), "drill — bash-скрипт с шебангом"
    assert "set -euo pipefail" in text
    # Non-zero exit при провале — контракт CI.
    assert "exit 1" in text
    # Машиночитаемый JSON и Markdown без секретов.
    assert "pilot-drill.json" in text
    assert "pilot-drill.md" in text


def test_drill_uses_only_fixture_keys_and_ephemeral_secrets() -> None:
    text = DRILL.read_text(encoding="utf-8")
    # Подпись канала — только fixture-ключ из testdata.
    assert "test_key.priv" in text
    assert "trusted_keys.json" in text
    # Секреты прогона генерируются на месте (openssl rand), не хардкодятся.
    assert "openssl rand -hex" in text
    for name in PRODUCTION_SECRET_NAMES:
        assert name not in text, f"production secret {name} в drill недопустим"
    # Секреты прогона не попадают в отчёты: генератор отчёта пишет только
    # коды стадий/счётчики (переменные секрета в heredoc отсутствуют).
    report_block = text[text.index('python3 - "$REPORT_JSON"') :]
    for secret_var in ("OWNER_PASSWORD", "ENGINE_TOKEN", "PG_PASSWORD", "SIGNING_KEY"):
        assert secret_var not in report_block, f"{secret_var} не должен фигурировать в отчёте"


def test_drill_checks_fail_closed_negative_stages() -> None:
    """Дефектные артефакты канала обязаны проверяться ДО изменения установки."""
    text = DRILL.read_text(encoding="utf-8")
    assert "manifest_bad_signature" in text
    assert "package_hash_mismatch" in text
    assert "bad_url" in text
    # Отказы канала не приводят к установке.
    assert "install_blocked_after_tamper" in text
    # Данные переживают «обновление» и рестарт стека без purge.
    assert "data_preserved_after_update" in text
    assert "uninstall_without_purge" in text
    # Resume: повторный опрос выдаёт ту же команду установки.
    assert "engine_resume_delivery" in text


def test_drill_overlay_isolated_from_real_pilot() -> None:
    data = yaml.safe_load(OVERLAY.read_text(encoding="utf-8"))
    # Отдельный проект: тома/сети drill не пересекаются с реальным пилотом.
    assert data["name"] == "hr-manager-pilot-drill"
    services = data["services"]
    # Канал drill не публикует портов наружу.
    channel = services["channel"]
    assert "ports" not in channel, "канал drill не должен публиковать порты"
    rendered = str(services)
    for name in PRODUCTION_SECRET_NAMES:
        assert name not in rendered
    # Backend: доверие только drill-CA + явная политика хостов канала.
    backend_env = services["backend"]["environment"]
    assert backend_env["SSL_CERT_FILE"] == "/certs/drill-ca.crt"
    assert backend_env["UPDATE_CHANNEL_ALLOWED_HOSTS"] == "channel"
    assert backend_env["UPDATE_INSTALLED_VERSION"] == "0.13.0"
    # Все drill-пути приходят из обязательных переменных (fail closed).
    volumes = channel["volumes"] + services["backend"]["volumes"]
    for mount in volumes:
        source = mount.split(":")[0]
        assert "HRM_DRILL_" in source, f"путь drill должен быть переменной: {source}"


def test_drill_channel_server_is_valid_python() -> None:
    source = SERVER.read_text(encoding="utf-8")
    tree = ast.parse(source)
    functions = {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
    assert "main" in functions
    # Только статика + redirect; никаких других сетевых исходящих вызовов.
    assert "urlopen" not in source and "requests." not in source
    # TLS обязателен (не plain HTTP).
    assert "load_cert_chain" in source
    # Запрет выхода за корень обслуживаемого каталога.
    assert "resolve()" in source
    assert source.index("resolve()") < source.index("is_file()")


def test_drill_server_redirect_target_is_file_controlled() -> None:
    """Redirect читается из файла каталога канала (drill управляет целью)."""
    source = SERVER.read_text(encoding="utf-8")
    assert "redirect-target.txt" in source
    assert "/redirect" in source
