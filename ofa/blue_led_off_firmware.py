#!/usr/bin/env python3
"""Build the OFA URC7985 MAIN (RTL8762C, Cortex-M4) "blue light bar off" candidate.

OFFLINE ONLY.  Nothing is flashed or transmitted.

The blue light bar under the e-paper is GPIO LED channel 2 (P1_1).  It is lit by
exactly four call sites into the LED API (steady-on 0x81bb9a / blink 0x81bbda);
this candidate NOPs those four sites and nothing else:

    0x81cb8a  bl  0x81bb9a   key handler: LED2 steady ON while a key is held
    0x81cb6e  bl  0x81bbda   key handler: LED2 200 ms blink variant
    0x81614e  bl  0x81bb9a   IR-transmit start hook: LED ON while IR repeats
    0x81d4a4  b.w 0x81bbda   touch event: LED2 250 ms blink (tail call after pop.w {r4,lr})

The first three become `nop; nop`; the tail call becomes `bx lr; nop` (lr was just
restored by the `pop.w {r4, lr}` at 0x81d49c).  The pairing blink at 0x812d8e/0x812d9a
(LED0 + LED2) is deliberately untouched so the pairing indicator survives.

BASE = the INSTALLED main v1 (screensaver W 0C forwarder, identity SS01, image SHA
3b2e3240...), itself spare-keys-v2 (6daaf9d7...) plus the additive forwarder.  It is
reproduced from the stock APK asset entirely from source (spare_keys_firmware.py +
screensaver_main_v1.{S,c}) and must match the pinned artifact byte-for-byte; the
LED patch is then applied on top.  The four sites are in the stock region, covered by
the Realtek image digest at image[372:404] (over image[12:372]+image[404:752]+
image[1008:]), so the digest is recomputed.  See check_blue_led_off.py for the
validation (static + unicorn) and flash.py for the reproduce-and-match acceptance.
"""
import argparse
import json
import struct
from pathlib import Path

from firmware import IMAGE_HEADER_SIZE, MP_SIZE, SHA_OFFSET, verify_image
from screensaver_firmware import (MAIN_BASE_SHA, MAIN_BRIDGE_VA, MAIN_FREE_MARKER, MAIN_RX_SITE,
                                  MAIN_RX_SITE_STOCK, MAIN_TBH_BASE, MAIN_VA, MAIN_W0C_ENTRY,
                                  assemble_stable, capstone_ok, realtek_digest, sha256)

ROOT = Path(__file__).resolve().parent

# ---- pinned base: the installed main v1 --------------------------------------
MAIN_V1_SHA = '3b2e3240b640f33d322bbd1b5484bba26f8e6feb569833150c146feee4bf82de'
MAIN_V1_PATH = ROOT / 'artifacts/screensaver-v1/main-screensaver-v1-DO-NOT-FLASH.bin'
MAIN_V1_CODE_SHA = '580d763bba1346e96ec4af157234c92d016f2170ee568b0afcd0069f31f8b31c'   # 236 B forwarder
MAIN_V1_CODE_BYTES = 236
MAIN_V1_FLASH_JOURNAL = ROOT / 'artifacts/control-desk/20260923T231559Z-main-fw-execute/transfer.json'
MAIN_V1_BYTES = 105500

# ---- LED API (stock region; asserted, never patched) --------------------------
LED_ON, LED_OFF_A, LED_BLINK, LED_OFF_B = 0x81bb9a, 0x81bbba, 0x81bbda, 0x81bc28
LED_DRIVER, LED_TIMER = 0x81ba28, 0x81ba96

NOP = bytes.fromhex('00bf')
BX_LR = bytes.fromhex('7047')

