"""Тесты сетевой политики канала (Phase 13 rework): каждый redirect-хоп
проверяется ДО обращения, автоматический redirect отключён.

Локальные детерминированные HTTPS-серверы (self-signed), без GitHub/CDN.
Доказывается, что запрещённый target НЕ получает запрос (счётчик hits).
"""

from __future__ import annotations

import ipaddress
import json
import ssl
import threading
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import cast
from urllib.parse import urlsplit

import pytest

from app.channel import (
    ChannelError,
    download_package,
    fetch_manifest_text,
)
from app.config import Settings
from app.update_channel_contract import parse_manifest_json

TESTDATA = Path(__file__).resolve().parents[2] / "infra" / "release" / "testdata"


def _channel_settings(tmp_path: Path, url: str, allowed_hosts: str) -> Settings:
    return Settings.model_validate(
        {
            "APP_ENV": "test",
            "APP_DEBUG": "false",
            "SECRET_KEY": "unit-test-secret-key",
            "DATABASE_URL": "sqlite+pysqlite://",
            "UPDATE_CHANNEL_URL": url,
            "UPDATE_CHANNEL_PUBLIC_KEYS": "{}",
            "UPDATE_ENGINE_TOKEN": "engine-token-0123456789abcdef",
            "UPDATE_INSTALLED_VERSION": "0.13.0",
            "UPDATE_INSTALLED_SHA": "3" * 40,
            "UPDATE_CHANNEL_ALLOWED_HOSTS": allowed_hosts,
            "UPDATE_STAGING_DIR": str(tmp_path / "staging"),
        }
    )


def _make_cert(tmp_path: Path) -> tuple[Path, Path]:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    san = x509.SubjectAlternativeName(
        [x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
    )
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(UTC) - timedelta(days=1))
        .not_valid_after(datetime.now(UTC) + timedelta(days=30))
        .add_extension(san, critical=False)
        .sign(key, hashes.SHA256())
    )
    cert_path = tmp_path / "cert.pem"
    key_path = tmp_path / "key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    return cert_path, key_path


class _TestServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], routes: dict) -> None:
        self.routes = routes
        self.hits: dict[str, int] = {}
        super().__init__(address, _TestHandler)


class _TestHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        server = cast(_TestServer, self.server)
        server.hits[path] = server.hits.get(path, 0) + 1
        entry = server.routes.get(path)
        if entry is None:
            self.send_error(404)
            return
        status, headers, body = entry
        self.send_response(status)
        for key, value in headers.items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def log_message(self, fmt: str, *args: object) -> None:
        pass


@pytest.fixture()
def https_world(tmp_path: Path) -> Iterator[dict]:
    """HTTPS-сервер (localhost) + сервер запрещённого хоста (127.0.0.1) +
    plain-HTTP сервер для downgrade; клиентский контекст доверяет сертификату."""
    cert_path, key_path = _make_cert(tmp_path)

    def tls_server(routes: dict) -> _TestServer:
        server = _TestServer(("127.0.0.1", 0), routes)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(str(cert_path), str(key_path))
        server.socket = context.wrap_socket(server.socket, server_side=True)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return server

    def plain_server(routes: dict) -> _TestServer:
        server = _TestServer(("127.0.0.1", 0), routes)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return server

    world: dict = {
        "cert": cert_path,
        "tls": tls_server,
        "plain": plain_server,
        "servers": [],
        "client_ctx": ssl.create_default_context(cafile=str(cert_path)),
    }
    yield world
    for server in world["servers"]:
        server.shutdown()
        server.server_close()


def _route(status: int, location: str | None = None, body: bytes = b"") -> tuple:
    headers = {"Location": location} if location else {}
    return (status, headers, body)


