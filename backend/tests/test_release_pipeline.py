"""Fixture-тесты release workflow Phase 13 (rework #1) БЕЗ production secret.

publish_channel.py — исполняемое ядро `.github/workflows/update-channel.yml`;
здесь проверяются: happy path (fixture-ключ), неверная подпись, fail-closed
без signing key, а также структурные инварианты самого workflow YAML.
Fixture-ключ разрешён ТОЛЬКО тестам и не входит в production trust store.
"""

from __future__ import annotations

import json
import os
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


_CI_GATE_JOB = "ci-gate"
_CI_GATE_RESOLVE_STEP = "Resolve exact release SHA (tag ref or owner dispatch)"
_CI_GATE_CHECK_STEP = "Require successful CI run for exact release SHA (fail closed)"

# Mock `gh`: отвечает заканоненным JSON и fail closed, если workflow-скрипт
# запрашивает CI не для exact SHA (в URL нет head_sha=<MOCK_GH_SHA>).
# Значение GH_TOKEN mock'ом не читается и не печатается.
_MOCK_GH_SCRIPT = """#!/usr/bin/env bash
set -euo pipefail
if [ "${1:-}" != "api" ]; then
  echo "mock-gh: unsupported invocation: $*" >&2
  exit 64
fi
url="${2:-}"
case "$url" in
  *"head_sha=${MOCK_GH_SHA:?}"*) ;;
  *)
    echo "mock-gh: query is not pinned to exact SHA head_sha" >&2
    exit 65
    ;;
esac
if [ "${MOCK_GH_EXIT:-0}" -ne 0 ]; then
  echo "HTTP 403: Resource not accessible by integration" >&2
  exit "${MOCK_GH_EXIT}"
fi
cat "${MOCK_GH_RESPONSE:?}"
"""


def _ci_gate_step_run(name: str) -> str:
    """run-скрипт именованного шага job `ci-gate` (точная копия)."""
    data = _workflow_data()
    for step in data["jobs"][_CI_GATE_JOB]["steps"]:
        if step.get("name") == name:
            return step["run"]
    raise AssertionError(f"ci-gate step не найден в workflow: {name}")


def _transitive_needs(job_name: str, jobs: dict) -> set[str]:
    """Transitive closure прямых `needs` job (граф запуска GitHub Actions)."""
    seen: set[str] = set()
    stack = [job_name]
    while stack:
        current = stack.pop()
        needs = jobs[current].get("needs", [])
        if isinstance(needs, str):
            needs = [needs]
        for dependency in needs:
            if dependency not in seen:
                seen.add(dependency)
                stack.append(dependency)
    return seen


def _ci_run(sha: str, **overrides: object) -> dict:
    run: dict = {
        "id": 35754211766,
        "name": "CI",
        "head_sha": sha,
        "status": "completed",
        "conclusion": "success",
        "event": "push",
    }
    run.update(overrides)
    return run


