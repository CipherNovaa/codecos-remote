"""Backlight / screen front-light / buzzer — reuses codec_led's settings struct.

Read-modify-write of the 8-byte settings struct (validator-safe, non-bricking): read
the current struct, apply field changes, run codec_led.validate (a mirror of the
firmware validator so we never send a struct the firmware would reject), write, then
read back and confirm. The BLUE LIGHT BAR is NOT in this struct — it is a firmware
GPIO (already patched off); the Lights panel says so.
"""
import codec_led as cl

from .link import BleSession

# re-exports for the panel
I_SCREEN_BRIGHT = cl.I_SCREEN_BRIGHT
I_SCREEN_TIMEOUT = cl.I_SCREEN_TIMEOUT
I_KEY_BRIGHT = cl.I_KEY_BRIGHT
I_KEY_TIMEOUT = cl.I_KEY_TIMEOUT
I_IDLE = cl.I_IDLE
I_BUZZER = cl.I_BUZZER
KEY_LEVEL_NAME = cl.KEY_LEVEL_NAME
BUZZER_MODES = cl.BUZZER_MODES
BUZZER_MODE_NAME = cl.BUZZER_MODE_NAME
describe = cl.describe
validate = cl.validate
BAR_NOTE = cl.BAR_NOTE


async def read():
    """Return the current settings struct (bytearray, 8 bytes)."""
    async with BleSession() as s:
        return await cl.read_settings(s.client, s.queue)


async def apply(changes):
    """Apply {idx: value} or {'buzzer': (mode|None, level|None)} changes.

    Returns dict: {ok, changed, reason, before(bytes), after(bytes), readback(bytes)}.
    Validated before any write; if the firmware would reject it, ok=False, no write.
    """
    import asyncio
    async with BleSession() as s:
        cur = await cl.read_settings(s.client, s.queue)
        new = cl.apply_changes(cur, changes)
        why = cl.validate(new)
        if why:
            return {"ok": False, "changed": False, "reason": why,
                    "before": bytes(cur), "after": bytes(new), "readback": bytes(cur)}
        if new == cur:
            return {"ok": True, "changed": False, "reason": "already set",
                    "before": bytes(cur), "after": bytes(new), "readback": bytes(cur)}
        await cl.write_settings(s.client, s.queue, new)
        await asyncio.sleep(0.2)
        back = await cl.read_settings(s.client, s.queue)
        return {"ok": back == new, "changed": True,
                "reason": "verified" if back == new else "firmware rejected / unexpected",
                "before": bytes(cur), "after": bytes(new), "readback": bytes(back)}
