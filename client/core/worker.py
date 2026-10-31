"""One daemon worker and a bounded queue; UI callbacks never run after shutdown."""
from __future__ import annotations

import queue
import threading


class BackgroundWorker:
    def __init__(self, schedule):
        self._queue = queue.Queue(maxsize=2)
        self._schedule = schedule
        self._closed = threading.Event()
        self._thread = threading.Thread(target=self._run, name="odg-network", daemon=True)
        self._thread.start()

    def submit(self, function, success, failure):
        if self._closed.is_set():
            return False
        try:
            self._queue.put_nowait((function, success, failure))
            return True
        except queue.Full:
            return False

    def _run(self):
        while not self._closed.is_set():
            try:
                function, success, failure = self._queue.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                value = function()
                callback = lambda _dt, value=value: success(value)
            except Exception as error:
                callback = lambda _dt, error=error: failure(error)
            finally:
                self._queue.task_done()
            if not self._closed.is_set():
                def deliver(_dt, callback=callback):
                    if not self._closed.is_set():
                        callback(_dt)
                self._schedule(deliver)

    def stop(self):
        self._closed.set()
        while True:
            try:
                self._queue.get_nowait()
                self._queue.task_done()
            except queue.Empty:
                break
