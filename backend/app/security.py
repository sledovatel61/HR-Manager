"""Security primitives: password hashing, token generation, password policy.

Passwords are hashed with Argon2id (RFC 9106 recommendation) using the
``argon2-cffi`` PasswordHasher defaults (memory 64 MiB, 3 iterations,
4-way parallelism), which satisfy current OWASP guidance. Only the hash is
ever persisted or logged; verification is constant-time inside the library.
"""

import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from app.config import MIN_PASSWORD_LENGTH

# OWASP-aligned Argon2id parameters (argon2-cffi defaults, stated explicitly
# so the policy is visible). Memory cost is in KiB.
password_hasher = PasswordHasher(
    time_cost=3,
    memory_cost=64 * 1024,
    parallelism=4,
    hash_len=32,
    salt_len=16,
)

# CSRF tokens are random 256-bit values rendered as URL-safe strings. Session
# identifiers are UUID4 primary keys (122 bits of entropy) carried in an
# HttpOnly cookie.
CSRF_TOKEN_BYTES = 32


class WeakPasswordError(ValueError):
    """Raised when a password does not satisfy the password policy."""


def hash_password(password: str) -> str:
    """Return an Argon2id hash for ``password``."""
    return password_hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    """Verify ``password`` against ``password_hash`` without raising.

    Returns ``False`` for malformed hashes or mismatches so callers never have
    to handle cryptographic exceptions on the login path.
    """
    try:
        return password_hasher.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError, ValueError):
        return False


def needs_rehash(password_hash: str) -> bool:
    """Whether the hash should be regenerated with current parameters."""
    try:
        return password_hasher.check_needs_rehash(password_hash)
    except (InvalidHashError, ValueError):
        return False


def generate_csrf_token() -> str:
    """Generate a random CSRF token (bound to a session server-side)."""
    return secrets.token_urlsafe(CSRF_TOKEN_BYTES)


def validate_password_policy(password: str, username: str | None = None) -> None:
    """Validate ``password`` against the minimum password policy.

    Raises :class:`WeakPasswordError` with a human-readable (Russian) message
    on the first violation. The policy deliberately stays simple and
    predictable for an internal corporate system: length plus character
    classes, no blocklists of personal data.
    """
    if not isinstance(password, str) or not password:
        raise WeakPasswordError("Пароль обязателен.")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise WeakPasswordError(f"Пароль должен содержать не менее {MIN_PASSWORD_LENGTH} символов.")
    if len(password) > 128:
        raise WeakPasswordError("Пароль не должен быть длиннее 128 символов.")
    if not any(char.isalpha() for char in password):
        raise WeakPasswordError("Пароль должен содержать хотя бы одну букву.")
    if not any(char.isdigit() for char in password):
        raise WeakPasswordError("Пароль должен содержать хотя бы одну цифру.")
    if username and password.strip().lower() == username.strip().lower():
        raise WeakPasswordError("Пароль не должен совпадать с именем пользователя.")
