#!/usr/bin/env python3
"""Fire a W16 payload N times in ONE BLE connection (for double-press modes)."""
import ofa_identity
import argparse, asyncio, sys
WR = "00001121-d102-11e1-9b23-00025b00a5a5"


async def run(address, payload, n, gap):
    from bleak import BleakClient
    async with BleakClient(address, timeout=25) as c:
        for i in range(n):
            await c.write_gatt_char(WR, payload, response=False)
            print(f"fired #{i+1}: {payload.hex(' ')}", flush=True)
            await asyncio.sleep(gap)
    print("done")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--address", default=ofa_identity.CONFIG_ADDR)
    ap.add_argument("--payload", required=True)
    ap.add_argument("--n", type=int, default=2)
    ap.add_argument("--gap", type=float, default=0.4)
    a = ap.parse_args()
    p = bytes.fromhex(a.payload.replace(" ", ""))
    if p[:2] != b"\x57\x16":
        sys.exit("refusing: payload must start with 57 16")
    asyncio.run(run(a.address, p, a.n, a.gap))


if __name__ == "__main__":
    main()
