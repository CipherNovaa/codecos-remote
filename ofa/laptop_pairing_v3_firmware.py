#!/usr/bin/env python3
"""Build the URC7985 LAPTOP pairing candidate with both audited runtime gates."""
import argparse
import hashlib
import json
from pathlib import Path

from firmware import image_digest, verify_image

BASE_SHA256 = "1f5db4d75778e930f5c4b9653a2121a1991d8fada0d13219a584f78504364e64"
PATCHES = {
    # Do not exit after completed Vol+ + OK solely because the Desktop runtime flag is clear.
    0xF72C: (bytes.fromhex("14 d0"), bytes.fromhex("00 bf")),
    # Permit runtime type 3 (LAPTOP) and 4 (DESKTOP) to reach the existing pairing action.
    0xF73A: (bytes.fromhex("04 28 08 d0 0a e0"), bytes.fromhex("03 38 01 28 07 d9")),
}
DIGEST_RANGE = range(0x200 + 372, 0x200 + 404)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def build(image):
    verify_image(image)
    if digest(image) != BASE_SHA256:
        raise ValueError("Input is not the exact installed custom-touch-v2 image")
    out = bytearray(image)
    for offset, (before, after) in PATCHES.items():
        if image[offset:offset + len(before)] != before:
            raise ValueError(f"Runtime pairing-gate bytes do not match at {offset:#x}")
        out[offset:offset + len(after)] = after
    out[0x200 + 372:0x200 + 404] = image_digest(out)
    verified = verify_image(out)
    changes = [
        {"offset": hex(n), "before": f"{before:02x}", "after": f"{after:02x}"}
        for n, (before, after) in enumerate(zip(image, out)) if before != after
    ]
    allowed = set(DIGEST_RANGE)
    for offset, (_, after) in PATCHES.items():
        allowed.update(range(offset, offset + len(after)))
    if any(int(change["offset"], 16) not in allowed for change in changes):
        raise AssertionError("Unexpected byte changed outside pairing gates or image digest")
    return bytes(out), {
        "status": "OFFLINE CANDIDATE; NOT FLASHED OR LIVE VERIFIED",
        "base_sha256": BASE_SHA256,
        "candidate": verified,
        "scope": "Completed Vol+ + OK pairing path only: type 3 (LAPTOP) and type 4 (DESKTOP) reach the firmware's original Bluetooth pairing routine regardless of the Desktop runtime flag.",
        "machine_code": [
            {"offset": hex(offset), "before": before.hex(" "), "after": after.hex(" ")}
            for offset, (before, after) in PATCHES.items()
        ],
        "preserved": ["profile records", "Bluetooth bond storage", "screen firmware", "BLE key-map rows", "non-Bluetooth profile types", "existing pairing routine", "appended touch/label code"],
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
    (args.output_dir / "candidate-0.5.7-laptop-pairing-v3.bin").write_bytes(out)
    (args.output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
