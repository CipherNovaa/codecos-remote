"""Host-specific constants and paths — the one place to edit for a new machine.

Everything the app touches that is machine- or install-specific lives here: the
remote's BLE identity, the venv, the flash inputs, the journal root. Ported to the
laptop = change only these (the plan: IR/amp work runs where the BLE radio is in
range, i.e. the laptop when casting).
"""
from pathlib import Path

# ── BLE identity (single-sourced from the engines; re-exported for panels/UI text) ──
import codec_screensaver as _ss

ADDR = _ss.ADDR                       # management/config radio (ofa_identity.CONFIG_ADDR)
WRITE_UUID = _ss.WRITE_UUID           # 00001121-…a5a5  write-no-response (config char)
NOTIFY_UUID = _ss.NOTIFY_UUID         # 00001122-…a5a5
NSLOTS = _ss.NSLOTS                   # 6 screensaver slots
SCREEN_FW_VERSION = _ss.EXT_VERSION   # e-paper firmware this toolkit targets (v15)

# ── paths ──
OFA_DIR = Path(__file__).resolve().parent.parent          # …/ofa
REPO_ROOT = OFA_DIR.parent                                # repo root
ARTIFACTS = OFA_DIR / "artifacts"
JOURNAL_ROOT = ARTIFACTS / "control-center"               # <stamp>-<action>/ per run
VENV_PY = ARTIFACTS / "venv" / "bin" / "python"           # the OFA venv interpreter

FINALISTS_DIR = _ss.LIBRARY_DIR                           # image library + lineup.json
LINEUP_PATH = _ss.LINEUP_PATH

# ── flash inputs (Firmware panel) ──
STOCK_BIN = ARTIFACTS / "ble24" / "stock-0.5.7.bin"
APK = ARTIFACTS / "apk" / "split_config.arm64_v8a.apk"   # your own copy of the vendor app split
# The one true recovery image if a main flash goes wrong (re-flash this):
RECOVERY_MAIN = ARTIFACTS / "screensaver-v1" / "main-screensaver-v1-DO-NOT-FLASH.bin"

# ── screensaver render vocab ──
RENDER_MODES = ("cover", "contain")
RENDER_METHODS = ("threshold", "dither")
EXPECT_1BPP = _ss.PAYLOAD_LEN          # 8072 bytes per packed slot blob
CYCLE_MODES = tuple(_ss.MODES.keys())  # ("random", "sequential")

# ── labels: valid key names on the current (spare-keys) firmware ──
from custom_firmware import ALIASES as _ALIASES
LABEL_KEYS = list(_ALIASES.keys())     # red/green/yellow/blue/return/info + spare1..spare10
LABEL_MAX_LEN = 15                      # 1..15 ASCII per label

# ── the config-link ritual (surfaced, never hidden) ──
RITUAL_STEPS = [
    "On your PHONE, turn Bluetooth OFF (the OFA app steals the link if it is running).",
    "On the remote, select the AMPLIFIER device.",
    "Hold ACTIVITIES + DEVICES together until the key LEDs blink — the config link is now up.",
    "It expires after a short idle; if a command times out, re-hold ACTIVITIES + DEVICES.",
]
RITUAL_ONE_LINER = ("config link down — phone BT OFF · select AMPLIFIER · "
                    "hold ACTIVITIES+DEVICES until the LEDs blink")

# ── the standing warnings the UI must keep in front of the user ──
NO_SYNC_WARNING = ("Do NOT let the OFA phone app connect/sync while using this — it "
                   "overwrites labels and the screen version cache. Keep phone BT off.")
