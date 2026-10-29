"""Main-chip firmware — argv builders for the validator gate and flash.py.

Flashing the main MCU has NO ROLLBACK and can brick the remote, so this module only
builds the commands; the panel enforces the gate: a check_*.py validator must exit 0
AND report PASS, then a type-to-confirm dialog, before flash.py --execute is allowed.
Plan mode (no --execute) is always safe. E-ink screen firmware is app-only (not here).

Recovery, if a main flash goes wrong: re-flash constants.RECOVERY_MAIN.
"""
import hashlib
from pathlib import Path

import flash as _flash

from .. import constants as K

FLASH_PY = K.OFA_DIR / "flash.py"

# Only validators we've confirmed accept a POSITIONAL .bin and emit a recognizable PASS
# are auto-mapped. (check_firmware/check_screensaver/… take a dir or the APK asset and
# would just error on a candidate .bin — routing to them wrongly LOCKED legit images.)
_VALIDATORS = [
    ("codecos", "check_blue_led_off.py"),   # codecOS v16.0 = the blue-LED-off release image
    ("blueoff", "check_blue_led_off.py"),
    ("blue-led", "check_blue_led_off.py"),
]

# Pinned SHA-256 of the reference images we ship. A byte-identical selection is a known,
# untampered image — a valid gate for images that have no single-file emulation validator
# (e.g. the recovery baseline), so a break-glass recovery flash can still unlock.
KNOWN_SHA = {
    "957c9549df23f1a2e8952f328a1eac7bf9e242b2aabca88dc4cf66f584b70300": "codecOS v16.0 (blue-LED-off)",
    "3b2e3240b640f33d322bbd1b5484bba26f8e6feb569833150c146feee4bf82de": "screensaver main v1 (recovery baseline)",
}


def suggest_validator(image_path):
    """The check_*.py that proves an image, or None when no single-file validator fits
    (the panel then falls back to the pinned-SHA gate, or leaves Execute locked)."""
    name = Path(image_path).name.lower()
    for hint, checker in _VALIDATORS:
        if hint in name:
            return checker
    return None


def image_sha(image_path):
    try:
        return hashlib.sha256(Path(image_path).read_bytes()).hexdigest()
    except OSError:
        return None


def known_image_name(image_path):
    """Friendly name if the image byte-matches a shipped reference, else None."""
    return KNOWN_SHA.get(image_sha(image_path))


def known_candidates():
    """Curated main-chip images we have on disk, newest-intent first."""
    out = []
    for label, path in [
        ("codecOS v16.0 — final unified main (currently flashed)", K.ARTIFACTS / "codecos-v16" / "main-codecos-v16-DO-NOT-FLASH.bin"),
        ("screensaver main v1 (recovery baseline)", K.RECOVERY_MAIN),
    ]:
        if Path(path).exists():
            out.append((label, str(path), suggest_validator(path)))
    return out


def validator_argv(checker, image_path):
    return [str(K.VENV_PY), str(K.OFA_DIR / checker), str(image_path)]


def is_pass(exit_code, output):
    """The gate: the validator exited 0 AND declared PASS, in either style.

    check_blue_led_off.py emits JSON `"result": "PASS"`; the other check_*.py print a
    human `PASS: <detail>` line. Accept both; reject if any line declares FAIL.
    """
    if exit_code != 0:
        return False
    low = output.lower()
    if any(line.strip().lower().startswith("fail") for line in output.splitlines()):
        return False
    if '"result": "pass"' in low or "\nresult: pass" in low:
        return True
    for line in output.splitlines():
        s = line.strip().lower()
        if s == "pass" or s.startswith("pass:") or s.startswith("pass "):
            return True
    return False


def plan_argv(image_path):
    """flash.py plan — prints the plan JSON, touches nothing (no --execute)."""
    return [str(K.VENV_PY), str(FLASH_PY), str(image_path),
            "--stock", str(K.STOCK_BIN), "--apk", str(K.APK)]


def execute_argv(image_path, log_path):
    """flash.py --execute — DESTRUCTIVE. log_path must NOT already exist (flash.py open('x'))."""
    return plan_argv(image_path) + ["--execute", "--log", str(log_path)]


def candidate_kind(image_path):
    """Describe what flash.py thinks the image is (best-effort, non-fatal)."""
    try:
        stock = K.STOCK_BIN.read_bytes()
        img = Path(image_path).read_bytes()
        if _flash.is_blue_led_off_candidate(img, stock):
            return "recognised: codecOS v16.0 (blue-LED-off release)"
        if _flash.is_screensaver_main_candidate(img, stock):
            return "recognised: screensaver main"
    except Exception:
        pass
    return "unrecognised image (validator gate is mandatory)"


def confirm_phrase():
    return "FLASH MAIN FIRMWARE"
