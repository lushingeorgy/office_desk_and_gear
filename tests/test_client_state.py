"""Client lifecycle, persistence and isolation checks without opening a window."""
import datetime as dt
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
TEMP = Path(os.getenv("ODG_TEST_TEMP", str(ROOT / "tests" / "artifacts" / "client-unit")))
TEMP.mkdir(parents=True, exist_ok=True)
tempfile.tempdir = str(TEMP)

from client.core.storage import LocalState, TokenVault
from client.core.device import DeviceTools
from client.core.worker import BackgroundWorker
from client.services.api_client import ApiClient, ApiError
from client.viewmodels.session import SessionViewModel


class Response:
    status_code = 200
    headers = {}
    closed = False

    def json(self):
        return {"id": 5}

    def close(self):
        self.closed = True


class ClientStateTests(unittest.TestCase):
    def test_camera_rotation_matches_sensor_and_display_instead_of_fixed_quarter_turn(self):
        for sensor, display, expected in ((90, 0, 90), (270, 0, 270), (90, 1, 0),
                                           (0, 1, 270), (90, 2, 270), (90, 3, 180)):
            with self.subTest(sensor=sensor, display=display):
                info = SimpleNamespace(orientation=sensor, facing=0)
                classes = {
                    "android.hardware.Camera": SimpleNamespace(getCameraInfo=lambda _index, _info: None),
                    "android.hardware.Camera$CameraInfo": lambda: info,
                    "org.kivy.android.PythonActivity": SimpleNamespace(mActivity=SimpleNamespace(
                        getWindowManager=lambda: SimpleNamespace(getDefaultDisplay=lambda: SimpleNamespace(
                            getRotation=lambda: display)))),
                }
                with patch.dict(sys.modules, {"jnius": SimpleNamespace(autoclass=classes.__getitem__)}):
                    self.assertEqual(expected, DeviceTools("android", None, None).camera_preview_rotation(0))
        self.assertEqual(0, DeviceTools("win", None, None).camera_preview_rotation(0))

    def test_android_keystore_passes_java_varargs_as_strings(self):
        testcase = self

        class VerifiedKeySpec(Exception):
            pass

        class Store:
            def load(self, _parameters):
                pass

            def containsAlias(self, _alias):
                return False

        class Builder:
            def __init__(self, _alias, _purposes):
                pass

            def setBlockModes(self, *values):
                testcase.assertEqual(("GCM",), values)

            def setEncryptionPaddings(self, *values):
                testcase.assertEqual(("NoPadding",), values)

            def build(self):
                return self

        class Generator:
            def init(self, spec):
                testcase.assertIsInstance(spec, Builder)
                raise VerifiedKeySpec()

        # PyJNIus wraps positional varargs into Java arrays. A Python list here
        # would create a nested array and fail before Android could save a key.
        classes = {
            "java.security.KeyStore": SimpleNamespace(getInstance=lambda _kind: Store()),
            "javax.crypto.Cipher": object(),
            "javax.crypto.KeyGenerator": SimpleNamespace(getInstance=lambda *_args: Generator()),
            "android.security.keystore.KeyGenParameterSpec$Builder": Builder,
            "android.security.keystore.KeyProperties": SimpleNamespace(PURPOSE_ENCRYPT=1, PURPOSE_DECRYPT=2),
        }
        with patch.dict(sys.modules, {"jnius": SimpleNamespace(autoclass=classes.__getitem__)}):
            with self.assertRaises(VerifiedKeySpec):
                TokenVault("android").seal("test-credential")

    def test_account_and_cache_survive_new_instance(self):
        with tempfile.TemporaryDirectory() as folder:
            state = LocalState(folder)
            state.bind_api_url("https://138.16.179.111/api/v1")
            user = {"id": 7, "email": "test@example.org"}
            state.save_account("test-bearer", user, (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=1)).isoformat())
            state.cache_put("bookings", [{"id": 3}])
            restored = LocalState(folder)
            restored.bind_api_url("https://138.16.179.111/api/v1/")
            self.assertEqual(restored.account(), ("test-bearer", user))
            self.assertEqual(restored.cache_get("bookings")["payload"], [{"id": 3}])
            if sys.platform == "win32":
                self.assertNotIn("test-bearer", state.path.read_text(encoding="utf-8"))

    def test_expired_account_removes_credential_and_private_cache(self):
        with tempfile.TemporaryDirectory() as folder:
            state = LocalState(folder)
            state.save_account("expired", {"id": 1}, "2020-01-01T00:00:00Z")
            state.cache_put("equipment", [1])
            self.assertEqual(state.account(), (None, None))
            self.assertIsNone(state.cache_get("equipment"))
            self.assertNotIn("credential", state.data)

    def test_logout_and_server_change_erase_private_data(self):
        with tempfile.TemporaryDirectory() as folder:
            state = LocalState(folder)
            # Simulate an installation whose manually saved API is now obsolete.
            state.data["api_url"] = "http://127.0.0.1:8094/api/v1"
            state.save_account("old", {"id": 1})
            state.cache_put("profile", {"private": True})
            state.media_path("photo.png").write_bytes(b"photo")
            restored = LocalState(folder)
            restored.bind_api_url("https://138.16.179.111/api/v1")
            state = LocalState(folder)
            self.assertEqual(state.api_url, "https://138.16.179.111/api/v1")
            self.assertEqual(state.account(), (None, None))
            self.assertNotIn("cache", state.data)
            self.assertEqual(list((Path(folder) / "media").iterdir()), [])

    def test_legacy_unbound_credential_is_not_forwarded_to_new_api(self):
        with tempfile.TemporaryDirectory() as folder:
            state = LocalState(folder)
            state.save_account("unknown-server-bearer", {"id": 7})
            state.cache_put("profile", {"private": True})
            state.bind_api_url("https://138.16.179.111/api/v1")
            self.assertEqual(state.account(), (None, None))
            self.assertIsNone(state.cache_get("profile"))

    def test_cache_is_bounded_and_account_switch_invalidates(self):
        with tempfile.TemporaryDirectory() as folder:
            state = LocalState(folder)
            state.save_account("one", {"id": 1})
            for number in range(100):
                state.cache_put(str(number), [number])
            self.assertEqual(len(state.data["cache"]), 80)
            state.save_account("two", {"id": 2})
            self.assertEqual(state.data["cache"], {})

    def test_large_cache_does_not_discard_persistent_login(self):
        with tempfile.TemporaryDirectory() as folder:
            state = LocalState(folder)
            state.save_account("preserved-bearer", {"id": 9})
            self.assertFalse(state.cache_put("too-large", "x" * (3 * 1024 * 1024)))
            for number in range(8):
                state.cache_put(str(number), "x" * (1024 * 1024))
            self.assertLess(state.path.stat().st_size, 7 * 1024 * 1024)
            restored = LocalState(folder)
            self.assertEqual(restored.account()[0], "preserved-bearer")
            self.assertLessEqual(len(restored.data["cache"]), 6)

    def test_response_is_closed_on_success_and_error(self):
        response = Response()
        api = ApiClient(transport=lambda *_a, **_k: response)
        self.assertEqual(api.get_me(), {"id": 5})
        self.assertTrue(response.closed)
        response.status_code, response.closed = 403, False
        with self.assertRaises(ApiError):
            api.get_me()
        self.assertTrue(response.closed)
        api.close()
        self.assertIsNone(api._session)

    def test_assignment_transport_distinguishes_removal_from_unchanged_fields(self):
        calls = []

        def transport(_method, _url, **kwargs):
            calls.append(kwargs["json"])
            return Response()

        api = ApiClient(transport=transport)
        try:
            api.admin_assign_equipment(5, assigned_user_id=12, desk_id=18)
            api.admin_assign_equipment(5, assigned_user_id=None)
            api.admin_assign_equipment(5, assigned_user_id=None, desk_id=None)
            api.admin_assign_equipment(5)
        finally:
            api.close()
        self.assertEqual([
            {"assigned_user_id": 12, "desk_id": 18},
            {"assigned_user_id": None},
            {"assigned_user_id": None, "desk_id": None},
            {},
        ], calls)

    def test_assignment_desk_choices_load_beyond_two_hundred_records(self):
        desks = [{"id": index} for index in range(420)]
        offsets, responses = [], []

        def transport(_method, _url, **kwargs):
            query = kwargs["params"]
            offsets.append(query["offset"])
            page = desks[query["offset"]:query["offset"] + query["limit"]]
            response = Response()
            response.json = lambda: page
            responses.append(response)
            return response

        api = ApiClient(transport=transport)
        try:
            self.assertEqual(desks, api.get_all_desks())
            self.assertEqual([0, 200, 400], offsets)
            self.assertTrue(all(response.closed for response in responses))
        finally:
            api.close()

    def test_pause_defers_pool_close_until_active_response_finishes(self):
        entered, release = threading.Event(), threading.Event()
        response = Response()

        def transport(*_a, **_k):
            entered.set()
            release.wait(2)
            return response
        api = ApiClient(transport=transport)
        thread = threading.Thread(target=api.get_me)
        thread.start()
        self.assertTrue(entered.wait(2))
        api.close()
        self.assertIsNotNone(api._session)
        release.set()
        thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertIsNone(api._session)
        self.assertTrue(response.closed)

    def test_late_response_from_old_account_does_not_pollute_cache(self):
        class Deferred:
            def submit(self, function, success, failure):
                self.success = success
                self.value = function()
                return True
        with tempfile.TemporaryDirectory() as folder:
            state = LocalState(folder)
            api = ApiClient()
            deferred = Deferred()
            context = ["user1"]
            model = SessionViewModel(api, state, deferred, lambda: tuple(context))
            calls = []
            model.execute(lambda: ["user1 private"], lambda value, stale: calls.append(stale), self.fail, cache_key="private")
            context[0] = "user2"
            deferred.success(deferred.value)
            self.assertEqual(calls, [True])
            self.assertIsNone(state.cache_get("private"))

    def test_late_failure_from_old_account_does_not_log_out_new_account(self):
        class Deferred:
            def submit(self, function, success, failure):
                self.failure = failure
                return True
        with tempfile.TemporaryDirectory() as folder:
            state = LocalState(folder)
            deferred, context, stale = Deferred(), ["old-user"], []
            model = SessionViewModel(ApiClient(), state, deferred, lambda: tuple(context))
            model.execute(lambda: None, lambda _value, old: stale.append(old), self.fail, cache_key="private")
            context[0] = "new-user"
            deferred.failure(ApiError("Session expired", 401))
            self.assertEqual(stale, [True])

    def test_photo_size_is_checked_before_upload(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "large.png"
            path.write_bytes(b"0" * 5_000_001)
            api = ApiClient(transport=lambda *_a, **_k: self.fail("Oversize image sent to server"))
            with self.assertRaises(ApiError):
                api.upload_photo(path)

    def test_private_photo_keeps_shared_proxy_prefix_and_never_leaks_bearer(self):
        calls, closed = [], []

        def transport(method, url, **kwargs):
            calls.append((method, url, kwargs))
            return SimpleNamespace(status_code=200, content=b"photo", close=lambda: closed.append(True))

        base = "https://office.example.org/office-desk-gear/api/v1"
        api = ApiClient(base, transport=transport)
        api.set_token("private-test-bearer")
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "photo.jpg"
            for source in ("/api/v1/uploads/own-id", "uploads/own-id",
                           base + "/uploads/own-id", "/office-desk-gear/api/v1/uploads/own-id"):
                api.download_private(source, output)
                self.assertEqual(output.read_bytes(), b"photo")
                self.assertEqual(calls[-1][1], base + "/uploads/own-id")
                self.assertFalse(calls[-1][2]["allow_redirects"])
                self.assertEqual(calls[-1][2]["headers"]["Authorization"], "Bearer private-test-bearer")
            self.assertEqual(len(closed), 4)
            for source in ("https://other.example.org/api/v1/uploads/id",
                           "https://office.example.org/mascarad/api/v1/uploads/id",
                           "http://office.example.org/office-desk-gear/api/v1/uploads/id",
                           "/api/v1/uploads/../../mascarad", "/mascarad/api/v1/uploads/id",
                           "/api/v1/uploads/%2e%2e/%2e%2e/mascarad", "/api/v1/uploads/id%2fother",
                           "/api/v1/uploads/id\\other",
                           base + "/uploads/id?redirect=elsewhere"):
                with self.subTest(source=source), self.assertRaises(ApiError):
                    api.download_private(source, output)
            self.assertEqual(len(calls), 4, "Rejected URLs must not issue a request")
        api.close()

    def test_stopped_worker_discards_callbacks_and_pending_jobs(self):
        callbacks, release, entered = [], threading.Event(), threading.Event()
        worker = BackgroundWorker(callbacks.append)

        def request():
            entered.set()
            release.wait(2)
            return "done"
        worker.submit(request, self.fail, self.fail)
        self.assertTrue(entered.wait(2))
        worker.stop()
        release.set()
        worker._thread.join(2)
        self.assertEqual(callbacks, [])
        self.assertFalse(worker.submit(lambda: None, self.fail, self.fail))


if __name__ == "__main__":
    unittest.main(verbosity=2)
