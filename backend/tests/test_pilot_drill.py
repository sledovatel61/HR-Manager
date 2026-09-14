"""Инварианты автоматизированного pilot drill (Phase 14, CI-контур).

Сам drill исполняется в CI с живым Docker Compose (см. джобу pilot-drill в
.github/workflows/phase14-live-drill.yml и infra/scripts/pilot-drill.sh).
Здесь проверяются структурные инварианты, которые обязаны выполняться всегда:

* drill использует ТОЛЬКО fixture-ключи и генерирует эфемерные секреты
  (никаких production secret-имён в скрипте/overlay);
* drill-стек изолирован от реального пилота (уникальное имя Compose-проекта
  передаётся с -p в КАЖДОЙ compose-команде);
* список обязательных стадий покрывает всю приёмку (readiness backend и
  frontend, синтетические данные с чтением назад, реальные байты бэкапа,
  restore с проверкой синтетики, полный tamper-suite, restart-persistence,
  cleanup без остатков) и каждая стадия кода входит в него;
* вердикт pass невозможен, если хоть одна обязательная стадия пропущена
  (skip → incomplete; провал → fail);
* workflow live drill отделён от unit-CI и выгружает evidence даже при
  провале; ручные ворота (Windows/production Authenticode) — отдельно.
"""

from __future__ import annotations

import ast
import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
DRILL = REPO / "infra" / "scripts" / "pilot-drill.sh"
SERVER = REPO / "infra" / "scripts" / "drill_channel_server.py"
OVERLAY = REPO / "infra" / "compose.drill.yml"
TESTDATA = REPO / "infra" / "release" / "testdata"
RELEASE = REPO / "infra" / "release"

# Workflow live drill: источник истины — in-tree .github/workflows/
# phase14-live-drill.yml. Пока GitHub App сессии без права `workflows` не
# может перенести его в дерево, инварианты проверяются на точной копии из
# review-artifacts/ (после переноса — in-tree версия).
_DRILL_WF_IN_TREE = REPO / ".github" / "workflows" / "phase14-live-drill.yml"
_DRILL_WF_FALLBACK = REPO / "review-artifacts" / "phase14-live-drill.yml"
_CI_P14 = _DRILL_WF_IN_TREE if _DRILL_WF_IN_TREE.exists() else _DRILL_WF_FALLBACK

PRODUCTION_SECRET_NAMES = (
    "UPDATE_CHANNEL_SIGNING_KEY",
    "INSTALLER_AUTHENTICODE_PFX",
    "HRM_SIGNING_PFX",
)


def test_ci_has_pilot_drill_job() -> None:
    """Джоба pilot-drill live drill обязана существовать (in-tree или точная
    копия в review-artifacts до переноса владельцем); без production
    secrets; evidence выгружается ВСЕГДА (даже при провале); unit-тесты не
    выдаются за live Docker E2E; ручные ворота — отдельной джобой."""
    assert _CI_P14.exists(), (
        f"workflow live drill не найден ни в {_DRILL_WF_IN_TREE}, ни в {_DRILL_WF_FALLBACK}"
    )
    data = yaml.safe_load(_CI_P14.read_text(encoding="utf-8"))
    jobs = data["jobs"]
    assert "pilot-drill" in jobs, "в workflow нет джобы pilot-drill"
    job = jobs["pilot-drill"]
    assert job["runs-on"] == "ubuntu-latest"
    text = str(job)
    for name in PRODUCTION_SECRET_NAMES:
        assert name not in text, f"production secret {name} в CI-джобе drill"
    # Джоба запускает drill, а НЕ pytest-агрегацию: unit ≠ live Docker E2E.
    drill_steps = [s for s in job["steps"] if "run" in s]
    assert any("pilot-drill.sh" in str(s["run"]) for s in drill_steps), (
        "джоба pilot-drill обязана запускать infra/scripts/pilot-drill.sh"
    )
    assert "pytest" not in text, "в джобе live drill не должно быть pytest-агрегации"
    # Evidence (JSON + MD) выгружается даже при провале джобы.
    upload = [s for s in job["steps"] if s.get("uses", "").startswith("actions/upload-artifact")]
    assert upload, "джоба pilot-drill должна загружать evidence drill"
    assert any(s.get("if") == "always()" for s in upload), "upload evidence — if: always()"
    assert any(s.get("with", {}).get("if-no-files-found") == "error" for s in upload), (
        "отсутствие evidence — ошибка (evidence обязателен даже при провале)"
    )
    # Обязательный шаг: вердикт drill обязан быть pass, иначе non-zero.
    assert any("verdict" in str(s.get("run", "")) for s in drill_steps), (
        "джоба обязана проверять вердикт drill (pass) отдельным шагом"
    )
    # Ручные ворота (Windows lifecycle, production Authenticode) — отдельная
    # джоба-документация, не выдающая себя за автоматическую проверку.
    assert "manual-gates" in jobs, "manual-gates (Windows/Authenticode) — отдельной джобой"
    manual_text = str(jobs["manual-gates"])
    assert "Authenticode" in manual_text and "Windows" in manual_text
    assert "pilot-drill.sh" not in manual_text and "pytest" not in manual_text


