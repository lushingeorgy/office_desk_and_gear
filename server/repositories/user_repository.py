from __future__ import annotations

from typing import List, Optional

from sqlalchemy import select

from server.models.users import User
from server.repositories.base_repository import BaseRepository


class UserRepository(BaseRepository):
    def get_user_by_id(self, user_id: Optional[int]) -> Optional[User]:
        return self.get(User, user_id)

    def get_user_by_email(self, email: Optional[str]) -> Optional[User]:
        if not email:
            return None
        stmt = select(User).where(User.email == str(email).strip().lower())
        return self.session.execute(stmt).scalars().first()

    def get_all_users(self) -> List[User]:
        stmt = select(User).order_by(User.full_name, User.id)
        return list(self.session.execute(stmt).scalars().all())

    def get_users_by_role(self, role: str) -> List[User]:
        return list(self.session.scalars(select(User).where(User.role == role)))

    def create_user(self, user: User) -> User:
        return self.add(user)

    def update_user(self, user: User) -> User:
        return self.save(user)

    def delete_user(self, user_id: int) -> bool:
        user = self.get_user_by_id(user_id)
        return self.delete(user) if user else False


__all__ = ["UserRepository"]
