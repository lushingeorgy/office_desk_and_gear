"""Non-destructive controls and evidence for navigation checks on one USB phone.

Never clears application data, changes display settings or modifies an account.
An APK update requires the same signing certificate as the installed APK.
Set ODG_ADB to an Android SDK adb executable, or put adb on PATH.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "tests" / "artifacts" / "navigation-phone"
ADB = Path(os.environ.get("ODG_ADB") or shutil.which("adb") or
           ROOT / "tests" / "artifacts" / "android-tools" / "platform-tools" / "adb.exe")
PACKAGE = "org.officedeskgear.officedeskgear"
os.environ["ADB_USB_LEGACY"] = "1"
os.environ["ANDROID_ADB_LOG_PATH"] = str(ROOT / "tests" / "artifacts" / "android-user" / "adb.log")


def adb(*args, required=True, timeout=45):
    result = subprocess.run([str(ADB), "-d", *map(str, args)], capture_output=True, timeout=timeout)
    if required and result.returncode:
        raise RuntimeError(result.stderr.decode(errors="replace")[:800])
    return result.stdout


def apk_certificate(path):
    """Extract the public signer certificate from the APK v2 signing block."""
    data = Path(path).read_bytes()
    end = data.rfind(b"PK\x05\x06")
    if end < 0:
        raise ValueError("APK ZIP footer is missing")
    central = struct.unpack_from("<I", data, end + 16)[0]
    if data[central - 16:central] != b"APK Sig Block 42":
        raise ValueError("APK signing block is missing")
    length = struct.unpack_from("<Q", data, central - 24)[0]
    start = central - length - 8
    if struct.unpack_from("<Q", data, start)[0] != length:
        raise ValueError("APK signing block size mismatch")

    def item(buffer, offset=0):
        size = struct.unpack_from("<I", buffer, offset)[0]
        return buffer[offset + 4:offset + 4 + size], offset + 4 + size

    cursor = start + 8
    while cursor < central - 24:
        size = struct.unpack_from("<Q", data, cursor)[0]
        kind = struct.unpack_from("<I", data, cursor + 8)[0]
        if kind == 0x7109871A:
            signers, _ = item(data[cursor + 12:cursor + 8 + size])
            signer, _ = item(signers)
            signed, _ = item(signer)
            _digests, offset = item(signed)
            certificates, _ = item(signed, offset)
            certificate, _ = item(certificates)
            return hashlib.sha256(certificate).hexdigest()
        cursor += size + 8
    raise ValueError("APK v2 signature is missing")


def account_summary():
    files = adb("shell", "run-as", PACKAGE, "find", ".", "-type", "f", "-name", "state.json").decode().splitlines()
    files = [p.strip() for p in files if p.strip().endswith("/private/state.json")]
    if len(files) != 1:
        raise RuntimeError("Expected exactly one private account state")
    state = json.loads(adb("exec-out", "run-as", PACKAGE, "cat", files[0]))
    credential = state.get("credential")
    return {
        "user_id": (state.get("user") or {}).get("id"),
        "role": (state.get("user") or {}).get("role"),
        "api_url": state.get("api_url"),
        "credential_kind": (credential or {}).get("kind"),
        "credential_fingerprint": hashlib.sha256(json.dumps(credential, sort_keys=True).encode()).hexdigest() if credential else None,
        "cache_keys": sorted(state.get("cache", {})),
        "plaintext_jwt_present": bool(re.search(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", json.dumps(state))),
    }


def inspect():
    version = adb("shell", "dumpsys", "package", PACKAGE).decode(errors="replace")
    return {
        "device": adb("shell", "getprop", "ro.product.model").decode().strip(),
        "android": adb("shell", "getprop", "ro.build.version.release").decode().strip(),
        "version": re.search(r"versionName=(\S+)", version).group(1),
        "account": account_summary(),
    }


def require_foreground():
    windows = adb("shell", "dumpsys", "window", "displays").decode(errors="replace")
    focus = next((line for line in windows.splitlines() if "mCurrentFocus=" in line), "")
    if PACKAGE not in focus:
        raise RuntimeError("Our app is not in the foreground; no touch was sent")


def install(apk):
    before = inspect()
    (OUTPUT / "before-update.json").write_text(json.dumps(before, indent=2), encoding="utf-8")
    installed = adb("shell", "pm", "path", PACKAGE).decode().splitlines()
    base = next(p.removeprefix("package:").strip() for p in installed if p.strip().endswith("/base.apk"))
    backup = OUTPUT / "installed-before.apk"
    adb("pull", base, backup)
    old_certificate, new_certificate = apk_certificate(backup), apk_certificate(apk)
    if old_certificate != new_certificate:
        raise RuntimeError("Signing certificates differ; update was not attempted and data was preserved")
    print(adb("install", "-r", apk, timeout=180).decode().strip())
    after = inspect()
    if before["account"] != after["account"]:
        raise AssertionError("The in-place update changed the saved account/cache")
    report = {"before": before, "after": after, "certificate_sha256": new_certificate,
              "apk_sha256": hashlib.sha256(Path(apk).read_bytes()).hexdigest(), "account_preserved": True}
    (OUTPUT / "installation.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--inspect", action="store_true")
    parser.add_argument("--install", type=Path)
    parser.add_argument("--restart", action="store_true")
    parser.add_argument("--tap", nargs=2, type=int)
    parser.add_argument("--swipe", nargs=4, type=int)
    parser.add_argument("--key", type=int)
    parser.add_argument("--capture")
    parser.add_argument("--record")
    parser.add_argument("--seconds", type=int, default=25)
    args = parser.parse_args()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    if args.record:
        require_foreground()
        if not re.fullmatch(r"[a-z0-9-]+", args.record) or not 1 <= args.seconds <= 45:
            raise ValueError("Invalid recording name or duration")
        remote = "/sdcard/Download/odg-" + args.record + ".mp4"
        adb("shell", "screenrecord", "--time-limit", args.seconds, "--size", "720x1560",
            "--bit-rate", "4000000", remote, timeout=args.seconds + 15)
        target = OUTPUT / (args.record + ".mp4")
        adb("pull", remote, target)
        adb("shell", "rm", remote)
        print(target)
    if args.install:
        install(args.install.resolve())
    if args.restart:
        adb("shell", "am", "force-stop", PACKAGE)
        adb("shell", "monkey", "-p", PACKAGE, "-c", "android.intent.category.LAUNCHER", "1")
        time.sleep(3)
    if args.tap or args.swipe or args.key:
        require_foreground()
        if args.tap:
            adb("shell", "input", "tap", *args.tap)
        if args.swipe:
            adb("shell", "input", "swipe", *args.swipe, "450")
        if args.key:
            adb("shell", "input", "keyevent", args.key)
        time.sleep(0.5)
    if args.capture:
        if not re.fullmatch(r"[a-z0-9-]+", args.capture):
            raise ValueError("Invalid evidence name")
        target = OUTPUT / (args.capture + ".png")
        target.write_bytes(adb("exec-out", "screencap", "-p"))
        from PIL import Image
        with Image.open(target) as picture:
            picture.thumbnail((720, 1560), Image.Resampling.LANCZOS)
            picture.save(OUTPUT / (args.capture + "-preview.png"))
    if args.inspect:
        print(json.dumps(inspect(), indent=2))


if __name__ == "__main__":
    main()
