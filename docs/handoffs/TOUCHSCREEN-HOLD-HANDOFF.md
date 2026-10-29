# TOUCHSCREEN-HOLD — controlled handoff for the firmware author (Codex)

*Supersedes TOUCHSCREEN-HOLD-BRIEF.md. Written 2026-09-26 after the scope question was
settled by measurement + a full two-firmware disassembly. This is the DESIGN + analysis;
the flashable patch code (the `ss_key` and main-side sources, and the exact byte edits) is
yours to author — everything you need to do so is below. Nothing here has been flashed.*

---

## 0. What is decided (don't re-litigate)

- **Feature:** real tap-vs-hold on the two touchscreen keys **CAST (Blue)** and **RESUME (spare-1)**. Tap = hint toast; hold = act (cast / resume). All other keys unchanged.
- **SCOPE = two-MCU (proven, not assumed).** The screen sends a **single tap**, not a down/up pair:
  - Runtime `rk` log: touchscreen `scan=199` fires DOWN+UP at the **same millisecond** (0/0/75 ms) even when held; physical `scan=114` grows 44/211/239 ms.
  - Firmware: the screen's touch-key callback emits its frame **only on LV_EVENT_CLICKED** (a completed tap, at finger-lift). So a main-only patch has nothing to defer. The screen must be taught to emit real down/up; the main must map them.
- **Codec side is DONE + committed** (`ec51d56`, `2e6f712`): CAST(195)/RESUME(199) arm a 400 ms hold timer on press; `resumeTarget()`/`remoteResumeLast()` are live-validated. **No codec change is needed** once the firmware delivers real down→up timing. ⚠ A TEMP `Logger::log("rk", …)` line at the top of `handleRemoteKey` must be **removed after verification**.
- **Bases confirmed by a live read over the config link** (main `SS01`, screen `v15`, slots 0-3):
  - **MAIN base:** codecOS v16.0 (= blue-LED-off), sha256 `957c9549…`, file `artifacts/codecos-v16/main-codecos-v16-DO-NOT-FLASH.bin`. Reference = stock 0.5.7 (`ofa_touch_0.5.7.bin`, VA = file + 0x80DE00). The 0x81d136 window is byte-identical v16↔stock.
  - **SCREEN base:** screensaver-v15 NOREARM, sha256 `64ec0c29…`, 180608 B, file `artifacts/screensaver-v15/screen-screensaver-v15-NOREARM-DO-NOT-FLASH.bin`. Reference = stock v013f (VA = file + 0x1000). The touch→0x43 path is byte-identical v15↔stock.
- **Keys:** CAST = Blue = logical **0x2F**; RESUME = spare-1 = logical **0xF0** (alias 0xF0 → usage 0x70 / KEY_F21 → evdev 191 → codec scan 199). Gate both patches on those exact logical ids.

## 1. Toolchain / framework (reuse the existing builders)

- Assembler: `screensaver_firmware.assemble_stable(cpu, ld, entry, srcs, defines)` — clang `--target=arm-none-eabi -mthumb -fuse-ld=lld -nostdlib -Oz`, then llvm-nm/objcopy. Returns `(code, {sym: va})`, asserts reproducible.
- **Screen = `cortex-m0plus` (ARMv6-M): BL only, NO B.W.** Use `thumb_bl` + `assert_bl`; run `armv6m_listing_ok` on the listing.
- **Main = `cortex-m4`** (B.W allowed via `thumb_bw_m4`).
- Digest: `realtek_digest(data)` = SHA over `image[12:372]+[404:752]+[1008:]` at `SHA_OFFSET = MP_SIZE+372` — **main only**. **Screen has NO in-image digest**: integrity is the OTA per-block CRC at flash time; the only in-image rule is **size == exactly 180608 B** (the bootloader rejects larger — proven on hardware).
- Self-check every build: `capstone_ok`, `check_sites`-style (each patched site disassembles to the intended instruction; every non-patched byte identical to base), size/digest gates.
- Emulate: extend `check_touch_digits.py` (unicorn) — it already drives the exact main path.
- Linker-script pattern: `screensaver_screen.ld` / `screensaver_main.ld` — define stock call targets as absolute symbols, place `.text` at the chosen free VA.

## 2. SCREEN change (RT150T, screensaver-v15 base — RECOVERABLE, do this MCU first)

**Root cause:** the touch-key callback at VA **0x87c4** reads the LVGL event code (`bl 0x12210`) and gates `cmp r0,#7; bne …` — it acts **only on LV_EVENT_CLICKED (7)**. The LVGL indev already dispatches real edges with true finger timing — **PRESSED=1 (0xeea2), RELEASED=8 (0xeda8), PRESS_LOST=3 (0xf45a)** — they're just never routed to a `0x43` send. The callback is **registered CLICKED-only** (the registration builder `0x3764` sets the `lv_obj_add_event_cb` filter to 7 at VA **0x38a8**).