def test_drill_script_exists_and_is_executable() -> None:
    assert DRILL.exists(), "infra/scripts/pilot-drill.sh должен существовать"
    assert DRILL.stat().st_mode & 0o111, "drill должен быть исполняемым"
    text = DRILL.read_text(encoding="utf-8")
    assert text.startswith("#!"), "drill — bash-скрипт с шебангом"
    assert "set -euo pipefail" in text
    # Non-zero exit при провале И при неполном прогоне — контракт CI.
    assert "exit 1" in text
    # Вердикты: pass / fail / incomplete — skip никогда не становится pass.
    assert '"incomplete"' in text
    assert 'verdict="pass"' in text
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
    # Секреты прогона не попадают в ОТЧЁТ: генератор отчёта (python-heredoc)
    # пишет только коды стадий/счётчики/контрольные суммы.
    start = text.index('python3 - "$REPORT_JSON"')
    report_block = text[start : text.index("\nPY\n", start)]
    for secret_var in ("OWNER_PASSWORD", "ENGINE_TOKEN", "PG_PASSWORD", "SIGNING_KEY"):
        assert secret_var not in report_block, f"{secret_var} не должен фигурировать в отчёте"


def test_drill_checks_fail_closed_negative_stages() -> None:
    """Дефектные артефакты канала обязаны проверяться ДО изменения установки."""
    text = DRILL.read_text(encoding="utf-8")
    assert "manifest_bad_signature" in text
    assert "package_hash_mismatch" in text
    assert "download_failed" in text  # truncated/повреждённый пакет (размер)
    assert "bad_url" in text
    # Отказы канала не приводят к установке.
    assert "install_blocked_after_tamper" in text
    # Данные переживают «обновление» и рестарт стека без purge.
    assert "data_preserved_after_update" in text
    assert "synthetic_data_after_restart" in text
    # Resume: повторный опрос выдаёт ту же команду установки.
    assert "engine_resume_delivery" in text


def test_drill_uses_unique_isolated_compose_project() -> None:
    """Каждый прогон — свой Compose-проект: уникальное имя + -p в КАЖДОЙ
    compose-команде (helper dc()); «голых» вызовов docker compose нет."""
    text = DRILL.read_text(encoding="utf-8")
    assert 'PROJECT_NAME="hrm-pilot-drill-$(date -u +%Y%m%d%H%M%S)-$$"' in text
    assert 'docker compose -p "$PROJECT_NAME" --env-file "$ENV_FILE"' in text
    # Все обращения к compose идут через dc() — прямых вызовов нет.

    direct = [
        line
        for line in text.splitlines()
        if line.lstrip().startswith("docker compose")
        and "-p" not in line
        and "docker compose version" not in line  # version — не проектная команда
    ]
    assert not direct, f"compose-команды без -p $PROJECT_NAME: {direct}"
    # Уникальное имя попадает в evidence (изоляция прогона доказуема).
    assert '"compose_project"' in text


