# OFA e-paper custom-image screensaver — offline build design (flash-once, push-images-freely)

Offline only. Nothing flashed/transmitted. Base = the INSTALLED mgs-font screen image.

## 0. Base image (confirmed)
- Installed = `ofa/artifacts/screen-font-v5-22px-condensed/candidate-screen-font-DO-NOT-FLASH.bin`
- SHA-256 `03ea324239012d7c5bf3266af898a6efdbdf8d5ca1f5eac8c33eeb007fbe9b51`, 180608 B.
- Matches the flash-journal plan `image_sha256` (transfer.json, 2026-09-22, 1412 pkts) → this is what is on the remote. Build ON THIS (keeps the font). VA = file + 0x1000.

## 1. THE DECISIVE RECOVERY-SAFETY FINDING (reshapes the build)
The screen's a55a app-command channel is driven ONLY by the main MCU. The PC's only bulk→screen pipe is `W A1/A2`, which main turns into the screen BOOTLOADER OTA (`4534/0x40` enter → screen resets into bootloader → `4254/0x46/0x48/0x47` reflash). Crucially, main sends screen cmd `0x40` with ZERO payload (handler `0x1c5e0` requires len==0) — it does NOT forward the `W A1` argument. So the screen APP cannot distinguish "recovery reflash" from "image push" on the wire.
⇒ A screen-fw-ONLY runtime image push that is recovery-safe is IMPOSSIBLE: the only way to make the app receive 8 KB is to intercept `0x40`/`0x48` in the app, and making `0x40` not-reset is exactly what breaks recovery (recovery depends on the running app handling `0x40`→set flag `55aa@0xE50`→reset into bootloader; the bootloader never self-enters).
⇒ Recovery-safe + truly-no-reflash requires a SMALL ADDITIVE MAIN opcode (the proven W0B/CT01 pattern) that forwards PC image chunks to the screen on a NEW a55a command, leaving `0x40/0x46/0x47/0x48` byte-for-byte intact. Cost: flash BOTH images once (still "flash once, then push freely").

### Honest tradeoff for item 3
- **Design A (recommended): two firmwares, flashed once.** Screen fw = idle screensaver page + flash-slot image + new a55a cmd 0x50 image-receiver. Main fw = additive W-opcode forwarding chunks → screen cmd 0x50. `0x40` OTA path untouched → recovery fully safe. Thereafter any PNG pushes over BLE, no reflash.
- **Design B (screen-only, no main flash): idle screensaver works, but the IMAGE can't be updated recovery-safely at runtime.** Ship a default image baked in the app region; changing the image later needs the main opcode or a screen reflash. Screen-only + runtime image push + recovery-safe = not simultaneously achievable (per the finding above). Do NOT intercept `0x40` (hard rule).

Recommendation: build Design A. It is the only design meeting all of the author's requirements at once, and the main change is the same additive, spare-sector, digest-recompute class the author already flashed safely 5×, with the recovery path (0x40/46/47/48) provably unmodified.

## 2. SCREEN firmware changes (base = mgs candidate)
Reserved image slot in free flash above app end (0x2d180 file / VA; app region OTA reflash never touches it):
- Slot @ physical flash **0x2E000 .. 0x30000** (16 × 512-B sectors = 8 KB). Layout:
  `[ 'S','S','A','V' (4) ][ len u16 = 8072 ][ 0,0 (2) ][ 8072 image bytes ]` = 8080 B ≤ 8 KB.
- Image bytes = exactly what `screen_images.py prepare --width 168 --height 384` / `screensaver_prep.py` emit: 8-byte palette (white then black RGBA) + 21×384 MSB-first byte-padded rows, 1=black. (lv_img cf=7 indexed-1bpp.)

Appended code region: file **0x2C180 .. ~0x2C600** (VA 0x2D180+, grows candidate ~1–1.5 KB, sector-round to 0x2C400/0x2C600). New code = (i) page builder, (ii) idle hook body, (iii) restore hook body, (iv) cmd-0x50 image-receive handler.

