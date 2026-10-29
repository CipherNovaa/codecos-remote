# OFA customization handoff — September 20, 2026

## Installed main firmware: CT01

**Main firmware was flashed, validated by the remote and booted successfully.**
The new `W 0B` capability and six label writes/readbacks work on the hardware.
**the author confirmed all six labels display correctly; the live capture confirms each
F14–F19 press and release.** The buttons were subsequently consolidated onto
page 2 in a two-column, three-row grid, with verified configuration readback;
the author subsequently confirmed that the combined page looks good.
Do not confuse the offline screen-font candidate with this installed main image.

- Installed main image SHA-256:
  `1f5db4d75778e930f5c4b9653a2121a1991d8fada0d13219a584f78504364e64`.
- Built with `custom_firmware.py` from the pinned stock 0.5.7 asset; retains
  physical BLE24, stock digit handling and the other profiles' rows.
- Main APP grows by 608 bytes, within the stock image's existing final 4 KiB
  sector. Existing payload bytes are not shifted. No screen firmware was sent.
- Sept 20, 17:38–17:40 local time: OTA transfer, remote validation `10 03 01`, activation.
  The reset interrupted the write response with GATT Unlikely Error, as in
  earlier successful runs. A fresh normal-mode read and CT01 query prove boot.
- Firmware version still reads 0.5.7. CT01 identifies the extension.
- All four device records and all pages matched before/after the flash exactly.
  Subsequent label writes changed only DESKTOP's pages and its custom labels.
  The later compact operation changed only its page layout.
  No app sync, slot create/delete/retype, or bond removal was performed.
- DESKTOP remains slot 1, type 4, identity `<HID_ADDR_SLOT1>`.
  AMPLIFIER slot 2/type 0, TELEVISION slot 3/type 1, LAPTOP slot 4/type 3.
- BlueZ still reports DESKTOP paired, bonded and trusted after all six edits.

Local evidence (ignored binary/JSON artifacts):

- `artifacts/custom-touch-preflash-02.json`: fresh original inventory/pages.
- `artifacts/custom-touch-v2/flash-01.json`: transfer/validation/activation log.
- `artifacts/custom-touch-v2/postflash.json`: identical inventory/pages after boot.
- `artifacts/custom-touch-v2/label-{red,green,yellow,blue,return,info}.json`:
  sequential backups, label/page changes and verified readbacks.
- `artifacts/custom-touch-v2/live-touch-keys.jsonl` and `live-touch-verification.json`:
  F14–F19 each captured once with clean press/release, plus physical Up.
- `artifacts/custom-touch-v2/compact-page2.json`: backed-up and verified consolidation.
- `artifacts/custom-touch-v2/page2-offline-check.json`: six-label serialization
  (104 bytes, 106 including outer slot/page fields, within the 128-byte buffer).

## Phase 1 — exact interface for Claude

All are **keyboard page 0x07**, not consumer usages. Named aliases and original
numeric-page keys send the same press/release reports. Additional routing is
restricted to **type-4 AND BLE-enabled** slots. Other types and IR-only slots
retain their previous paths. Existing digit BLE routing is unchanged.

| Original key | Logical ID | Named alias | Current label | HID usage | Linux scan / key |
| --- | --- | --- | --- | --- | --- |
| Red | `2d` | `fa` | MODE | `69` | `70069` / F14 |
| Green | `30` | `fb` | SUBS | `6a` | `7006a` / F15 |
| Yellow | `2e` | `fc` | AUDIO | `6b` | `7006b` / F16 |
| Blue | `2f` | `fd` | SPARE | `6c` | `7006c` / F17 |
| Return / Last CH | `1b` | `fe` | RETURN | `6d` | `7006d` / F18 |
| Info | `1f` | `ff` | INFO | `6e` | `7006e` / F19 |

The screen's English table confirms this order. **Return is Last CH, not the
physical navigation Back button** (logical `31`). Suggested codec actions are
F14 screen mode, F15 subtitles, F16 audio track, F17 spare; F18/F19 are available
for Claude/the author to bind. No codec application code was changed in this pass.

Physical Power remains F13 (`07:68`) from the installed BLE24 base.

