#!/usr/bin/env python3
"""Talk to the OFA Smart Pro Touch (URC7985) config channel from the PC — the same protocol
the phone app uses (reverse-engineered from an HCI snoop, 2026-09-19).

Config radio: $OFA_ADDR (reachable while the remote is in app-link mode:
hold ACTIVITIES+DEVICES until the LEDs blink blue; the phone app must NOT be connected).
  write  00001121-d102-11e1-9b23-00025b00a5a5   (commands, write-without-response)
  notify 00001122-d102-11e1-9b23-00025b00a5a5   (replies; first byte echoes the opcode)
Commands are b"W" + opcode + args. Seen from the app:
  01            identify           -> 01 <16-char id>
  02            list devices       -> 02 <records...>  (multi-notification)
  08 / 09 / 36  unknown (settings?)
  11 <slot>     prepare slot (before a create)
  14 <slot> <type> 80 <codeHi> <codeLo> 00 <profile> <name[16]>   create device
  14 <0x80|slot> 00 80 <codeHi> <codeLo> 00 <profile> <name[16]>  rename/update
  19 <slot order...>  device order
Usage:
  ofa_cfg.py ident | list | raw <hex...>
"""
import asyncio, sys, time
from bleak import BleakClient

import os
from pathlib import Path
ADDR = os.environ.get("OFA_ADDR", "XX:XX:XX:XX:XX:XX").upper()  # config radio (see ofa/ofa_identity.py)
WR = "00001121-d102-11e1-9b23-00025b00a5a5"
NT = "00001122-d102-11e1-9b23-00025b00a5a5"
LOG = str(Path.home() / ".cache/ofa/cfg.log")

def log(s):
    s = f"{time.strftime('%H:%M:%S')} {s}"
    print(s, flush=True)
    open(LOG, "a").write(s + "\n")

async def main(argv):
    replies = []
    def on_notify(_, data: bytearray):
        replies.append(bytes(data))
        log(f"<< {bytes(data).hex(' ')}")
    async with BleakClient(ADDR, timeout=20) as c:
        log(f"connected to {ADDR}")
        await c.start_notify(NT, on_notify)
        async def cmd(payload: bytes, wait=1.5):
            log(f">> {payload.hex(' ')}")
            await c.write_gatt_char(WR, payload, response=False)
            await asyncio.sleep(wait)
        op = argv[0] if argv else "ident"
        if op == "ident":
            await cmd(b"W\x01")
        elif op == "list":
            await cmd(b"W\x02", wait=2.5)
            blob = b"".join(r for r in replies if r)[1:]  # strip the 02 echo
            for i in range(0, len(blob), 22):
                rec = blob[i:i + 22]
                if not any(rec): continue
                name = rec[6:22].split(b"\0")[0].decode(errors="replace")
                log(f"   record {i // 22}: {rec[:6].hex(' ')}  '{name}'")
        elif op == "mkdev":
            # mkdev <slot> <type> <codeHex> <profileHex> <NAME>
            slot, typ = int(argv[1]), int(argv[2], 16)
            code, prof, name = int(argv[3], 16), int(argv[4], 16), argv[5].encode()[:16]
            await cmd(b"W\x11" + bytes([slot]))
            rec = bytes([slot, typ, 0x80, code >> 8, code & 0xFF, 0x00, prof]) + name.ljust(16, b"\0")
            await cmd(b"W\x14" + rec)
            order = bytes(range(1, slot + 1)).ljust(12, b"\0")
            await cmd(b"W\x19" + order)
            await cmd(b"W\x02", wait=2.5)
        elif op == "update":
            # update <slot> <codeHex> <profileHex> <NAME>  (the app's rename form: 0x80|slot, type 00)
            slot, code, prof, name = int(argv[1]), int(argv[2], 16), int(argv[3], 16), argv[4].encode()[:16]
            rec = bytes([0x80 | slot, 0x00, 0x80, code >> 8, code & 0xFF, 0x00, prof]) + name.ljust(16, b"\0")
            await cmd(b"W\x14" + rec)
            await cmd(b"W\x02", wait=2.5)
            blob = b"".join(r for r in replies if r and r[0] == 2)[1:]
            for i in range(0, len(blob), 22):
                rec = blob[i:i + 22]
                if any(rec): log(f"   record {i // 22}: {rec[:6].hex(' ')}  '{rec[6:22].split(bytes(1))[0].decode(errors='replace')}'")
        elif op == "raw":
            await cmd(bytes.fromhex("".join(argv[1:])), wait=2.5)
        await c.stop_notify(NT)

asyncio.run(main(sys.argv[1:]))
