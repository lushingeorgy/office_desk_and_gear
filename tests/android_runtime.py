"""Black-box smoke of the built ARM64 APK on an Android emulator.

This drives the shipping UI with adb and OCR. It never injects Python, edits
private app state, decrypts credentials, or adds a test entry point to the APK.
An unavailable ARM translator or failed native import is an error, not a skip.
"""
from __future__ import annotations

import argparse
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import unicodedata

from PIL import Image, ImageOps
import pytesseract

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
PACKAGE = "org.officedeskgear.officedeskgear"
EMAIL = "employee@company.ru"
PASSWORD = "password123"  # Explicit development-only seed fixture.
API_URL = "http://10.0.2.2:8000/api/v1"


def adb(*arguments: str, binary=False, required=True):
    result = subprocess.run(["adb", *map(str, arguments)], capture_output=True, timeout=45)
    if required and result.returncode:
        raise RuntimeError(f"adb {arguments[0]} failed: {result.stderr.decode(errors='replace')[:600]}")
    return result.stdout if binary else result.stdout.decode(errors="replace").strip()


def normalized(value):
    return " ".join(re.findall(r"[a-zа-я0-9]+", unicodedata.normalize("NFKC", value).lower().replace("ё", "е")))


def primary_button_bounds(picture):
    """Locate the observed blue canvas button, without relying on its OCR."""
    width, height = picture.size
    mask = bytearray(width * height)
    for index, (red, green, blue) in enumerate(picture.convert("RGB").getdata()):
        mask[index] = (abs(red - 139) <= 45 and abs(green - 173) <= 45
                       and abs(blue - 255) <= 45 and blue - red >= 40)
    candidates = []
    for start in range(len(mask)):
        if not mask[start]:
            continue
        mask[start] = 0
        pending = [start]
        left = right = start % width
        top = bottom = start // width
        area = 0
        while pending:
            index = pending.pop()
            x, y = index % width, index // width
            left, right = min(left, x), max(right, x)
            top, bottom = min(top, y), max(bottom, y)
            area += 1
            neighbours = []
            if x: neighbours.append(index - 1)
            if x + 1 < width: neighbours.append(index + 1)
            if y: neighbours.append(index - width)
            if y + 1 < height: neighbours.append(index + width)
            for neighbour in neighbours:
                if mask[neighbour]:
                    mask[neighbour] = 0
                    pending.append(neighbour)
        box_width, box_height = right - left + 1, bottom - top + 1
        if box_width >= width * .6 and 24 <= box_height <= height * .15 and area >= box_width * box_height * .55:
            candidates.append((left, top, right, bottom))
    if len(candidates) != 1:
        raise AssertionError(f"Expected one observed primary canvas button, found {len(candidates)}")
    return candidates[0]


