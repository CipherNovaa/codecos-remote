"""Screensaver — the flagship: preview an image, pick a render, push it to a slot.

Drop/browse an image → live 1-bit preview that follows mode/method/invert (Compare 4
opens the four variants to click) → Assign to the selected slot (BEGIN/DATA*/COMMIT
with a live progress bar and verify) → the 6-slot grid shows what is in each slot and
its device state. Cycle mode, wake-threshold, show and clear round it out. All slot
bookkeeping shares codec_screensaver's lineup.json with the CLI.
"""
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton,
                             QComboBox, QCheckBox, QSpinBox, QProgressBar, QPlainTextEdit,
                             QGroupBox, QRadioButton, QButtonGroup, QDialog, QWidget,
                             QScrollArea, QFrame)

from .base import Panel
from .. import constants as K
from ..widgets.dropzone import DropZone
from ..widgets.preview import EpaperPreview
from ..widgets.slot_grid import SlotGrid
from ..engine import render
from ..engine import screensaver as sv


class _CompareDialog(QDialog):
    """Four clickable variants (cover/contain × threshold/dither). Sets .picked."""
    def __init__(self, parent, tiles):
        super().__init__(parent)
        self.setWindowTitle("Compare variants — click one to use it")
        self.picked = None
        lay = QGridLayout(self)
        for i, (mode, method, png) in enumerate(tiles):
            box = QWidget()
            v = QVBoxLayout(box)
            prev = EpaperPreview(f"{mode} / {method}", width=130)
            prev.set_png(png)
            btn = QPushButton(f"Use {mode}/{method}")
            btn.clicked.connect(lambda _c, m=mode, me=method: self._choose(m, me))
            v.addWidget(prev)
            v.addWidget(btn)
            lay.addWidget(box, 0, i)

    def _choose(self, mode, method):
        self.picked = (mode, method)
        self.accept()


