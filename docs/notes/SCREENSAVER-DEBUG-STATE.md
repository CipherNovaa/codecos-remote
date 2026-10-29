# Screensaver firmware — debug state & Fable brief (2026-09-23)

## v15 (2026-09-24, Fable) — WAKE/RESTORE HARDENING (keypress FREEZE + pickup DOUBLE-SWITCH) — BUILT + VALIDATED, NOT FLASHED
**The two bugs, from the disassembly (v14 NOREARM flashed and working at idle):**
- **Keypress FREEZE (root cause).** The v14 restore only set `SHOWN := 0xff`; the loop's redraw `0xe65c` then called the stock page
  dispatcher `0x1ec5c`, whose tail (`0x1ef0a`) sets the full-refresh flag `0x20000a25` from the stock **refresh policy `0xfa3c(state)`**:
  it returns PARTIAL for the device pages `0x5b..0x5d` (mask `0x7803 >> (state-0x50)`), for any `0x19`-table page after its first
  draw (`0x20000a2d`), and for the `0x23`-table pages (incl. `0x34`/state 0) whenever the LVGL draw-buffer flag `0xf9d0()` is set.
  `0x1858` then issues `0x22/0xcf` with the **partial LUT (`0x27f36`: two active waveform phases vs four in the full LUT `0x2801f`)**
  unless the partial counter `0x20000a24 >= 0x1e`. A partial LUT cannot erase a dense full-contrast image (`dense-art`), so the panel
  visibly stayed on the saver while the framebuffer already held the page; every later key wake was `ignored` (`saver_active 0`)
  and the stock wake `0xbe10` never re-renders, so nothing repainted until the next sleep-draw's forced FULL blit (`0x22/0xff`) —
  exactly the observed "frozen until an idle cycle un-sticks it". Light images survive a partial update; dense ones don't.
- **Pickup DOUBLE-SWITCH.** The same lost restore: the framebuffer held the page but the panel held saver A; when main went quiet the
  sleep hook found `fb != A`, **picked another slot (A is excluded when another is filled) and full-blitted B** — the "switch" — and
  the next genuine wake restored the page. A quiet pickup (no UART traffic after the wake) would also re-sleep 500 ms after the wake
  stamp (`0xe5a8`: `now - 0x20006bc8 >= 500`) and swap images even with a working restore.
- Secondary hazard found on the way: for the "sync" page group (state 0 etc.; their builders clear `0x200008b8` at `0x1ee5e`) the
  stock `0x1858` tail runs the stock wait `0x1e160`, whose **1.5 s timeout pulses RST (`0x1e11c`) mid-refresh**; a full refresh
  measured 1.31 s (`busy_ms10 131`) leaves ~0.2 s of margin (cold panels are slower). `0x1848`/`0xbe10` were re-confirmed: the wake
  path pulses RST (`0xc0ac`) and never re-renders; the panel init `0x18ac` is boot-only (single caller `0xa6b8`).
**What v15 changes (`screensaver_screen.c`/`.S`/`.ld`; sleep blit, picker, slots, forensics untouched):**
- **Counter lever:** `ss_push` (sleep-draw + SHOW) leaves `0x20000a24 = 0x1e` after its own full update → the NEXT stock flush is a
  FULL update (`0x1858` @`0x186e`: counter ≥ 0x1e → `0xc030`, counter := 0) whatever `FULL_FLAG` the dispatcher chose; the stock
  policy resumes afterwards (no sticky flag). Proven against the REAL `0x1858` in the validator (counter 0x1e → `0x22/0xff` + 0;
  5 → `0x22/0xcf` + 6).
- **5th patch site `0xe694`** (`bl 0x1ec5c` → `bl ss_dispatch_hook`, region C `ss_dispatch`): before EVERY page build, if BUSY reads
  high wait ≤ 4 s (ours: never the RST-on-timeout `0x1e160`); after a genuine restore (`rst_pending`) re-assert the counter, set
  `0x200008b8 := 1` (the restore's full refresh is guarded by our bounded waits — this hook, the sleep hook, SHOW — not the stock
  1.5 s/RST wait) and record **`rst_policy`** (the `FULL_FLAG` the stock chose: 0 = partial = the v14 freeze mode) and **`rst_sync`**
  (the `0x200008b8` it left: 0 = sync group). `0xe65c` keeps `&shown/&target` in r4/r5 across the call: callee-saved, validated.
- **Grace:** a genuine restore arms `grace_armed` + `rst_tick`; for **20 s of awake time** the sleep hook returns 0 (stock sleep
  deferred, decided again next pass; `grace_defers`) instead of drawing a NEW image; the skip path (fb already holds the current slot)
  still sleeps silently. `quiet_ms10` = awake ms/8 from the restore to the first sleep decision (how fast main went quiet).
- **Pre-waits everywhere:** the sleep hook pre-waits BUSY on every path (draw, skip, saver-off) and SHOW pre-waits: the panel is never
  deep-slept or overwritten mid-refresh. `wait_busy` takes the timeout counter it should bump.
- **Readback regs 84..99** (NREGS 100, `ext_version 15`, magic `SSRF`, `ss_state` 68 → **84 B** `0x20007f80..0x20007fd4`): 84 rst_pending,
  85 grace_armed, 86 disp_calls, 87 disp_forced_full, 88 disp_busy_pre, 89 disp_ms10 (×8 ms), 90 disp_timeouts, 91 grace_defers,
  92 quiet_ms10, 93 **wake_snap** (bit0 rx-edge, 1 slept `0x872`, 2 LPTIM-woke `0x873`, 3 BUSY-at-wake, 4 PA2, 5 ARMED, 7:6 pre-wake
  mode — the raw wake source, taken before `0xbe10` pulses RST), 94 rst_policy, 95 rst_sync, 96/97/98 LIVE `0x20000a24`/`0x20000a25`/
  `0x200008b8`, 99 grace seconds. `reg_read` is now table-driven (`REG_SRC`/`REG_LIVE`, section `.ctab` in region C) to fit;
  numbering 0..83 unchanged. `fb_matches`/`caller_code`/`ss_lptim` moved to region C for the region-B budget.
- Tool `codec_screensaver.py`: 100 regs, `status` decodes `wake_snap`, `rst_policy`, `rst_sync`, `grace`, `quiet_after_wake`,
  `dispatches`, `dispatch_wait`, `refresh_live`; reads 61/84/100 regs for v13/v14/v15.
**Candidates (`artifacts/screensaver-v15/`, both 180608 B, reproducible — scratch rebuild = identical SHAs; `manifest.json`):**
- **`screen-screensaver-v15-NOREARM-DO-NOT-FLASH.bin` — SHA `64ec0c29e2c91a5623276b4ffb9f40cf86a2bf2f09c80714cc3f34130e11ed73`
  (⭐ THE ONE TO FLASH: v12–v14 NOREARM behaviour, variant reg 23 = 4).**
- `screen-screensaver-v15-REARM-DO-NOT-FLASH.bin` — SHA `ba6be4b86c084407fd0bcf37d99c825f9b9b157afcf1b6bc205930cd85088903`.
- main v2 OPTIONAL unchanged (`26b9ea48…`); the installed main v1 forwards everything v15 needs.
- Budget: `.text` 1380/1424 (44 free), `.b` 976/1072 NOREARM (96 free; REARM 1004), `.c` 900/912 (12 free) → 2956 changed bytes
  (NOREARM; REARM 2978), all inside the 3 padding regions + the **5** sites (`0x1c3a2`, `0xe632`, `0xbe12`, `0x1d67c`, `0xe694`);
  recovery `0x1c2ce..0x1c2f2`, `0x1c4e4..0x1c664`, `0xe00` sector, `0x1848`, `0x1ac68`, page table[0x59], `0x1858`, `0x19da`, the
  whole dispatcher `0x1ec5c..0x1ef30` and `0xfa3c` byte-identical (checked independently of the builder); the builder additionally
  asserts the policy plumbing stock (`0xe690/0xe698`, `0x1ec7e`, `0x1ee5e`, `0x1ef0a`, `0x186e`, `0x1888`, the `0x20000a24/25` pools,
  `0xc030`/`0xc090` heads). ARMv6-M listing gate passed; a linear capstone pass flags only literal-pool words.
