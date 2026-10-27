from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from server.core.config import settings

engine = create_engine(
    settings.DATABASE_URL,
    echo=settings.SQL_ECHO,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=5,
    pool_timeout=15,
    connect_args={"connect_timeout": 5, "application_name": "office_desk_gear"},
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    with SessionLocal() as db:
        try:
            yield db
        except Exception:
            db.rollback()
            raise


@contextmanager
def session_scope() -> Iterator[Session]:
    with SessionLocal.begin() as db:
        yield db
