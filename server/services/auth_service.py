import datetime as dt
import secrets

from server.core.config import settings
from server.core.security import hash_token_value
from server.models.auth import utc_now
from server.repositories.auth_repository import AuthRepository
from server.services.user_service import UserService


class AuthService:
    def __init__(self, repository: AuthRepository, users: UserService):
        self.repository = repository
        self.users = users

    def login(self, email: str, password: str) -> str:
        user = self.users.authenticate_model(email, password)
        session_id = secrets.token_urlsafe(32)
        self.repository.create_session(
            hash_token_value(session_id), user.id,
            utc_now() + dt.timedelta(minutes=settings.SESSION_EXPIRE_MINUTES),
        )
        return session_id

    def logout(self, session_id: str):
        self.repository.revoke_session(hash_token_value(session_id))
