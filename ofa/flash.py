#!/usr/bin/env python3
"""Version-pinned experimental OFA flasher. Default: offline plan, NO Bluetooth.

--execute is the destructive operation; obtain the owner's explicit flash
approval first. It writes only the APP image, then activates only after the
remote reports successful image validation. No automatic retry or resume.
"""
import ofa_identity
import argparse
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

from dfu import CP, DATA, encrypted_block, load_jni, parse_image_info, transfer_plan
from firmware import MP_SIZE, build_candidate, verify_image
from preflight import DFU_SERVICE, enter_ota, find_ota

EXPECTED_CAPS = bytes.fromhex("05 01 00 07 00 08 00 00 f0 0f 00 00")
EXPECTED_VERSION = bytes.fromhex("00 50 00 38")


def is_screensaver_main_candidate(image, stock):
    """Accept the reproducible screensaver main candidates (the additive W 0C forwarder
    built on spare-keys-v2): v1 = the INSTALLED image and the recovery image for any
    later main candidate (SS01, SHA 3b2e3240...), v2 = the optional relay build.  Each is
    reproduced from source and required to match byte-for-byte; anything else is refused,
    like every other candidate."""
    try:
        from blue_led_off_firmware import reproduce_main_v1
        if image == reproduce_main_v1(stock):
            return True
    except Exception:
        pass
    try:
        from screensaver_firmware import reproduce_main
        return image == reproduce_main(stock)
    except Exception:
        return False


def is_blue_led_off_candidate(image, stock):
    """Accept the reproducible "blue light bar off" main candidate: the installed main v1
    (spare-keys-v2 + the W 0C forwarder, both rebuilt from source and matched to the
    flashed SHA) with its four LED2 call sites NOP-ed and the Realtek digest recomputed.
    Reproduced from source and required to match byte-for-byte; anything else is refused."""
    try:
        from blue_led_off_firmware import reproduce_candidate
        return image == reproduce_candidate(stock)
    except Exception:
        return False


def is_touchscreen_hold_candidate(image, stock):
    """Accept only the in-place touchscreen edge-report patch rebuilt from stock."""
    try:
        from blue_led_off_firmware import reproduce_candidate
        from touchscreen_hold_main import build_inplace
        return image == build_inplace(reproduce_candidate(stock))
    except Exception:
        return False


def is_control_desk_keymap_candidate(image, stock):
    """Accept only the Control Desk's one-button type-4 offline remaps.

    This keeps the flasher's candidate allowlist narrow while allowing a user
    to change one physical HID output through ``ofa_control.py keys patch``.
    It proves the source is the current audited final candidate, one type-4
    matrix byte changes to an otherwise unused Consumer pool entry, only that
    entry's two usage bytes change, and all other changes are the Realtek hash.
    """
    from firmware import BUTTONS, POOL_BASE, ROWS, SHA_OFFSET
    from pause_playpause_firmware import build as build_pause_playpause_candidate

    base, _ = build_pause_playpause_candidate(stock)
    if len(image) != len(base):
        return False
    changed = {offset for offset, (before, after) in enumerate(zip(base, image)) if before != after}
    digest = set(range(SHA_OFFSET, SHA_OFFSET + 32))
    body = changed - digest
    matrix_positions = {ROWS[4] + index for index in BUTTONS}
    selected = body & matrix_positions
    if len(selected) != 1:
        return False
    matrix_position = selected.pop()
    pool_index = image[matrix_position]
    all_assigned = {base[row + index] for row in ROWS.values() for index in range(30)}
    if pool_index < 0x21 or pool_index >= 128 or pool_index in all_assigned:
        return False
    pool_positions = {POOL_BASE + pool_index * 2, POOL_BASE + pool_index * 2 + 1}
    if not body.issubset({matrix_position, *pool_positions}) or not body & pool_positions:
        return False
    try:
        verify_image(image)
    except ValueError:
        return False
    return True