class NativeSmoke:
    def __init__(self, directory):
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)
        self.checks = []
        self.sequence = 0
        self.size = (480, 960)
        self.state_path = None
        self.last_pid = None
        self.picture = None

    def logcat(self, app_only=False):
        arguments = ["logcat", "-d", "-v", "threadtime"]
        if app_only and self.last_pid:
            arguments.append("--pid=" + self.last_pid)
        return adb(*arguments, required=False)

    def screenshot(self, label):
        self.sequence += 1
        raw = adb("exec-out", "screencap", "-p", binary=True)
        picture = Image.open(io.BytesIO(raw)).convert("RGB")
        self.picture = picture
        self.size = picture.size
        picture.save(self.directory / f"{self.sequence:03d}-{label}.png")
        gray = ImageOps.autocontrast(ImageOps.grayscale(picture))
        output = []
        evidence = []
        # The same screen contains light text on dark cards and dark text on
        # blue action buttons. A single inverted pass loses the latter.
        for polarity, variant in (("normal", gray), ("inverted", ImageOps.invert(gray))):
            enlarged = variant.resize((gray.width * 2, gray.height * 2), Image.Resampling.LANCZOS)
            data = pytesseract.image_to_data(enlarged, lang="rus+eng", config="--psm 11", output_type=pytesseract.Output.DICT)
            lines = {}
            for index, text in enumerate(data["text"]):
                if normalized(text):
                    key = (data["block_num"][index], data["par_num"][index], data["line_num"][index])
                    lines.setdefault(key, []).append((text, data["left"][index] // 2, data["top"][index] // 2, data["width"][index] // 2, data["height"][index] // 2))
            evidence.append(f"[{polarity}]")
            evidence.extend(" ".join(word[0] for word in words) for words in lines.values())
            for words in lines.values():
                # Tesseract may emit "IT-администратор" as a single token.
                # Its component words still refer to the same clickable label.
                for word, x, y, width, height in words:
                    output.extend((part, x + width // 2, y + height // 2) for part in normalized(word).split())
                for start in range(len(words)):
                    for end in range(start + 1, len(words) + 1):
                        part = words[start:end]
                        text = normalized(" ".join(item[0] for item in part))
                        x, y = min(item[1] for item in part), min(item[2] for item in part)
                        right, bottom = max(item[1] + item[3] for item in part), max(item[2] + item[4] for item in part)
                        output.append((text, (x + right) // 2, (y + bottom) // 2))
        # These are fixture-screen labels, never private state or credentials.
        (self.directory / f"{self.sequence:03d}-{label}.ocr.txt").write_text("\n".join(evidence), encoding="utf-8")
        return output

    def fatal_check(self):
        if not self.last_pid:
            raise AssertionError("Android launch did not produce an app process; inspect saved logcat")
        text = self.logcat(app_only=True)
        # logcat is restricted to the app PID. Android names the process
        # "SDLActivity" in native crash lines, so package-name matching here
        # would miss actual SIGILL/SIGSEGV failures and only time out on OCR.
        patterns = (r"FATAL EXCEPTION", r"Fatal signal \d+", r"(?:ImportError|ModuleNotFoundError):", r"dlopen failed:", r"ndk_translation.*Undefined instruction")
        if any(re.search(pattern, text, flags=re.I) for pattern in patterns):
            raise AssertionError("Native/Python startup failure; inspect saved logcat")

    def find(self, phrase, *, timeout=40, scroll=False):
        target = normalized(phrase)
        deadline = time.monotonic() + timeout
        swipes = 0
        while time.monotonic() < deadline:
            for text, x, y in self.screenshot("screen"):
                if text == target:
                    return x, y
            self.fatal_check()
            if scroll and swipes < 5:
                w, h = self.size
                adb("shell", "input", "swipe", str(w // 2), str(int(h * .79)), str(w // 2), str(int(h * .32)), "500")
                swipes += 1
            time.sleep(1)
        raise AssertionError(f"UI text did not appear: {phrase}")

    def click(self, phrase, *, scroll=False):
        x, y = self.find(phrase, scroll=scroll)
        adb("shell", "input", "tap", str(x), str(y))
        time.sleep(.7)

    def click_primary(self, page, expected_text):
        # Only use the observed canvas bounds after the expected screen and
        # current field value have been confirmed through the shipping UI.
        self.find(page)
        self.find(expected_text)
        self.screenshot("primary-button")
        bounds = primary_button_bounds(self.picture)
        (self.directory / f"{self.sequence:03d}-primary-button.bounds.json").write_text(json.dumps({"page": page, "bounds": bounds}), encoding="utf-8")
        left, top, right, bottom = bounds
        adb("shell", "input", "tap", str((left + right) // 2), str((top + bottom) // 2))
        time.sleep(.7)
        self.fatal_check()

    def fill(self, label, value):
        x, y = self.find(label)
        adb("shell", "input", "tap", str(x), str(y + 12))
        time.sleep(.5)
        adb("shell", "input", "keyevent", "123")  # MOVE_END
        adb("shell", "input", "keyevent", *(["67"] * 128))
        time.sleep(.5)
        # All input values here are fixed ASCII test fixtures, with no shell syntax.
        adb("shell", "input", "text", value)
        # SDL forwards Android key events to Kivy asynchronously. Let the last
        # characters arrive before Back dismisses the IME/field focus.
        time.sleep(.8)
        adb("shell", "input", "keyevent", "4")
        time.sleep(.7)

    def launch(self):
        self.last_pid = None
        adb("shell", "am", "force-stop", PACKAGE)
        adb("shell", "monkey", "-p", PACKAGE, "-c", "android.intent.category.LAUNCHER", "1")
        for _ in range(10):
            identifiers = adb("shell", "pidof", PACKAGE, required=False).split()
            if identifiers:
                self.last_pid = identifiers[0]
                break
            time.sleep(.2)

    def state(self, timeout=20):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not self.state_path:
                files = adb("shell", "run-as", PACKAGE, "find", ".", "-type", "f", "-name", "state.json", required=False).splitlines()
                paths = [path.strip() for path in files if path.strip().endswith("/private/state.json")]
                if len(paths) == 1:
                    self.state_path = paths[0]
            if self.state_path:
                raw = adb("exec-out", "run-as", PACKAGE, "cat", self.state_path, required=False)
                try:
                    return json.loads(raw)
                except ValueError:
                    pass
            time.sleep(.5)
        raise AssertionError("Private state.json was not found through the debug app's run-as")

    def wait_state(self, predicate, message, timeout=25):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            value = self.state(timeout=5)
            if predicate(value):
                return value
            self.fatal_check()
            time.sleep(.5)
        raise AssertionError(message)

    def check(self, name, condition):
        if not condition:
            raise AssertionError(name)
        self.checks.append(name)

    @staticmethod
    def server_sessions():
        # Independent server evidence; no access token is read or printed.
        from sqlalchemy import select, func
        import server.database.base  # Register all relationship targets before querying User.
        from server.database.session import SessionLocal
        from server.models.auth import AuthSession
        from server.models.users import User
        with SessionLocal() as database:
            return database.scalar(select(func.count()).select_from(AuthSession).join(User, AuthSession.user_id == User.id).where(User.email == EMAIL))

    def wait_sessions(self, count, timeout=15):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.server_sessions() == count:
                return True
            time.sleep(.3)
        return False

    def run(self, apk):
        adb("wait-for-device")
        properties = {
            "abis": "ro.product.cpu.abilist",
            "native_bridge": "ro.dalvik.vm.native.bridge",
            "android": "ro.build.version.release",
            "sdk": "ro.build.version.sdk",
            "fingerprint": "ro.build.fingerprint",
        }
        device = {name: adb("shell", "getprop", key) for name, key in properties.items()}
        (self.directory / "device.json").write_text(json.dumps(device, indent=2), encoding="utf-8")
        self.check("Emulator explicitly advertises ARM64 translation", "arm64-v8a" in device["abis"] and "x86_64" in device["abis"] and device["native_bridge"] not in ("", "0"))
        adb("install", "-r", "-t", str(apk.resolve()))
        adb("shell", "pm", "clear", PACKAGE)
        adb("shell", "wm", "size", "480x960")
        adb("shell", "wm", "density", "160")
        adb("logcat", "-c")
        self.launch()
        self.find("С возвращением", timeout=90)
        self.check("Actual ARM64 APK renders the KivyMD login screen", bool(adb("shell", "pidof", PACKAGE, required=False)))
        self.screenshot("cold-login")
        self.click("Создать аккаунт")
        self.click("Сотрудник")  # Real touch on the readonly job-title field.
        self.find("Офис-менеджер", timeout=20)
        self.screenshot("registration-job-menu")
        self.click("администратор")
        time.sleep(1)
        selected = self.screenshot("registration-job-selected")
        self.fatal_check()
        texts = {item[0] for item in selected}
        self.check("A real dropdown touch selects the IT job title and dismisses its menu", normalized("администратор") in texts and normalized("Офис-менеджер") not in texts)
        self.check("Native registration dropdown selection keeps the same app process alive", adb("shell", "pidof", PACKAGE, required=False).split() == [self.last_pid])
        self.click("У меня уже есть аккаунт", scroll=True)
        self.find("С возвращением")
        self.click("Настроить сервер")
        self.find("Подключение к серверу")
        # A populated Kivy field has a tiny floating hint. Locate its visible
        # current value instead; the saved API and actual server login are
        # independently asserted below.
        self.fill("http://127.0.0.1:8000/api/v1", API_URL)
        self.click_primary("Подключение к серверу", API_URL)
        self.find("С возвращением")
        self.check("Remote API address persisted by the shipping settings UI", self.state().get("api_url") == API_URL)
        self.fill("Рабочая почта", EMAIL)
        self.fill("Пароль", PASSWORD)
        self.click_primary("С возвращением", EMAIL)
        self.find("Мой профиль", timeout=60)
        state = self.wait_state(lambda item: bool(item.get("cache", {}).get("home")) and bool(item.get("credential")), "Login did not persist credential and the completed home response")
        credential = state.get("credential") or {}
        self.check("Android Keystore encrypted a persistent credential", credential.get("kind") == "android-keystore" and bool(credential.get("data")))
        self.check("Saved credential is not a plaintext JWT", not re.search(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", json.dumps(state)))
        self.check("Server recorded exactly one UI login", self.wait_sessions(1))
        self.check("Home response was cached on Android", bool(state.get("cache", {}).get("home")))
        user_id = state.get("user", {}).get("id")
        self.screenshot("signed-in")
        self.launch()
        # This fixture's home controls fit the fixed viewport. Never queue a
        # swipe on SDL's startup splash: Kivy can receive its release only
        # after the home buttons appear and navigate away during restoration.
        self.find("Мой профиль", timeout=90)
        restored = self.wait_state(lambda item: item.get("user", {}).get("id") == user_id, "Restart did not restore the account")
        self.check("Force-stop and process restart restore the same account", bool(user_id) and restored.get("user", {}).get("id") == user_id)
        self.check("Restart decrypts the same Keystore credential without a new login", restored.get("credential") == credential and self.server_sessions() == 1)
        self.screenshot("restored")
        self.click("Выйти из аккаунта")
        self.find("С возвращением")
        cleared = self.wait_state(lambda item: all(key not in item for key in ("credential", "user", "cache")), "Logout did not clear private account state")
        self.check("Explicit logout removes credential, user and private cache", all(key not in cleared for key in ("credential", "user", "cache")))
        self.check("Explicit logout revokes the server session", self.wait_sessions(0))
        self.launch()
        self.find("С возвращением", timeout=60)
        self.screenshot("logged-out-restart")
        self.fatal_check()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apk", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=ROOT / "tests" / "artifacts" / "android-runtime")
    args = parser.parse_args()
    smoke = NativeSmoke(args.output)
    outcome = {"passed": False, "kind": "Actual ARM64 APK on Google APIs emulator with ARM translation; not a physical-device test"}
    try:
        smoke.run(args.apk)
        outcome["passed"] = True
    except Exception as error:
        outcome["error"] = str(error)
        raise
    finally:
        outcome["checks"] = smoke.checks
        (smoke.directory / "result.json").write_text(json.dumps(outcome, indent=2, ensure_ascii=False), encoding="utf-8")
        (smoke.directory / "logcat.txt").write_text(smoke.logcat(), encoding="utf-8")
        print(json.dumps({"passed": outcome["passed"], "checks": len(smoke.checks)}, ensure_ascii=False))
        adb("shell", "am", "force-stop", PACKAGE, required=False)


if __name__ == "__main__":
    main()
