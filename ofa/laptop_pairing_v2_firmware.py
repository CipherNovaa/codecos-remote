#!/usr/bin/env python3
"""Build the URC7985 LAPTOP pairing runtime-gate candidate, offline only.

The installed base is the pinned custom-touch-v2 image.  The patch removes
one early exit from the completed Vol+ + OK gesture handler.  The existing
Bluetooth-profile check and the existing pairing routine remain in place.
"""
import argparse
import hashlib
import json
from pathlib import Path

from firmware import image_digest, verify_image

BASE_SHA256 = "1f5db4d75778e930f5c4b9653a2121a1991d8fada0d13219a584f78504364e64"
PATCH_OFFSET = 0xF72C
BEFORE = bytes.fromhex("14 d0")  # beq 0x81d558 when runtime flag is zero
AFTER = bytes.fromhex("00 bf")   # nop: continue to the existing type-4 check
DIGEST_RANGE = range(0x200 + 372, 0x200 + 404)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def build(image):
    verify_image(image)
    if digest(image) != BASE_SHA256:
        raise ValueError("Input is not the exact installed custom-touch-v2 image")
    if image[PATCH_OFFSET:PATCH_OFFSET + len(BEFORE)] != BEFORE:
        raise ValueError("Runtime pairing-gate bytes do not match the audited 0.5.7 image")
    out = bytearray(image)
    out[PATCH_OFFSET:PATCH_OFFSET + len(AFTER)] = AFTER
    out[0x200 + 372:0x200 + 404] = image_digest(out)
    verified = verify_image(out)
    changes = [
        {"offset": hex(n), "before": f"{before:02x}", "after": f"{after:02x}"}
        for n, (before, after) in enumerate(zip(image, out)) if before != after
    ]
    allowed = {PATCH_OFFSET, PATCH_OFFSET + 1, *DIGEST_RANGE}
    if any(int(change["offset"], 16) not in allowed for change in changes):
        raise AssertionError("Unexpected byte changed outside runtime gate or image digest")
    return bytes(out), {
        "status": "OFFLINE CANDIDATE; NOT FLASHED OR LIVE VERIFIED",
        "base_sha256": BASE_SHA256,
        "candidate": verified,
        "scope": "Vol+ + OK completion only: bypasses its Desktop-only runtime flag, then retains the existing type-4 Bluetooth pairing check and pairing routine.",
        "machine_code": {
            "offset": hex(PATCH_OFFSET), "before": BEFORE.hex(" "), "after": AFTER.hex(" "),
            "effect": "a zero runtime flag no longer exits before the existing type-4 Bluetooth pairing branch",
        },
        "changes": changes,
        "preserved": ["profile records", "Bluetooth bond storage", "screen firmware", "BLE key-map rows", "existing type-4 Desktop behavior", "appended touch/label code"],
        "limitations": ["No dual-bank rollback is available for the main APP image.", "Offline validation cannot prove live pairing or host switching."],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    out, manifest = build(args.image.read_bytes())
    args.output_dir.mkdir(parents=True, exist_ok=False)
    (args.output_dir / "base-custom-touch-v2.bin").write_bytes(args.image.read_bytes())
    (args.output_dir / "candidate-0.5.7-laptop-pairing-v2.bin").write_bytes(out)
    (args.output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