## Phase 2 — independent button labels

Installed DESKTOP pages:

1. Original numeric keypad and six stock-labeled extra keys, unchanged.
2. All six named controls, aliases `fa fb fc fd fe ff`, arranged as:

   | Left | Right |
   | --- | --- |
   | MODE | SUBS |
   | AUDIO | SPARE |
   | RETURN | INFO |

The initial three-controls-per-page limit was a conservative tool setting, not
a display limitation. Page 3 was removed by rewriting only the page list.

`labels.py` provides backed-up, verified, reversible config edits. Labels use
additional generic text pages; the tool does not overwrite the numeric grid.
Select another device and then DESKTOP to refresh screen pages after editing.

```sh
# Phone Bluetooth off; remote in ACTIVITIES + DEVICES mode.
# Default: read and show plan. Add --apply to save; choose a NEW backup filename.
ofa/artifacts/venv/bin/python ofa/labels.py set \
  --slot 1 --key green --text SUBTITLES --backup /tmp/ofa-subs-plan.json

ofa/artifacts/venv/bin/python ofa/labels.py set \
  --slot 1 --key green --text SUBTITLES --backup /tmp/ofa-subs-change.json --apply

# Undo that verified operation, provided its pages/label have not since changed.
ofa/artifacts/venv/bin/python ofa/labels.py restore \
  --source /tmp/ofa-subs-change.json --backup /tmp/ofa-subs-undo.json --apply
```

Constraints:

- **1–15 printable ASCII characters**, then NUL in the 16-byte stored field.
  Sixteen non-NUL characters are rejected: the screen formatter uses `strlen`.
- The stock OFA font lacks some ASCII punctuation, which may render blank;
  letters, digits, spaces and simple punctuation such as hyphens are practical.
  Text may wrap. Short labels such as MODE/SUBS/AUDIO work best.
- Keys supported by this extension: `0`…`9`, `red`, `green`, `yellow`, `blue`,
  `return`, `info`. This does not rename every arbitrary IR function.
- Digit aliases `f0`…`f9` retain keyboard 0…9; their text can also be renamed.
- The helper now uses at most **six custom labels per added page** and
  validates expanded messages against the main firmware's screen buffer.
  Each 82×68 button occupies a grid cell with 85-pixel horizontal / 71-pixel
  vertical spacing; six fit inside the existing 168×280 content area.
  Ten pages per device; 128 custom-label records globally.
- CT01 must be reported before any edit. Only existing type-4 BLE-enabled
  records are accepted. No IR learning or slot recreation is needed.
- Tool readback proves values in running firmware; a separate power-cycle
  persistence test has not yet been performed.

To combine older custom-label pages without rewriting labels or key mappings:

```sh
ofa/artifacts/venv/bin/python ofa/labels.py compact \
  --slot 1 --backup /tmp/ofa-compact.json --apply

# Restore that page-only operation if needed:
ofa/artifacts/venv/bin/python ofa/touch_pages.py restore \
  --backup /tmp/ofa-compact.json --save-backup /tmp/ofa-compact-undo.json
```

`compact` preserves the numeric page, all other layouts, every key and label,
and all other devices. It only combines consecutive custom-text pages.

Wire protocol (all numbers hexadecimal; `slot` is 1–12):

| Request | Response |
| --- | --- |
| `57 0b 00` | `0b 01 43 54 30 31` (CT01) |
| `57 0b 01 slot alias label[16]` | `0b 01` on set |
| `57 0b 02 slot alias` | `0b 01` on remove |
| `57 0b 03 slot alias` | `0b 01 present label[16]` |

Error status `02` means invalid/unsupported; `03` full/corrupt label table.
Existing stock custom-label storage and FTL persistence are reused. Stock
`W 41` enters IR learning and is **not** the label-only API. The new opcode is
an unused stock dispatch entry; the main image implements it in appended code.

## Phase 3 — font: corrected V2 candidate, no screen flash

Latest investigation and reproducible recovery checks:
[SCREEN-RECOVERY.md](SCREEN-RECOVERY.md). **V1 is superseded:** its glyph
callbacks passed, but it missed the screen's separate label-wrapping widths.
V2 fixes that and tests the actual ARM formatter as well.

