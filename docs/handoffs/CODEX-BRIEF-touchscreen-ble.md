# Codex — OFA URC7985 remote customization (one comprehensive brief)

You've already patched firmware 0.5.7 (24 physical buttons → BLE; touchscreen digits 0–9 → BLE on
type-4/BLE slots). This bundles the remaining **remote-side** work into one pass so the author doesn't
have to keep prompting. **Division of labor:** you own the remote (firmware image + config channel +
E-Ink screen). Claude owns codec (the PC app) and **provides assets** (mgs-codec glyphs, logo PNGs)
and **binds** whatever keys you assign. Do the phases in order; **proceed with the suggested defaults
rather than waiting** — just report, at the end, the interface facts Claude needs (listed per phase).

## Current remote state
Devices: **DESKTOP** (slot1, type-4 Google-TV, BLE, `<HID_ADDR_SLOT1>` bond — THE codec device;
never delete/re-type it), **TELEVISION** (slot3 Sony IR), **AMPLIFIER** (slot2 Yamaha IR),
**LAPTOP** (slot4 type-3). Your tools/findings: `ofa/` (`TOUCHSCREEN.md`, `touch_pages.py`,
`device_order.py`, `check_touch_digits.py`, `firmware.py`). Screen firmware asset:
`artifacts/apk/x/assets/flutter_assets/assets/bin/ofa_touch_rt150t_0002t_v013f.bin` (RT150T).

## Phase 1 — R/G/Y/B/Return/Info → BLE HID (BLE slots only)   [highest value]
Make the six non-digit touchscreen keys transmit BLE HID for a BLE-connected (type-4) slot, like
digits already do; **IR slots unchanged** (TELEVISION/AMPLIFIER keep sending IR for these).
- Dispatcher **0x81d136**: BLE slot + digit ID 0x0d..0x16 → BLE via **0x81cb0c** builder; else IR at
  **0x81d082**. Digit→usage table file **0x16136**; HID pool **0x16420**. The six keys are logical
  IDs **0x1b 0x1f 0x2d 0x2e 0x2f 0x30** (R/G/Y/B/Return/Info — confirm order).
- Extend the BLE-routed set for BLE slots to include those six, each → a distinct keyboard usage.
  **Default to F14–F19** (R→0x69 G→0x6a Y→0x6b B→0x6c Return→0x6d Info→0x6e) unless something blocks it.
- Verify offline like `check_touch_digits.py` (BLE slot builds the new reports; IR slot still → 0x81d082).
- **Report to Claude:** the final usage per key.

## Phase 2 — custom labels on touchscreen buttons
Goal: a button's **displayed text** independent of its transmit key, so a key that sends F14 can read
"SUBS". Investigate the label path (`writeEInkDisplayInfo` label handling for ≥0xF0 IDs;
`message_rename_key`). If per-button labels are settable over the **config channel**, build a small
tool (like `touch_pages.py`); if it needs firmware to decouple label-from-keyID, patch it.
- **Report to Claude:** how labels are set + any per-label constraints (max chars, charset).

## Phase 3 — mgs-codec font on the E-Ink screen
Goal: the remote's touchscreen text in codec's **mgs-codec** pixel font (it's a pixel font — ideal
for E-Ink). Investigate two paths, pick the safer that works:
- **(a) per-button custom images (config):** if the screen accepts custom button bitmaps, Claude
  renders labels as mgs-codec bitmaps and you upload them — **no screen-firmware change** (preferred).
- **(b) screen-firmware font swap:** if labels are text-only (screen renders with a built-in font),
  locate the font glyph table in the RT150T screen firmware and replace it with mgs-codec glyphs,
  then reflash the screen firmware. ⚠ separate image from the main one; confirm a recovery path
  before flashing (your notes flag no safe rollback).
- Claude provides glyphs from `assets/mechui/mgs-codec.ttf` (family "MGS1 Codec") in **whatever
  bitmap format/size/metrics you specify.**
- **Report to Claude:** which path, and the exact glyph/image spec (dimensions, bit depth, encoding).

## Phase 4 — custom device logos (PNGs)
Goal: custom device icons (e.g., an mgs-codec "CODEC" logo for DESKTOP). Investigate whether logos
are uploadable images (config) or built-in selections; if uploadable, define format/size.
- **Report to Claude:** uploadable or not, and the image spec — Claude provides the PNGs.

## Safety (do not break)
Preserve the **DESKTOP slot-1 type-4 record + `…7A` bond**, the digit BLE path, the 24 physical-key
BLE map, IR-slot behavior (TELEVISION/AMPLIFIER), and the Sony/Fire rows. Verify every firmware
change offline before flashing (no safe rollback). Don't churn config records or invoke app sync
(that caused device dupes/divergence before). Phases 2–4 may turn out config-only (no flash) — prefer
that when it works.
