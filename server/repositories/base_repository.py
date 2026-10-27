from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session


class BaseRepository:
    def __init__(self, session: Session) -> None:
        self.session = session


    def commit(self) -> None:
        try:
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise

    def flush(self) -> None:
        self.session.flush()

    def rollback(self) -> None:
        self.session.rollback()

    def refresh(self, obj) -> None:
        self.session.refresh(obj)


    def add(self, obj):
        self.session.add(obj)
        self.commit()
        return obj

    def delete(self, obj) -> bool:
        if obj is None:
            return False
        self.session.delete(obj)
        self.commit()
        return True

    def save(self, obj):
        self.session.add(obj)
        self.commit()
        return obj

    def get(self, model, pk: Optional[int]):
        if pk is None:
            return None
        return self.session.get(model, pk)


__all__ = ["BaseRepository"]
