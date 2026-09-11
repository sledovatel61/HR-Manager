"""Зеркальность контракта канала: backend-копия обязана совпадать с
источником infra/release/channel_contract.py (за вычетом заголовка-зеркала).
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE = REPO_ROOT / "infra" / "release" / "channel_contract.py"
MIRROR = REPO_ROOT / "backend" / "app" / "update_channel_contract.py"


def test_mirror_matches_source_exactly() -> None:
    source = SOURCE.read_text(encoding="utf-8")
    mirror = MIRROR.read_text(encoding="utf-8")
    # Зеркало = свой заголовок + тело источника без собственного заголовка
    # (coding-комментарий и docstring источника заменяются).
    source_start = source.index('"""')
    source_body = source[source.index('"""', source_start + 3) + 3 :].lstrip("\n")
    mirror_body = mirror[mirror.index("from __future__") :]
    assert mirror_body == source_body, "backend-копия контракта рассинхронизирована с infra/release"


def test_mirror_header_present() -> None:
    mirror = MIRROR.read_text(encoding="utf-8")
    assert mirror.startswith("# -*- coding: utf-8 -*-")
    assert "ЗЕРКАЛО infra/release/channel_contract.py" in mirror


def test_source_imports_and_validates() -> None:
    import sys

    sys.path.insert(0, str(SOURCE.parent))
    from channel_contract import SCHEMA_VERSION, canonical_bytes  # type: ignore

    assert SCHEMA_VERSION == 1
    assert callable(canonical_bytes)
