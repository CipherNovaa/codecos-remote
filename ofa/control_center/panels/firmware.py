"""Firmware — main-chip flashing behind a mandatory validator gate + confirm.

Flow (each step gates the next):
  pick image → Validate (check_*.py must exit 0 AND PASS) → Plan (safe, no write)
  → Execute (type-to-confirm 'FLASH MAIN FIRMWARE', then flash.py --execute --log).
Main flashing has NO ROLLBACK and can brick the remote; recovery = re-flash the
baseline image. E-ink screen firmware is app-only (not handled here).
"""
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QComboBox,
                             QPlainTextEdit, QFrame, QProgressBar, QFileDialog,
                             QInputDialog, QMessageBox)

from .base import Panel
from .. import constants as K
from ..engine import firmware as fw
from ..engine.proc import ProcRunner


class FirmwarePanel(Panel):
    title = "Firmware"

    def build(self):
        self.image = None
        self.validator = None
        self._validated = None          # path that passed the gate
        self.runner = ProcRunner(self)
        self.runner.line.connect(lambda ln: self.console.appendPlainText(ln))
        self.runner.progress.connect(self._on_progress)
        self.runner.finished.connect(self._on_finished)
        self.runner.failed.connect(self._on_failed)
        self._phase = None

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        head = QLabel("Main-chip firmware")
        head.setObjectName("heading")
        root.addWidget(head)

        warn = QFrame()
        warn.setObjectName("card")
        wl = QVBoxLayout(warn)
        w = QLabel("⚠ Main flashing has NO ROLLBACK and can BRICK the remote.\n"
                   "• Battery ≥ 50% and use ALKALINES (a brownout mid-flash is fatal).\n"
                   "• Enter OTA by holding ACTIVITIES+DEVICES; an 'Unlikely Error' on "
                   "activation is EXPECTED, keep holding.\n"
                   f"• Recovery if it goes wrong: re-flash {Path(K.RECOVERY_MAIN).name}.")
        w.setObjectName("warn")
        w.setWordWrap(True)
        wl.addWidget(w)
        root.addWidget(warn)

        # image picker
        pick = QHBoxLayout()
        pick.addWidget(QLabel("image"))
        self.combo = QComboBox()
        self.candidates = fw.known_candidates()
        for label, path, _v in self.candidates:
            self.combo.addItem(label, path)
        self.combo.addItem("Browse…", "__browse__")
        self.combo.currentIndexChanged.connect(self._on_pick)
        pick.addWidget(self.combo, 1)
        root.addLayout(pick)

        self.kind = QLabel("")
        self.kind.setObjectName("dim")
        root.addWidget(self.kind)

        # gated step buttons
        steps = QHBoxLayout()
        self.validate_btn = QPushButton("1 · Validate")
        self.validate_btn.setObjectName("primary")
        self.validate_btn.clicked.connect(self._validate)
        self.plan_btn = QPushButton("2 · Plan (safe)")
        self.plan_btn.clicked.connect(self._plan)
        self.exec_btn = QPushButton("3 · Execute flash…")
        self.exec_btn.setObjectName("danger")
        self.exec_btn.clicked.connect(self._execute)
        self.exec_btn.setEnabled(False)
        steps.addWidget(self.validate_btn)
        steps.addWidget(self.plan_btn)
        steps.addWidget(self.exec_btn)
        steps.addStretch()
        root.addLayout(steps)

        self.gate = QLabel("gate: validator has not run")
        self.gate.setObjectName("dim")
        root.addWidget(self.gate)

        self.bar = QProgressBar()
        self.bar.setVisible(False)
        root.addWidget(self.bar)

        self.console = QPlainTextEdit()
        self.console.setObjectName("console")
        self.console.setReadOnly(True)
        root.addWidget(self.console, 1)

        eink = QLabel("E-ink screen firmware is flashed only through the OFA phone app "
                      "(app-only). This panel flashes the MAIN chip.")
        eink.setObjectName("dim")
        eink.setWordWrap(True)
        root.addWidget(eink)

        self._sync_selection()

    # ── selection ──
    def _on_pick(self, _i):
        data = self.combo.currentData()
        if data == "__browse__":
            path, _ = QFileDialog.getOpenFileName(self, "Choose a firmware image",
                                                  str(K.ARTIFACTS), "Firmware (*.bin)")
            if not path:
                self.combo.setCurrentIndex(0)
                return
            # add + select it
            self.combo.insertItem(self.combo.count() - 1, Path(path).name, path)
            self.combo.setCurrentIndex(self.combo.count() - 2)
            return
        self._sync_selection()

    def _sync_selection(self):
        data = self.combo.currentData()
        if not data or data == "__browse__":
            return
        self.image = data
        self.validator = fw.suggest_validator(data)
        known = fw.known_image_name(data)
        if self.validator:
            how = f"validator: {self.validator}"
        elif known:
            how = "gate: pinned-SHA match (known reference image)"
        else:
            how = "gate: no validator for this image — Execute stays locked"
        self.kind.setText(f"{fw.candidate_kind(data)}   ·   {how}")
        # any change invalidates a prior PASS
        self._validated = None
        self.exec_btn.setEnabled(False)
        self.gate.setText("gate: not verified for this image")
        self.gate.setStyleSheet("")

    # ── steps ──
    def _busy(self, on):
        for b in (self.validate_btn, self.plan_btn, self.exec_btn):
            b.setEnabled(False if on else True)
        if not on:
            self.exec_btn.setEnabled(self._validated == self.image)

    def _validate(self):
        if not self.image:
            return
        self.console.clear()
        if self.validator:                                # emulation validator (subprocess)
            self._phase = "validate"
            self.console.appendPlainText(f"VALIDATE {Path(self.image).name} via {self.validator}\n")
            self._busy(True)
            self.runner.run(fw.validator_argv(self.validator, self.image),
                            action=f"validate-{Path(self.image).stem}")
            return
        # no single-file validator → pinned-SHA identity gate (in-process, instant)
        known = fw.known_image_name(self.image)
        sha = fw.image_sha(self.image)
        self.console.appendPlainText(f"SHA-256 {sha}\n")
        if known:
            self._validated = self.image
            self.gate.setText(f"gate: ✓ pinned-SHA match — {known} — Execute unlocked")
            self.gate.setStyleSheet("color:#a6e3a1;")
            self.exec_btn.setEnabled(True)
            self.status("known reference image (SHA verified) — Execute unlocked", 6000)
        else:
            self._validated = None
            self.gate.setText("gate: ✗ unknown image, no validator — Execute locked "
                              "(verify manually / flash via CLI)")
            self.gate.setStyleSheet("color:#f38ba8;")
            self.exec_btn.setEnabled(False)
            self.status("unknown image — no gate can pass it here", 7000)

    def _plan(self):
        if not self.image:
            return
        self._phase = "plan"
        self.console.clear()
        self.console.appendPlainText(f"PLAN (safe, no write) {Path(self.image).name}\n")
        self._busy(True)
        self.runner.run(fw.plan_argv(self.image),
                        action=f"plan-{Path(self.image).stem}")

    def _execute(self):
        if self._validated != self.image:
            self.status("validator must PASS for this image first", 6000)
            return
        phrase = fw.confirm_phrase()
        text, ok = QInputDialog.getText(
            self, "Confirm destructive flash",
            f"This will FLASH THE MAIN CHIP — no rollback, can brick.\n"
            f"Type exactly:  {phrase}")
        if not ok or text != phrase:
            self.status("flash cancelled (phrase not matched)", 5000)
            return
        if QMessageBox.warning(self, "Last chance",
                               f"Flash {Path(self.image).name} now?",
                               QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                               QMessageBox.StandardButton.Cancel) != QMessageBox.StandardButton.Yes:
            return
        # take the device lock + block every other tab for the whole no-rollback flash
        if not self.win.acquire_device("FLASHING — do not touch"):
            self.status("device busy — cannot flash right now", 6000)
            return
        self.win.set_flashing(True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        logdir = K.JOURNAL_ROOT / f"flash-{stamp}"
        logdir.mkdir(parents=True, exist_ok=True)
        logpath = logdir / "flash.log"           # fresh path (flash.py open('x'))
        self._phase = "execute"
        self.console.appendPlainText(f"\nEXECUTE flash → log {logpath}\n")
        self.bar.setVisible(True)
        self.bar.setRange(0, 0)
        self._busy(True)
        self.runner.run(fw.execute_argv(self.image, logpath),
                        action=f"flash-{Path(self.image).stem}", parse_json=True)

    def _on_progress(self, sent, total):
        if total and self.bar.maximum() != total:
            self.bar.setRange(0, total)
        self.bar.setValue(sent)

    def _on_finished(self, code, out):
        self.bar.setVisible(False)
        if self._phase == "validate":
            if fw.is_pass(code, out):
                self._validated = self.image
                self.gate.setText("gate: ✓ PASS — Execute unlocked")
                self.gate.setStyleSheet("color:#a6e3a1;")
                self.status("validator PASS ✓ — Execute unlocked", 6000)
            else:
                self._validated = None
                self.gate.setText(f"gate: ✗ validator did not PASS (exit {code}) — Execute stays locked")
                self.gate.setStyleSheet("color:#f38ba8;")
                self.status("validator did NOT pass — flash locked", 7000)
        elif self._phase == "execute":
            self._end_flash()
            self._validated = None               # a second flash must re-validate (audit L1)
            self.exec_btn.setEnabled(False)
            self.gate.setText("gate: re-validate before another flash")
            self.gate.setStyleSheet("")
            self.status("flash finished — verify the remote booted" if code == 0
                        else f"flash exited {code} — check the log", 12000)
        self._busy(False)

    def _on_failed(self, err):
        # QProcess FailedToStart emits `failed`, NOT `finished` — recover the UI here so
        # the panel never wedges with buttons disabled (audit M1).
        self.bar.setVisible(False)
        if self._phase == "execute":
            self._end_flash()
        self._busy(False)
        self.status(f"⚠ {err}", 9000)

    def _end_flash(self):
        self.win.set_flashing(False)
        self.win.release_device()