**Validators (PASS):** `check_screensaver.py` **17 groups per variant** (v14's 15 + v15: the counter lever vs the REAL `0x1858` incl. the
sync-group wait; SHOW/sleep blits leave the counter at 0x1e; dispatch hook — ordinary page build leaves the stock policy alone, a
pending restore → counter 0x1e + `0x200008b8 = 1` + `rst_policy/rst_sync` recorded, BUSY high → 13 reads/11 polls first, stuck BUSY →
capped at 4 s and still dispatched, r4 preserved; grace — deferral returns 0 with only the pre-wait read, `quiet_ms10` sampled once,
expiry draws, in-grace skip still sleeps, saturation; restore arms `rst_pending/grace_armed/rst_tick`, `wake_snap` bit-exact, a
suppressed blip arms nothing; pre-wait on the skip/no-image paths) + main v2 (2). `check_screensaver_e2e.py` PASS (REG 16 masquerade
= `90 0f`).
**Flash + acceptance recipe (the author, OFA app "E-paper - Select File", the NOREARM file; images survive the reflash — slots are flash):**
1. `codec_screensaver status` → `ext_version 15`, `sentinel_V 86`, `variant direct-blit-NOREARM`, `slot_present` as before,
   `grace idle`, `dispatches N page builds` (counts from boot), `rst_policy - (no restore yet)`.
2. Load images incl. `dense-art` (`set … --slot N`), `cycle random`. Idle until a HEAVY image is on the panel (repeat idles until it is).
3. **(a) key wake:** press a physical key → the devices page must come back with ONE full (flashing) refresh, no freeze; press more keys
   → they work. `status` → `restores +1`, `restore_caller uart-in-sleep` or `pa8/rx-edge flags`, `disp_forced_full +1`,
   **`rst_policy`** = the verdict (`PARTIAL …` = the v14 mechanism confirmed on this page; `FULL` = the page was already full-policy and
   the freeze was the sync-wait/in-flight hazard instead), `rst_sync`, `wake_snap` names the source, `partial_cnt` small again.
4. **(b) pickup wake:** idle to a heavy image again, pick the remote up without pressing anything → the devices page, and it must
   STAY (no switch to another saver) while you hold it; put it down; the saver returns only ≥ 20 s later (or when main goes quiet,
   whichever is later). `status` → `quiet_after_wake` (how long main kept it awake), `grace_defers` > 0 only if main went quiet
   inside the 20 s, `picks`/`sleep_draws` advanced by exactly one per idle, never two.
5. Repeat (a)/(b) ~5× each across images; `disp_timeouts` must stay 0 and `busy_timeouts` 0; `dispatch_wait` shows `BUSY was low`
   at the last build in the common case.
**Residual risk only hardware settles:** panel settle timing is physical — the partial-LUT root cause is inferred from the stock
policy code and the LUT tables, not observed; `rst_policy`/`rst_sync` after the first wake are the proof (if `rst_policy` reads
`FULL` on the author's idle page, the counter lever is still correct but the freeze was the 1.5 s RST-on-timeout / in-flight collision,
which the dispatch pre-wait + `0x200008b8` bypass address). Unknowns: whether BUSY reads sanely right after the wake RST pulse
(`wake_snap` bit 3, `disp_busy_pre`), whether main really stays chatty ≥ 20 s after a pickup (`quiet_after_wake`), and the cost of
the dispatch pre-wait if BUSY ever sticks high (`disp_timeouts`, up to 4 s per page change). Final proof is the author's eyes on v15.

## v14 (2026-09-24, Fable) — THE "v13 CAN'T PROGRAM SLOT 0" REGRESSION IS NOT IN THE CODE; v14 = FORENSIC FLASH PATH — BUILT + VALIDATED, NOT FLASHED
**Question asked:** why can v13 not program `0x2e000` (slot 0) when v12 could and v13 programs `0x30000` (slot 1)?
**Answer (root cause, two sentences):** there is no v13 regression in the slot-0 erase/unlock/program/crc path — the flashed
v13 bytes, run under a SLOCK/CR-faithful model of the REAL stock driver (`0x1afbc/0x4fc4/0x4fe4/0x5034/0x50c0/0x4edc`,
reset SLOCK = 0 = all locked as the RM says), issue for slot 0 exactly what v12 and v6 issue (525 single-bit SLOCK2 writes
for bits 28..31 at `0x40020040`, 16 erases `0x2e000..0x2fe00`, per-2 KiB programs, base/crc/header for i = 0; slot 1 is the
same path on SLOCK3 bits 0..3). The premise was wrong: **no `set` ever ran under v12** (transcript: every v12-era
`cmd_calls` increment is a `status` read; the last slot-0 writes were v6's at 02:35Z/03:30Z, `--no-verify`, and EVERY
slot-0 write after v6's first one carried the SAME `ss_test.png`, which cannot distinguish a working erase/program from a
silent no-op) — so "v12 could, v13 can't" was never measured; the first-ever slot-0 write of DIFFERENT content was the v13
one that failed.
**What the live evidence actually says (from the session transcript, 2026-09-24 19:38Z–21:55Z):**
- runs (all 16-byte DATA chunks = 507 frames, ~0.79 s/frame): slot 1 image-a ✅ 19:48Z, slot 2 image-b ❌ 19:57Z, **`reg 12` =
  `last_err 0` at 19:59Z**, slot 1 image-a ✅ 20:14Z, slot 2 image-b ❌ 20:22Z, slot 3 image-c ❌ 20:31Z, slot 0 image-c ❌ 21:01Z
  (map `[11....]` → `[.1....]`: the BEGIN **did erase sector `0x2e000`**, so SLOCK2 bit-28 unlock + OP=2 erase + the RM
  "operation code in 0–32K" rule all hold for bank 2), batteries out 21:04Z–21:47Z (1.53 V/cell), slot 0 image-c ❌ 21:54Z
  (**first flash op after a clean power-on, SLOCK3 never touched → no "bank-3 history" theory survives**).
- `last_err 0` after the slot-2 failure = **no erase / program / header error was ever flagged**: the COMMIT NAKed on the
  crc (bytes never landed, or did not retain). That contradicts the earlier ceiling analysis's mechanism (it predicted
  `last_err 2` = refused program). `last_err` was never read after the slot-0 failures — it is still in RAM (`reg 12`,
  `reg 59`) unless the batteries were pulled again.
- The seven runs **confound ADDRESS with IMAGE**: image-a only ever went to slot 1; image-b/image-c only to slots 0/2/3.
  image-b / image-c / image-a blobs contain 320 / 97 / 18 all-zero 16-byte DATA frames. Every DATA frame of all three
  images parses and programs correctly through the REAL screen ISR → validator → dispatcher in emulation, and main v1's
  W 0C bridge (`0x82792c`) and `SEND 0x81a540` are content-blind — so no offline mechanism for either axis; only
  hardware can split them.
- Stock RM facts (HC32L19x RM Rev1.72 §10.3/10.6/10.8): SLOCK0..3 at `0x30/0x34/0x40/0x44`, reset 0 (locked), 1 bit =
  4 sectors = 2 KiB, a protected sector's erase/program is VOID (no error return, only `FLASH_IFR.IF1`); the flash-op
  code must execute from 0–32K (the stock driver at `0x50xx` does); PC-in-target-sector refusal (never our case).
