# Touchscreen-hold regression fix — Codex handoff

**Date:** 2026-09-28 · **Author of brief:** Claude (Opus 4.8) · **Firmware author:** Codex · **Auditor:** Claude (read-only) · **Flasher/verifier:** the author

> Companion to `TOUCHSCREEN-HOLD-HANDOFF.md` (the original two-MCU tap-vs-hold work). That flash shipped `a5fd98e` and introduced the regressions below. This brief specifies the fix; Codex authors the firmware-modification source, Claude audits read-only, the author flashes.

---

## TL;DR — two regressions, both from the touchscreen-hold flash, fixed in one screen + one main patch

1. **Favorites broken (screen).** Selecting a **device** or **activity** favorite black-flashes and fails to open. A **key** favorite is also mis-slotted (only works if it targets the currently-selected device). Root: `ss_key_impl` in `touchscreen_hold_screen.c`.
2. **"More keys" grid pages dead (screen).** Keys on the device/activity shortcut grids (screen states `SHOWN ∈ {0xb, 0x15}`) delegate to stock, which emits a `byte8=0` frame → the patched main zeroes the keycode → release-only = dead. Same function.
3. **Touchscreen IR keys double-fire (main).** A touchscreen key that reaches the IR-transmit path fires IR on **both** the down (`byte8=1`) and up (`byte8=0`) frame = two IR blasts per tap. In *activity* mode an HID key's up-edge is also remapped into the IR path. Root: the `0x81b002 bl 0x81d082` IR branch is not gated on `byte8`.

Confirmed by static RE + unicorn emulation (Fable, full report in the Appendix; re-verified by Claude with `emu_main.py`). Fixes #1+#2 are **screen-side, recoverable**; fix #3 is **main-side, no-rollback**.

**Flash order is fixed: SCREEN FIRST, then MAIN.** The main IR-gate drops `byte8=0` frames from IR; the current grid pages send *only* a `byte8=0` frame, so the gate is only safe after the screen fix makes those pages emit a `byte8=1` down-edge.

---

## Images & builders (paths, SHAs, VA offsets — all verified 2026-09-28)

| Role | File | SHA256 | Size | VA |
|---|---|---|---|---|
| **Screen — image to modify** (current HOLD) | `ofa/artifacts/touchscreen-hold-screen/screen-touchscreen-hold-all-keys-DO-NOT-FLASH.bin` | `f067082f…` | 180608 | file+0x1000 |
| Screen base | v15-NOREARM `64ec0c29…` | — | 180608 | — |
| Screen stock ref | `artifacts/apk/x/assets/flutter_assets/assets/bin/ofa_touch_rt150t_0002t_v013f.bin` | — | 180608 | — |
| **Main — image to modify** (current PATCH) | `ofa/artifacts/touchscreen-hold-main/main-touchscreen-hold-inplace-DO-NOT-FLASH.bin` | `407c86b7…` | 105500 | file+0x80de00 |
| Main base | `ofa/artifacts/codecos-v16/main-codecos-v16-DO-NOT-FLASH.bin` `957c9549…` | — | 105500 | — |

**Builders (already in repo):**
- Screen: `ofa/touchscreen_hold_screen.{py,S,c,ld}` — the `.c` holds the injected wrapper logic (`ss_key_impl`, `ss_key_num_impl`, `ss_key_table_impl`, `ss_key_grp_impl`); `.py` assembles + links + injects and emits the 180608 B image.
- Main: `ofa/touchscreen_hold_main.py` — `build_inplace(base)`: asserts the v16 bytes at `SITE=0x81d17c`, writes the 12-byte HID-gate, recomputes the Realtek digest, asserts nothing else changed, keeps 105500 B.

---

## ⚠⚠ CORRECTION (2026-09-29) — candidate `d220b0e2` CRASHED the screen on hardware; fix the COPY_KEY miscall

The first screen candidate (`d220b0e2`) implemented the branch logic correctly but **crashes the screen MCU on every favorite tap** (backlight dies, e-ink blanks, returns to previous screen — confirmed on hardware + by real-code emulation). Root cause, proven by disasm + unicorn trace:

