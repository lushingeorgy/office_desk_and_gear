"""Private, atomic local state; passwords are deliberately never persisted."""
from __future__ import annotations

import base64
import ctypes
import json
import os
import threading
import time
import datetime as dt
from pathlib import Path


class TokenVault:
    """Platform encryption for bearer credentials, independent from the data cache."""

    def __init__(self, platform="desktop"):
        self.platform = platform

    def _android(self, value, decrypt=False):
        from jnius import autoclass
        KeyStore = autoclass("java.security.KeyStore")
        Cipher = autoclass("javax.crypto.Cipher")
        store = KeyStore.getInstance("AndroidKeyStore")
        store.load(None)
        alias = "office_desk_gear.session.v1"
        if not store.containsAlias(alias):
            KeyGen = autoclass("javax.crypto.KeyGenerator")
            Builder = autoclass("android.security.keystore.KeyGenParameterSpec$Builder")
            Properties = autoclass("android.security.keystore.KeyProperties")
            generator = KeyGen.getInstance("AES", "AndroidKeyStore")
            spec = Builder(alias, Properties.PURPOSE_ENCRYPT | Properties.PURPOSE_DECRYPT)
            spec.setBlockModes(["GCM"])
            spec.setEncryptionPaddings(["NoPadding"])
            generator.init(spec.build())
            generator.generateKey()
        key = store.getKey(alias, None)
        cipher = Cipher.getInstance("AES/GCM/NoPadding")
        if decrypt:
            raw = base64.b64decode(value)
            GCM = autoclass("javax.crypto.spec.GCMParameterSpec")
            cipher.init(Cipher.DECRYPT_MODE, key, GCM(128, raw[:12]))
            return bytes(cipher.doFinal(raw[12:])).decode("utf-8")
        cipher.init(Cipher.ENCRYPT_MODE, key)
        encrypted = bytes(cipher.getIV()) + bytes(cipher.doFinal(value.encode("utf-8")))
        return base64.b64encode(encrypted).decode("ascii")

    @staticmethod
    def _windows(value, decrypt=False):
        from ctypes import wintypes

        class Blob(ctypes.Structure):
            _fields_ = [("length", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]

        raw = base64.b64decode(value) if decrypt else value.encode("utf-8")
        buffer = ctypes.create_string_buffer(raw)
        source = Blob(len(raw), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
        target = Blob()
        crypt = ctypes.windll.crypt32
        call = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
        if not call(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
            raise OSError("Credential encryption unavailable")
        try:
            result = ctypes.string_at(target.data, target.length)
            return result.decode("utf-8") if decrypt else base64.b64encode(result).decode("ascii")
        finally:
            ctypes.windll.kernel32.LocalFree(target.data)

    def seal(self, token):
        if self.platform == "android":
            return {"kind": "android-keystore", "data": self._android(token)}
        if os.name == "nt":
            return {"kind": "windows-dpapi", "data": self._windows(token)}
        # On Linux/iOS the file lives inside the user's private app directory (0600).
        return {"kind": "private-file", "data": token}

    def open(self, payload):
        kind, value = payload.get("kind"), payload.get("data", "")
        if kind == "android-keystore":
            return self._android(value, decrypt=True)
        if kind == "windows-dpapi":
            return self._windows(value, decrypt=True)
        if kind == "private-file" and self.platform != "android":
            return value
        raise ValueError("Unsupported credential vault")


class LocalState:
    MAX_CACHE_ITEMS = 80
    MAX_CACHE_BYTES = 6 * 1024 * 1024
    MAX_ENTRY_BYTES = 2 * 1024 * 1024
    CACHE_AGE_SECONDS = 7 * 86400

    def __init__(self, directory, platform="desktop", vault=None):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        try:
            self.directory.chmod(0o700)
        except OSError:
            pass
        self.path = self.directory / "state.json"
        self._lock = threading.RLock()
        self.vault = vault or TokenVault(platform)
        try:
            self.data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(self.data, dict) or self.path.stat().st_size > 8 * 1024 * 1024:
                raise ValueError()
        except (ValueError, OSError):
            self.data = {}

    def flush(self):
        with self._lock:
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(json.dumps(self.data, ensure_ascii=False), encoding="utf-8")
            try:
                temporary.chmod(0o600)
            except OSError:
                pass
            os.replace(temporary, self.path)

    @property
    def api_url(self):
        return self.data.get("api_url")

    def set_api_url(self, value):
        self.clear_account()
        self.data["api_url"] = value
        self.flush()

    def save_account(self, token, user, expires_at=None):
        with self._lock:
            old_id = (self.data.get("user") or {}).get("id")
            if old_id != user.get("id"):
                self.data["cache"] = {}
            self.data.update(credential=self.vault.seal(token), user=user, expires_at=expires_at)
            self.flush()

    def account(self):
        try:
            expires = self.data.get("expires_at")
            if expires:
                expiry = dt.datetime.fromisoformat(str(expires).replace("Z", "+00:00"))
                if expiry.tzinfo is None:
                    expiry = expiry.replace(tzinfo=dt.timezone.utc)
                if expiry <= dt.datetime.now(dt.timezone.utc):
                    self.clear_account()
                    return None, None
            token = self.vault.open(self.data.get("credential", {}))
            user = self.data.get("user")
            if token and isinstance(user, dict):
                return token, user
        except Exception:
            # A restored Android backup cannot decrypt another device's Keystore key.
            self.clear_account()
        return None, None

    def update_user(self, user):
        self.data["user"] = user
        self.flush()

    def clear_account(self):
        with self._lock:
            for key in ("credential", "user", "expires_at", "cache"):
                self.data.pop(key, None)
            media = self.directory / "media"
            if media.exists():
                for path in media.iterdir():
                    if path.is_file():
                        path.unlink(missing_ok=True)
            self.flush()

    def cache_get(self, key):
        entry = self.data.get("cache", {}).get(key)
        if not entry or time.time() - entry.get("at", 0) > self.CACHE_AGE_SECONDS:
            return None
        return entry

    def cache_put(self, key, payload):
        with self._lock:
            encoded_size = len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
            if encoded_size > self.MAX_ENTRY_BYTES:
                return False
            cache = self.data.setdefault("cache", {})
            cache[key] = {"payload": payload, "at": time.time()}
            sizes = {name: len(json.dumps(entry, ensure_ascii=False).encode("utf-8")) for name, entry in cache.items()}
            total = sum(sizes.values())
            for old in sorted(cache, key=lambda k: cache[k]["at"]):
                if len(cache) <= self.MAX_CACHE_ITEMS and total <= self.MAX_CACHE_BYTES:
                    break
                total -= sizes[old]
                del cache[old]
            self.flush()
            return True

    def media_path(self, filename):
        directory = self.directory / "media"
        directory.mkdir(exist_ok=True)
        name = Path(filename).name
        files = sorted((p for p in directory.iterdir() if p.is_file() and p.name != name), key=lambda p: p.stat().st_mtime)
        total = sum(p.stat().st_size for p in files)
        while files and (len(files) >= 40 or total > 64 * 1024 * 1024):
            oldest = files.pop(0)
            total -= oldest.stat().st_size
            oldest.unlink(missing_ok=True)
        return directory / name
