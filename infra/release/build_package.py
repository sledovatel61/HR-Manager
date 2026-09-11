# -*- coding: utf-8 -*-
"""Детерминированная сборка релизного zip-пакета Windows-пилота.

Пакет = снимок приложения (backend/, frontend/, infra/, release.json),
который движок кладёт в staging и передаёт существующему
``-Action update -ReleaseDir`` (см. infra/windows/engine/Channel.psm1).

Детерминированность: отсортированные записи, фиксированные времена записей
(1980-01-01, эпоха ZIP), одинаковые флаги сжатия — одинаковый вход даёт
одинаковые байты (проверка воспроизводимости по SHA256 пакета).

    python infra/release/build_package.py --snapshot installer/staging/app \
        --out dist/hr-manager-windows-0.14.0.zip --version 0.14.0 \
        --release-sha <40-hex>
"""

from __future__ import annotations

import argparse
import json
import sys
import zipfile
from pathlib import Path

ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)
EXPECTED_TOP_LEVEL = {"backend", "frontend", "infra", "release.json"}


def build_package(snapshot: Path, out: Path, version: str, release_sha: str) -> None:
    snapshot = snapshot.resolve()
    if not (snapshot / "release.json").is_file():
        raise SystemExit(f"ОШИБКА: в снимке нет release.json: {snapshot}")
    release_data = json.loads((snapshot / "release.json").read_text(encoding="utf-8"))
    if release_data.get("version") != version:
        raise SystemExit(
            f"ОШИБКА: release.json version={release_data.get('version')!r} != {version!r}"
        )
    if release_data.get("release_sha") != release_sha:
        raise SystemExit(
            f"ОШИБКА: release.json release_sha={release_data.get('release_sha')!r} "
            f"!= {release_sha!r}"
        )
    files: list[Path] = []
    for path in sorted(snapshot.rglob("*")):
        if path.is_file() and not path.is_symlink():
            files.append(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in files:
            relative = path.relative_to(snapshot).as_posix()
            info = zipfile.ZipInfo(relative, date_time=ZIP_EPOCH)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            with path.open("rb") as fh:
                zf.writestr(info, fh.read())
    print(f"пакет собран: {out} ({out.stat().st_size} байт, {len(files)} файлов)")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--release-sha", required=True)
    args = parser.parse_args()
    build_package(
        Path(args.snapshot), Path(args.out), args.version, args.release_sha
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