- **`COPY_KEY` (0x11e4) is `memmove(dst, src, size)` — a THREE-argument function** (size in r2). The header declares it `void (*)(u8*, const void*)` and calls `COPY_KEY(record, source)` with **two** args, so nothing sets the size. In this build's codegen the size register `r2` still holds the function pointer (`0x11e5`), so the call memmoves **4581 bytes** from SCREEN_STATE over the 20-byte `u8 record[0x14]` stack buffer → smashes the saved return address → firmware fault. Trace: `COPY_KEY dst=0x2000efd8 src=0x200001d6 SIZE=r2=0x11e5`.
- **This is why my first audit passed and was wrong:** it *stubbed* COPY_KEY with a clean 0x14 copy. Audits must run the REAL function (Fable's `emu_screen.py`, which uses real stock + real COPY_KEY, reproduces the failure — device/activity favorites never reach stock, no op-0x41/0x42).

**FIX (pick one, prefer the first):**
1. **Don't copy — read the fields directly** off the `volatile u8 *source` pointer, like stock does: `type = source[0]; dev = source[1]; key = source[2];`. No stack buffer, no memmove, no calling-convention dependency. Cleanest.
2. If you keep the copy: declare `COPY_KEY` as `void (*)(void*, const void*, unsigned)` and call `COPY_KEY(record, source, 0x14)`.

**Re-audit bar (mandatory):** the corrected candidate must pass **Fable's real-code `emu_screen.py`** (no stubs on COPY_KEY or stock 0x87c4) showing: device favorite (type 0) CLICKED → `op41`; activity (type 1) CLICKED → `op42`; key favorites (2–4) → SEND_43 with slot=record[1]; type 5 → slot 0; grid 0xb/0x15 edges; and **no oversized COPY_KEY / no stack smash** (add an assert that any COPY_KEY call uses size 0x14, or that the path is copy-free). Byte-diff still confined; image 180608 B.

---

## Bug 1 — SCREEN: `ss_key_impl` (favorites + more-keys)

**File:** `ofa/touchscreen_hold_screen.c`, function `ss_key_impl` (currently ~lines 35–57). Three defects in the current source:

- **D1 — more-keys dead.** `if (phase == 0x15 || phase == 0x0b || phase != 0 || index > 7) return 0;` — the `0x15`/`0x0b` terms are dead (both are `!= 0`), so grid pages return 0 → delegate to stock → stock's grid path sends `byte8=0` → main zeroes it → dead.
- **D2 — favorites device/activity fail.** For `phase == 0`, `if (edge == LV_CLICKED) { SCREEN_STATE[6]=0; return 1; }` consumes the click for **every** slot **before the record is read**, so device/activity favorites (`record[0] ∈ {0,1}`) never reach stock `0x87c4`'s op `0x41` (device select, `0x885c`) / `0x42` (activity open, `0x8886`).
- **D3 — favorites key-tile mis-slot.** `send_edge` transmits `SEND_43(SCREEN_STATE[5], key, 1, …)` for all favorites; a key-type favorite (`record[0] ∈ {2,3,4}`) must use `record[1]` as the slot, and `record[0]==5` must use slot 0.

### Required behavior (spec — Codex implements in the `.c`)

Keep the `input_event(edge)` event-code gate **first** (the wake-loop lesson — never touch state on the non-input firehose). Then branch on `phase = KEY_STATE` (`[0x200003f4]`):

- **`phase ∈ {0x0b, 0x15}` (device/activity "more keys" grid):**
  - count-gate: `index >= SCREEN_STATE[0x259]` → `return 0`;
  - `LV_CLICKED` → `return 1` (consume);
  - `PRESSED`/`RELEASED`/`PRESS_LOST` → `send_edge(edge, SCREEN_STATE[0x25a + index])` (grid key by index; `send_edge` already uses slot `SCREEN_STATE[5]`, matching stock `0x882c–0x8846`, but as press/release edges).
- **`phase == 0` (favorites):** copy the record **first** (`COPY_KEY(record, SCREEN_STATE + index*0x14 + 0x1ae)`, `index > 7` guard as today), then branch on `record[0]`:
  - **`0` or `1` (device/activity) or `> 5`** → `return 0` on **every** event (tail-call stock: stock does op `0x41`/`0x42` on CLICKED at `0x885c`/`0x8886`; the other phases are no-ops in stock).
  - **`2`, `3`, `4` (key favorite w/ explicit device)** → latch `SCREEN_STATE[5] = record[1]`; on `LV_CLICKED` set `SCREEN_STATE[6]=0` and `return 1`; on the three edges emit an edge with `key=record[2]`, slot = `record[1]` (the latched `[5]`).
  - **`5`** → same as 2–4 but slot = 0.
- **any other `phase`** → `return 0` (delegate to stock, as today).

⚠ **Types 2–5 MUST consume `LV_CLICKED` (return 1), not delegate.** Stock's CLICKED for those types sends `byte8=1`, which under the patched main is a keycode **DOWN with no matching UP** = a stuck key. Only types 0/1/>5 delegate (their stock CLICKED does the op-0x41/0x42 dispatch and sends no HID).

**Constraints.** Image **must stay 180608 B**. `ss_key_impl` is ~118 B inside a 308 B run at `0x2a198`; if the branch overflows, `BL` into free space at `0x2a391` / `0x2a4dc` (Fable). Do not disturb `ss_key_num_impl` / `ss_key_table_impl` / `ss_key_grp_impl` (those handlers already work — see the per-surface table in the Appendix). No other pool/callback repoints change.

**Helpful constants already in the `.c`:** `EVENT_CODE 0x12211`, `EVENT_TARGET 0x1225b`, `TARGET_INDEX 0x172fb`, `COPY_KEY 0x11e5`, `SEND_43 0x1bf51`, `KEY_STATE *0x200003f4`, `SCREEN_STATE *0x20000000`. Records: base `SCREEN_STATE + index*0x14 + 0x1ae`; `record[0]`=type, `record[1]`=slot/device, `record[2]`=key.

---

## Bug 2 — MAIN: gate the touchscreen IR transmit on `byte8`

**File:** `ofa/touchscreen_hold_main.py` (extend `build_inplace`, or add a second in-place site).

**Path (current `407c86b7`).** 0x43 handler `0x81ad60` reads `byte8` into **r7** at `0x81af42` (r7 is callee-saved; the `0x81d136` push is `{r2,r3,r4,r5,r6,lr}`, so r7 survives as `byte8`). HID keys go through `0x81aff8 bl 0x81d136` → variant-4 tail `0x81d17c` which **already** gates the keycode on r7 (the existing 12-byte patch). Non-HID / IR keys go `0x81afe2 cbz r4 → 0x81affe → 0x81b002 bl 0x81d082` (IR transmit) — **this branch is NOT gated on byte8**, so both the down (`byte8=1`) and up (`byte8=0`) frame transmit → double IR. In *activity* mode an HID key's up-frame is remapped (`0x81cdca`) into this same `0x81d082` path.

**Required.** Skip the IR transmit (`0x81d082`) when `byte8 (r7) == 0`, so IR fires only on the **down**-edge. (There is already a `cbz r7` at `0x81b00a`, but it is **after** the `bl` — the gate must move **before** `0x81b002`.) This also removes the spurious activity-mode up-edge IR.

**Constraints.**
- ⚠ **Do NOT** make the main synthesize keycode+zero for a lone `byte8=0` frame — that would turn every real UP edge back into a tap and undo the hold feature. Leave the existing HID gate at `0x81d17c` exactly as-is.
- ⚠ The **physical-button IR path is separate** and must remain untouched. The gate applies only to the 0x43-frame IR branch at `0x81b002`.
- Keep the image **105500 B** and recompute `realtek_digest`. Prefer a true in-place patch (like the existing site); if the byte-budget at `0x81b002` won't hold a pre-`bl` conditional, use a small trampoline into free space (the screen builder's injection pattern). Extend the builder's byte-diff assertion to cover **only** the new code bytes + the digest.

