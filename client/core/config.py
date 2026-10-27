import os

API_BASE_URL = os.getenv("DSH_API_URL", "http://127.0.0.1:8000/api/v1").rstrip("/")
REQUEST_TIMEOUT = 10