class Control:
    def __init__(self, client, record):
        self.client, self.record = client, record
        self.queue = asyncio.Queue()

    async def subscribe(self):
        await self.client.start_notify(CP, lambda _, data: self.queue.put_nowait(bytes(data)))

    def check_notification(self, data):
        self.record("notification", value=data.hex(" "))
        if len(data) < 3 or data[0] != 0x10 or data[2] != 1:
            raise ValueError(f"DFU error/unrecognized notification: {data.hex(' ')}")

    def check_pending(self):
        while not self.queue.empty():
            self.check_notification(self.queue.get_nowait())

    async def command(self, data, *, reply=False, timeout=12):
        self.check_pending()
        self.record("command", value=data.hex(" "))
        await self.client.write_gatt_char(CP, data, response=True)
        if reply:
            async with asyncio.timeout(timeout):
                while True:
                    answer = await self.queue.get()
                    self.check_notification(answer)
                    if answer[1] == data[0]:
                        return answer


async def transfer(client, image, library, record, *, pace=0.01):
    """No mode entry here: require an already identified dedicated OTA target."""
    verify_image(image)
    wire = image[MP_SIZE:]
    payload = wire[12:]
    if len(payload) % 16:
        raise ValueError("Unsupported packet alignment")
    control = Control(client, record)
    await control.subscribe()
    current = parse_image_info(await control.command(bytes.fromhex("06 93 27"), reply=True))
    record("before_transfer", **current)
    if current["version"] != "0x38005000" or current["update_offset"] != 0:
        raise ValueError("Unexpected firmware or partial update; refusing automatic overwrite/resume")
    # Start DFU writes the first 12 image bytes. This is the first flash operation.
    answer = await control.command(b"\x01" + encrypted_block(library, wire[:12] + bytes(4)), reply=True)
    if answer != bytes.fromhex("10 01 01"):
        raise ValueError("Unexpected Start DFU reply")
    await control.command(bytes.fromhex("02 93 27 0c 00 00 00"))
    await asyncio.sleep(pace)
    # Do not enable buffer-check mode (09): use the SDK's ordinary transfer path.
    # Small packets fit ATT MTU 23 and each carries exactly one encrypted block.
    for offset in range(0, len(payload), 16):
        control.check_pending()
        await client.write_gatt_char(DATA, encrypted_block(library, payload[offset:offset + 16]),
                                     response=False)
        if offset % 4096 == 0 or offset + 16 == len(payload):
            record("transfer_progress", sent=offset + 16, total=len(payload))
        await asyncio.sleep(pace)
    # The successful BLE24 hardware run took about 64 seconds from validation
    # request to reply. Keep a bounded margin for flash verification latency.
    answer = await control.command(bytes.fromhex("03 93 27"), reply=True, timeout=120)
    if answer != bytes.fromhex("10 03 01"):
        raise ValueError("Unexpected image validation reply; refusing activation")
    record("image_validated", image_sha256=verify_image(image)["sha256"])
    # Firmware will reset the link, which can interrupt the GATT write response.
    record("activation_requested", value="04")
    try:
        await control.command(b"\x04")
    except Exception as error:
        record("activation_write_result", message=str(error),
               warning="Activation outcome unknown until the remote boots and is checked")
    record("transfer_complete", boot_and_buttons_verified=False)


