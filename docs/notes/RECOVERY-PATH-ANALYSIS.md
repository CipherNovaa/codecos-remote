# Screen-recovery path — analysis for Codex (2026-09-21)

Written by Claude (with the author's PC + spare token budget) to save you re-deriving this.
**No flashing was done and none is implied here — this is analysis only.** the author will
not open the remote, so everything below is a SOFTWARE/OTA (over-BLE) path.

## TL;DR — the recovery path already exists; stop the blutter dig

The OFA app (`libapp.so`) has THREE OTA flows, two of which are recovery paths that
need **no case-opening** (strings pulled from the AOT snapshot):

- **`startForceScreenOTA` / `forceScreenOTA:`** — re-push the screen firmware over BLE
  **bypassing the version gate** (re-flash even if the on-device version matches).
- **`OtaRecoveryExtension` → "Recovery EInk Display"** (`package:oneforall_remote/.../ota_recovery.dart`,
  `getOtaRecoveryList`/`otaRecoveryList`/`isEInkOta`/`needUpgradeEInk`,
  `eInkRemoteFirmwareUpgradeFileMap`) — a dedicated E-Ink **recovery** flow, with recovery
  images keyed to version gaps: *"this recovery for Eink 3.12 -> 4.0"* and
  *"this recovery for Eink <5.0 for multiple bluetooth remote"*.

You were building blutter+DartSDK to find out *whether* a recovery route exists. It does
— it's named "Recovery EInk Display". **You don't need the full Flutter decompile for
that question.** The narrow thing still worth tracing is cheap (below).

## The reframe that makes the font flash safe

The brick fear assumes "a bad font flash kills the screen." It (almost) can't, because:

- `screen_fonts.py` V2 changes **glyph bitmap bytes + the 91-entry wrapping-width table +
  one width immediate** — it does **not** touch the vector table, entry point, or control
  flow. The result is the *same bootable app* with different font DATA.
- A bootable app with ugly glyphs **still boots and still speaks the BLE bridge** → you can
  always Force-Screen-OTA / Recovery back to `v013f`. Worst realistic case = wrong-looking
  text for one re-flash, **not** a dead screen.

The only ways to actually hard-brick are: (a) the flashed image fails the on-device
integrity check so the app won't boot, or (b) a partial/corrupt transport write. Both are
addressable offline + by the atomic update path — see the safe plan.

## Safe plan (the author-executable, no case-opening, brick risk ≈ 0)

1. **Prove the recovery mechanism FIRST, with known-good images (zero risk).**
   Use `startForceScreenOTA` to re-flash the *current* `v013f` (and/or a prior bundled
   `ofa_touch_rt150t_0002t_v013e.bin`) onto the screen, and confirm it re-flashes + boots.
   Every image here is a known-good bootable one, so this cannot brick — it just proves the
   force-OTA re-flash path works round-trip. THIS is your recovery drill.
2. **Confirm the screen update is validate-before-activate (atomic).** You already proved
   this for the MAIN image (`check_flash.py`: partial/rejected/disconnect → no activation).
   Confirm the screen path's `46 start / 48 seq+data / 47 exit` does the same — i.e. the
   bootloader only swaps the active app after a full, verified transfer (the `48` sequence
   numbers already let it detect a missing chunk). If atomic, a bad image is *rejected*,
   old app survives — no brick.
3. **Make the font image pass the on-device integrity check.** `check_screen_fonts.py`
   currently validates the LVGL font callbacks + ARM wrapping, but NOT an image-level
   CRC/length/header. Find what the screen bootloader/update validates (length? a CRC in
   the image header? the same encrypted-image scheme as the main path?) and recompute it in
   the V2 candidate so the image is *accepted*. This is the single most important offline
   check before any real flash.
4. **Then flash the font V2.** If the glyphs look wrong or anything's off, Force-Screen-OTA
   back to `v013f` (proven in step 1).

## The one narrow thing left to trace (cheap — no blutter)

Determine whether Force-Screen-OTA / OtaRecovery can reach a screen whose **app doesn't
boot at all** (the true-brick case), or only a running-but-mismatched one. That's answered
by the FIRMWARE, which you already have disassembled — not the phone app:

- Trace what BLE command the app's `startForceScreenOTA` / `OtaRecoveryExtension` sends,
  then follow it in the RTL8762C disasm (`ofa-thumb.txt`) to the screen-update handler.
- Key question: does the main MCU set the screen boot flag (`55aa` @ `0xE50`, erase `0xE00`)
  and reset the screen **itself over UART**, or does it rely on the SCREEN APP's handler
  (`1c5e0`, cmd `40`) to do it? If the main MCU can drive the flag+reset directly, recovery
  works even on a non-booting screen app. If only the screen app can, then recovery needs
  the app to at least boot — which the reframe guarantees for a valid font-swap.

Either way, with the reframe (a valid font-swap stays bootable) + a proven force-OTA
round-trip + a validated image, the font flash is safe to attempt without opening the case.

## Hard limits (context, not blockers)

- The screen **bootloader binary (0x0000–0x0fff) is unobtainable in software** — it's not
  in any APK asset (all `rt150t_*.bin` are app-region, VA ≥ 0x1000) and there's no
  arbitrary screen-flash READ over the bridge. So its exact "invalid-app" behavior can't be
  read directly — which is *why* we lean on the reframe + the app's own recovery flow
  instead of trying to reason about the bootloader.
- **NEVER write the 0x0000–0x0fff bootloader region** (only the app region + the update
  flag the bootloader itself manages). If that code were ever corrupted, the only recovery
  is SWD/`BOOT`-pin ISP on the HC32L186 = opening the case = out. Staying in the app region
  keeps you permanently in software-recoverable territory.

## Sources
App strings from `~/.cache/ofa/split/lib/arm64-v8a/libapp.so`; firmware handlers from your
`SCREEN-RECOVERY.md`; HC32L186 register map from `ofa/artifacts/tmp-rescue-20260921/ofa-hc32/`.
