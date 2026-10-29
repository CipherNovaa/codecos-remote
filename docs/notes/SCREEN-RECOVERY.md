# Screen firmware: recovery investigation, September 20, 2026

**No screen firmware or screen-update entry command has been sent. Recovery is
still unconfirmed.** The font candidate is ready for further offline review,
not installation. This follows the author's explicit requirement in
[CODEX-BRIEF-touchscreen-ble.md](CODEX-BRIEF-touchscreen-ble.md): confirm a
recovery path before flashing this separate screen controller.

## New facts

- The remote's saved, successful post-main-flash `W 02` inventory **reports
  screen version `013f`**, matching the stock screen asset used by the font
  builder. This is a version match, not a complete installed-image readback.
- There is a main-firmware debug command named `$reset einkMCU`. Its handler
  pulses GPIO data bit 0 low, delays with argument 10, and sets it high again.
  It has no screen command dependency, but no explicit boot-mode pin operation.
  **A reset alone is not evidence that the bootloader will wait for an update.**
- Normal screen update entry **erases a flash sector and writes a boot flag**
  before resetting. It is not a read-only capability probe.
- The screen bootloader at addresses `0000–0fff` is absent from the APK asset.
  Consequently its validation, retry, timeout and recovery behavior cannot be
  established from that asset. The entry routine touches sector `0e00`, so do
  not assume the entire region below the application is immutable boot code.

## Reproduce the offline checks

```sh
ofa/artifacts/venv/bin/python ofa/check_screen_recovery.py \
  artifacts/ble24/stock-0.5.7.bin \
  artifacts/apk/x/assets/flutter_assets/assets/bin/ofa_touch_rt150t_0002t_v013f.bin \
  --capture ofa/artifacts/custom-touch-v2/postflash.json
```

This executes actual ARM application routines with explicit ROM/peripheral
hooks. The hooks model successful peripheral operations; they do **not**
execute the missing bootloader or prove hardware recovery. Saved output is
`artifacts/screen-font-v2/recovery-audit.json`.

### Version path

All screen addresses here are VA; subtract `1000` for screen file offsets.
Main VA = main file offset + `80de00`.

1. Screen `5bb0` returns `013f`. Handler `1c5b6` (UART command `09`, ordinary
   framing) returns those two bytes in big-endian order. Normal application
   command `02`, handler `1cbd0`, includes the same bytes first in its reply.
2. Main response handler `81aaf4`, command-02 branch `81abb6`, stores them at
   RAM `20b166` and `20b167`. Command A0's response also updates these bytes.
3. Main config `W 02`, tail at `818ad4`, appends those cached bytes last.
4. Saved postflash inventory is 512 bytes: opcode, twelve 22-byte device
   records, twelve 20-byte activity records, seven trailer bytes. Last two
   bytes are `01 3f`. The ARM check also uses distinct sentinel versions to
   verify byte order and both halves of the cache-to-inventory path.

No new live read is needed to recover this evidence. A future preflash check
should obtain a fresh `W 02` using the existing inspection tool.

### Main hardware reset

The six-entry debug-command table at VA `823eb8` includes:

| String | Handler |
| --- | --- |
| `$read remote data` | `8185c4` |
| `$read app data` | `8185e2` |
| `$read settings data` | `818600` |
| `$start ble test,` | `818670` |
| `$stop ble test` | `81872c` |
| `$reset einkMCU` | `817b94` |

The first three read fixed-size main RAM structures; they are not arbitrary
screen memory readers. The reset handler uses `80ef1e(0)` to obtain GPIO mask
`1`, updates register `40001000`, calls the delay pointer at `200158`, and
restores the bit high. The emulator checks this exact sequence. It has not
been invoked on the remote during this investigation.

### Normal update entry and boot flag

Main `81b5de` checks a screen-bootloader state flag (bit `400`). In ordinary
application state it sends header `4534`, command `40`. On the screen that
framing selects handler `1c5e0`; **command `40` under A55A application framing
has a different meaning**. Never identify a serial operation by opcode alone.

For zero-length payload the screen handler requests:

1. Reply/acknowledgment and flash initialization.
2. Unlock selection through `1afbc(0e00)` → `4fc4(0, 2)`.
3. Flash sector-erase mode (`4fe4(2)`), then erase address `0e00` (`5034`).
4. Flash write mode (`4fe4(1)`), then write bytes **`55 aa` at `0e50`** (`50c0`).
5. UI event `46`, shutdown preparation, system reset through `684c`.

The main controller subsequently uses bootloader header `4254`, commands
`46` (four-byte start argument), `48` (sequence + image data), and `47` (exit
request). Their main-side encoders are identified, but the bootloader-side
implementation is absent. **No screen uploader is implemented.** The existing
`flash.py` is only for the main RTL8762C APP image.

## Chip and hardware recovery lead

The screen's flash, GPIO and peripheral addresses, plus `5a5a/a5a5` flash
unlock writes, match the HC32L18x driver register layout. This is a **family
identification inference**, not a confirmed chip marking; related HC32 parts
share registers. The manufacturer's [HC32L180 product page](https://www.xhsc.com.cn/product/1259.html)
documents ISP/IAP and SWD support and links the
[HC32L18x driver source](https://oss-nc-beijing-2.cecloudcs.com/doc-hc/HC32L18x_DDL_Rev1.1.0.zip).
The reviewed package uses FLASH `40020000`, GPIO `40020c00`, and the same
erase/write modes and unlock sequence.

That does not establish that programming pads are accessible on this remote,
that debug access is enabled, or that a main-controller pin can force recovery.
The useful next evidence is **the actual screen-controller marking and board
test-pad layout**, followed by a non-erasing read of the bootloader/flash if
hardware access permits. Do not clear read protection: that may erase the
bootloader and configuration. A verified bootloader dump can establish whether
the existing main-controlled reset supports a Bluetooth recovery procedure.

Until then, the existing working screen stays installed. No change to DESKTOP,
its bond, physical/digit BLE mapping, Sony/Fire rows or IR behavior is needed.

## Font candidate correction discovered during this pass

The original `screen-font-v1` candidate changed glyph widths but missed an
independent wrapping-width table. **V1 is superseded and must not be flashed.**
The actual formatter `d26a` calls `d844`, which uses 91 u32 widths at VA
`1f9f4` (file `1e9f4`) for ASCII 32–122 and a constant fallback width at
`d856` (file `c856`). V2 updates that table and the fallback immediate to match
the font. No addresses, callbacks or executable control flow move.

`screen_fonts.py` now defaults to a 22-pixel source size (8-pixel cap height),
which keeps all six current labels on one line. `--font-size 32` gives a larger
12-pixel cap height but wraps AUDIO, SPARE and RETURN onto two lines. Both
variants pass 558 font callback cases and 105 actual ARM label-formatting cases,
including all printable ASCII bytes repeated to the maximum label length.

| Candidate | SHA-256 | Artifact directory |
| --- | --- | --- |
| V2, default 22 px | `70a098280fcd207e2c0c9f584979853477bf126f08420af023dd53f14ed4c8cd` | `artifacts/screen-font-v2-22px/` |
| V2, optional 32 px | `2105b0b0f4a9a06ab05ca52c78a942f365cb2c430cbda12fffede247c5bcfcd1` | `artifacts/screen-font-v2/` |

Each directory has decoded glyph previews and `mgs-page2.png`: a native-size
168×280 content-area illustration using the actual font metrics and ARM
wrapping result. Borders and centering are illustrative, not a complete LVGL
or physical E-Ink rendering test. Custom font installation remains pending.
