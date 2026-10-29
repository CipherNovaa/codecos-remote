#!/usr/bin/env python3
"""OFA W16 IR-trigger LIVE test — SINGLE shot, guarded. Read-only unless --fire.

Default (dry run): connect + do the SAFE W02 device-list read (exactly what
read_config.py does) to confirm the link and the device slots. This also probes
whether the config channel answers in the CURRENT remote state (chord held vs
not) WITHOUT emitting any IR.

With --fire: send exactly ONE W16 IR-emit write, then stop. Never loops.
Payload must start with 57 16. Amp is slot 2, TV slot 3; POWER=01 VOL+=05 VOL-=04.
"""
import ofa_identity
import argparse, asyncio, sys

WR = "00001121-d102-11e1-9b23-00025b00a5a5"
NT = "00001122-d102-11e1-9b23-00025b00a5a5"


async def run(address, payload, fire):
    from bleak import BleakClient
    replies = []
    print(f"connecting to {address} ...", flush=True)
    async with BleakClient(address, timeout=25) as client:
        print("connected.", flush=True)
        await client.start_notify(NT, lambda _, d: replies.append(bytes(d)))
        # SAFE: device-list read first (read-only) — confirms link + slots.
        await client.write_gatt_char(WR, b"W\x02", response=False)
        await asyncio.sleep(3)
        blob = b"".join(replies)
        print("device-list reply:", blob.hex(" ") if blob else "(none)", flush=True)
        if blob and blob[0] == 2:
            for i in range(1, len(blob) - 21, 22):
                raw = blob[i:i + 22]
                if not any(raw):
                    break
                name = raw[6:].split(b"\0")[0].decode(errors="replace")
                print(f"  slot {raw[0] & 15} type {raw[0] >> 4} "
                      f"code {int.from_bytes(raw[1:3], 'little'):#06x}  {name}", flush=True)
        if not fire:
            print("\nDRY RUN — no IR write sent. Re-run with --fire to emit one shot.", flush=True)
            return
        replies.clear()
        print(f"\n*** FIRING ONE W16: {payload.hex(' ')} ***", flush=True)
        await client.write_gatt_char(WR, payload, response=False)
        await asyncio.sleep(2)
        print("post-fire notify:", b"".join(replies).hex(" ") if replies else "(none)", flush=True)
    print("done.", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--address", default=ofa_identity.CONFIG_ADDR)
    ap.add_argument("--payload", default="57 16 02 05 00 00 00",
                    help="hex W16 frame (default: amp VOL+)")
    ap.add_argument("--fire", action="store_true", help="send the single W16 write for real")
    a = ap.parse_args()
    payload = bytes.fromhex(a.payload.replace(" ", ""))
    if payload[:2] != b"\x57\x16":
        sys.exit("refusing: payload must start with 57 16 (W16)")
    asyncio.run(run(a.address, payload, a.fire))


if __name__ == "__main__":
    main()
