"""Static and unit checks for the phase 12 Windows installer engine.

The PowerShell engine itself runs on Windows CI (see hr-manager.Tests.ps1 and
the ``windows-installer`` job). These Python tests run everywhere and verify,
deterministically and without pwsh/Docker:

* the engine exposes the required action set and the required safety
  properties (no git pull, no alembic downgrade, secret-free command lines,
  mockable command runner, non-interactive/test modes);
* the shared redaction patterns (infra/windows/redaction-patterns.json) are
  valid and actually scrub secrets and PII;
* the release manifest schema and the Inno Setup script are well-formed and
  reference the engine/manifest with a pinned toolchain;
* the build script hashes payload files and writes/verifies the manifest.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
WINDOWS_DIR = REPO_ROOT / "infra" / "windows"

ENGINE = WINDOWS_DIR / "hr-manager.ps1"
TESTS = WINDOWS_DIR / "hr-manager.Tests.ps1"
ISS = WINDOWS_DIR / "hr-manager.iss"
REDACTION = WINDOWS_DIR / "redaction-patterns.json"
REDACTION_SCHEMA = WINDOWS_DIR / "redaction-patterns.schema.json"
MANIFEST_SCHEMA = WINDOWS_DIR / "release-manifest.schema.json"
BUILD_SCRIPT = WINDOWS_DIR / "scripts" / "build-installer.ps1"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_engine_exposes_the_required_actions() -> None:
    src = _read(ENGINE)
    validate_set = re.search(r"ValidateSet\((.*?)\)", src, re.S)
    assert validate_set is not None, "ValidateSet action list not found"
    actions = re.findall(r"'([a-z-]+)'", validate_set.group(1))
    required_actions = (
        "install",
        "start",
        "stop",
        "status",
        "update",
        "diagnostics",
        "uninstall",
        "check",
    )
    for required in required_actions:
        assert required in actions, f"missing action '{required}'"


def test_engine_never_pulls_git_or_downgrades_alembic() -> None:
    src = _read(ENGINE)
    assert "git pull" not in src.lower()
    assert "alembic downgrade" not in src.lower()
    assert "alembic down" not in src.lower()
    # Only the one-shot forward migration is allowed.
    assert "alembic', 'upgrade', 'head'" in src


def test_engine_uses_a_cryptographic_rng_for_secrets() -> None:
    src = _read(ENGINE)
    assert "RandomNumberGenerator" in src
    assert "Math.Random" not in src
    assert "New-Guid" not in src


def test_surname_and_role_are_not_passed_on_the_command_line() -> None:
    src = _read(ENGINE)
    param_block = re.search(r"param\(.*?\n\)", src, re.S)
    assert param_block is not None
    # Фамилия/роль не являются параметрами процесса.
    assert "Surname" not in param_block.group(0)
    assert "WorkingMode" not in param_block.group(0)
    # Они читаются из JSON input file.
    assert "InputFile" in src
    assert "input.surname" in src
    assert "input.working_mode" in src


def test_engine_supports_noninteractive_and_test_modes() -> None:
    src = _read(ENGINE)
    assert "[switch]$NonInteractive" in src
    assert "[switch]$TestMode" in src


def test_engine_is_dot_source_safe_and_has_a_mockable_runner() -> None:
    src = _read(ENGINE)
    # Dot-source guard: Main runs only on direct invocation, so Pester can
    # call individual functions without executing the whole engine.
    assert "$MyInvocation.InvocationName -ne '.'" in src
    assert "$script:CmdRunner" in src


def test_engine_reports_errors_without_early_exit_in_functions() -> None:
    src = _read(ENGINE)
    # Действия возвращают управление и выставляют $script:ExitCode; `exit`
    # присутствует только в guard прямого запуска (и в build-скрипте).
    assert "exit $script:ExitCode" in src
    actions_slice = src[src.index("function Invoke-Install") : src.index("function Main")]
    assert "exit " not in actions_slice


def test_engine_diagnostics_redact_output() -> None:
    src = _read(ENGINE)
    assert "function Invoke-Diagnostics" in src
    assert "Redact-Text" in src
    # Секреты никогда не пишутся в stdout диагностики напрямую.
    assert "Write-Output $env" not in src


def test_redaction_patterns_are_valid_json_and_schema() -> None:
    spec = json.loads(_read(REDACTION))
    schema = json.loads(_read(REDACTION_SCHEMA))
    assert schema["required"] == ["patterns", "replacements"]
    assert spec["patterns"], "no redaction patterns"
    assert spec["replacements"]["default"] == "<REDACTED>"


def test_redaction_patterns_scrub_secrets_and_pii() -> None:
    spec = json.loads(_read(REDACTION))
    patterns = [p["pattern"] for p in spec["patterns"]]
    sample = "\n".join(
        [
            "POSTGRES_PASSWORD=supersecret123",
            "SECRET_KEY=deadbeefdeadbeefdeadbeefdeadbeef",
            "BACKUP_ENC_KEY=YWJjZGVmZ2hpamtsbW5vcA==",
            "BACKUP_KEY_ID=pilot-abcdef",
            "FIRST_RUN_TOKEN=abcdef1234567890_-",
            "DATABASE_URL=postgresql+psycopg://user:pass@db:5432/hr_manager",
            "contact: user@example.com",
            "тел: +7 912 345-67-89",
            "Ответственный: Иван Петров",
        ]
    )
    scrubbed = sample
    for pattern in patterns:
        scrubbed = re.sub(pattern, "<REDACTED>", scrubbed, flags=re.IGNORECASE)
    for secret in (
        "supersecret123",
        "deadbeefdeadbeefdeadbeefdeadbeef",
        "YWJjZGVmZ2hpamtsbW5vcA==",
        "abcdef1234567890_-",
        "user@example.com",
        "+7 912 345-67-89",
        "Иван Петров",
    ):
        assert secret not in scrubbed, f"secret survived redaction: {secret!r}"
    assert "<REDACTED>" in scrubbed


def test_release_manifest_schema_is_well_formed() -> None:
    schema = json.loads(_read(MANIFEST_SCHEMA))
    assert schema["type"] == "object"
    assert "release_sha" in schema["properties"]
    assert schema["properties"]["release_sha"]["pattern"] == r"^[0-9a-fA-F]{40}$"
    assert "files" in schema["required"]


def test_build_script_hashes_payload_and_supports_verification() -> None:
    src = _read(BUILD_SCRIPT)
    assert "SHA256" in src
    assert "release-manifest.json" in src
    assert "[switch]$ManifestOnly" in src
    assert "[string]$Verify" in src
    assert "iscc" in src.lower()
    # Честный статус подписи.
    assert "signed" in src
    assert "false" in src


def test_inno_script_references_the_engine_and_pins_toolchain() -> None:
    src = _read(ISS)
    assert "hr-manager.ps1" in src
    assert "GetInputFilePath" in src
    assert "/DIAGNOSTICS" in src
    assert "Inno Setup 6" in src
    # Uninstall по умолчанию НЕ удаляет данные (нет `down -v`).
    assert "down -v" not in src
    # Роль и фамилия передаются через input file, не через командную строку.
    assert "working_mode" in src
    assert "install-input.json" in src


def test_engine_braces_and_brackets_are_balanced() -> None:
    src = _read(ENGINE)
    assert src.count("{") == src.count("}")
    assert src.count("(") == src.count(")")
    assert src.count("[") == src.count("]")


def test_pester_suite_never_touches_a_real_installation() -> None:
    src = _read(TESTS)
    assert "-TestMode" in src
    assert "$script:CmdRunner" in src
    assert "hr-manager-pester-state" in src
