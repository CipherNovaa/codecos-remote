#!/usr/bin/env python3
"""Offline, version-pinned OFA URC7985 firmware inspection and candidate builder.

No Bluetooth access. No firmware is bundled. The input is the user's APK asset.
The candidate is experimental until each physical button has been tested.
"""
import argparse
import hashlib
import json
import struct
from pathlib import Path

STOCK_SHA256 = "2802d95568ecd78fbe8ec318508d6e6e807b8fb70ff15ee52698dd31239b5f47"
MP_SIZE = 0x200
IMAGE_HEADER_SIZE = 0x400
SHA_OFFSET = MP_SIZE + 372
POOL_BASE = 0x1641E  # index 0 is the release sentinel; entries start at 0x16420
ROWS = {1: 0x163A6, 2: 0x163C4, 3: 0x163E2, 4: 0x16400}
PHYSICAL_BASE = 0x1636A
LOGICAL_BASE = 0x16388
# Matrix indices are row * 6 + column, proven by lookup at file offset 0xf62e.
# Added button labels are inferred from the parallel UEI logical-key matrix.
# They must be checked against the physical remote under an exclusive key grab.
BUTTONS = {
    1: "Play", 2: "OK", 4: "Record", 6: "Mic", 7: "Stop", 8: "Up",
    9: "Mute", 10: "FastForward", 11: "ChannelDown", 12: "Back",
    13: "Rewind", 14: "Guide", 15: "Right", 16: "ChannelUp",
    18: "VolumeUp", 19: "VolumeDown", 20: "Down", 21: "Home",
    23: "Power", 24: "Left", 25: "Pause", 26: "Apps", 27: "Menu", 29: "Input",
}
NEW_KEYS = {4: 42, 7: 35, 10: 39, 11: 44, 13: 38, 14: 63,
            16: 43, 26: 57, 27: 66, 29: 64}
# These entries are unused by ALL four stock BLE profiles. Their consumer page
# comes from index >= 0x21, not from the numerical value of the HID usage.
NEW_USAGES = {38: 0xB4, 39: 0xB3, 42: 0xB2, 43: 0x9C, 44: 0x9D,
              45: 0xB0, 46: 0xB1}


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def image_digest(data):
    """Realtek slient_dfu_check_sha256 coverage; includes three disjoint spans."""
    image = data[MP_SIZE:]
    return hashlib.sha256(image[12:372] + image[404:752] + image[1008:]).digest()


def verify_image(data):
    if len(data) < MP_SIZE + IMAGE_HEADER_SIZE:
        raise ValueError("Image is truncated")
    if data[:4] != bytes.fromhex("01 00 02 00"):
        raise ValueError("Not a supported 512-byte MP-header image")
    ic, secure, flags, image_id, crc, payload_len = struct.unpack_from("<BBHHHI", data, MP_SIZE)
    if ic != 5 or image_id != 0x2793 or crc != 0:
        raise ValueError("Expected an RTL8762C SHA-256 application image")
    if len(data) != MP_SIZE + IMAGE_HEADER_SIZE + payload_len:
        raise ValueError("Payload length disagrees with file size")
    if image_digest(data) != data[SHA_OFFSET:SHA_OFFSET + 32]:
        raise ValueError("Realtek image SHA-256 does not match")
    return {"sha256": sha256(data), "size": len(data), "image_id": hex(image_id),
            "payload_size": payload_len, "secure_version": secure,
            "flags": hex(flags), "encrypted_executable": bool(flags & 0xA),
            "realtek_sha256": image_digest(data).hex()}


def button_map(data):
    result = []
    for matrix_index, name in BUTTONS.items():
        index = data[ROWS[4] + matrix_index]
        usage = struct.unpack_from("<H", data, POOL_BASE + 2 * index)[0] if index else 0
        page = 0x07 if index < 0x21 else 0x0C
        result.append({"button": name, "matrix_index": matrix_index,
                       "logical_key": hex(data[LOGICAL_BASE + matrix_index]),
                       "pool_index": index, "page": hex(page), "usage": hex(usage),
                       "scan": hex(page << 16 | usage),
                       "label_evidence": "inferred; live test pending" if matrix_index in NEW_KEYS
                       else "stock profile / prior live notes"})
    return result


