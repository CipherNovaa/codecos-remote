#!/usr/bin/env python3
"""Save raw device-list replies; the only command is read-list W 02."""
import ofa_identity
import argparse
import asyncio
import json
from pathlib import Path

WR = "00001121-d102-11e1-9b23-00025b00a5a5"
NT = "00001122-d102-11e1-9b23-00025b00a5a5"


async def read(address):
    from bleak import BleakClient
    replies = []
    async with BleakClient(address, timeout=25) as client:
        await client.start_notify(NT, lambda _, data: replies.append(bytes(data)))
        await client.write_gatt_char(WR, b"W\x02", response=False)
        await asyncio.sleep(3)
    blob = b"".join(replies)
    if not blob or blob[0] != 2:
        raise ValueError(f"Unexpected device-list format: {blob.hex(' ')}")
    records = []
    # This response also contains padding and an undocumented trailer. Preserve
    # every byte, and only interpret the contiguous nonempty 22-byte records.
    for index in range(1, len(blob) - 21, 22):
        raw = blob[index:index + 22]
        if not any(raw):
            break
        records.append({"record_index": len(records), "slot": raw[0] & 15, "type": raw[0] >> 4,
                        "code": hex(int.from_bytes(raw[1:3], "little")),
                        "name": raw[6:].split(b"\0")[0].decode(errors="replace"),
                        "raw": raw.hex(" ")})
    return {"address": address, "command": "57 02", "replies": [r.hex(" ") for r in replies],
            "records": records, "scope": "Device list only; not a full settings/learned-IR backup"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--address", default=ofa_identity.CONFIG_ADDR)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    # Reserve output before accessing hardware.
    with args.output.open("x") as output:
        output.write("{}\n")
    result = asyncio.run(read(args.address))
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
