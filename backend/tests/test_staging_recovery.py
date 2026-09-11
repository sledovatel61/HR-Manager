"""Тесты восстановления повреждённого staging-артефакта (Phase 13 rework #5).

Существующий target переиспользуется только если он валиден (размер +
SHA256 против manifest); повреждённый/частичный атомарно заменяется
новым проверенным файлом; ошибки не оставляют мусора.
"""

from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path

import pytest

from app.channel import ChannelError, download_package
from app.config import Settings
from app.update_channel_contract import parse_manifest_json

TESTDATA = Path(__file__).resolve().parents[2] / "infra" / "release" / "testdata"
PACKAGE_BYTES = (TESTDATA / "package.valid.zip").read_bytes()


def _settings(tmp_path: Path) -> Settings:
    return Settings.model_validate(
        {
            "APP_ENV": "test",
            "APP_DEBUG": "false",
            "SECRET_KEY": "unit-test-secret-key",
            "DATABASE_URL": "sqlite+pysqlite://",
            "UPDATE_CHANNEL_URL": "https://updates.example.com/hrm/manifest.json",
            "UPDATE_CHANNEL_PUBLIC_KEYS": "{}",
            "UPDATE_ENGINE_TOKEN": "engine-token-0123456789abcdef",
            "UPDATE_INSTALLED_VERSION": "0.13.0",
            "UPDATE_INSTALLED_SHA": "3" * 40,
            "UPDATE_CHANNEL_ALLOWED_HOSTS": "",
            "UPDATE_STAGING_DIR": str(tmp_path / "staging"),
        }
    )


@pytest.fixture()
def manifest(tmp_path: Path) -> tuple[Settings, dict]:
    raw = parse_manifest_json((TESTDATA / "manifest.valid.json").read_text(encoding="utf-8"))
    raw["package_size"] = len(PACKAGE_BYTES)
    raw["package_sha256"] = hashlib.sha256(PACKAGE_BYTES).hexdigest()
    return _settings(tmp_path), raw


def _target(settings: Settings, manifest: dict) -> Path:
    root = Path(settings.update_staging_dir)
    return root / f"release-{manifest['release_sha'][:12]}.zip"


def test_valid_staging_target_is_reused_without_download(
    manifest: tuple[Settings, dict], monkeypatch: pytest.MonkeyPatch
) -> None:
    settings, data = manifest
    target = _target(settings, data)
    target.parent.mkdir(parents=True)
    target.write_bytes(PACKAGE_BYTES)

    def boom(*args: object, **kwargs: object) -> tuple[Path, int]:
        raise AssertionError("сеть не должна вызываться при валидном staging")

    monkeypatch.setattr("app.channel._download_to_temp", boom)
    result = download_package(settings, data)
    assert result == target
    assert result.read_bytes() == PACKAGE_BYTES


def test_corrupted_staging_target_is_replaced(
    manifest: tuple[Settings, dict], monkeypatch: pytest.MonkeyPatch
) -> None:
    settings, data = manifest
    target = _target(settings, data)
    target.parent.mkdir(parents=True)
    target.write_bytes(b"corrupted partial zip" * 10)

    def fake_download(
        manifest_dict: dict, max_bytes: int, *args: object, **kwargs: object
    ) -> tuple[Path, int]:
        assert manifest_dict["package_sha256"] == data["package_sha256"]
        fd, name = tempfile.mkstemp(prefix="hrm-test-", suffix=".zip.part")
        temp_path = Path(name)
        import os

        os.close(fd)
        temp_path.write_bytes(PACKAGE_BYTES)
        return temp_path, len(PACKAGE_BYTES)

    monkeypatch.setattr("app.channel._download_to_temp", fake_download)
    result = download_package(settings, data)
    assert result == target
    assert result.read_bytes() == PACKAGE_BYTES
    assert result.stat().st_size == len(PACKAGE_BYTES)


def test_wrong_size_staging_target_is_replaced(
    manifest: tuple[Settings, dict], monkeypatch: pytest.MonkeyPatch
) -> None:
    settings, data = manifest
    target = _target(settings, data)
    target.parent.mkdir(parents=True)
    target.write_bytes(b"x" * (len(PACKAGE_BYTES) + 1))

    def fake_download(
        manifest_dict: dict, max_bytes: int, *args: object, **kwargs: object
    ) -> tuple[Path, int]:
        fd, name = tempfile.mkstemp(prefix="hrm-test-", suffix=".zip.part")
        temp_path = Path(name)
        import os

        os.close(fd)
        temp_path.write_bytes(PACKAGE_BYTES)
        return temp_path, len(PACKAGE_BYTES)

    monkeypatch.setattr("app.channel._download_to_temp", fake_download)
    result = download_package(settings, data)
    assert result.read_bytes() == PACKAGE_BYTES


def test_hash_mismatch_cleans_temp_and_leaves_no_release(
    manifest: tuple[Settings, dict], monkeypatch: pytest.MonkeyPatch
) -> None:
    settings, data = manifest
    target = _target(settings, data)

    def fake_download(
        manifest_dict: dict, max_bytes: int, *args: object, **kwargs: object
    ) -> tuple[Path, int]:
        fd, name = tempfile.mkstemp(prefix="hrm-test-", suffix=".zip.part")
        temp_path = Path(name)
        import os

        os.close(fd)
        temp_path.write_bytes(b"bad content of the right size" * 20)
        return temp_path, 20 * len(b"bad content of the right size")

    monkeypatch.setattr("app.channel._download_to_temp", fake_download)
    with pytest.raises(ChannelError) as excinfo:
        download_package(settings, data)
    assert excinfo.value.code == "package_hash_mismatch"
    assert not target.exists()  # релиз не создан
    leftovers = list(target.parent.glob("*.part"))
    assert leftovers == []  # временный файл удалён


def test_download_error_leaves_previous_release_untouched(
    manifest: tuple[Settings, dict], monkeypatch: pytest.MonkeyPatch
) -> None:
    settings, data = manifest
    target = _target(settings, data)
    target.parent.mkdir(parents=True)
    target.write_bytes(b"previous garbage")

    def offline(
        manifest_dict: dict, max_bytes: int, *args: object, **kwargs: object
    ) -> tuple[Path, int]:
        raise ChannelError("channel_offline", "канал недоступен")

    monkeypatch.setattr("app.channel._download_to_temp", offline)
    with pytest.raises(ChannelError) as excinfo:
        download_package(settings, data)
    assert excinfo.value.code == "channel_offline"
    # Предыдущий (повреждённый) файл не тронут, никаких .part не осталось.
    assert target.read_bytes() == b"previous garbage"
    assert list(target.parent.glob("*.part")) == []