def test_drill_mandatory_stage_registry_covers_acceptance() -> None:
    """Список MANDATORY_PENDING покрывает ВСЕ обязательные стадии приёмки и
    совпадает с фактически вызываемыми stage_pass/stage_fail/stage_skip."""
    import re

    text = DRILL.read_text(encoding="utf-8")
    match = re.search(r"MANDATORY_PENDING=\(\n(.*?)^\)", text, re.DOTALL | re.MULTILINE)
    assert match, "список MANDATORY_PENDING не найден"
    registered = {line.strip() for line in match.group(1).splitlines() if line.strip()}
    called = set(re.findall(r'stage_(?:pass|fail|skip)\s+"([a-z0-9_]+)"', text))
    # redirect-стадии вызываются через helper redirect_case "имя-стадии".
    called |= set(re.findall(r'redirect_case\s+"([a-z0-9_]+)"', text))
    # Каждая вызываемая стадия зарегистрирована (и наоборот — включая
    # cleanup-стадии, записываемые в _record_cleanup_stages).
    assert called == registered, (
        f"расхождение стадий: только в коде={sorted(called - registered)}, "
        f"только в реестре={sorted(registered - called)}"
    )
    required = {
        # readiness обеих публичных точек
        "backend_ready",
        "frontend_ready",
        # first-run и синтетика с чтением назад
        "first_run_claim",
        "first_run_redeem",
        "synthetic_data_created",
        "synthetic_data_verified",
        # реальные байты бэкапа + restore с маркером синтетики
        "encrypted_backup",
        "backup_bytes_verified",
        "restore_drill",
        # подписанный канал: download со сверкой SHA256+размера
        "channel_check",
        "channel_download",
        "install_requested",
        "engine_resume_delivery",
        "update_completed",
        "data_preserved_after_update",
        # полный tamper-suite
        "tampered_signature_rejected",
        "tampered_manifest_rejected",
        "forbidden_redirect_rejected",
        "unsafe_scheme_redirect_rejected",
        "protocol_relative_redirect_rejected",
        "traversal_url_rejected",
        "corrupted_package_rejected",
        "truncated_package_rejected",
        "install_blocked_after_tamper",
        # restart-persistence: данные по полям, бэкап неизменен
        "services_restarted",
        "synthetic_data_after_restart",
        "backup_unchanged_after_restart",
        # cleanup без остатков
        "cleanup_down_v",
        "no_residual_resources",
    }
    assert required <= registered, f"не хватает стадий: {sorted(required - registered)}"


def test_drill_verdict_never_pass_with_skipped_mandatory() -> None:
    """Вердикт pass невозможен при skip/fail обязательной стадии; evidence
    пишется даже при аномальном выходе (trap EXIT → write_reports)."""
    text = DRILL.read_text(encoding="utf-8")
    assert 'elif [ "$SKIP" -gt 0 ]; then verdict="incomplete"' in text
    assert 'if [ "$FAIL" -gt 0 ] || [ "$SKIP" -gt 0 ]; then' in text
    # Аномальный выход (set -e/сигнал) всё равно оставляет evidence.
    assert 'write_reports "incomplete"' in text
    assert "trap on_exit EXIT" in text
    # Недоступный docker — НЕ pass: override на incomplete.
    assert 'VERDICT_OVERRIDE="incomplete"' in text
    assert '[ -n "$VERDICT_OVERRIDE" ] && verdict="$VERDICT_OVERRIDE"' in text


def test_drill_backup_bytes_and_restore_marker() -> None:
    """Байты бэкапа реально извлекаются и проверяются; restore drill обязан
    проверять восстановленную синтетику; бэкап неизменен после restart."""
    text = DRILL.read_text(encoding="utf-8")
    assert 'dc cp "backup:$BACKUP_PATH_IN_CONTAINER"' in text
    assert 'stat -c %s "$WORKDIR/backup-downloaded.enc"' in text
    assert 'sha256sum "$WORKDIR/backup-downloaded.enc"' in text
    assert "BACKUP_DRILL_EXPECT_CANDIDATE_EMAIL=" in text
    # Сверка SHA-256 бэкапа до/после restart.
    assert "backup_unchanged_after_restart" in text
    assert "sha256sum '$BACKUP_PATH_IN_CONTAINER'" in text
    # Staging-файл клиент называет по ПЕРВЫМ 12 символам release_sha
    # (латентный баг старого drill: путь из полного sha не существовал).
    assert "release-${CHANNEL_SHA:0:12}.zip" in text


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


