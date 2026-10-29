"""IR & Amp — drive the Yamaha amp / Sony TV via the remote's IR-over-BLE (W16).

Uses the remote's NORMAL bond (no ACTIVITIES+DEVICES config chord). Must run on the
machine whose Bluetooth is in range of the remote — the laptop when the author is casting.
"""
from PyQt6.QtWidgets import (QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QGroupBox,
                             QGridLayout, QDoubleSpinBox)

from .base import Panel
from ..engine import amp


class AmpPanel(Panel):
    title = "IR & Amp"

    def build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        head = QLabel("Amp / TV — IR over BLE")
        head.setObjectName("heading")
        root.addWidget(head)

        note = QLabel("Uses the remote's normal bond — no config chord needed. "
                      "Run this on the machine in Bluetooth range of the remote "
                      "(the laptop when casting).")
        note.setObjectName("dim")
        note.setWordWrap(True)
        root.addWidget(note)

        opt = QHBoxLayout()
        opt.addWidget(QLabel("gap between shots"))
        self.gap = QDoubleSpinBox()
        self.gap.setRange(0.1, 2.0)
        self.gap.setSingleStep(0.1)
        self.gap.setValue(0.4)
        self.gap.setSuffix(" s")
        opt.addWidget(self.gap)
        opt.addStretch()
        root.addLayout(opt)

        for gname, cmds in amp.GROUPS:
            box = QGroupBox(gname)
            grid = QGridLayout(box)
            for i, (cmd, label) in enumerate(cmds):
                b = QPushButton(label)
                b.setToolTip(amp.describe(cmd))
                b.clicked.connect(lambda _c, name=cmd: self._fire(name))
                grid.addWidget(b, i // 4, i % 4)
            root.addWidget(box)

        root.addStretch()

    def _fire(self, name):
        self.run_ble(lambda: amp.fire(name, gap=self.gap.value()),
                     on_done=lambda n: self.status(f"{name} → {n} shot(s) sent ✓", 4000),
                     busy=f"IR {name}…")
