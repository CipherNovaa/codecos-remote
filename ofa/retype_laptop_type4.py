#!/usr/bin/env python3
"""Retarget only the unpaired LAPTOP slot to the established type-4 BLE profile."""
import ofa_identity
import argparse
import asyncio
import json
from pathlib import Path

from read_config import WR, read


def laptop(records):
    matches = [r for r in records if r["slot"] == 4]
    if len(matches) != 1 or matches[0]["type"] != 3 or matches[0]["code"] != "0x2707" or matches[0]["name"] != "LAPTOP":
        raise ValueError("LAPTOP slot is not the expected unpaired type-3 record")
    return matches[0]


async def change(address, log):
    before = await read(address)
    old = laptop(before["records"])
    desktop = [r for r in before["records"] if r["slot"] == 1]
    if len(desktop) != 1 or desktop[0]["raw"] != "41 0b 27 80 00 05 44 45 53 4b 54 4f 50 00 00 00 00 00 00 00 00 00":
        raise ValueError("DESKTOP record differs; refusing LAPTOP write")
    command = b"W\x14" + bytes.fromhex("04 40 80 27 0b 00 05") + b"LAPTOP".ljust(16, b"\0")
    async with __import__("bleak").BleakClient(address, timeout=25) as client:
        await client.write_gatt_char(WR, command, response=False)
        await asyncio.sleep(1)
    after = await read(address)
    changed = [r for r in after["records"] if r["slot"] == 4]
    if len(changed) != 1 or changed[0]["type"] != 4 or changed[0]["code"] != "0x270b" or changed[0]["name"] != "LAPTOP":
        raise ValueError("LAPTOP type-4 retarget was not reflected")
    if [r for r in after["records"] if r["slot"] != 4] != [r for r in before["records"] if r["slot"] != 4]:
        raise ValueError("Another record changed unexpectedly")
    log.write_text(json.dumps({"before": before, "command": command.hex(" "), "after": after}, indent=2) + "\n")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--address", default=ofa_identity.CONFIG_ADDR)
    p.add_argument("--log", required=True, type=Path)
    a = p.parse_args()
    if a.log.exists():
        raise FileExistsError(a.log)
    asyncio.run(change(a.address, a.log))
    print(f"LAPTOP is now type 4; verified {a.log}")


if __name__ == "__main__":
    main()