**flash.py allowlist (required, or the main flash won't run).** `flash.py` refuses any main image it can't reproduce from source (`is_touchscreen_hold_candidate`: stock → codecOS-v16 → `touchscreen_hold_main.build_inplace`). The new patch changes `build_inplace`'s output, so the recognizer (and any baked SHA) **must be updated** to accept the new byte-for-byte reproduced image.

---

## Build

- **Screen:** edit `touchscreen_hold_screen.c`, run `touchscreen_hold_screen.py` (assemble/link/inject) → new 180608 B image under `artifacts/touchscreen-hold-screen/`. Add/extend the unicorn asserts in the builder (or `emu_screen.py`).
- **Main:** edit `touchscreen_hold_main.py`, run it → new 105500 B image under `artifacts/touchscreen-hold-main/`. Update the `flash.py` recognizer.

---

## Verification bar (Claude audits read-only, BEFORE any flash)

Reusable RE/emulation harnesses (Fable, session scratchpad — will be copied to a durable path): `fw.py`/`fw2.py` (pool-annotated disasm), `emu_screen.py`/`emu_screen2.py` (screen, HOLD vs v15 with real stock `0x87c4`), `emu_main.py` (main, v16 vs PATCH `0x43` handler with `byte8=1/0`).

- **Screen:** byte-diff new vs `f067082f` (only the `ss_key_impl` region + any new pool words); emulate `ss_key_impl` across `phase==0 × record[0]∈{0,1,2,3,4,5,6}` and `phase∈{0xb,0x15}`. Assert: device/activity (0,1) tail-call stock → op `0x41`/`0x42`; key favorites (2,3,4) send edges with slot=`record[1]` + consume CLICKED; type 5 → slot 0; `>5` delegate; grid pages send edges (`byte8=1` down, `0` up); event-code gate stays first (no wake-loop); **size == 180608 B**.
- **Main:** byte-diff new vs `407c86b7` (only the IR-gate bytes + digest); extend `emu_main.py`. Assert: IR key (`0x22`/`0x36`) `byte8=1` → **1** IR fire, `byte8=0` → **0** IR fires; HID keys unchanged (keycode on down, release on up); activity BLUE `byte8=0` → no IR; physical IR path untouched; **size == 105500 B**.

Only after both pass do we flash.

---

## Flash procedure & order

1. **SCREEN first (recoverable).** Flash via the **OFA app** (the screen/e-paper MCU path — the app works for the screen). Verify live: device favorite opens, activity favorite opens, key favorite opens, and the "more keys" grid keys act. Recovery: reflash `screensaver-v15-NOREARM` (`64ec0c29`) via the OFA app.
2. **MAIN second (no-rollback).** Flash via **`flash.py`** — **NOT** the OFA app (its Custom OTA fails `DFU_VALIDATE` for a custom main and rolls back). Isolate the remote first: unpair the tablet's Bluetooth, `bluetoothctl power off` on the desktop, hold **ACTIVITIES+DEVICES** to enter pairing mode, start `flash.py` while held. **"Unlikely Error on activation is EXPECTED"** (the remote drops BLE to reboot into the new bank; `flash.py` only activates after a successful validate, so a rejected image is a safe no-op). Recovery: reflash codecOS-v16 (`957c9549`) or stock 0.5.7 (both in `flash.py`'s allowlist). **Batteries:** strong/alkaline — heavy writes; the NiMH gauge lies and brownout causes the repeated-input bug. Verify: no double-IR (if a touch IR key is available), HID keys still hold correctly, and the **CODEC CAST activity touch keys release cleanly** (the activity-remap wrinkle).

---

## Division of labor & safety

