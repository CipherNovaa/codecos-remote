#!/usr/bin/env python3
"""Reproducible main-firmware candidate: physical BLE24 + touch6 + custom labels.

Only builds offline. Requires clang/lld and llvm-objcopy; exact generated code
is pinned as well as the stock firmware. Screen firmware is never modified.

Two variants share this builder:
- legacy CT01 (default): aliases f0..f9 are keyboard digit aliases. This is the
  image that was flashed on 2026-09-20 and the base of the installed chain.
- spare keys (``spare_keys=True``): aliases f0..f9 become ten independent,
  renameable SPARE keys with their own keyboard-page HID usages. The screen
  firmware only renders custom text for logical IDs f0..ff, so sixteen custom
  keys per device is the whole renameable space; the six named keys keep fa..ff.
"""
import argparse
import json
import struct
import subprocess
import tempfile
from pathlib import Path

from firmware import MP_SIZE, IMAGE_HEADER_SIZE, POOL_BASE, ROWS, SHA_OFFSET, image_digest, sha256, verify_image
from touch_firmware import APPEND_OFFSET, HOOK_OFFSET, HOOK, KEYS, build_touch_candidate

PATCH_SHA256 = 'a009a768c3c77630450c79c60ba94c036ddbdc633f53cd7115772f00ba6f08ee'
SPARE_PATCH_SHA256 = '401e4465ee9704b8290f646cac21a9a7b6ac0dbe471f927b58dbb35ba87e2836'
CONFIG_TABLE_ENTRY = 0xA9E4 + 0x0B * 2
BRIDGE_ADDRESS = 0x82773C
NAMED = ('red', 'green', 'yellow', 'blue', 'return', 'info')
# Ten spare touchscreen keys on aliases f0..f9. Pool indices 2..12 are keyboard-page
# entries (index < 0x21) that no BLE profile row, the digit table, or touch6 uses.
# Usages avoid F20: xkb maps KEY_F20 to XF86AudioMicMute, which Hyprland binds on the
# desktop and would swallow before codec sees it. Every entry is a distinct evdev key.
SPARE_KEYS_V1 = [  # flashed 2026-09-23 (spare-keys-v1); kept so that image stays reproducible
    {'name': 'spare1', 'alias': 0xF0, 'pool': 2, 'usage': 0x70, 'linux': 'KEY_F21', 'evdev': 191},
    {'name': 'spare2', 'alias': 0xF1, 'pool': 3, 'usage': 0x71, 'linux': 'KEY_F22', 'evdev': 192},
    {'name': 'spare3', 'alias': 0xF2, 'pool': 4, 'usage': 0x72, 'linux': 'KEY_F23', 'evdev': 193},
    {'name': 'spare4', 'alias': 0xF3, 'pool': 5, 'usage': 0x73, 'linux': 'KEY_F24', 'evdev': 194},
    {'name': 'spare5', 'alias': 0xF4, 'pool': 6, 'usage': 0x75, 'linux': 'KEY_HELP', 'evdev': 138},
    {'name': 'spare6', 'alias': 0xF5, 'pool': 7, 'usage': 0x7A, 'linux': 'KEY_UNDO', 'evdev': 131},
    {'name': 'spare7', 'alias': 0xF6, 'pool': 9, 'usage': 0x7E, 'linux': 'KEY_FIND', 'evdev': 136},
    {'name': 'spare8', 'alias': 0xF7, 'pool': 10, 'usage': 0x7C, 'linux': 'KEY_COPY', 'evdev': 133},
    {'name': 'spare9', 'alias': 0xF8, 'pool': 11, 'usage': 0x7D, 'linux': 'KEY_PASTE', 'evdev': 135},
    {'name': 'spare10', 'alias': 0xF9, 'pool': 12, 'usage': 0x74, 'linux': 'KEY_OPEN', 'evdev': 134},
]
# v2: spare5..10 moved to keys with no OS-wide meaning (kernel hid_keyboard[]:
# 0x76->130 PROPS, 0x77->132 FRONT, 0x79->129 AGAIN, 0x8a->92 HENKAN,
# 0x8b->94 MUHENKAN, 0x92->90 KATAKANA). Same aliases and pool indices as v1.
SPARE_KEYS = [
    *SPARE_KEYS_V1[:4],
    {'name': 'spare5', 'alias': 0xF4, 'pool': 6, 'usage': 0x76, 'linux': 'KEY_PROPS', 'evdev': 130},
    {'name': 'spare6', 'alias': 0xF5, 'pool': 7, 'usage': 0x77, 'linux': 'KEY_FRONT', 'evdev': 132},
    {'name': 'spare7', 'alias': 0xF6, 'pool': 9, 'usage': 0x79, 'linux': 'KEY_AGAIN', 'evdev': 129},
    {'name': 'spare8', 'alias': 0xF7, 'pool': 10, 'usage': 0x8A, 'linux': 'KEY_HENKAN', 'evdev': 92},
    {'name': 'spare9', 'alias': 0xF8, 'pool': 11, 'usage': 0x8B, 'linux': 'KEY_MUHENKAN', 'evdev': 94},
    {'name': 'spare10', 'alias': 0xF9, 'pool': 12, 'usage': 0x92, 'linux': 'KEY_KATAKANA', 'evdev': 90},
]
LEGACY_ALIASES = {str(i): 0xF0 + i for i in range(10)}
LEGACY_ALIASES.update(dict(zip(NAMED, range(0xFA, 0x100))))
ALIASES = {k['name']: k['alias'] for k in SPARE_KEYS}
ALIASES.update(dict(zip(NAMED, range(0xFA, 0x100))))
# Screen (RT150T) custom-label getter: key >> 4 == 0xf selects custom text.
CUSTOM_KEY_SPACE = range(0xF0, 0x100)