def test_manifest_happy_path_with_relative_redirect(https_world: dict, tmp_path: Path) -> None:
    manifest_text = (TESTDATA / "manifest.valid.json").read_text(encoding="utf-8")
    server = https_world["tls"](
        {
            "/start": _route(302, "/manifest.json"),
            "/manifest.json": _route(200, body=manifest_text.encode()),
        }
    )
    https_world["servers"].append(server)
    settings = _channel_settings(
        tmp_path, f"https://localhost:{server.server_port}/start", "localhost"
    )
    result = fetch_manifest_text(settings, ssl_context=https_world["client_ctx"])
    assert json.loads(result)["version"] == "0.14.0"
    assert server.hits == {"/start": 1, "/manifest.json": 1}


def test_manifest_redirect_to_forbidden_host_no_request(https_world: dict, tmp_path: Path) -> None:
    forbidden = https_world["tls"]({"/never": _route(200, body=b"secret")})
    https_world["servers"].append(forbidden)
    server = https_world["tls"](
        {"/start": _route(302, f"https://127.0.0.1:{forbidden.server_port}/never")}
    )
    https_world["servers"].append(server)
    # allowed = только localhost; 127.0.0.1 — запрещённый хост.
    settings = _channel_settings(
        tmp_path, f"https://localhost:{server.server_port}/start", "localhost"
    )
    with pytest.raises(ChannelError) as excinfo:
        fetch_manifest_text(settings, ssl_context=https_world["client_ctx"])
    assert excinfo.value.code == "bad_url"
    # Запрещённый target НЕ получил запрос.
    assert forbidden.hits == {}
    assert "127.0.0.1" not in str(excinfo.value)


def test_manifest_https_to_http_downgrade_no_request(https_world: dict, tmp_path: Path) -> None:
    plain = https_world["plain"]({"/never": _route(200, body=b"secret")})
    https_world["servers"].append(plain)
    server = https_world["tls"](
        {"/start": _route(302, f"http://localhost:{plain.server_port}/never")}
    )
    https_world["servers"].append(server)
    settings = _channel_settings(
        tmp_path, f"https://localhost:{server.server_port}/start", "localhost"
    )
    with pytest.raises(ChannelError) as excinfo:
        fetch_manifest_text(settings, ssl_context=https_world["client_ctx"])
    assert excinfo.value.code == "bad_url"
    assert plain.hits == {}  # HTTP target не получил запрос


def test_manifest_redirect_chain_over_limit(https_world: dict, tmp_path: Path) -> None:
    routes = {f"/h{i}": _route(302, f"/h{i + 1}") for i in range(7)}
    routes["/h7"] = _route(200, body=b"ok")
    server = https_world["tls"](routes)
    https_world["servers"].append(server)
    settings = _channel_settings(
        tmp_path, f"https://localhost:{server.server_port}/h0", "localhost"
    )
    with pytest.raises(ChannelError) as excinfo:
        fetch_manifest_text(settings, ssl_context=https_world["client_ctx"])
    assert excinfo.value.code == "redirect_limit"


def test_manifest_redirect_loop(https_world: dict, tmp_path: Path) -> None:
    server = https_world["tls"]({"/a": _route(302, "/b"), "/b": _route(302, "/a")})
    https_world["servers"].append(server)
    settings = _channel_settings(tmp_path, f"https://localhost:{server.server_port}/a", "localhost")
    with pytest.raises(ChannelError) as excinfo:
        fetch_manifest_text(settings, ssl_context=https_world["client_ctx"])
    assert excinfo.value.code == "redirect_loop"


def test_manifest_initial_non_https_url_rejected_without_network(tmp_path: Path) -> None:
    settings = _channel_settings(tmp_path, "http://localhost/x", "localhost")
    with pytest.raises(ChannelError) as excinfo:
        fetch_manifest_text(settings)
    assert excinfo.value.code == "bad_url"


def _package_manifest(package_url: str, package_bytes: bytes) -> dict:
    import hashlib

    manifest = parse_manifest_json((TESTDATA / "manifest.valid.json").read_text(encoding="utf-8"))
    manifest["package_url"] = package_url
    manifest["package_size"] = len(package_bytes)
    manifest["package_sha256"] = hashlib.sha256(package_bytes).hexdigest()
    return manifest


