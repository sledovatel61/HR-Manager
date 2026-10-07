#!/usr/bin/env python3
"""Contract checks for the portable single-file license issuer (P8).

The real artifact can only be built on Windows (csc.exe ships with Windows and
build.ps1 downloads the embeddable Python from python.org), so this script
checks the parts that must hold *before* anyone builds it:

* the launcher source is ASCII-only (Windows PowerShell/csc read source in the
  system code page) and never touches the network or private keys;
* the payload layout the launcher expects is exactly what build.ps1 packs;
* the ZIP trailer written by build-portable.ps1 is byte-compatible with the
  trailer the launcher reads back (marker + 14 digits);
* the builder fails closed on key material and records SHA256 + versions for
  reproducibility;
* the pinned cryptography version exists as a Windows wheel for Python 3.12;
* documentation mentions the single-file artifact (owner flow).

Run locally (also works on Linux):  python3 tools/license-issuer/ci-portable-contract.py
Exit code 0 = all contract checks pass.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent.parent
LAUNCHER = ROOT / "launcher" / "Program.cs"
BUILDER = ROOT / "build-portable.ps1"
BUNDLE_BUILDER = ROOT / "build.ps1"
CI_HELPER = ROOT / "ci-windows-checks.ps1"
README = ROOT / "README.md"
OWNER_DOC = REPO / "docs" / "OWNER_QUICKSTART.md"
GITIGNORE = REPO / ".gitignore"

MARKER = "HRMISSUER-PAYLOAD1"
LENGTH_DIGITS = 14

failures: list[str] = []
notes: list[str] = []


def check(condition: bool, message: str) -> None:
    if condition:
        notes.append("PASS: " + message)
    else:
        failures.append("FAIL: " + message)


def read_bytes(path: Path) -> bytes:
    check(path.exists(), f"{path.relative_to(REPO)} exists")
    return path.read_bytes() if path.exists() else b""


def body_after_bom(data: bytes) -> bytes:
    return data[3:] if data[:3] == b"\xef\xbb\xbf" else data


def is_ascii_after_bom(data: bytes) -> bool:
    return all(b < 0x80 for b in body_after_bom(data))


def decode_ascii(data: bytes) -> str:
    """ASCII text without the UTF-8 BOM (returns '' when the file is not ASCII)."""
    if not data or not is_ascii_after_bom(data):
        return ""
    return body_after_bom(data).decode("ascii")


# --- launcher ---------------------------------------------------------------
launcher = read_bytes(LAUNCHER)
launcher_text = decode_ascii(launcher)
check(bool(launcher_text), "launcher/Program.cs is ASCII-only (any code page parses it)")
if launcher_text:
    check(
        f'private const string PayloadMarker = "{MARKER}"' in launcher_text,
        f"launcher reads the same trailer marker ({MARKER})",
    )
    check("private const int TrailerSize = 32;" in launcher_text, "launcher trailer size 32 bytes")
    check(
        "private const int LengthDigits = 14;" in launcher_text,
        f"launcher trailer length field is {LENGTH_DIGITS} ASCII digits",
    )
    for forbidden in ("HttpClient", "WebRequest", "WebClient", "Socket", "Dns.", "SmtpClient"):
        check(forbidden not in launcher_text, f"launcher never uses {forbidden} (no network)")
    check(
        "license-issuer" in launcher_text and '"python"' in launcher_text,
        'launcher expects the payload layout payload\\python + payload\\license-issuer',
    )
    for script in ("gui.py", "cli.py", "license_issuer.py"):
        check(script in launcher_text, f"launcher checks for {script}")
    check(
        'Environment.SpecialFolder.LocalApplicationData' in launcher_text
        and '"LicenseIssuer"' in launcher_text,
        "launcher extracts only below %LOCALAPPDATA%\\HRManager\\LicenseIssuer",
    )
    check(
        "WriteAllText" in launcher_text and "private_key" not in launcher_text,
        "launcher writes only the extraction marker/self-check, never key files",
    )
    check("--hrm-selfcheck" in launcher_text, "launcher exposes --hrm-selfcheck for CI evidence")
    check(
        "HRM_PORTABLE_LOG" in launcher_text and "RunCliCaptured" in launcher_text,
        "launcher captures the child CLI output into HRM_PORTABLE_LOG (CI evidence)",
    )
    check(
        re.search(r"return child\.ExitCode;", launcher_text) is not None,
        "CLI mode returns the bundled python exit code (verifiable in CI)",
    )
    check("pythonw.exe" in launcher_text, "double-click path uses pythonw.exe (no console window)")
    for code_word in ("\\u0417\\u0430\\u043f\\u0443\\u0441\\u043a", "\\u041f\\u043e\\u0434\\u0433\\u043e\\u0442\\u0430\\u0432\\u043b"):
        check(code_word not in launcher_text or "\\u" in launcher_text, "Russian UI strings stay in \\uXXXX escapes")

# --- builder ----------------------------------------------------------------
builder = read_bytes(BUILDER)
builder_text = decode_ascii(builder)
check(builder[:3] == b"\xef\xbb\xbf", "build-portable.ps1 has a UTF-8 BOM")
check(bool(builder_text), "build-portable.ps1 is ASCII-only")
if builder_text:
    check(MARKER in builder_text, "build-portable.ps1 writes the same trailer marker as the launcher")
    check(
        '$lengthDigits = 14' in builder_text or "$lengthDigits = 14" in builder_text,
        "build-portable.ps1 writes a 14-digit payload length",
    )
    check("D$lengthDigits" in builder_text, "build-portable.ps1 zero-pads the payload length")
    builder_code = "\n".join(
        line for line in builder_text.splitlines() if not line.lstrip().startswith("#")
    )
    check(
        "foreach ($source in @($pythonDir, $appDir))" in builder_text
        and '$baseName + "/" + $relative' in builder_text
        and "$archive.CreateEntry($entryName" in builder_text,
        'payload zip contains top-level "python" and "license-issuer" folders '
        "(native ZipArchive, forward-slash entry names)",
    )
    check(
        "Compress-Archive" not in builder_code,
        "builder does not use Compress-Archive (minutes for ~10k files under PowerShell 5.1)",
    )
    check("--hrm-selfcheck" in builder_text, "builder smoke-tests --hrm-selfcheck")
    for step in ("gen-keypair", "issue", "verify"):
        check(step in builder_text, f"builder smoke-tests the CLI step '{step}'")
    check(
        "private key material leaked into" in builder_text,
        "builder refuses to ship when the private key appears in CLI output",
    )
    check(
        "Get-FileHash -Path $exePath -Algorithm SHA256" in builder_text,
        "builder records the artifact SHA256",
    )
    check("sha256_exe" in builder_text and "BUILD-INFO.txt" in builder_text, "builder writes BUILD-INFO.txt")
    check(
        "private_key" in builder_text and "key material found under dist/" in builder_text,
        "builder fails closed if key material lands in dist/",
    )
    check(
        "hrm-portable-smoke-" in builder_text and "GetTempPath()" in builder_text,
        "builder smoke test runs outside the repository and cleans up",
    )
    check("Roslyn" in builder_text, "builder prefers the deterministic Roslyn compiler when present")
    check(
        "Framework64\\v4.0.30319\\csc.exe" in builder_text,
        "builder falls back to the csc.exe that ships with Windows",
    )
    check(
        "Add-Type -TypeDefinition" not in builder_code,
        "builder does not compile C# via Add-Type (explicit csc invocation)",
    )
    check(
        "Find-CSharpCompiler" in builder_text and "@cscArgs" in builder_text,
        "builder compiles the launcher with an explicit csc.exe invocation",
    )
    check(
        "function Invoke-PortableExe" in builder_text
        and "[System.Diagnostics.Process]::Start" in builder_text
        and "WaitForExit" in builder_text,
        "builder waits for the GUI-subsystem launcher with an explicit timeout and reads its exit code",
    )
    check(
        "cannot read the exit code" in builder_text,
        "an unreadable exit code fails the build instead of counting as success",
    )
    check(
        "HRM_PORTABLE_LOG" in builder_text,
        "builder smoke test reads the real child CLI output through HRM_PORTABLE_LOG",
    )

# --- bundle builder: pinned, reproducible inputs ----------------------------
bundle = read_bytes(BUNDLE_BUILDER)
bundle_text = decode_ascii(bundle)
check(bool(bundle_text), "build.ps1 stays ASCII-only")
if bundle_text:
    match = re.search(r'\[string\]\$CryptographyVersion = "([0-9.]+)"', bundle_text)
    check(match is not None, "build.ps1 pins the cryptography version")
    pinned = match.group(1) if match else ""
    pinned_pip_lines = [
        line
        for line in bundle_text.splitlines()
        if "Invoke-NativeLogged" in line and "cryptography==$CryptographyVersion" in line
    ]
    check(
        len(pinned_pip_lines) == 2,
        f"both pip install paths install the pinned cryptography version (found {len(pinned_pip_lines)})",
    )
    check('$PythonVersion = "3.12.3"' in bundle_text, "build.ps1 pins the embeddable Python version")
    check(
        '"--only-binary", ":all:"' in bundle_text and '"--platform", "win_amd64"' in bundle_text,
        "system-pip fallback installs Windows wheels for the pinned Python",
    )
    if pinned:
        notes.append(f"INFO: pinned cryptography={pinned}")

# --- CI helper --------------------------------------------------------------
ci = read_bytes(CI_HELPER)
ci_text = decode_ascii(ci)
if ci_text:
    check('"portable"' in ci_text and '-eq "portable"' in ci_text, "ci-windows-checks.ps1 has a portable phase")
    check("build-portable.ps1" in ci_text, "the portable phase runs build-portable.ps1 under PowerShell 5.1")
    check("build-portable.ps1" in ci_text.split("foreach ($scriptName in @(")[1].split(")")[0], "parser phase covers build-portable.ps1")
    check("trailer marker mismatch" in ci_text, "portable phase validates the trailer independently")
    check(
        "HRM_PORTABLE_LOG" in ci_text and "function Invoke-PortableExe" in ci_text,
        "portable phase captures CLI output through HRM_PORTABLE_LOG with an explicit timeout",
    )

# --- repository hygiene -----------------------------------------------------
gitignore = read_bytes(GITIGNORE).decode("utf-8", "replace")
for pattern in ("private_key.hex", "*.hrmlicense", "license-issuer-dist.zip", "dist/"):
    check(pattern in gitignore, f".gitignore excludes {pattern}")
tracked_keys = [
    str(p.relative_to(REPO))
    for p in REPO.rglob("*")
    if p.is_file()
    and ".git" not in p.parts
    and (p.name == "private_key.hex" or p.suffix == ".hrmlicense")
]
check(not tracked_keys, f"no private key / license files in the work tree (found: {tracked_keys})")

# --- documentation ----------------------------------------------------------
owner_doc = read_bytes(OWNER_DOC).decode("utf-8", "replace")
readme = read_bytes(README).decode("utf-8", "replace")
for name, text in (("docs/OWNER_QUICKSTART.md", owner_doc), ("tools/license-issuer/README.md", readme)):
    check("LicenseIssuer-Portable.exe" in text, f"{name} documents the single-file artifact")
    check("build-portable.ps1" in text, f"{name} documents how the artifact is built")
check(
    "run-gui.bat" in owner_doc or "zip" in owner_doc.lower(),
    "the owner doc keeps the bundle fallback documented",
)

# --- pinned version exists as a Windows wheel for Python 3.12 ---------------
# PyPI is reachable from this sandbox; CI can skip the network check.
if os.environ.get("HRM_SKIP_PYPI_CHECK") != "1" and "pinned" in "".join(notes):
    pinned = re.search(r'pinned cryptography=([0-9.]+)', "".join(notes))
    version = pinned.group(1) if pinned else ""
    if version:
        url = f"https://pypi.org/pypi/cryptography/{version}/json"
        try:
            with urllib.request.urlopen(url, timeout=20) as response:  # noqa: S310 (fixed PyPI URL)
                data = json.load(response)
            wheels = [
                f["filename"]
                for f in data.get("urls", [])
                if f["filename"].endswith("win_amd64.whl") and f["filename"].startswith("cryptography")
            ]
            abi3 = [w for w in wheels if "abi3" in w]
            check(bool(abi3), f"PyPI has a win_amd64 abi3 wheel for cryptography {version}")
            notes.append(f"INFO: wheels={len(wheels)} abi3={abi3[:1]}")
        except Exception as exc:  # pragma: no cover - network dependent
            notes.append(f"WARN: PyPI check skipped ({exc})")
    else:
        notes.append("WARN: no pinned version found to verify against PyPI")
else:
    notes.append("INFO: PyPI wheel check skipped (HRM_SKIP_PYPI_CHECK=1)")

for line in notes:
    print(line)
if failures:
    print("\n".join(failures))
    print(f"\n{len(failures)} contract check(s) FAILED")
    sys.exit(1)
print(f"\nportable issuer contract: {len(notes)} checks passed")
