#!/usr/bin/env python3
"""codec_led — the OFA remote's "remote settings" struct over BLE (NO reflash), decoded correctly.

THE THREE LIGHTS (the author's hardware fact) and what this tool can and cannot do:

  1. physical-KEY backlight      -> YES: bytes 2/3 of the settings struct ("Keypad Backlit" in the app)
  2. e-paper/touchscreen front-light -> YES: bytes 0/1 ("Brightness" / screen timeout in the app)
  3. the BLUE LIGHT BAR under the e-paper (the "rave") -> NO. It is a plain GPIO LED
     (LED channel 2 = P1_1, active-low, driver 0x81ba28) that the MAIN firmware drives
     directly: steady-ON while a key is held (key handler 0x81cb0c), ON while IR is being
     transmitted (IR-engine start/stop hooks 0x81610a/0x81615c), a 250 ms blink on a
     touch event (0x81d47c), and the pairing blink.  No settings byte, no W opcode and no
     "$" text command gates it, and it has no PWM (GPIO only) so it cannot be dimmed.
     Turning it off needs a firmware patch (NOP the four LED-2 call sites: that is
     blue_led_off_firmware.py) - deliberately NOT done by this tool.

The 8-byte struct read by W 09 and written by W 36 (validated at 0x81622e, persisted to
FTL offset 0x6a4, then forwarded to the screen MCU as a55a message type 0x15):

    idx  field (name from the OFA app's own debug dump)   validator
     0   screenBrightness   0..3                           <= 3
     1   screenTimeout      seconds                        1..60
     2   keypadBrightness   0=Off 1=Low 2=Med 3=High       <= 3
     3   keypadTimeout      seconds                        1..60
     4   lightSensorLevel   0..3                           <= 3
     5   gSensorLevel       bitfield (bit7 set by default) (not validated)
     6   idleTime           (units: firmware default 5)    1..60
     7   buzzerMode<<4 | buzzerLevel                       mode <= 3, level 1..5
         mode 0=off 1=physical keys 2=touch 3=both; level -> 2/5/8/15/30 % PWM duty
         (the P3_4 / TIM2 2040 Hz PWM is the BUZZER - "buzzer timer" - not a light)

A prior version of this tool treated byte 7 as the keypress LED: wrong. Byte 7 = 0x03 on
the author's remote means "buzzer off, level 3", which is why the light bar was on regardless.

    codec_led status                                  # decode the whole struct
    codec_led dump                                    # raw bytes
    codec_led backlight [--level 0..3] [--timeout 1..60]   # physical-key backlight (byte 2/3)
    codec_led screen    [--level 0..3] [--timeout 1..60]   # e-paper front-light (byte 0/1)
    codec_led buzzer    [--mode off|physical|touch|both] [--level 1..5]   # byte 7
    codec_led off      # key backlight Off  (byte 2 = 0)
    codec_led night    # key backlight Low  (byte 2 = 1)
    codec_led day      # key backlight Med  (byte 2 = 2)

Every write is a READ-MODIFY-WRITE of the whole 8 bytes (only the requested fields change),
followed by a read-back to verify.  The firmware validator silently rejects out-of-range
values, so a bad write leaves the struct unchanged - not a brick-capable operation.

Live prereqs (like the other config tools): phone Bluetooth OFF; select AMPLIFIER on
the remote to drop the HID link; hold ACTIVITIES+DEVICES; run via ofa/artifacts/venv.

    ofa/artifacts/venv/bin/python ofa/codec_led.py status

⚠ Do NOT let the OFA app "sync" settings after using this — its own copy of the
struct will overwrite it (same divergence as custom key labels).
"""
import ofa_identity
import argparse
import asyncio
import sys

ADDR = ofa_identity.CONFIG_ADDR                                   # management/config radio
WRITE_UUID = "00001121-d102-11e1-9b23-00025b00a5a5"          # write-no-response
NOTIFY_UUID = "00001122-d102-11e1-9b23-00025b00a5a5"
PREFIX = 0x57                                                # 'W'
OP_READ = 0x09                                               # W 09 -> reply "09 <8 struct bytes>"
OP_WRITE = 0x36                                              # W 36 <8 struct bytes> -> validate + copy + ftl_save + echo
SETTINGS_LEN = 8

# struct indices (names from the app's debug dump: screenBrightness, screenTimeout, keypadBrightness,
# keypadTimeout, lightSensorLevel, gSensorLevel, idleTime, buzzerMode, buzzerLevel)
I_SCREEN_BRIGHT, I_SCREEN_TIMEOUT, I_KEY_BRIGHT, I_KEY_TIMEOUT = 0, 1, 2, 3
I_LIGHT_SENSOR, I_GSENSOR, I_IDLE, I_BUZZER = 4, 5, 6, 7

