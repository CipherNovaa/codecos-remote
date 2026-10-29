# The official app's hidden Debug Mode, and dual-host bonding

## Debug Mode

The One For All companion app (`com.uei.ofa.advanced`, a Flutter app) contains a
developer menu: an API server switch (production / staging), app logging, a floating
log button and, most usefully, **Custom OTA**: flash the main or e-paper firmware from
the vendor's server *or from a local file*. That last option is the sanctioned way to
put a custom e-paper image on the remote without opening it.

**How it is enabled.** Read on a rooted Android tablet with `adb`: the gate is nothing
more than two booleans in the app's Flutter shared preferences
(`shared_prefs/FlutterSharedPreferences.xml`):

```
flutter.debugMode   = true   # shows Settings > "Advanced Mode Options"
flutter.debugLogOn  = true   # the floating LOG button
```

The decompiled class names (`DebugModeSettingViewController`, `debug_log_screen.dart`)
confirm the menu is real code, not a leftover.

**On a non-rooted phone there is no clean route.** Every door was checked and is shut:

| Route | Result |
|---|---|
| The implied unlock gesture (tap "App Version" 10×) | Not accepted on the release build |
| `adb run-as` to write the prefs | Release build, not debuggable |
| `adb backup` / restore | The app excludes its prefs from backup |
| A deep link or exported intent | The manifest has one launcher activity and no data scheme |
| Rooting the phone | Rejected: trips the phone's hardware integrity fuse and breaks banking apps |

The only remaining path is a **re-signed debuggable APK**: set
`android:debuggable="true"` on the base APK *and* the arm64 split, re-sign both with the
same key, install them together with `adb install-multiple`, then push the prefs file
in with `run-as`. (An earlier attempt that would not launch almost certainly re-signed
only the base: the Flutter engine and the app's compiled Dart live in the arm64 split.)
This is left undone; the rooted tablet works as the service console instead.

The tools `prepare_root_force_screen_patch.py`, `clone_force_screen_picker.py` and
`build_force_screen_picker_set.py` are the auditable APK-side experiments from this
work. They operate only on your own copy of the app and never ship any of it.

## Two computers on one remote

The remote supports one live Bluetooth HID link at a time but can hold more than one
bond. Getting a **second** host (a laptop) paired took a static reverse-engineering pass
on the main firmware (see `handoffs/FABLE-ANSWER-laptop-pairing-2026-09-21.md`):

- A slot's "provisioned" bit is a **receipt** of a completed bond, written only by the
  pairing-completion handler. No config opcode sets it.
- So the five firmware candidates that tried to force pairing on an unprovisioned slot
  (`laptop_pairing_v*_firmware.py`) patched the wrong gate. v5 was provably a no-op.
  They are kept as a record and must not be rebuilt.
- The fix needed **no firmware**: create the device in the official app as a Google TV
  box, choose "Pair with Bluetooth Device", select it on the remote and hold
  **Vol+ and OK** until the blue LEDs blink. A real bond sets the bit.

Afterwards the touchscreen pages and labels were copied onto the new slot with the
config-channel tools, and switching between the two hosts was tested both ways.
