"""Opt-in, non-destructive UI checks of the shipping app on one USB phone.

Only screenshots, visible text, ordinary taps and text input are used. Account
storage is never read. The runner never saves bookings, changes profiles or
logs out. Windows OCR is built in; no external OCR installation is needed.
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import unicodedata

import cv2
import numpy as np
from PIL import Image

from android_phone_navigation import PACKAGE, ROOT, adb, require_foreground

OUTPUT = ROOT / "tests" / "artifacts" / "theme-phone"


def normalized(value):
    return " ".join(re.findall(r"[a-zа-я0-9]+", unicodedata.normalize("NFKC", value).lower().replace("ё", "е")))


def windows_ocr(path):
    """Return visible OCR lines with geometry through the Windows WinRT API."""
    script = r'''
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Storage.StorageFile, Windows.Storage, ContentType=WindowsRuntime]
$null = [Windows.Storage.Streams.IRandomAccessStream, Windows.Storage.Streams, ContentType=WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics.Imaging, ContentType=WindowsRuntime]
$null = [Windows.Graphics.Imaging.SoftwareBitmap, Windows.Graphics.Imaging, ContentType=WindowsRuntime]
$null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType=WindowsRuntime]
$null = [Windows.Media.Ocr.OcrResult, Windows.Foundation, ContentType=WindowsRuntime]
$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.IsGenericMethod -and $_.GetParameters().Count -eq 1 -and
    $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
})[0]
function Await($operation, $type) {
    $task = $asTask.MakeGenericMethod($type).Invoke($null, @($operation))
    try { $null = $task.Wait(-1) }
    catch { throw $_.Exception.GetBaseException().Message }
    return $task.Result
}
$fileStream = [System.IO.File]::OpenRead($env:ODG_OCR_IMAGE)
$stream = [System.IO.WindowsRuntimeStreamExtensions]::AsRandomAccessStream($fileStream)
$decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
$bitmap = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
if ($null -eq $engine) { throw 'Windows OCR language is unavailable' }
$result = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
$lines = @($result.Lines | ForEach-Object {
    $words = @($_.Words | ForEach-Object {
        @{ text=$_.Text; x=$_.BoundingRect.X; y=$_.BoundingRect.Y; width=$_.BoundingRect.Width; height=$_.BoundingRect.Height }
    })
    @{ text=$_.Text; words=$words }
})
@{ language=$engine.RecognizerLanguage.LanguageTag; lines=$lines } | ConvertTo-Json -Depth 8 -Compress
$stream.Dispose()
'''
    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    executable = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    result = subprocess.run([str(executable), "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                            env=dict(os.environ, ODG_OCR_IMAGE=str(path.resolve())), capture_output=True, timeout=45)
    if result.returncode:
        raise RuntimeError("Windows OCR failed: " + result.stderr.decode("utf-8", errors="replace")[-1500:])
    return json.loads(result.stdout.decode("utf-8-sig"))


def palette_stats(picture):
    frame = np.asarray(picture.convert("RGB")).astype(np.int16)
    frame = frame[int(frame.shape[0] * .04):int(frame.shape[0] * .95)]
    spread = frame.max(axis=2) - frame.min(axis=2)
    green = (frame[:, :, 1] - frame[:, :, 0] >= 25) & (frame[:, :, 1] - frame[:, :, 2] >= 15)
    red = (frame[:, :, 0] - frame[:, :, 1] >= 25) & (frame[:, :, 0] - frame[:, :, 2] >= 15)
    return {"neutral_fraction": round(float((spread <= 10).mean()), 4),
            "green_pixels": int(green.sum()), "red_pixels": int(red.sum())}


def outline_boxes(picture):
    frame = np.asarray(picture.convert("RGB"))
    neutral = cv2.inRange(frame, np.array([48, 48, 48]), np.array([90, 90, 90]))
    neutral |= cv2.inRange(frame, np.array([220, 220, 220]), np.array([255, 255, 255]))
    error = cv2.inRange(frame, np.array([90, 40, 35]), np.array([255, 165, 165]))
    difference = frame[:, :, 0].astype(np.int16) - frame[:, :, 1].astype(np.int16)
    error[difference < 25] = 0
    neutral |= error
    contours = cv2.findContours(neutral, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[0]
    width, height = picture.size
    boxes = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        if width * .24 <= w <= width * .94 and height * .02 <= h <= height * .12:
            if cv2.countNonZero(neutral[y:y + h, x:x + w]) < w * h * .30:
                boxes.append((x, y, x + w, y + h))
    return sorted(boxes, key=lambda item: (item[1], item[0]))


class PhoneThemeCheck:
    def __init__(self, directory=OUTPUT):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.results = []
        self.sequence = 0
        self.lines = []
        self.picture = None
        self.physical_size = None

    def capture(self, name="screen"):
        if re.fullmatch(r"[a-z0-9-]+", name) is None:
            raise ValueError("Evidence name must use lowercase letters, digits and hyphens")
        require_foreground()
        self.sequence += 1
        picture = Image.open(io.BytesIO(adb("exec-out", "screencap", "-p"))).convert("RGB")
        self.physical_size = picture.size
        full_path = self.directory / f"{self.sequence:03d}-{name}-full.png"
        picture.save(full_path)
        picture.thumbnail((720, 2000), Image.Resampling.LANCZOS)
        self.picture = picture
        preview_path = self.directory / f"{self.sequence:03d}-{name}.png"
        picture.save(preview_path)
        result = windows_ocr(preview_path)
        self.lines = result["lines"]
        result.update(palette=palette_stats(picture), outlines=outline_boxes(picture),
                      preview=str(preview_path), original=str(full_path))
        (self.directory / f"{self.sequence:03d}-{name}.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        return result

    def tap_point(self, x, y):
        require_foreground()
        if self.picture is None:
            raise AssertionError("Capture a visible screen before sending a tap")
        physical_x = round(x * self.physical_size[0] / self.picture.width)
        physical_y = round(y * self.physical_size[1] / self.picture.height)
        adb("shell", "input", "tap", physical_x, physical_y)
        time.sleep(.6)

    def click(self, phrase):
        allowed = {"Столы", "Главная", "Список", "План этажа", "Найти места", "Фильтры"}
        if phrase not in allowed:
            raise ValueError("This helper only taps navigation and search actions")
        self.capture("before-tap")
        target = normalized(phrase)
        for line in self.lines:
            if normalized(line["text"]) == target:
                words = line["words"]
                left, top = min(w["x"] for w in words), min(w["y"] for w in words)
                right = max(w["x"] + w["width"] for w in words)
                bottom = max(w["y"] + w["height"] for w in words)
                self.tap_point((left + right) / 2, (top + bottom) / 2)
                return
        raise AssertionError("Visible action was not found: " + phrase)

    def time_boxes(self):
        self.capture("time-fields")
        width = self.picture.width
        candidates = [box for box in outline_boxes(self.picture) if box[2] - box[0] <= width * .49]
        for left in candidates:
            for right in candidates:
                if left[2] < right[0] and abs(left[1] - right[1]) < 5 and abs(left[3] - right[3]) < 5:
                    return left, right
        raise AssertionError("The two visible time fields were not found")

    def fill_time(self, side, value):
        if side not in {"start", "end"} or re.fullmatch(r"[0-9]{2}:[0-9]{2}", value) is None:
            raise ValueError("Use start/end and HH:MM")
        box = self.time_boxes()[0 if side == "start" else 1]
        left, top, right, bottom = box
        self.tap_point((left + right) / 2, (top + bottom) / 2)
        require_foreground()
        adb("shell", "input", "keyevent", "123")
        adb("shell", "input", "keyevent", *(["67"] * 16))
        adb("shell", "input", "text", value)
        adb("shell", "input", "keyevent", "4")
        time.sleep(.6)

    def has_text(self, phrase):
        target = normalized(phrase)
        text = " ".join(normalized(line["text"]) for line in self.lines)
        return target in text

    def step(self, name, callback, on_result=None):
        start = time.monotonic()
        try:
            detail = callback()
            entry = {"name": name, "status": "PASS", "seconds": round(time.monotonic() - start, 2), "detail": detail}
        except Exception as error:
            entry = {"name": name, "status": "FAIL", "seconds": round(time.monotonic() - start, 2), "error": str(error)}
            self.results.append(entry)
            self.save(False)
            if on_result:
                on_result(entry)
            raise
        self.results.append(entry)
        self.save(False)
        if on_result:
            on_result(entry)
        print(json.dumps(entry, ensure_ascii=False), flush=True)

    def save(self, passed):
        report = {"passed": passed, "package": PACKAGE, "kind": "Physical USB phone, black-box UI",
                  "checks": self.results,
                  "limitations": ["No account storage or credentials are read.",
                                  "No bookings, profiles, roles or server records are changed.",
                                  "Invalid input is verified by the visible error; no network interception is performed.",
                                  "Red/green results depend on actual bookings for the selected time."]}
        (self.directory / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    def run(self, on_result=None):
        def palette():
            result = self.capture("initial")
            if result["palette"]["neutral_fraction"] < .80:
                raise AssertionError("The app surface is not predominantly neutral")
            return result["palette"]

        self.step("neutral app palette", palette, on_result)
        self.step("open desks", lambda: self.click("Столы"), on_result)
        self.capture("initial-desks")
        if self.has_text("Список"):
            self.click("Список")

        def search():
            self.fill_time("start", "10:30")
            self.fill_time("end", "12:00")
            self.click("Найти места")
            time.sleep(2)
            result = self.capture("valid-interval")
            if not self.has_text("10:30") or not self.has_text("12:00"):
                raise AssertionError("Selected hours are not visible")
            return result["palette"]

        self.step("search a minute-precision interval", search, on_result)

        def invalid():
            self.fill_time("start", "13:00")
            self.fill_time("end", "12:00")
            self.click("Найти места")
            self.capture("invalid-range")
            if not self.has_text("окончания должно быть позже"):
                raise AssertionError("The invalid range has no visible error")

        self.step("reject an end before the start", invalid, on_result)
        self.fill_time("start", "10:30")
        self.click("Найти места")
        time.sleep(2)

        def map_view():
            self.click("План этажа")
            time.sleep(2)
            result = self.capture("map-interval")
            if not self.has_text("10:30") or not self.has_text("12:00"):
                raise AssertionError("Changing view lost the selected hours")
            return result["palette"]

        self.step("map keeps the selected hours", map_view, on_result)
        self.step("return to the list", lambda: self.click("Список"), on_result)
        self.capture("final-list")
        self.save(True)


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture")
    parser.add_argument("--tap")
    parser.add_argument("--fill-time", nargs=2, metavar=("SIDE", "HH:MM"))
    parser.add_argument("--search", action="store_true")
    parser.add_argument("--assert-text")
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--analyze", type=Path, help="OCR/palette analysis of an existing local image; no device access")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    if args.analyze:
        path = args.analyze.resolve()
        with Image.open(path) as picture:
            result = windows_ocr(path)
            result.update(palette=palette_stats(picture), outlines=outline_boxes(picture))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    check = PhoneThemeCheck(args.output)
    if args.run:
        check.run()
    if args.tap:
        check.click(args.tap)
    if args.fill_time:
        check.fill_time(*args.fill_time)
    if args.search:
        check.click("Найти места")
    if args.capture or args.assert_text:
        result = check.capture(args.capture or "assert")
        print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.assert_text and not check.has_text(args.assert_text):
        raise AssertionError("Visible text was not found: " + args.assert_text)


if __name__ == "__main__":
    main()
