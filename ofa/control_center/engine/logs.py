"""Loggers, device reads, and the journal browser — argv builders + helpers.

The live watcher needs root (exclusive evdev grab + mgmt RSSI socket), so it runs via
pkexec. pkexec runs the tool AS ROOT, and a non-root process CANNOT signal a root one
(kill → EPERM) — so the app cannot stop it with QProcess.kill(). The reliable stop is
therefore ALSO root: `pkexec pkill` the logger by name (root kills root). That is the
whole design; there is no timer — the watcher runs until Stop.

`ofa_range.py` already logs keypress AND RSSI in one line ("KEY #3 KEY_F21 RSSI -78
dBm"), so it is the single combined watcher. BLE reads are short-lived and root-free.
"""
from pathlib import Path

from .. import constants as K

PKEXEC = "pkexec"

# pkill -f markers (unique substrings of the root logger command lines)
WATCH_MARKER = "ofa_range.py"
CAPTURE_MARKER = "log_keys.py"


def watch_argv():
    """Combined live watcher: each keypress + the link RSSI (root). Runs until Stop.

    A large duration stands in for "until stopped"; Stop kills it by name. ofa_range.py
    exclusively grabs the OFA input while running (keys won't reach the desktop).
    """
    return [PKEXEC, str(K.VENV_PY), str(Path.home() / "ofa_range.py"), "86400"]


def capture_argv(out_path):
    """Exclusive capture of OFA input incl. reconnects → out_path (root). Runs until Stop."""
    return [PKEXEC, str(K.VENV_PY), str(K.OFA_DIR / "log_keys.py"), "--output", str(out_path)]


def stop_argv(marker):
    """Root-kill the running logger by name — the only way to stop a root process."""
    return [PKEXEC, "pkill", "-TERM", "-f", marker]


def readconfig_argv(out_path):
    """Read the remote's device list over BLE → JSON at out_path (no root)."""
    return [str(K.VENV_PY), str(K.OFA_DIR / "read_config.py"), "--output", str(out_path)]


def ofaread_argv(cmd="570702"):
    """Read page-key config over BLE, printed to stdout (no root)."""
    return [str(K.VENV_PY), str(K.OFA_DIR / "ofa_read.py"), "--cmd", str(cmd)]


def list_journals(limit=60):
    """(name, path) of recent control-center run journals, newest first."""
    root = K.JOURNAL_ROOT
    if not root.exists():
        return []
    dirs = sorted((p for p in root.iterdir() if p.is_dir()), reverse=True)
    return [(p.name, p) for p in dirs[:limit]]


def read_journal(path):
    """Return the output.log text for a journal dir (best effort)."""
    log = Path(path) / "output.log"
    try:
        return log.read_text(errors="replace")
    except OSError:
        return "(no output.log)"