- **Codex** authors the firmware-modification source: the `ss_key_impl` rewrite in `touchscreen_hold_screen.c`, the main IR-gate in `touchscreen_hold_main.py`, and the `flash.py` recognizer update; rebuilds both images.
- **Claude** audits **read-only** — byte-diff, capstone disasm-vs-stock, unicorn emulation. Does **not** generate firmware-modification code.
- **the author** flashes on hardware and eyeball-verifies; authorizes each step.
- ⚠ **Never let the OFA app update *firmware*** (clobbers the custom main + screen). Screen flash via the OFA app is fine; the **MAIN must go through `flash.py`**. The OFA app's IR (learn/send/device order) and e-paper labels are safe alongside the custom firmware.

---

## Appendix — Fable's read-only RE report (full evidence + address map)

> Verbatim. Every address re-verified against `f067082f` (screen) and `407c86b7` (main) in that session; Claude independently re-ran `emu_main.py` and confirmed the double-IR (both `byte8=1` and `byte8=0` reach `0x81d082` for IR keys `0x22`/`0x36`).

### Images verified by sha256 on disk
- SCREEN HOLD: `ofa/artifacts/touchscreen-hold-screen/screen-touchscreen-hold-all-keys-DO-NOT-FLASH.bin` = `f067082f…` (180608 B). Base v15-NOREARM `64ec0c29…`; stock v013f `artifacts/apk/x/assets/flutter_assets/assets/bin/ofa_touch_rt150t_0002t_v013f.bin`. VA = file+0x1000.
- MAIN PATCH: `ofa/artifacts/touchscreen-hold-main/main-touchscreen-hold-inplace-DO-NOT-FLASH.bin` = `407c86b7…` (105500 B). Base codecOS-v16 `957c9549…`. VA = file+0x80de00.
- Byte-diff HOLD vs v15 = exactly the 13 declared sites (pools 0x1c34/0x307c/0x9e3c/0x2480/0xa088; filters 0x38a8/0x9e22/0x1d94/0x9f5c/0xa01a → `movs r2,#0`; code 0x29e54(264B)/0x2a02c(78B)/0x2a198(118B)). Byte-diff PATCH vs v16 = only 0x81d17c..0x81d182 + digest at file 0x374.
- Scratch tooling: `fw.py`/`fw2.py` (loader + pool-annotated disasm), `rea1..11.py`, `main1..8.py`, `emu_screen.py`, `emu_screen2.py` (unicorn, HOLD vs v15 with REAL stock 0x87c4 code), `emu_main.py` (unicorn, v16 vs PATCH 0x43 handler with byte8=1/0).

### 0. Screen-state model (needed to read everything below)
- `[0x200003ec]` = REQUESTED screen id; `[0x200003f4]` = SHOWN screen id. Transition manager `0xe65c`: `0xe668 cmp r1,r0; beq` → `0xe690-0xe694 ldr r0,[r5]; str r0,[r4]; bl 0x1ec5c` (copies ec→f4, rebuilds). `0x1ec5c` jump table `0x1ec94` (0x60 ids) → builders:
  - **0x0** → `0x1ee5a bl 0x3028` = FAVORITES (0x3028: `0x3064 movs r2,#8; 0x3066 ldr r3,=0x87c5 (pool 0x307c); 0x306c bl 0x3764` — 8 slots).
  - **0xb / 0x15** → `0x1ee26 bl 0x1b98` = SHORTCUT GRID (0x1b98: `0x1c02 ldr r0,=0x259; ldrb r2,[r7,r0]` count; `0x1c06 ldr r1,=0x20000a8c` icon records; `0x1c08 ldr r3,=0x87c5 (pool 0x1c34)`; `0x1c0c bl 0x3764`). 0x1b98 titles by `[ec]==0x15` (activity) else device name → **0xb = device grid, 0x15 = activity grid**.
  - **0xc / 0x16** → `0x1ee32 bl 0x2078` = KEYPAD: scans `[0x25a..]` (0x2102-0x2176): digits 0xd-0x16 set flags → `0x219a bl 0xa098` (numeric pad, cb `0xa681` pool 0x9e3c, filter 0x9e22); keys 0x1b..0x30 via table 0x213c set 6 flags → `0x21a8 bl 0x9eb8` (7-key group, cb `0x5b89` pool 0xa088, filters 0x9f5c/0xa01a).
  - **0xd / 0x17** → `0x1ee3e bl 0x23d4` = KEY LIST (`0x2422 ldrb r2,[0x259]`; `0x242a ldr r0,=0xc235 (pool 0x2480)`; `0x243a bl 0x1c38`; visibility filter `0xd9a5`: keys ≥0xf0 use the 16-B label at `0x26e+idx*0x10`).
  - 0xa/0xe/0xf → 0x1fb0 (DEVICES list, cb 0xa505 via 0x1c38); 0x14/0x18/0x19 → 0x1ae0 (ACTIVITIES list, cb 0x852d via 0x1c38); 0x1e/0x22 → 0x2ac0 (cb 0x1df85 via 0x1c38); 0x1f → 0x1a94 (cb 0x8475 via 0x1c38). None of these four callbacks reach the 0x43 sender.
