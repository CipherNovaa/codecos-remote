#!/usr/bin/env python3
"""Create and verify a separately signed, non-installed OFA picker APK set.

The set contains the original base APK and the reviewed arm64 split produced
by prepare_force_screen_picker.py. It cannot update the vendor-signed app;
Android requires uninstalling that app before this set could be installed.
"""
import argparse
import hashlib
import shutil
import subprocess
from pathlib import Path


BASE_SHA256 = '30b010b0cce3d5b66be5a64136fc3f2677ac180d663be1c93232f56a985e54ea'
PATCHED_SPLIT_SHA256 = 'ce6b2c3d4ed15e99963766423cc6d67330b56134ecaab0777086bf5da53a7e37'
CONFIG_SPLITS = {
    'split_config.en.apk': '5ec6a7f3df7a72dedccddc9bd7f9f22c4931dda06a30b60b52b42651f8d20580',
    'split_config.fr.apk': 'ff7ad561f0391db4374b2247052a413a39749504850217e96810ecd7fcf38d75',
    'split_config.xxhdpi.apk': '95ab26d909fdf9aeff5f9374525f89c4e602c31e7df2082c951e6c61f7c7ad58',
}
KEY_ALIAS = 'ofa-local-review'
KEY_PASS = 'ofa-local-review-only'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(*args):
    subprocess.run(args, check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', type=Path, required=True)
    parser.add_argument('--patched-split', type=Path, required=True)
    parser.add_argument('--config-splits-dir', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--build-tools', type=Path,
                        default=Path('/opt/android-sdk/build-tools/37.0.0'))
    args = parser.parse_args()
    if digest(args.base) != BASE_SHA256:
        raise ValueError('base APK does not match the pinned OFA 1.0.7 input')
    if digest(args.patched_split) != PATCHED_SPLIT_SHA256:
        raise ValueError('patched split does not match the reviewed picker artifact')
    config_splits = []
    for name, expected in CONFIG_SPLITS.items():
        path = args.config_splits_dir / name
        if digest(path) != expected:
            raise ValueError(f'{name} does not match the pinned restored-app input')
        config_splits.append((path, name))
    if args.output_dir.exists():
        raise FileExistsError(f'refusing to overwrite {args.output_dir}')
    zipalign = args.build_tools / 'zipalign'
    apksigner = args.build_tools / 'apksigner'
    if not zipalign.is_file() or not apksigner.is_file():
        raise FileNotFoundError('missing zipalign or apksigner')
    args.output_dir.mkdir(parents=True)
    key = args.output_dir / 'local-review.keystore'
    run('keytool', '-genkeypair', '-noprompt', '-keystore', str(key),
        '-storepass', KEY_PASS, '-keypass', KEY_PASS, '-alias', KEY_ALIAS,
        '-keyalg', 'RSA', '-keysize', '4096', '-validity', '3650',
        '-dname', 'CN=OFA Local Review,OU=Codec,O=Local,L=Local,ST=Local,C=US')
    sources = [(args.base, 'base.apk'), (args.patched_split, 'split_config.arm64_v8a.apk')]
    sources.extend(config_splits)
    for source, name in sources:
        aligned = args.output_dir / f'aligned-{name}'
        signed = args.output_dir / name
        run(str(zipalign), '-p', '-f', '4', str(source), str(aligned))
        run(str(apksigner), 'sign', '--ks', str(key), '--ks-key-alias', KEY_ALIAS,
            '--ks-pass', f'pass:{KEY_PASS}', '--key-pass', f'pass:{KEY_PASS}',
            '--v1-signing-enabled', 'true', '--v2-signing-enabled', 'true',
            '--v3-signing-enabled', 'true', '--v4-signing-enabled', 'false',
            '--out', str(signed), str(aligned))
        aligned.unlink()
        run(str(zipalign), '-c', '-p', '-v', '4', str(signed))
        run(str(apksigner), 'verify', '--verbose', str(signed))
    print('REVIEW ARTIFACT ONLY; DO NOT INSTALL')
    for _, name in sources:
        print(f'{name} sha256={digest(args.output_dir / name)}')
    print('Install is intentionally a separate step because this certificate differs from OFA\'s.')


if __name__ == '__main__':
    main()
