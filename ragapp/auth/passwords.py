"""Password hashing and verification (PBKDF2-HMAC-SHA256)."""

import hashlib
import secrets

from ragapp import settings


def hash_password(password, salt=None):
    if salt is None:
        salt = secrets.token_bytes(32)

    password_hash = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        settings.HASH_ITERATIONS,
    )

    return (
        password_hash.hex(),
        salt.hex(),
    )


def verify_password(password, stored_hash, stored_salt):
    salt = bytes.fromhex(stored_salt)
    password_hash, _ = hash_password(
        password,
        salt=salt,
    )
    return secrets.compare_digest(
        password_hash,
        stored_hash,
    )