- **Which id a received page gets** is decided by `0xa5a4` (called after every page rx: 0x1c970/0x1cfa4/0x1d02c) from the page's FIRST key `[0x25a]` and the activity flag `[0x20000006]`: first key ∈ icon table `0x201e4` = {0x22,0x23,0x24,0x36,0x32,0x34,0x39,0x37} (streaming-app shortcuts) → **0xb/0x15 grid** (0xa5e8/0xa5dc); first key ∈ digit table `0x20244` or colour table `0x202bc` → **0xc/0x16 keypad**; anything else (incl. custom ≥0xf0 keys) → **0xd/0x17 list** (0xa620/0xa61c); then `0xa656-0xa65e [ec]=id; [f4]=0`.
- `[0x20000005]` (slot used by every sender) is written by main's page replies: rx `0x1c8c4`(r6=1: `0x1c8fa [6]=byte0; 0x1c8fe [5]=byte1`), `0x1c8ce`(r6=0: `0x1c910 [6]=0; 0x1c914 [5]=byte0`), `0x1cf40`, plus `0x1c752`, `0xe22a`(=0), the op-0x41 sender `0x1be9a`, `0x1bf68` (SEND_43 itself), and stock favorites `0x8826`/`0x8860`.
- Complete 0x43-sender (`0x1bf50`) caller set, verified by BL scan of the whole image (identical stock/HOLD): `0x5b9e`(0x5b88 group), `0x8846`(0x87c4 grid path), `0x88b8`(0x87c4 favorites path), `0xa6a2`(0xa680 numeric), `0xc25a`(0xc234 list), `0x1f444`(0x1f428 release helper, key=0). No pooled `0x1bf51` in stock; HOLD adds one at `0x29ec4` (send_edge). Stock byte8 (r3) values: **0x87c4 favorites types 2-5 = 1** (`0x88b4 movs r2,#1; 0x88b6 mov r3,r2`); **everything else = 0** (`0x8844`, `0xc258`, `0xa69e`, `0x5b9a`, `0x1f442`).

### A. VERDICT on the "more keys" pages SHOWN ∈ {0xb, 0x15} — CONFIRMED, with a narrower blast radius than predicted