Patches (byte-allowlist — every changed byte accounted for):
1. **Page-builder table** `0x1ec94` entry for state **0x59**: word @ `0x1ec94 + 0x59*4 = 0x1edf8` : `0x1ee65` → `<new_builder>|1`. (4 bytes.)
2. **Dispatcher jump table** `0x1c304` (base 0x1c306, cmd=idx+1): claim cmd **0x50** (idx 0x4f). Its 16-bit table slot currently points to the error stub. Repoint to a 4-byte trampoline (`bl <handler>; pop`) placed in a free stub region, or add a stub in appended space reachable by the table's ±range. (2-byte table entry + small stub.)
3. **Idle hook** at the LPTIM-wake consumer `0x1d67c` (`bl 0xbeec`): replace with `bl <idle_hook>`; idle_hook calls `0xbeec` then runs the counter. (4 bytes at 0x1d67c.)
4. **Restore hook** at activity `0xbe10` prologue: divert first instr to `bl <restore_hook>` which does the original work then, if target/shown state==0x59, restores saved state + zeroes counter. (Trampoline; 2–4 bytes at 0xbe10 + relocated prologue in appended code.)
5. Appended code + reserved-slot region (all previously 0x00/absent → new).

Reused primitives (ABIs confirmed by disasm):
- `lv_obj_create(NULL)=0x15548`, `lv_img_create(parent)=0x12d58`, `lv_img_set_src(img,&dsc)=0x13860`.
- Framebuffer `0x20004c3c` (8064 B); full RAM write `0x19da(buf)`; refresh `0x1858`; force-full `0x4d9c(1)`.
- Flash: init `0x4edc(clkdiv,1)` (clkdiv from `0x13e8(sysclk, 0x3d0900)` as in 0x1c5e0); unlock-select `0x1afbc(addr)`; mode `0x4fe4(1=write|2=erase)`; erase-sector `0x5034(sector)`; program `0x50c0(dest,src,len)` — self-verifying byte programmer, returns 0=ok/1=verify-fail/9=timeout. Controller `0x40020000`, unlock `5a5a/a5a5`→`[0x4002000c]`, sector 512 B.
- a55a frame: payload-ptr `0x1d204`, payload-len `0x1d218`, ack `0x1d392`, nak `0x1d378`, reply-bytes `0x1d386`.
- State vars: target `0x200003ec`, shown `0x200003f4`. Redraw fires when target changes (page dispatcher `0x1ec5c`).

### Page builder (state 0x59) — pseudocode pinned to addresses
```
scr = lv_obj_create(NULL)              ; 0x15548
if (*(u32*)0x2E000 == 'VASS'/'SSAV'):  ; magic present in flash slot
    dsc.header = 0x3002a007            ; cf=7,168x384
    dsc.data_size = 8072
    dsc.data = 0x2E008                 ; flash slot payload
    img = lv_img_create(scr)           ; 0x12d58
    lv_img_set_src(img, &dsc)          ; 0x13860
    0x4d9c(1)                          ; force full refresh (no ghosting)
; else: leave blank scr (no image yet)
return scr                             ; dispatcher loads it as the active screen
```

### cmd-0x50 image-receive handler — pseudocode (writes RESERVED SLOT only; recovery path untouched)
```
len = 0x1d218(); p = 0x1d204()
sub = p[0]
if sub==0 (BEGIN):   0x4edc(clkdiv,1); for s in 0..15: 0x1afbc(0x2E000+s*0x200); 0x4fe4(2); 0x5034(0x2E000+s*0x200)
                     rx_expected = p[1..2] (u16 total bytes); rx_got=0; 0x1d392()  ; ack
elif sub==1 (DATA):  idx=p[1..2]; n=len-3; 0x4fe4(1); 0x50c0(0x2E008+idx, &p[3], n); rx_got+=n; ack/nak on 0x50c0 ret
elif sub==2 (COMMIT): verify crc32(slot payload)==p[1..4]; if ok write magic 'SSAV'+len at 0x2E000 (0x4fe4(1)+0x50c0); ack; else nak
elif sub==3 (SHOW):  *(u32*)0x200003ec = 0x59 ; ack   ; (immediate display)
```
The BEGIN erase + DATA program + COMMIT magic sequence mirrors the stock OTA idiom at `0x1c5e0`/`0x1c63c` exactly, but targets `0x2E000` (free flash), NEVER the app region or the boot-flag sector `0xE00`. `0x40/0x46/0x47/0x48` are not touched → the proven `screen direct execute` recovery reflash still works on a bad boot.