**What v14 changes (source `screensaver_screen.c`; `.S`/`.ld` untouched; sleep/blit/picker/hooks untouched):**
- `fl_unlock()` = stock single-bit unlock **+ RM 10.3.1 Step 7**: read the SLOCK bit back, retry ≤ 8, else code `0xa`.
- `fl_erase()` reads every erased sector back as 512 × 0xFF (a void erase → code `8`); driver failure → `7`.
- `fl_write()`/`fl_erase()` record `err_addr` + driver code (`1` readback mismatch, `6` OP refused, `9` BUSY timeout).
- DATA keeps `rx_crc` (arrival-order crc32 state; a frame at offset 0 restarts it), `rx_frames`, `rx_bytes`.
- COMMIT: `diag = 0x10 | rx_ok | flash_ok<<1`; a wrong flash crc NAKs but **leaves `rx_active = 1`** so the tool can re-send
  all DATA (identical bytes are a no-op; a dropped frame's 0xFF cells take the data) and COMMIT again with NO new erase.
- regs **61..83**: 61-64 rx_crc state, 65/66 rx_frames, 67/68 rx_bytes, 69-72 err_addr, 73 err_code, 74 diag, 75
  slock_tries, 76 pad, 77 `FLASH_IFR[7:0]` (bit 1 = IF1), 78 `FLASH_CR[7:0]`, 79 `SLOCK2[31:24]`, 80/81/82 `SLOCK3` bytes
  0/1/2, 83 first DATA byte of the slot in flash. NREGS 84, ext_version **14**, `ss_state` 52 → **68 B**
  (`0x20007f80..0x20007fc4`, magic `SSRE`).
- Tool `codec_screensaver.py`: 84 regs; after a MISSING verdict `set` reads regs 11/12/61-83, prints a **verdict**
  (BEGIN/DATA/COMMIT failed at `err_addr` with the driver code + IFR/SLOCK state · TRANSPORT: N/505 frames arrived ·
  RETENTION: all arrived and verified, flash crc wrong) and, if the transfer is still open, **re-sends all DATA + COMMIT
  once** and verifies again. `status` prints the decoded forensics.
**Candidates (`artifacts/screensaver-v14/`, both 180608 B, reproducible — scratch rebuild = identical SHAs; `manifest.json`):**
- **`screen-screensaver-v14-NOREARM-DO-NOT-FLASH.bin` — SHA `11d40dee404c7920d7260d887a8701ed713f55038db6557186b1230d96a733d2`
  (⭐ THE ONE TO FLASH: v12/v13 NOREARM behaviour, variant reg 23 = 4).**
- `screen-screensaver-v14-REARM-DO-NOT-FLASH.bin` — SHA `0a2563c3a02c7ca28b025e8bde03155fdfe55df164074fe3d80fd6b99a5e80b9`.
- main v2 OPTIONAL unchanged (`26b9ea48…`); the installed main v1 forwards everything v14 needs.
- Budget: `.text` 1380/1424 (44 free), `.b` 868/1072 NOREARM (204 free; REARM 892), `.c` 900/912 (12 free) → 2826 changed
  bytes (NOREARM), all inside the 3 padding regions + the 4 sites; recovery `0x1c2ce..0x1c2f2`, `0x1c4e4..0x1c664`,
  `0xe00` sector, `0x1848`, `0x1ac68`, page table[0x59] byte-identical (checked independently of the builder).
**Validators (PASS):** `check_screensaver.py` **15 groups per variant** (v13's 14 + v14 forensics: BEGIN resets the diagnostics
and issues one verified unlock per sector; a dropped DATA frame → COMMIT NAK, `diag 0x10`, `rx_active 1`, no header; the
lone frame re-sent → present (`diag 0x12`, stream out of order); a full re-send from offset 0 → `diag 0x13`, rx_frames 64,
rx_bytes 8072, `~rx_crc == crc32(blob)`; a second COMMIT after success NAKs; modelled retention failure → `diag 0x11`; a
SLOCK bit that never sets → BEGIN NAK `last_err 1`, `err_code 0xa`, `err_addr = slot base`, 8 tries, NO erase issued; a void
erase → `err_code 8` at that sector; a programmer mismatch → `last_err 2`, `err_code 1`, `err_addr`; the v13 dead-slot model
→ `7`; MMIO readbacks 77-83 exact; malformed frames + reg 84 → NAK) + main v2 (2). `check_screensaver_e2e.py` PASS (real
ISR/validator/dispatcher; REG 16 masquerade = `90 0e`; its stubbed unlock now models the single-bit SLOCK write). Also run
outside the validators: the real stock driver under a SLOCK/CR-faithful MMIO model with the author's exact sequence (slot 1,
2, 1, 0) programs every slot; ARMv6-M gate passed (the builder's assembler-mapping listing check; a linear capstone pass
flags only two literal-pool words).
**Flash + acceptance recipe (the author, OFA app "E-paper - Select File", NOREARM file). BEFORE flashing, two free readings on the
CURRENT v13 that decide the whole question:** `codec_screensaver reg 12` (`last_err`: 0 = crc mismatch, 1 = erase, 2 =
program, 3 = header) and `reg 59` (`rx_slot`) — RAM still holds the slot-0 verdict unless the batteries were pulled again.
And the decorrelating pair on v13 as-is: **`set image-a.png --slot 0`** then **`set image-c.png --slot 1`** (image vs address).
Then, on v14:
1. `codec_screensaver status` → `ext_version 14`, `sentinel_V 86`, `variant direct-blit-NOREARM`, `slot_present` (slot 1's
   image-a still there: `slots 1 [.1....]`), forensics all zero, `flash_ifr`, `slock2_hi/slock3` as the last op left them.
2. **`set <img> --slot 0`** → the acceptance line is **`slot 0 PRESENT`**. If MISSING, the tool now prints the verdict and
   the register dump, and (if the screen left the transfer open) re-sends and re-verifies by itself; paste the whole tail.
   Reading the verdict: `BEGIN FAILED … code 8/0xa` + `IF1` → the die refuses that 2 KiB block (a lock/silicon fact, not
   code); `DATA FAILED … code 1` → a byte did not take at `err_addr`; `TRANSPORT` → frames are being lost between the
   tool and `ss_command` (main/UART, not flash — the resend should then succeed); `RETENTION` → bytes verified at program
   time but read back wrong at COMMIT (die/marginal timing).
3. `set <img2> --slot 1` → `slot 1 PRESENT`; `status` → `[11....]`; `cycle random`; idle → A; key → idle → B ≠ A; repeat
   ~6×: never the same twice in a row; `picks`/`sleep_draws` advance together, `cur_slot` = what is on the panel.
