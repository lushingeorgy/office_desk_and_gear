import datetime as dt

from sqlalchemy import delete, select

from server.models.auth import AuthSession, PasswordResetCode, utc_now
from server.models.users import User
from server.repositories.base_repository import BaseRepository


class AuthRepository(BaseRepository):
    def create_session(self, token_hash: str, user_id: int, expires_at: dt.datetime):
        self.session.execute(delete(AuthSession).where(AuthSession.expires_at <= utc_now()))
        return self.add(AuthSession(token_hash=token_hash, user_id=user_id, expires_at=expires_at))

    def get_session_user(self, token_hash: str):
        return self.session.scalar(select(User).join(AuthSession, AuthSession.user_id == User.id).where(
            AuthSession.token_hash == token_hash,
            AuthSession.expires_at > utc_now(),
        ))

    def revoke_session(self, token_hash: str):
        self.session.execute(delete(AuthSession).where(AuthSession.token_hash == token_hash))
        self.commit()

    def revoke_user_sessions(self, user_id: int):
        self.session.execute(delete(AuthSession).where(AuthSession.user_id == user_id))

    def save_reset_code(self, user_id: int, code_hash: str, expires_at: dt.datetime):
        self.session.execute(delete(PasswordResetCode).where(PasswordResetCode.expires_at <= utc_now()))
        self.session.merge(PasswordResetCode(user_id=user_id, code_hash=code_hash, expires_at=expires_at))
        self.commit()

    def consume_reset_code(self, user_id: int, code_hash: str) -> bool:
        result = self.session.execute(delete(PasswordResetCode).where(
            PasswordResetCode.user_id == user_id,
            PasswordResetCode.code_hash == code_hash,
            PasswordResetCode.expires_at > utc_now(),
        ))
        return result.rowcount == 1