The investigated screen path sends key IDs and 16-byte labels, not per-button
bitmaps. The screen renders labels using compiled LVGL fonts. **No config-only
custom label-image upload path was found.** The available path is a font swap
in the separate RT150T firmware.

`screen_fonts.py` already reads `assets/mechui/mgs-codec.ttf` directly. Claude
need not supply more glyph assets for this candidate:

```sh
python ofa/screen_fonts.py \
  artifacts/apk/x/assets/flutter_assets/assets/bin/ofa_touch_rt150t_0002t_v013f.bin \
  --font assets/mechui/mgs-codec.ttf --output-dir /tmp/ofa-font-candidate
```

Exact format and placement:

- Screen asset SHA-256
  `64670a392082daa0d708eb1f5f5ff58227e7d01d60da480ed56b157f5a60980d`,
  180608 bytes. Screen VA = file offset + `1000`.
- Three OFA text faces at file `2bd98`, `2bd7c`, `2bd60`.
  The general custom-button renderer explicitly selects the `2bd7c` face.
  Two additional general LVGL faces remain stock in this candidate.
- Default source TTF at **22 px**, giving 8-pixel capital height and typical
  12.375-pixel advance (rounded to 12 by the screen's font callback). All six
  current labels fit on one line. `--font-size 32` is also tested: 12-pixel
  capitals, typical **16×12** bitmap / **18-pixel advance**, but AUDIO, SPARE
  and RETURN wrap onto two lines.
  Glyphs are cropped, so dimensions and offsets are per glyph, not fixed cells.
- Packed **1-bit alpha**, foreground 1, MSB first, continuous row-major bits;
  **no row padding**. Each glyph starts at a byte boundary. Identical glyph
  bytes are shared (e.g. this font's uppercase/lowercase designs).
- LVGL glyph descriptors retain the 8-byte format: bitmap offset (20 bits),
  advance ×16 (12 bits), width/height (u8), x/y offsets (s8).
  Original line heights 14/14/16 and baseline 2 remain unchanged.
- Supported ASCII mappings are replaced, while existing cmaps and glyph IDs
  are retained. Unsupported punctuation remains unsupported. Non-ASCII glyph
  shapes are retained but quantized from 2-bit to 1-bit coverage.
- Bitmap use at 22 px is 558/1552, 552/1386 and 576/1921 bytes respectively.
  No pointers, image size or allocated regions move.
- V2 also updates the 91-entry u32 wrapping-width table at file `1e9f4` and
  one instruction's immediate at file `c856` (fallback width). The original
  screen wrapping routine does not consult LVGL's font metrics; both must agree.
- Default offline candidate SHA-256
  `70a098280fcd207e2c0c9f584979853477bf126f08420af023dd53f14ed4c8cd`.
  Candidate and before/after previews: `artifacts/screen-font-v2-22px/`.
  `mgs-page2.png` illustrates the native-size button layout with firmware-derived
  glyphs and wrapping; borders/centering are illustrative.
- Optional 32 px V2: `artifacts/screen-font-v2/`, SHA-256
  `2105b0b0f4a9a06ab05ca52c78a942f365cb2c430cbda12fffede247c5bcfcd1`.

**Screen flashing is withheld under the brief's recovery requirement.** The
supplied asset begins at VA `1000`; it does not include the screen bootloader
at `0000–0fff`. The traced main-firmware update entry sends a UART command to
the running screen app. That is not proof of recovery from a broken screen
app. Neither a forced bootloader entry nor usable SWD recovery has been verified.
The saved live `W 02` read **does report screen version `013f`**, now confirmed
by tracing/emulating the cached-version path. A complete installed-image readback
is still absent. A stock asset alone is not a recovery route. No screen update
command was sent; entry itself erases sector `0e00` and writes a boot flag.
The font-only artifact has not been integrated into a screen OTA uploader;
`flash.py` accepts main APP images only and must not be used for this asset.

The font struct interpretation was cross-checked with LVGL's
[font declarations](https://github.com/lvgl/lvgl/blob/v8.3.11/src/font/lv_font_fmt_txt.h)
and [lookup implementation](https://github.com/lvgl/lvgl/blob/v8.3.11/src/font/lv_font_fmt_txt.c),
then verified against the screen's actual ARM callbacks.

## Phase 4 — device PNGs: converter ready; upload path absent

The app logo option selects **compiled shortcut icons**, not arbitrary per-device
images. Executing screen function `db54` for all 256 IDs returns only seven
flash-resident descriptors: Netflix (`22`), Prime (`23`), YouTube (`24`), HBO (`32`),
Rakuten (`34`), Disney (`36`), Paramount+ (`37`). An enable flag selects logos
versus ordinary text; no pixel bytes arrive in the examined page protocol.

There is **no implemented per-device logo uploader**. Replacing a built-in
shortcut bitmap would affect that shortcut across devices and would still
require the screen flash; it would not give DESKTOP its own logo. Per-device
logos require new screen rendering/storage/protocol work and the recovery route
above. Claude should not treat a prepared PNG as already uploadable.

The offline `screen_images.py` tool extracts originals or sizes/converts PNGs:

```sh
python ofa/screen_images.py extract \
  artifacts/apk/x/assets/flutter_assets/assets/bin/ofa_touch_rt150t_0002t_v013f.bin \
  --output-dir /tmp/ofa-original-icons

python ofa/screen_images.py prepare /path/to/logo.png \
  --width 82 --height 74 --output-dir /tmp/ofa-logo
```

Known image format: LVGL indexed 1-bit (`cf=7`), 8-byte palette (opaque white
then black RGBA), then MSB-first pixels, **byte-padded rows**, 1 = black.
This differs from the font's continuous packing. Five stock icons are 82×74
(822 data bytes); Rakuten and Paramount+ are 83×75 (833 bytes).
The converter keeps aspect ratio, centers on white, composites transparency,
uses nearest-neighbor scaling by default and threshold 128; `--resample lanczos`
is available. It writes a preview, packed bytes and manifest, never Bluetooth.

For a future device-row design, **168×52** fits the traced device-row widget,
but this is a design canvas, **not an existing upload contract**. Full screen
bounds are 168×384. Do not promise asset loading until the new screen path exists.

## Verification and remaining hardware check

- `check_custom.py`: 2,048 touchscreen cases across four types, BLE on/off,
  distinct event/selected slots; preserved physical map; wake/switch paths and
  macro/key-mover precedence; actual ARM set/read/update/delete in all 12 slots;
  bounded FTL writes; preserved other labels/records; malformed/overlong inputs
  rejected; original screen-message serializer emits independent custom text.
- `check_flash.py`: reconstructed exact encrypted image and verified that a
  partial update, rejection, disconnect, async error or failed validation
  prevents activation. Live main update validation/boot subsequently succeeded.
- `check_screen_fonts.py`: 558 actual ARM font lookup/metrics/bitmap cases on
  stock/candidate; bounds; independent NUL-terminated custom-label lookup;
  all 256 icon IDs and disabled-logo behavior; 105 actual ARM wrapping cases.
  Both 22 px and 32 px V2 variants pass.
- `check_screen_recovery.py`: actual screen version getter, main response/cache/
  inventory path, GPIO reset sequence and update entry with modeled flash calls.
  Saved live inventory matches version `013f`; bootloader recovery is unverified.
- PNG conversion: transparency, row padding, non-byte widths and roundtrip at
  stock icon sizes and proposed device-row size.

Live verification at Sept 20, 17:59 local time: scans `70069`…`7006e` each produced
one F14–F19 press/release pair; the author confirmed that all six labels were visible.
Physical Up also appears. This capture did not include the requested digit/OK
samples, so it is not a fresh live regression check of the original full map.
The existing offline checks preserve those mappings.

The subsequent page-2 consolidation was tested against the actual ARM page
writer/reader/serializer and read back on the remote. No firmware or label
values changed during consolidation. Leave config mode and select another
device, then DESKTOP, to load the new page layout. The saved logger capture is
under `artifacts/custom-touch-v2/`; the logger was stopped before configuration.
Never run old pairing/restore tools or app sync to fix a missing page; inspect
the current state first.