# --- Регрессия rework-ревью: per-version release fixtures ------------------------


def test_drill_snapshots_declare_their_own_version() -> None:
    """Каждый релизный артефакт drill строится из СВОЕГО снимка.

    Регрессия: make_snapshot хардкодил version 0.14.0, один снимок
    переиспользовался для канала 0.15.0 — publish_channel.py отвергал
    артефакт (bad_release_json), drill умирал с exit 2 до Docker Compose.
    """
    text = DRILL.read_text(encoding="utf-8")
    # Источник бага — хардкод версии внутри make_snapshot — исчезает.
    assert '"version": "0.14.0"' not in text
    # make_snapshot параметризован версией и sha; безверсионного снимка нет.
    assert "make_snapshot() { # make_snapshot <dir> <version> <release-sha>" in text
    assert '"$WORKDIR/snapshot" --version' not in text
    # Снимок готовится отдельно для каждой публикуемой версии.
    assert (
        'make_snapshot "$WORKDIR/snapshot-$CHANNEL_VERSION" "$CHANNEL_VERSION" "$CHANNEL_SHA"'
        in text
    )
    assert 'make_snapshot "$WORKDIR/snapshot-$NEXT_VERSION" "$NEXT_VERSION" "$NEXT_SHA"' in text
    # publish_channel получает снимок и версию ЯВНОЙ парой (рабочий канал
    # 0.14.0 + три канала негативных стадий 0.15.0: next/redirect/traversal).
    assert 'publish_channel "$WORKDIR/snapshot-$CHANNEL_VERSION" "$CHANNEL_VERSION"' in text
    assert text.count('publish_channel "$WORKDIR/snapshot-$NEXT_VERSION" "$NEXT_VERSION"') == 3, (
        "каналы негативных стадий (0.15.0) обязаны строиться из снимка 0.15.0"
    )
    # Cleanup удаляет все снимки прогона (включая per-version).
    assert '"$WORKDIR"/snapshot*' in text


def _drill_snapshot(target: Path, version: str, release_sha: str) -> Path:
    """Снимок релиза с согласованным release.json (семантика make_snapshot)."""
    shutil.copytree(TESTDATA / "snapshot", target)
    release = {
        "version": version,
        "release_sha": release_sha,
        "built_at": "2026-09-14T00:00:00Z",
    }
    (target / "release.json").write_text(json.dumps(release), encoding="utf-8")
    return target