FIELDS = (
    ("screenBrightness", "e-paper front-light 0..3"),
    ("screenTimeout", "seconds 1..60"),
    ("keypadBrightness", "physical-key backlight 0=Off 1=Low 2=Med 3=High"),
    ("keypadTimeout", "seconds 1..60"),
    ("lightSensorLevel", "0..3"),
    ("gSensorLevel", "bitfield, not validated"),
    ("idleTime", "1..60"),
    ("buzzer", "mode<<4 | level"),
)
BUZZER_DUTY = {1: 2, 2: 5, 3: 8, 4: 15, 5: 30}               # buzzer level -> PWM duty % (table 0x823d1e)
BUZZER_MODE_NAME = {0: "off", 1: "physical keys", 2: "touch keys", 3: "both (physical + touch)"}
BUZZER_MODES = {"off": 0, "physical": 1, "touch": 2, "both": 3}
KEY_LEVEL_NAME = {0: "Off", 1: "Low", 2: "Med", 3: "High"}

BAR_NOTE = ("  note   : the BLUE LIGHT BAR under the e-paper is NOT in this struct (GPIO LED2 / P1_1,\n"
            "           driven directly by the key/IR/touch handlers) - firmware patch needed to kill it.")


def frame_read():
    return bytes([PREFIX, OP_READ])


def frame_write(struct8):
    if len(struct8) != SETTINGS_LEN:
        raise ValueError(f"settings struct must be {SETTINGS_LEN} bytes")
    return bytes([PREFIX, OP_WRITE]) + bytes(struct8)


def validate(s):
    """Mirror of the firmware validator at 0x81622e; returns None if OK else the reason."""
    if s[0] > 3: return "screenBrightness > 3"
    if not 1 <= s[1] <= 60: return "screenTimeout not in 1..60"
    if s[2] > 3: return "keypadBrightness > 3"
    if not 1 <= s[3] <= 60: return "keypadTimeout not in 1..60"
    if s[4] > 3: return "lightSensorLevel > 3"
    if not 1 <= s[6] <= 60: return "idleTime not in 1..60"
    mode, level = s[7] >> 4, s[7] & 0x0F
    if level == 0 or level > 5: return "buzzer level not in 1..5"
    if mode > 3: return "buzzer mode > 3"
    return None


def describe(s):
    mode, level = s[I_BUZZER] >> 4, s[I_BUZZER] & 0x0F
    buzz = ("off" if mode == 0 else
            f"{BUZZER_MODE_NAME.get(mode, mode)}, level {level}/5 (~{BUZZER_DUTY.get(level, '?')}% duty)")
    return (f"  screen : brightness {s[I_SCREEN_BRIGHT]}/3, timeout {s[I_SCREEN_TIMEOUT]} s\n"
            f"  keys   : backlight {KEY_LEVEL_NAME.get(s[I_KEY_BRIGHT], s[I_KEY_BRIGHT])} "
            f"({s[I_KEY_BRIGHT]}/3), timeout {s[I_KEY_TIMEOUT]} s\n"
            f"  sensors: lightSensorLevel {s[I_LIGHT_SENSOR]}, gSensorLevel {s[I_GSENSOR]:#04x}, "
            f"idleTime {s[I_IDLE]}\n"
            f"  buzzer : {buzz}  [byte7={s[I_BUZZER]:#04x}]")


async def _txn(client, queue, frame, want_op, timeout=8.0):
    """Write a frame, then return the first notify whose opcode byte matches want_op."""
    while not queue.empty():
        queue.get_nowait()
    await client.write_gatt_char(WRITE_UUID, frame, response=False)
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while True:
        remaining = deadline - loop.time()
        if remaining <= 0:
            raise TimeoutError(f"no W {want_op:02x} reply within {timeout:.0f}s "
                               "(is the config link up? phone BT off, AMPLIFIER selected, ACTIVITIES+DEVICES held?)")
        r = await asyncio.wait_for(queue.get(), timeout=remaining)
        if r and r[0] == want_op:
            return r


async def read_settings(client, queue):
    r = await _txn(client, queue, frame_read(), OP_READ)
    if len(r) < 1 + SETTINGS_LEN:
        raise RuntimeError(f"short W 09 reply: {r.hex(' ')}")
    return bytearray(r[1:1 + SETTINGS_LEN])