def build_candidate(stock):
    info = verify_image(stock)
    if info["encrypted_executable"]:
        raise ValueError("Patching encrypted executable images is unsupported")
    if sha256(stock) != STOCK_SHA256:
        raise ValueError("Patch offsets apply ONLY to the exact saved 0.5.7 APK asset")
    output = bytearray(stock)
    used = {index for base in ROWS.values() for index in stock[base:base + 30]}
    if used.intersection(NEW_USAGES):
        raise ValueError("A proposed pool entry is already used by a stock BLE profile")
    for matrix_index, pool_index in NEW_KEYS.items():
        if output[ROWS[4] + matrix_index] != 0:
            raise ValueError("A new key would overwrite an existing assignment")
        output[ROWS[4] + matrix_index] = pool_index
    for pool_index, usage in NEW_USAGES.items():
        struct.pack_into("<H", output, POOL_BASE + 2 * pool_index, usage)
    output[ROWS[4] + 1] = 45   # separate Play and Pause, previously the same usage
    output[ROWS[4] + 25] = 46
    # F13 in firmware: no dependency on a host-specific power-key hwdb rule.
    # Index 1 occurs only in Google TV among the four stock BLE profiles.
    struct.pack_into("<H", output, POOL_BASE + 2, 0x68)
    output[SHA_OFFSET:SHA_OFFSET + 32] = image_digest(output)
    verify_image(output)

    # Verify OTHER profile behavior, including the shared usage pool.
    for profile in (1, 2, 3):
        base = ROWS[profile]
        if output[base:base + 30] != stock[base:base + 30]:
            raise ValueError("Another BLE profile changed")
        for index in stock[base:base + 30]:
            if index and output[POOL_BASE + index * 2:POOL_BASE + index * 2 + 2] != stock[POOL_BASE + index * 2:POOL_BASE + index * 2 + 2]:
                raise ValueError("Another profile's shared HID usage changed")
    keys = button_map(output)
    if len({key["scan"] for key in keys}) != 24 or any(key["pool_index"] == 0 for key in keys):
        raise ValueError("Candidate does not have 24 distinct button reports")
    if any(output[ROWS[4] + i] for i in (0, 3, 5, 17, 22, 28)):
        raise ValueError("Setup buttons or unpopulated matrix cells changed")
    allowed = set(range(SHA_OFFSET, SHA_OFFSET + 32))
    allowed.update(ROWS[4] + i for i in (*NEW_KEYS, 1, 25))
    for index in (*NEW_USAGES, 1):
        allowed.update((POOL_BASE + index * 2, POOL_BASE + index * 2 + 1))
    changes = [{"offset": hex(i), "before": f"{a:02x}", "after": f"{b:02x}"}
               for i, (a, b) in enumerate(zip(stock, output)) if a != b]
    if any(int(change["offset"], 16) not in allowed for change in changes):
        raise ValueError("Unexpected change outside the allowlist")
    manifest = {"status": "EXPERIMENTAL — NOT FLASHED OR LIVE VERIFIED",
                "stock": verify_image(stock), "candidate": verify_image(output),
                "scope": "Google TV/type 4; 24 main buttons. Activities/Devices remain local setup controls.",
                "changes": changes, "buttons": keys,
                "limitations": ["New button labels need a physical mapping pass",
                                "No dual-bank rollback reported by this remote",
                                "Image integrity is not a guarantee of boot success",
                                "Codec input routing still needs integration and live testing"]}
    return bytes(output), manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    inspect = sub.add_parser("inspect")
    inspect.add_argument("image", type=Path)
    build = sub.add_parser("build")
    build.add_argument("image", type=Path)
    build.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    data = args.image.read_bytes()
    if args.command == "inspect":
        print(json.dumps(verify_image(data), indent=2))
        return
    output, manifest = build_candidate(data)
    # Reproducible bundle, never replace an earlier result or the source asset.
    args.output_dir.mkdir(parents=True, exist_ok=False)
    (args.output_dir / "stock-0.5.7.bin").write_bytes(data)
    (args.output_dir / "candidate-0.5.7-ble24.bin").write_bytes(output)
    (args.output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"Built experimental candidate: {len(manifest['changes'])} changed bytes; 24 distinct reports")
    print(f"SHA-256: {manifest['candidate']['sha256']}")
    print(f"Bundle: {args.output_dir}")


if __name__ == "__main__":
    main()
