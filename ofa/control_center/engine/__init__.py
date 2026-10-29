"""Engine adapters — thin async/subprocess wrappers over the verified CLI modules.

Nothing here reimplements device logic; each module imports the real engine
(``codec_screensaver``, ``codec_led``, ``codec_amp``, ``labels``, ``flash``,
``screensaver_prep``) and re-drives its primitives with progress callbacks so the
GUI can await them on the qasync loop (BLE) or stream them through QProcess (flash,
validators, loggers, labels).
"""
