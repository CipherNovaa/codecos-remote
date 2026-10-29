# `ofa/artifacts/`: your local workspace

Nothing in this folder is committed except this file. It holds the inputs only you
can supply and everything the tools produce:

| Path | What goes here | Who puts it there |
|---|---|---|
| `apk/` | Your own copy of the official One For All app (base APK + `split_config.arm64_v8a.apk`), unzipped to `apk/x/` | You, pulled from your phone with `adb` |
| `ble24/stock-0.5.7.bin` | The stock **main** firmware 0.5.7 (`assets/ofa_touch_0.5.7.bin` inside the app) | You, copied out of `apk/x/` |
| `apk/x/assets/flutter_assets/assets/bin/ofa_touch_rt150t_0002t_v013f.bin` | The stock **e-paper screen** firmware v013f | Already inside the unzipped app |
| `venv/` | The Python environment the wrappers prefer | `python3 -m venv ofa/artifacts/venv` |
| `<patch-name>/` | Built candidates (`*-DO-NOT-FLASH.bin`) + `manifest.json` per builder | The builders |
| `control-desk/`, `control-center/` | Timestamped journals of every live operation | `ofa_control.py`, the Control Center |

## Why the firmware is not in the repo

The stock images are the vendor's copyrighted firmware, and the OTA transfer key lives
in the vendor's `libRtkAesJni.so`. This project ships **patch scripts only**: like an
emulator that needs your own BIOS dump, every builder reads *your* stock image,
checks its SHA-256 against the version it was written for (main `0.5.7`, screen
`v013f`), and refuses anything else. `dfu.py` reads the transfer key out of your own
copy of the app's library at run time; it is never printed or stored.

## Getting the inputs

```sh
adb shell pm path com.uei.ofa.advanced          # lists base.apk + split_config.arm64_v8a.apk
adb pull <each path> ofa/artifacts/apk/
cd ofa/artifacts/apk && mkdir x && cd x && unzip ../base.apk && unzip -o ../split_config.arm64_v8a.apk
mkdir -p ../../ble24 && cp assets/ofa_touch_0.5.7.bin ../../ble24/stock-0.5.7.bin
```

If your app ships a different main firmware version than 0.5.7, or a screen image
other than v013f, the builders will refuse it. That is the intended behaviour: every
offset in this project was verified against those exact images.
