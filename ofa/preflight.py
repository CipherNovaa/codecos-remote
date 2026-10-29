#!/usr/bin/env python3
"""Temporarily enter OFA OTA mode, query image state, and reset without flashing.

The only permitted writes are FFD1=01 (enter OTA), CP=069327 (query),
and CP=05 (system reset). No Start DFU, image bytes, or activation command.
The discovery match uses the device MAC read from FFD2, as the OFA APK does.
"""
import ofa_identity
import argparse
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

from dfu import CP, parse_image_info

ENTRY = "0000ffd1-0000-1000-8000-00805f9b34fb"
MAC = "0000ffd2-0000-1000-8000-00805f9b34fb"
MODEL = "00002a24-0000-1000-8000-00805f9b34fb"
DFU_SERVICE = "00006287-3c17-d293-8e48-14fe2e4da212"


async def enter_ota(address, record, check=None):
    from bleak import BleakClient
    async with BleakClient(address, timeout=25) as client:
        model = bytes(await client.read_gatt_char(MODEL)).rstrip(b"\0")
        mac = bytes(await client.read_gatt_char(MAC))
        if model != b"URC7985 R00" or mac != bytes.fromhex(address.replace(":", "")):
            raise ValueError("Remote identity mismatch; no writes performed")
        record("normal_identity", model=model.decode(), mac=mac.hex(" "))
        if check is not None:
            await check(client)
        # Writes this single mode-switch byte only, never firmware.
        record("enter_ota_request", characteristic=ENTRY, value="01")
        await client.write_gatt_char(ENTRY, b"\x01", response=False)
        await asyncio.sleep(1)
    return await find_ota(address, record)


async def find_ota(address, record):
    from bleak import BleakScanner
    mac = bytes.fromhex(address.replace(":", ""))
    matched = asyncio.get_running_loop().create_future()

    def advertisement(device, adv):
        manufacturer_match = any(value in (mac, mac[::-1])
                                 for value in adv.manufacturer_data.values())
        # A matching normal address is recorded but not assumed to be OTA mode.
        if manufacturer_match or device.address.upper() == address.upper():
            record("advertisement", address=device.address, name=adv.local_name,
                   services=adv.service_uuids,
                   manufacturer={str(k): v.hex(" ") for k, v in adv.manufacturer_data.items()})
        if manufacturer_match and not matched.done():
            matched.set_result(device)

    async with BleakScanner(detection_callback=advertisement):
        device = await asyncio.wait_for(matched, timeout=30)
    record("ota_target", address=device.address)
    return device


async def inspect_mode(address, result, save, *, already_ota=False):
    from bleak import BleakClient

    def record(event, **data):
        entry = {"time": datetime.now(timezone.utc).isoformat(), "event": event, **data}
        result["events"].append(entry)
        print(json.dumps(entry), flush=True)
        save()

    device = await find_ota(address, record) if already_ota else await enter_ota(address, record)
    async with BleakClient(device, timeout=25) as client:
        services = [service.uuid for service in client.services]
        record("ota_services", services=services)
        if DFU_SERVICE not in services:
            raise ValueError("Matched device lacks DFU service; refusing control writes")
        queue = asyncio.Queue()
        await client.start_notify(CP, lambda _, data: queue.put_nowait(bytes(data)))
        try:
            record("query", value="06 93 27")
            await client.write_gatt_char(CP, bytes.fromhex("06 93 27"), response=True)
            async with asyncio.timeout(8):
                while True:
                    response = await queue.get()
                    record("notification", value=response.hex(" "))
                    if response[:2] == bytes.fromhex("10 06"):
                        result["image"] = parse_image_info(response)
                        save()
                        break
        finally:
            record("system_reset_request", value="05")
            try:
                await client.write_gatt_char(CP, b"\x05", response=True)
                await asyncio.sleep(3)
            except Exception as error:
                record("reset_write_result", message=str(error))
    record("probe_complete", firmware_sent=False,
           return_to_normal="must be verified separately with read_device.py")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--address", default=ofa_identity.CONFIG_ADDR)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--already-ota", action="store_true",
                        help="query/reset an already-confirmed OTA advertisement; do not enter OTA")
    args = parser.parse_args()
    result = {"address": args.address, "events": [], "firmware_sent": False}
    # Reserve before touching the remote and retain partial logs on any failure.
    with args.output.open("x") as output:
        json.dump(result, output)

    def save():
        args.output.write_text(json.dumps(result, indent=2) + "\n")

    try:
        asyncio.run(inspect_mode(args.address, result, save, already_ota=args.already_ota))
    except Exception as error:
        result["error"] = str(error)
        save()
        raise
    save()


if __name__ == "__main__":
    main()
