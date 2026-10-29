#!/usr/bin/env python3
"""Read OFA identification and OTA capabilities; never write a characteristic.

Run with ~/.cache/ofa/venv/bin/python. Hold ACTIVITIES+DEVICES and disconnect
the phone first. Output contains only this remote's GATT service metadata and
an explicit allowlist of identification/capability values.
"""
import ofa_identity
import argparse
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path


async def inspect(address):
    from bleak import BleakClient

    result = {"address": address, "time": datetime.now(timezone.utc).isoformat(),
              "services": [], "values": {}}
    readable = {0x2A19, 0x2A24, 0x2A25, 0x2A26, 0x2A27, 0x2A28, 0x2A29,
                0xFFD2, 0xFFD3, 0xFFD4, 0xFFD5, 0xFFF1, 0xFFF3,
                *range(0xFFE0, 0xFFF0)}
    async with BleakClient(address, timeout=25) as client:
        for service in client.services:
            entry = {"uuid": service.uuid, "characteristics": []}
            result["services"].append(entry)
            for char in service.characteristics:
                entry["characteristics"].append({"uuid": char.uuid,
                    "handle": char.handle, "properties": char.properties})
                if ("read" in char.properties
                        and char.uuid.endswith("-0000-1000-8000-00805f9b34fb")
                        and int(char.uuid[:8], 16) in readable):
                    try:
                        data = bytes(await client.read_gatt_char(char))
                        value = {"hex": data.hex(" ")}
                        result["values"][char.uuid] = value
                        print(f"{char.uuid}: {data.hex(' ')}", flush=True)
                    except Exception as error:
                        result["values"][char.uuid] = {"error": str(error)}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--address", default=ofa_identity.CONFIG_ADDR)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = asyncio.run(inspect(args.address))
    # Refuse to overwrite an earlier capture.
    with args.output.open("x") as output:
        json.dump(result, output, indent=2)
        output.write("\n")
    print(f"Saved {args.output}")


if __name__ == "__main__":
    main()