def _publish(
    snapshot: Path,
    version: str,
    release_sha: str,
    out_dir: Path,
    package_url: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Публикация канала из снимка (контракт drill: snapshot+version парой)."""
    command = [
        sys.executable,
        str(RELEASE / "publish_channel.py"),
        "--snapshot",
        str(snapshot),
        "--version",
        version,
        "--release-sha",
        release_sha,
        "--package-url",
        package_url or f"https://channel:8443/hr-manager-windows-{version}.zip",
        "--minimum-supported-version",
        "0.13.0",
        "--notes-ru",
        "Pilot drill (fixture key, NOT production)",
        "--private-key",
        str(TESTDATA / "test_key.priv"),
        "--key-id",
        "pilot-test-key",
        "--public-keys-json",
        str(TESTDATA / "trusted_keys.json"),
        "--out-dir",
        str(out_dir),
    ]
    return subprocess.run(command, capture_output=True, text=True, timeout=120, check=False)


def test_drill_publishes_two_consecutive_versions(tmp_path: Path) -> None:
    """Две последовательные версии публикуются из согласованных снимков.

    Полная цепочка согласованности fixture/version: release.json version ==
    имя пакета (версия в имени) == версия release.json ВНУТРИ пакета ==
    version manifest == ожидаемая целевая версия; SHA-256 и размер пакета
    совпадают с манифестом; подпись манифеста независимо проверяется
    публичным ключом клиента.
    """
    import hashlib

    for version, sha in (("0.14.0", "2" * 40), ("0.15.0", "4" * 40)):
        snapshot = _drill_snapshot(tmp_path / f"snapshot-{version}", version, sha)
        out_dir = tmp_path / f"channel-{version}"
        result = _publish(snapshot, version, sha, out_dir)
        assert result.returncode == 0, result.stderr

        package = out_dir / f"hr-manager-windows-{version}.zip"
        manifest = json.loads((out_dir / "update-channel.json").read_text(encoding="utf-8"))
        assert package.exists(), "артефакт не создан"
        # Целевая версия: manifest декларирует именно её.
        assert manifest["version"] == version
        assert manifest["release_sha"] == sha
        # release.json ВНУТРИ пакета декларирует именно эту версию.
        with zipfile.ZipFile(package) as archive:
            inner = json.loads(archive.read("release.json"))
        assert inner["version"] == version, "release.json в пакете декларирует чужую версию"
        assert inner["release_sha"] == sha
        # SHA-256 и размер ФАКТИЧЕСКИХ байтов пакета совпадают с manifest.
        package_bytes = package.read_bytes()
        assert manifest["package_sha256"] == hashlib.sha256(package_bytes).hexdigest()
        assert manifest["package_size"] == len(package_bytes)
        # Подпись манифеста проверяется независимо публичным ключом клиента.
        verification = subprocess.run(
            [
                sys.executable,
                str(RELEASE / "verify_channel.py"),
                "--manifest",
                str(out_dir / "update-channel.json"),
                "--public-key",
                str(TESTDATA / "test_key.pub"),
            ],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        assert verification.returncode == 0, verification.stderr


def test_drill_publishes_traversal_channel_for_negative_stage(tmp_path: Path) -> None:
    """Traversal package_url (dot-segments) проходит публикацию (инструмент
    требует только https и отсутствие query/fragment) — негативная стадия
    drill строит подписанный manifest с таким URL, а ОТКАЗ — обязанность
    клиента (см. test_channel_network.py: bad_url ДО обращения)."""
    snapshot = _drill_snapshot(tmp_path / "snapshot-0.15.0", "0.15.0", "4" * 40)
    out_dir = tmp_path / "channel-traversal"
    result = _publish(
        snapshot,
        "0.15.0",
        "4" * 40,
        out_dir,
        package_url="https://channel:8443/../../../etc/hostname",
    )
    assert result.returncode == 0, result.stderr
    manifest = json.loads((out_dir / "update-channel.json").read_text(encoding="utf-8"))
    assert manifest["package_url"] == "https://channel:8443/../../../etc/hostname"


@pytest.mark.parametrize(
    ("publish_version", "publish_sha"),
    [
        pytest.param("0.15.0", "4" * 40, id="snapshot-0.14-published-as-0.15"),
        pytest.param("0.14.0", "4" * 40, id="sha-mismatch"),
    ],
)
def test_drill_rejects_inconsistent_snapshot(
    tmp_path: Path, publish_version: str, publish_sha: str
) -> None:
    """Несогласованный снимок отвергается fail closed (bad_release_json):
    исходный баг ревью (снимок 0.14.0 публиковался как 0.15.0) и
    несовпадение release_sha. Артефакты при этом не создаются."""
    snapshot = _drill_snapshot(tmp_path / "snapshot-0.14.0", "0.14.0", "2" * 40)
    out_dir = tmp_path / "out"
    result = _publish(snapshot, publish_version, publish_sha, out_dir)
    assert result.returncode == 1, "несогласованный снимок обязан отвергаться"
    assert "bad_release_json" in result.stderr
    assert not (out_dir / f"hr-manager-windows-{publish_version}.zip").exists()
