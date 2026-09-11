"""Fixture-тесты release workflow Phase 13 (rework #1) БЕЗ production secret.

publish_channel.py — исполняемое ядро `.github/workflows/update-channel.yml`;
здесь проверяются: happy path (fixture-ключ), неверная подпись, fail-closed
без signing key, а также структурные инварианты самого workflow YAML.
Fixture-ключ разрешён ТОЛЬКО тестам и не входит в production trust store.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
TESTDATA = REPO / "infra" / "release" / "testdata"
RELEASE = REPO / "infra" / "release"
# После переноса владельцем workflow лежит в .github/workflows/; до
# переноса (GitHub App сессии без права `workflows` не может его запушить)
# инварианты проверяются на точной копии из review-artifacts/.
_WORKFLOW_IN_TREE = REPO / ".github" / "workflows" / "update-channel.yml"
WORKFLOW = (
    _WORKFLOW_IN_TREE
    if _WORKFLOW_IN_TREE.exists()
    else REPO / "review-artifacts" / "update-channel.yml"
)
PACKAGE_URL = (
    "https://github.com/sledovatel61/HR-Manager/releases/download/"
    "v0.14.0/hr-manager-windows-0.14.0.zip"
)


def _run_publish(
    tmp_path: Path, extra: list[str], snapshot_dir: Path | None = None
) -> subprocess.CompletedProcess:
    snapshot = snapshot_dir if snapshot_dir is not None else tmp_path / "app"
    if snapshot_dir is None:
        shutil.copytree(TESTDATA / "snapshot", snapshot)
    out_dir = tmp_path / "dist" / "channel"
    command = [
        sys.executable,
        str(RELEASE / "publish_channel.py"),
        "--snapshot",
        str(snapshot),
        "--version",
        "0.14.0",
        "--release-sha",
        "2" * 40,
        "--package-url",
        PACKAGE_URL,
        "--minimum-supported-version",
        "0.13.0",
        "--notes-ru",
        "Тестовый канал обновлений (fixture-ключ, НЕ production).",
        "--public-keys-json",
        str(TESTDATA / "trusted_keys.json"),
        "--out-dir",
        str(out_dir),
        *extra,
    ]
    return subprocess.run(command, capture_output=True, text=True, timeout=120)


def test_publish_happy_path_fixture_key(tmp_path: Path) -> None:
    result = _run_publish(
        tmp_path, ["--private-key", str(TESTDATA / "test_key.priv"), "--key-id", "pilot-test-key"]
    )
    assert result.returncode == 0, result.stderr
    out_dir = tmp_path / "dist" / "channel"
    manifest_path = out_dir / "update-channel.json"
    package_path = out_dir / "hr-manager-windows-0.14.0.zip"
    assert manifest_path.exists()
    assert package_path.exists()
    assert (out_dir / "SHA256SUMS").exists()

    # Подпись проверяется независимо — публичным ключом клиента (не тем,
    # что получен из подписывающего ключа).
    verification = subprocess.run(
        [
            sys.executable,
            str(RELEASE / "verify_channel.py"),
            "--manifest",
            str(manifest_path),
            "--public-key",
            str(TESTDATA / "test_key.pub"),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert verification.returncode == 0, verification.stderr

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    import hashlib

    package_sha = hashlib.sha256(package_path.read_bytes()).hexdigest()
    assert manifest["package_size"] == package_path.stat().st_size
    assert manifest["package_sha256"] == package_sha
    sums = (out_dir / "SHA256SUMS").read_text(encoding="utf-8")
    assert f"{package_sha}  hr-manager-windows-0.14.0.zip" in sums
    assert hashlib.sha256(manifest_path.read_bytes()).hexdigest() in sums


def test_publish_bad_signature_fails_verification(tmp_path: Path) -> None:
    result = _run_publish(
        tmp_path, ["--private-key", str(TESTDATA / "test_key.priv"), "--key-id", "pilot-test-key"]
    )
    assert result.returncode == 0, result.stderr
    manifest_path = tmp_path / "dist" / "channel" / "update-channel.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    sig = manifest["signature"]["sig"]
    # Портится подпись: первый hex-символ инвертируется.
    manifest["signature"]["sig"] = ("0" if sig[0] != "0" else "1") + sig[1:]
    tampered = tmp_path / "tampered.json"
    tampered.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    verification = subprocess.run(
        [
            sys.executable,
            str(RELEASE / "verify_channel.py"),
            "--manifest",
            str(tampered),
            "--public-key",
            str(TESTDATA / "test_key.pub"),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert verification.returncode != 0


def test_publish_fail_closed_without_signing_key(tmp_path: Path) -> None:
    result = _run_publish(tmp_path, [])  # без --private-key
    assert result.returncode != 0
    assert "missing_signing_key" in result.stderr
    # Unsigned stable manifest НЕ создан.
    assert not (tmp_path / "dist" / "channel" / "update-channel.json").exists()


def test_publish_fail_closed_without_trust_store(tmp_path: Path) -> None:
    # Ключ есть, но независимая проверка невозможна — тоже отказ.
    snapshot = tmp_path / "app"
    shutil.copytree(TESTDATA / "snapshot", snapshot)
    command = [
        sys.executable,
        str(RELEASE / "publish_channel.py"),
        "--snapshot",
        str(snapshot),
        "--version",
        "0.14.0",
        "--release-sha",
        "2" * 40,
        "--package-url",
        PACKAGE_URL,
        "--minimum-supported-version",
        "0.13.0",
        "--public-keys-json",
        str(tmp_path / "no-such-file.json"),
        "--private-key",
        str(TESTDATA / "test_key.priv"),
        "--key-id",
        "pilot-test-key",
        "--out-dir",
        str(tmp_path / "out"),
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=120)
    assert result.returncode != 0


def _workflow_data() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _resolve_release_script() -> str:
    """run-скрипт шага «Resolve release facts» channel-джоба (точная копия)."""
    data = _workflow_data()
    jobs = data["jobs"]
    for step in jobs["channel-release"]["steps"]:
        if step.get("name") == "Resolve release facts (protected SemVer tag or owner dispatch)":
            return step["run"]
    raise AssertionError("resolve release step не найден в workflow")


def _run_resolve_script(
    script: str, tmp_path: Path, version: str, notes: str
) -> subprocess.CompletedProcess:
    """Реальное исполнение resolve-скрипта bash'ем с поддельным GITHUB_OUTPUT.

    INPUT_SHA — текущий HEAD тестового репозитория (checkout внутри скрипта
    становится no-op); атакующие/валидные notes передаются через env, как
    в настоящем workflow_dispatch.
    """
    import os
    import shutil

    if shutil.which("bash") is None:
        pytest.skip("bash требуется для исполняемого теста resolve-шага")
    head_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True, text=True, check=True
    ).stdout.strip()
    output_file = tmp_path / "github-output.txt"
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", str(tmp_path)),
        "EVENT_NAME": "workflow_dispatch",
        "GITHUB_REF_NAME": "arena/01a084e4-hr-manager",
        "INPUT_VERSION": version,
        "INPUT_SHA": head_sha,
        "INPUT_NOTES": notes,
        "GITHUB_OUTPUT": str(output_file),
        "GITHUB_REPOSITORY": "sledovatel61/HR-Manager",
    }
    return subprocess.run(
        ["bash", "-c", script], cwd=REPO, env=env, capture_output=True, text=True, timeout=120
    )


@contextmanager
def _git_checkout_safe_restore() -> Iterator[None]:
    """Скрипт может перевести репозиторий в detached HEAD (checkout no-op
    по SHA); после теста восстанавливаем ветку."""
    branch = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=REPO, capture_output=True, text=True
    ).stdout.strip()
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True, text=True
    ).stdout.strip()
    try:
        yield
    finally:
        restore = head if branch == "HEAD" else branch
        subprocess.run(["git", "checkout", "-q", restore], cwd=REPO, check=True)


def test_dispatch_notes_crlf_rejected_before_output(tmp_path: Path) -> None:
    """CR/LF в notes_ru отклоняются fail closed ДО записи в $GITHUB_OUTPUT:
    поддельные строки-выводы (sha/tag/version/notes) не появляются."""
    script = _resolve_release_script()
    evil_sha = "d" * 40
    payloads = [
        f"Заметка релиза\nsha={evil_sha}",
        "Заметка релиза\rtag=v9.9.9",
        "Заметка релиза\nversion=9.9.9\nnotes=подделка",
    ]
    for payload in payloads:
        with _git_checkout_safe_restore():
            result = _run_resolve_script(script, tmp_path, "0.14.0", payload)
        assert result.returncode != 0, f"CR/LF не отклонён: {payload!r}"
        assert "single line" in result.stderr
        output_file = tmp_path / "github-output.txt"
        content = output_file.read_text(encoding="utf-8") if output_file.exists() else ""
        # Ни одной строки-вывода (в т.ч. подмешанных ключей) — отказ до записи.
        assert "sha=" not in content
        assert "tag=" not in content
        assert "version=" not in content
        assert "notes=" not in content


def test_dispatch_valid_single_line_russian_note_preserved(tmp_path: Path) -> None:
    """Валидная однострочная русская заметка сохраняется без изменений,
    и никакие лишние ключи-выводы не появляются."""
    script = _resolve_release_script()
    note = "Исправлены ошибки канала обновлений (Windows-пилот)"
    with _git_checkout_safe_restore():
        result = _run_resolve_script(script, tmp_path, "0.14.0", note)
    assert result.returncode == 0, result.stderr
    lines = (tmp_path / "github-output.txt").read_text(encoding="utf-8").splitlines()
    values: dict[str, str] = {}
    for line in lines:
        key, _, value = line.partition("=")
        assert key not in values, f"дублирующийся ключ-вывод: {key!r}"
        values[key] = value
    head_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True, text=True, check=True
    ).stdout.strip()
    assert values["version"] == "0.14.0"
    assert values["sha"] == head_sha
    assert values["notes"] == note  # русская заметка сохранена без изменений
    assert values["tag"] == "v0.14.0"
    assert values["package_url"].startswith(
        "https://github.com/sledovatel61/HR-Manager/releases/download/v0.14.0/"
    )
    assert len(lines) == 5  # ровно ожидаемые ключи, ничего подмешанного


def test_workflow_patch_applies_byte_exact(tmp_path: Path) -> None:
    """Патч — полноценный unified diff: реальное применение в отдельном
    временном каталоге создаёт byte-identical workflow.

    Раньше патч собирался без `@@`-hunk header: `git apply --check`
    проходил, но применение создавало пустой файл (0 байт). Одного
    `--check` недостаточно — тест реально применяет патч и сравнивает
    байты установленного файла с артефактом.
    """
    import shutil

    if shutil.which("git") is None:
        pytest.skip("git требуется для теста применения патча")
    patch_path = REPO / "review-artifacts" / "update-channel.patch"
    artifact_path = REPO / "review-artifacts" / "update-channel.yml"
    patch_text = patch_path.read_text(encoding="utf-8")
    assert "@@" in patch_text, "патч должен содержать unified hunk header (@@)"

    workdir = tmp_path / "apply"
    workdir.mkdir()
    checked = subprocess.run(
        ["git", "apply", "--check", str(patch_path)],
        cwd=workdir,
        capture_output=True,
        text=True,
    )
    assert checked.returncode == 0, checked.stderr
    applied = subprocess.run(
        ["git", "apply", str(patch_path)],
        cwd=workdir,
        capture_output=True,
        text=True,
    )
    assert applied.returncode == 0, applied.stderr
    installed = workdir / ".github" / "workflows" / "update-channel.yml"
    assert installed.exists(), "apply не создал .github/workflows/update-channel.yml"
    assert installed.stat().st_size > 0, "применение дало пустой файл (0 байт)"
    assert installed.read_bytes() == artifact_path.read_bytes(), (
        "установленный workflow не совпадает побайтово с review-artifacts/update-channel.yml"
    )


def test_workflow_yaml_security_invariants() -> None:
    assert WORKFLOW.exists(), (
        "workflow должен находиться в .github/workflows/ "
        "(или, до переноса владельцем, в review-artifacts/)"
    )
    data = _workflow_data()
    # PyYAML 6 читает ключ `on:` как boolean True (YAML 1.1); GitHub Actions
    # парсит YAML 1.2, где `on` остаётся строкой.
    triggers = data["on"] if "on" in data else data[True]
    # Production signing secret недоступен коду из PR/fork.
    assert "pull_request" not in triggers
    assert "push" in triggers
    assert triggers["push"]["tags"] == ["v[0-9]+.[0-9]+.[0-9]+"]
    jobs = data["jobs"]
    signing_job = jobs["channel-release"]
    # Секреты — только через environment (protection rules владельца).
    assert signing_job["environment"] == "update-channel-signing"
    steps_text = json.dumps(signing_job["steps"], ensure_ascii=False)
    assert "publish_channel.py" in steps_text
    assert "secrets.UPDATE_CHANNEL_SIGNING_KEY" in steps_text
    assert "secrets.UPDATE_CHANNEL_PUBLIC_KEYS" in steps_text
    # Публикация — только после шага независимой проверки (порядок шагов).
    names = [step.get("name", "") for step in signing_job["steps"]]
    assert "Build package, sign manifest, verify with client-trusted key" in names
    verify_index = names.index("Build package, sign manifest, verify with client-trusted key")
    publish_index = names.index("Publish immutable GitHub Release (draft, assets verified above)")
    assert verify_index < publish_index
    # Dispatch-безопасность: release_sha валидируется (40 hex), проверяется
    # его существование в репозитории, checkout выполняется по нему и HEAD
    # сверяется с SHA до сборки (и installer-джоб, и channel-джоб).
    installer_steps = json.dumps(jobs["windows-installer"]["steps"], ensure_ascii=False)
    channel_steps = json.dumps(signing_job["steps"], ensure_ascii=False)
    for text in (installer_steps, channel_steps):
        assert "git cat-file -e" in text  # SHA существует в репозитории
        assert "checkout HEAD != release_sha" in text  # сверка HEAD с SHA
        assert "git checkout -q" in text  # checkout строго по SHA
    # Значения dispatch-пользователя не попадают в shell напрямую — только
    # через env (никаких inline-expressions от пользователя в run-блоках).
    for step in jobs["windows-installer"]["steps"] + signing_job["steps"]:
        run = step.get("run", "") if isinstance(step, dict) else ""
        assert "${{ inputs." not in run, f"user input inlined in run block: {run[:80]}"
        assert "${{ github.event_name }}" not in run
    # Тег/релиз создаётся --target на тот же SHA, из которого собрано.
    assert "gh release create" in channel_steps
    assert "--target" in channel_steps
    # Реальная fail-closed логика CR/LF: отклонение notes происходит ДО
    # записи notes в $GITHUB_OUTPUT (исполняемо проверяется отдельными
    # тестами test_dispatch_notes_crlf_rejected_before_output и
    # test_dispatch_valid_single_line_russian_note_preserved).
    resolve_script = _resolve_release_script()
    assert 'reject_newline "$notes" "notes_ru"' in resolve_script
    assert "*$'\\r'*|*$'\\n'*" in resolve_script
    reject_idx = resolve_script.index('reject_newline "$notes" "notes_ru"')
    write_idx = resolve_script.index('echo "notes=$notes" >> "$GITHUB_OUTPUT"')
    assert reject_idx < write_idx
    # Deploy/rollback workflow не затронут.
    assert (REPO / ".github" / "workflows" / "release.yml").exists()


# --- Phase 14: две подписи + trust store в workflow ------------------------------

# GitHub App сессии не имеет права `workflows`: Phase 14-версия update-channel
# workflow публикуется в review-artifacts/ и переносится владельцем. Тесты
# проверяют её там; после переноса владельцем — автоматически in-tree.
_PHASE14_MARKER = "installer-signing"
_WORKFLOW_IN_TREE_P14 = REPO / ".github" / "workflows" / "update-channel.yml"
_WORKFLOW_P14 = (
    _WORKFLOW_IN_TREE_P14
    if _WORKFLOW_IN_TREE_P14.exists()
    and _PHASE14_MARKER in _WORKFLOW_IN_TREE_P14.read_text(encoding="utf-8")
    else REPO / "review-artifacts" / "update-channel.phase14.yml"
)


def _phase14_workflow_data() -> dict:
    assert _WORKFLOW_P14.exists(), (
        "Phase 14 workflow не найден ни в .github/workflows/ (перенос владельца), "
        "ни в review-artifacts/update-channel.phase14.yml"
    )
    return yaml.safe_load(_WORKFLOW_P14.read_text(encoding="utf-8"))


# --- Phase 14: встроенный trust store в publish_channel -------------------------


def _write_embedded_trust_store(snapshot: Path, payload: dict) -> None:
    (snapshot / "release-trust-store.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _production_embedded() -> dict:
    flat = json.loads((TESTDATA / "trusted_keys.json").read_text(encoding="utf-8"))
    return {"schema_version": 1, "environment": "production", "keys": flat}


def _publish_with_embedded(tmp_path: Path, embedded: dict) -> subprocess.CompletedProcess:
    snapshot = tmp_path / "app"
    shutil.copytree(TESTDATA / "snapshot", snapshot)
    _write_embedded_trust_store(snapshot, embedded)
    return _run_publish(
        tmp_path,
        ["--private-key", str(TESTDATA / "test_key.priv"), "--key-id", "pilot-test-key"],
        snapshot_dir=snapshot,
    )


def test_publish_embedded_trust_store_published_and_checksummed(tmp_path: Path) -> None:
    """Совпадающий встроенный trust store публикуется как trust-store.json."""
    result = _publish_with_embedded(tmp_path, _production_embedded())
    assert result.returncode == 0, result.stderr
    out_dir = tmp_path / "dist" / "channel"
    trust_path = out_dir / "trust-store.json"
    assert trust_path.exists()
    published = json.loads(trust_path.read_text(encoding="utf-8"))
    assert published["environment"] == "production"
    assert set(published["keys"]) == {"pilot-test-key", "pilot-revoked-key"}
    # trust-store.json включён в SHA256SUMS с корректным hash.
    import hashlib

    trust_sha = hashlib.sha256(trust_path.read_bytes()).hexdigest()
    sums = (out_dir / "SHA256SUMS").read_text(encoding="utf-8")
    assert f"{trust_sha}  trust-store.json" in sums
    # Внутри пакета встроенный store тоже детерминированно присутствует.
    assert (tmp_path / "app" / "release-trust-store.json").exists()
    # Никакого private material в опубликованном артефакте.
    private_material = (TESTDATA / "test_key.priv").read_text(encoding="utf-8").strip()
    assert private_material not in trust_path.read_text(encoding="utf-8")


def test_publish_embedded_trust_store_mismatch_blocks_release(tmp_path: Path) -> None:
    """Подмена встроенного trust store блокирует выпуск (fail closed)."""
    embedded = _production_embedded()
    embedded["keys"]["pilot-test-key"]["key"] = "RdoOG6nUyIJr4vqrLPQD36UISqCFrLov+HgDcisGAAA="
    result = _publish_with_embedded(tmp_path, embedded)
    assert result.returncode != 0
    assert "trust_store_mismatch" in result.stderr
    out_dir = tmp_path / "dist" / "channel"
    assert not (out_dir / "update-channel.json").exists()
    assert not (out_dir / "trust-store.json").exists()
    assert not (out_dir / "SHA256SUMS").exists()


def test_publish_embedded_trust_store_extra_key_blocks_release(tmp_path: Path) -> None:
    """Лишний ключ во встроенном store ≠ release metadata → блокировка."""
    embedded = _production_embedded()
    embedded["keys"]["rogue-key"] = {
        "key": "AdoOG6nUyIJr4vqrLPQD36UISqCFrLov+HgDcisGKxM=",
        "revoked": False,
    }
    result = _publish_with_embedded(tmp_path, embedded)
    assert result.returncode != 0
    assert "trust_store_mismatch" in result.stderr


def test_publish_embedded_trust_store_private_material_blocks(tmp_path: Path) -> None:
    """Private material в встроенном store — ошибка строгой схемы."""
    embedded = _production_embedded()
    embedded["keys"]["pilot-test-key"]["private"] = "f" * 64
    result = _publish_with_embedded(tmp_path, embedded)
    assert result.returncode != 0
    assert "private_material" in result.stderr
    assert not (tmp_path / "dist" / "channel" / "update-channel.json").exists()


def test_publish_embedded_trust_store_test_env_never_production_root(tmp_path: Path) -> None:
    """Dev/test store не становится молча production trust root."""
    embedded = _production_embedded()
    embedded["environment"] = "test"
    result = _publish_with_embedded(tmp_path, embedded)
    assert result.returncode != 0
    assert "test_trust_store" in result.stderr


def test_check_embedded_trust_store_defense_in_depth(tmp_path: Path) -> None:
    """Прямые ветки unknown_key/revoked_key в _check_embedded_trust_store.

    Через CLI эти ветки экранируются более ранними проверками (flat-проверка
    ключа подписи, затем stores_match) — они защищают от регрессий порядка
    проверок и вызываются здесь напрямую.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "publish_channel_module", RELEASE / "publish_channel.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    other_key = "RdoOG6nUyIJr4vqrLPQD36UISqCFrLov+HgDcisGKxM="
    snapshot = tmp_path / "app"
    snapshot.mkdir()

    # unknown_key: встроенный store совпадает с release store, но ключа
    # подписи в обоих нет.
    flat_without = {"another-key": {"key": other_key, "revoked": False}}
    _write_embedded_trust_store(
        snapshot, {"schema_version": 1, "environment": "production", "keys": flat_without}
    )
    with pytest.raises(module.ChannelError) as excinfo:
        module._check_embedded_trust_store(snapshot, flat_without, "pilot-test-key")
    assert excinfo.value.code == "unknown_key"

    # revoked_key: совпадающие store, ключ подписи отозван в обоих.
    flat_revoked = {
        "pilot-test-key": {"key": other_key, "revoked": True},
        "another-key": {"key": "D83KWJq/Tb9ETFv8x2gNe7DvOfZM0vMY4YKH+aQVvy0=", "revoked": False},
    }
    _write_embedded_trust_store(
        snapshot, {"schema_version": 1, "environment": "production", "keys": flat_revoked}
    )
    with pytest.raises(module.ChannelError) as excinfo:
        module._check_embedded_trust_store(snapshot, flat_revoked, "pilot-test-key")
    assert excinfo.value.code == "revoked_key"


