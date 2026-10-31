"""Password hashing and signed, revocable access tokens."""
import hashlib
import secrets
import jwt
from passlib.context import CryptContext
from server.core.config import settings

_passwords = CryptContext(schemes=["pbkdf2_sha256", "bcrypt"], deprecated="auto", pbkdf2_sha256__default_rounds=600_000)

def hash_password(password: str) -> str:
    return _passwords.hash(password)

def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bool(_passwords.verify(password, password_hash))
    except (ValueError, TypeError):
        return False

def hash_token_value(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()

def signing_secret() -> str:
    if settings.JWT_SECRET:
        if settings.ENVIRONMENT == "production" and len(settings.JWT_SECRET) < 32:
            raise RuntimeError("JWT_SECRET must be at least 32 characters")
        return settings.JWT_SECRET
    if settings.ENVIRONMENT == "production":
        raise RuntimeError("JWT_SECRET must be configured in production")
    path = settings.DATA_DIR / "host-secret.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="ascii") as file:
            file.write(secrets.token_urlsafe(48))
        path.chmod(0o600)
    except FileExistsError:
        pass
    return path.read_text(encoding="ascii").strip()

def decode_access_token(token: str) -> dict:
    return jwt.decode(token, signing_secret(), algorithms=["HS256"], issuer="office-desk-gear", options={"require": ["exp", "iat", "sub", "jti", "iss"]})
