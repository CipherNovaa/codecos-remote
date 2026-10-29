#!/usr/bin/env python3
"""OFA update investigation: offline transfer plan and non-flashing image query.

This tool deliberately has no flash/activate operation yet. The query sends
only Realtek Report Target Image Information (06 93 27), which reads update
state. No Start DFU, firmware packets, reset, or activation commands are sent.
"""
import ofa_identity
import argparse
import asyncio
import hashlib
import json
import struct
import zipfile
from pathlib import Path

from firmware import MP_SIZE, verify_image

CP = "00006487-3c17-d293-8e48-14fe2e4da212"
DATA = "00006387-3c17-d293-8e48-14fe2e4da212"
JNI_SHA256 = "abc8aa12ad1591efac9fc09f1978e4c2e05b40e6bd4e7abfff3f0958fcbd3a3d"


def load_jni(apk):
    with zipfile.ZipFile(apk) as archive:
        library = archive.read("lib/arm64-v8a/libRtkAesJni.so")
    if hashlib.sha256(library).hexdigest() != JNI_SHA256:
        raise ValueError("Unrecognized JNI library; embedded-key offset must be re-audited")
    return library


def encrypted_block(library, block):
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    if len(block) != 16:
        raise ValueError("Exactly one AES block required")
    # Q1.c leaves the key null; aesInit(3, null) uses the 32-byte key at 0x690.
    encryptor = Cipher(algorithms.AES(library[0x690:0x6B0]), modes.ECB()).encryptor()
    return encryptor.update(block) + encryptor.finalize()


def transfer_plan(image, library):
    info = verify_image(image)
    wire = image[MP_SIZE:]
    payload = wire[12:]
    if len(payload) % 16:
        raise ValueError("This provisional plan requires an integral number of 16-byte packets")
    start = b"\x01" + encrypted_block(library, wire[:12] + bytes(4))
    ciphertext = b"".join(encrypted_block(library, payload[i:i + 16])
                          for i in range(0, len(payload), 16))
    return {"status": "OFFLINE PLAN — NO TRANSFER PERFORMED", "image": info,
            "image_id": "0x2793", "transport": "AES-256 ECB; 16-byte data packets",
            "skip_mp_header_bytes": MP_SIZE, "start_command": start.hex(" "),
            "receive_command": "02 93 27 0c 00 00 00",
            "data_bytes": len(payload), "data_packets": len(payload) // 16,
            "ciphertext_sha256": hashlib.sha256(ciphertext).hexdigest(),
            "validate_command": "03 93 27", "activate_command": "04",
            "unresolved": ["No live Start DFU/data/validate/activate transaction tested",
                           "Boot and physical button behavior require hardware testing",
                           "Stock image is available; no guaranteed recovery after a bad boot"],
            "live_preflight": "2026-09-19: OTA entry, image query and reset verified; offset 0"}


def parse_image_info(response):
    if response == bytes.fromhex("10 06 09"):
        raise ValueError("DFU query rejected: system state error (0x09); no image data sent")
    if len(response) not in (9, 11, 13) or response[:3] != bytes.fromhex("10 06 01"):
        raise ValueError(f"Unexpected image-information response: {response.hex(' ')}")
    version, offset = struct.unpack_from("<HI" if len(response) == 9 else "<II", response, 3)
    result = {"version": hex(version), "update_offset": offset, "raw": response.hex(" ")}
    if len(response) == 13:
        result["buffer_check_offset"] = struct.unpack_from("<H", response, 11)[0]
    return result


async def probe(address):
    from bleak import BleakClient

    async with BleakClient(address, timeout=25) as client:
        queue = asyncio.Queue()
        await client.start_notify(CP, lambda _, data: queue.put_nowait(bytes(data)))
        print("Querying image state only: 06 93 27", flush=True)
        await client.write_gatt_char(CP, bytes.fromhex("06 93 27"), response=True)
        async with asyncio.timeout(8):
            while True:
                data = await queue.get()
                print(f"Reply: {data.hex(' ')}", flush=True)
                if data[:2] == bytes.fromhex("10 06"):
                    return parse_image_info(data)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    plan = sub.add_parser("plan")
    plan.add_argument("image", type=Path)
    plan.add_argument("--apk", type=Path, required=True)
    query = sub.add_parser("probe")
    query.add_argument("--address", default=ofa_identity.CONFIG_ADDR)
    args = parser.parse_args()
    if args.command == "plan":
        result = transfer_plan(args.image.read_bytes(), load_jni(args.apk))
    else:
        result = asyncio.run(probe(args.address))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
