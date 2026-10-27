import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy.engine import make_url

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")


class Settings:
    def __init__(self):
        database_url = make_url(os.getenv("DATABASE_URL") or "postgresql+psycopg://postgres@127.0.0.1:5432/office_desk_gear")
        if database_url.get_backend_name() != "postgresql":
            raise ValueError("Приложение поддерживает только PostgreSQL. Проверьте DATABASE_URL в .env.")
        self.DATABASE_URL = database_url.set(drivername="postgresql+psycopg")
        self.SQL_ECHO = os.getenv("SQL_ECHO", "0").lower() in {"1", "true"}
        self.SESSION_EXPIRE_MINUTES = int(os.getenv("SESSION_EXPIRE_MINUTES", "720"))
        self.QR_BASE_URL = os.getenv("QR_BASE_URL", "http://127.0.0.1:8000/api/v1/equipment").rstrip("/")
        self.CORS_ORIGINS = [value.strip() for value in os.getenv("CORS_ORIGINS", "http://127.0.0.1:8000").split(",") if value.strip()]
        self.HOST = os.getenv("HOST") or "127.0.0.1"
        self.PORT = int(os.getenv("PORT", "8000"))
        self.SEED_ON_START = os.getenv("ODG_SEED", "0").lower() in {"1", "true"}
        self.WORKDAY_HOURS = int(os.getenv("WORKDAY_HOURS", "8"))
        self.ANALYTICS_DEFAULT_DAYS = int(os.getenv("ANALYTICS_DEFAULT_DAYS", "30"))


settings = Settings()
