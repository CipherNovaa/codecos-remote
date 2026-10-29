"""The config-link ritual checklist — surfaced whenever a BLE op reports LinkDown."""
from PyQt6.QtWidgets import QFrame, QVBoxLayout, QLabel

from .. import constants as K


class RitualCard(QFrame):
    """A dismissable card that spells out how to bring the config link up."""
    def __init__(self):
        super().__init__()
        self.setObjectName("card")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(6)

        head = QLabel("Config link is down — bring it up:")
        head.setObjectName("warn")
        lay.addWidget(head)

        for i, step in enumerate(K.RITUAL_STEPS, 1):
            lbl = QLabel(f"{i}.  {step}")
            lbl.setWordWrap(True)
            lay.addWidget(lbl)

        note = QLabel("(This chord is only for the CONFIG link — screensaver, labels, "
                      "lights. The amp/TV IR uses the remote's normal bond and needs no chord.)")
        note.setObjectName("dim")
        note.setWordWrap(True)
        lay.addWidget(note)
        self.hide()
