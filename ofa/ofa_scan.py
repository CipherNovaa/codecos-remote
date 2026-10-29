#!/usr/bin/env python3
"""Fire W16 for a list/range of keyids (one slot) in ONE connection, gap between each.
Emits real IR for VALID ids (visible on the device); invalid ids are safe no-ops."""
import ofa_identity
import argparse, asyncio, sys
WR = "00001121-d102-11e1-9b23-00025b00a5a5"


async def run(address, slot, ids, gap):
    from bleak import BleakClient
    async with BleakClient(address, timeout=25) as c:
        for kid in ids:
            await c.write_gatt_char(WR, bytes([0x57, 0x16, slot, kid, 0, 0, 0]), response=False)
            print(f"fired {kid:#04x}", flush=True)
            await asyncio.sleep(gap)
    print("done")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--address", default=ofa_identity.CONFIG_ADDR)
    ap.add_argument("--slot", type=lambda x: int(x, 0), default=2)
    ap.add_argument("--ids", required=True, help="comma list and/or lo-hi ranges, hex")
    ap.add_argument("--gap", type=float, default=1.0)
    a = ap.parse_args()
    ids = []
    for tok in a.ids.split(","):
        if "-" in tok:
            lo, hi = tok.split("-")
            ids += list(range(int(lo, 16), int(hi, 16) + 1))
        else:
            ids.append(int(tok, 16))
    asyncio.run(run(a.address, a.slot, ids, a.gap))


if __name__ == "__main__":
    main()
