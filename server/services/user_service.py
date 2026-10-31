from __future__ import annotations

import datetime as dt
import secrets
from enum import Enum
from typing import Any, Optional

from sqlalchemy.exc import IntegrityError

from server.core.exceptions import BadRequestError, NotFoundError, UnauthorizedError, ValidationError
from server.core.security import hash_password, verify_password, hash_token_value
from server.dto.user_dto import UserDTO
from server.models.users import User, ROLES, ROLE_EMPLOYEE
from server.models.auth import utc_now
from server.models.auth import EmailVerificationCode
from server.core.config import settings
from server.core.exceptions import ForbiddenError, ConflictError
from server.services.mail_service import send_code
from sqlalchemy import delete
from server.repositories.user_repository import UserRepository
from server.repositories.auth_repository import AuthRepository

MIN_PASSWORD_LENGTH = 6
DEFAULT_ROLE = ROLE_EMPLOYEE


def _norm_email(email: Optional[str]) -> str:
    return (email or "").strip().lower()


def _validate_email(email: str) -> str:
    if email.count("@") != 1 or any(char.isspace() for char in email) or email.startswith("@") or email.endswith("@") or "." not in email.split("@")[-1]:
        raise BadRequestError("Укажите корректный адрес электронной почты")
    return email


def _validate_password(password: Optional[str]) -> str:
    if not password or len(password) < MIN_PASSWORD_LENGTH:
        raise ValidationError(
            f"Пароль должен содержать не менее {MIN_PASSWORD_LENGTH} символов"
        )
    return password


