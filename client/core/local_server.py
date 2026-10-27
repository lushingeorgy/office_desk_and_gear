import threading
import time

import requests


class LocalServer:
    def __init__(self, host="127.0.0.1", port=8000):
        self.host = host
        self.port = port
        address = "127.0.0.1" if host == "0.0.0.0" else "::1" if host == "::" else host
        if ":" in address:
            address = f"[{address}]"
        self.base_url = f"http://{address}:{port}/api/v1"
        self.server = None
        self.thread = None

    def __enter__(self):
        try:
            with requests.get(self.base_url + "/system/info", timeout=1) as response:
                if response.status_code == 200 and response.json().get("service") == "office-desk-gear":
                    return self
        except (requests.RequestException, ValueError, AttributeError):
            pass

        import uvicorn
        from server.main import app

        self.server = uvicorn.Server(uvicorn.Config(
            app, host=self.host, port=self.port, timeout_graceful_shutdown=5,
        ))
        self.thread = threading.Thread(target=self._run, name="office-desk-gear-api", daemon=True)
        self.thread.start()
        deadline = time.monotonic() + 15
        try:
            while not self.server.started:
                if not self.thread.is_alive():
                    raise RuntimeError("Сервер не запустился. Проверьте PostgreSQL, настройки .env и доступность порта.")
                if time.monotonic() >= deadline:
                    raise RuntimeError("Сервер не успел запуститься. Проверьте подключение к PostgreSQL.")
                time.sleep(0.05)
        except BaseException:
            self.stop()
            raise
        return self

    def _run(self):
        try:
            self.server.run()
        except SystemExit:
            pass

    def stop(self):
        if self.server is None:
            return
        self.server.should_exit = True
        self.thread.join(timeout=10)
        if self.thread.is_alive():
            self.server.force_exit = True
            self.thread.join(timeout=5)
        if self.thread.is_alive():
            raise RuntimeError("Не удалось завершить локальный сервер.")

    def __exit__(self, exc_type, exc_value, traceback):
        self.stop()
