#!/usr/bin/env python3
"""Give the URC7985 LAPTOP (profile 3) the audited DESKTOP BLE key map."""
import argparse
import hashlib
import json
from pathlib import Path

from firmware import ROWS, image_digest, verify_image

BASE_SHA256 = "1f5db4d75778e930f5c4b9653a2121a1991d8fada0d13219a584f78504364e64"
LAPTOP_ROW = ROWS[3]
DESKTOP_ROW = ROWS[4]
ROW_SIZE = 30
DIGEST_RANGE = range(0x200 + 372, 0x200 + 404)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def build(image):
    verify_image(image)
    if digest(image) != BASE_SHA256:
        raise ValueError("Input is not the exact known-good custom-touch-v2 image")
    desktop = image[DESKTOP_ROW:DESKTOP_ROW + ROW_SIZE]
    laptop = image[LAPTOP_ROW:LAPTOP_ROW + ROW_SIZE]
    if laptop == desktop:
        raise ValueError("LAPTOP row already equals DESKTOP row")
    out = bytearray(image)
    out[LAPTOP_ROW:LAPTOP_ROW + ROW_SIZE] = desktop
    out[0x200 + 372:0x200 + 404] = image_digest(out)
    verified = verify_image(out)
    changes = [
        {"offset": hex(n), "before": f"{before:02x}", "after": f"{after:02x}"}
        for n, (before, after) in enumerate(zip(image, out)) if before != after
    ]
    allowed = set(range(LAPTOP_ROW, LAPTOP_ROW + ROW_SIZE)) | set(DIGEST_RANGE)
    if any(int(change["offset"], 16) not in allowed for change in changes):
        raise AssertionError("Unexpected byte changed outside LAPTOP map or image digest")
    if out[DESKTOP_ROW:DESKTOP_ROW + ROW_SIZE] != desktop:
        raise AssertionError("DESKTOP map changed")
    for profile in (1, 2):
        base = ROWS[profile]
        if out[base:base + ROW_SIZE] != image[base:base + ROW_SIZE]:
            raise AssertionError(f"profile {profile} map changed")
    return bytes(out), {
        "status": "OFFLINE CANDIDATE; NOT FLASHED OR LIVE VERIFIED",
        "base_sha256": BASE_SHA256,
        "candidate": verified,
        "scope": "Copies the existing 30-byte DESKTOP BLE physical-key map to LAPTOP. This maps LAPTOP Vol+ to logical key 40, matching the working DESKTOP pairing gesture.",
        "laptop_row": hex(LAPTOP_ROW), "desktop_row": hex(DESKTOP_ROW),
        "changed_cells": [n for n, (before, after) in enumerate(zip(laptop, desktop)) if before != after],
        "preserved": ["DESKTOP map", "TV and amplifier maps", "profile records", "Bluetooth bond storage", "screen firmware", "application code", "custom touch/label extension"],
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
    (args.output_dir / "candidate-0.5.7-laptop-ble-map.bin").write_bytes(out)
    (args.output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