def test_package_happy_path_with_relative_redirect(https_world: dict, tmp_path: Path) -> None:
    package_bytes = (TESTDATA / "package.valid.zip").read_bytes()
    server = https_world["tls"](
        {"/pkg": _route(302, "/pkg2"), "/pkg2": _route(200, body=package_bytes)}
    )
    https_world["servers"].append(server)
    manifest = _package_manifest(f"https://localhost:{server.server_port}/pkg", package_bytes)
    settings = _channel_settings(tmp_path, "https://localhost/unused", "localhost")
    target = download_package(settings, manifest, ssl_context=https_world["client_ctx"])
    assert target.exists()
    assert target.read_bytes() == package_bytes
    assert server.hits == {"/pkg": 1, "/pkg2": 1}


def test_package_redirect_to_forbidden_host_no_request(https_world: dict, tmp_path: Path) -> None:
    package_bytes = (TESTDATA / "package.valid.zip").read_bytes()
    forbidden = https_world["tls"]({"/never": _route(200, body=package_bytes)})
    https_world["servers"].append(forbidden)
    server = https_world["tls"](
        {"/pkg": _route(302, f"https://127.0.0.1:{forbidden.server_port}/never")}
    )
    https_world["servers"].append(server)
    manifest = _package_manifest(f"https://localhost:{server.server_port}/pkg", package_bytes)
    settings = _channel_settings(tmp_path, "https://localhost/unused", "localhost")
    with pytest.raises(ChannelError) as excinfo:
        download_package(settings, manifest, ssl_context=https_world["client_ctx"])
    assert excinfo.value.code == "bad_url"
    assert forbidden.hits == {}
    assert "127.0.0.1" not in str(excinfo.value)


def test_package_https_to_http_downgrade_no_request(https_world: dict, tmp_path: Path) -> None:
    package_bytes = (TESTDATA / "package.valid.zip").read_bytes()
    plain = https_world["plain"]({"/never": _route(200, body=package_bytes)})
    https_world["servers"].append(plain)
    server = https_world["tls"](
        {"/pkg": _route(302, f"http://localhost:{plain.server_port}/never")}
    )
    https_world["servers"].append(server)
    manifest = _package_manifest(f"https://localhost:{server.server_port}/pkg", package_bytes)
    settings = _channel_settings(tmp_path, "https://localhost/unused", "localhost")
    with pytest.raises(ChannelError) as excinfo:
        download_package(settings, manifest, ssl_context=https_world["client_ctx"])
    assert excinfo.value.code == "bad_url"
    assert plain.hits == {}


def test_package_redirect_loop(https_world: dict, tmp_path: Path) -> None:
    package_bytes = (TESTDATA / "package.valid.zip").read_bytes()
    server = https_world["tls"]({"/a": _route(302, "/b"), "/b": _route(302, "/a")})
    https_world["servers"].append(server)
    manifest = _package_manifest(f"https://localhost:{server.server_port}/a", package_bytes)
    settings = _channel_settings(tmp_path, "https://localhost/unused", "localhost")
    with pytest.raises(ChannelError) as excinfo:
        download_package(settings, manifest, ssl_context=https_world["client_ctx"])
    assert excinfo.value.code == "redirect_loop"


def test_package_redirect_chain_over_limit(https_world: dict, tmp_path: Path) -> None:
    package_bytes = (TESTDATA / "package.valid.zip").read_bytes()
    routes = {f"/h{i}": _route(302, f"/h{i + 1}") for i in range(7)}
    routes["/h7"] = _route(200, body=package_bytes)
    server = https_world["tls"](routes)
    https_world["servers"].append(server)
    manifest = _package_manifest(f"https://localhost:{server.server_port}/h0", package_bytes)
    settings = _channel_settings(tmp_path, "https://localhost/unused", "localhost")
    with pytest.raises(ChannelError) as excinfo:
        download_package(settings, manifest, ssl_context=https_world["client_ctx"])
    assert excinfo.value.code == "redirect_limit"
