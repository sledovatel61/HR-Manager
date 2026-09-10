"""Серверная часть канала обновлений: доверенные ключи, получение manifest
и безопасное скачивание пакета в staging.

Вся политика канала задаётся ТОЛЬКО серверной конфигурацией (Settings):
клиент никогда не передаёт URL, пути, команды, manifest payload или
release SHA. Fail closed на каждом шаге: до распаковки и до изменения
установки ничего не принимается.
"""

from __future__ import annotations

import hashlib
import json
import ssl
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from app.config import Settings
from app.update_channel_contract import ChannelError, parse_manifest_json, verify_signature

# Лимиты канала (защита от аномальных/злонамеренных ответов).
MANIFEST_MAX_BYTES = 64 * 1024
DOWNLOAD_TIMEOUT_SECONDS = 120
DOWNLOAD_MAX_REDIRECTS = 5
# Разумный потолок пакета (конфигурируемый предел проверяется отдельно).
DOWNLOAD_DEFAULT_MAX_BYTES = 512 * 1024 * 1024

# Хосты, разрешённые политикой канала (явный список; конечный redirect
# обязан остаться HTTPS и принадлежать одному из них).
DEFAULT_ALLOWED_HOSTS = ("github.com", "objects.githubusercontent.com", "updates.example.com")


def _split_identifiers(text: str) -> list[str] | None:
    if not text:
        return []
    parts = text.split(".")
    result = []
    for part in parts:
        if part == "":
            return None
        result.append(part)
    return result


def _compare_prerelease(left: list[str], right: list[str]) -> int:
    # SemVer 2.0: prerelease отсутствует > prerelease есть; числовые
    # идентификаторы сравниваются численно и меньше буквенных; поэлементно.
    if not left and not right:
        return 0
    if not left:
        return 1
    if not right:
        return -1
    for a, b in zip(left, right, strict=True):
        if a == b:
            continue
        a_num = a.isdigit() and not (len(a) > 1 and a.startswith("0"))
        b_num = b.isdigit() and not (len(b) > 1 and b.startswith("0"))
        if a_num and b_num:
            return -1 if int(a) < int(b) else 1
        if a_num != b_num:
            return -1 if a_num else 1
        return -1 if a < b else 1
    if len(left) == len(right):
        return 0
    return -1 if len(left) < len(right) else 1


def compare_semver(left: str, right: str) -> int:
    """Строгое SemVer-сравнение: -1 (left<right), 0, 1 (left>right).
    Build metadata игнорируется; некорректные версии — ChannelError.
    """
    from app.update_channel_contract import SEMVER_RE, ChannelError

    for value in (left, right):
        if not isinstance(value, str) or not SEMVER_RE.match(value):
            raise ChannelError("bad_semver", f"некорректный SemVer: {value!r}")
    l_core, _, _build = left.partition("+")
    r_core, _, _build = right.partition("+")
    l_main, _, l_pre = l_core.partition("-")
    r_main, _, r_pre = r_core.partition("-")
    l_parts = [int(part) for part in l_main.split(".")]
    r_parts = [int(part) for part in r_main.split(".")]
    if l_parts != r_parts:
        return -1 if l_parts < r_parts else 1
    l_pre_ids = _split_identifiers(l_pre)
    r_pre_ids = _split_identifiers(r_pre)
    if l_pre_ids is None or r_pre_ids is None:
        raise ChannelError("bad_semver", "некорректный prerelease SemVer")
    return _compare_prerelease(l_pre_ids, r_pre_ids)


def channel_enabled(settings: Settings) -> bool:
    return bool(settings.update_channel_url and settings.update_channel_public_keys.strip())


def manifest_url(settings: Settings, preview: bool = False) -> str:
    if preview and settings.update_preview_channel_url:
        return settings.update_preview_channel_url
    return settings.update_channel_url


def parse_trusted_keys(settings: Settings) -> dict[str, dict]:
    """Разбор набора доверенных ключей {key_id: {key, revoked}}."""
    raw = settings.update_channel_public_keys.strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ChannelError("bad_key_set", f"некорректный JSON набора ключей: {exc}") from exc
    if not isinstance(data, dict) or not data:
        raise ChannelError("bad_key_set", "набор доверенных ключей пуст или не объект")
    for key_id, entry in data.items():
        if not isinstance(key_id, str) or not key_id:
            raise ChannelError("bad_key_set", "key_id должен быть непустой строкой")
        if not isinstance(entry, dict):
            raise ChannelError("bad_key_set", f"запись ключа {key_id!r} не объект")
        if not isinstance(entry.get("key"), str) or not entry["key"]:
            raise ChannelError("bad_key_set", f"у ключа {key_id!r} нет значения key")
        if not isinstance(entry.get("revoked"), bool):
            raise ChannelError("bad_key_set", f"у ключа {key_id!r} нет флага revoked")
    return data


def _lookup_trusted_key(manifest: dict, trusted: dict[str, dict]) -> str:
    signature = manifest.get("signature")
    if not isinstance(signature, dict) or not isinstance(signature.get("key_id"), str):
        raise ChannelError("bad_signature", "отсутствует signature.key_id")
    key_id = signature["key_id"]
    entry = trusted.get(key_id)
    if entry is None:
        raise ChannelError("unknown_key", f"ключ {key_id!r} не входит в доверенный набор")
    if entry["revoked"]:
        raise ChannelError("revoked_key", f"ключ {key_id!r} отозван")
    return entry["key"]