**Confirmed integration points (findings):**
- The callback pointer `0x000087c5` is pooled at **VA 0x1c34 and 0x307c** (loaded at 0x1c34-adjacent and 0x3066/0x307c). Both must point at your new handler instead.
- The registration filter constant at **0x38a8** is `7` (CLICKED); it must become **0 (LV_EVENT_ALL)** so your handler receives press/release. Safe: every *other* key's stock callback still ignores non-CLICKED events, so their behaviour is unchanged.
- Free placement for your handler: **VA 0x29e54** (341 B of verified zero padding, outside v15's screensaver regions `0x250b1-0x263a9` + its hook sites — do not touch those). Confirm all-zero before placing; keep size 180608.

**Your handler (`ss_key`) — logic to author (ARMv6-M):** entered from the repointed callback with `r0 = lv_event_t*`.
1. event code: `bl 0x12210` → `r0`.
2. Resolve this widget's **logical key** exactly as stock 0x87c4 does: `0x1225a` (`r0 = [event]` = target) → `0x172fa` (`r0 = [target+0x10]` = index) → phase gate `[0x200003f4]` → `0x11e4` copies the 0x14-byte key record to a local; **record byte1 = slot, byte2 = logical key**. Slot in the stock senders = `[0x20000000 + 5]` (the digit/shortcut callbacks 0xa680→0xa694 and 0xc234→0xc254 read `ldrb r0,[r5,#5]`, r5=0x20000000, as slot before the sender).
3. If logical key ∈ {**0x2F**, **0xF0**}:
   - event **PRESSED(1)** → sender `0x1bf50(r0=slot, r1=key, r2=1, r3=1)`  — **down** (byte8=1).
   - event **RELEASED(8)** or **PRESS_LOST(3)** → `0x1bf50(slot, key, 1, 0)` — **up** (byte8=0).
   - event **CLICKED(7)** or anything else → return (down/up already cover the gesture).
4. Otherwise → **tail-call stock 0x87c4** with the original event intact (`r0` = the original `lv_event_t*`, no callee-saved register left dirty, plain `bx` to `0x87c4|1`; stock re-reads the event via 0x12210/0x1225a). Byte-identical behaviour for every other key/event.
- **Sender ABI:** `0x1bf50(r0=slot, r1=key, r2, r3=byte8)` builds the a55a `0x43` frame (`movs r0,#0x43; bl 0x1be08`; packs slot→`[0x20000000+9]`, key→`[+5]`, byte8→`[+8]`).

## 3. MAIN change (RTL8762C, codecOS v16.0 base — NO ROLLBACK, brick risk, do LAST)

**Root cause:** the connected (variant-4) branch of the report path at VA **0x81d136** emits the keycode and then, **unconditionally**, a zero through the shared report builder `0x81cb0c` — the zero is at VA **0x81d182 / file 0xf382**. Variant 4 is the live connected state (written at 0x811904). `byte8` (r7 in the `0x43` handler at 0x81af40) is not currently forwarded into 0x81d136.

**To author (cortex-m4, appended in v16's free space, clear of its ~0x350-byte spare-keys/forwarder/LED-off tail):**
- **Defer the zero:** emit the keycode on the **byte8=1** frame and the zero on the **byte8=0** frame, instead of both inline at 0x81d182. (Trampoline out of the variant-4 tail into appended code; keep the shared builder 0x81cb0c untouched.)
- **Plumb byte8:** get `r7` from the 0x43 handler (0x81af40) to the 0x81d136 path — a scratch-global stash in the handler prologue, or a branch-patch at the `0x81aff8 → 0x81d136` call site.
- **MANDATORY auto-release safety (the repeated-input guardrail — non-negotiable):**
  - a one-shot **~3-5 s** timer (start `0x26c3e` / cancel `0x26c64`) whose callback force-emits `0x81cb0c(0)` and clears the busy bit if no up arrives;
  - a **flush on the link-state transitions** (reconnect `0x8118dc`, teardown `0x811ace`) that forces a zero + cancels the timer, so a mid-hold disconnect can never leave a key down.
- **Gate on the same logical ids (0x2F, 0xF0)** so only CAST/RESUME hold; every other key keeps the discrete keycode+zero tap, unchanged.
- **Recompute** the Realtek digest (§1) and update the size field (`MP_SIZE+8`).

## 4. Build + validation (offline, before any flash)

`assemble_stable` → place code in the free region → apply the screen repoints+filter / the main trampoline+digest → **self-checks** (every patched site disassembles to intent; all other bytes identical to base; screen size == 180608; main digest valid; ARMv6-M-legal on the screen) → **emulate**: extend `check_touch_digits.py` to drive variant-4 with `byte8=1` then `byte8=0` as **separate** runs and assert the report sequence is `[keycode]` (no zero) then `[zero]`, plus a run where the **timeout callback fires `[zero]`**. Fence all writes as the prior builders do; the assemble→capstone-self-check→unicorn-emulate loop must pass before emitting a flashable blob.

## 5. Flash safety + recovery (unchanged rules)

- **SCREEN first (recoverable):** OFA app → Advanced Mode → Custom OTA → *E-paper – Select File*. Do the **+8 KB-of-0xFF size-probe** reflash first (proves the bootloader still accepts+boots). Recovery = reflash **screensaver-v15-NOREARM** (restores today's behaviour) or **stock v013f**. **NEVER touch** `0x1c5e0`/`0x1c294`, cmd-0x40 special-case `0x1c2ce-0x1c2f2`, or the 0x46/0x47/0x48 handlers. The image picture-slots (0x2e000+) survive reflash.
- **MAIN last (NO ROLLBACK — can brick):** only after the screen is flashed **and the hold is verified end-to-end** and the emulated sequences pass. **Alkalines only** (NiMH brownout independently fakes repeated inputs). "Unlikely Error on activation is EXPECTED." Recovery = re-flash **codecOS v16.0** (`957c9549`) or **main-screensaver-v1** (`3b2e3240`). Preserve the 4 device bonds + all touchscreen pages.

## 6. Acceptance test (after each flash)

1. `debugLog` on. **Hold spare-1** → `rk` log shows `scan=199 DOWN` then `UP` **>400 ms later** → "NEXT ▸ …" toast + playback. **Tap** → DOWN/UP <400 ms → hint toast. **Hold Blue** → cast (upstairs, with a TV).
2. **Stress (main):** 50+ presses + a mid-hold **slide-off** (LVGL PRESS_LOST → up) + a mid-hold **range walk-out** + a mid-hold **disconnect** → each yields **exactly one down + one up**, no repeats/stuck keys. Confirm with the phone HCI snoop (full level): one physical hold = one down report + one up report.

## 7. Biggest risk

A **lost release edge** → a stuck key. Note this is **not** the 2026-09-22 "repeated-input" territory (that was a *repeat storm* from an active-HID branch re-emitting); the two-MCU design never re-emits (one keycode on DOWN, one zero on UP), so its worst case is a single key held too long. Mitigations, all structural (see §11 revised design): **ss_key treats PRESS_LOST as an up** (screen side); **single-usage report** so a new DOWN replaces any stuck key (main side); **HOGP host auto-release + existing teardown/reconnect reinit** on disconnect. The slide-off / walk-out / disconnect + 50-press soak tests prove them.

## 8. Reference artifacts (all present on disk)

- Bases: `artifacts/codecos-v16/main-codecos-v16-DO-NOT-FLASH.bin`, `artifacts/screensaver-v15/screen-screensaver-v15-NOREARM-DO-NOT-FLASH.bin`. Stock refs: `artifacts/ble24/stock-0.5.7.bin` (main), `…/flutter_assets/assets/bin/ofa_touch_rt150t_0002t_v013f.bin` (screen).
- Builders to model on: `screensaver_firmware.py` (assemble_stable, realtek_digest, thumb_bl, build_screen/build_main pattern), `blue_led_off_firmware.py`, `spare_keys_firmware.py`. Screen linker/sources: `screensaver_screen.ld` / `.S` / `.c`. Emulation model: `check_touch_digits.py`. venv: `ofa/artifacts/venv/bin/python` (capstone + unicorn). ⚠ Don't name a helper `dis.py` (shadows stdlib, breaks capstone).

## 9. ALL-KEYS EXTENSION (2026-09-26) — supersedes the CAST/RESUME-only gate

The v1 screen candidate (`screen-touchscreen-hold-DO-NOT-FLASH.bin`, sha `f0612bc7`) was validated but is **CAST/RESUME-only** — `ss_key` gates on logical `0x2F`/`0xF0` and repoints only the `0x87c4` callback. **New requirement: the hold must work on ALL touchscreen keys** (uniform with the physical keys). This is a re-spin of BOTH patches. Author + build on this side of the tooling; the audit is done independently (structural diff + byte-level wrapper check + unicorn edge tests, as for v1).

**Coverage (confirmed by disassembly of the v15-NOREARM base):** there are THREE touch-key event callbacks, ALL registered CLICKED-only (`add_event_cb` filter = 7):

| callback | pool word(s) | builder | filter site |
|---|---|---|---|
| `0x87c4` | `0x1c34`, `0x307c` | `0x3764` | `movs r2,#7` @ `0x38a8` (v1 flipped this) |
| `0xa681` | `0x9e3c` | `0x14e60` | `movs r2,#7` @ `0x9e22` (the `ldr r1` that loads the cb ptr is at `0x9e20`) |
| `0xc234` (thumb `0xc235`) | `0x2480` (pool word) | wrapper `0x1c38` | **LIVE — registered via `0x1c38`** (see 2026-09-26 resolution) |

⚠ **RESOLVED (2026-09-26, audit disasm) — the registration model is a shared wrapper + dispatcher, not a direct `add_event_cb`:**
- **`0x87c4` group:** widget setup calls **wrapper `0x3764`** with `r3` = the callback pointer. `0x3764` internally does `add_event_cb(obj, dispatcher 0x8f1b, filter @0x38a8 = movs r2,#7, user_data=descriptor)`, and **`0x8f1b` is a generic dispatcher** (`ldr r1,[user_data+4]; blx r1` @`0x8f42`) that calls `descriptor->field4`. The `0x87c5` pools (`0x1c34` @loader `0x1c08`, `0x307c` @loader `0x3066`) are the `r3`/field4 callback. **So the candidate's repoint (`0x1c34`/`0x307c`→`ss_key`) + filter flip (`0x38a8`→0) IS correct: `0x8f1b` then dispatches all edges to `ss_key`.** ✅
- **`0xa681` numeric:** the clean direct case — `0x9e20` `ldr r1,=[0x9e3c]=0xa681`, filter `movs r2,#7` @`0x9e22`, `bl 0x14e60` @`0x9e28`. Repoint `0x9e3c` + flip `0x9e22`. ✅
- **`0xc234` is LIVE (not dead):** its pointer `0xc235` (pool `0x2480`) is loaded at `0x242a` (`ldr r0,=[0x2480]`) and passed as a **stacked argument** to a *third* wrapper **`0x1c38`** (`str r0,[sp,#4]` @`0x242c`; `bl 0x1c38` @`0x243a`). That is why it was elusive — it is not a pc-relative `ldr r1` into `add_event_cb`. `0x2440` remains **live code** (`ldrb r3,[r7,r0]`), NOT a filter site — do not patch it. **To cover `0xc234`:** trace `0x1c38` (does it use the same `0x8f1b` dispatcher + a filter, or register `0xc234` directly with its own filter?), then repoint pool `0x2480` (`0xc235`→the shared `ss_key`, if resolver-compatible) and flip that path's filter. Determine which key group `0x1c38`/`0xc234` serves before claiming all-keys coverage.
- ⚠ **Shared-filter widening risk:** flipping `0x38a8` changes the filter for the `0x8f1b` dispatcher used by **every** `0x3764`-registered widget, not just the two `0x87c5` ones. **RESOLVED:** `0x3764` has exactly two callers (`0x1c0c`, `0x306c`), both passing `r3 = 0x87c5` → both are the `ss_key` widgets. No other callback rides that filter. Safe. ✅

**DEFINITIVE KEY-HANDLER ENUMERATION (2026-09-26) — the completeness criterion for "all keys":** every touch key emits its frame through the `0x43` sender **`0x1bf50`**. Enumerating *all* callers of `0x1bf50` gives the complete set of key handlers (do this instead of guessing which callbacks exist):

| `0x1bf50` caller | role | coverage |
|---|---|---|
| `0x87c4` | colour/custom keys (via `0x3764`→`0x8f1b`→field4) | ✅ `ss_key` |
| `0xa680` | numeric keypad (`0x1225e`+0x0d) | ✅ `ss_key_num` |
| `0xc234` | table keys (via `0x1c38`→`0x8f1b`→field4; key=`state[0x25a+idx]`) | ✅ `ss_key_table` |
| **`0x5b88`** | **7-key group (`0x1225e`+resolver `0x9b58`, indices 0-6)** | ❌ **UNCOVERED** |
| `0x1f428` | release helper — sends `SEND_43(slot, key=0, 1, 0)`; ~10 callers | n/a — not a press handler, leave alone |

**`0x5b88` — the 4th and last uncovered key group (to cover in the next spin):** stock `0x5b88` has **no internal CLICKED gate** (relies on its filter) and **no `[5]`/`[6]` bookkeeping** — it just `key = 0x9b58(EVENT_NUMERIC_TARGET(event))`, `slot = state[5]`, `SEND_43` on CLICKED. It is registered **directly** (not via `0x8f1b`): both sites `0x9f62` and `0xa020` load the **same** cb pool **`0xa088`** (= `0x5b89`) and each has its own `movs r2,#7` filter at **`0x9f5c`** and **`0xa01a`**. To cover: (1) repoint pool `0xa088` → a new handler (`ss_key_grp`); (2) flip **both** filters `0x9f5c` + `0xa01a` to `movs r2,#0`; (3) author `ss_key_grp` = read `EVENT_CODE`; on PRESSED/RELEASED/PRESS_LOST send `SEND_43(state[5], 0x9b58(EVENT_NUMERIC_TARGET(event)), 1, PRESSED?1:0)`, consume everything else (no stock fallback — stock has none). `0x9b58` maps index 0-6 through a 7-byte table (`>6 → key 0`); mirror it exactly. Extend the Unicorn edges for this group. After this, **all four** live key handlers are edge-driven and "all keys" is truly complete.

**To do for all-keys (design + author):**
1. **Determine which key group each callback serves** (numeric keypad vs custom-label colour keys vs spares; which logical-key ranges/pages), and confirm which callback CAST (`0x2F`) and RESUME (`0xF0`) route through — so v1 is even reaching both.
2. **Cover every LIVE touch-key callback**, not just `0x87c4`: repoint each live callback's pool word(s) and flip each registration filter to `LV_EVENT_ALL (0)`. Confirmed filter sites: `0x38a8` (`0x87c4`), `0x9e22` (`0xa681`). The `0xc234` registration + filter is still to be located (see the correction above) — cover it only once found and confirmed live.
3. **Drop the `0x2F`/`0xF0` gate** in the handler so it emits down (byte8=1 on PRESSED) / up (byte8=0 on RELEASED+PRESS_LOST) for **every** resolved key, and consumes CLICKED for all (no residual tap). Keep the tail-call fix (restore caller-LR, balance stack, preserve r4-r11; ARMv6-M has no `pop {..,lr}` so pop the saved LR into a scratch reg + `mov lr, rN`).
4. **Preserve each stock callback's on-click bookkeeping.** Each stock callback does state work beyond the `0x43` send (e.g. `strb r1,[state+6]` @ `0x87f0`, released-index table writes, redraw/highlight). Determine what is externally visible and replicate/route so consuming CLICKED doesn't regress the display/state. If a single generic handler serves all three, it needs the correct per-callback fallback for events it doesn't handle (LONG_PRESSED=5 / REPEAT=6 / etc.) — either tail-call the matching stock callback or verify no fallback is needed.
5. Confirm the resolver (`0x1225a`→`0x172fa`→record byte2 = logical key; slot = `[0x20000000+5]`) is identical across all three callbacks so one `ss_key` can serve all.
6. Keep the screen image EXACTLY **180608 B** (append handler(s) into verified-zero free space outside v15's regions; more than one free run exists near `0x29e54`/`0x2a02c`/`0x2a6cd`/`0x2ad62`). No in-image digest to fix.

**MAIN side, all-keys:** the main patch must **defer the release for ALL keys**, not gate on `0x2F`/`0xF0`. Everything else in §3 (defer the zero at `0x81d182`, plumb byte8, the mandatory auto-release timeout + disconnect-flush, Realtek digest) is unchanged — the guardrail now covers every key. ⚠ This widens the repeated-input surface to all keys; the auto-release + disconnect-flush are what keep it safe, so the slide-off / walk-out / disconnect stress tests are more important than ever.

**Behavioural note for the author's review:** all keys shift from tap-on-release to fire-on-press (like the physical keys); normal taps still register (quick down→up). Codec already handles keys via `handleRemoteKey` with `isAutoRepeat` available, so per-key hold/repeat behaviour stays codec-side.

## 10. ALL-KEYS SCREEN CANDIDATE — audit result + close-out (2026-09-26)

Candidate history: `2d4462a9` → `151880a8` → `86395557` → `faadd4ca` (**FLASHED → wake-loop, recovered**) → **`f067082f3808f34ea96d7a3bf503568cd07ab991286ffdd33b1c9f26e505fd57` — event-first fix, FIREHOSE-TESTED, current**.

**⛔ `faadd4ca` and all earlier: DO NOT FLASH** (`faadd4ca` was flashed 2026-09-26 and caused a wake-loop; recovered by reflashing `screensaver-v15-NOREARM`; main MCU never touched — this is why screen went first).

**✅ FIXED in `f067082f` (event-first gate + split layout):** reproduces byte-for-byte; exactly 180608 B; 3 code regions all in verified base-zero (primary `0x29e54` 264/341, `ss_key_grp` `0x2a02c` 78, split `ss_key_impl` `0x2a198` 118, reached via BL); event-code gate is FIRST in every handler (verified: `ss_key` disasm shows `EVENT_CODE`→`cmp #8;bhi`→`tst (1<<edge),0x18a;beq` **before** any resolver/`SCREEN_STATE` access); `[5]` write removed; `[6]=0` only on CLICKED. **Firehose test PASS** — driving all four handlers with non-input codes (2,4,5,6,0,0x0e,0x16,0x1d) yields **zero** `SCREEN_STATE` writes and **zero** sends (the exact test `faadd4ca` would have failed — it wrote `[5]`/`[6]` on every one). Input events correct: PRESSED→down, RELEASED/PRESS_LOST→up, CLICKED→consume (ss_key: single `[6]=0`, no send). Residual: `LV_EVENT_ALL` still *runs* the (now side-effect-free) handlers per event; if hardware still misbehaves, escalate to per-event registration (kills the firehose). Recoverable, so safe to retry.

**ROOT CAUSE — confirmed by emulation (the test I should have run first):** the patch flips the touch-key filters to `LV_EVENT_ALL`, so the handlers run on *every* LVGL event, not just input edges. `ss_key_impl` does its state writes (`SCREEN_STATE[6]=0`, and `[5]=record[1]` for `record[0]∈{2,3,4}`) **after only the phase/index gate — before checking the event code**. Driving the handler with non-input codes (PRESSING=2, draw/refresh codes) shows it writes `[6]=0` and `[5]=0x77` on **all** of them. At idle, whatever LVGL fires continuously → the handler stomps `[5]`/`[6]` repeatedly → the wake-loop. (The prior emulation passed only because it drove `{1,3,7,8}` exclusively — never the firehose `LV_EVENT_ALL` admits.)

**THE FIX (all four handlers): event-code gate FIRST.**
```
edge = EVENT_CODE(event)
if edge != PRESSED && edge != PRESS_LOST && edge != CLICKED && edge != RELEASED:
    return 0                      // tail-call stock — NO target resolution, NO state write
// only the 4 real input events reach here (fire only on an actual touch):
... (phase/index gate for ss_key) ...
if edge == CLICKED: [ss_key only: SCREEN_STATE[6]=0]; return 1   // consume, NO send
else: send_edge(edge, key); return 1                            // down on PRESSED, up on RELEASED/PRESS_LOST
```
- **Drop the `[5]=record[1]` write entirely** — `[5]` is the connection-maintained slot (the numeric handler reads it read-only and works), so it's already correct at send time; writing it was both unnecessary and a churn source. Keep only `[6]=0`, and only on CLICKED (once per click = stock frequency = safe).
- After the gate, the idle firehose is byte-behaviour-identical to stock (stock's callbacks also do nothing on non-CLICKED), so it cannot loop.

**MANDATORY new test (this is what was missing):** the emulation must drive a range of **non-input** event codes (e.g. `2`, `4`, `5`, `6`, `0x16`, `0x1d`) with a valid key context and assert **zero writes** to `SCREEN_STATE` (`0x20000000..0x20000020`) and **no** `SEND_43` — plus tail-call-to-stock. Then the input-event asserts (PRESSED→down, RELEASED/PRESS_LOST→up, CLICKED→`[6]=0` + consume, no send).

**Residual honesty:** this fixes the *confirmed* cause (state-write churn). `LV_EVENT_ALL` still makes the dispatcher + handlers *run* on every event (side-effect-free now), which was never before the case (stock filter was CLICKED-only). If hardware still misbehaves after this fix, escalate to registering the specific events (PRESSED/RELEASED/PRESS_LOST) individually instead of `LV_EVENT_ALL` — eliminates the firehose entirely. Screen is recoverable, so iterate.

**LAYOUT — the fix grows the primary handler past the 341-B `0x29e54` run (measured 384 B), so split it.** Gate **all four** handlers (robustness — don't leave numeric/table/grp running resolvers on the firehose). Free zero runs available (base v15-NOREARM, VA=file+0x1000; total 6166 B), all clear of the protected OTA/recovery region (`0x1c5e0`/`0x1c294`, far below): `0x29e54`(341) · `0x2a02c`(344; ss_key_grp uses 46, ~298 free after `0x2a05a`) · `0x2a198`(308) · `0x2a391`(311) · `0x2a4dc`(320) · `0x2a6cd`(331) · `0x2ab70`(327) · `0x2ad62`(330) · `0x2aec0`(320). ARMv6-M has no `B.W`, so cross-run transfers use **BL** (±16 MB range — every run above is trivially reachable; the builder already emits `thumb_bl`/`assert_bl` and the `.ld` already places multiple sections). Options: move one or two impls (e.g. `ss_key_num_impl`/`ss_key_table_impl`) into a second run called via BL, **or** factor the shared event-code gate into one helper all four handlers BL to (dedups the `EVENT_CODE` call + the 4-way compare, likely shrinks enough to fit). Either is fine — keep every appended byte inside a listed run, image stays exactly 180608 B, allowlist covers each run.

*(Historical audit notes for earlier revisions retained below.)*

**Independent audit — PASS on brick-safety + byte-cleanliness + code correctness:**
- Size exactly **180608 B**; build **reproduces byte-for-byte** from `touchscreen_hold_screen.{py,S,c,ld}` (source == on-disk candidate).
- Byte-diff = **exactly 6 declared sites**, no strays: pools `0x1c34`/`0x307c`→`ss_key`, `0x9e3c`→`ss_key_num`; filters `0x38a8`/`0x9e22` (`07`→`00` = `LV_EVENT_ALL`); 248-B handler in verified zero-pad @ `0x29e54` (budget 341 B). `0xc234` region untouched — ✓.
- **v1 tail-call bug fixed in BOTH wrappers** (`mov r3,r5 / pop {r4,r5} / mov lr,r5 / mov r5,r3 / bx stock+1`); `run(7,phase=1)` emulation confirms stock returns to the wrapper's caller with SP intact + r4/r5 preserved.
- `send_edge` marshals `SEND_43(slot=[0x20000005], key, 1, PRESSED?1:0)` — down on PRESSED, up on RELEASED/PRESS_LOST, CLICKED consumed. All 7 edge asserts pass.
- Stock RE re-verified by disasm: latch-clear is real (`strb #0,[0x20000006]` @ `0x87f0` on CLICKED after the phase gate — replicated at `0x29ebe`); both filter sites are genuine `movs r2,#7` feeding `add_event_cb` (`bl 0x14e60`).

**✅ CLOSED in v2 (`151880a8`):** `ss_key_impl` now mirrors stock's normal-key bookkeeping — for `record[0]∈{2,3,4}` it writes `SCREEN_STATE[5] = record[1]` before edge delivery (disasm @ `0x29ed6`: `subs r0,#2; cmp r0,#2; bhi` = the exact unsigned range gate; `strb record[1],[0x20000005]` @ `0x29ee0`), after the unconditional `[6]=0` clear and before `send_edge` (which reads the just-latched `[5]`). Byte-parity with stock's `record[0]∈{2,3,4}` jump-table case. Handler 264/341 B; build reproduces to `151880a8`; new Unicorn assert starts from a mismatched slot and verifies arbitrary keys latch the stock slot while numeric keys stay read-only. The special-case types (`record[0]∈{0,1,5}`: host/slot-select etc.) are still delegated only implicitly — if any *touchscreen* key turns out to be one of those, its side effects would be dropped; the on-hardware edge-timing test settles it (every key → correct keycode to correct host).

*Original finding (for the record):* Stock `0x87c4`'s CLICKED jump table (indexed by `record[0]`, table @ `0x881c` = `1f 34 02 02 02 48`) writes **`SCREEN_STATE[5] = record[1]`** for normal keys (`record[0]∈{2,3,4}`, case @ `0x8822`) *before* it sends, then sends with `slot=[5]`. The earlier all-keys handler consumed CLICKED and **only read `[5]`** — dropping that per-keypress slot write.

*Very likely benign* (evidence): stock **numeric** `0xa680` also sends with `slot=[5]` and **never writes `[5]`** (`ldrb r4,[base+5]`, read-only). So `[5]` is an authoritative slot maintained elsewhere (connection/host-select), and `0x87c4`'s `[5]=record[1]` must be idempotent (`record[1]==` current slot for normal keys) — else any arbitrary keypress would corrupt the slot numeric keys depend on. Dropping it therefore leaves `[5]` correct. Residual risk is narrow: only a touchscreen key with `record[0]=0` (host/slot-select, case @ `0x885c`, multi-write) or `record[0]∈{1,5}` (cases @ `0x8886`/`0x88ae`, unexamined) would lose real side effects — but host-switching here is the physical ACTIVITIES/DEVICES buttons, so touchscreen keys are almost certainly all normal sends.

**Close it one of two ways (author's pick):**
1. Dump each touchscreen key's per-key record (`0x20000000 + index*0x14 + 0x1ae`) live and confirm every reachable key is `record[0]∈{2,3,4}` with `record[1]==` the connected slot; **or**
2. Add a ~6-byte defensive `SCREEN_STATE[5] = record[1]` in `ss_key_impl`, gated on `record[0]∈{2,3,4}` → strict byte-parity with stock's normal-key side effect. Plenty of budget. **Recommended** (cheap insurance, makes the impl a strict superset of stock).

Either way, fully settled by the on-hardware edge-timing test (every key must send the correct keycode to the correct host; a hold shows two frame-events split by the hold). After that: main-side all-keys patch (§11), then the no-rollback main flash.

## 11. MAIN-side ALL-KEYS spec — next Codex task (RTL8762C, NO ROLLBACK / brick risk)

This is the second half of the two-MCU design and it is **not optional**: once the screen is flashed it emits *two* `0x43` frames per press (down `byte8=1`, up `byte8=0`); the unmodified main fw treats every `0x43` as a full tap (keycode+zero), so a screen-only flash would double-fire every key. This patch turns those edges into real hold timing for **every** key (drops the `0x2F`/`0xF0` gate from §3). Author + build on Codex's side; I audit read-only (reproduce → byte-diff → capstone-vs-stock → unicorn), same loop as the screen.

**⛔ MAIN CANDIDATE `e431b706` FAILED ACTIVATION ON HARDWARE (2026-09-26) — rebuild IN-PLACE (no grow).** It flashed to 100% + green flash, then the final commit step failed ("reconnect batteries" — a generic error) and rolled back to stock; **failed identically on two battery types, so it is NOT power** — the bootloader rejects the image at commit. Diagnosis: header recipe is complete (stock→codecOS-v16 changed *only* size `0x208` + digest `0x374`, exactly what we changed; no CRC/signature — v16 is itself an unsigned modified image that activates). The one thing that differs from the proven-flashable codecOS v16 (105500 B) is that `e431b706` **grew the image to 105520 B**. **Fix: do the conditional IN-PLACE in the 12 freed bytes — it fits exactly** (`cmp r7,#0; ite ne; movne r0,r5; moveq r0,#0; bl 0x81cb0c` = 2+2+2+2+4 = 12 B at `0x81d17c`, then falls through to the stock bic-bracket at `0x81d188`). No trampoline, no append, no `0x208` size-word change → image stays **exactly 105500 B**, byte-size-identical to codecOS v16; only the 12 bytes at `0x81d17c` + the recomputed digest change. This removes the grow (the suspected commit-time rejection). If the in-place build *still* fails to activate, the grow wasn't it and power/other returns to the table.

**✅ IN-PLACE MAIN CANDIDATE `407c86b74d0c4098165e83544fc4fc4b2c5237b283931c41a317e4499f704187` — AUDITED, staged for flash.** `main-touchscreen-hold-inplace-DO-NOT-FLASH.bin`, **exactly 105500 B (== codecOS v16, no grow)**. Only the in-place conditional at `0x81d17c` (`cmp r7,#0; ite ne; movne r0,r5; moveq r0,#0; bl 0x81cb0c`, falling through to the unchanged bic-bracket at `0x81d188`) + the recomputed digest differ from codecOS v16 — 6 code bytes actually change (the trailing `moveq r0,#0`=`00 20` and `bl 0x81cb0c` coincide with the old bytes) plus 32 digest bytes; size word `0x208` unchanged; the entire boot/OTA/recovery surface is byte-identical to v16. Digest valid (boots). Emulation: DOWN(byte8=1)→one `0x81cb0c(keycode)`, UP(byte8=0)→one `0x81cb0c(0)`, bit1 set during each send + cleared after. This eliminates the grow (the suspected commit-time rejection). If it *still* fails activation, the grow wasn't the cause → revisit power/other.

*(Superseded record: `e431b706`, 105520 B, base +20 = appended thunk + 16-B align — FAILED activation.)* Builder `touchscreen_hold_main.py` (self-verifies the base's original 12 bytes at `0x81d17c` before patching). **Audit PASS:** reproduces byte-for-byte; **Realtek digest valid** (recomputed == stored @`0x374`); size-word @`0x208` incremented by the appended amount; only three in-place changes (`0x81d17c` trampoline 12 B = `b.w 0x827a1c` + 4 dead nops · size word · digest) plus the pure-append thunk (nothing overwritten). Stock brackets `orr #2`@`0x81d174` / `bic #2`@`0x81d188` and the `pop`@`0x81d190` intact; `r4` (struct) survives `0x81cb0c` so the bic bracket is correct on return. Thunk @`0x827a1c` = `cmp r7,#0; ite ne; movne r0,r5; moveq r0,#0; bl 0x81cb0c; b.w 0x81d188`. **Unicorn:** DOWN(byte8=1)→ one `0x81cb0c(keycode)`, UP(byte8=0)→ one `0x81cb0c(0)`, bit1 set during each send + cleared after. **Both firmware halves are now built + audited; next step is the physical flash (screen first, main last).**

**Base (re-verified on the live image):** `artifacts/codecos-v16/main-codecos-v16-DO-NOT-FLASH.bin`, sha `957c9549df23f1a2e8952f328a1eac7bf9e242b2aabca88dc4cf66f584b70300`, **105500 B**. VA = file offset + `0x80de00`. Realtek digest = `sha256(image[12:372] + image[404:752] + image[1008:])` where `image = data[512:]`, stored at **file `0x374`–`0x394`** (the excluded `image[372:404]` gap). Image-size word at **file `0x208`** (currently `0x1961c`).

**Confirmed sites (my disasm of the v16 image — Codex re-verify, don't trust blindly):**
- **`0x43` handler @`0x81af40`** (file `0xd140`): `ldrb r6,[r4,#6]` = slot, `ldrb r7,[r4,#8]` = **byte8 (the down/up flag) → r7**, `ldrb.w r9,[r4,#7]` = logical key. `r7` is callee-saved.
- **Connected-slot call site @`0x81aff8`** (file `0xd1f8`): `bl 0x81d136`. This is the only caller I observed.
- **Variant-4 arm @`0x81d136`** (file `0xf336`): pushes `{r2,r3,r4,r5,r6,lr}` — **not r7**, so byte8 stays live in r7 through the whole function. `r5` = keycode (from `r0`). Variant selector `[r4+0x15]`: 0→`0x81d192`, 1→`0x81d19c`, **4 (live connected)**→ the arm at `0x81d172`:
  - `0x81d172-7a`: set **busy bit** (bit1) of `[state+4]` (`ldrh; orr #2; strh`);
  - `0x81d17c-7e`: `r0=keycode; bl 0x81cb0c` → **DOWN report**;
  - `0x81d182-84`: `movs r0,#0; bl 0x81cb0c` → **the unconditional zero (synthetic UP)** ← this is what must become conditional;
  - `0x81d188-8e`: clear busy bit; `0x81d190`: `pop {…,pc}`.
- Shared report builder `0x81cb0c` — leave untouched (physical keys share it).

**⭐ REVISED DESIGN (2026-09-26, evidence-based) — the whole main patch is ONE self-contained trampoline; NO external hooks, NO latch, NO flush, NO timer.**

*Decisive finding — the HID report is SINGLE-USAGE.* `0x81cb0c` looks up `r5 = usage_table[keycode]` (`ldrh r5,[table + keycode<<1]` @`0x81cb1a`) and sends exactly that one usage via `0x4f7a4` (`strb r5,[sp,#0xa]`, len 0xa on one path; `strb r5,[sp,#8]` 16-bit on the other). `keycode=0` → usage `0` = release. So **a new keycode replaces the prior on the host** (it is not an additive bitmap). Consequences that make the guardrails structural rather than code:
- A lost UP **self-heals on the next DOWN** — the new usage replaces the stuck one. (No next-event flush needed.)
- A BLE disconnect makes the HID **host auto-release all keys** (HOGP), and teardown already zeroes `[struct+4]` (`strb r7,[r4,#4]`, r7=0 @`0x811ade`) while reconnect rewrites it (`strb r5,[r4,#4]`, r5=1 @`0x8118fa`, bit1=0) and re-sets variant-4 (`strb r0,[r4,#0x15]`, r0=4 @`0x811904`). (No link-transition hook needed — and hooking mid-teardown/reconnect was unsafe anyway.)

**The change — make the trailing zero conditional on byte8, nothing else.** Stock variant-4 brackets its sends with `orr [struct+4],#2` (`0x81d174-7a`) … `bic [struct+4],#2` (`0x81d188-8e`) — **keep both brackets exactly as-is** (bit1 stays transient, the report-builder's connected-mode flag). Only replace the two fixed sends (`0x81d17c`–`0x81d187`, the `mov r0,r5; bl 0x81cb0c; movs r0,#0; bl 0x81cb0c` block) with a `b.w` to an appended thunk that emits **one** send chosen by `r7` (byte8) and returns to `0x81d188`:
```
    ; r5 = keycode, r7 = byte8, both live at 0x81d17c (0x81d136 never writes r7; r5=keycode)
    cmp   r7, #0
    ite   ne
    movne r0, r5        ; DOWN (byte8!=0): press keycode
    moveq r0, #0        ; UP   (byte8==0): release
    bl    0x81cb0c
    b.w   0x81d188       ; fall back into the stock `bic bit1` + return
```
No `0x2F`/`0xF0` gate — every key defers. Variants 0/1 and the shared builder untouched. This is a **clean code boundary** (Codex already deemed the variant-4 trampoline straightforward), *not* a mid-flow hook into someone else's frame.

**STUCK-KEY GUARDRAIL — structural, no code (DECIDED 2026-09-26).** The dangerous historical failure was a *repeat storm* (the old active-HID branch re-emitting); this design never re-emits, so its worst case is one key held too long. Coverage: (1) single-usage replace — next DOWN releases any stuck key; (2) host auto-release on BLE disconnect (HOGP); (3) existing teardown/reconnect reinit of `[struct+4]`/`[struct+0x15]`. **Residual (accepted):** UP lost **and** link stays connected **and** no further touch → one key held until the next touch (or disconnect) clears it. Low-probability (internal wired screen→main link, not BLE), low-severity (single key, no storm), self-healing. Validate with the stress tests (slide-off / walk-out / disconnect + 50-press soak).

*If a real stuck-key ever surfaces in testing, the fallback is a timer (RE'd but NOT needed now): `0x26c3e`=`os_timer_restart(pp_handle, ms)` (r1 is milliseconds, not a callback — corrects the earlier note), `0x26c64`=`os_timer_stop(pp_handle)`, `0x26bd6`=`os_timer_create(pp_handle, name, id, interval, reload, cb)` (cb = 6th arg at `[sp+4]`). Adding it would require a free RAM word for the handle, a non-colliding timer_id, and proof `0x81cb0c` is callable from the timer-service task — none of which we take on for the shipped patch.*

**Load-bearing confirmations Codex MUST make before emitting a flashable blob (each one is a brick-or-break risk):**
1. **byte8 liveness:** prove `r7 == byte8` still holds at `0x81d136` entry by emulating the full `0x43`-handler → `0x81aff8` → `0x81d136` path (r7 is callee-saved, so it *should* survive, but confirm no intermediate reassigns it). If it doesn't survive, stash byte8 to a scratch global in the handler prologue (`0x81af42`) and read that instead.
2. **Touch-only:** confirm `0x81d136` (variant-4) is reached **only** from the `0x43`/touch path and never by physical keys — grep every `bl`/`b.w` target `== 0x81d136`. If physical keys share it, gate the deferral so only touch-originated frames (those carrying a byte8) defer; physical keys must keep their real hardware down/up.
3. **Single-key holds:** the e-paper screen is single-touch, so at most one key is held at once → one busy latch + one timer suffices. Confirm there's no path that holds two touch keys simultaneously (would need per-key state).
4. **`0xc234` coverage:** resolve the screen-side `0xc234` callback (§9) — if it turns out to serve real keys, those keys need the screen repoint too, or "all keys" is incomplete. (Screen-side item, but it gates whether this main patch truly covers everything.)

**Placement + size + digest — model the proven builder, don't hand-pick an offset:** free space is tight and partly unsafe — the two early zero runs (file `0x400`/`0x4f0`) sit in/next to the **digest-excluded `image[752:1008]` region** (likely a signature/reserved area — do NOT place code there), and the tail free runs (VA `0x827416`/90 B, `0x8274af`/49 B) are fragmented. `screensaver_firmware.py` already appended the main forwarder near VA `0x82792c` and correctly updated the size word (`0x208`) + Realtek digest (`0x374`); **place the new handler + timer/flush thunks exactly the way that builder does** (append past the current used tail, VA `0x827a17`, update size + digest), and re-run `realtek_digest` to verify.

**Build + validation (offline, before any flash):** `assemble_stable('cortex-m4', …)` → apply the trampoline + appended code + digest → self-checks (every patched site disassembles to intent; **all other bytes byte-identical to base**; digest valid; size word consistent) → extend `check_touch_digits.py`: drive variant-4 with `byte8=1` → assert **exactly one** `0x81cb0c(keycode)` call and **no** `0x81cb0c(0)`; drive `byte8=0` → assert **exactly one** `0x81cb0c(0)` and no keycode send; assert the `orr #2`/`bic #2` bracketing still runs on both paths (bit1 transient, restored); assert `b.w` returns to `0x81d188` with the stock frame intact. A back-to-back `byte8=1`(K1) then `byte8=1`(K2) run confirms single-usage replace (K2's usage supersedes K1 — no explicit release needed). Fence all writes as the prior builders do. The assemble→capstone-self-check→unicorn loop must pass before a blob exists.

**Flash order (unchanged, §5):** SCREEN first (recoverable, verify the hold end-to-end via the `rk` edge-timing test), MAIN **last** (no rollback), alkalines only, "Unlikely Error on activation is EXPECTED," recovery = re-flash `957c9549`. Then the §6 acceptance + stress tests (50+ presses, mid-hold slide-off / range-walkout / disconnect → exactly one down + one up each, confirmed on the phone HCI snoop).
