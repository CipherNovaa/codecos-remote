#!/usr/bin/env python3
"""Clone the reviewed picker build under a new Android package identifier.

The source and destination identifiers have equal byte length.  This allows a
mechanical replacement in compiled UTF-8/UTF-16 Android string pools without
rebuilding the Flutter app.  The cloned artifacts are newly zip-aligned and
signed; they do not modify an installed application or contact Bluetooth.
"""
import argparse
import hashlib
import struct
import subprocess
import zipfile
import zlib
from pathlib import Path


OLD = b'com.uei.ofa.advanced'
NEW = b'com.uei.ofa.codecdev'
KEY_ALIAS = 'ofa-local-review'
KEY_PASS = 'ofa-local-review-only'


def run(*args):
    subprocess.run(args, check=True)


def clone(source, destination):
    replacements = 0
    with zipfile.ZipFile(source) as original, zipfile.ZipFile(destination, 'x') as result:
        for member in original.infolist():
            data = original.read(member)
            count = data.count(OLD) + data.count(OLD.decode().encode('utf-16le'))
            data = data.replace(OLD, NEW).replace(OLD.decode().encode('utf-16le'), NEW.decode().encode('utf-16le'))
            if member.filename.endswith('.dex'):
                # A DEX header contains SHA-1(bytes[32:]) followed by the
                # Adler-32 of bytes[12:]. Replacing the application ID in
                # classes.dex requires refreshing both fields.
                data = bytearray(data)
                data[12:32] = hashlib.sha1(data[32:]).digest()
                data[8:12] = struct.pack('<I', zlib.adler32(data[12:]) & 0xffffffff)
                data = bytes(data)
            replacements += count
            info = zipfile.ZipInfo(member.filename, member.date_time)
            info.compress_type = member.compress_type
            info.external_attr = member.external_attr
            info.comment = member.comment
            info.extra = member.extra
            result.writestr(info, data, compress_type=member.compress_type)
    return replacements


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-dir', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--build-tools', type=Path,
                        default=Path('/opt/android-sdk/build-tools/37.0.0'))
    args = parser.parse_args()
    if len(OLD) != len(NEW):
        raise AssertionError('package identifiers must remain equal length')
    if args.output_dir.exists():
        raise FileExistsError(f'refusing to overwrite {args.output_dir}')
    apk_names = sorted(path.name for path in args.source_dir.glob('*.apk'))
    if apk_names != ['base.apk', 'split_config.arm64_v8a.apk', 'split_config.en.apk',
                     'split_config.fr.apk', 'split_config.xxhdpi.apk']:
        raise ValueError('expected the complete five-split review set')
    zipalign = args.build_tools / 'zipalign'
    apksigner = args.build_tools / 'apksigner'
    key = args.source_dir / 'local-review.keystore'
    if not key.is_file():
        raise FileNotFoundError('review signing key is absent')
    args.output_dir.mkdir(parents=True)
    for name in apk_names:
        unsigned = args.output_dir / f'unsigned-{name}'
        aligned = args.output_dir / f'aligned-{name}'
        output = args.output_dir / name
        count = clone(args.source_dir / name, unsigned)
        if count == 0:
            raise AssertionError(f'{name} has no package identifier to clone')
        run(str(zipalign), '-p', '-f', '4', str(unsigned), str(aligned))
        run(str(apksigner), 'sign', '--ks', str(key), '--ks-key-alias', KEY_ALIAS,
            '--ks-pass', f'pass:{KEY_PASS}', '--key-pass', f'pass:{KEY_PASS}',
            '--v1-signing-enabled', 'true', '--v2-signing-enabled', 'true',
            '--v3-signing-enabled', 'true', '--v4-signing-enabled', 'false',
            '--out', str(output), str(aligned))
        unsigned.unlink()
        aligned.unlink()
        run(str(apksigner), 'verify', '--verbose', str(output))
        print(f'{name}: package-string-replacements={count} sha256={hashlib.sha256(output.read_bytes()).hexdigest()}')
    print('CLONE REVIEW ARTIFACT ONLY; package=com.uei.ofa.codecdev')


if __name__ == '__main__':
    main()
