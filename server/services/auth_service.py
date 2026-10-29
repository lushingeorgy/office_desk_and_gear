import datetime as dt
import secrets
import jwt

from server.core.config import settings
from server.core.security import hash_token_value, signing_secret
from server.models.auth import utc_now
from server.repositories.auth_repository import AuthRepository
from server.services.user_service import UserService


class AuthService:
    def __init__(self, repository: AuthRepository, users: UserService):
        self.repository = repository
        self.users = users

    def login(self, email: str, password: str) -> str:
        user = self.users.authenticate_model(email, password)
        now = utc_now().replace(tzinfo=dt.UTC)
        expires = now + dt.timedelta(minutes=settings.SESSION_EXPIRE_MINUTES)
        session_id = jwt.encode({"sub": str(user.id), "jti": secrets.token_urlsafe(32), "iat": now, "exp": expires, "iss": "office-desk-gear"}, signing_secret(), algorithm="HS256")
        self.repository.create_session(
            hash_token_value(session_id), user.id,
            expires.replace(tzinfo=None),
        )
        return session_id

    def logout(self, session_id: str):
        self.repository.revoke_session(hash_token_value(session_id))