**Residual risk only hardware settles:** whether slot 0's failure is transport (fixable by the resend, and then likely
image-dependent), a refused/void block (the RM's IF1 + `err_code 8/0xa` will say so — not fixable in code), or retention
(`diag 0x11`); slots 2–5 keep the prior ceiling verdict but note its `last_err 2` mechanism is contradicted by the live
`last_err 0` — v14's `diag`/`err_*` will name their real mode too. `reg 79..82` read live MMIO (harmless 32-bit reads).

## v13 (2026-09-24) — SIX SLOTS + RANDOM/SEQUENTIAL CYCLE + CLEAR/MODE/THRESHOLD on the v12 blit — BUILT + VALIDATED, NOT FLASHED
**What it is.** v12 NOREARM (working on hardware) with the SOURCE slot made dynamic; the sleep-draw itself (wrapper
order, waits, fallback, scrub, NOREARM rule, one-pass deferral, activity gating) is untouched. **Candidates**
(`artifacts/screensaver-v13/`, both 180608 B, reproducible — a scratch rebuild gave identical SHAs — `manifest.json`):
- **`screen-screensaver-v13-NOREARM-DO-NOT-FLASH.bin` — SHA `0c2f69ff815b095f68177e29e66923cb1008463ad66ccd919cc822314cac040c`
  (⭐ THE ONE TO FLASH: same NOREARM behaviour v12 proved; variant reg 23 = 4).**
- `screen-screensaver-v13-REARM-DO-NOT-FLASH.bin` — SHA `c9f54df8c90d55b5a3747604d923e2105c2244a9bcea7698c197599d2f5c3a77`
  (calls `0x1848` after the settle; only if the author ever wants touch-to-wake back; variant 3).
- `main-screensaver-v2-OPTIONAL…` unchanged (`26b9ea48…`); the INSTALLED main v1 is sufficient — emulated it: v1 forwards
  BEGIN slot 0–5, DATA (≤128 B payload), COMMIT, SHOW [slot<6], THRESHOLD, CLEAR slot|0xff, MODE 0–2 [+slot], STATUS.
**Changed vs v12 (all in `screensaver_screen.c`; `.S`/`.ld` unchanged but for comments):**
- **Slots:** `0x2e000 + i*0x2000`, i = 0..5 (`0x2e000..0x3a000`, all past the image end `0x2d180`, inside the stock
  driver's `< 0x40000` bound). `BEGIN <slot>` stores `rx_slot`, erases exactly that slot's 16 sectors; `DATA`/`COMMIT`
  program only it (`fl_ok` range-checks every program to the BEGIN slot; `fl_erase` range-checks to the slot window;
  a validator asserts every flash op lands inside the addressed slot, incl. an offset at the top of the image).
  COMMIT writes the `SSAV`+8072 header only after the crc32 of the programmed bytes verified → a slot that fails to
  erase/program never becomes present. `present(i)` = header check; **reg 0 = 6-bit presence map** (STATUS byte 2 too).
- **Picker (`pick`, region C, 168 B, Thumb-1 only, two `muls`):** at the `0xe632` decision, among FILLED slots.
  MODE 0 random (default): `x = (seed ^ ms_tick[0x5150] ^ ADC_raw[0x20000a70] << 20) * 1103515245 + 12345`,
  `idx = ((x >> 16) * count) >> 16` (no divide), the slot already on the panel is EXCLUDED whenever another is filled
  (so ≥ 2 slots never repeat back-to-back); seed lives in `ss_state` (survives deep sleep; a reset reseeds from the
  tick). **Entropy = SysTick ms count at the sleep decision (how long the user was active) ⊕ the battery ADC raw
  sample.** MODE 1 sequential: the next filled slot after the current one, wrapping. No filled slot → stock sleep.
- **Skip rule unchanged:** a sleep entry whose fb already holds the CURRENT slot (`cur_slot`) sleeps silently (`skips`);
  a new slot is drawn only after a genuine wake re-dispatched the normal page (or SHOW/CLEAR changed things).
- **CLEAR `05 <slot|ff>`:** erases the 512-B header sector of a PRESENT slot (all = every present slot); absent slots
  are not touched (no flash op); if the cleared slot was on the panel the next sleep draws another.
- **THRESHOLD `04 <v16>`:** 0 → `disabled` (stock sleep, nothing drawn), else enabled; the value is kept for readback
  only — the draw is always at the sleep instant (v12 cadence). `0xFFii` stays the REG alias (main v1 path), `0xFExx`
  (CHEM alias) NAKs. **MODE `06 <0|1>[slot]`** (3rd byte ignored; mode 2 NAKs). **SHOW `03 [slot]`:** blit that slot
  (NAK if absent); no arg = the current slot, or a pick.
- **RAM:** `ss_state` 40 → 52 B (`0x20007f80..0x20007fb4`, still above the Keil ZI+stack end `0x20007e58`), magic
  `SSRD` (so a v12 leftover struct is re-initialised). New regs **52 cur_slot (0xff none), 53 mode, 54/55 threshold,
  56 picks, 57 map_at_draw, 58 disabled, 59 rx_slot, 60 seed_lo**; NREGS 61; ext_version **13**.
**Patch sites: IDENTICAL to v12** — `0x1c3a2` (cmd 0x50 → `ss_cmd_stub` 0x25290), `0xe632` (`bl ss_sleep_hook`),
`0xbe12` (`bl ss_activity_hook`), `0x1d67c` (`bl ss_lptim_hook`); page table[0x59] stock. **Byte budget:** `.text`
1144/1424 (280 free), `.b` 868/1072 NOREARM (204 free; REARM 892), `.c` 812/912 (100 free) → **2530 changed bytes
(NOREARM), every one inside the 3 padding regions + the 4 sites**; recovery `0x1c2ce..0x1c2f2`, `0x1c4e4..0x1c664`,
`0xe00` sector, `0x1848`, `0x1ac68` byte-identical (checked independently of the builder). **Nothing was cut** — all
of (a) per-slot BEGIN, (b) random, (c) presence map, (d) sequential, (e) threshold on/off, (f) CLEAR-all fit.
**Validators (both PASS):** `check_screensaver.py` **14 groups per variant** (v12's 8 + slots 3/5 store-verify with
per-slot flash windows + presence map/STATUS; SHOW <slot>; random picker over 60 idles — only filled slots, never a
back-to-back repeat, all 3 reached; sequential order `0,2,5,0,2,5,0`; single-slot repeats; skip-until-panel-changes;
THRESHOLD 0 sleeps without drawing, 300 re-enables; MODE 0/1/2; CLEAR slot/all/absent; a modelled DEAD slot 4
(erase returns 9) → BEGIN NAKs, `last_err 1`, never present, never picked; 19 malformed frames → NAK, no flash) + main
v2 (2). `check_screensaver_e2e.py` **9 wire cases** through the real ISR/validator/dispatcher: BEGIN slot 3 erases
`0x34000..0x36000` only, BEGIN slot 6 NAKs with no flash op, MODE/CLEAR-all ack, REG 16 masquerade = `90 0d`.
**Tool (`codec_screensaver.py`):** ext_version 13, 61 regs (`status` prints `slot_present = slots 0,2,5 [1.1..1]`,
`cur_slot`, `cycle_mode`, `threshold`, `saver`), `show --slot N`, per-slot verify after `set` (`(map >> slot) & 1`,
was `map & 1` for every slot), **DATA chunk capped at 128** (main v1/v2 refuse 129+ with `0c 02`; the old default 192
only worked because the MTU shrank it), `threshold` guarded to `< 0xfe00`.
**⚠ On-hardware unknown — slots 1–5 (`0x30000..0x3a000`) are UNVERIFIED.** They are flash **bank 3** (unlock `0x1afbc`
→ SLOCK reg index 5; slot 0 is bank 2 → index 4). Evidence they exist: the stock erase/program bound is `< 0x40000`,
and the 184.7 KB app rules out a 128 KiB part (HC32L1xx sizes are 64/128/256 KiB). Unknowable offline: whether bank
3 unlocks/erases/programs on this silicon and whether it held factory data. Fail-safe by design: erase/program
failures NAK, the slot never becomes present, the picker skips it; **only slot 0 is proven writable.**
**Acceptance / flash recipe (the author, via the OFA app "E-paper - Select File", NOREARM file):**
1. `codec_screensaver status` → `ext_version 13`, `sentinel_V 86`, `variant direct-blit-NOREARM`, `slot_present`
   (slot 0's v12 image is still there: `slots 0 [1.....]`), `saver enabled`, `cycle_mode random`.
2. **Upper-slot writability test FIRST:** `codec_screensaver set <img1> --slot 1` → the verify line must say
   `slot 1 PRESENT`; `status` → `slot_present = slots 0,1 [11....]`. If it says MISSING, read `last_err`
   (1 = BEGIN erase failed, 2 = DATA program failed, 3 = COMMIT header/crc) — bank 3 is dead/locked: stay on slot 0.
3. If slot 1 works: `set <img2> --slot 2` … `set <img5> --slot 5` (each verify PRESENT), `status` → `[111111]`.
4. `codec_screensaver show --slot 3` → that image on the panel at once (`show_blits` +1, `cur_slot 3`).
5. `codec_screensaver cycle random` (default anyway). Idle → image A drawn silently; press a key (restore) → idle →
   image B ≠ A; repeat ~6×: every filled slot should appear, never the same twice in a row. `status` → `picks` and
   `sleep_draws` advance together, `cur_slot` = what is on the panel, `map_at_draw [111111]`, `skips` counts the
   silent re-sleeps, `busy_*`/`restores` as in v12.
6. `cycle sequential` → 0,1,2,3,4,5,0…; `threshold 0` → next idle sleeps on the NORMAL page (`disabled 1`);
   `threshold 300` → back on. `clear --slot 4` → `[11111.]`, never drawn again; `clear --all` → `[......]`, stock sleep.
**Residual unknowns only hardware settles:** bank-3 writability (above); whether reading `0x30000+` headers is benign
if the region turned out absent (would HardFault → reset at each idle — recoverable by reflash, never a brick);
real-world entropy quality (the tick at the sleep decision + ADC LSBs — should shuffle fine, not a CSPRNG);
flash-erase time for a full BEGIN (16 sectors) on bank 3 vs the tool's 0.6 s pause before the first DATA.

## v12 (2026-09-24) — DIRECT FRAMEBUFFER BLIT (no LVGL, no saver page) — BUILT + VALIDATED, NOT FLASHED → ✅ NOREARM FLASHED + WORKING
**Why.** v11 readback proved the LVGL flush callback never runs at the `0xe632` instant (`busy_first 0`,
`flushed 0`), and v7–v10 proved a page-0x59 state gets "restored" by self-generated wakes. v12 removes both:
at the sleep decision it writes the panel itself, at the level of the stock flush callback, and touches no LVGL
state. **Candidates** (`artifacts/screensaver-v12/`, both 180608 B, reproducible, manifest.json):
- `screen-screensaver-v12-DO-NOT-FLASH.bin` — SHA `5297a1be539d5d80e7331d0647f8edc669d3e4627cdbdb95e448e6595704905d`
  (**recommended first**; re-arms the touch INT after the refresh settles, variant reg 23 = 3).
- `screen-screensaver-v12-NOREARM-DO-NOT-FLASH.bin` — SHA `2173a08787c6cc584bb9d5b5ba23daa1c8c913985f286fca2b0f8fb245a5bf4a`
  (never calls `0x1848`; touch cannot wake the saver sleep, keys can; fallback if the readback shows
  `restore_caller touch-INT(PA2)` phantom wakes; variant 4).
- Placement: `.b` 0x25290 (848 B / 824 B, 1072 free), `.c` 0x25898 (512 B, 912 free), `.text` 0x25e1c (800 B,
  1424 free); 1969 / 1947 changed bytes, all inside the three glyph-padding regions + the 4 sites below.
**Patch sites (all BL / table halfword; ARMv6-M listing gate passed):** `0x1c3a2` (cmd 0x50 → `ss_cmd_stub`
0x25290), `0xe632` (`bl ss_sleep_hook` 0x2529a), `0xbe12` (`bl ss_activity_hook` 0x252aa), `0x1d67c`
(`bl ss_lptim_hook` 0x252b8). **`0x1edf8` (page table[0x59]) is left STOCK** — v12 has no page. Recovery
`0x1c2ce..0x1c2f2`, `0x1c4e4..0x1c664` byte-identical; `0x1848`, `0x1ac68` and the whole stock EPD path the blit
calls (`0xbff0/0x19da/0x1858/0x6598/0xa1fe/0x17a8/0x5cfc/0x17fc/0xea10`) asserted stock by the builder.
**Framebuffer format — MEASURED, TRANSFORMED (bytewise NOT).** `set_px_cb 0xbff0`: index `21*y + (x>>3)`, bit
`1 << (7 - (x&7))` (MSB-first, 21-B stride, 384 rows = 8064 B — same geometry as our blob) but a pixel whose
brightness (`0x10948`) is ≥ 0x81 **ORs** the bit and a darker one **clears** it (0xc020) → **framebuffer 1 = WHITE**;
our lv_img blob stores 1 = palette[1] = **black**. So `fb[i] = ~blob[16+i]` (8-B slot header `SSAV/len` + the blob's
8-B palette; a validator caught the first build using +8). `0x19da` streams cmd 0x24 + the 8064 fb bytes verbatim
(`0x3f<<7`), `0x1858` does the full update (`0x18/0x80, 0x22/0xa1, 0x20, 0x1b` temp read, `0x1a`, `0x2c`, `0x22/0xff`,
`0x20`) when `0x20000a25` = 1 and skips its own busy-wait because `0x200008b8` (set by the page dispatcher, never
cleared) — all confirmed by running the REAL routines in unicorn (`check_screensaver.py verify_format`: every one of the
64512 pixels through the real `0xbff0` equals the blit; the real `0x19da` on the blit's fb yields 0x24 + ~blob).
**Sleep path (`ss_sleep_draw`, region B):** present? → `fb == ~image` already? (skip: `skips`++, silent sleep) →
pre-wait BUSY `0xea10` ≤ 4 s (`busy_pre`, `pre_ms10`; a stock refresh may still be running) → the stock flush wrapper
in the stock order: `0x6598` (NVIC IRQ6 off, PA10→GPIO), `0xa1fe(1,1)` PB0:=1, `0x17a8` (touch INT masked) →
copy/invert 8064 B → `0x19da(0x20004c3c)` → `FULL_FLAG:=1`, `0x1858`, flag restored → PB0:=0, `0x5cfc` (PA10→UART),
`0x17fc(6,3,1)` → 5 ms → `busy_first` → unconditional BUSY poll ≤ 4 s (`busy_ms10`, `busy_timeouts`) or, if BUSY never
reads high, a fixed 3 s fallback (`busy_fallbacks`) → 100 ms → [rearm build] `0x1848` (`rearms`) → 50 ms → scrub
`0x20006e50/51` (`scrubbed`) and any PA2 event `0x20006e43` (`pa2_scrubbed`, re-arm again) → [norearm: `ARMED:=0` so
the awake loop re-arms at 0xe7f0] → if main talked during the blit (`RX_COUNT 0x20006c90` ≠ 0) defer the sleep ONE
pass (`defers`) → return 1 → stock `0xbe8c` in the same pass. No `0x1848` before the panel is idle; no touch/wake
triggered; no LED/light touched (the light is the PC14/PC15 idle state machine `0x5794`, never called).
**⭐ Finding that changes the mandate: `0x1848` is NOT a backlight.** GPIO decode (HC32 DDL shapes): `0x52a0` =
PxOUT bit write, `0x51b8` = PxIN read, `0x5268` = pin AF select, `0x5188/0x51a0` = clear/set an IRQ-enable bit at
`0x40020d30 + 4*type + port`, `0x515c` = clear IRQ flag. So `0x17a8` = ClearIrq(PA2)+DisableIrq(PA2,falling) and
**`0x1848` = EnableIrq(PA2, falling)**. The port-A ISR `0x5a70` on PA2: disarm one-shot, `ARMED:=0`, `PA2_EVENT:=1`;
its consumer `0x4e3c` reads I2C addr **0x38** regs 1, 2&0xf, 3..6 (`>>6` event flag, `>>4` id) = **FT6x36 touch
controller** → **PA2 = the touchscreen INT**; the stock masks it during every SPI flush and re-arms it 100 ms after
(`0x1d5d6`). The mode-1 loop wakes fully on it (`0x1d630`), i.e. touch-to-wake NEEDS `0x1848`. Hence the two builds.
Also: PA8 (`0xd4f4`, flag `0x20006e50`, reads reg 9 via `0x1b25c`) is the "touch-line" of v8–v11; PA10 = UART RX edge;
PB0 (`0xa1fe(1,·)`) is asserted only around the SPI burst and the battery ADC sample (a load/level enable, not power).
**Restore on a genuine wake:** `ss_activity` (0xbe12 hook) sets `SHOWN := 0xff` so the loop's own
`target != shown` path (0xe65c) re-dispatches the current page (full refresh: dispatcher tail 0x1ef22 sets
0x20000a25) — only while `saver_active` and for: UART frame in sleep (2), touch INT (4), flags path (3) with the
RX-edge source or the PA8 line after ≥ 1 LPTIM wake. Everything else → `suppressed`. **SHOW (`03`)** = the same blit
while awake = an instant on-panel proof of the format (the next page change overwrites it; the next sleep skips).
**Validators:** `check_screensaver.py` PASS both variants, 8 groups each (real dispatcher routing cmd 0x50→0x25290 /
0x51→error stub / 0x02→stock / 0x40→stock; format proof vs the real `0xbff0/0x19da/0x1858`; SHOW blit order;
BEGIN/DATA/COMMIT; 12 malformed → NAK no flash; 52-reg readback + alias + STATUS; sleep hook: order, 41 polls, pre-busy,
fallback 3 s, 4 s cap, PANEL_ABSENT ignored, scrub, PA2 scrub/re-arm, one-pass deferral, no image; activity gating) +
main v2 (2). `check_screensaver_e2e.py` PASS: main's exact wire bytes through the real ISR `0x65b8` → validator `0x1d2bc`
→ dispatcher → `ss_command` for BEGIN (ack, 16 erases), DATA (nak w/o BEGIN), SHOW (nak w/o image), REG 16 via the
THRESHOLD alias (masquerade TX `a6 6a 02 00 08 00 90 0c …` = ext_version 12); cmd 02 still stock. Tool:
`codec_screensaver.py` REGS updated to 52 (ext_version 12; `status` prints variant / last_path / fb_bytes_written).
**Acceptance on hardware (`codec_screensaver status` after `set` + one idle cycle):** `sleep_draws 1` (+1 per idle),
`fb_bytes_written 8064`, `fb_match 1`, `last_path "sleep blit"`; `busy_first 1` with `busy_ms10 ~150–300` and
`busy_fallbacks 0` = BUSY line works (if `busy_first 0`+`busy_fallbacks 1` = BUSY reads low, fallback used; if
`busy_timeouts 1` = stuck high, 4 s cap — both still show the image); `restores 0` while idle, `skips` advancing on
later idles, `suppressed/scrubbed/pa2_scrubbed` small, `defers 0`; the panel **silently holds the image asleep, no light,
no keypad wake**; a key press → `restores +1` (`restore_caller uart-in-sleep`), a screen touch → `restore_caller
touch-INT(PA2)` (rearm build) and the normal page comes back with a full refresh. `codec_screensaver show` while
connected must put the image on the panel at once (`show_blits 1`). If `restores` tracks `sleep_draws` with
`restore_caller touch-INT(PA2)` → flash the NOREARM file.

## v11 (2026-09-24) — graceful final frame: real BUSY wait, quiet ordering, readback in region C
v10 read `busy_ms10 0`: its BUSY wait honoured the stock "panel absent" flag `0x20006bc4` (set at boot
by `0x1e190` if the panel is still busy 150 ms after its reset pulse — likely on real hardware; the stock
driver then skips ALL busy-waits and refreshes fire-and-forget). So v10 never waited, the stock sleep's
EPD deep-sleep (`0xbf68`) hit the panel mid-refresh, and the scrub ran before any blip existed.
**v11:** unconditional bounded BUSY poll (`0xea10`, ≤4 s); records `busy_first` (reg 40, raw BUSY right
after the render pass = refresh engaged) and `flushed` (reg 41, flush_cb ran: it zeroes BL_DONE); order is
render → BUSY idle → 100 ms → `0x1848` → 50 ms → scrub `0x20006e50/51` → sleep, so the panel is quiet and
its image latched BEFORE the EPD deep-sleep, and the blips from the refresh/GPIO transition are dropped
before they can wake the device (LPTIM-expiring window kept as belt-and-braces). No 0xbe10, no light,
no touch re-init happens on our path; those only occur if a blip still wakes the device. Readback moved
to region C (`.c` @ 0x25898, 2nd face padding). Acceptance: `busy_first 1`, `busy_ms10 ~150–250`,
`flushed 1`, `restores` flat, `suppressed`/`scrubbed` small, panel silently holds the image asleep.

## v10 (2026-09-24) — suppression WINDOW with an LPTIM expiry clock (one variant, sync)
v9 (both variants) still restored: `restore_caller touch/rx-edge`, `rst_src touch-line`, `ignored 0`
(the defer variant never armed the one-shot; sync counters not captured). The self-generated events are
the TOUCH controller's INT during/after our refresh and the `0x1848` power transition — same source as a
real touch, possibly several, and the ms tick freezes in WFI so time can't separate them. Also verified
the stock wake path (`0xbe10` body: `0xc0ac` RST pulse to leave EPD deep sleep, `0x5d18` touch re-init,
clock/IRQ re-enables) does NOT re-render — every revert seen was our own restore, so approach B (direct
framebuffer blit) is unnecessary. **v10:** the sleep-draw arms `saver_active` and zeroes
`lptim_since_draw`; after the BUSY wait it scrubs pending wake flags `0x20006e50/51` (blips raised during
the refresh); a **5th hook at `0x1d67c`** (`bl 0xbeec`, the mode-1 LPTIM re-sleep consumer — the only
clock that runs while asleep) counts LPTIM wakes. `ss_activity` restores only for genuine input: a UART
frame while asleep, or the flags path with the UART RX-edge source, or the touch-line source once ≥1
LPTIM wake (5..60 s, settings byte 0x254, exposed as reg 39) has passed since the draw; everything else
in the window is counted in `suppressed` (reg 36). New regs 34–39. Acceptance: asleep panel holds the
image; `restores` flat while `sleep_draws` advances; `suppressed`/`scrubbed` advance; `lptim_wakes`
counts; a real key/pickup (UART source) restores at once, a touchscreen touch restores after the first
LPTIM period.

## v9 (2026-09-24) — the readback named the path: touch/RX-edge flags, not PA2
v8 counters: `restores == sleep_draws`, `ignored 0`, `restore_caller = touch/rx-edge` (0x1d531). The
self-generated activity after our refresh arrives on the SAME flags path a real touch uses, so caller
identity cannot separate them. **v9 discriminates by sequence:** the sleep-draw sets `just_drew`; the
first 0x1d531 event afterwards is consumed (ignored once, counted, flag source recorded in reg 29:
1 = UART RX edge / 0 = touch line); later 0x1d531/0x1d48f events with pre-wake mode ≠ 0 restore. Also:
`0x1858` skips its busy-wait after any page change (`0x200008b8` set by the dispatcher, never cleared),
so the refresh runs async and the stock sleep could send EPD deep-sleep mid-refresh → v9 polls the
driver's BUSY reader `0xea10` (PB3, nonzero = busy) for ≤4000 ms before `0x1848`/sleep (NOT the stock
wait `0x1e160`: its timeout `0x1e11c` pulses the panel RST). New regs 28–33: just_drew, ign_src,
rst_src, draw_shown (SHOWN at draw time — settles the "saved_state 0" question: 0 IS the idle page),
busy_ms10, busy_timeouts. Acceptance: asleep panel holds the image; `restores` stops tracking
`sleep_draws`; `ignored` advances with `ignored_caller = touch/rx-edge`; real touch → `restores`+1.

## v8 (2026-09-23, late night) — v7 readback caught the last bug: a spurious RESTORE
v7 on hardware: nudge gone, image builds+paints at every idle (`sleep_draws == page_draws`), but
`restores == sleep_draws` — each draw was undone in the same cycle (panel slept on the normal page,
white flash on pickup). `saved_state 0` is CORRECT (the idle page is state 0; `0x34` is the page shown
while the config link is connected). The restore fired with pre-wake mode ≠ 0, i.e. a non-user wake
through `0xbe10` while asleep: the mode-1 block at `0x1d630` calls `0xbe10` on the PA2 event
(`0x20006e43`), the panel-side line that fires when OUR OWN refresh completes.
**v8:** `ss_activity` receives `0xbe10`'s pushed return address (at `[sp+16]` under our hook) and
restores ONLY for the input paths — `0x1d48f` (UART frame while asleep) and `0x1d531` (touch / RX-edge
flags) — with mode ≠ 0; `0x1d635` (PA2 in sleep) and `0x1d473` (first run) never restore. New regs
24–27: last activity caller, last restore caller, last ignored caller, ignored count (1 first-run,
2 uart-in-sleep, 3 touch/rx-edge, 4 pa2-in-sleep, 5 other). Acceptance: after idle cycles
`sleep_draws` advances, `restores` does NOT, `ignored_caller == 4`; a real key/pickup → `restores` +1
with `restore_caller` 2 or 3. If restores still track sleep_draws, `restore_caller` names the path.

## v7 (2026-09-23, night) — ACTIVATION SOLVED (app flash) → v6 RAN → sync sleep-draw + readback
**Flash path that COMMITS:** OFA app Advanced Mode → Custom OTA → "E-paper - Select File" (push the
.bin to the tablet). Proof: version-bump probe → `01 41` after power cycle; v6 booted and ran.
`screen_direct.py` transfers but never finalizes. Size must stay 180608 B.
**v6 on hardware:** cmd 0x50 works, slot 0 commits (runtime flash at 0x2e000 PROVEN), the sleep hook
fires and a full EPD refresh happens — but the deferred draw (target:=0x59 + idle-stamp restart) causes
a ONE-cycle nudge (flush-time battery sample → report → main lights keypad ~30 s, its UART/PA10-edge
traffic → `0xbe10` → our hook REVERTED the page), and the 2nd-cycle sleep holds the NORMAL page.
**v7 (`artifacts/screensaver-v7/`):** synchronous draw at the `0xe632` decision (page dispatcher
`0x1ec5c` → `lv_tick_inc(100)` [refresh task period 80 ms, 0x10be2] → `lv_task_handler 0x19a9c` →
real flush_cb → `0x19da`+`0x1858` full refresh → stock `0x1848` + BL_DONE → stock `0xbe8c` sleep in the
SAME pass; nothing returns to the loop, no report/nudge/revert). Activity restore only when the pre-wake
mode byte `0x20006bcc` ≠ 0 (real wake). Readback back (REG masquerade via `W 0C 04 ii ff` + `W 02`;
`codec_screensaver status`): 24 regs incl. cmd_calls, slot_present, sleep_hits/sleep_draws/page_draws/
restores/act_calls/act_mode, shown/target/lp_mode, variant. Two regions: `.b` 0x25290 (glue+hooks+
readback, 628 B) and `.text` 0x25e1c (cmd+flash, 804 B). A `-defer-FALLBACK` file (v6 strategy + readback +
real-wake gating) is emitted alongside in case the in-hook render does not run on hardware.

## ACTIVATION (2026-09-23, latest) — screen_direct never finalizes; W 02 tail is STALE
Power-cycle test proved `screen_direct execute` does NOT activate: it sends only `W A1`
(start: count+crc32) then `W A2`×N (128-B data) and stops after the last `a2 02`. It sends
NO finalize. The screen bootloader OTA (from `screen_ota_dryrun.py` + main encoders
0x81b5b4/0x81b622/**0x81b678**) is `0x46`(start,len32) → `0x48`(seq16+512B)×N → **`0x47`(exit/apply)**;
without the exit the loaded image is never committed, so every reset falls back to the last
valid app → the factory `v013f` keeps booting (W 02 tail `01 3f` = MAIN's cached screen version
at 0x20b166/7, only refreshed on main boot, so all prior reads were stale anyway). Not bricked
because activation never happened.
- Main W-opcode table: `A1`/`A2` share handler 0x834fb8; **`A3` → separate handler 0x82ece4**;
  `screen_direct` never sends A3. The 0x47-exit (0x81b678) is called from a CONFIG-command branch
  (0x81857c), not from the per-A2 data path → a distinct finalize opcode issues it. Leading
  hypothesis: **W A3 is the finalize** the app sends after data, which drives screen 0x47.
- ⚠ EVIDENCE BOUNDARY: the A1/A2/A3 handlers (0x82e/0x834xxx) and the screen-bootloader acks are
  ABOVE the captured APP image (ends 0x827a9c) and absent from all saved evidence (screen_ota_dryrun
  says so explicitly). So the A3 payload/framing and whether A1 even triggers the 0x40 enter cannot be
  confirmed offline. DO NOT bolt a guessed finalize onto screen_direct: the commit/exit is the ONE
  step where a bad image can brick (every failed flash so far was safe precisely because it never
  committed). Activate via the stock app's screenUpgrade (ota_manager `startOTAFromPath`/`finish`,
  which issues the full enter→data→finalize) OR capture one real app screen-upgrade BLE exchange to
  learn the A3 frame, THEN add it. Main v1 (W 0C) left the entire OTA path byte-identical to stock
  (A0..AF table, 0x40/46/47/48 encoders, 0x47-exit branch, 0x46-start caller all unchanged), so it did
  not perturb activation.

## v6 — WHY NOTHING EVER RAN, AND THE FIT (2026-09-23, late)
**The screen bootloader rejects any image larger than the stock 180608 B** (activation probe
reporting version `01 41` came back `01 3f` on hardware): every appended candidate v1..v5.1
was acked packet-by-packet but never activated; the mgs-font image kept running, NAKing
cmd 0x50 and never drawing. Also proven offline first: with main's exact wire bytes the
full receive path (real ISR `0x65b8` → validator `0x1d2bc` → dispatcher → our code → reply)
works (`check_screensaver_e2e.py`), so the logic was never the problem.
**v6** = minimal extension (932 B) placed INSIDE the image in the zero padding the font tool
left after the 3rd glyph bitmap (VA `0x25e1c..0x261c0`; capacity to `0x263ac`; every glyph's
bitmap ends at `0x25e19`), size exactly 180608 B. Kept: dispatch fix `0x1c3a2`, single slot 0
@ `0x2e000` BEGIN/DATA/COMMIT/SHOW, sleep-draw `0xe632`, activity restore `0xbe12`, page
table 0x59. Dropped: readback, NiMH hook, multi-slot/CLEAR/MODE/THRESHOLD/config. Next
unknown after it runs: runtime program/erase at `0x2e000` (past the image) on the real part.

## v5.1 — THE ACTUAL SEAM BUG (found by the first on-device readback, 2026-09-23 night)
`codec_screensaver status` on screen v5 + main v1 answered `SS01` but `REG 0` left main's version
cache at `01 3f`: cmd 0x50 never dispatched on the screen. Cause: the a55a dispatcher (`0x1c2f2`)
indexes its table as **`0x1c304 + 2*(cmd-1)`** (cmd 0x02 → `0x1c306` → `bl 0x1cbd0`), so cmd 0x50 is
at **`0x1c3a2`**; every candidate v1..v5 repointed `0x1c3a4` = **cmd 0x51**. Main always sent 0x50 →
error stub → NAK; main still answered `0c 01` (its SEND is asynchronous). No BEGIN/DATA/COMMIT/SHOW
ever ran on the screen. **v5.1 patches `0x1c3a2`** (`artifacts/screensaver-v5.1/`), validator now runs
the real dispatcher slice. Verify: flash v5.1 → `codec_screensaver status` (cmd_calls>0 = seam alive).

## v5 RESOLUTION (Fable, 2026-09-23 evening)
- **Root cause of the no-draw.** The screen renders ONLY via LVGL's task handler (`0x19a9c`, called
  from the main loop at `0x1d57e`) → flush cb `0xbf80` → EPD RAM write `0x19da` + refresh `0x1858`.
  `0x1848` is NOT a sleep entry: it is `0x51a0(0,2,3)` = a GPIO write fired 100 ms after EVERY refresh
  (flag `0x20006e46` set at the end of the flush cb). The real disconnected idle→deep-sleep path is
  `0xe5a8` (500 ms after the last activity stamp `0x20006bc8`, once target==shown, a flush completed and
  `0x20006e44` is armed) → `bl 0xbe8c` @ **`0xe632`** → mode 1, LPTIM arm, EPD deep-sleep cmd 0x10
  (`0xbf68`), SLEEPDEEP+WFI (`0x569c`). v1 hooked the LPTIM re-sleep consumer (mode 1, where the loop
  never reaches page dispatch); v2/v3 hooked the post-refresh GPIO call; **v4 patched `0x1848` with a
  Thumb-2 `b.w` (`F02B BCA5`) — ARMv6-M has no B.W → HardFault (`0x52b5`) → `0x6824` = SYSRESETREQ**:
  the screen MCU reset ~100 ms after every refresh, which looks exactly like "unchanged".
  `0x4d9c(1)` is only a flag consumed inside `0x1858`; there is no synchronous draw from command
  context (the stock low-battery page `0xbe64` also just sets target 0x3c).
- **v5 fix:** hook `bl 0xbe8c` @ `0xe632`; first decision DEFERS (target:=0x59, idle stamp restarted like a
  UART page command), the stock loop builds/renders/flushes the page, the next decision sleeps with it
  shown. Activity (`0xbe10`) restores. All 5 patch sites are BL/table entries; builder has an ARMv6-M gate.
- **Readback with the INSTALLED main v1:** screen answers `W 0C 04 <idx> ff` (THRESHOLD alias = REG idx)
  with a cmd-02 masquerade → main caches the two "version" bytes at `0x20b166/7` → last two bytes of
  `W 02` = `(0x80|idx, value)`. `codec_screensaver status` does this for 30 registers (sleep_hits,
  defers, draws, restores, cmd_calls, slot map, battery mV/%…) and restores `01 3f` afterwards.
- **Battery:** the SCREEN measures it (`0x1abf4`, HC32 ADC, mV=raw*6600>>12), `pct=(mV-4000)/20`,
  sampled right after each refresh (peak load) and **latched non-increasing** (`0x1ac7c`) → any sag
  ratchets 2a19 down until a battery pull. v5 hook @ `0x1ac68`: stock unless `chem nimh` (piecewise
  NiMH curve, max of last 4 samples). Main only copies the screen's byte (`0x20b0ce`) into BAS.
- **Flash slots:** 256 KiB part (bound checks < 0x40000), SLOCK0..3 cover banks 0..3, RW init is Keil
  compressed (image self-contained) → `0x2e000..0x3a1ff` writable and safe.
- Candidates: `artifacts/screensaver-v5/` (screen v5 = the fix; main v2 OPTIONAL relay). Not flashed.

Custom 1-bit image as the e-paper **sleep screen** on the OFA URC7985 remote: push a PNG
over BLE into a flash slot, and have the screen draw it at low-power entry so the
persistent e-ink retains it while asleep. Design in `SCREENSAVER-DESIGN.md`; multi-slot
params in the `codec-remote-future-directions` memory note.

## What is FLASHED right now (all recoverable except main is no-rollback)
- **Screen: `screensaver-v4`** (SHA `2badd4ab98a387fea171e4675ae8a00d8f806ec93384a872662934dcd671ee40`).
- **Main: `screensaver-v1`** (SHA `3b2e3240b640f33d322bbd1b5484bba26f8e6feb569833150c146feee4bf82de`),
  additive **W 0C** forwarder → screen a55a **cmd 0x50**. Buttons/labels verified intact.
- Bases: screen = `artifacts/screen-font-v5-22px-condensed/candidate-screen-font-DO-NOT-FLASH.bin`
  (SHA `03ea3242…`, 180608 B, VA = file + 0x1000); main = spare-keys-v2 (SHA `6daaf9d7…`).
- Tools: `screensaver_firmware.py` (builder, clang/lld + capstone), `check_screensaver.py`
  (unicorn validator), `codec_screensaver.py` (W 0C pusher, sub-ops BEGIN0/DATA1/COMMIT2/
  SHOW3/THRESHOLD4/CLEAR5/MODE6/STATUS7), `ofa_control.py`, venv at `artifacts/venv`.

## What WORKS (verified on hardware)
- Full flash pipeline: `+8k` size probe, screen v1/v2/v3/v4, main v1 all transferred and
  validated (`10 03 01`, "Unlikely Error" on activation = expected). Remote boots, works.
- PC → main W 0C: every push frame acks `0c 01`. **But that only proves the PC reached the
  MAIN — nothing about whether the SCREEN received it or wrote flash.**

## The BUG (unsolved): the image never displays
Symptom: after pushing an image to slot 0 and letting the remote idle-sleep (disconnected),
the e-paper sleeps on the **normal/fav page** — never the image, never blank.

### Hooks tried for the sleep-draw, all failed to draw at normal idle-sleep:
- **v1**: hooked `bl 0xbeec` @ `0x1d67c` (LPTIM idle-wake), idle-tick counter → no draw.
- **v2/v3**: hooked `bl 0x1848` @ `0x1d5d6` (in func `0x1d44c`) → slept **unchanged**; that
  `bl` is a conditional branch the normal idle path does not take. v3 made ss_sleep_draw
  **always** draw state 0x59 (blank if slot empty) as a diagnostic — still unchanged.
- **v4**: hooked the `0x1848` **function** itself (`b.w ss_bloff_hook` over its first 4 bytes,
  lr preserved; runs stock body `0x51a0(0,2,3)` after drawing) → **still unchanged** at
  idle-sleep, even though the main loop (`0x1d564`) calls `0xe79c` which calls `0x1848`.

### The observability deadlock (why we're stuck)
- While the config BLE link is connected, the screen shows a "connected checkmark" page that
  **masks** any page change we make (`show` did nothing visible).
- Disconnecting restores the normal page.
- So the ONLY way to see a result is the sleep path (which isn't drawing) or a mechanism that
  **persists through disconnect**. There is **no readback**: main forwards W 0C and replies
  `0c 01` without relaying the screen's a55a reply, so `status` returns nothing useful.
- Net: we cannot tell whether (a) cmd 0x50 reaches the screen, (b) the flash slot commits, or
  (c) the draw/refresh happens — because all three are downstream of a trigger that never fires.

## Open questions for Fable (RE is offline; images are in the repo)
1. **Real low-power path.** From the screen disassembly, what is the exact normal
   (disconnected) idle → backlight-off / MCU-sleep path? Why did hooking `0x1848` (v4) NOT
   draw, given `0xe79c`→`0x1848`? Candidate causes to rule in/out:
   - `0x1848` genuinely not called on that path (different backlight/power routine);
   - `ss_sleep_draw` gated off (`ST->threshold == 0`) because cmd 0x50 never reached the
     screen so THRESHOLD never set it, and cfg_load found nothing;
   - the LVGL refresh is deferred: `ss_build_page` calls `F_FULL_REFRESH(1)` = `0x4d9c(1)`
     which only SETS A FLAG (confirmed: `0x4d9c` is `ldr r1,[pc]; strb r0,[r1]; bx lr`); the
     actual e-paper update runs later in the main loop (`0x1858`), which may not run before
     the MCU sleeps → nothing rendered. **May need a synchronous refresh at draw time.**
2. **Readback to break the deadlock.** Design a reliable way to confirm slot commit on-device
   — preferably make **main relay the screen's a55a cmd-0x50 STATUS reply** back to the BLE
   notify (mirror the stock synchronous `W 02` screen-version query path), so
   `codec_screensaver status` returns the real slot bitmap. This is the single highest-value
   change — it turns blind guessing into data.
3. **Flash-slot writability.** Is `0x2e000..0x3a000` physically present and runtime-programmable
   (chip flash size; read-while-write on the bank our appended code executes from at `0x2d180`)?
   The offline emulator modeled all flash writes as succeeding, so this is untested.

## Deliverables wanted (bundle, proper + bugtested, offline build+validate only)
1. Screen v5 that draws the image at the REAL sleep-entry and forces the e-paper refresh so
   it renders before sleep; keep recovery path (`0x40/0x46/0x47/0x48`, OTA idiom `0x1c5e0`)
   byte-identical.
2. On-device readback (main relay of the STATUS reply, or a persist-through-disconnect sticky
   diagnostic) so commit can be verified.
3. **NiMH battery-gauge fix** (main fw): the % from char `2a19` is calibrated for alkalines and
   reads low for NiMH (the main flash gated at battery 51%). Recalibrate the voltage→level
   curve for rechargeables, or add an alkaline/NiMH toggle. Investigate where the battery
   level is produced in the main app.
4. Validated candidates via the existing `screensaver_firmware.py` / `check_screensaver.py`
   pattern; the author flashes interactively (screen recoverable; main no-rollback → careful).

## Key addresses / facts (all confirmed by disasm this session)
- State vars: **target `0x200003ec`**, **shown `0x200003f4`** (main loop `0xe65c`: shown:=target,
  dispatch(target) via `0x1ec5c`; redraw when target≠shown). Device pages are states `0x5b..0x5d`,
  `0x32`; screensaver claims unused state **`0x59`** (page table word @ `0x1edf8`, base `0x1ec94`).
- a55a command dispatch: `add pc` base `0x1c306`, cmd 0x50 entry @ `0x1c3a4` (was error stub `0x1c456`).
- Screen ROM (thumb, VA=file+0x1000, Cortex-M0+): payload `0x1d204`, len `0x1d218`, ack `0x1d392`,
  nak `0x1d378`, reply+ack `0x1d386`, clkdiv `0x13e8`, flash init `0x4edc`, unlock `0x1afbc`
  (→lock-set `0x4fc4`, per-64KB-bank reg via table `[1,4,5]`@`0x1afe4`, bit=`1<<((addr&0xffff)>>11)`),
  mode `0x4fe4` (1=write/2=erase), erase-sector `0x5034`, program+verify `0x50c0` (allows dest<256KB),
  img create `0x12d58`, img set_src `0x13860`, force-refresh-FLAG `0x4d9c`, page dispatcher `0x1ec5c`,
  refresh executor ~`0x1858`, full RAM write `0x19da`, framebuffer `0x20004c3c`, settings/LPTIM period
  byte `0x20000254` (valid 5..60 via `0xf974`).
- Backlight/power-off: `0x1848` = `0x51a0(0,2,3)`. `0x1848` callers: `0x5800` (in `0x5794`, a staged
  idle state-machine writing states 4/5/6 by elapsed-time thresholds incl `0x127`=295), `0xe80e`
  (in `0x79c`… actually func `0xe79c`, gated by `0xf964` period-valid + a flag + `(tick-last)≥0x127`),
  `0x1d5d6` (in `0x1d44c`, conditional). `0x51a0` also called from `0x63f2`, `0xd44c`, `0xd4d0`.
- Recovery/OTA (DO NOT TOUCH): cmd 0x40 special-cased at `0x1c2ce..0x1c2f2`; OTA idiom `0x1c5e0`
  (erase sector `0xe00`, write `55 aa` @ `0xe50`, ui_event 0x46, reset `0x684c`).
- Main (Cortex-M4, VA=file+0x80DE00): W-tbh table @ file `0xA9E4` (VA `0x8187E4`); W 0C entry @
  `0xA9FC` (was `0x0482` unused-marker) → bridge `0x82792c`; a55a sender `0x81a540`
  (`SEND(1,1,0xa55a,0x50,0,ptr,len)` builds `[a5][5a][cmd][sub][len16][payload]`); config reply
  path `0x819cb4`/`0x818382`; Realtek digest = SHA-256 of image[12:372]+[404:752]+[1008:] @ `image[372:404]`.
- ss_state RAM struct @ `0x20007f80` (above stack top `0x20007e58`; guarded by magic `SSRM`).
- Slots: `0x2e000 + i*0x2000` (i=0..5), config `0x3a000`; image blob 8072 B (8-byte palette + 21×384
  rows, MSB-first, 1=black), magic `SSAV`+len. Push framing all verified against firmware acceptance.

## Toolchain
`clang --target=arm-none-eabi -mthumb -fuse-ld=lld -nostdlib -Oz -ffreestanding -fno-builtin
-fno-unwind-tables -fno-asynchronous-unwind-tables -mcpu=cortex-m0plus` (screen) / `cortex-m4`
(main). Symbols via `llvm-nm`; disasm via capstone (see `/tmp/.../ofadis.py` pattern or rebuild).
The editor's `-mno-direct-extern-access` clangd warning is noise, not a real build flag.