async def execute(args, image, library, record):
    from bleak import BleakClient

    async def check(client):
        values = {}
        for name, identifier in (("battery", "2a19"), ("capabilities", "fff1"), ("app_version", "ffd4")):
            uuid = f"0000{identifier}-0000-1000-8000-00805f9b34fb"
            values[name] = bytes(await client.read_gatt_char(uuid))
        record("normal_capabilities", **{name: value.hex(" ") for name, value in values.items()})
        if (values["capabilities"] != EXPECTED_CAPS or values["app_version"] != EXPECTED_VERSION
                or len(values["battery"]) != 1 or values["battery"][0] < 50):
            raise ValueError("Capability/version/battery preconditions not met")

    if args.already_ota:
        # Only for a separately confirmed OTA target; still require matching MAC
        # in its manufacturer data and zero pending offset before any flash write.
        device = await find_ota(args.address, record)
    else:
        device = await enter_ota(args.address, record, check)
    async with BleakClient(device, timeout=25) as client:
        services = {service.uuid for service in client.services}
        if DFU_SERVICE not in services or len(services) != 3:
            raise ValueError("Expected the dedicated three-service OTA environment")
        await transfer(client, image, library, record)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path)
    parser.add_argument("--stock", required=True, type=Path)
    parser.add_argument("--apk", required=True, type=Path)
    parser.add_argument("--address", default=ofa_identity.CONFIG_ADDR)
    parser.add_argument("--execute", action="store_true", help="actually flash and activate; explicit approval required")
    parser.add_argument("--already-ota", action="store_true", help="target an already confirmed OTA advertisement")
    parser.add_argument("--log", type=Path)
    args = parser.parse_args()
    stock = args.stock.read_bytes()
    candidate, _ = build_candidate(stock)
    image = args.image.read_bytes()
    if image not in (stock, candidate):
        from custom_firmware import build_custom_candidate
        custom, _ = build_custom_candidate(stock)
        if image != custom:
            from laptop_pairing_firmware import build as build_laptop_pairing_candidate
            laptop_pairing, _ = build_laptop_pairing_candidate(custom)
            if image != laptop_pairing:
                from laptop_pairing_v2_firmware import build as build_laptop_pairing_v2_candidate
                laptop_pairing_v2, _ = build_laptop_pairing_v2_candidate(custom)
                if image != laptop_pairing_v2:
                    from laptop_pairing_v3_firmware import build as build_laptop_pairing_v3_candidate
                    laptop_pairing_v3, _ = build_laptop_pairing_v3_candidate(custom)
                    if image != laptop_pairing_v3:
                        from laptop_ble_map_firmware import build as build_laptop_ble_map_candidate
                        laptop_ble_map, _ = build_laptop_ble_map_candidate(custom)
                        if image != laptop_ble_map:
                            from laptop_pairing_v5_firmware import build as build_laptop_pairing_v5_candidate
                            laptop_pairing_v5, _ = build_laptop_pairing_v5_candidate(custom)
                            if image != laptop_pairing_v5:
                                from channel_swap_firmware import build as build_channel_swap_candidate
                                channel_swap, _ = build_channel_swap_candidate(stock)
                                if image != channel_swap:
                                    from type4_parity_firmware import build as build_type4_parity_candidate
                                    type4_parity, _ = build_type4_parity_candidate(stock)
                                    if image != type4_parity:
                                        from pause_playpause_firmware import build as build_pause_playpause_candidate
                                        pause_playpause, _ = build_pause_playpause_candidate(stock)
                                        if image != pause_playpause:
                                            from spare_keys_firmware import REVISIONS, build as build_spare_keys_candidate
                                            spare_keys = [build_spare_keys_candidate(stock, r)[0] for r in REVISIONS]
                                            if (image not in spare_keys and not is_control_desk_keymap_candidate(image, stock)
                                                    and not is_screensaver_main_candidate(image, stock)
                                                    and not is_blue_led_off_candidate(image, stock)
                                                    and not is_touchscreen_hold_candidate(image, stock)):
                                                parser.error("Only stock, BLE24, reproducible custom-touch, or audited type-4/laptop candidates are supported")
    library = load_jni(args.apk)
    plan = transfer_plan(image, library)
    if not args.execute:
        print(json.dumps(plan, indent=2))
        return
    if not args.log:
        parser.error("--execute requires a new --log path")
    result = {"plan": plan, "events": [], "status": "started"}
    with args.log.open("x") as output:
        json.dump(result, output)

    def save():
        args.log.write_text(json.dumps(result, indent=2) + "\n")

    def record(event, **data):
        entry = {"time": datetime.now(timezone.utc).isoformat(), "event": event, **data}
        result["events"].append(entry)
        print(json.dumps(entry), flush=True)
        save()

    try:
        asyncio.run(execute(args, image, library, record))
        result["status"] = "transfer finished; boot and buttons must be checked"
    except BaseException as error:
        result["status"] = "interrupted; inspect log before any further writes"
        result["error"] = repr(error)
        # Deliberately no automatic activation, reset, retry, or resume.
        raise
    finally:
        save()


if __name__ == "__main__":
    main()
