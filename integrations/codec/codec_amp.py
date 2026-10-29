#!/usr/bin/env python3
"""codec_amp — drive the author's Yamaha HTR-6130 (and the Bravia's power) over BLE by
triggering the OFA URC7985 remote's built-in IR via the W16 config-char opcode.

Full command map from the 2026-09-23 live reverse-engineering session (see project
memory codec-remote-features). Every command is `57 16 <slot> <keyid> 00 00 00`
written (no-response) to config char 00001121-…a5a5 on the remote; the remote then
emits the amp's/TV's IR. No chord needed; works over a normal bonded connection.

⚠ The machine running this must be within Bluetooth range of the remote (the
remote sits by the TV/amp — so this runs on whichever machine is casting, i.e.
the LAPTOP when casting; the desktop is out of range upstairs).

Usage:
    codec_amp <command> [count]        # count = extra repeats (e.g. held volume)
    codec_amp --list

Examples:
    codec_amp cast-on          # power the amp on + drop it into TV-viewing (cast)
    codec_amp sleep-off        # power the amp off (sleep)
    codec_amp vol-up 3         # nudge volume up 3 steps
    codec_amp tv-power         # toggle the Bravia power (slot 3)
"""
import argparse
import asyncio
import sys
from pathlib import Path

# the shared identity module lives in the toolkit (../../ofa)
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "ofa"))
import ofa_identity  # noqa: E402

ADDR = ofa_identity.CONFIG_ADDR          # remote's management/config BLE identity
WR = "00001121-d102-11e1-9b23-00025b00a5a5"   # config char (write-no-response)

AMP = 2   # device slot 2 = AMPLIFIER (Yamaha IR)
TV = 3    # device slot 3 = TELEVISION (Sony IR)

# name -> (slot, keyid, default_shots)   default_shots>1 = a rapid double-press
# (fired in ONE connection ~0.4s apart), which is how the amp cycles sub-modes.
CMDS = {
    # power (DISCRETE + reliable — the base 0x01 toggle is flaky, avoid it)
    "power-on":  (AMP, 0x52, 1),
    "power-off": (AMP, 0x53, 1),
    # volume / mute
    "vol-up":    (AMP, 0x05, 1),
    "vol-down":  (AMP, 0x04, 1),
    "mute":      (AMP, 0x06, 1),
    # DSP / viewing modes (the digit keys 0-9 the author remapped)
    "music-enh": (AMP, 0x0d, 1),
    "dvd-view":  (AMP, 0x0e, 1),
    "disc":      (AMP, 0x0f, 1),   # DVD input, his 3.5mm music
    "tv-view":   (AMP, 0x10, 1),   # ⭐ the cast mode
    "radio":     (AMP, 0x11, 1),
    "jazz":      (AMP, 0x12, 1),
    "sports":    (AMP, 0x13, 1),
    "game":      (AMP, 0x13, 2),   # double-press cycles TV-Sports -> Game
    "movie":     (AMP, 0x14, 1),   # cycles Movie Dramatic/Spacious
    "2ch":       (AMP, 0x15, 1),
    "5ch":       (AMP, 0x15, 2),   # double-press cycles 2ch -> 5ch
    # inputs
    "in-dvd":    (AMP, 0x46, 1),
    "in-dvr":    (AMP, 0x47, 1),
    "in-cd":     (AMP, 0x48, 1),
    "in-cbl":    (AMP, 0x49, 1),   # the cast/TV-optical input (tv-view is nicer)
    "in-vaux":   (AMP, 0x4a, 1),
    "in-tuner":  (AMP, 0x4c, 1),
    # menu / cursor (amp setup — codec never needs these; LEFT has no W16 code)
    "menu":      (AMP, 0x17, 1),   # toggle
    "up":        (AMP, 0x28, 1),
    "down":      (AMP, 0x2b, 1),
    "right":     (AMP, 0x29, 1),
    "select":    (AMP, 0x2a, 1),
    "back":      (AMP, 0x18, 1),
    # television power (Sony slot 3)
    "tv-power":  (TV,  0x01, 1),
}

# Composite sequences fired in order in one connection.
SEQ = {
    "cast-on":   [(AMP, 0x52), (AMP, 0x10)],   # amp power-on + TV-viewing
    "sleep-off": [(AMP, 0x53)],                # amp power-off (TV handled separately)
}


def frame(slot, keyid):
    return bytes([0x57, 0x16, slot, keyid, 0x00, 0x00, 0x00])


async def send(seq, gap, tries=2):
    from bleak import BleakClient
    last = None
    for attempt in range(tries):
        try:
            async with BleakClient(ADDR, timeout=25) as client:
                for slot, keyid in seq:
                    await client.write_gatt_char(WR, frame(slot, keyid), response=False)
                    await asyncio.sleep(gap)
            return
        except Exception as exc:  # noqa: BLE001 - surface any connect/write failure
            last = exc
            await asyncio.sleep(0.6)
    raise last


def resolve(command, count):
    if command in SEQ:
        return list(SEQ[command])
    if command in CMDS:
        slot, keyid, shots = CMDS[command]
        shots = count if count else shots
        return [(slot, keyid)] * shots
    known = ", ".join(sorted(set(list(CMDS) + list(SEQ))))
    sys.exit(f"codec_amp: unknown command '{command}'. Known: {known}")


def main():
    ap = argparse.ArgumentParser(description="Drive the Yamaha amp / Bravia via the OFA remote over BLE.")
    ap.add_argument("command", nargs="?", help="see --list")
    ap.add_argument("count", nargs="?", type=int, default=None, help="repeat/hold count")
    ap.add_argument("--gap", type=float, default=0.4, help="seconds between shots")
    ap.add_argument("--list", action="store_true", help="print the command map and exit")
    a = ap.parse_args()
    if a.list or not a.command:
        for name in sorted(set(list(CMDS) + list(SEQ))):
            if name in SEQ:
                detail = " + ".join(f"{s}:{k:#04x}" for s, k in SEQ[name])
            else:
                s, k, shots = CMDS[name]
                detail = f"slot {s} key {k:#04x}" + (f" x{shots}" if shots > 1 else "")
            print(f"  {name:<11} {detail}")
        return
    seq = resolve(a.command.lower(), a.count)
    asyncio.run(send(seq, a.gap))
    print(f"codec_amp: {a.command} -> {len(seq)} shot(s) sent")


if __name__ == "__main__":
    main()
