#!/usr/bin/env python3
"""Rebalance the OFA SPARE-key touchscreen pages to N buttons each (default 5).

The screen renders custom-label pages as a vertical list of full-width buttons and
only ~5 fit on screen, so a 6-key page hides its 6th button off the bottom. This
re-splits only the SPARE pages (aliases f0..f9) into pages of `--per-page`, in order,
reusing their existing page ids. The numeric keypad and the named page (fa..ff) are
left byte-identical. Backed up + readback-verified, exactly like labels.py.

Needs CT01 firmware; phone Bluetooth OFF; select AMPLIFIER first (frees the management
radio) then hold ACTIVITIES+DEVICES if prompted. Default = read + plan; --apply writes.
"""
import argparse
import asyncio
import copy
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from labels import LabelConfig
from touch_pages import ADDRESS, encode_pages, slot_identity, validate_pages, check_slot


def is_spare_page(page):
    return page['layout'] == 0xFF and page['keys'] and all(0xF0 <= k <= 0xF9 for k in page['keys'])


def rebalance(pages, per_page):
    spare_pages = [p for p in pages if is_spare_page(p)]
    if not spare_pages:
        return copy.deepcopy(pages)
    others = [p for p in pages if not is_spare_page(p)]
    keys = [k for p in spare_pages for k in p['keys']]                 # preserve order
    chunks = [keys[i:i + per_page] for i in range(0, len(keys), per_page)]
    ids = sorted(p['id'] for p in spare_pages)
    used = {p['id'] for p in others}
    ids += [i for i in range(1, 11) if i not in used and i not in ids]
    if len(chunks) > len(ids):
        raise ValueError('Not enough free touchscreen pages for the rebalance')
    result = copy.deepcopy(others)
    for chunk, pid in zip(chunks, ids):
        result.append({'id': pid, 'layout': 0xFF, 'keys': chunk})
    result.sort(key=lambda p: p['id'])
    validate_pages(result)
    return result


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
        desired = rebalance(existing, args.per_page)
        journal.update(slot=args.slot, before=before, desired=desired, status='planned')
        save()
        print(json.dumps(desired, indent=2), flush=True)
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
        print('Rebalanced + verified; labels and other devices unchanged. Reselect the device to refresh.')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--address', default=ADDRESS)
    p.add_argument('--slot', type=int, required=True)
    p.add_argument('--per-page', type=int, default=5)
    p.add_argument('--backup', type=Path, required=True)
    p.add_argument('--apply', action='store_true')
    args = p.parse_args()
    check_slot(args.slot)
    journal = {'schema': 1, 'address': args.address, 'action': 'rebalance-spares',
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
