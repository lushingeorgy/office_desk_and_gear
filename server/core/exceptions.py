from __future__ import annotations

from typing import Optional


class ServiceError(Exception):
    status_code: int = 400
    default_message: str = "Не удалось выполнить операцию"

    def __init__(
        self,
        message: Optional[str] = None,
        status_code: Optional[int] = None,
        headers: Optional[dict[str, str]] = None,
    ) -> None:
        self.message = message or self.default_message
        if status_code is not None:
            self.status_code = status_code
        self.headers = headers or {}
        super().__init__(self.message)

    def __str__(self) -> str:
        return self.message


class BadRequestError(ServiceError):
    status_code = 400
    default_message = "Некорректный запрос"


class ValidationError(ServiceError):
    status_code = 422
    default_message = "Проверьте правильность заполнения полей"


class UnauthorizedError(ServiceError):
    status_code = 401
    default_message = "Неверный адрес почты или пароль"


class ForbiddenError(ServiceError):
    status_code = 403
    default_message = "Недостаточно прав для выполнения операции"


class NotFoundError(ServiceError):
    status_code = 404
    default_message = "Запись не найдена"


class ConflictError(ServiceError):
    status_code = 409
    default_message = "Конфликт данных"


class DatabaseError(ServiceError):
    status_code = 500
    default_message = "Ошибка сервера. Попробуйте позже"


__all__ = [
    "ServiceError",
    "BadRequestError",
    "ValidationError",
    "UnauthorizedError",
    "ForbiddenError",
    "NotFoundError",
    "ConflictError",
    "DatabaseError",
]