class UserService:
    def __init__(self, user_repository: UserRepository) -> None:
        self.user_repository = user_repository
        self.auth_repository = AuthRepository(user_repository.session)


    def get_user(self, user_id: int) -> UserDTO:
        user = self.user_repository.get_user_by_id(user_id)
        if user is None:
            raise NotFoundError("Пользователь не найден")
        return UserDTO.from_user(user)

    def get_user_model(self, user_id: int) -> User:
        user = self.user_repository.get_user_by_id(user_id)
        if user is None:
            raise NotFoundError("Пользователь не найден")
        return user

    def get_user_by_email(self, email: str) -> Optional[UserDTO]:
        user = self.user_repository.get_user_by_email(_norm_email(email))
        return UserDTO.from_user(user) if user else None

    def get_all_users(self) -> list[UserDTO]:
        return [UserDTO.from_user(user) for user in self.user_repository.get_all_users()]


    def register(self, payload: Any) -> UserDTO:
        full_name = (getattr(payload, "full_name", "") or "").strip()
        email = _validate_email(_norm_email(getattr(payload, "email", None)))
        password = _validate_password(getattr(payload, "password", None))
        if not full_name:
            raise BadRequestError("Укажите ФИО сотрудника")

        if self.user_repository.get_user_by_email(email) is not None:
            raise ConflictError("Пользователь с такой почтой уже зарегистрирован")

        role = getattr(payload, "role", DEFAULT_ROLE)
        role = role.value if isinstance(role, Enum) else role
        if not isinstance(role, str) or role not in ROLES:
            raise ValidationError("Недопустимая роль пользователя")
        user = User(
            full_name=full_name,
            job_title=(getattr(payload, "job_title", "") or "").strip(),
            email=email,
            password_hash=hash_password(password),
            role=role,
            created_at=dt.date.today(),
            is_verified=not settings.REQUIRE_EMAIL_VERIFICATION,
        )
        try:
            self.user_repository.session.add(user)
            self.user_repository.flush()
            if not user.is_verified:
                self._send_verification(user)
            self.user_repository.commit()
        except IntegrityError as error:
            self.user_repository.rollback()
            raise ConflictError("Пользователь с такой почтой уже зарегистрирован") from error
        return UserDTO.from_user(user)


    create_user = register

    def authenticate(self, email: str, password: str) -> UserDTO:
        user = self.user_repository.get_user_by_email(_norm_email(email))
        if user is None or not verify_password(password or "", user.password_hash):
            raise UnauthorizedError("Неверный адрес почты или пароль")
        return UserDTO.from_user(user)

    def authenticate_model(self, email: str, password: str) -> User:
        user = self.user_repository.get_user_by_email(_norm_email(email))
        if user is None or not verify_password(password or "", user.password_hash):
            raise UnauthorizedError("Неверный адрес почты или пароль")
        if not user.is_verified:
            raise ForbiddenError("Подтвердите электронную почту кодом из письма")
        return user


    def update_profile(self, user_id: int, payload: Any) -> UserDTO:
        user = self.user_repository.get_user_by_id(user_id)
        if user is None:
            raise NotFoundError("Пользователь не найден")

        full_name = getattr(payload, "full_name", None)
        email = getattr(payload, "email", None)
        job_title = getattr(payload, "job_title", None)
        for field in ("phone", "avatar_url"):
            value = getattr(payload, field, None)
            if value is not None:
                if field == "avatar_url":
                    from server.repositories.activity_repository import ActivityRepository
                    ActivityRepository(self.user_repository.session).validate_photo(value, user_id)
                setattr(user, field, value.strip() or None if field == "avatar_url" else value.strip())
        if job_title is not None:
            user.job_title = job_title.strip()

        if full_name is not None:
            cleaned = str(full_name).strip()
            if not cleaned:
                raise BadRequestError("ФИО не может быть пустым")
            user.full_name = cleaned

        if email is not None:
            new_email = _validate_email(_norm_email(email))
            if new_email != user.email:
                if self.user_repository.get_user_by_email(new_email) is not None:
                    raise BadRequestError("Эта почта уже используется другим сотрудником")
                user.email = new_email
                if settings.REQUIRE_EMAIL_VERIFICATION:
                    user.is_verified = False
                    self._send_verification(user)

        try:
            user = self.user_repository.update_user(user)
        except IntegrityError as error:
            self.user_repository.rollback()
            raise BadRequestError("Эта почта уже используется другим сотрудником") from error
        return UserDTO.from_user(user)


    update_user = update_profile

    def set_role(self, user_id: int, role: str) -> UserDTO:
        user = self.user_repository.get_user_by_id(user_id)
        if user is None:
            raise NotFoundError("Пользователь не найден")
        candidate = (role or "").strip().lower()
        if candidate not in ROLES:
            raise ValidationError("Недопустимая роль пользователя")
        user.role = candidate
        return UserDTO.from_user(self.user_repository.update_user(user))


    def issue_reset_code(self, email: str) -> str:
        normalized = _validate_email(_norm_email(email))
        user = self.user_repository.get_user_by_email(normalized)
        if user is None:
            return ""  # Do not reveal whether an address has an account.
        code = f"{secrets.randbelow(10 ** 6):06d}"
        send_code(normalized, code, "Восстановление пароля")
        self.auth_repository.save_reset_code(
            user.id, hash_token_value(code), utc_now() + dt.timedelta(minutes=30)
        )
        return code

    def _send_verification(self, user: User):
        code = f"{secrets.randbelow(10 ** 6):06d}"
        send_code(user.email, code, "Подтверждение электронной почты")
        self.user_repository.session.merge(EmailVerificationCode(user_id=user.id, code_hash=hash_token_value(code), expires_at=utc_now() + dt.timedelta(minutes=30)))

    def resend_verification(self, email: str):
        user = self.user_repository.get_user_by_email(_norm_email(email))
        if user is not None and not user.is_verified:
            self._send_verification(user)
            self.user_repository.commit()

    def verify_email(self, email: str, code: str):
        user = self.user_repository.get_user_by_email(_norm_email(email))
        if user is None:
            raise ValidationError("Неверный или просроченный код подтверждения")
        result = self.user_repository.session.execute(delete(EmailVerificationCode).where(EmailVerificationCode.user_id == user.id, EmailVerificationCode.code_hash == hash_token_value(code.strip()), EmailVerificationCode.expires_at > utc_now()))
        if result.rowcount != 1:
            raise ValidationError("Неверный или просроченный код подтверждения")
        user.is_verified = True
        self.user_repository.commit()

    def confirm_reset(self, email: str, code: str, new_password: str) -> UserDTO:
        normalized = _validate_email(_norm_email(email))
        password = _validate_password(new_password)
        user = self.user_repository.get_user_by_email(normalized)
        if user is None:
            raise BadRequestError("Пользователь с такой почтой не найден")

        provided = (code or "").strip()
        valid = bool(provided) and self.auth_repository.consume_reset_code(user.id, hash_token_value(provided))
        if not valid:
            raise BadRequestError("Неверный или просроченный код подтверждения")

        user.password_hash = hash_password(password)
        self.auth_repository.revoke_user_sessions(user.id)
        return UserDTO.from_user(self.user_repository.update_user(user))

    def delete_user(self, user_id: int) -> bool:
        return self.user_repository.delete_user(user_id)


__all__ = ["UserService", "MIN_PASSWORD_LENGTH", "DEFAULT_ROLE"]