def _run_ci_gate(
    tmp_path: Path,
    script: str,
    *,
    sha: str = "a" * 40,
    payload: dict | None = None,
    gh_exit: int = 0,
) -> subprocess.CompletedProcess:
    """Исполнение CI-check скрипта ci-gate с mock `gh` на первом месте PATH.

    Успех требует (1) корректного вывода mock'а, (2) запроса с
    head_sha=<exact SHA> — mock отказывает на любом другом URL, (3) разбора
    status/conclusion самим скриптом: exit code `gh` без успешного conclusion
    успехом не считается.
    """
    bash_path = _find_real_bash()
    if bash_path is None:
        pytest.skip("исполняемый bash недоступен: тест ci-gate пропущен, а не ослаблен")
    if shutil.which("python3") is None:
        pytest.skip("python3 недоступен: тест ci-gate пропущен, а не ослаблен")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    gh_mock = bin_dir / "gh"
    gh_mock.write_text(_MOCK_GH_SCRIPT, encoding="utf-8")
    gh_mock.chmod(0o755)
    response = tmp_path / "gh-runs.json"
    body = payload if payload is not None else {"workflow_runs": []}
    response.write_text(json.dumps(body), encoding="utf-8")
    env = {
        "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', os.defpath)}",
        "HOME": str(tmp_path),
        "GH_TOKEN": "unit-test-placeholder-token",
        "GITHUB_REPOSITORY": "sledovatel61/HR-Manager",
        "SHA": sha,
        "MOCK_GH_RESPONSE": str(response),
        "MOCK_GH_SHA": sha,
        "MOCK_GH_EXIT": str(gh_exit),
    }
    return subprocess.run(
        [bash_path, "-c", script],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def _run_ci_gate_resolve(
    tmp_path: Path,
    script: str,
    *,
    event_name: str,
    input_sha: str,
) -> subprocess.CompletedProcess:
    """Исполнение resolve-скрипта ci-gate с поддельным GITHUB_OUTPUT."""
    bash_path = _find_real_bash()
    if bash_path is None:
        pytest.skip("исполняемый bash недоступен: тест ci-gate пропущен, а не ослаблен")
    output_file = tmp_path / "github-output.txt"
    env = {
        "PATH": os.environ.get("PATH", os.defpath),
        "HOME": str(tmp_path),
        "EVENT_NAME": event_name,
        "INPUT_SHA": input_sha,
        "GITHUB_OUTPUT": str(output_file),
    }
    return subprocess.run(
        [bash_path, "-c", script],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def _ci_gate_output(tmp_path: Path) -> str:
    output_file = tmp_path / "github-output.txt"
    return output_file.read_text(encoding="utf-8") if output_file.exists() else ""


def _find_real_bash() -> str | None:
    """Возвращает путь к действительно исполняемому ``bash`` или ``None``.

    Одного ``shutil.which("bash")`` недостаточно: на машине с включённым, но
    не установленным дистрибутивом WSL ``C:\\Windows\\System32\\bash.exe`` —
    это shim, который формально находится в PATH и даже стартует как процесс,
    но команды как POSIX-shell не исполняет. Поэтому исполнимость проверяется
    фактическим запуском зонда.

    Решение принимается ТОЛЬКО по коду возврата и по наличию собственного
    ASCII-маркера в stdout — никогда по тексту чужого сообщения об ошибке
    (оно может прийти в чужой кодировке, т.е. «модзибаке», и вводить в
    заблуждение).
    """
    candidate = shutil.which("bash")
    if candidate is None:
        return None
    marker = "HRM_BASH_PROBE_OK"
    try:
        probe = subprocess.run(
            [candidate, "-c", f"echo {marker}"],
            capture_output=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if probe.returncode != 0:
        return None
    # Сравниваем байты, чтобы не зависеть от кодировки консоли.
    if marker.encode("utf-8") not in probe.stdout:
        return None
    return candidate


def _run_resolve_script(
    script: str, tmp_path: Path, version: str, notes: str
) -> subprocess.CompletedProcess:
    """Реальное исполнение resolve-скрипта bash'ем с поддельным GITHUB_OUTPUT.

    INPUT_SHA — текущий HEAD тестового репозитория (checkout внутри скрипта
    становится no-op); атакующие/валидные notes передаются через env, как
    в настоящем workflow_dispatch.
    """
    bash_path = _find_real_bash()
    if bash_path is None:
        pytest.skip(
            "исполняемый bash недоступен (например, в PATH найден лишь "
            "нерабочий WSL shim): исполняемый тест resolve-шага пропущен, "
            "а не ослаблен"
        )
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
        [bash_path, "-c", script], cwd=REPO, env=env, capture_output=True, text=True, timeout=120
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


def test_find_real_bash_rejects_stub_that_does_not_execute(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Регрессия на ошибку детекции: «bash», который присутствует в PATH, но
    не исполняет команды (как сломанный WSL shim), не должен считаться
    пригодным — одного ``shutil.which`` для этого решения недостаточно."""
    stub_dir = tmp_path / "stubbin"
    stub_dir.mkdir()
    if os.name == "nt":
        # PATHEXT включает .BAT — shutil.which("bash") находит заглушку,
        # но как процесс она команду не исполняет (симуляция отказа шима).
        (stub_dir / "bash.bat").write_bytes(b"@exit /b 1\r\n")
    else:
        stub = stub_dir / "bash"
        stub.write_bytes(b"#!/bin/sh\nexit 1\n")
        os.chmod(stub, 0o755)
    monkeypatch.setenv("PATH", str(stub_dir) + os.pathsep + os.environ.get("PATH", os.defpath))
    assert _find_real_bash() is None


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
    for step in (
        jobs["ci-gate"]["steps"] + jobs["windows-installer"]["steps"] + signing_job["steps"]
    ):
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


def test_ci_gate_resolve_dispatch_rejects_bad_sha_format(tmp_path: Path) -> None:
    """Dispatch: неверный формат release_sha отклоняется ДО записи вывода."""
    script = _ci_gate_step_run(_CI_GATE_RESOLVE_STEP)
    for bad_sha in ("main", "a" * 39, "g" * 40, "", f"{'a' * 40}\nmain"):
        result = _run_ci_gate_resolve(
            tmp_path, script, event_name="workflow_dispatch", input_sha=bad_sha
        )
        assert result.returncode != 0, f"плохой release_sha принят: {bad_sha!r}"
        assert "sha=" not in _ci_gate_output(tmp_path)


def test_ci_gate_resolve_dispatch_rejects_missing_commit(tmp_path: Path) -> None:
    """Dispatch: несуществующий commit отклоняется (git cat-file -e)."""
    script = _ci_gate_step_run(_CI_GATE_RESOLVE_STEP)
    result = _run_ci_gate_resolve(
        tmp_path, script, event_name="workflow_dispatch", input_sha="0" * 40
    )
    assert result.returncode != 0
    assert "does not exist" in result.stderr
    assert "sha=" not in _ci_gate_output(tmp_path)


def test_ci_gate_resolve_dispatch_uses_exact_requested_sha(tmp_path: Path) -> None:
    """Dispatch: в вывод попадает ровно запрошенный exact SHA (40 hex)."""
    script = _ci_gate_step_run(_CI_GATE_RESOLVE_STEP)
    head_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True, text=True, check=True
    ).stdout.strip()
    with _git_checkout_safe_restore():
        result = _run_ci_gate_resolve(
            tmp_path, script, event_name="workflow_dispatch", input_sha=head_sha
        )
    assert result.returncode == 0, result.stderr
    assert _ci_gate_output(tmp_path).strip() == f"sha={head_sha}"


def test_ci_gate_resolve_push_uses_tagged_commit_sha(tmp_path: Path) -> None:
    """Tag-push: exact SHA коммита защищённого ref, не branch name."""
    script = _ci_gate_step_run(_CI_GATE_RESOLVE_STEP)
    head_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True, text=True, check=True
    ).stdout.strip()
    result = _run_ci_gate_resolve(tmp_path, script, event_name="push", input_sha="")
    assert result.returncode == 0, result.stderr
    assert _ci_gate_output(tmp_path).strip() == f"sha={head_sha}"


def test_ci_gate_passes_for_completed_successful_ci_exact_sha(tmp_path: Path) -> None:
    """CI completed/success для exact SHA → gate проходит.

    Mock дополнительно отказывает, если скрипт не запрашивает head_sha=<SHA>
    (т.е. success здесь также подтверждает запрос именно exact SHA).
    """
    sha = "c" * 40
    script = _ci_gate_step_run(_CI_GATE_CHECK_STEP)
    result = _run_ci_gate(tmp_path, script, sha=sha, payload={"workflow_runs": [_ci_run(sha)]})
    assert result.returncode == 0, result.stderr
    assert f"CI completed successfully for exact SHA {sha}" in result.stdout
    assert "unit-test-placeholder-token" not in result.stdout + result.stderr


def test_ci_gate_rejects_missing_ci_run(tmp_path: Path) -> None:
    """CI отсутствует → fail closed (не последний успешный run другого SHA)."""
    sha = "c" * 40
    script = _ci_gate_step_run(_CI_GATE_CHECK_STEP)
    result = _run_ci_gate(tmp_path, script, sha=sha, payload={"workflow_runs": []})
    assert result.returncode != 0
    assert "no CI workflow run found" in result.stderr
    assert f"exact SHA {sha}" in result.stderr


def test_ci_gate_rejects_non_ci_workflow_run(tmp_path: Path) -> None:
    """Успех workflow с другим именем (не `CI`) gate не проходит."""
    sha = "c" * 40
    script = _ci_gate_step_run(_CI_GATE_CHECK_STEP)
    payload = {"workflow_runs": [_ci_run(sha, name="Release")]}
    result = _run_ci_gate(tmp_path, script, sha=sha, payload=payload)
    assert result.returncode != 0
    assert "no CI workflow run found" in result.stderr


@pytest.mark.parametrize("status", ["queued", "in_progress"])
def test_ci_gate_rejects_ci_in_progress(tmp_path: Path, status: str) -> None:
    """CI ещё выполняется → отказ, а не ожидание и не зачёт по exit code."""
    sha = "c" * 40
    script = _ci_gate_step_run(_CI_GATE_CHECK_STEP)
    payload = {"workflow_runs": [_ci_run(sha, status=status, conclusion=None)]}
    result = _run_ci_gate(tmp_path, script, sha=sha, payload=payload)
    assert result.returncode != 0
    assert "CI still in progress" in result.stderr


@pytest.mark.parametrize("conclusion", ["failure", "cancelled", "skipped"])
def test_ci_gate_rejects_unsuccessful_ci_conclusion(tmp_path: Path, conclusion: str) -> None:
    """CI completed с failure/cancelled/skipped → отказ.

    gh api при этом завершается успешно (exit 0): решение принимается по
    conclusion, а не только по локальному exit code.
    """
    sha = "c" * 40
    script = _ci_gate_step_run(_CI_GATE_CHECK_STEP)
    payload = {"workflow_runs": [_ci_run(sha, conclusion=conclusion)]}
    result = _run_ci_gate(tmp_path, script, sha=sha, payload=payload)
    assert result.returncode != 0
    assert "CI not successful" in result.stderr
    assert conclusion in result.stderr


def test_ci_gate_rejects_ci_run_for_different_sha(tmp_path: Path) -> None:
    """Успешный CI для другого SHA → отказ для запрошенного exact SHA."""
    sha = "c" * 40
    other_sha = "d" * 40
    script = _ci_gate_step_run(_CI_GATE_CHECK_STEP)
    payload = {"workflow_runs": [_ci_run(other_sha)]}
    result = _run_ci_gate(tmp_path, script, sha=sha, payload=payload)
    assert result.returncode != 0
    assert "no CI workflow run found" in result.stderr
    assert f"exact SHA {sha}" in result.stderr


def test_ci_gate_rejects_when_api_query_fails(tmp_path: Path) -> None:
    """Ошибка GitHub API / нехватка permissions → fail closed, без токенов в логе."""
    sha = "c" * 40
    script = _ci_gate_step_run(_CI_GATE_CHECK_STEP)
    result = _run_ci_gate(tmp_path, script, sha=sha, gh_exit=1)
    assert result.returncode != 0
    assert "fail closed" in result.stderr
    assert "403" in result.stderr
    assert "unit-test-placeholder-token" not in result.stdout + result.stderr


def test_ci_gate_rejects_non_hex_resolved_sha(tmp_path: Path) -> None:
    """Resolved SHA вне 40-hex → отказ до похода в API."""
    script = _ci_gate_step_run(_CI_GATE_CHECK_STEP)
    result = _run_ci_gate(tmp_path, script, sha="branch-name")
    assert result.returncode != 0
    assert "40 hex" in result.stderr


def test_workflow_ci_gate_orders_production_signing_after_exact_sha_ci() -> None:
    """Структурный контракт порядка: CI exact SHA — до production signing.

    * `ci-gate` не имеет `environment: update-channel-signing` и не ссылается
      на secrets.* — production signing material gate недоступен по построению;
    * `windows-installer` (production signing inputs + Authenticode) имеет
      `needs: ci-gate` — при failed/skipped gate GitHub dependent-джоб не
      стартует (environment approval и signing inputs не запрашиваются);
    * `channel-release` сохраняет зависимость от `windows-installer`;
    * production secrets syntactically доступны только в jobs, transitively
      depending on `ci-gate`.
    """
    data = _workflow_data()
    jobs = data["jobs"]
    assert _CI_GATE_JOB in jobs
    gate = jobs[_CI_GATE_JOB]
    # ci-gate — без production environment и без secrets.
    assert "environment" not in gate
    gate_blob = json.dumps(gate, ensure_ascii=False)
    assert "secrets." not in gate_blob
    assert "UPDATE_CHANNEL_" not in gate_blob
    assert "update-channel-signing" not in gate_blob
    # Gate реально проверяет CI для exact SHA: формат, существование, HEAD
    # == release_sha и запрос actions/runs по head_sha с разбором conclusion.
    gate_text = "\n".join(step.get("run", "") for step in gate["steps"] if isinstance(step, dict))
    assert "head_sha=" in gate_text
    assert 'run.get("name") == "CI"' in gate_text
    assert "release refused" in gate_text
    assert "git cat-file -e" in gate_text
    assert "checkout HEAD != release_sha" in gate_text
    # Порядок: windows-installer зависит от ci-gate; его шаг signing inputs
    # присутствует и выполняется GitHub только после успешного needs.
    installer_needs = jobs["windows-installer"].get("needs", [])
    if isinstance(installer_needs, str):
        installer_needs = [installer_needs]
    assert _CI_GATE_JOB in installer_needs
    installer_names = [step.get("name", "") for step in jobs["windows-installer"]["steps"]]
    assert "Require production Authenticode signing inputs (fail closed)" in installer_names
    # channel-release: текущий порядок сохранён (после windows-installer) и
    # прямая зависимость от ci-gate (повторный gate до публикации).
    channel_needs = jobs["channel-release"].get("needs", [])
    if isinstance(channel_needs, str):
        channel_needs = [channel_needs]
    assert "windows-installer" in channel_needs
    assert _CI_GATE_JOB in channel_needs
    # Production secrets — только в jobs, стартующих после ci-gate.
    for job_name, job in jobs.items():
        if "secrets.UPDATE_CHANNEL_" in json.dumps(job, ensure_ascii=False):
            assert _CI_GATE_JOB in _transitive_needs(job_name, jobs), (
                f"job {job_name} ссылается на production secrets без needs на ci-gate"
            )


def test_test_mode_sums_match_all_four_channel_assets(tmp_path: Path) -> None:
    import hashlib

    result = _run_publish(
        tmp_path, ["--private-key", str(TESTDATA / "test_key.priv"), "--key-id", "pilot-test-key"]
    )
    assert result.returncode == 0, result.stderr
    out = tmp_path / "dist/channel"
    lines = (out / "SHA256SUMS").read_text().splitlines()
    names = [
        "hr-manager-windows-0.14.0.zip",
        "update-channel.json",
        "release-metadata.json",
        "trust-store.json",
    ]
    assert lines == [
        f"{hashlib.sha256((out / name).read_bytes()).hexdigest()}  {name}" for name in sorted(names)
    ]


@pytest.mark.parametrize(
    "missing", ["Setup.exe", "authenticode-verification.json", "authenticode-attestation.json"]
)
def test_sums_fail_closed_for_missing_final_asset(tmp_path: Path, missing: str) -> None:
    sys.path.insert(0, str(RELEASE))
    from publish_channel import PolicyError, write_sha256sums  # type: ignore[import-not-found]

    paths = [
        tmp_path / name
        for name in ("Setup.exe", "authenticode-verification.json", "authenticode-attestation.json")
    ]
    for path in paths:
        if path.name != missing:
            path.write_bytes(b"final bytes")
    with pytest.raises(PolicyError, match="missing or unreadable"):
        write_sha256sums(paths, tmp_path / "SHA256SUMS")
    assert not (tmp_path / "SHA256SUMS").exists()


def test_sums_reject_duplicate_basenames_and_self_reference(tmp_path: Path) -> None:
    sys.path.insert(0, str(RELEASE))
    from publish_channel import PolicyError, write_sha256sums

    target = tmp_path / "SHA256SUMS"
    for paths in ([target], [tmp_path / "one/Setup.exe", tmp_path / "two/Setup.exe"]):
        with pytest.raises(PolicyError, match="duplicate/self-referencing"):
            write_sha256sums(paths, target)
        assert not target.exists()
