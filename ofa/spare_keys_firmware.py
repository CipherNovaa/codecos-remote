#!/usr/bin/env python3
"""Build the cumulative type-4 image with ten renameable SPARE touchscreen keys.

Offline only. Starts from the custom-label builder in spare-keys mode, then
re-applies the three corrections that are already installed on the remote
(Channel Up/Down swap, Play/Pause swap, Pause pool 0x00b1 -> 0x00cd). The
result is compared byte-for-byte with the installed pause-playpause image:
only the ten alias-table bytes, the ten spare HID pool entries and the Realtek
digest may differ. Bonds, profile records, pages and labels live in FTL
storage, not in this image, and are never written by this builder.

Revisions: 1 = flashed 2026-09-23 (F21..F24 + Help/Undo/Find/Copy/Paste/Open);
2 = spare5..10 moved to Props/Front/Again/Henkan/Muhenkan/Katakana. A revision-2
build must differ from revision 1 only in those six pool entries plus digest.
"""
import argparse
import json
import struct
from pathlib import Path

from custom_firmware import SPARE_KEYS, SPARE_KEYS_V1, ALIASES, build_custom_candidate
from firmware import POOL_BASE, ROWS, SHA_OFFSET, image_digest, sha256, verify_image
from pause_playpause_firmware import build as build_installed
from touch_firmware import APPEND_OFFSET, KEYS

INSTALLED_SHA256 = '2128ff23be689d7ff5b0a06edb93b5931238d2a70827d6c6a958ea665e07467c'
SPARE_V1_SHA256 = '0d83d12665bcf0ea31c0f43631d9243f41b96b0b82af04fd3a06df85f242a24b'
REVISIONS = {1: SPARE_KEYS_V1, 2: SPARE_KEYS}
LEGACY_ALIAS_TABLE = bytes([20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 13, 18, 19, 30, 31, 32])
CHANNEL_DOWN_CELL, CHANNEL_UP_CELL, PLAY_CELL, PAUSE_CELL, PAUSE_POOL = 11, 16, 1, 25, 46