class ScreensaverPanel(Panel):
    title = "Screensaver"
    needs_link = True

    def build(self):
        self.source = None
        root = QHBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(16)

        # ── left: the slot grid + slot-level actions ──
        left = QVBoxLayout()
        left.setSpacing(10)
        h = QLabel("Slots")
        h.setObjectName("heading")
        left.addWidget(h)

        self.grid = SlotGrid()
        self.grid.slot_selected.connect(self._on_slot_selected)
        left.addWidget(self.grid)

        refresh = QPushButton("Refresh from device")
        refresh.clicked.connect(self.win.refresh_link)
        left.addWidget(refresh)

        # cycle mode
        cyc = QGroupBox("Rotation")
        cv = QHBoxLayout(cyc)
        self.cycle_group = QButtonGroup(self)
        self.rb_random = QRadioButton("random")
        self.rb_seq = QRadioButton("sequential")
        self.rb_random.setChecked(True)
        self.cycle_group.addButton(self.rb_random)
        self.cycle_group.addButton(self.rb_seq)
        apply_cyc = QPushButton("Apply")
        apply_cyc.clicked.connect(self._apply_cycle)
        cv.addWidget(self.rb_random)
        cv.addWidget(self.rb_seq)
        cv.addStretch()
        cv.addWidget(apply_cyc)
        left.addWidget(cyc)

        # threshold / wake
        thr = QGroupBox("Wake threshold (seconds; 0 = disabled)")
        tv = QHBoxLayout(thr)
        self.thr_spin = QSpinBox()
        self.thr_spin.setRange(0, 3600)
        self.thr_spin.setValue(0)
        set_thr = QPushButton("Set")
        set_thr.clicked.connect(self._set_threshold)
        tv.addWidget(self.thr_spin)
        tv.addStretch()
        tv.addWidget(set_thr)
        left.addWidget(thr)

        # show / clear
        act = QHBoxLayout()
        show_btn = QPushButton("Show selected")
        show_btn.clicked.connect(self._show_selected)
        clr_btn = QPushButton("Clear selected")
        clr_btn.setObjectName("danger")
        clr_btn.clicked.connect(self._clear_selected)
        clr_all = QPushButton("Clear ALL")
        clr_all.setObjectName("danger")
        clr_all.clicked.connect(self._clear_all)
        act.addWidget(show_btn)
        act.addWidget(clr_btn)
        act.addWidget(clr_all)
        left.addLayout(act)
        left.addStretch()
        # wrap the left column so the larger slot thumbnails never push the
        # rotation/threshold/show-clear controls off the bottom on a short window
        left_holder = QWidget()
        left_holder.setLayout(left)
        left_scroll = QScrollArea()
        left_scroll.setWidget(left_holder)
        left_scroll.setWidgetResizable(True)
        left_scroll.setFrameShape(QFrame.Shape.NoFrame)
        root.addWidget(left_scroll, 3)

        # ── right: image → render → assign ──
        right = QVBoxLayout()
        right.setSpacing(10)
        h2 = QLabel("New image")
        h2.setObjectName("heading")
        right.addWidget(h2)

        self.drop = DropZone()
        self.drop.file_chosen.connect(self._on_file)
        right.addWidget(self.drop)

        mid = QHBoxLayout()
        self.preview = EpaperPreview("Preview", width=150)
        mid.addWidget(self.preview, 0, Qt.AlignmentFlag.AlignTop)

        opts = QVBoxLayout()
        opts.setSpacing(8)
        self.mode = QComboBox()
        self.mode.addItems(K.RENDER_MODES)
        self.method = QComboBox()
        self.method.addItems(K.RENDER_METHODS)
        self.invert = QCheckBox("invert (white ink on black)")
        for w in (self.mode, self.method):
            w.currentIndexChanged.connect(self._rerender)
        self.invert.stateChanged.connect(self._rerender)
        opts.addWidget(QLabel("fit mode"))
        opts.addWidget(self.mode)
        opts.addWidget(QLabel("1-bit method"))
        opts.addWidget(self.method)
        opts.addWidget(self.invert)
        cmp_btn = QPushButton("Compare 4 variants")
        cmp_btn.clicked.connect(self._compare)
        opts.addWidget(cmp_btn)
        opts.addStretch()
        mid.addLayout(opts, 1)
        right.addLayout(mid)

        self.assign_btn = QPushButton("Assign to slot 0")
        self.assign_btn.setObjectName("primary")
        self.assign_btn.setEnabled(False)
        self.assign_btn.clicked.connect(self._assign)
        right.addWidget(self.assign_btn)

        self.bar = QProgressBar()
        self.bar.setVisible(False)
        right.addWidget(self.bar)

        self.log = QPlainTextEdit()
        self.log.setObjectName("console")
        self.log.setReadOnly(True)
        self.log.setPlaceholderText("Push progress + verify output appears here.")
        self.log.setMaximumHeight(150)
        right.addWidget(self.log)
        root.addLayout(right, 4)

        self._load_cycle_from_lineup()
        self._refresh_grid_records(None)

    # ── link + grid ──
    def on_link(self, info):
        present = info["slots"] if info else None
        self._refresh_grid_records(present)

    def _refresh_grid_records(self, present):
        lu = sv.load_lineup()
        self.grid.update_records(lu, present)
        # render thumbnails for recorded slots (CPU, off-thread)
        for i in range(K.NSLOTS):
            e = lu.get("slots", {}).get(str(i))
            if e and Path(e["path"]).exists():
                self._render_thumb(i, e)

    def _render_thumb(self, slot, entry):
        def done(png, s=slot):
            pix = QPixmap()
            if pix.loadFromData(png):
                self.grid.tiles[s].set_thumb(pix)
        self.run_async(render.render_png(entry["path"], entry.get("mode", "cover"),
                                         entry.get("method", "threshold"),
                                         entry.get("invert", False)), on_done=done)

    def _load_cycle_from_lineup(self):
        cyc = sv.load_lineup().get("cycle", "random")
        (self.rb_seq if cyc == "sequential" else self.rb_random).setChecked(True)

    def _on_slot_selected(self, slot):
        self.assign_btn.setText(f"Assign to slot {slot}")

    # ── image workflow ──
    def _on_file(self, path):
        self.source = path
        self.drop.setText(Path(path).name)
        self.assign_btn.setEnabled(True)
        self._rerender()

    def _rerender(self):
        if not self.source:
            return
        m, me, inv = self.mode.currentText(), self.method.currentText(), self.invert.isChecked()
        self.run_async(render.render_png(self.source, m, me, inv, upscale=1),
                       on_done=lambda png: self.preview.set_png(png, f"{m} / {me}"))

    def _compare(self):
        if not self.source:
            self.status("drop an image first")
            return
        combos = [("cover", "threshold"), ("cover", "dither"),
                  ("contain", "threshold"), ("contain", "dither")]

        async def gather():
            out = []
            for m, me in combos:
                png = await render.render_png(self.source, m, me, False, upscale=1)
                out.append((m, me, png))
            return out
        self.run_async(gather(), on_done=self._show_compare)

    def _show_compare(self, tiles):
        dlg = _CompareDialog(self, tiles)
        if dlg.exec() and dlg.picked:
            m, me = dlg.picked
            self.mode.setCurrentText(m)
            self.method.setCurrentText(me)
            self._rerender()

    def _assign(self):
        if not self.source:
            return
        slot = self.grid.selected
        m, me, inv = self.mode.currentText(), self.method.currentText(), self.invert.isChecked()
        self.log.clear()
        self.bar.setVisible(True)
        self.bar.setRange(0, 0)          # indeterminate until first frame count arrives
        self.assign_btn.setEnabled(False)

        def prog(done, total):
            if self.bar.maximum() != total:
                self.bar.setRange(0, total)
            self.bar.setValue(done)

        def logline(s):
            self.log.appendPlainText(s)

        def finished(present):
            self.bar.setVisible(False)
            self.assign_btn.setEnabled(True)
            self.status(f"slot {slot}: {'PRESENT ✓' if present else 'push done (verify manually)'}")
            # keep the cached slot map consistent so the grid isn't stale (audit L7)
            lc = self.win.link_cache
            if present and lc and lc.get("slot_map") is not None:
                lc["slot_map"] |= (1 << slot)
                lc["slots"] = [bool(lc["slot_map"] >> i & 1) for i in range(K.NSLOTS)]
            self._refresh_grid_records(lc["slots"] if lc else None)
            if present:
                # the push verified reg(0) for this slot — show it now, don't wait for a re-probe
                entry = sv.load_lineup().get("slots", {}).get(str(slot))
                self.grid.tiles[slot].set_record(entry, True)

        def failed(e):
            self.bar.setVisible(False)
            self.assign_btn.setEnabled(True)
            self.win._default_error(e)

        self.run_ble(lambda: sv.push_slot(self.source, slot, m, me, inv,
                                          on_progress=prog, on_log=logline),
                     on_done=finished, on_error=failed, busy=f"writing slot {slot}…")

    # ── slot actions ──
    def _apply_cycle(self):
        mode = "sequential" if self.rb_seq.isChecked() else "random"
        self.run_ble(lambda: sv.set_cycle(mode, on_log=self.log.appendPlainText),
                     on_done=lambda _: self.status(f"cycle = {mode}"), busy="set cycle…")

    def _set_threshold(self):
        secs = self.thr_spin.value()
        self.run_ble(lambda: sv.set_threshold(secs, on_log=self.log.appendPlainText),
                     on_done=lambda _: self.status(f"threshold = {secs}s"), busy="set threshold…")

    def _show_selected(self):
        slot = self.grid.selected
        self.run_ble(lambda: sv.show(slot, on_log=self.log.appendPlainText),
                     on_done=lambda _: self.status(f"showing slot {slot}"), busy="show…")

    def _clear_selected(self):
        slot = self.grid.selected
        self.run_ble(lambda: sv.clear(slot, on_log=self.log.appendPlainText),
                     on_done=lambda _: (self.status(f"cleared slot {slot}"),
                                        self._refresh_grid_records(None)), busy="clear…")

    def _clear_all(self):
        self.run_ble(lambda: sv.clear(0xFF, on_log=self.log.appendPlainText),
                     on_done=lambda _: (self.status("cleared ALL slots"),
                                        self._refresh_grid_records(None)), busy="clear all…")
