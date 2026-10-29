#!/usr/bin/env python3
"""Prepare an unsigned OFA arm64 split that exposes the stock E-paper picker.

It changes exactly one AArch64 instruction in libapp.so: the conditional that
selects normal versus forced E-paper OTA becomes an unconditional branch to
the existing force path.  The force path merely presents the app's .bin file
picker and then calls its unchanged stock uploader.

The result deliberately has no APK signature and cannot be installed.  A
separate, reviewed signing step is required before any device interaction.
"""
import argparse
import hashlib
import shutil
import zipfile
from pathlib import Path


SOURCE_SHA256 = '5b6bd370aa7921c782442a08f2b7dc94c48de2cad32d884a7646e78653d3405d'
LIB_PATH = 'lib/arm64-v8a/libapp.so'
INSTRUCTION_OFFSET = 0x5C9980
EXPECTED = bytes.fromhex('c1002037')  # tbnz w1, #4, default-screen-OTA
REPLACEMENT = bytes.fromhex('01000014')  # b force-screen-OTA


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare(source, output):
    if sha256(source) != SOURCE_SHA256:
        raise ValueError('source is not the pinned OFA 1.0.7 arm64 split')
    if output.exists():
        raise FileExistsError(f'refusing to overwrite {output}')
    with zipfile.ZipFile(source) as original:
        members = original.infolist()
        app = original.read(LIB_PATH)
        if app[INSTRUCTION_OFFSET:INSTRUCTION_OFFSET + 4] != EXPECTED:
            raise ValueError('libapp.so instruction does not match the reviewed branch')
        patched = bytearray(app)
        patched[INSTRUCTION_OFFSET:INSTRUCTION_OFFSET + 4] = REPLACEMENT
        # Preserve every ZIP member and compression method.  Signatures become
        # invalid by design because libapp.so's CRC and content change.
        with zipfile.ZipFile(output, 'x') as result:
            for member in members:
                contents = patched if member.filename == LIB_PATH else original.read(member)
                info = zipfile.ZipInfo(member.filename, member.date_time)
                info.compress_type = member.compress_type
                info.external_attr = member.external_attr
                info.comment = member.comment
                info.extra = member.extra
                result.writestr(info, contents, compress_type=member.compress_type)
    with zipfile.ZipFile(output) as result:
        verify = result.read(LIB_PATH)
    if verify[INSTRUCTION_OFFSET:INSTRUCTION_OFFSET + 4] != REPLACEMENT:
        raise AssertionError('output patch verification failed')
    return sha256(output), hashlib.sha256(verify).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    apk_sha, lib_sha = prepare(args.source, args.output)
    print('UNSIGNED; DO NOT INSTALL')
    print(f'output_sha256={apk_sha}')
    print(f'patched_libapp_sha256={lib_sha}')
    print(f'patch_offset=0x{INSTRUCTION_OFFSET:x} {EXPECTED.hex()} -> {REPLACEMENT.hex()}')


if __name__ == '__main__':
    main()
