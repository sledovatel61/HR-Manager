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
from pathlib import Path

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


def test_workflow_yaml_security_invariants() -> None:
    assert WORKFLOW.exists(), (
        "workflow должен находиться в .github/workflows/ "
        "(или, до переноса владельцем, в review-artifacts/)"
    )
    data = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
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
    # Deploy/rollback workflow не затронут.
    assert (REPO / ".github" / "workflows" / "release.yml").exists()
