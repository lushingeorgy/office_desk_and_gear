"""SMTP delivery. Local outbox is explicit development-only tooling."""
import json
import secrets
import smtplib
import ssl
from email.message import EmailMessage
from server.core.config import settings
from server.core.exceptions import ServiceError


def send_code(email: str, code: str, purpose: str):
    if settings.MAIL_MODE == "outbox" and settings.ENVIRONMENT != "production":
        directory = settings.DATA_DIR / "outbox"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / (secrets.token_hex(16) + ".json")
        path.write_text(json.dumps({"email": email, "code": code, "purpose": purpose}), encoding="utf-8")
        path.chmod(0o600)
        return
    if not settings.SMTP_HOST:
        raise ServiceError("Отправка почты не настроена. Обратитесь к администратору", status_code=503)
    message = EmailMessage()
    message["From"], message["To"] = settings.SMTP_FROM, email
    message["Subject"] = "Office Desk & Gear — код подтверждения"
    message.set_content(f"{purpose}\nКод: {code}\nДействует 30 минут. Никому не сообщайте код.")
    try:
        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=10) as smtp:
            if settings.SMTP_TLS:
                smtp.starttls(context=ssl.create_default_context())
            if settings.SMTP_USER:
                smtp.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
            smtp.send_message(message)
    except (OSError, smtplib.SMTPException) as exc:
        raise ServiceError("Не удалось отправить письмо. Повторите позже", status_code=503) from exc
