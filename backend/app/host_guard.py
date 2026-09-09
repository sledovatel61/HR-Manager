"""Loopback host guard for the locally-trusted pilot profile (phase 12).

The pilot profile serves the whole stack over plain ``http://127.0.0.1`` with
non-Secure session cookies. To keep that safe, every API request in the
profile must carry a Host header that is a loopback name. This closes the
classic DNS-rebinding hole: a malicious web page whose (rebound) domain
resolves to ``127.0.0.1`` would send ``Host: evil.example.com`` and is
rejected here, while the legitimate browser sends ``Host: 127.0.0.1`` (nginx
forwards ``$host`` from the browser unchanged).

The check is intentionally a pure function so it can be unit-tested on any
platform without the web framework.
"""

from __future__ import annotations

# Loopback names accepted as the request Host (case-insensitive). Any port
# suffix is allowed and stripped before the comparison (browsers keep the
# port in the Host header: ``127.0.0.1:8081``).
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "[::1]"})

REJECTION_DETAIL = (
    "HR Manager в локальном пилотном профиле отвечает только по адресу 127.0.0.1; "
    "обращение с другим именем хоста отклонено."
)


def split_host_header(value: str | None) -> str:
    """Return the bare host part of a Host header value (no port, no zone id).

    Handles ``host:port``, bracketed IPv6 ``[::1]:8080`` and bare ``::1``.
    Trailing dots and case differences are normalised away; malformed values
    are returned lower-cased unchanged (and then rejected).
    """
    if value is None:
        return ""
    host = value.strip().rstrip(".").lower()
    if host.startswith("["):
        # Bracketed IPv6, optionally with :port after the bracket.
        end = host.find("]")
        return host[: end + 1] if end != -1 else host
    # A colon in a non-bracketed value separates the port for IPv4/DNS names.
    # A bare IPv6 address (multiple colons, no brackets) is compared as-is.
    if host.count(":") == 1:
        host = host.split(":", 1)[0]
    return host


def is_loopback_host(value: str | None) -> bool:
    """True when the Host header names a loopback endpoint of this machine."""
    return split_host_header(value) in LOOPBACK_HOSTS
