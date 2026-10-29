"""Labels — list every touchscreen key with its current label, rename inline, apply.

Read the remote → one row per key (its current label read live from the device) → edit
the ones you want → Preview (local diff, no write) → Apply (each change through the
audited labels.py set path, journaled + readback-verified) → re-read to confirm.
"""
from datetime import datetime

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QSpinBox,
                             QLineEdit, QPlainTextEdit, QFrame, QScrollArea, QWidget)

from .base import Panel
from .. import constants as K
from ..engine import labels as lab
from ..engine.proc import ProcRunner


class _KeyRow(QWidget):
    def __init__(self, key, current):
        super().__init__()
        self.key = key
        self.current = current or ""
        lay = QHBoxLayout(self)
        lay.setContentsMargins(2, 2, 2, 2)
        lay.setSpacing(10)

        name = QLabel(key)
        name.setFixedWidth(80)
        name.setObjectName("subheading")
        lay.addWidget(name)

        now = QLabel(current if current else "(unlabeled)")
        now.setFixedWidth(160)
        now.setObjectName("dim" if current else "warn")
        lay.addWidget(now)

        arrow = QLabel("→")
        arrow.setObjectName("dim")
        lay.addWidget(arrow)

        self.edit = QLineEdit(current or "")
        self.edit.setMaxLength(K.LABEL_MAX_LEN)
        self.edit.setPlaceholderText(f"new label (1..{K.LABEL_MAX_LEN} ASCII)")
        lay.addWidget(self.edit, 1)

    def changed(self):
        return self.edit.text() != self.current

    def new_text(self):
        return self.edit.text()


