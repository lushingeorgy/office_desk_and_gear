"""Black-box smoke of the built ARM64 APK on an Android emulator.

This drives the separately packaged QA UI with adb and OCR. It never injects Python, edits
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
PACKAGE = "org.officedeskgear.officedeskgearqa"
EMAIL = "employee@company.ru"
PASSWORD = "password123"  # Explicit development-only seed fixture.
API_URL = "http://10.0.2.2:8000/api/v1"


def adb(*arguments: str, binary=False, required=True):
    result = subprocess.run(["adb", *map(str, arguments)], capture_output=True, timeout=45)
    if required and result.returncode:
        raise RuntimeError(f"adb {arguments[0]} failed: {result.stderr.decode(errors='replace')[:600]}")
    return result.stdout if binary else result.stdout.decode(errors="replace").strip()


def normalized(value):
    return " ".join(re.findall(r"[a-zа-я0-9]+", unicodedata.normalize("NFKC", value.replace("№", " ")).lower().replace("ё", "е")))


def primary_button_bounds(picture):
    """Locate a white (or legacy blue) primary canvas button from pixels."""
    width, height = picture.size
    mask = bytearray(width * height)
    for index, (red, green, blue) in enumerate(picture.convert("RGB").getdata()):
        neutral = min(red, green, blue) >= 220 and max(red, green, blue) - min(red, green, blue) <= 12
        legacy = (abs(red - 139) <= 45 and abs(green - 173) <= 45
                  and abs(blue - 255) <= 45 and blue - red >= 40)
        mask[index] = neutral or legacy
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


def header_profile_point(picture):
    """Locate the observed neutral/legacy user icon in the app header."""
    import cv2
    import numpy as np
    frame = np.asarray(picture.convert("RGB"))
    height, width = frame.shape[:2]
    mask = cv2.inRange(frame, np.array([105, 140, 215]), np.array([175, 210, 255]))
    mask |= cv2.inRange(frame, np.array([220, 220, 220]), np.array([255, 255, 255]))
    mask[:, :int(width * .80)] = 0
    mask[:int(height * .035)] = 0
    mask[int(height * .16):] = 0
    count, _, stats, centers = cv2.connectedComponentsWithStats(mask)
    candidates = [(centers[i][1], centers[i][0]) for i in range(1, count)
                  if 2 <= stats[i][2] <= width * .10 and 2 <= stats[i][3] <= width * .10
                  and stats[i][4] >= 4]
    if not candidates:
        raise AssertionError("Top-right user icon was not visible in the app header")
    y, x = min(candidates)
    return round(x), round(y)


def login_field_bounds(picture, observed_texts):
    """Locate both outlined fields on an independently recognized login form.

    The heading and action must be readable before field order can stand in
    for a faint placeholder. Reject incomplete or ambiguous geometry.
    """
    import cv2
    import numpy as np

    texts = {normalized(text) for text in observed_texts}
    if not {normalized("С возвращением"), normalized("Войти")}.issubset(texts):
        raise AssertionError("Login heading and primary action were not both recognized")
    frame = np.asarray(picture.convert("RGB"))
    height, width = frame.shape[:2]
    # Thin outlines are blended with the background at some Android densities.
    neutral = cv2.inRange(frame, np.array([40, 40, 40]), np.array([90, 90, 90]))
    bright = cv2.inRange(frame, np.array([150, 150, 150]), np.array([255, 255, 255]))
    spread = frame.max(axis=2).astype("int16") - frame.min(axis=2)
    neutral[spread > 5] = 0
    bright[spread > 12] = 0
    mask = neutral | bright
    mask |= cv2.inRange(frame, np.array([35, 43, 58]), np.array([65, 80, 102]))
    mask |= cv2.inRange(frame, np.array([80, 100, 150]), np.array([150, 190, 255]))
    contours = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[0]
    boxes = []
    edge = max(2, round(width * .006))
    for contour in contours:
        x, y, box_width, box_height = cv2.boundingRect(contour)
        if not (box_width >= width * .6 and 32 <= box_height <= height * .16
                and x >= width * .02 and x + box_width <= width * .98):
            continue
        region = mask[y:y + box_height, x:x + box_width]
        # A filled primary button or a wide line of text is not an input.
        if cv2.countNonZero(region) >= box_width * box_height * .25:
            continue
        if not (np.any(region[:, :edge], axis=1).mean() >= .5
                and np.any(region[:, -edge:], axis=1).mean() >= .5):
            continue
        boxes.append((x, y, x + box_width, y + box_height))
    boxes.sort(key=lambda box: box[1])
    if len(boxes) != 2:
        raise AssertionError(f"Expected two observed login fields, found {len(boxes)}")
    first, second = boxes
    if (first[3] >= second[1] or abs(first[0] - second[0]) > width * .02
            or abs(first[2] - second[2]) > width * .02
            or abs((first[3] - first[1]) - (second[3] - second[1])) > height * .025):
        raise AssertionError("Observed login fields do not form an aligned pair")
    return boxes


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
        # light action buttons. A single inverted pass loses the latter.
        for polarity, variant in (("normal", gray), ("inverted", ImageOps.invert(gray))):
            enlarged = variant.resize((gray.width * 2, gray.height * 2), Image.Resampling.LANCZOS)
            data = pytesseract.image_to_data(enlarged, lang="rus+eng", config="--psm 11", output_type=pytesseract.Output.DICT)
            lines = {}
            for index, text in enumerate(data["text"]):
                if normalized(text):
                    key = (data["block_num"][index], data["par_num"][index], data["line_num"][index])
                    lines.setdefault(key, []).append((text, data["left"][index] // 2, data["top"][index] // 2, data["width"][index] // 2, data["height"][index] // 2))
            # Sparse OCR sometimes puts the ID and equipment name in different
            # blocks even though they share a visible baseline.
            geometric=[]
            for word in sorted((word for row in lines.values() for word in row),key=lambda word:word[2]+word[4]/2):
                center=word[2]+word[4]/2
                row=next((row for row in geometric if abs((row[0][2]+row[0][4]/2)-center)<=7),None)
                if row is None: geometric.append([word])
                else: row.append(word)
            for index,row in enumerate(geometric): lines[('geometric',index,0)]=sorted(row,key=lambda word:word[1])
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
        # Canvas buttons and floating Material hints need their own segmentation;
        # full-screen sparse OCR can discard dark text inside the light action.
        import cv2
        import numpy as np
        frame=np.asarray(picture)
        blue=cv2.inRange(frame,np.array([94,128,210]),np.array([184,218,255]))
        blue[(frame[:,:,2].astype('int16')-frame[:,:,0])<40]=0
        blue |= cv2.inRange(frame,np.array([220,220,220]),np.array([255,255,255]))
        count,_,stats,_=cv2.connectedComponentsWithStats(blue)
        regions=[]
        for x,y,width,height,area in stats[1:]:
            if width>=picture.width*.6 and 24<=height<=picture.height*.15 and area>=width*height*.55:
                regions.append(('button',(x+8,y+4,x+width-8,y+height-4)))
        border=cv2.inRange(frame,np.array([35,43,58]),np.array([43,51,66]))
        border |= cv2.inRange(frame,np.array([68,68,68]),np.array([76,76,76]))
        contours=cv2.findContours(border,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)[0]
        for contour in contours:
            x,y,width,height=cv2.boundingRect(contour)
            if 16<=x<=32 and width<=4 and 30<=height<=110:
                regions.append(('hint',(x+8,max(0,y-21),picture.width-x-8,y+2)))
        for kind,bounds in regions:
            bounds=tuple(map(int,bounds))
            crop=ImageOps.grayscale(picture.crop(bounds))
            if kind=='hint':
                crop=crop.point(lambda value: 0 if value>70 else 255)
                if sum(1 for value in crop.getdata() if value==0)<8: continue
            else: crop=ImageOps.autocontrast(crop)
            crop=crop.resize((crop.width*3,crop.height*3),Image.Resampling.LANCZOS)
            region_text=pytesseract.image_to_string(crop,lang='rus+eng',config='--psm 7').strip()
            evidence.append('['+kind+'] '+region_text)
            parts=normalized(region_text).split()
            x1,y1,x2,y2=bounds
            for start in range(len(parts)):
                for end in range(start+1,len(parts)+1):
                    output.append((' '.join(parts[start:end]),(x1+x2)//2,(y1+y2)//2))
        # These are fixture-screen labels, never private state or credentials.
        (self.directory / f"{self.sequence:03d}-{label}.ocr.txt").write_text("\n".join(evidence), encoding="utf-8")
        # Android's own Pixel Launcher can ANR during a fresh emulator boot.
        # Dismiss only this observed system-app dialog, never an APK ANR.
        if any(text == normalized("Pixel Launcher isn't responding") for text,_,_ in output):
            close = next(((x,y) for text,x,y in output if text in {"clos","close"}),None)
            if close:
                (self.directory / f"{self.sequence:03d}-external-launcher-dialog.json").write_text(json.dumps({"app":"Pixel Launcher","action":"Close app","point":close}))
                adb("shell","input","tap",*close)
                time.sleep(1)
                return []
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
            raise AssertionError("Native/Python failure; inspect saved logcat")

    def find(self, phrase, *, timeout=40, scroll=False, field=False):
        target = normalized(phrase)
        deadline = time.monotonic() + timeout
        swipes = 0
        while time.monotonic() < deadline:
            words = self.screenshot("screen")
            texts = {text for text, _, _ in words}
            login_field = (field and target in {normalized("Рабочая почта"), normalized("Пароль")}
                           and normalized("С возвращением") in texts)
            # A word inside "Забыли пароль?" is a button, not the password
            # field. On the recognized login form, use only its observed
            # outlined input boxes and reject ambiguous geometry.
            if login_field:
                try:
                    boxes = login_field_bounds(self.picture, (text for text, _, _ in words))
                except AssertionError:
                    pass  # Keep waiting; an unrecognized form is not a match.
                else:
                    index = 0 if target == normalized("Рабочая почта") else 1
                    left, top, right, bottom = boxes[index]
                    (self.directory / f"{self.sequence:03d}-login-field.bounds.json").write_text(
                        json.dumps({"form": "login", "field": phrase, "bounds": boxes[index],
                                    "method": "recognized form and observed outlines"}, ensure_ascii=False), encoding="utf-8")
                    return (left + right) // 2, (top + bottom) // 2
            if not login_field:
                for text, x, y in words:
                    if text == target:
                        return x, (y + 12 if field else y)
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

    def open_profile(self):
        self.find("Быстрые действия", timeout=60)
        self.screenshot("header-profile-icon")
        point = header_profile_point(self.picture)
        (self.directory / f"{self.sequence:03d}-profile-icon.bounds.json").write_text(json.dumps({"point": point}))
        adb("shell", "input", "tap", *point)
        NativeSmoke.find(self, "Мой профиль", timeout=40)

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

    def fill(self, label, value, *, clear=True):
        x, y = self.find(label, field=True)
        adb("shell", "input", "tap", str(x), str(y))
        time.sleep(1)
        self.screenshot("focused-field")
        if clear:
            adb("shell", "input", "keyevent", "123")  # MOVE_END
            adb("shell", "input", "keyevent", *(["67"] * 128))
            time.sleep(.8)
        # All input values here are fixed ASCII test fixtures, with no shell syntax.
        # The cold login form is observed empty; do not queue unnecessary delete
        # events before typing into those two fresh fields.
        adb("shell", "input", "text", value)
        time.sleep(1)
        self.screenshot("typed-field")
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
        self.fill("Должность", "QA specialist")
        self.screenshot("registration-job-title")
        self.fatal_check()
        self.check("Native registration accepts a job title without administrative role selection",
                   bool(adb("shell", "pidof", PACKAGE, required=False)))
        self.click("У меня уже есть аккаунт", scroll=True)
        self.find("С возвращением")
        self.check("QA build binds its fixed local API without manual settings", self.state().get("api_url") == API_URL)
        self.fill("Рабочая почта", EMAIL, clear=False)
        self.fill("Пароль", PASSWORD, clear=False)
        self.click_primary("С возвращением", EMAIL)
        self.find("Быстрые действия", timeout=60)
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
        self.find("Быстрые действия", timeout=90)
        restored = self.wait_state(lambda item: item.get("user", {}).get("id") == user_id, "Restart did not restore the account")
        self.check("Force-stop and process restart restore the same account", bool(user_id) and restored.get("user", {}).get("id") == user_id)
        self.check("Restart decrypts the same Keystore credential without a new login", restored.get("credential") == credential and self.server_sessions() == 1)
        self.screenshot("restored")
        self.open_profile()
        self.click("Выйти из аккаунта", scroll=True)
        self.find("С возвращением")
        cleared = self.wait_state(lambda item: all(key not in item for key in ("credential", "user", "cache")), "Logout did not clear private account state")
        self.check("Explicit logout removes credential, user and private cache", all(key not in cleared for key in ("credential", "user", "cache")))
        self.check("Explicit logout revokes the server session", self.wait_sessions(0))
        self.launch()
        self.find("С возвращением", timeout=60)
        self.screenshot("logged-out-restart")
        self.fatal_check()


def main():
    global PACKAGE
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apk", type=Path, required=True)
    parser.add_argument("--package", default=PACKAGE)
    parser.add_argument("--output", type=Path, default=ROOT / "tests" / "artifacts" / "android-runtime")
    args = parser.parse_args()
    PACKAGE = args.package
    smoke = NativeSmoke(args.output)
    outcome = {"passed": False, "kind": "QA ARM64 APK with fixed local fixture API on Google APIs emulator; not the shipping endpoint or a physical-device test", "package": PACKAGE, "api_url": API_URL}
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
