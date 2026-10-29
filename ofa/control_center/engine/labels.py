"""Touchscreen key labels — drives the audited labels.py path via subprocess.

We do NOT reimplement the label encoding. labels.py already does the right thing:
a live read + plan (no --apply) is a safe dry run; --apply writes with a fresh
--backup journal and a readback verify. Panels run these argv through ProcRunner so
the plan/verify output streams into the console and every write is journaled.

Key names are custom_firmware.ALIASES: red/green/yellow/blue/return/info + spare1..spare10.
"""
from .. import constants as K
from .link import LinkDown

LABELS_PY = K.OFA_DIR / "labels.py"

# physical coloured/function keys first, then the spare touchscreen keys
DISPLAY_ORDER = (["red", "green", "yellow", "blue", "return", "info"]
                 + [f"spare{i}" for i in range(1, 11)])


def decode_label(hexstr):
    """A read_label hex reply → its ASCII text, or None if the key has no label."""
    if not hexstr:
        return None
    try:
        return bytes.fromhex(hexstr).rstrip(b"\0").decode("ascii", "replace")
    except ValueError:
        return None


async def read_all(slot=1):
    """Live-read the current label of every touchscreen key on `slot`.

    Returns [{key, alias, text|None}] in DISPLAY_ORDER. Raises LinkDown if the config
    link is not up; a wrong-firmware / read error surfaces as a normal exception.
    """
    from bleak import BleakClient
    import labels as labels_cli
    from touch_pages import ADDRESS, check_slot
    from custom_firmware import ALIASES

    check_slot(slot)
    try:
        client = BleakClient(ADDRESS, timeout=25)
        await client.__aenter__()
    except Exception as e:                       # never connected → link down
        raise LinkDown(str(e))
    out = []
    try:
        config = labels_cli.LabelConfig(client)
        await config.start()
        await config.capabilities()              # confirms the CT01 label firmware
        for name in DISPLAY_ORDER:
            alias = ALIASES[name]
            try:
                out.append({"key": name, "alias": alias,
                            "text": decode_label(await config.read_label(slot, alias))})
            except Exception:                    # one flaky read shouldn't drop the list
                out.append({"key": name, "alias": alias, "text": None, "error": True})
    finally:
        try:
            await client.__aexit__(None, None, None)
        except Exception:
            pass
    return out


def _base(slot, key, text):
    return [str(K.VENV_PY), str(LABELS_PY), "set",
            "--slot", str(int(slot)), "--key", str(key), "--text", str(text)]


def plan_argv(slot, key, text, backup):
    """Read + plan only (no write). backup path is required by labels.py even for a plan."""
    return _base(slot, key, text) + ["--backup", str(backup)]


def apply_argv(slot, key, text, backup):
    """Write with a fresh backup + readback verify."""
    return _base(slot, key, text) + ["--backup", str(backup), "--apply"]


def clear_plan_argv(slot, key, backup):
    """Read + plan a label removal (no write)."""
    return [str(K.VENV_PY), str(LABELS_PY), "set",
            "--slot", str(int(slot)), "--key", str(key), "--clear", "--backup", str(backup)]


def clear_argv(slot, key, backup):
    """Remove a key's custom label (leaves the page in place), with backup + verify."""
    return clear_plan_argv(slot, key, backup) + ["--apply"]


def valid_key(key):
    return key in K.LABEL_KEYS


def valid_text(text):
    if text == "":
        return None                      # empty = clear the label (handled as a remove)
    if not (1 <= len(text) <= K.LABEL_MAX_LEN):
        return f"label must be 1..{K.LABEL_MAX_LEN} characters"
    if not all(32 <= ord(ch) < 127 for ch in text):
        return "label must be printable ASCII"
    return None
