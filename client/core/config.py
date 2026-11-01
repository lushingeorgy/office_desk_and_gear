import os

# The application has one server. DSH_API_URL is only a developer/test-process
# override; neither UI input nor saved device settings choose the endpoint.
DEFAULT_API_URL = "https://138.16.179.111/api/v1"
API_BASE_URL = (os.getenv("DSH_API_URL") or DEFAULT_API_URL).rstrip("/")
REQUEST_TIMEOUT = 10