# --- Phase 14: инварианты двух независимых подписей в workflow -------------------


def test_workflow_yaml_phase14_signing_invariants() -> None:
    """Authenticode-контракт workflow: fail-closed, secrets только в env,
    production-режим на тегах, побайтовая сверка встроенного trust store,
    публикация trust-store.json как артефакта канала."""
    data = _phase14_workflow_data()
    jobs = data["jobs"]
    installer_job = jobs["windows-installer"]
    signing_job = jobs["channel-release"]

    # Authenticode-секреты — в ОТДЕЛЬНОМ environment installer-signing.
    assert installer_job["environment"] == "installer-signing"
    # Режим подписи экспортируется в channel-release (проверка той же политикой).
    assert installer_job["outputs"]["signing_mode"]

    installer_text = json.dumps(installer_job["steps"], ensure_ascii=False)
    # Подпись выполняется sign-installer.ps1, production требует секреты.
    assert "sign-installer.ps1" in installer_text
    assert "secrets.INSTALLER_AUTHENTICODE_PFX_BASE64" in installer_text
    # Проверка контракта ПОСЛЕ подписи — до upload артефакта.
    names = [step.get("name", "") for step in installer_job["steps"]]
    sign_index = names.index("Sign installer (Authenticode, fail closed)")
    upload_index = names.index("Upload signed installer artifact")
    assert sign_index < upload_index
    # Тег v* => всегда production (fail closed на отсутствие сертификата);
    # выбор пользователя попадает только в env, не в run-блок.
    sign_step = installer_job["steps"][sign_index]
    sign_run = sign_step.get("run", "")
    assert "${{ inputs." not in sign_run, "user input inlined in run block"
    assert "github.event_name == 'push' && 'production'" in json.dumps(sign_step["env"])

    # Trust store встраивается из защищённого входа и проверяется дважды.
    assert "secrets.INSTALLER_TRUST_STORE" in installer_text
    assert "-TrustStore" in installer_text

    channel_text = json.dumps(signing_job["steps"], ensure_ascii=False)
    assert "secrets.UPDATE_CHANNEL_PUBLIC_KEYS" in channel_text
    # Побайтовая сверка встроенного trust store с trust store канала —
    # ДО публикации релиза.
    channel_names = [step.get("name", "") for step in signing_job["steps"]]
    assert channel_names.index(
        "Verify installer signing contract and embedded trust store (fail closed)"
    ) < channel_names.index("Publish immutable GitHub Release (draft, assets verified above)")
    # trust-store.json публикуется как артефакт релиза.
    assert "dist/channel/trust-store.json" in channel_text