async def write_settings(client, queue, struct8):
    # The echo reply confirms receipt; the read-back below is the real proof.
    try:
        await _txn(client, queue, frame_write(struct8), OP_WRITE, timeout=4.0)
    except TimeoutError:
        pass                                                 # some builds ack silently; verify by read-back


def apply_changes(cur, changes):
    """changes: dict idx -> value, or ('buzzer', mode|None, level|None)."""
    new = bytearray(cur)
    for k, v in changes.items():
        if k == "buzzer":
            mode, level = new[I_BUZZER] >> 4, new[I_BUZZER] & 0x0F
            if v[0] is not None: mode = v[0]
            if v[1] is not None: level = v[1]
            if not 1 <= level <= 5: level = 3               # validator rejects level 0 even when mode is off
            new[I_BUZZER] = (mode << 4) | level
        else:
            new[k] = v
    return new


async def _live(action):
    from bleak import BleakClient
    queue = asyncio.Queue()
    async with BleakClient(ADDR, timeout=25) as client:
        await client.start_notify(NOTIFY_UUID, lambda _c, d: queue.put_nowait(bytes(d)))
        if action == "DUMP":
            r = await _txn(client, queue, frame_read(), OP_READ)
            print(f"  raw W09 reply ({len(r)} bytes): {r.hex(' ')}")
            for i, b in enumerate(r[1:1 + SETTINGS_LEN]):
                name, desc = FIELDS[i]
                print(f"    idx {i}: {b:#04x} ({b:3d})  {name:<18} {desc}")
            print(BAR_NOTE)
            print("done.")
            return
        cur = await read_settings(client, queue)
        print("  current:")
        print(describe(cur))

        if action is None:                                   # status only
            print(BAR_NOTE)
            print("done.")
            return

        new = apply_changes(cur, action)
        why = validate(new)
        if why:
            raise RuntimeError(f"refusing to write: {why} (firmware would reject it anyway)")
        if new == cur:
            print("  already set — no change.")
            print("done.")
            return

        await write_settings(client, queue, new)
        await asyncio.sleep(0.2)
        back = await read_settings(client, queue)
        if back == new:
            print("  set    : ✓ verified (persisted to flash, forwarded to the screen MCU)")
            print(describe(back))
        elif back == cur:
            print(f"  FAILED : firmware rejected {new.hex(' ')} — struct unchanged.")
        else:
            print(f"  UNEXPECTED: struct is now {back.hex(' ')} (asked for {new.hex(' ')}).")
        print("done.")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    sub.add_parser("dump", help="print the raw settings struct with field names")
    kb = sub.add_parser("backlight", help="physical-key backlight (bytes 2/3)")
    kb.add_argument("--level", type=int, choices=range(0, 4))
    kb.add_argument("--timeout", type=int, choices=range(1, 61), metavar="1..60")
    sc = sub.add_parser("screen", help="e-paper front-light (bytes 0/1)")
    sc.add_argument("--level", type=int, choices=range(0, 4))
    sc.add_argument("--timeout", type=int, choices=range(1, 61), metavar="1..60")
    bz = sub.add_parser("buzzer", help="keypress buzzer (byte 7)")
    bz.add_argument("--mode", choices=list(BUZZER_MODES))
    bz.add_argument("--level", type=int, choices=range(1, 6))
    sub.add_parser("off", help="key backlight Off (byte 2 = 0)")
    sub.add_parser("night", help="key backlight Low (byte 2 = 1)")
    sub.add_parser("day", help="key backlight Med (byte 2 = 2)")
    a = p.parse_args()

    if a.cmd == "status":
        action = None
    elif a.cmd == "dump":
        action = "DUMP"
    elif a.cmd in ("backlight", "screen"):
        if a.level is None and a.timeout is None:
            p.error(f"{a.cmd} needs --level and/or --timeout")
        bi, ti = (I_KEY_BRIGHT, I_KEY_TIMEOUT) if a.cmd == "backlight" else (I_SCREEN_BRIGHT, I_SCREEN_TIMEOUT)
        action = {}
        if a.level is not None: action[bi] = a.level
        if a.timeout is not None: action[ti] = a.timeout
    elif a.cmd == "buzzer":
        if a.mode is None and a.level is None:
            p.error("buzzer needs --mode and/or --level")
        action = {"buzzer": (BUZZER_MODES[a.mode] if a.mode else None, a.level)}
    elif a.cmd == "off":
        action = {I_KEY_BRIGHT: 0}
    elif a.cmd == "night":
        action = {I_KEY_BRIGHT: 1}
    else:  # day
        action = {I_KEY_BRIGHT: 2}

    try:
        asyncio.run(_live(action))
    except (TimeoutError, RuntimeError) as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
