#!/usr/bin/env python3
"""Prepare an auditable in-place patch for the tablet's official OFA split.

The installed APK remains in place, retaining its vendor package identity.  This
tool does not contact a device; it makes a byte-for-byte copy, changes the one
reviewed AArch64 branch in stored libapp.so, updates ZIP CRC fields, and writes
the three tiny byte ranges needed for a root-mediated application of the patch.
"""
import argparse
import hashlib
import json
import shutil
import struct
import zlib
import zipfile
from pathlib import Path

SOURCE_SHA256 = '6af5e86ac613ed0c1871fea0ca6b0f4cb9bf1127c5ebfaed04b54a0387da4c97'
LIB = 'lib/arm64-v8a/libapp.so'
INSTRUCTION = 0x5C9980
EXPECTED = bytes.fromhex('c1002037')
REPLACEMENT = bytes.fromhex('01000014')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('source', type=Path)
    ap.add_argument('output_dir', type=Path)
    args = ap.parse_args()
    if digest(args.source) != SOURCE_SHA256:
        raise ValueError('source does not match the pinned tablet arm64 split')
    if args.output_dir.exists():
        raise FileExistsError(f'refusing to overwrite {args.output_dir}')
    args.output_dir.mkdir(parents=True)
    with zipfile.ZipFile(args.source) as z:
        member = z.getinfo(LIB)
        if member.compress_type != zipfile.ZIP_STORED:
            raise ValueError('libapp.so must be stored for an in-place patch')
        original = z.read(member)
        if original[INSTRUCTION:INSTRUCTION + 4] != EXPECTED:
            raise ValueError('reviewed branch instruction not found')
        patched = bytearray(original)
        patched[INSTRUCTION:INSTRUCTION + 4] = REPLACEMENT
        crc = zlib.crc32(patched) & 0xffffffff
        local_crc_offset = member.header_offset + 14
        local_name_len, local_extra_len = struct.unpack_from('<HH', args.source.read_bytes(), member.header_offset + 26)
        data_offset = member.header_offset + 30 + local_name_len + local_extra_len
        if data_offset + INSTRUCTION >= args.source.stat().st_size:
            raise ValueError('computed libapp location is out of bounds')
        central_crc_offset = z.start_dir + z.infolist().index(member)  # placeholder, locate precisely below
        raw = args.source.read_bytes()
        marker = b'PK\x01\x02' + b'\x00' * 12 + struct.pack('<I', member.CRC)
        central = raw.find(marker, z.start_dir)
        if central < z.start_dir:
            # General ZIP central header: CRC at +16; match the filename too.
            pos = z.start_dir
            central = -1
            while raw[pos:pos + 4] == b'PK\x01\x02':
                name_len, extra_len, comment_len = struct.unpack_from('<HHH', raw, pos + 28)
                name = raw[pos + 46:pos + 46 + name_len].decode('utf-8')
                if name == LIB:
                    central = pos
                    break
                pos += 46 + name_len + extra_len + comment_len
            if central < 0:
                raise ValueError('central directory entry for libapp.so not found')
        central_crc_offset = central + 16
    out = args.output_dir / 'tablet-arm64-force-picker-structural-check.apk'
    shutil.copyfile(args.source, out)
    with out.open('r+b') as f:
        f.seek(data_offset + INSTRUCTION); f.write(REPLACEMENT)
        f.seek(local_crc_offset); f.write(struct.pack('<I', crc))
        f.seek(central_crc_offset); f.write(struct.pack('<I', crc))
    with zipfile.ZipFile(out) as z:
        if z.testzip() is not None:
            raise AssertionError('ZIP CRC validation failed')
        got = z.read(LIB)
        if got[INSTRUCTION:INSTRUCTION + 4] != REPLACEMENT:
            raise AssertionError('patched instruction validation failed')
        if z.getinfo(LIB).CRC != crc:
            raise AssertionError('central CRC validation failed')
    branch = args.output_dir / 'branch.bin'; branch.write_bytes(REPLACEMENT)
    crc_file = args.output_dir / 'crc.bin'; crc_file.write_bytes(struct.pack('<I', crc))
    manifest = {
        'source_sha256': SOURCE_SHA256, 'patched_apk_sha256': digest(out),
        'libapp_offset_in_apk': data_offset, 'instruction_offset_in_libapp': INSTRUCTION,
        'instruction_offset_in_apk': data_offset + INSTRUCTION,
        'expected': EXPECTED.hex(), 'replacement': REPLACEMENT.hex(),
        'crc32': f'{crc:08x}', 'local_crc_offset_in_apk': local_crc_offset,
        'central_crc_offset_in_apk': central_crc_offset,
    }
    (args.output_dir / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps(manifest, indent=2))


if __name__ == '__main__':
    main()