### Idle + restore hooks
- idle_hook (replaces `bl 0xbeec` @ 0x1d67c): call 0xbeec; if shown-state(0x200003f4)==0 (device page) → if ++wakes*period_secs ≥ THRESHOLD → save shown state, set target 0x200003ec=0x59. (period_secs = LPTIM period, settings byte6 5..60s; THRESHOLD in a new RAM/slot-header byte, default e.g. 300 s.)
- restore_hook (trampoline on 0xbe10): run original 0xbe10; if target/shown==0x59 → target=saved, wakes=0. (Any UART frame, touch, device switch, macro, or label/page write routes through 0xbe10 → clears the screensaver.)

Screen candidate SHA: computed by the builder after emit; report at build time.

## 3. MAIN firmware change (Design A) — additive, recovery-safe
- New W-opcode (e.g. `W 1A`) in main W-dispatch (`0x8187b4` tbh table). Handler reads the PC chunk and calls a55a sender `0x81a540` with cmd **0x50** and the chunk as payload → screen cmd-0x50 handler.
- Append handler in main's spare final 4-KiB sector (as CT01 did, +608 B); do NOT move existing bytes. Recompute Realtek digest = SHA-256 of `image[12:372]+image[404:752]+image[1008:]` at `image[372:404]` (per toolkit). `0x40/0x46/0x47/0x48` and the whole screen-OTA path unchanged → recovery safe. Builder = extend `spare_keys_firmware.py`/`custom_firmware.py` pattern.

## 4. Deliverables status
- Design (this doc) + base SHA: DONE.
- Candidate .bin + byte-allowlist manifest + unicorn validation (page builder AND image-receive-to-flash handler): **NOT emitted this session** — the sandbox auto-mode classifier repeatedly/non-deterministically blocked the `venv/bin/python`+unicorn execution required to hand-assemble, self-check (capstone), and emulate-validate the bytes, and a safety classifier stopped an earlier response. Emitting UNvalidated flashable bytes for a device with a partially-mapped bootloader would be the brick-risk case; correctness requires the emulation step. Needs a Bash permission rule for the venv interpreter (or a local run by the author/the toolkit).

## 5. PC-side WIRE PROTOCOL (for `codec_screensaver set <img.png>`, on top of screen_direct.py)
Convert: `screen_images.py prepare --width 168 --height 384 <png>` → 8072-B indexed-1bpp blob (palette white-then-black, MSB-first, byte-padded rows, 1=black).
Transport = GATT config char `0000**1121**-d102-11e1-9b23-00025b00a5a5` (write-no-resp), notify `…**1122**`. Prefix `W`=0x57. New opcode `0x1A`:
- BEGIN: `57 1A 00 <total_bytes u16 LE>`  → notify `1a 01` (ok). Main erases slot, screen cmd 0x50 sub0.
- DATA:  `57 1A 01 <idx u16 LE> <data…>`  chunk `idx` = byte offset into the 8072 payload. → notify `1a 01` per chunk (`1a 02`=error, stop).
- COMMIT:`57 1A 02 <crc32 u32 LE>` over the 8072 payload → `1a 01` if flash verifies, else `1a 02`.
- SHOW:  `57 1A 03` → screen sets target state 0x59 (display now). (Optional; else it shows on next idle.)
- THRESHOLD: `57 1A 04 <secs u16 LE>` sets idle threshold; wake period (5..60 s) stays on the existing settings blob (screen cmd 0x15).
Chunk size: negotiated ATT MTU 247 → payload 244; W1A overhead 5 → ~192 data bytes/chunk → 8072/192 ≈ 43 chunks, per-chunk ack. (screen_direct.py already does MTU acquire + per-packet ack — reuse that loop with the W1A framing.)
Prereqs (unchanged): phone BT off; drop the live HID link (select AMPLIFIER); hold ACTIVITIES+DEVICES for the config channel; run via `ofa/artifacts/venv`.
