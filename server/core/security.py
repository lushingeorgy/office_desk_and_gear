from __future__ import annotations

import base64
import hashlib
import hmac


__all__ = ["hash_password", "verify_password", "hash_token_value"]


_HASH_SCHEMES: list[str] = ["pbkdf2_sha256"]
try:
    import bcrypt as _bcrypt

    _HASH_SCHEMES.append("bcrypt")
except Exception:
    pass

try:
    from passlib.context import CryptContext

    _pwd_context = CryptContext(schemes=_HASH_SCHEMES, deprecated="auto")
except Exception:
    _pwd_context = None


def _stdlib_hash(password: str, *, iterations: int = 29000) -> str:
    salt = os_urandom_salt()
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return "pbkdf2_sha256${}${}${}".format(
        iterations,
        base64.b64encode(salt).decode("ascii"),
        base64.b64encode(digest).decode("ascii"),
    )


def os_urandom_salt(length: int = 16) -> bytes:
    import os

    return os.urandom(length)


def hash_password(password: str) -> str:
    if not isinstance(password, str) or not password:
        raise ValueError("Пароль не может быть пустым")
    if _pwd_context is not None:
        try:
            return _pwd_context.hash(password)
        except Exception:
            pass
    return _stdlib_hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    if not password or not password_hash:
        return False
    if _pwd_context is not None:
        try:
            return bool(_pwd_context.verify(password, password_hash))
        except Exception:
            pass
    if password_hash.startswith("pbkdf2_sha256$"):
        try:
            _, iterations, salt_b64, digest_b64 = password_hash.split("$")
            digest = hashlib.pbkdf2_hmac(
                "sha256",
                password.encode("utf-8"),
                base64.b64decode(salt_b64),
                int(iterations),
            )
            return hmac.compare_digest(digest, base64.b64decode(digest_b64))
        except Exception:
            return False
    return False


def hash_token_value(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
