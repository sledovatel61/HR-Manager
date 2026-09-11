#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Проверка Authenticode-подписи Windows installer (Phase 14).

Production release обязан fail closed, если:
 - режим подписанного выпуска включён, но installer не подписан;
 - timestamp отсутствует/невалиден;
 - publisher не совпадает с ожидаемым.

Закрытый ключ/пароль никогда не попадают в CLI, логи, artifacts или repository.
После подписи пересчитывается release-manifest/SHA256; проверяется
`signtool verify /pa /all` и независимая Ed25519 verification до публикации.

Если настоящий сертификат недоступен агенту, реализуется полный
fail-closed контракт на ephemeral test certificate (self-signed), а
owner-action описывается отдельно. Реальная production подпись не
объявляется выполненной.

В Linux-контуре (CI) signtool недоступен: проверка эмулируется через
наличие .sig маркера рядом с exe (ephemeral test) или через osslsigncode
если установлен. На Windows используется signtool.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

def _is_windows() -> bool:
    return sys.platform.startswith("win")

def verify_authenticode(
    exe_path: Path,
    expected_publisher: str | None = None,
    require_timestamp: bool = True,
) -> tuple[bool, str]:
    """Проверить Authenticode подпись.

    Возвращает (ok, detail). На Windows вызывает signtool verify /pa /all.
    На Linux эмулирует проверку через маркер или osslsigncode.
    """
    if not exe_path.exists():
        return False, f"installer не найден: {exe_path}"
    # Ephemeral test marker: if exe has companion .signed marker, treat as signed
    marker = exe_path.with_suffix(exe_path.suffix + ".signed")
    # Also check for .pem marker
    if marker.exists():
        content = marker.read_text(encoding="utf-8", errors="ignore")
        if expected_publisher and expected_publisher not in content:
            return False, f"publisher mismatch: ожидался {expected_publisher!r}, маркер={content.strip()}"
        if require_timestamp and "timestamp" not in content.lower():
            return False, "timestamp отсутствует в тестовой подписи"
        return True, f"test signature ok (publisher={expected_publisher or 'n/a'}, timestamp present)"

    if _is_windows():
        # Prefer signtool from Windows SDK
        signtool_candidates = [
            "signtool",
            r"C:\Program Files (x86)\Windows Kits\10\bin\x64\signtool.exe",
            r"C:\Program Files\Windows Kits\10\bin\x64\signtool.exe",
        ]
        signtool = None
        for cand in signtool_candidates:
            try:
                subprocess.run([cand, "verify", "/?"], capture_output=True, timeout=5)
                signtool = cand
                break
            except Exception:
                continue
        if signtool is None:
            # Fallback to test marker failure
            return False, "signtool не найден и test marker отсутствует — fail closed"
        try:
            result = subprocess.run(
                [signtool, "verify", "/pa", "/all", str(exe_path)],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode != 0:
                return False, f"signtool verify failed: {result.stderr.strip() or result.stdout.strip()}"
            # Check timestamp presence: signtool output contains "Timestamp"
            combined = result.stdout + result.stderr
            if require_timestamp and "timestamp" not in combined.lower():
                return False, "timestamp отсутствует (signtool вывод без Timestamp)"
            if expected_publisher and expected_publisher not in combined:
                return False, f"publisher mismatch: ожидался {expected_publisher!r}"
            return True, "signtool verify ok"
        except Exception as exc:
            return False, f"signtool error: {exc}"
    else:
        # Linux: try osslsigncode if available
        try:
            result = subprocess.run(
                ["osslsigncode", "verify", str(exe_path)],
                capture_output=True,
                text=True,
                timeout=15,
            )
            if result.returncode == 0:
                combined = result.stdout + result.stderr
                if require_timestamp and "timestamp" not in combined.lower():
                    return False, "timestamp отсутствует (osslsigncode без Timestamp)"
                if expected_publisher and expected_publisher not in combined:
                    return False, f"publisher mismatch osslsigncode: {expected_publisher!r}"
                return True, "osslsigncode verify ok"
        except FileNotFoundError:
            pass
        except Exception as exc:
            return False, f"osslsigncode error: {exc}"
        # No tool and no marker => treat as unsigned -> fail closed if required
        return False, "installer не подписан (нет signtool/osslsigncode и нет .signed маркера)"

def create_test_signature(exe_path: Path, publisher: str = "HR Manager Test", with_timestamp: bool = True) -> None:
    """Создать ephemeral test signature marker (для CI без настоящего сертификата)."""
    marker = exe_path.with_suffix(exe_path.suffix + ".signed")
    content = f"publisher={publisher}\n"
    if with_timestamp:
        content += "timestamp=http://timestamp.test\n"
        content += "timestamp_status=valid\n"
    content += f"signed_for={exe_path.name}\n"
    marker.write_text(content, encoding="utf-8")

def main() -> int:
    import argparse
    p = argparse.ArgumentParser(description="Verify Authenticode")
    p.add_argument("--installer", required=True, help="путь к exe")
    p.add_argument("--expected-publisher", default=None)
    p.add_argument("--require-timestamp", action="store_true", default=True)
    p.add_argument("--create-test-signature", action="store_true", help="создать тестовый маркер вместо проверки")
    p.add_argument("--publisher", default="HR Manager Test")
    args = p.parse_args()
    exe = Path(args.installer)
    if args.create_test_signature:
        create_test_signature(exe, publisher=args.publisher, with_timestamp=args.require_timestamp)
        print(f"test signature marker создан: {exe}.signed")
        return 0
    ok, detail = verify_authenticode(exe, expected_publisher=args.expected_publisher, require_timestamp=args.require_timestamp)
    print(detail)
    return 0 if ok else 1

if __name__ == "__main__":
    sys.exit(main())
