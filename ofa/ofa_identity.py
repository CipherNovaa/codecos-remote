"""The remote's Bluetooth identity: the one per-unit value every live tool needs.

Every URC7985 has its own factory address. The tools never hard-code it; they read
it from (first match wins):

  1. the ``OFA_ADDR`` environment variable, e.g. ``OFA_ADDR=XX:XX:XX:XX:XX:XX``
  2. ``~/.config/codecos-remote/config.json`` -> ``{"addr": "XX:XX:XX:XX:XX:XX"}``
     (copy ``config.example.json`` from the repo root)

``ADDR`` is the management/config radio, the identity the remote advertises as
"OFA Smart Control" while ACTIVITIES+DEVICES is held. Find it with
``bluetoothctl scan le`` (or ``ofa/scan_remote.py`` once a prefix is known).

Each device slot also gets its own BLE HID identity, derived from the same factory
address: the last two bytes swap and the low byte adds the slot number. So for a
config radio ``..:..:..:..:P:Q`` the slot-N HID address is ``..:..:..:..:Q:(P+N)``.
Override it with ``OFA_HID_ADDR`` if your unit differs.

Offline tools (builders, checkers) import this too but never use the value, so an
unset address only fails once a tool actually tries to connect.
"""
import json
import os
from pathlib import Path

UNSET = "UNSET-SEE-CONFIG.EXAMPLE.JSON"
CONFIG_PATH = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "codecos-remote" / "config.json"


def _config():
    try:
        return json.loads(CONFIG_PATH.read_text())
    except (OSError, ValueError):
        return {}


def _addr():
    value = os.environ.get("OFA_ADDR") or _config().get("addr") or UNSET
    return value.upper()


CONFIG_ADDR = _addr()


def hid_addr(slot=1):
    """BLE HID identity of device slot ``slot`` (the address the PC bonds with)."""
    override = os.environ.get("OFA_HID_ADDR") or _config().get("hid_addr")
    if override and slot == 1:
        return override.upper()
    parts = CONFIG_ADDR.split(":")
    if len(parts) != 6:
        return UNSET
    return ":".join(parts[:4] + [parts[5], f"{int(parts[4], 16) + slot:02X}"])


def prefix():
    """The first four address bytes, shared by every identity of this unit."""
    return CONFIG_ADDR[:12]


def is_set():
    return CONFIG_ADDR != UNSET
