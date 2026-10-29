"""OFA Remote Control Center — a native GUI over the whole URC7985 toolkit.

One desktop app that unifies every custom-remote feature we built over the saga:
the e-paper screensaver (BLE slot push), touchscreen key labels, LED/buzzer/backlight
settings, amp/TV IR-over-BLE, main-chip firmware flashing, and the loggers/validators.

It does not reimplement anything — it imports the existing, verified engines
(``codec_screensaver``, ``screensaver_prep``, ``codec_led``, ``codec_amp``, ``labels``,
``flash``) and reuses the audited flash/label paths. It also *surfaces* the parts that
are physical or dangerous: the config-link ritual, the "don't let the OFA app sync"
trap, and the no-rollback main flash.

Launch:  ``ofa/control_center_app``  (→ ``artifacts/venv/bin/python -m control_center``)
"""
import sys
import pathlib

# The engine adapters import the sibling CLI modules (codec_screensaver, codec_amp, …)
# that live in ofa/. Guarantee that directory is importable no matter how we launch.
_OFA_DIR = pathlib.Path(__file__).resolve().parent.parent
if str(_OFA_DIR) not in sys.path:
    sys.path.insert(0, str(_OFA_DIR))
# codec_amp (the IR & Amp tab's engine) is a household integration kept in ../integrations/codec.
_INTEGRATION_DIR = _OFA_DIR.parent / "integrations" / "codec"
if _INTEGRATION_DIR.is_dir() and str(_INTEGRATION_DIR) not in sys.path:
    sys.path.append(str(_INTEGRATION_DIR))

__version__ = "1.0"