# The four LED2 sites: VA, expected instruction, stock bytes, replacement, and the
# surrounding stock context that must hold for the replacement to be sound.
SITES = (
    {'va': 0x81cb8a, 'mnemonic': 'bl', 'target': LED_ON, 'stock': 'fff706f8', 'patch': NOP + NOP,
     'what': 'key handler 0x81cb0c: LED2 steady ON at key press (held)',
     'context': {0x81cb88: '0220'}},                            # movs r0, #2  (LED2)
    {'va': 0x81cb6e, 'mnemonic': 'bl', 'target': LED_BLINK, 'stock': 'fff734f8', 'patch': NOP + NOP,
     'what': 'key handler 0x81cb0c: LED2 200 ms blink variant',
     'context': {0x81cb66: 'c822', 0x81cb68: '0123', 0x81cb6a: '1146', 0x81cb6c: '0220'}},
    {'va': 0x81614e, 'mnemonic': 'bl', 'target': LED_ON, 'stock': '05f024fd', 'patch': NOP + NOP,
     'what': 'IR-transmit start hook 0x81610a: LED ON while IR repeats (LED2; LED0 when bit 12 of 0x20b448)',
     'context': {0x816148: '0020', 0x81614a: '00e0', 0x81614c: '0220', 0x816152: '2068'}},
    {'va': 0x81d4a4, 'mnemonic': 'b.w', 'target': LED_BLINK, 'stock': 'fef799bb', 'patch': BX_LR + NOP,
     'what': 'touch event 0x81d47c: LED2 250 ms blink, tail call after pop.w {r4, lr}',
     'context': {0x81d498: 'fa22', 0x81d49a: '0123', 0x81d49c: 'bde81040', 0x81d4a0: '1146', 0x81d4a2: '0220'}},
)

# Untouched by design (asserted identical): the pairing blink LED0 + LED2.
PRESERVED = {0x812d8c: '0020', 0x812d8e: '08f024ff', 0x812d98: '0220', 0x812d9a: '08f01eff'}

# Named regions asserted byte-identical to main v1 (redundant with the whole-image
# diff, listed so the manifest names what was checked).
NAMED_REGIONS = {
    'led_api_0x81ba28_0x81bc48': (0x81ba28, 0x81bc48),
    'pairing_blink_0x812d80_0x812da0': (0x812d80, 0x812da0),
    'w_opcode_tbh_table_0x8187e4_512B': (MAIN_TBH_BASE, MAIN_TBH_BASE + 512),
    'response_dispatcher_tail_0x81b938': (MAIN_RX_SITE, MAIN_RX_SITE + 4),
    'w0c_forwarder_appended_0x82792c': (MAIN_BRIDGE_VA, MAIN_VA + MAIN_V1_BYTES),
    'hid_matrix_and_pool_0x1636a_0x164a0': (MAIN_VA + 0x1636A, MAIN_VA + 0x164A0),
}


def _md():
    from capstone import Cs, CS_ARCH_ARM, CS_MODE_THUMB, CS_MODE_MCLASS
    return Cs(CS_ARCH_ARM, CS_MODE_THUMB | CS_MODE_MCLASS)


def disasm_one(image, va):
    """(mnemonic, op_str, size) of the single instruction at VA, or None."""
    off = va - MAIN_VA
    ins = list(_md().disasm(image[off:off + 4], va, count=1))
    if not ins:
        return None
    return ins[0].mnemonic, ins[0].op_str, ins[0].size


def disasm_span(image, va, n):
    out = []
    off = va - MAIN_VA
    pos = 0
    md = _md()
    while pos < n:
        ins = list(md.disasm(image[off + pos:off + pos + 4], va + pos, count=1))
        if not ins:
            out.append(f'{va + pos:08x}  {image[off + pos:off + pos + 2].hex()}  <undef>')
            pos += 2
            continue
        i = ins[0]
        out.append(f'{i.address:08x}  {i.bytes.hex():<10} {i.mnemonic:<8} {i.op_str}')
        pos += i.size
    return out


def _branch_target(op_str):
    return int(op_str.lstrip('#'), 16)