def test_workflow_phase14_patch_applies_byte_exact(tmp_path: Path) -> None:
    """Phase 14-патч — полноценный unified diff: применение к базовому
    workflow даёт byte-identical файл артефакта (инструкция переноса
    владельцем воспроизводима и проверяема)."""
    import shutil

    if shutil.which("git") is None:
        pytest.skip("git требуется для теста применения патча")
    patch_path = REPO / "review-artifacts" / "update-channel.phase14.patch"
    artifact_path = REPO / "review-artifacts" / "update-channel.phase14.yml"
    patch_text = patch_path.read_text(encoding="utf-8")
    assert "@@" in patch_text, "патч должен содержать unified hunk header (@@)"

    # Мини-репозиторий с базовой (Phase 13) версией workflow.
    workdir = tmp_path / "apply"
    (workdir / ".github" / "workflows").mkdir(parents=True)
    base = (
        _WORKFLOW_IN_TREE_P14.read_text(encoding="utf-8") if _WORKFLOW_IN_TREE_P14.exists() else ""
    )
    if _PHASE14_MARKER in base:
        pytest.skip("владелец уже перенёс Phase 14 workflow in-tree")
    (workdir / ".github" / "workflows" / "update-channel.yml").write_text(base, encoding="utf-8")
    checked = subprocess.run(
        ["git", "apply", "--check", str(patch_path)],
        cwd=workdir,
        capture_output=True,
        text=True,
    )
    assert checked.returncode == 0, checked.stderr
    applied = subprocess.run(
        ["git", "apply", str(patch_path)],
        cwd=workdir,
        capture_output=True,
        text=True,
    )
    assert applied.returncode == 0, applied.stderr
    result = (workdir / ".github" / "workflows" / "update-channel.yml").read_bytes()
    assert result == artifact_path.read_bytes(), "применённый патч != артефакт"
