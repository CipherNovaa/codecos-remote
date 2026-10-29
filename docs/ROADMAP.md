# Roadmap

The goal: one remote that controls every IR or Bluetooth device in the room with all
its buttons working, runs custom firmware, and drives the paired computers and their
software directly, so nothing needs a keyboard from the sofa.

## Done

- All 24 physical buttons over Bluetooth HID (stock: 9 to 12, depending on profile).
- Touchscreen keys that send HID, with custom e-paper labels, 6 named + 10 spare.
- Tap-vs-hold on every touchscreen key (two-MCU firmware), which roughly doubles the
  touchscreen's command surface; the hold policy now lives entirely on the PC side.
- Channel up/down fixed, Pause → Play/Pause, the blue key-press light bar switched off.
- An e-paper multi-image screensaver (6 slots, random or sequential) managed from the PC.
- A custom pixel font on the e-paper screen (built from any TTF by `screen_fonts.py`).
- Two paired computers on one remote.
- Amplifier and TV IR fired on command over Bluetooth (`integrations/codec`).
- A desktop app (the Control Center) over all of it.

## Next

1. **Investigate the TV's own Bluetooth remote channel.** If it exposes every button and
   reaches further than IR, move TV control off IR.
2. **One-button sleep while casting**: stop playback, amp off, TV off, monitors off.
3. **Hold-to-cast**: power the amp and TV to the right inputs and resume the last thing
   watched on the TV. Includes a spike on doing it with only the hub computer on.
4. **Global display toggles** (CRT ↔ HD, blue-light filter with hold-to-adjust), working
   even when the media player is closed.
5. **Activities as scenes** on the remote's ACTIVITIES key: an emulator scene, a music
   scene, games.
6. **More devices** on the remote's own Devices screen (game consoles), patching only
   their dead buttons.
7. **Voice search.** The remote has a microphone and Android TV Voice (ATVV)
   characteristics; capturing the audio stream over BLE and running speech-to-text on
   the PC is software only, no firmware. An early `GET_CAPS` got no reply, so the
   remote likely waits for a proper voice-session handshake first.
8. **Now playing on the e-paper** (stretch). The screensaver work already built a
   direct framebuffer blit and partial-window primitives; battery cost and e-ink refresh
   are the open questions.

## Known limits

- **16 custom touchscreen labels in total.** The e-paper firmware only renders custom
  text for logical ids `0xF0`-`0xFF`; 6 are the named keys, 10 the spares. More needs a
  screen-firmware change.
- **About 5 list buttons per touchscreen page** render; a 6th falls off the bottom
  (`rebalance_spare_pages.py` re-splits the spare pages).
- **No main-firmware rollback.** A bad main image can brick the remote. See the safety
  section in the README.
- **Never let the official app update firmware or "sync"**: it overwrites the custom
  main and screen images, and its device database has diverged from the remote's.
