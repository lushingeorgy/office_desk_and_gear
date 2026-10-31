"""Bounded per-process throttling for authentication endpoints."""
from collections import defaultdict, deque
import threading
import time
from fastapi import HTTPException, Request

_attempts = defaultdict(deque)
_lock = threading.Lock()


def auth_rate_limit(request: Request):
    # Do not trust X-Forwarded-For supplied by untrusted callers.
    key = (request.client.host if request.client else "unknown", request.url.path)
    now = time.monotonic()
    limit = 30 if request.url.path.endswith("login") else 10
    with _lock:
        if len(_attempts) > 4096:
            for old_key in list(_attempts):
                if not _attempts[old_key] or _attempts[old_key][-1] < now - 60:
                    del _attempts[old_key]
        queue = _attempts[key]
        while queue and queue[0] < now - 60:
            queue.popleft()
        if len(queue) >= limit:
            raise HTTPException(status_code=429, detail="Слишком много попыток. Повторите через минуту", headers={"Retry-After": "60"})
        queue.append(now)
