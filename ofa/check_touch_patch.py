#!/usr/bin/env python3
"""Execute every logical screen ID against original and patched ARM handlers."""
import argparse
import subprocess
import tempfile
from pathlib import Path

from check_touch_digits import execute_screen_key
from check_firmware import execute_lookup
from firmware import ROWS, build_candidate, button_map, verify_image
from touch_firmware import HOOK, CODE, KEYS, build_touch_candidate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stock', type=Path)
    args = parser.parse_args()
    stock = args.stock.read_bytes()
    before, _ = build_candidate(stock)
    after, manifest = build_touch_candidate(stock)
    verify_image(after)
    assert (len(after) - 524) % 16 == 0
    assert button_map(before) == button_map(after)
    root = Path(__file__).resolve().parent
    with tempfile.TemporaryDirectory() as folder:
        elf = Path(folder) / 'patch.elf'
        hook, code = Path(folder) / 'hook.bin', Path(folder) / 'code.bin'
        subprocess.run(['clang', '--target=arm-none-eabi', '-mcpu=cortex-m4', '-mthumb',
                        '-fuse-ld=lld', '-nostdlib', f'-Wl,-T,{root / "touch_patch.ld"}',
                        '-Wl,--entry=touch_lookup', str(root / 'touch_patch.S'), '-o', str(elf)], check=True)
        subprocess.run(['llvm-objcopy', '--dump-section', f'.hook={hook}',
                        '--dump-section', f'.text={code}', str(elf)], check=True)
        assert hook.read_bytes() == HOOK and code.read_bytes() == CODE
    expected = {k['logical']: k['usage'] for k in KEYS}
    count = 0
    for profile in (1, 2, 3, 4):
        for enabled in (False, True):
            for key in range(256):
                # Slot 12 tests index bounds; different selected slot proves
                # gating uses the event's actual device, not the current menu.
                options = dict(slot=12, profile=profile, ble_enabled=enabled, selected_slot=1)
                original = execute_screen_key(before, key, **options)
                actual = execute_screen_key(after, key, **options)
                if enabled and profile == 4 and key in expected:
                    press = bytes([0, 0, expected[key], 0, 0, 0, 0, 0])
                    assert actual == ([(0x0A, press), (0x0A, bytes(8))], []), (key, actual)
                else:
                    assert actual == original, (profile, enabled, key, original, actual)
                count += 1
    for slot in range(1, 13):
        for key, usage in expected.items():
            reports, ir = execute_screen_key(after, key, slot=slot)
            assert not ir and reports == [(10, bytes([0, 0, usage, 0, 0, 0, 0, 0])), (10, bytes(8))]
        for profile, base in ROWS.items():
            for cell in range(30):
                assert execute_lookup(after, profile, slot, cell) == before[base + cell]
    print(f'PASS: {count} exhaustive before/after screen dispatch comparisons')
    print('PASS: six HID press/release pairs in all 12 slots; all 1,440 physical lookups unchanged')
    print('PASS: assembly reproduces pinned helper; image integrity and OTA packet alignment')
    print('Modeled connected state; radio, boot, wake/reconnect and physical display are not emulated.')
    print(manifest['candidate']['sha256'])


if __name__ == '__main__':
    main()
