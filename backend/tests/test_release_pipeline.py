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


def _run_publish(tmp_path: Path, extra: list[str]) -> subprocess.CompletedProcess:
    snapshot = tmp_path / "app"
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