def assemble(spare_keys=False):
    root = Path(__file__).resolve().parent
    with tempfile.TemporaryDirectory() as folder:
        elf = Path(folder) / 'patch.elf'
        hook, code = Path(folder) / 'hook.bin', Path(folder) / 'code.bin'
        defines = ['-DOFA_CUSTOM_LABELS'] + (['-DOFA_SPARE_KEYS'] if spare_keys else [])
        subprocess.run(['clang', '--target=arm-none-eabi', '-mcpu=cortex-m4', '-mthumb',
                        '-fuse-ld=lld', '-nostdlib', '-Oz', '-ffreestanding', '-fno-builtin',
                        '-fno-unwind-tables', '-fno-asynchronous-unwind-tables',
                        *defines, f'-Wl,-T,{root / "custom_patch.ld"}',
                        '-Wl,--entry=touch_lookup', str(root / 'touch_patch.S'),
                        str(root / 'label_bridge.S'), str(root / 'label_patch.c'), '-o', str(elf)], check=True)
        subprocess.run(['llvm-objcopy', '--dump-section', f'.hook={hook}',
                        '--dump-section', f'.text={code}', str(elf)], check=True)
        result = code.read_bytes()
        pin = SPARE_PATCH_SHA256 if spare_keys else PATCH_SHA256
        if hook.read_bytes() != HOOK or sha256(result) != pin:
            raise ValueError(f'Compiler output changed ({sha256(result)}); re-audit before updating the code pin')
        return result


def spare_pool_check(image, spares):
    """Refuse a spare pool entry that any existing BLE mapping already references."""
    used = {i for base in ROWS.values() for i in image[base:base + 30]}
    used.update(image[0x16136:0x16140])
    used.update(k['pool'] for k in KEYS)
    pools = [k['pool'] for k in spares]
    if used.intersection(pools) or len(set(pools)) != len(pools):
        raise ValueError('A spare touchscreen pool entry is already in use')
    if any(not 1 <= p < 0x21 for p in pools):
        raise ValueError('Spare pool entries must be keyboard-page indices (1..32)')
    if len({k['usage'] for k in spares} | {k['usage'] for k in KEYS}) != len(spares) + len(KEYS):
        raise ValueError('Spare HID usages must be distinct from each other and from the six named keys')
    # Keyboard usages already emitted by physical buttons (F13) must stay unique too.
    if 0x68 in {k['usage'] for k in spares}:
        raise ValueError('Spare usage collides with physical Power F13')
    if len({k['alias'] for k in spares} | set(range(0xFA, 0x100))) != 16:
        raise ValueError('Spare aliases must fill f0..f9 exactly')
    if [k['alias'] for k in spares] != list(range(0xF0, 0xFA)) or [k['pool'] for k in spares] != [2, 3, 4, 5, 6, 7, 9, 10, 11, 12]:
        raise ValueError('Spare aliases/pools must match the assembled alias table')


