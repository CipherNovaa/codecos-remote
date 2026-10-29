#!/usr/bin/env python3
"""Build the narrow URC7985 LAPTOP pairing-gate candidate, offline only.

The installed main image is the exact custom-touch-v2 candidate pinned below.
This changes the selected-profile test in the pairing-state handler from
``type == 4`` to ``3 <= type <= 4``.  It does not alter profile records,
bond storage, the screen firmware, key tables, or appended custom code.
"""
import argparse
import hashlib
import json
from pathlib import Path

from firmware import image_digest, verify_image

BASE_SHA256 = "1f5db4d75778e930f5c4b9653a2121a1991d8fada0d13219a584f78504364e64"
PATCH_OFFSET = 0xF73A
BEFORE = bytes.fromhex("04 28 08 d0 0a e0")
# subs r0,#3; cmp r0,#1; bls 0x81d550
# Original type values 3 and 4 take the existing BLE-action branch.
AFTER = bytes.fromhex("03 38 01 28 07 d9")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def build(image):
    verify_image(image)
    if digest(image) != BASE_SHA256:
        raise ValueError("Input is not the exact installed custom-touch-v2 image")
    if image[PATCH_OFFSET:PATCH_OFFSET + len(BEFORE)] != BEFORE:
        raise ValueError("Pairing gate bytes do not match the audited 0.5.7 image")
    out = bytearray(image)
    out[PATCH_OFFSET:PATCH_OFFSET + len(AFTER)] = AFTER
    # Main APP's Realtek digest covers payload data but excludes its digest field.
    out[0x200 + 372:0x200 + 404] = image_digest(out)
    verified = verify_image(out)
    changes = [
        {"offset": hex(n), "before": f"{before:02x}",
         "after": f"{after:02x}"}
        for n, (before, after) in enumerate(zip(image, out)) if before != after
    ]
    allowed = set(range(PATCH_OFFSET, PATCH_OFFSET + len(AFTER)))
    allowed.update(range(0x200 + 372, 0x200 + 404))
    if any(int(change["offset"], 16) not in allowed for change in changes):
        raise AssertionError("Unexpected byte changed outside pairing gate or image digest")
    return bytes(out), {
        "status": "OFFLINE CANDIDATE; NOT FLASHED OR LIVE VERIFIED",
        "base_sha256": BASE_SHA256,
        "candidate": verified,
        "scope": "Pairing-state gate only: types 3 (LAPTOP) and 4 (DESKTOP) enter the existing BLE action.",
        "machine_code": {
            "offset": hex(PATCH_OFFSET), "before": BEFORE.hex(" "),
            "after": AFTER.hex(" "),
            "effect": "types 3 and 4 branch to existing 0x81d550; other types retain the exit path",
        },
        "changes": changes,
        "preserved": ["profile records", "Bluetooth bond storage", "screen firmware",
                      "BLE key-map rows", "existing type-4 Desktop behavior", "appended touch/label code"],
        "limitations": [
            "This enables the existing pairing action for LAPTOP; it cannot prove that the radio has capacity for two simultaneous host bonds.",
            "No dual-bank rollback is available for the main APP image.",
            "Offline validation cannot prove live boot, pairing, or host switching.",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    out, manifest = build(args.image.read_bytes())
    args.output_dir.mkdir(parents=True, exist_ok=False)
    (args.output_dir / "base-custom-touch-v2.bin").write_bytes(args.image.read_bytes())
    (args.output_dir / "candidate-0.5.7-laptop-pairing.bin").write_bytes(out)
    (args.output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
