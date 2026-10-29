"""E-paper preview panel — shows a 1-bit render at the true 168×384 portrait aspect."""
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import QFrame, QVBoxLayout, QLabel

_W, _H = 168, 384


class EpaperPreview(QFrame):
    def __init__(self, title="Preview", width=150):
        super().__init__()
        self.setObjectName("preview_frame")
        img_w = width
        img_h = round(width * _H / _W)
        self.setFixedSize(img_w + 24, img_h + 52)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 12)
        lay.setSpacing(6)

        self.title = QLabel(title)
        self.title.setObjectName("subheading")
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.title)

        self.img = QLabel("no image")
        self.img.setObjectName("dim")
        self.img.setFixedSize(img_w, img_h)
        self.img.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.img.setStyleSheet("background:#0b0b12; border-radius:4px;")
        lay.addWidget(self.img, alignment=Qt.AlignmentFlag.AlignHCenter)

    def set_png(self, data: bytes, title=None):
        if title is not None:
            self.title.setText(title)
        pix = QPixmap()
        if pix.loadFromData(data):
            self.img.setPixmap(pix.scaled(
                self.img.size(), Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.FastTransformation))
        else:
            self.img.setText("render failed")

    def set_pixmap(self, pix: QPixmap, title=None):
        if title is not None:
            self.title.setText(title)
        if pix and not pix.isNull():
            self.img.setPixmap(pix.scaled(
                self.img.size(), Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.FastTransformation))
        else:
            self.clear()

    def clear(self, title=None):
        self.img.clear()
        self.img.setText("no image")
        if title is not None:
            self.title.setText(title)
