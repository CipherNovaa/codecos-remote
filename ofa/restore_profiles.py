#!/usr/bin/env python3
"""Restore only the CODEC/FIRE records specified in the user's handoff."""
import ofa_identity
import argparse
import asyncio
import json
from pathlib import Path

from read_config import WR, read


async def restore(address, record):
    from bleak import BleakClient
    before = await read(address)
    record("before", capture=before)
    for slot, name in ((1, "CODEC"), (4, "FIRE")):
        matches = [entry for entry in before["records"] if entry["slot"] == slot]
        if not matches or any(entry["name"] != name for entry in matches):
            raise ValueError(f"Unexpected slot {slot}; refusing configuration writes")
    async with BleakClient(address, timeout=25) as client:
        for fields, name in (("01 40 80 27 0b 00 05", "CODEC"),
                             ("04 30 80 27 07 00 01", "FIRE")):
            command = b"W\x14" + bytes.fromhex(fields) + name.encode().ljust(16, b"\0")
            record("restore", command=command.hex(" "), name=name)
            await client.write_gatt_char(WR, command, response=False)
            await asyncio.sleep(1)
    after = await read(address)
    record("after", capture=after)
    for slot, name, profile, code in ((1, "CODEC", 4, "0x270b"), (4, "FIRE", 3, "0x2707")):
        matches = [entry for entry in after["records"] if entry["slot"] == slot]
        if not any(entry["name"] == name and entry["type"] == profile and entry["code"] == code
                   for entry in matches):
            raise ValueError(f"Slot {slot} restore not reflected in device-list response")
    sony_before = [entry["raw"] for entry in before["records"] if entry["slot"] in (2, 3)]
    sony_after = [entry["raw"] for entry in after["records"] if entry["slot"] in (2, 3)]
    if sony_before != sony_after:
        raise ValueError("Sony records changed unexpectedly")
    record("verified", sony_records_unchanged=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--address", default=ofa_identity.CONFIG_ADDR)
    parser.add_argument("--log", type=Path, required=True)
    args = parser.parse_args()
    events = []
    with args.log.open("x") as output:
        output.write("[]\n")

    def record(event, **data):
        events.append({"event": event, **data})
        args.log.write_text(json.dumps(events, indent=2) + "\n")
        print(event, json.dumps(data) if event not in ("before", "after") else
              [(r["slot"], r["name"], r["type"], r["code"]) for r in data["capture"]["records"]], flush=True)

    asyncio.run(restore(args.address, record))


if __name__ == "__main__":
    main()
