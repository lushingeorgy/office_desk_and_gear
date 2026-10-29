from __future__ import annotations

import uvicorn
import logging
import time
import uuid
from fastapi.middleware.cors import CORSMiddleware

from server.api.api import app
from server.core.config import settings
from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from server.database.base import init_db

logger = logging.getLogger("office_desk_gear")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


@app.exception_handler(RequestValidationError)
async def validation_error(_request, exc):
    # FastAPI's default error includes rejected input, potentially a password.
    errors = [{"loc": item["loc"], "msg": item["msg"], "type": item["type"]} for item in exc.errors()]
    return JSONResponse(status_code=422, content={"detail": errors})


@app.middleware("http")
async def log_requests(request: Request, call_next):
    identifier = uuid.uuid4().hex
    started = time.monotonic()
    response = await call_next(request)
    response.headers["X-Request-ID"] = identifier
    logger.info("request=%s method=%s path=%s status=%s duration_ms=%.1f", identifier, request.method, request.url.path, response.status_code, (time.monotonic() - started) * 1000)
    return response


@app.exception_handler(IntegrityError)
async def integrity_error(_request, _exc):
    # Never log bound SQL parameters (they may contain personal data or hashes).
    logger.warning("Database integrity constraint rejected an operation")
    return JSONResponse(status_code=409, content={"detail": "Операция конфликтует с существующими данными"})


@app.exception_handler(SQLAlchemyError)
async def database_error(_request, _exc):
    logger.error("Database operation failed")
    return JSONResponse(status_code=503, content={"detail": "Хранилище временно недоступно. Повторите позже"})


@app.exception_handler(Exception)
async def unexpected_error(_request, exc):
    logger.error("Unhandled server error type=%s", type(exc).__name__)
    return JSONResponse(status_code=500, content={"detail": "Ошибка сервера. Повторите позже"})

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials="*" not in settings.CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", tags=["system"])
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/ready", tags=["system"])
def ready():
    try:
        init_db()
    except Exception:
        return JSONResponse(status_code=503, content={"status": "unavailable"})
    return {"status": "ready"}


def main() -> None:
    uvicorn.run(app, host=settings.HOST, port=settings.PORT)


if __name__ == "__main__":
    main()