def fetch_manifest_text(settings: Settings, preview: bool = False) -> str:
    """Скачивание manifest: HTTPS, лимит redirect'ов, проверка host/scheme.

    Сетевые ошибки транслируются в ChannelError("channel_offline", ...) —
    вызывающий код отличает offline от других отказов.
    """
    url = manifest_url(settings, preview)
    if not url:
        raise ChannelError("not_configured", "канал обновлений не настроен")
    if not url.startswith("https://"):
        raise ChannelError("bad_url", "URL канала обязан использовать https")
    allowed = list(DEFAULT_ALLOWED_HOSTS)
    context = ssl.create_default_context()
    current = url
    for _ in range(DOWNLOAD_MAX_REDIRECTS + 1):
        parsed = urllib.parse.urlsplit(current)
        if parsed.scheme != "https":
            raise ChannelError("bad_url", f"канал перешёл на незащищённую схему: {parsed.scheme}")
        if parsed.hostname not in allowed:
            raise ChannelError("bad_url", f"хост канала {parsed.hostname!r} не входит в политику")
        request = urllib.request.Request(
            current, headers={"User-Agent": "hr-manager-pilot-update/1.0"}
        )
        try:
            with urllib.request.urlopen(request, timeout=30, context=context) as response:
                final = response.geturl()
                if final != current:
                    current = final
                    continue
                data = response.read(MANIFEST_MAX_BYTES + 1)
                if len(data) > MANIFEST_MAX_BYTES:
                    raise ChannelError("manifest_invalid", "manifest превышает лимит размера")
                return data.decode("utf-8")
        except ChannelError:
            raise
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ChannelError(
                "channel_offline", f"канал недоступен: {exc.__class__.__name__}"
            ) from exc
    raise ChannelError("channel_offline", "превышен лимит redirect'ов канала")


def verified_manifest(settings: Settings, preview: bool = False) -> dict:
    """Полный цикл: скачивание → валидация → проверка подписи доверенным ключом."""
    text = fetch_manifest_text(settings, preview)
    manifest = parse_manifest_json(text)
    from app.update_channel_contract import validate_manifest_fields

    validate_manifest_fields(manifest)
    trusted = parse_trusted_keys(settings)
    public_key = _lookup_trusted_key(manifest, trusted)
    verify_signature(manifest, public_key)
    return manifest


def staging_root(settings: Settings) -> Path:
    """Корень staging (не внутри пользовательских данных и не в backup volume)."""
    if settings.update_staging_dir:
        return Path(settings.update_staging_dir)
    return Path(tempfile.gettempdir()) / "hrm-update-staging"


def _download_to_temp(manifest: dict, max_bytes: int) -> tuple[Path, int]:
    url = manifest["package_url"]
    context = ssl.create_default_context()
    request = urllib.request.Request(url, headers={"User-Agent": "hr-manager-pilot-update/1.0"})
    allowed = list(DEFAULT_ALLOWED_HOSTS)
    current = url
    temp_path: Path | None = None
    try:
        for _ in range(DOWNLOAD_MAX_REDIRECTS + 1):
            parsed = urllib.parse.urlsplit(current)
            if parsed.scheme != "https" or parsed.hostname not in allowed:
                raise ChannelError("bad_url", "пакет скачивается не по политике канала")
            try:
                with urllib.request.urlopen(
                    request, timeout=DOWNLOAD_TIMEOUT_SECONDS, context=context
                ) as response:
                    final = response.geturl()
                    if final != current:
                        current = final
                        request = urllib.request.Request(
                            current, headers={"User-Agent": "hr-manager-pilot-update/1.0"}
                        )
                        continue
                    if temp_path is None:
                        fd, name = tempfile.mkstemp(prefix="hrm-update-", suffix=".zip.part")
                        temp_path = Path(name)
                        import os

                        os.close(fd)
                    declared = int(manifest["package_size"])
                    total = 0
                    with temp_path.open("wb") as out:
                        while True:
                            chunk = response.read(1024 * 1024)
                            if not chunk:
                                break
                            total += len(chunk)
                            if total > max_bytes or total > declared + 1:
                                raise ChannelError(
                                    "download_failed", "пакет превышает объявленный размер"
                                )
                            out.write(chunk)
                    if total != declared:
                        raise ChannelError(
                            "download_failed",
                            f"размер не совпал: получено {total}, объявлено {declared}",
                        )
                    return temp_path, total
            except ChannelError:
                raise
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                raise ChannelError(
                    "channel_offline", f"скачивание прервано: {exc.__class__.__name__}"
                ) from exc
        raise ChannelError("channel_offline", "превышен лимит redirect'ов пакета")
    except Exception:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink(missing_ok=True)
        raise


def download_package(settings: Settings, manifest: dict) -> Path:
    """Скачивание пакета во временный файл с проверкой размера и SHA256,
    затем атомарная публикация в staging. Частичный файл релизом не считается.
    """
    declared = int(manifest["package_size"])
    if declared > DOWNLOAD_DEFAULT_MAX_BYTES:
        raise ChannelError("download_failed", "пакет превышает допустимый предел")
    temp_path, _ = _download_to_temp(manifest, DOWNLOAD_DEFAULT_MAX_BYTES)
    digest = hashlib.sha256()
    with temp_path.open("rb") as fh:
        while True:
            chunk = fh.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    if digest.hexdigest() != manifest["package_sha256"]:
        temp_path.unlink(missing_ok=True)
        raise ChannelError("package_hash_mismatch", "SHA256 пакета не совпал с manifest")
    root = staging_root(settings)
    root.mkdir(parents=True, exist_ok=True)
    target = root / f"release-{manifest['release_sha'][:12]}.zip"
    if target.exists():
        # Повторный запуск после обрыва: staging уже готов — используем его.
        temp_path.unlink(missing_ok=True)
        return target
    temp_path.replace(target)  # атомарная публикация
    return target
