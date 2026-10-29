import base64
import binascii
import io
import secrets
from PIL import Image, UnidentifiedImageError
from server.core.config import settings
from server.core.exceptions import ForbiddenError, NotFoundError, ValidationError
from server.models.activity import Upload, TicketEvent
from server.repositories.activity_repository import ActivityRepository
from server.repositories.ticket_repository import TicketRepository


class ActivityService:
    def __init__(self, repository: ActivityRepository):
        self.repository = repository

    def notifications(self, user):
        return self.repository.notifications(user.id)

    def read_notification(self, identifier, user):
        item = self.repository.notification(identifier)
        if item is None:
            raise NotFoundError("Уведомление не найдено")
        if item.user_id != user.id:
            raise ForbiddenError()
        item.is_read = True
        self.repository.commit()

    def _ticket(self, ticket_id, user):
        ticket = TicketRepository(self.repository.session).get_ticket_by_id(ticket_id)
        if ticket is None:
            raise NotFoundError("Обращение не найдено")
        if ticket.author_id != user.id and user.role != "it_admin":
            raise ForbiddenError()
        return ticket

    def events(self, ticket_id, user):
        self._ticket(ticket_id, user)
        return self.repository.events(ticket_id)

    def comment(self, ticket_id, user, text):
        self._ticket(ticket_id, user)
        text = text.strip()
        if not text:
            raise ValidationError("Введите комментарий")
        item = TicketEvent(ticket_id=ticket_id, author_id=user.id, kind="comment", text=text)
        return self.repository.add(item)

    def upload_image(self, user, encoded):
        try:
            raw = base64.b64decode(encoded.split(",", 1)[-1], validate=True)
            if len(raw) > 5_000_000:
                raise ValueError()
            with Image.open(io.BytesIO(raw)) as image:
                if image.width * image.height > 20_000_000:
                    raise ValueError()
                image.load()
                image = image.convert("RGB")
                image.thumbnail((1600, 1600))
                output = io.BytesIO()
                image.save(output, "JPEG", quality=85)
        except (ValueError, binascii.Error, UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
            raise ValidationError("Выберите изображение JPG или PNG размером до 5 МБ") from exc
        identifier = secrets.token_hex(24) + ".jpg"
        directory = settings.DATA_DIR / "uploads"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / identifier
        path.write_bytes(output.getvalue())
        try:
            self.repository.add(Upload(id=identifier, user_id=user.id))
        except Exception:
            path.unlink(missing_ok=True)
            raise
        return {"photo_url": "/api/v1/uploads/" + identifier}

    def image_path(self, identifier, user):
        upload = self.repository.upload(identifier)
        if upload is None:
            raise NotFoundError("Изображение не найдено")
        if upload.user_id != user.id and user.role != "it_admin":
            raise ForbiddenError()
        path = settings.DATA_DIR / "uploads" / upload.id
        if not path.is_file():
            raise NotFoundError("Изображение не найдено")
        return path
