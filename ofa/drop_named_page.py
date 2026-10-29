#!/usr/bin/env python3
"""Remove the duplicate NAMED page (red/green/yellow/blue/return/info as text
buttons) from a device slot, then re-split the SPARE pages 5 per page.

Why: page 1 (the stock keypad) already has dedicated red/green/yellow/blue,
return and info buttons. The named page (aliases fa..ff, added 2026-09-20 so
those keys could carry text labels) holds the SAME six aliases again — so its
"SLEEP" button WAS the return key, "SCREEN MODE" WAS red, and so on. Two
buttons per code made unique jobs impossible. Without it every touchscreen
button is unique: page 1 = digits + the six dedicated keys, pages 2+ = the ten
spares (f0..f9), each with its own label and job.

Only the page records change. Labels (incl. the named keys' stored text) and
other device slots are left byte-identical; backed up and readback-verified
exactly like labels.py / rebalance_spare_pages.py.

Needs CT01 firmware; phone Bluetooth OFF; select AMPLIFIER, then hold
ACTIVITIES+DEVICES until the LEDs blink. Default = read + plan; --apply writes.
"""
import argparse
import asyncio
import copy
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from labels import LabelConfig
from rebalance_spare_pages import rebalance
from touch_pages import ADDRESS, check_slot, encode_pages, slot_identity, validate_pages

NAMED = set(range(0xFA, 0x100))


def is_named_page(page):
    return page['layout'] == 0xFF and page['keys'] and all(k in NAMED for k in page['keys'])


def plan(pages, per_page):
    kept = [copy.deepcopy(p) for p in pages if not is_named_page(p)]
    validate_pages(kept)
    result = rebalance(kept, per_page)
    # Close the hole the named page leaves: spare pages take the lowest free
    # ids after the keypad (2, 3, …) so the screen's page order has no gap.
    spares = sorted((p for p in result if all(0xF0 <= k <= 0xF9 for k in p['keys'])),
                    key=lambda p: p['id'])
    others = [p for p in result if p not in spares]
    used = {p['id'] for p in others}
    free = [i for i in range(1, 11) if i not in used]
    for page, pid in zip(spares, free):
        page['id'] = pid
    result = sorted(others + spares, key=lambda p: p['id'])
    validate_pages(result)
    return result


def describe(pages):
    names = {0xFA: 'red', 0xFB: 'green', 0xFC: 'yellow', 0xFD: 'blue', 0xFE: 'return', 0xFF: 'info'}
    out = []
    for p in pages:
        if all(0xF0 <= k <= 0xF9 for k in p['keys']):
            keys = ' '.join(f"spare{k - 0xEF}" for k in p['keys'])
        elif is_named_page(p):
            keys = ' '.join(names[k] for k in p['keys']) + '   <- DUPLICATE named page'
        else:
            keys = f"{len(p['keys'])} keys (keypad/stock)"
        out.append(f"  page {p['id']:>2} layout {p['layout']:02x}: {keys}")
    return '\n'.join(out)


async def run(args, journal, save):
    from bleak import BleakClient
    async with BleakClient(args.address, timeout=25) as client:
        config = LabelConfig(client)
        journal['transactions'] = config.transactions
        await config.start()
        await config.capabilities()
        before = await config.snapshot()
        slot_identity(before, args.slot)
        existing = before['pages'][str(args.slot)]
        desired = plan(existing, args.per_page)
        journal.update(slot=args.slot, before=before, desired=desired, status='planned')
        save()
        print(f"slot {args.slot} NOW:\n{describe(existing)}\nslot {args.slot} PLAN:\n{describe(desired)}",
              flush=True)
        if not args.apply:
            print('Read + plan only. Use --apply and a new --backup path to write.')
            return
        if desired != existing:
            journal['status'] = 'write_pending'
            save()
            if await config.command(encode_pages(args.slot, desired)) != b'\x17\x01':
                raise ValueError('Page write rejected')
        after = await config.snapshot()
        expected = copy.deepcopy(before)
        expected['pages'][str(args.slot)] = desired
        journal['after'] = after
        if after != expected:
            raise ValueError('Readback differs from the intended page-only change')
        journal['status'] = 'verified'
        save()
        print('Named page removed + spares re-split, verified; labels and other slots unchanged.')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--address', default=ADDRESS)
    p.add_argument('--slot', type=int, required=True)
    p.add_argument('--per-page', type=int, default=5)
    p.add_argument('--backup', type=Path, required=True)
    p.add_argument('--apply', action='store_true')
    args = p.parse_args()
    check_slot(args.slot)
    journal = {'schema': 1, 'address': args.address, 'action': 'drop-named-page',
               'time': datetime.now(timezone.utc).isoformat(), 'status': 'starting'}
    with args.backup.open('x') as stream:
        json.dump(journal, stream)

    def save():
        temporary = args.backup.with_name(args.backup.name + '.tmp')
        with temporary.open('w') as stream:
            json.dump(journal, stream, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(args.backup)

    try:
        asyncio.run(run(args, journal, save))
    except Exception as error:
        journal['error'] = str(error)
        p.exit(1, f'{error}\nBackup: {args.backup}\n')
    finally:
        save()


if __name__ == '__main__':
    main()
