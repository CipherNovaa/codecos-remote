#!/usr/bin/env python3
"""Build the narrow URC7985 LAPTOP type-4 pairing-route candidate.

The completed Vol+ + OK handler normally enters the radio pairing routine only
when the runtime pairing-state byte is one.  Its type-4 fallback instead enters
the ordinary key-action routine.  This replaces that *type-4 fallback* branch
with a branch to the existing radio pairing routine.  Non-type-4 profiles keep
their original cleanup path; the already-paired DESKTOP path is unchanged.
"""
import argparse
import hashlib
import json
from pathlib import Path

from firmware import image_digest, verify_image

BASE_SHA256 = "1f5db4d75778e930f5c4b9653a2121a1991d8fada0d13219a584f78504364e64"
PATCH_OFFSET = 0xF73C
BEFORE = bytes.fromhex("08 d0")  # beq 0x81d550 (ordinary action)
AFTER = bytes.fromhex("05 e0")   # b   0x81d54a (existing radio pairing)
DIGEST_RANGE = range(0x200 + 372, 0x200 + 404)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def build(image):
    verify_image(image)
    if digest(image) != BASE_SHA256:
        raise ValueError("Input is not the exact known-good custom-touch-v2 image")
    if image[PATCH_OFFSET:PATCH_OFFSET + len(BEFORE)] != BEFORE:
        raise ValueError("Type-4 fallback branch does not match the audited image")
    out = bytearray(image)
    out[PATCH_OFFSET:PATCH_OFFSET + len(AFTER)] = AFTER
    out[0x200 + 372:0x200 + 404] = image_digest(out)
    verified = verify_image(out)
    changed = {n for n, (before, after) in enumerate(zip(image, out)) if before != after}
    allowed = set(DIGEST_RANGE) | set(range(PATCH_OFFSET, PATCH_OFFSET + len(AFTER)))
    if not changed <= allowed:
        raise AssertionError("Unexpected byte changed outside pairing route or image digest")
    return bytes(out), {
        "status": "OFFLINE CANDIDATE; NOT FLASHED OR LIVE VERIFIED",
        "base_sha256": BASE_SHA256,
        "candidate": verified,
        "scope": "Only the type-4 fallback after Vol+ + OK. It now calls the stock radio pairing routine instead of the ordinary key-action routine.",
        "machine_code": [{"offset": hex(PATCH_OFFSET), "before": BEFORE.hex(" "), "after": AFTER.hex(" "),
                          "from": "beq 0x81d550", "to": "b 0x81d54a"}],
        "preserved": ["DESKTOP's already-paired path", "non-type-4 profiles", "profile records", "Bluetooth bond storage", "screen firmware", "BLE maps", "custom touch/label extension"],
        "limitations": ["No dual-bank rollback is available for the main APP image.", "The existing radio routine may replace the controller's sole primary-host bond; offline validation cannot prove multi-host capacity."],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    base = args.image.read_bytes()
    out, manifest = build(base)
    args.output_dir.mkdir(parents=True, exist_ok=False)
    (args.output_dir / "base-custom-touch-v2.bin").write_bytes(base)
    (args.output_dir / "candidate-0.5.7-laptop-pairing-v5.bin").write_bytes(out)
    (args.output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
