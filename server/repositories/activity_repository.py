from sqlalchemy import select
from server.repositories.base_repository import BaseRepository
from server.models.activity import Notification, TicketEvent, Upload


class ActivityRepository(BaseRepository):
    def notifications(self, user_id: int):
        return list(self.session.scalars(select(Notification).where(Notification.user_id == user_id).order_by(Notification.created_at.desc(), Notification.id.desc()).limit(200)))

    def events(self, ticket_id: int):
        return list(self.session.scalars(select(TicketEvent).where(TicketEvent.ticket_id == ticket_id).order_by(TicketEvent.created_at, TicketEvent.id)))

    def upload(self, identifier: str):
        return self.session.get(Upload, identifier)

    def notification(self, identifier: int):
        return self.session.get(Notification, identifier)

    def validate_photo(self, url: str | None, user_id: int):
        from server.core.exceptions import ValidationError, ForbiddenError
        if not url:
            return
        prefix = "/api/v1/uploads/"
        if not url.startswith(prefix):
            raise ValidationError("Сначала загрузите изображение в приложение")
        item = self.upload(url.removeprefix(prefix))
        if item is None:
            raise ValidationError("Изображение не найдено")
        if item.user_id != user_id:
            raise ForbiddenError("Нельзя использовать чужое изображение")
