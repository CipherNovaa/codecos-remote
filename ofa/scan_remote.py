#!/usr/bin/env python3
"""Record advertisements belonging to this OFA remote only."""
import ofa_identity
import argparse
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path


async def scan(output, seconds):
    from bleak import BleakScanner
    seen = {}
    mac = bytes.fromhex(ofa_identity.CONFIG_ADDR.replace(":", ""))

    def callback(device, adv):
        if not (device.address.upper().startswith(ofa_identity.prefix()) or
                any(value in (mac, mac[::-1]) for value in adv.manufacturer_data.values())):
            return
        item = {"time": datetime.now(timezone.utc).isoformat(), "address": device.address,
                "name": adv.local_name, "rssi": adv.rssi, "services": adv.service_uuids,
                "manufacturer": {str(k): v.hex(" ") for k, v in adv.manufacturer_data.items()}}
        seen[device.address] = item
        output.write(json.dumps(item) + "\n")
        print(json.dumps(item), flush=True)

    async with BleakScanner(detection_callback=callback):
        await asyncio.sleep(seconds)
    print(f"Scan complete: {len(seen)} OFA identities", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--seconds", type=float, default=20)
    args = parser.parse_args()
    with args.output.open("x", buffering=1) as output:
        asyncio.run(scan(output, args.seconds))


if __name__ == "__main__":
    main()
