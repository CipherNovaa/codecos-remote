"""Logs / Verify — one combined live watcher (Start/Stop), device reads, journals.

The live watcher (keypress + RSSI in one line) runs as root via pkexec; Stop kills it
by name with a second pkexec (a user cannot signal a root process). Starting any tool
while one runs auto-stops the first, so switching never wedges. BLE reads are
short-lived and root-free. No timer — the watcher runs until you press Stop.
"""
from datetime import datetime

from PyQt6.QtWidgets import (QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
                             QPlainTextEdit, QListWidget, QGroupBox)

from .base import Panel
from .. import constants as K
from ..engine import logs as lg
from ..engine.proc import ProcRunner


class LogsPanel(Panel):
    title = "Logs / Verify"

    def build(self):
        self.runner = ProcRunner(self)
        self.runner.line.connect(lambda ln: self.console.appendPlainText(ln))
        self.runner.finished.connect(self._on_finished)
        self.runner.failed.connect(self._on_failed)
        self.stopper = ProcRunner(self)          # separate root pkill for stopping
        self.stopper.finished.connect(lambda *_: None)
        self._readconfig_out = None
        self._pending = None                     # (argv, action, marker) to launch after stop
        self._marker = None                      # pkill marker of the running root logger

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        head = QLabel("Loggers & verification")
        head.setObjectName("heading")
        root.addWidget(head)

        wgrp = QGroupBox("Live watch (root — pkexec asks for your password)")
        wl = QVBoxLayout(wgrp)
        row = QHBoxLayout()
        self.watch_btn = QPushButton("Watch keys + RSSI")
        self.watch_btn.setObjectName("primary")
        self.watch_btn.clicked.connect(self._watch)
        self.capture_btn = QPushButton("Capture keys to file")
        self.capture_btn.clicked.connect(self._capture)
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.setObjectName("danger")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self._stop)
        row.addWidget(self.watch_btn)
        row.addWidget(self.capture_btn)
        row.addStretch()
        row.addWidget(self.stop_btn)
        wl.addLayout(row)
        note = QLabel("Each keypress logs its key + the link RSSI. Runs until Stop. The "
                      "watcher grabs the OFA input while running (keys won't reach the "
                      "desktop) — that's normal. Stop kills it as root (may re-ask the "
                      "pkexec password).")
        note.setObjectName("dim")
        note.setWordWrap(True)
        wl.addWidget(note)
        root.addWidget(wgrp)

        rgrp = QGroupBox("Device reads (no root)")
        rrow = QHBoxLayout(rgrp)
        self.dev_btn = QPushButton("Read device list")
        self.dev_btn.clicked.connect(self._read_devices)
        self.page_btn = QPushButton("Read page keys")
        self.page_btn.clicked.connect(lambda: self._start(lg.ofaread_argv(), "pagekeys"))
        rrow.addWidget(self.dev_btn)
        rrow.addWidget(self.page_btn)
        rrow.addStretch()
        root.addWidget(rgrp)

        self.console = QPlainTextEdit()
        self.console.setObjectName("console")
        self.console.setReadOnly(True)
        root.addWidget(self.console, 2)

        jgrp = QGroupBox("Run journals (artifacts/control-center/)")
        jl = QVBoxLayout(jgrp)
        jrow = QHBoxLayout()
        jrefresh = QPushButton("Refresh list")
        jrefresh.clicked.connect(self._load_journals)
        jrow.addWidget(jrefresh)
        jrow.addStretch()
        jl.addLayout(jrow)
        self.jlist = QListWidget()
        self.jlist.itemDoubleClicked.connect(self._open_journal)
        jl.addWidget(self.jlist)
        root.addWidget(jgrp, 1)

        self._load_journals()

    # ── button handlers ──
    def _starts(self):
        return (self.watch_btn, self.capture_btn, self.dev_btn, self.page_btn)

    def _set_starts(self, on):
        for b in self._starts():
            b.setEnabled(on)

    def _watch(self):
        self._start(lg.watch_argv(), "watch-keys-rssi", marker=lg.WATCH_MARKER)

    def _capture(self):
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        out = K.JOURNAL_ROOT / f"capture-{stamp}" / "keys.log"
        out.parent.mkdir(parents=True, exist_ok=True)
        self.console.appendPlainText(f"(capturing to {out})")
        self._start(lg.capture_argv(out), "capture-keys", marker=lg.CAPTURE_MARKER)

    def _read_devices(self):
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        self._readconfig_out = K.JOURNAL_ROOT / f"devlist-{stamp}.json"
        self._readconfig_out.parent.mkdir(parents=True, exist_ok=True)
        self._start(lg.readconfig_argv(self._readconfig_out), "devlist")

    # ── process control: auto-switch + reliable stop ──
    def _start(self, argv, action, marker=None):
        if self.runner.is_running():
            self._pending = (argv, action, marker)   # switch: stop current, then launch
            self._stop()
            self.status(f"switching to {action}…", 3000)
            return
        self._launch(argv, action, marker)

    def _launch(self, argv, action, marker):
        if not self.win.acquire_device(action):        # one device at a time (blocks during a flash)
            return
        self._marker = marker
        self.console.appendPlainText(f"\n$ {action}\n")
        self.runner.run(argv, action)
        self.stop_btn.setEnabled(True)
        self._set_starts(False)
        self.status(f"{action} running…")

    def _stop(self):
        self.stop_btn.setEnabled(False)
        if self._marker:                              # root logger → root kills it by name
            self.console.appendPlainText(f"(stopping root logger: pkexec pkill {self._marker})")
            self.stopper.run(lg.stop_argv(self._marker), f"stop-{self._marker}")
        else:                                         # user process → direct kill works
            self.runner.kill()
        self.status("stopping…", 3000)

    def _on_finished(self, code, _out):
        self._marker = None
        self.win.release_device()
        self.stop_btn.setEnabled(False)
        self._set_starts(True)
        if self._readconfig_out and self._readconfig_out.exists():
            try:
                self.console.appendPlainText("\n--- device list ---\n"
                                             + self._readconfig_out.read_text())
            except OSError:
                pass
            self._readconfig_out = None
        self._load_journals()
        if self._pending:
            argv, action, marker = self._pending
            self._pending = None
            self._launch(argv, action, marker)
        else:
            self.status(f"done (exit {code})", 5000)

    def _on_failed(self, err):
        self._marker = None
        self.win.release_device()
        self.stop_btn.setEnabled(False)
        self._set_starts(True)
        self.status(f"⚠ {err}", 8000)

    # ── journals ──
    def _load_journals(self):
        self.jlist.clear()
        for name, _path in lg.list_journals():
            self.jlist.addItem(name)

    def _open_journal(self, item):
        for name, path in lg.list_journals():
            if name == item.text():
                self.console.setPlainText(f"# {name}\n\n" + lg.read_journal(path))
                return
