"""Lights — e-paper front-light, keypad backlight, and buzzer (codec_led struct).

Read-modify-write of the 8-byte settings struct, validated (firmware mirror) before
any write and confirmed by read-back. Non-bricking. The BLUE LIGHT BAR is NOT here —
it is a firmware GPIO (already patched off); this panel says so.
"""
from PyQt6.QtWidgets import (QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QComboBox,
                             QSpinBox, QGroupBox, QPlainTextEdit)

from .base import Panel
from ..engine import lights as li


class LightsPanel(Panel):
    title = "Lights"
    needs_link = True

    def build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        head = QLabel("Lights, buzzer & timeouts")
        head.setObjectName("heading")
        root.addWidget(head)

        top = QHBoxLayout()
        read_btn = QPushButton("Read current settings")
        read_btn.setObjectName("primary")
        read_btn.clicked.connect(self._read)
        top.addWidget(read_btn)
        top.addStretch()
        root.addLayout(top)

        levels = ["0 — Off", "1 — Low", "2 — Med", "3 — High"]
        self._writers = []               # apply/quick buttons — disabled until a Read populates

        # screen front-light
        sg = QGroupBox("E-paper front-light")
        sgl = QHBoxLayout(sg)
        self.screen_level = QComboBox()
        self.screen_level.addItems(levels)
        self.screen_timeout = QSpinBox()
        self.screen_timeout.setRange(1, 60)
        self.screen_timeout.setSuffix(" s")
        apply_s = QPushButton("Apply")
        apply_s.clicked.connect(self._apply_screen)
        self._writers.append(apply_s)
        sgl.addWidget(QLabel("brightness"))
        sgl.addWidget(self.screen_level)
        sgl.addWidget(QLabel("timeout"))
        sgl.addWidget(self.screen_timeout)
        sgl.addStretch()
        sgl.addWidget(apply_s)
        root.addWidget(sg)

        # keypad backlight
        kg = QGroupBox("Physical-key backlight")
        kgl = QHBoxLayout(kg)
        self.key_level = QComboBox()
        self.key_level.addItems(levels)
        self.key_timeout = QSpinBox()
        self.key_timeout.setRange(1, 60)
        self.key_timeout.setSuffix(" s")
        for name, lvl in [("Off", 0), ("Low", 1), ("Med", 2), ("High", 3)]:
            b = QPushButton(name)
            b.clicked.connect(lambda _c, v=lvl: self._quick_key(v))
            self._writers.append(b)
            kgl.addWidget(b)
        kgl.addSpacing(10)
        kgl.addWidget(QLabel("level"))
        kgl.addWidget(self.key_level)
        kgl.addWidget(QLabel("timeout"))
        kgl.addWidget(self.key_timeout)
        apply_k = QPushButton("Apply")
        apply_k.clicked.connect(self._apply_keys)
        self._writers.append(apply_k)
        kgl.addWidget(apply_k)
        root.addWidget(kg)

        # buzzer
        bg = QGroupBox("Keypress buzzer")
        bgl = QHBoxLayout(bg)
        self.buzz_mode = QComboBox()
        self.buzz_mode.addItems(list(li.BUZZER_MODES.keys()))   # off/physical/touch/both
        self.buzz_level = QSpinBox()
        self.buzz_level.setRange(1, 5)
        apply_b = QPushButton("Apply")
        apply_b.clicked.connect(self._apply_buzzer)
        self._writers.append(apply_b)
        bgl.addWidget(QLabel("mode"))
        bgl.addWidget(self.buzz_mode)
        bgl.addWidget(QLabel("level (1..5)"))
        bgl.addWidget(self.buzz_level)
        bgl.addStretch()
        bgl.addWidget(apply_b)
        root.addWidget(bg)

        note = QLabel(li.BAR_NOTE.replace("\n", " ").strip())
        note.setObjectName("dim")
        note.setWordWrap(True)
        root.addWidget(note)

        self.console = QPlainTextEdit()
        self.console.setObjectName("console")
        self.console.setReadOnly(True)
        self.console.setMaximumHeight(150)
        root.addWidget(self.console, 1)

        # Apply is read-modify-write; until a Read populates the controls they hold
        # placeholder defaults, so keep every writer disabled until then (audit M5).
        for b in self._writers:
            b.setEnabled(False)
        self.console.setPlaceholderText("Read current settings first — Apply is disabled "
                                        "until the panel reflects what's on the device.")

    def _read(self):
        self.run_ble(li.read, on_done=self._sync_from_struct, busy="reading settings…")

    def _sync_from_struct(self, s):
        self.screen_level.setCurrentIndex(min(3, s[li.I_SCREEN_BRIGHT]))
        self.screen_timeout.setValue(max(1, min(60, s[li.I_SCREEN_TIMEOUT])))
        self.key_level.setCurrentIndex(min(3, s[li.I_KEY_BRIGHT]))
        self.key_timeout.setValue(max(1, min(60, s[li.I_KEY_TIMEOUT])))
        mode, level = s[li.I_BUZZER] >> 4, s[li.I_BUZZER] & 0x0F
        self.buzz_mode.setCurrentIndex(min(3, mode))
        self.buzz_level.setValue(max(1, min(5, level)))
        self.console.setPlainText(li.describe(s))
        for b in self._writers:                    # now the controls reflect the device
            b.setEnabled(True)
        self.status("read current settings ✓")

    def _apply(self, changes, busy):
        def done(res):
            self.console.setPlainText(
                f"before: {res['before'].hex(' ')}\n"
                f"after : {res['after'].hex(' ')}\n"
                f"result: {res['reason']}\n\n" + li.describe(res["readback"]))
            self.status(("✓ " if res["ok"] else "⚠ ") + res["reason"], 6000)
        self.run_ble(lambda: li.apply(changes), on_done=done, busy=busy)

    def _apply_screen(self):
        self._apply({li.I_SCREEN_BRIGHT: self.screen_level.currentIndex(),
                     li.I_SCREEN_TIMEOUT: self.screen_timeout.value()}, "set screen light…")

    def _apply_keys(self):
        self._apply({li.I_KEY_BRIGHT: self.key_level.currentIndex(),
                     li.I_KEY_TIMEOUT: self.key_timeout.value()}, "set key backlight…")

    def _quick_key(self, level):
        self.key_level.setCurrentIndex(level)
        self._apply({li.I_KEY_BRIGHT: level}, f"key backlight {level}…")

    def _apply_buzzer(self):
        mode = li.BUZZER_MODES[self.buzz_mode.currentText()]
        self._apply({"buzzer": (mode, self.buzz_level.value())}, "set buzzer…")