def build(stock, revision=2):
    spares = REVISIONS[revision]
    installed, _ = build_installed(stock)
    if sha256(installed) != INSTALLED_SHA256:
        raise ValueError('Legacy chain no longer reproduces the installed image')
    base, custom_manifest = build_custom_candidate(stock, spare_keys=True, spares=spares)
    out = bytearray(base)
    down, up = ROWS[4] + CHANNEL_DOWN_CELL, ROWS[4] + CHANNEL_UP_CELL
    play, pause = ROWS[4] + PLAY_CELL, ROWS[4] + PAUSE_CELL
    if (out[down], out[up]) != (44, 43) or (out[play], out[pause]) != (45, 46):
        raise ValueError('Unexpected type-4 matrix before re-applying the installed corrections')
    out[down], out[up] = out[up], out[down]
    out[play], out[pause] = out[pause], out[play]
    pause_offset = POOL_BASE + 2 * PAUSE_POOL
    if struct.unpack_from('<H', out, pause_offset)[0] != 0x00B1:
        raise ValueError('Unexpected Pause HID pool usage')
    struct.pack_into('<H', out, pause_offset, 0x00CD)
    out[SHA_OFFSET:SHA_OFFSET + 32] = image_digest(out)
    verified = verify_image(out)

    # The only intended differences from the INSTALLED pause-playpause image.
    table = installed.find(LEGACY_ALIAS_TABLE, APPEND_OFFSET)
    if table < 0 or installed.find(LEGACY_ALIAS_TABLE, table + 1) >= 0:
        raise ValueError('Legacy alias table is not unique in the installed image')
    digest = set(range(SHA_OFFSET, SHA_OFFSET + 32))
    allowed = set(range(table, table + 10)) | digest
    for key in spares:
        allowed.update(range(POOL_BASE + 2 * key['pool'], POOL_BASE + 2 * key['pool'] + 2))
    if len(out) != len(installed):
        raise ValueError('Image length changed versus the installed image')
    changed = {i for i, (a, b) in enumerate(zip(installed, out)) if a != b}
    if changed - allowed:
        raise ValueError('Change outside the spare-keys allowlist versus the installed image')
    if bytes(out[table:table + 16]) != bytes([k['pool'] for k in spares] + [k['pool'] for k in KEYS]):
        raise ValueError('Alias table does not match the spare keys + named keys')
    for key in spares:
        if struct.unpack_from('<H', out, POOL_BASE + 2 * key['pool'])[0] != key['usage']:
            raise ValueError('Spare pool usage not written')
    manifest = {
        'status': 'OFFLINE CANDIDATE; NOT FLASHED OR LIVE VERIFIED',
        'revision': revision,
        'installed_base_sha256': INSTALLED_SHA256,
        'candidate': verified,
        'scope': 'Type-4 BLE slots (DESKTOP 1, LAPTOP 5): ten SPARE touchscreen keys on aliases f0..f9; '
                 'retains touch6 fa..ff, CH swap, Play/Pause swap, Pause 0x00cd',
        'spare_keys': [{**k, 'alias': hex(k['alias']), 'usage': hex(k['usage']),
                        'scan': hex(0x70000 | k['usage']), 'x11_keycode': k['evdev'] + 8} for k in spares],
        'label_aliases': {name: hex(alias) for name, alias in ALIASES.items()},
        'renameable_key_limit': 'Screen firmware renders custom text only for logical IDs f0..ff: '
                                '16 per device = 6 named + 10 spare. Not limited by sector or HID pool.',
        'final_sector_bytes_remaining': custom_manifest['final_sector_bytes_remaining'],
        'changes_from_installed': [{'offset': hex(i), 'before': f'{installed[i]:02x}', 'after': f'{out[i]:02x}'}
                                   for i in sorted(changed)],
        'removed': ['legacy digit aliases f0..f9 (keyboard 0..9); never placed on any device page'],
        'preserved': ['six named keys fa..ff (F14..F19)', 'stock digit keys 0d..16', 'all matrix rows',
                      'types 1-3', 'screen firmware', 'profile records', 'Bluetooth bonds', 'pages', 'labels'],
        'limitations': ['No guaranteed bad-boot recovery', 'Offline checks do not verify live boot/display/radio'],
    }
    if revision == 1:
        if sha256(out) != SPARE_V1_SHA256:
            raise ValueError('Revision 1 no longer reproduces the flashed spare-keys-v1 image')
    else:
        v1, _ = build(stock, 1)
        v1_allowed = set(digest)
        for key in spares[4:]:
            v1_allowed.update(range(POOL_BASE + 2 * key['pool'], POOL_BASE + 2 * key['pool'] + 2))
        v1_changed = {i for i, (a, b) in enumerate(zip(v1, out)) if a != b}
        if v1_changed - v1_allowed or not v1_changed - digest:
            raise ValueError('Revision 2 must differ from revision 1 only in the spare5..10 pool entries')
        manifest['installed_spare_v1_sha256'] = SPARE_V1_SHA256
        manifest['changes_from_spare_v1'] = [{'offset': hex(i), 'before': f'{v1[i]:02x}', 'after': f'{out[i]:02x}'}
                                             for i in sorted(v1_changed)]
    return bytes(out), manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stock', type=Path)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--revision', type=int, choices=sorted(REVISIONS), default=2)
    args = parser.parse_args()
    image, manifest = build(args.stock.read_bytes(), args.revision)
    args.output_dir.mkdir(parents=True, exist_ok=False)
    name = 'candidate-0.5.7-spare-keys.bin' if args.revision == 1 else f'candidate-0.5.7-spare-keys-v{args.revision}.bin'
    (args.output_dir / name).write_bytes(image)
    (args.output_dir / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps(manifest, indent=2))


if __name__ == '__main__':
    main()