def build_custom_candidate(stock, spare_keys=False, spares=None):
    spares = SPARE_KEYS if spares is None else spares
    touch, _ = build_touch_candidate(stock)
    out = bytearray(touch[:APPEND_OFFSET])
    code = assemble(spare_keys)
    out.extend(code)
    out.extend(bytes((-len(code)) % 16))
    if (0x80DE00 + len(out) - 1) // 4096 != (0x80DE00 + len(stock) - 1) // 4096:
        raise ValueError('Extension exceeds existing final APP sector')
    if stock[CONFIG_TABLE_ENTRY:CONFIG_TABLE_ENTRY + 2] != bytes.fromhex('82 04'):
        raise ValueError('W 0B is not an unused opcode in this firmware')
    delta = BRIDGE_ADDRESS - 0x8187E4
    if delta % 2 or not 0 <= delta // 2 <= 65535:
        raise ValueError('Config dispatch branch out of range')
    struct.pack_into('<H', out, CONFIG_TABLE_ENTRY, delta // 2)
    allowed = set(range(APPEND_OFFSET, len(out)))
    allowed.update(range(CONFIG_TABLE_ENTRY, CONFIG_TABLE_ENTRY + 2))
    allowed.update(range(MP_SIZE + 8, MP_SIZE + 12))
    allowed.update(range(SHA_OFFSET, SHA_OFFSET + 32))
    if spare_keys:
        spare_pool_check(touch, spares)
        for key in spares:
            struct.pack_into('<H', out, POOL_BASE + 2 * key['pool'], key['usage'])
            allowed.update(range(POOL_BASE + 2 * key['pool'], POOL_BASE + 2 * key['pool'] + 2))
    struct.pack_into('<I', out, MP_SIZE + 8, len(out) - MP_SIZE - IMAGE_HEADER_SIZE)
    out[SHA_OFFSET:SHA_OFFSET + 32] = image_digest(out)
    verify_image(out)
    changed = {i for i, (a, b) in enumerate(zip(touch, out)) if a != b}
    if changed - allowed:
        raise ValueError('Change outside custom-label allowlist')
    sector_end = (0x80DE00 + len(stock) + 0xFFF) & ~0xFFF
    return bytes(out), {
        'status': 'OFFLINE CANDIDATE; NOT FLASHED OR LIVE VERIFIED',
        'candidate': verify_image(out), 'stock': verify_image(stock),
        'six_keys': KEYS, 'spare_keys': spares if spare_keys else [],
        'label_aliases': ALIASES if spare_keys else LEGACY_ALIASES,
        'label_protocol': 'W 0B; capability CT01; ASCII labels, 1..15 bytes plus NUL; type-4 BLE slots only',
        'added_payload_bytes': len(out) - len(stock),
        'final_sector_bytes_remaining': sector_end - (0x80DE00 + len(out)),
        'code_sha256': SPARE_PATCH_SHA256 if spare_keys else PATCH_SHA256,
        'scope': 'Main APP only; screen firmware, all profile records and bonds untouched by builder',
        'limitations': ['No guaranteed bad-boot recovery', 'Offline checks do not verify live boot/display/radio'],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stock', type=Path)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--spare-keys', action='store_true', help='ten SPARE keys on aliases f0..f9')
    args = parser.parse_args()
    image, manifest = build_custom_candidate(args.stock.read_bytes(), args.spare_keys)
    args.output_dir.mkdir(parents=True, exist_ok=False)
    name = 'candidate-0.5.7-custom-touch-spare.bin' if args.spare_keys else 'candidate-0.5.7-custom-touch.bin'
    (args.output_dir / name).write_bytes(image)
    (args.output_dir / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps(manifest, indent=2))


if __name__ == '__main__':
    main()