Screen path (f067082f), cited:
1. Grid tiles register `0x87c5` via `0x3764` (pool `0x1c34` → now `0x29e55`); `0x3764` registers dispatcher `0x8f1b` with filter `0x38a8` = `movs r2,#0` (LV_EVENT_ALL); `0x8f1b` does `0x8f3e ldr r1,[r6,#4]; 0x8f42 blx r1` → `ss_key 0x29e54`.
2. `ss_key`: `0x29e58 bl 0x2a198` (`ss_key_impl`). Impl: `0x2a19e/0x2a1a0` EVENT_CODE; `0x2a1a4 cmp r0,#8; bhi`; `0x2a1b0-0x2a1b6` bit-test against 0x18a (PRESSED/PRESS_LOST/CLICKED/RELEASED); `0x2a1b8-0x2a1c0` EVENT_TARGET → TARGET_INDEX; **`0x2a1c2 ldr r1,=0x200003f4; 0x2a1c4 ldr r1,[r1]; 0x2a1c6 cmp r1,#0; 0x2a1c8 bne 0x2a1fa` → returns 0 for ANY SHOWN ≠ 0** (there is no 0xb/0x15 branch at all — the C source's `phase==0x15||phase==0xb||phase!=0` collapses to `!=0`).
3. `ss_key` on 0: `0x29e62-0x29e6e` restores r0 = original event, pops, `ldr r3,=0x87c5 (pool 0x29e94); bx r3` → stock `0x87c4` with the ORIGINAL event.
4. Stock `0x87c4`: `0x87ce cmp r0,#7; 0x87d0 bne 0x884a` → PRESSED(1)/PRESS_LOST(3)/RELEASED(8) do nothing. CLICKED: `0x87dc ldr r1,=0x200003f4; 0x87e0 cmp r1,#0x15; beq 0x882c; 0x87e4 cmp r1,#0xb; beq 0x882c`. At `0x882c`: `idx < [0x259]` (0x8834 bhs → return), `0x883a-0x883c key=[0x20000000+0x25a+idx]`, `0x883e strb [9]=key`, `0x8840 ldrb r0,[r2,#5]` slot, **`0x8842 movs r2,#1; 0x8844 movs r3,#0; 0x8846 bl 0x1bf50` → SEND_43(slot=[5], key, 1, byte8=0)**.
5. Unicorn (`emu_screen.py`) with real stock code, SHOWN=0xb and 0x15: HOLD and v15 are call-for-call identical — PRESSED/PRESS_LOST/RELEASED → no send; CLICKED → exactly one `SEND_43(slot,key,1,byte8=0)`; stack/LR balanced. So on these pages the screen emits ONE frame per tap, at finger-lift, with byte8=0.

Main path (407c86b7), cited:
- 0x43 handler entry `0x81ad60` (r5=0x20b448 @0x81ad64, r10=0x20a2a0 @0x81ad86, fp=0x20b0b0 @0x81ad70). `0x81af40 ldrb r6,[r4,#6]` slot; **`0x81af42 ldrb r7,[r4,#8]` byte8**; `0x81af44 ldrb.w r9,[r4,#7]` key. r7 is callee-saved and `0x81d136` pushes `{r2,r3,r4,r5,r6,lr}` (not r7): r7 == byte8 at `0x81d17c` (confirmed by emulation: `->0x81d136 … r7=0/1`).
- Key→HID mapping (v16 forwarder `0x8276cc`, hooked from `0x81afcc`): digits 0xd-0x16 → `0x823f36` table (idx 0x14-0x1d); on profile-4 slots: 0x2d→0x0d, 0x30→0x12, 0x2e→0x13, 0x2f→0x1e, 0x1b→0x1f, 0x1f→0x20 (pairs @`0x827720`), 0xf0+n → `0x82772c`[n] (0xf0→idx 2 = usage 0x70/F21 — matches the RESUME memory). Any other key → r4=0 → `0x81afe2 cbz r4 → 0x81affe` = IR/other path `0x81d082`, never `0x81d136`.
- Mapped key + `[0x20b448]` bit15 (connected) + slot == connected slot (`0x81afea-0x81aff4`) → `0x81aff8 bl 0x81d136` (the ONLY caller: BL/B.W scan of the whole image + no pooled `0x81d137`). Variant `[0x20b0b0+0x15]==4` → `0x81d172`: `0x81d174-0x81d17a orr #2` busy; **`0x81d17c cmp r7,#0; 0x81d17e ite ne; 0x81d180 movne r0,r5; 0x81d182 moveq r0,#0; 0x81d184 bl 0x81cb0c`**; `0x81d188-0x81d18e bic #2`; `0x81d190 pop`. Variants 0/1 → `0x81d992`/`0x81d0fa` (pending/queue, no send).
- `0x81cb0c` builds a single-usage report: `0x81cb1a ldrh r5,[0x82421e + idx*2]`, `0x81cbd4 strb r5,[sp,#0xa]`, `0x81cbe2 bl 0x4f7a4` (id 0xa, 8 bytes). Other `0x81cb0c` callers (`0x81d552`, `0x81d9d8`, `0x81e086`, `0x81e1a4`) are not on the 0x43 frame path.
- Unicorn (`emu_main.py`), PATCH, BLE slot connected, byte8=0: `0x81cb0c(idx=0)` → HID report `00 00 00 00 00 00 00 00` ONLY (v16 gave keycode then zero). byte8=1: keycode report only. Unmapped keys (0x22, 0x36, 0x00) → `0x81d082` regardless of byte8.

**Verdict:** For SHOWN 0xb/0x15, a tap produces exactly one byte8=0 frame; on a BLE-profile-4 connected slot, any HID-mapped key (digits, 0x2d/0x30/0x2e/0x2f/0x1b/0x1f, 0xf0-0xff) therefore yields ONLY a zero report = release of nothing = **dead (MORE-KEYS-RELEASE-DEAD)**. No other main path delivers a keycode for that frame. **Nuance (important):** (i) both the device grid (0xb) and the activity grid (0x15) behave identically — the gate is `SHOWN≠0`, not page-specific; (ii) a page is routed to 0xb/0x15 only when its FIRST key is a streaming-shortcut id (0x22/0x23/0x24/0x36/0x32/0x34/0x39/0x37 — tables at `0x201e4`), and those shortcut ids are NOT HID-mapped, so on a typical shortcut page every key goes down the untouched IR/`0x81d082` path and behaves exactly as stock (single tap; no edges, not dead). The dead case materialises only for a page that starts with a shortcut id and also carries digits/colour/spare keys. On the codec BLE device, pages that start with a digit, a colour key (MODE/SUBS/AUDIO/SPARE/RETURN/INFO = 0x2d/0x2e/0x30/0x2f/0x1b/0x1f) or a spare (≥0xf0) land on 0xc/0x16 or 0xd/0x17 and are served by the working handlers — so CAST(0x2f)/RESUME(0xf0) are most likely NOT on a 0xb/0x15 page (the v1 assumption that they went through 0x87c4 is not supported by the classifier). I did not read the live page inventory; confirm with `check_touch_pages.py`/`W 07 <slot>` which page starts with which key.

### B. Per-surface classification (HOLD screen f067082f + PATCH main 407c86b7)

| Surface (SHOWN, builder, stock cb → HOLD handler) | Key kind / slot type | Class | Evidence |
|---|---|---|---|
| **Favorites 0x0**, 0x3028→0x3764, 0x87c4→`ss_key`/`ss_key_impl` — record[0]∈{0,1} (device/activity tile) | any | **FAVORITES-BROKEN** | impl: `0x2a1ca cmp r0,#7` idx gate, `0x2a1d2 cmp r4,#7; 0x2a1d6-0x2a1da [6]=0; return 1` consumes CLICKED before the record is read; stock `0x885c` (op 0x41 via `0x1be84`) / `0x8886` (op 0x42 via `0x1be24`) never run. Emulation: v15 → `op41(slot=rec[1],0,1,1)`/`op42(act)`; HOLD → nothing. Extra: HOLD emits `SEND_43(slot=[5], key=0, byte8)` on PRESSED/RELEASED (key 0 → main `0x81d08a cbz → return 0`, harmless). |
| Favorites 0x0 — record[0]∈{2,3,4} (key with explicit device `record[1]`) | any | **OTHER (mis-slotted edge key)** | HOLD sends `SEND_43(slot=[0x20000005], key=record[2], 1, edge)` (`0x29eb4-0x29ebc`); stock sends `slot=record[1]` and latches `[5]=record[1]` (`0x8824-0x8826`, `0x88b4-0x88b8`). Correct only when the favorite targets the currently selected device. |
| Favorites 0x0 — record[0]==5 | any | **OTHER (mis-slotted)** | stock `0x88ae-0x88b2: r0=0` → slot 0 = main-local handler `0x816e2a` (`0x81af6c cbz r6`); HOLD sends slot=[5] → goes to a device instead. |
| **Grid 0xb (device) / 0x15 (activity)**, 0x1b98→0x3764, 0x87c4 delegated | HID-mapped key on BLE slot | **MORE-KEYS-RELEASE-DEAD** | Section A. |
| Grid 0xb/0x15 | non-mapped key (shortcut ids 0x22…) or any key on an IR slot | WORKS-as-stock (single tap at lift, no edges) | one byte8=0 frame → `0x81affe → 0x81d082` (unpatched). |
| **Keypad 0xc/0x16 numeric**, 0x2078→0xa098, 0xa680→`ss_key_num` (0x29e70→0x29ec8) | digits 0xd-0x16 on BLE slot | **WORKS (edge)** | `0x29eec-0x29ef0` EVENT_NUMERIC_TARGET, `0x29ef2 cmp #9`, key=idx+0xd (`0x29efa`), `0x29f00 bl send_edge`; main byte8=1→keycode, 0→zero. Numeric page requires a digit-first or colour-first page (0xa5a4). |
| Keypad 0xc/0x16 numeric | digits on an IR slot | **OTHER — probable double IR fire** (see C2) | two frames/tap, both → `0x81d082`. |
| **Keypad 0xc/0x16 group**, 0x2078→0x9eb8, 0x5b88→`ss_key_grp` (0x2a02c→0x2a034) | 0x2d/0x30/0x2e/0x2f/0x1b/0x1f (table `0x9b68`) on BLE slot | **WORKS (edge)** | `0x2a05e ldr r1,=0x9b59; blx`, `0x2a066 bl send_edge`; all six are HID-mapped (usages 0x69/0x6a/0x6b/0x6c/0x6d/0x6e = F14-F19; BLUE 0x2f → idx 0x1e → 0x6c). This is where CAST=Blue lives if it is on the R/G/Y/B/Return/Info page. |
| Keypad group | same keys on IR slot | OTHER — probable double IR fire | as above |
| **List 0xd/0x17**, 0x23d4→0x1c38, 0xc234→`ss_key_table` (0x29e8c→0x29f0c) | HID-mapped incl. custom ≥0xf0 spares on BLE slot | **WORKS (edge)** | `0x29f36 ldr r1,=0x172fb` TARGET_INDEX, `0x29f3c ldr r1,=0x2000025a; 0x29f3e ldrb r1,[r0,r1]`, `0x29f42 bl send_edge` — same lookup as stock `0xc24c-0xc252`, no page/count gate (stock has none either). RESUME=0xf0 lives here (list is chosen for a spare-first page). |
| List 0xd/0x17 | unmapped key / IR slot | OTHER — probable double IR fire | |
| 0x1c38-registered non-key callbacks: 0x8474 (SHOWN 0x1f), 0x852c (activities list 0x14/0x18/0x19), 0xa504 (devices list 0xa/0xe/0xf), 0x1df84 (0x1e/0x22) | n/a | OTHER — benign fan-out | filter `0x1d94` is inside the shared wrapper 0x1c38 (callers 0x1acc/0x1b40/0x2034/0x243a/0x2b2a), so these four now receive LV_EVENT_ALL through `0x8f1b`; each gates `bl 0x12210; cmp r0,#7; bne` before any side effect (`0x847c`, `0x8536`, `0xa50e`, `0x1df8c`). Behaviour unchanged, only extra dispatch. |
| Release helper `0x1f428` (SEND_43(slot,key=0,1,0), ~10 callers) | n/a | unchanged | key 0 is dropped by main at `0x81d08a`. |

### C. Surprises beyond the prediction

1. **Favorites key-type tiles (record[0]∈{2,3,4,5}) are mis-slotted, not just device/activity tiles consumed.** f067082f dropped the v2 `[5]=record[1]` latch and always sends `slot=[0x20000005]` (send_edge `0x29eb4 ldr r0,=0x20000005`). Stock semantics of byte8=1 on the main side is precisely "explicit slot, skip activity routing": `0x81af76 cbz r7 → 0x81af96: if [0x20b451]==1 → r6 = 0x81cdca([0x20a2a0]>>4, key)` (activity's device = `[0x20b4c3+act*4]&0xf`, default 1). `[0x20b451]` is set to 1 by the op-0x42 activity-open handler `0x81cec2` (`0x81cec6`) and to 0 by the op-0x41 device-select `0x81ce18` (`0x81ce36`). Under HOLD every DOWN is byte8=1 (no remap, slot=[5]) and every UP is byte8=0 (remapped when an activity is active). I checked this is benign for the page surfaces: main's op-0x42 reply puts `[0x20b4c4+act*4-1]&0xf` in payload byte0 (`0x81af08-0x81af24`), the screen stores it in `[5]` (`0x1c914`), i.e. `[5]` == the `0x81cdca` remap value, so DOWN and UP hit the same slot. It is NOT benign for favorites, whose slot is per-record.
2. **Probable double IR fire on IR-device pages (keypad/list/grid-fixed).** HOLD sends DOWN+UP frames for every key; main's non-HID path `0x81affe → 0x81d082(slot,key)` runs for BOTH (`emu_main.py`: key 0x22/0x36 byte8=1 and 0 both reach 0x81d082); the only byte8 test on that path is AFTER the send (`0x81b00a cbz r7 → 0x81b064 [0x20b44a]=0` vs `0x81b010 bl 0x814a6c`). `0x81d082` → `0x81d0f6 b.w 0x816012` → `0x815eda` (IR codeset lookup `0x817d26` → build/send `0x81ea3c`), which reads as a one-shot transmit with no press/release state. So a tap on the TV/amp keypad or key list very likely emits the IR code twice. Not proven on hardware — verify live (e.g. one VOL+ tap on the Bravia page = 2 steps?). Note the physical-button IR path is separate and unaffected.
3. **v1's premise that CAST/RESUME go through 0x87c4 is unsupported.** Per `0xa5a4`, colour-first or spare-first pages become keypad/list pages (group `0x5b88` / list `0xc234`), not 0x87c4 grids. On the codec slot those handlers work, which is consistent with the author seeing the favorites regression rather than dead CAST/RESUME.
4. `[0x20000006]` is not merely a "released-index latch": `0xa5a4` uses `[6]≠0` to pick the activity variant of every page (0x15/0x16/0x17), and the rx handlers write it from main (`0x1c8fa`). HOLD clears it on favorites CLICKED exactly like stock (`0x87f0`), so no change — just don't repurpose it.

### D. Minimal fixes (recommendations only — nothing written)

Screen-side (recoverable; fixes both broken classes; no main change needed for them). In `ss_key_impl` after the existing event-code gate, branch on SHOWN:
- **SHOWN ∈ {0xb, 0x15}:** `if idx >= [0x20000259] → return 0` (stock returns); `key = [0x2000025a+idx]`; CLICKED → return 1 (consume); PRESSED/RELEASED/PRESS_LOST → `SEND_43([5], key, 1, PRESSED?1:0)`. (Stock's `[9]=key` at `0x883e` is redundant — `0x1bf50` writes `[9]` itself at `0x1bf66`.)
- **SHOWN == 0:** copy the record (`0x11e4`, `0x20000000+0x1ae+idx*0x14`) BEFORE deciding: `record[0] ∈ {0,1}` → **return 0 on every event** (tail-call stock: CLICKED then does op 0x41/0x42 byte-exactly at `0x885c`/`0x8886`, other events are ignored by `0x87ce`); `record[0] ∈ {2,3,4}` → `[5]=record[1]` and send edges with `slot=record[1]`; `record[0]==5` → edges with `slot=0`; `record[0]>5` → return 0. Keep CLICKED → `[6]=0` + consume for 2-5 (stock's CLICKED for those would send byte8=1 = a stuck DOWN under the patched main).
- Any other SHOWN → return 0 (as now). Budget: impl is 118 B in a 308-B run (0x2a198); the additions fit or can BL into 0x2a391/0x2a4dc. Keep 180608 B, keep the event-code gate first (wake-loop lesson).

Main-side: **no change needed** for FAVORITES-BROKEN or MORE-KEYS-RELEASE-DEAD; do NOT make main synthesize keycode+zero for a lone byte8=0 frame (that would turn every real UP edge back into a tap). For surprise C2 (only if confirmed live), the fix is main-side and must come AFTER the screen fix: gate the IR branch on byte8 before `0x81b002 bl 0x81d082` (today's `cbz r7` at `0x81b00a` is after the send); doing it before the screen fix would drop the delegated grid pages' single byte8=0 frame.

### E. Addresses the firmware author can trust (all re-verified this session)
Screen: `0x87c4` gate `0x87ce/0x87d0`; SHOWN compares `0x87e0/0x87e4/0x87e8`; type jump table `0x881c` (`1f 34 02 02 02 48`) → `0x885c`(0)/`0x8886`(1)/`0x8822`(2-4)/`0x88ae`(5); grid path `0x882c-0x8846`; pools `0x88c0`=0x259, `0x88c4`=0x25a, `0x898c`=0x200003f4, `0x8994`=0x20000000. HOLD: `ss_key 0x29e54` (tail-call `0x29e62-0x29e6e`, pool `0x29e94`=0x87c5), `ss_key_num 0x29e70/0x29ec8`, `ss_key_table 0x29e8c/0x29f0c`, `send_edge 0x29e9c` (pools `0x29ec0`=0x20000005, `0x29ec4`=0x1bf51), `ss_key_grp 0x2a02c/0x2a034`, `ss_key_impl 0x2a198` (pools `0x2a200`=0x12211, `0x2a204`=0x172fb, `0x2a208`=0x200003f4, `0x2a20c`=0x11e5). Page classifier `0xa5a4` (tables `0x201e4`/`0x20244`/`0x202bc`; commit `0xa656-0xa65e`). Builder dispatcher `0x1ec5c`/table `0x1ec94`. Main: handler `0x81ad60`, byte8 `0x81af42`, activity remap `0x81af96-0x81afaa`/`0x81cdca`, forwarder `0x8276cc` (tables `0x827720`, `0x82772c`, `0x823f36`, usages `0x82421e`), sole `0x81d136` caller `0x81aff8`, patched tail `0x81d17c-0x81d184`, IR/other path `0x81affe → 0x81d082 → 0x816012 → 0x815eda`, flag setters `0x81cec6`(=1)/`0x81ce36`(=0).