# ---------------------------------------------------------------- main v1 reproduction
def reproduce_main_v1(stock):
    """Rebuild the INSTALLED main v1 from the stock APK asset, from source:
    spare-keys-v2 (spare_keys_firmware) + the v1 W 0C forwarder (screensaver_main_v1.{S,c})
    appended at 0x82792c, W 0C tbh entry, payload length, Realtek digest.  Must equal the
    pinned artifact (the SHA the remote validated `10 03 01` on 2026-09-23)."""
    from spare_keys_firmware import build as build_spare_keys
    base, _ = build_spare_keys(stock, 2)
    if sha256(base) != MAIN_BASE_SHA:
        raise ValueError('spare-keys-v2 did not reproduce')
    code, syms = assemble_stable('cortex-m4', 'screensaver_main.ld', 'ss_bridge',
                                 ['screensaver_main_v1.S', 'screensaver_main_v1.c'])
    if len(code) != MAIN_V1_CODE_BYTES or sha256(code) != MAIN_V1_CODE_SHA:
        raise ValueError('v1 forwarder did not reproduce (clang output differs from the flashed code)')
    if syms['ss_bridge'] != MAIN_BRIDGE_VA or MAIN_VA + len(base) != MAIN_BRIDGE_VA:
        raise ValueError('bridge VA mismatch')
    capstone_ok(code, MAIN_BRIDGE_VA, {'ss_bridge': syms['ss_bridge'], 'ss_command': syms['ss_command']})
    if struct.unpack_from('<H', base, MAIN_W0C_ENTRY)[0] != MAIN_FREE_MARKER:
        raise ValueError('W 0C is not an unused opcode in spare-keys-v2')
    out = bytearray(base)
    out.extend(code)
    out.extend(bytes((-len(code)) % 16))
    struct.pack_into('<H', out, MAIN_W0C_ENTRY, (MAIN_BRIDGE_VA - MAIN_TBH_BASE) // 2)
    struct.pack_into('<I', out, MP_SIZE + 8, len(out) - MP_SIZE - IMAGE_HEADER_SIZE)
    out[SHA_OFFSET:SHA_OFFSET + 32] = realtek_digest(out)
    # v1 has NO response-relay splice: the dispatcher tail call must still be stock
    off = MAIN_RX_SITE - MAIN_VA
    if bytes(out[off:off + 4]) != MAIN_RX_SITE_STOCK:
        raise ValueError('v1 must leave the response-dispatcher tail call stock')
    if len(out) != MAIN_V1_BYTES or sha256(out) != MAIN_V1_SHA:
        raise ValueError('main v1 did not reproduce byte-for-byte')
    verify_image(bytes(out))
    return bytes(out)


def load_main_v1(path=MAIN_V1_PATH):
    data = path.read_bytes()
    if sha256(data) != MAIN_V1_SHA:
        raise ValueError(f'{path} is not the installed main v1 (SHA {MAIN_V1_SHA[:8]}...)')
    return data


def flash_journal_proof(path=MAIN_V1_FLASH_JOURNAL):
    """The 2026-09-23 main flash journal: the remote validated this exact SHA (10 03 01)."""
    j = json.loads(path.read_text())
    events = j.get('events', [])
    validated = [e for e in events if e.get('event') == 'image_validated']
    ok = (j.get('plan', {}).get('image', {}).get('sha256') == MAIN_V1_SHA
          and any(e.get('image_sha256') == MAIN_V1_SHA for e in validated)
          and any(e.get('event') == 'notification' and e.get('value') == '10 03 01' for e in events)
          and any(e.get('event') == 'transfer_complete' for e in events))
    return {'journal': str(path.relative_to(ROOT)), 'image_validated_sha256_matches': ok,
            'status': j.get('status')}


# ---------------------------------------------------------------------- the patch
def check_sites(image, *, patched):
    """Every site disassembles to exactly what is expected (stock or patched) and its
    stock context holds.  Returns the per-site disassembly for the manifest."""
    report = []
    for s in SITES:
        off = s['va'] - MAIN_VA
        have = bytes(image[off:off + 4])
        for cva, chex in s['context'].items():
            coff = cva - MAIN_VA
            if bytes(image[coff:coff + len(chex) // 2]).hex() != chex:
                raise ValueError(f'context at {cva:#x} is not stock ({chex})')
        if not patched:
            if have.hex() != s['stock']:
                raise ValueError(f'{s["va"]:#x}: bytes {have.hex()} are not the expected stock {s["stock"]}')
            d = disasm_one(image, s['va'])
            if d is None or d[0] != s['mnemonic'] or d[2] != 4 or _branch_target(d[1]) != s['target']:
                raise ValueError(f'{s["va"]:#x}: expected `{s["mnemonic"]} {s["target"]:#x}`, got {d}')
            report.append({'va': hex(s['va']), 'file_offset': hex(off), 'stock_bytes': have.hex(),
                           'disasm': f'{d[0]} #{s["target"]:#x}', 'what': s['what']})
        else:
            if have != s['patch']:
                raise ValueError(f'{s["va"]:#x}: not patched')
            a = disasm_one(image, s['va'])
            b = disasm_one(image, s['va'] + 2)
            want = ('bx', 'lr') if s['patch'][:2] == BX_LR else ('nop', '')
            if a is None or b is None or (a[0], a[1]) != want or b[0] != 'nop' or a[2] != 2 or b[2] != 2:
                raise ValueError(f'{s["va"]:#x}: patched bytes do not disassemble as intended: {a} {b}')
            report.append({'va': hex(s['va']), 'file_offset': hex(off), 'patched_bytes': have.hex(),
                           'disasm': f'{a[0]} {a[1]}'.strip() + ' ; nop', 'what': s['what']})
    return report


def build(main_v1):
    """Apply the four-site LED2 patch to main v1; recompute the Realtek digest."""
    if sha256(main_v1) != MAIN_V1_SHA or len(main_v1) != MAIN_V1_BYTES:
        raise ValueError('base is not the installed main v1')
    info_before = verify_image(main_v1)
    before = check_sites(main_v1, patched=False)
    for va, chex in PRESERVED.items():
        off = va - MAIN_VA
        if bytes(main_v1[off:off + len(chex) // 2]).hex() != chex:
            raise ValueError(f'pairing blink site {va:#x} is not as expected')

    out = bytearray(main_v1)
    allowed = set()
    for s in SITES:
        off = s['va'] - MAIN_VA
        if off - MP_SIZE < 1008:
            raise ValueError('a site lies outside the digest-covered [1008:] span; unexpected')
        out[off:off + 4] = s['patch']
        allowed.update(range(off, off + 4))
    old_digest = bytes(out[SHA_OFFSET:SHA_OFFSET + 32])
    out[SHA_OFFSET:SHA_OFFSET + 32] = realtek_digest(out)
    if bytes(out[SHA_OFFSET:SHA_OFFSET + 32]) == old_digest:
        raise ValueError('digest did not change although digest-covered bytes did')
    allowed.update(range(SHA_OFFSET, SHA_OFFSET + 32))
    out = bytes(out)

    changed = {i for i, (a, b) in enumerate(zip(main_v1, out)) if a != b}
    if changed - allowed:
        raise ValueError('change outside the allowlist')
    site_changed = sum(1 for s in SITES for i in range(4) if (s['va'] - MAIN_VA + i) in changed)
    info_after = verify_image(out)
    after = check_sites(out, patched=True)
    for va, chex in PRESERVED.items():
        off = va - MAIN_VA
        if bytes(out[off:off + len(chex) // 2]).hex() != chex:
            raise ValueError('pairing blink changed')
    regions = {}
    for name, (lo, hi) in NAMED_REGIONS.items():
        a, b = lo - MAIN_VA, hi - MAIN_VA
        if out[a:b] != main_v1[a:b]:
            raise ValueError(f'named region {name} changed')
        regions[name] = {'from': hex(lo), 'to': hex(hi), 'identical': True}
    if struct.unpack_from('<H', out, MAIN_W0C_ENTRY)[0] != (MAIN_BRIDGE_VA - MAIN_TBH_BASE) // 2:
        raise ValueError('W 0C entry lost')

    manifest = {
        'status': 'CANDIDATE — NOT FLASHED. Main has NO rollback; flash only with fresh batteries, '
                  'after review, via flash.py reproduce-and-match (ofa_control.py main-fw).',
        'feature': 'blue light bar (GPIO LED2 / P1_1) never lit by key hold, key blink, IR-active or touch; '
                   'pairing blink (LED0+LED2) preserved',
        'base': {'name': 'main v1 = screensaver W 0C forwarder (SS01) on spare-keys-v2',
                 'sha256': MAIN_V1_SHA, 'bytes': MAIN_V1_BYTES,
                 'spare_keys_v2_sha256': MAIN_BASE_SHA,
                 'forwarder_code_sha256': MAIN_V1_CODE_SHA, 'forwarder_code_bytes': MAIN_V1_CODE_BYTES,
                 'reproduced_from_source': 'spare_keys_firmware.build(stock, 2) + screensaver_main_v1.{S,c}',
                 'realtek_digest_before': info_before['realtek_sha256']},
        'candidate': {'sha256': sha256(out), 'bytes': len(out), 'realtek_digest': info_after['realtek_sha256'],
                      'image': info_after},
        'changed_bytes_total': len(changed), 'changed_site_bytes': site_changed, 'changed_digest_bytes': 32,
        'changed_blocks': _blocks(sorted(changed)),
        'sites_before': before, 'sites_after': after,
        'preserved_pairing_blink': {hex(k): v for k, v in PRESERVED.items()},
        'named_regions_identical': regions,
        'digest': 'Realtek SHA-256 at image[372:404] over image[12:372]+image[404:752]+image[1008:] '
                  '(image = file[512:]); all four sites are in [1008:], so the digest was recomputed '
                  'with the same method verify_image() checks (and that main v1 was flashed with).',
        'led_api_untouched': {'steady_on': hex(LED_ON), 'off': [hex(LED_OFF_A), hex(LED_OFF_B)],
                              'blink': hex(LED_BLINK), 'gpio_driver': hex(LED_DRIVER), 'timer': hex(LED_TIMER)},
        'note_0x81614e': 'this site selects LED0 (bit 12 of 0x20b448 set) or LED2; the NOP drops both in the '
                         'IR-active path. A 2-byte alternative (0x81614c `movs r0,#2` -> `b 0x816152` = 01 e0) '
                         'would keep the LED0 variant; not used, per the agreed four-site patch.',
        'recovery': 'main v1 = artifacts/screensaver-v1/main-screensaver-v1-DO-NOT-FLASH.bin '
                    f'({MAIN_V1_SHA}), flashed and booted 2026-09-23; reflash it via the same path.',
        'flash_journal_proof_for_base': flash_journal_proof() if MAIN_V1_FLASH_JOURNAL.exists() else None,
    }
    return out, manifest


def _blocks(offsets):
    out = []
    for o in offsets:
        if out and o == out[-1][1] + 1:
            out[-1][1] = o
        else:
            out.append([o, o])
    return [{'from': hex(a), 'to': hex(b), 'bytes': b - a + 1} for a, b in out]


def reproduce_candidate(stock):
    """flash.py gate: stock asset -> main v1 (from source) -> LED patch; exact bytes."""
    image, _ = build(reproduce_main_v1(stock))
    return image


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--stock', type=Path, default=ROOT / 'artifacts/ble24/stock-0.5.7.bin',
                    help='the saved 0.5.7 APK asset (main v1 is rebuilt from it and must match the artifact)')
    ap.add_argument('--output-dir', type=Path, default=ROOT / 'artifacts/codecos-v16')
    ap.add_argument('--force', action='store_true')
    args = ap.parse_args()

    artifact = load_main_v1()
    rebuilt = reproduce_main_v1(args.stock.read_bytes())
    if rebuilt != artifact:
        raise SystemExit('main v1 rebuilt from source differs from the flashed artifact; refusing')
    image, manifest = build(artifact)
    image2, _ = build(rebuilt)
    if image != image2:
        raise SystemExit('build is not deterministic')
    manifest['reproducible'] = {'main_v1_from_source_equals_artifact': True,
                                'candidate_rebuild_identical': True}
    args.output_dir.mkdir(parents=True, exist_ok=args.force)
    # codecOS v16.0 is the release name for this final unified main image; the
    # blue-LED-off patch is what this builder applies on top of main v1.
    out = args.output_dir / 'main-codecos-v16-DO-NOT-FLASH.bin'
    out.write_bytes(image)
    (args.output_dir / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(f'candidate: {out}')
    print(f'sha256:    {manifest["candidate"]["sha256"]}  ({len(image)} B)')
    print(f'changed:   {manifest["changed_bytes_total"]} bytes = {manifest["changed_site_bytes"]} at 4 sites + 32 digest')
    for s in manifest['sites_after']:
        print(f'  {s["va"]}  {s["patched_bytes"]}  {s["disasm"]:<14} {s["what"]}')


if __name__ == '__main__':
    main()
