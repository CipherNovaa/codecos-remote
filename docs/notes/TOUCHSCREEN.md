# OFA touchscreen status — 2026-09-20

**The new main firmware is installed and boots.** DESKTOP retains its numeric
page and now has one additional page: MODE/SUBS/AUDIO/SPARE/RETURN/INFO. All
six keys produced clean F14–F19 press/release events in the live capture, and
the author confirmed the labels are visible and the combined page 2 looks good.
The layout is saved and read back. Original physical/digit mappings remain unchanged.

The complete current state, key map, rename commands, font candidate and logo
findings are in [CUSTOMIZATION-2026-09-20.md](CUSTOMIZATION-2026-09-20.md).
That handoff supersedes the investigation's original "not yet tested" status.
The four device records and pre-existing pages survived the flash unchanged.

- R/G/Y/B/Return/Info map to F14–F19 on type-4 BLE slots.
- `labels.py` changes displayed text independently of those actions (1–15 ASCII
  characters plus NUL); the main firmware exposes the CT01 label command.
- The corrected V2 screen-font candidate is offline only. Screen version `013f`
  is confirmed from the saved live inventory; recovery remains unverified.
  See [SCREEN-RECOVERY.md](SCREEN-RECOVERY.md) for the new width-table fix,
  previews and recovery evidence. No screen firmware was flashed.
- The stock logo feature selects compiled shortcut images. There is no identified
  per-device PNG upload path; `screen_images.py` is an offline converter.

## Original stock-firmware digit investigation (historical)

A screen-event-to-BLE digit path already exists in stock 0.5.7. The original
BLE24 patch did not add that path; enabling its page made it usable. This
corrects the older claim that touchscreen digits required new HID plumbing.
The notes below describe the original stock investigation, before the page
writes and CT01 extension documented above. Its proposed next steps are history.

## Evidence in the application firmware

All offsets refer to the pinned `ofa_touch_0.5.7.bin` APK asset described in
README.md. File-to-virtual-address translation adds `0x80de00`.

- Internal framed-message parser at VA `0x81b812` recognizes `a5 5a` requests
  and dispatches them at `0x81ad60`. These are internal screen messages, **not**
  config-characteristic `W` commands; do not send this frame to GATT.
- Command `0x43` selects the handler at file `0xd140`: device slot in frame
  byte 6, logical key in byte 7, an additional flag in byte 8.
- File `0xd1ac` checks the slot's BLE-enable bitmap through `0x8146e8`, and
  checks key-mover/macro overrides before choosing a transport.
- File `0xd1cc` accepts logical keys `0x0d..0x16`. Literal at file `0xd2b8`
  points to the ten-byte table at `0x16136`:
  `14 15 16 17 18 19 1a 1b 1c 1d` (HID-pool indices, hexadecimal).
- These pool indices resolve to keyboard usages `27 1e 1f 20 21 22 23 24 25 26`:
  digits **0, 1, 2, 3, 4, 5, 6, 7, 8, 9**.
- For an already connected matching slot, file `0xd1f8` calls `0x81d136`.
  Its connected-state branch sends the selected pool index and then zero
  through the same `0x81cb0c` report builder used by physical buttons.
- Non-digits and slots without the BLE-enabled bit follow `0x81d082` instead.
  The lookup is not limited to type 4; the existing digit table is shared.

Reproduce the bounded offline execution:

```sh
ofa/artifacts/venv/bin/python ofa/check_touch_digits.py artifacts/ble24/stock-0.5.7.bin
```

Passed: 40 digit events (ten digits × two slots × stock/candidate), with the
actual report builder producing press/release buffers. Non-digit and IR-only
controls take the IR branch. The emulator models a connected slot, keyboard
notifications enabled, and no macro/key-mover override; it stubs logging,
screen acknowledgments, LEDs/buzzer and the final radio submission. It does
not emulate the physical screen, radio or pairing, and is not a live-key test.

## Configuration lead

The firmware contains per-device page storage and a writer already reachable
from the config channel:

- `W 07 <slot>` reads page count and page records at file `0xaee6`.
  The argument is a **device slot**, not an individual touchscreen key.
- `W 06` iterates device slots at file `0xae76`. The repeated `01 00 ... 0c 00`
  response describes empty per-device records; it is not evidence of twelve
  fixed touchscreen keys or a prohibition on BLE.
- `W 17` reaches file `0xb4e6` and writes only the addressed device's 192-byte
  page block. Parsing reads a slot, page count, then records containing page
  index, a presentation byte, key count and logical key IDs. There are ten
  19-byte page records in this block (three header bytes plus up to 16 keys).
  The handler itself does not perform adequate length/bounds checks, so an
  eventual sender must validate the complete payload before transmission.
- `0x815320` serializes those page records back toward the screen. Values
  `0xf0` and above receive extra label handling. Numeric logical IDs already
  fall within the known transmit range above.

The meaning of the presentation byte, required refresh and any additional
screen-side gating still need confirmation. A separate screen firmware asset
is available in the APK at
`assets/flutter_assets/assets/bin/ofa_touch_rt150t_0002t_v013f.bin`; this has
not been flashed or identified as the remote's installed screen version.

Next useful live step: read and save `W 07` for CODEC and a populated IR device,
plus a fresh device inventory. Use a known numeric page as the layout reference
and prepare a strictly validated, reversible page-only change. Preserve CODEC's
type-4 record and bond, and the Sony device's page data. Do not replay historical
create/delete/restore sequences or invoke app sync to populate the keypad.

No new firmware candidate or live page write was made in this pass.