class LabelsPanel(Panel):
    title = "Labels"
    needs_link = True

    def build(self):
        self.rows = []
        self.runner = ProcRunner(self)
        self.runner.line.connect(lambda ln: self.console.appendPlainText(ln))
        self.runner.finished.connect(self._on_proc_finished)
        self.runner.failed.connect(self._on_proc_failed)
        self._queue = []
        self._applying = False

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(10)

        head = QLabel("Touchscreen key labels")
        head.setObjectName("heading")
        root.addWidget(head)

        banner = QFrame()
        banner.setObjectName("card")
        bl = QVBoxLayout(banner)
        bl.setContentsMargins(12, 8, 12, 8)
        warn = QLabel("⚠ " + K.NO_SYNC_WARNING)
        warn.setObjectName("warn")
        warn.setWordWrap(True)
        bl.addWidget(warn)
        root.addWidget(banner)

        ctl = QHBoxLayout()
        ctl.addWidget(QLabel("device slot"))
        self.slot = QSpinBox()
        self.slot.setRange(1, 12)
        self.slot.setValue(1)
        ctl.addWidget(self.slot)
        note = QLabel("(1 = this desktop · 5 = laptop)")
        note.setObjectName("dim")
        ctl.addWidget(note)
        self.read_btn = QPushButton("Read labels from remote")
        self.read_btn.setObjectName("primary")
        self.read_btn.clicked.connect(self._read)
        ctl.addStretch()
        ctl.addWidget(self.read_btn)
        root.addLayout(ctl)

        # scrollable list of key rows
        self.list_holder = QWidget()
        self.list_lay = QVBoxLayout(self.list_holder)
        self.list_lay.setContentsMargins(0, 0, 0, 0)
        self.list_lay.setSpacing(4)
        self.placeholder = QLabel("Bring the config link up, then “Read labels from "
                                  "remote” to list every touchscreen key and its current label.")
        self.placeholder.setObjectName("dim")
        self.placeholder.setWordWrap(True)
        self.list_lay.addWidget(self.placeholder)
        self.list_lay.addStretch()
        scroll = QScrollArea()
        scroll.setWidget(self.list_holder)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        root.addWidget(scroll, 1)

        btns = QHBoxLayout()
        self.preview_btn = QPushButton("Preview changes")
        self.preview_btn.clicked.connect(self._preview)
        self.preview_btn.setEnabled(False)
        self.apply_btn = QPushButton("Apply changes (write + verify)")
        self.apply_btn.setObjectName("primary")
        self.apply_btn.clicked.connect(self._apply)
        self.apply_btn.setEnabled(False)
        btns.addWidget(self.preview_btn)
        btns.addWidget(self.apply_btn)
        btns.addStretch()
        root.addLayout(btns)

        self.console = QPlainTextEdit()
        self.console.setObjectName("console")
        self.console.setReadOnly(True)
        self.console.setMaximumHeight(140)
        root.addWidget(self.console)

    # ── read + list ──
    def _read(self):
        self.read_btn.setEnabled(False)
        self.status("reading labels from the remote…")
        self.run_ble(lambda: lab.read_all(self.slot.value()),
                     on_done=self._populate, on_error=self._read_failed,
                     busy="reading labels…")

    def _read_failed(self, e):
        self.read_btn.setEnabled(True)
        self.win._default_error(e)

    def _populate(self, entries):
        self.read_btn.setEnabled(True)
        # clear the list
        while self.list_lay.count():
            item = self.list_lay.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        self.rows = []
        for e in entries:
            cur = e.get("text")
            if e.get("error"):
                cur = None
            row = _KeyRow(e["key"], cur)
            self.rows.append(row)
            self.list_lay.addWidget(row)
        self.list_lay.addStretch()
        self.preview_btn.setEnabled(True)
        self.apply_btn.setEnabled(True)
        labelled = sum(1 for e in entries if e.get("text"))
        self.status(f"read {len(entries)} keys · {labelled} labelled", 6000)

    # ── preview + apply ──
    def _changes(self):
        out = []
        for r in self.rows:
            if r.changed():
                why = lab.valid_text(r.new_text())
                out.append((r.key, r.current, r.new_text(), why))
        return out

    def _preview(self):
        changes = self._changes()
        if not changes:
            self.console.setPlainText("No changes — every field matches the remote.")
            return
        lines = [f"PLAN — {sum(1 for c in changes if not c[3])} change(s) on slot {self.slot.value()}:"]
        for key, cur, new, why in changes:
            mark = f"  ⚠ {why}" if why else ""
            newdisp = repr(new) if new else "(clear label)"
            lines.append(f"  {key:8} {cur or '(unlabeled)'!r} → {newdisp}{mark}")
        self.console.setPlainText("\n".join(lines))

    def _apply(self):
        changes = [(k, c, n) for k, c, n, why in self._changes() if not why]
        invalid = [k for k, c, n, why in self._changes() if why]
        if invalid:
            self.status(f"⚠ fix invalid labels first: {', '.join(invalid)}", 6000)
            return
        if not changes:
            self.status("no valid changes to apply", 4000)
            return
        if self.runner.is_running():
            self.status("an apply is already running", 4000)
            return
        if not self.win.acquire_device("writing labels…"):     # hold the device for the batch
            return
        self._queue = changes
        self._applying = True
        self.console.clear()
        self.console.appendPlainText(f"APPLY — {len(changes)} change(s) on slot {self.slot.value()}\n")
        self.preview_btn.setEnabled(False)
        self.apply_btn.setEnabled(False)
        self.read_btn.setEnabled(False)
        self._apply_next()

    def _apply_next(self):
        if not self._queue:                       # queue drained → confirm by re-reading
            self._applying = False
            self.win.release_device()             # let the re-read take the lock
            self.console.appendPlainText("\nall changes applied — re-reading to confirm …")
            self.status("applied · re-reading", 5000)
            self.preview_btn.setEnabled(True)
            self.apply_btn.setEnabled(True)
            self._read()
            return
        key, _cur, new = self._queue.pop(0)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        clearing = (new == "")
        backup = K.JOURNAL_ROOT / f"labels-{'clear' if clearing else 'apply'}-{stamp}" / "backup.json"
        backup.parent.mkdir(parents=True, exist_ok=True)
        self.console.appendPlainText(f"\n→ {key} = {'(clear label)' if clearing else repr(new)}")
        argv = (lab.clear_argv(self.slot.value(), key, backup) if clearing
                else lab.apply_argv(self.slot.value(), key, new, backup))
        self.runner.run(argv, action=f"labels-{'clear' if clearing else 'apply'}-{key}")

    def _halt_apply(self, msg):
        """Stop the batch, release the lock, restore the UI — do NOT claim success."""
        self._applying = False
        self._queue = []
        self.win.release_device()
        self.preview_btn.setEnabled(True)
        self.apply_btn.setEnabled(True)
        self.read_btn.setEnabled(True)
        self.console.appendPlainText(f"\n✗ {msg}")
        self.status(f"⚠ {msg}", 9000)

    def _on_proc_finished(self, code, _out):
        if not self._applying:
            return
        if code != 0:                             # a write was rejected/failed — stop here
            self._halt_apply(f"a label write failed (exit {code}) — batch halted; "
                             "some keys may be unchanged. Re-read and retry.")
            return
        self._apply_next()

    def _on_proc_failed(self, err):
        if self._applying:
            self._halt_apply(f"could not run labels.py: {err}")
        else:
            self.status(f"⚠ {err}", 8000)
