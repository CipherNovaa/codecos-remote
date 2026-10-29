"""pywal theming — robust surfaces derived from background, wal colors as accents.

Ported from scratchpad/supernote_ref.py, then hardened: an image-derived pywal palette
can put ANY colour in col;8/colour1 (the author's had a *light* color8), so surfaces are NOT
taken from wal slots — they are mixed from `background`, and body text from `foreground`.
Wal colours are used only as accents, each nudged until it contrasts the surface. Result
reads correctly on any wallpaper, light or dark.

Object names panels rely on: ``heading`` ``subheading`` ``dim`` ``primary`` ``danger``
``ok`` ``warn`` ``preview_frame`` ``drop_zone`` ``card`` ``badge`` ``console``.
"""
import json
from pathlib import Path

WAL_COLORS = Path.home() / ".cache/wal/colors.json"


def load_wal_colors():
    """Read pywal colors.json and return a dict of color strings."""
    defaults = {
        "background": "#181825", "foreground": "#cdd6f4",
        "color0": "#1e1e2e", "color1": "#f38ba8", "color2": "#a6e3a1",
        "color3": "#f9e2af", "color4": "#89b4fa", "color5": "#cba6f7",
        "color6": "#94e2d5", "color7": "#cdd6f4", "color8": "#313244",
        "color15": "#cdd6f4",
    }
    try:
        with open(WAL_COLORS) as f:
            data = json.load(f)
        colors = data.get("colors", {})
        special = data.get("special", {})
        return {
            "background": special.get("background", defaults["background"]),
            "foreground": special.get("foreground", defaults["foreground"]),
            **{f"color{i}": colors.get(f"color{i}", defaults.get(f"color{i}", "#888888"))
               for i in range(16)},
        }
    except Exception:
        return defaults


# ── colour math (theme-robust tokens) ──
def _rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _hex(rgb):
    return "#%02x%02x%02x" % tuple(max(0, min(255, int(round(c)))) for c in rgb)


def _mix(a, b, t):
    """Blend a→b by fraction t (0..1)."""
    ra, rb = _rgb(a), _rgb(b)
    return _hex(tuple(ra[i] + (rb[i] - ra[i]) * t for i in range(3)))


def _lum(h):
    r, g, b = (c / 255 for c in _rgb(h))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _ensure(color, base, target=0.34):
    """Nudge `color` toward white (on a dark base) / black (on a light base) until its
    luminance differs from `base` by at least `target` — so accents stay legible."""
    toward = "#ffffff" if _lum(base) < 0.5 else "#000000"
    c = color
    for _ in range(12):
        if abs(_lum(c) - _lum(base)) >= target:
            break
        c = _mix(c, toward, 0.18)
    return c


def _text_on(c):
    """Readable ink for a filled accent surface."""
    return "#0d0d0d" if _lum(c) > 0.55 else "#f2f4f2"


def build_stylesheet(c):
    bg, fg = c["background"], c["foreground"]
    dark = _lum(bg) < 0.5
    if abs(_lum(fg) - _lum(bg)) < 0.35:                      # guarantee body contrast
        fg = "#e6e9e6" if dark else "#101010"

    # surfaces derived from the real background (never from a wal slot)
    surface = _mix(bg, fg, 0.07)
    surface2 = _mix(bg, fg, 0.12)
    surface_hi = _mix(bg, fg, 0.19)
    border = _mix(bg, fg, 0.26)
    border_hi = _mix(bg, fg, 0.42)

    # text tones derived from foreground
    text = fg
    muted = _mix(fg, bg, 0.42)
    faint = _mix(fg, bg, 0.55)

    # accents from wal, each guaranteed to read on `surface`
    accent = _ensure(c["color4"], surface, 0.42)             # primary / headings
    accent2 = _ensure(c["color6"], surface, 0.36)            # subheading / badge
    ok = _ensure(c["color2"], surface, 0.40)
    warn = _ensure(c["color3"], surface, 0.42)
    danger = _ensure(c["color1"], surface, 0.40)
    on_accent = _text_on(accent)
    on_danger = _text_on(danger)

    return f"""
    QMainWindow, QWidget {{
        background-color: {bg}; color: {text};
        font-family: "IosevkaTerm Nerd Font", "Iosevka Nerd Font", "Noto Sans", sans-serif;
        font-size: 13px;
    }}
    QTabWidget::pane {{ border: 1px solid {border}; background: {bg}; border-radius: 6px; }}
    QTabBar::tab {{
        background: {surface}; color: {muted}; padding: 8px 16px;
        border: 1px solid {border}; border-bottom: none;
        border-radius: 5px 5px 0 0; margin-right: 2px;
    }}
    QTabBar::tab:selected {{ background: {accent}; color: {on_accent}; font-weight: bold; border-color: {accent}; }}
    QTabBar::tab:hover:!selected {{ color: {text}; background: {surface_hi}; }}

    QPushButton {{
        background-color: {surface2}; color: {text}; border: 1px solid {border_hi};
        border-radius: 6px; padding: 7px 14px; font-size: 13px;
    }}
    QPushButton:hover {{ background-color: {surface_hi}; border-color: {accent}; }}
    QPushButton:pressed {{ background-color: {surface}; }}
    QPushButton:disabled {{ color: {faint}; border-color: {border}; background: {surface}; }}
    QPushButton#primary {{ background-color: {accent}; color: {on_accent}; border-color: {accent}; font-weight: bold; }}
    QPushButton#primary:hover {{ background-color: {_mix(accent, fg, 0.18)}; }}
    QPushButton#danger {{ background-color: {surface}; color: {danger}; border: 1px solid {danger}; font-weight: bold; }}
    QPushButton#danger:hover {{ background-color: {danger}; color: {on_danger}; }}

    QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit, QTextEdit {{
        background-color: {surface2}; color: {text}; border: 1px solid {border_hi};
        border-radius: 5px; padding: 6px 10px; selection-background-color: {accent};
        selection-color: {on_accent};
    }}
    QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{ border-color: {accent}; }}
    QComboBox::drop-down {{ border: none; width: 20px; }}
    QComboBox QAbstractItemView {{
        background: {surface2}; color: {text}; selection-background-color: {accent};
        selection-color: {on_accent}; border: 1px solid {border_hi}; outline: none;
    }}
    QSpinBox::up-button, QSpinBox::down-button,
    QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{ width: 16px; background: {surface_hi}; }}

    QLabel {{ color: {text}; background: transparent; }}
    QLabel#heading {{ font-size: 16px; font-weight: bold; color: {accent}; }}
    QLabel#subheading {{ font-size: 12px; color: {accent2}; }}
    QLabel#dim {{ color: {muted}; font-size: 11px; }}
    QLabel#ok {{ color: {ok}; font-weight: bold; }}
    QLabel#warn {{ color: {warn}; }}
    QLabel#badge {{
        background: {surface2}; color: {accent2}; border: 1px solid {border_hi};
        border-radius: 4px; padding: 2px 8px;
    }}

    QFrame#card {{ background-color: {surface}; border: 1px solid {border}; border-radius: 10px; }}
    QFrame#preview_frame {{ background-color: {surface}; border: 1px solid {border_hi}; border-radius: 8px; }}
    QFrame#drop_zone {{ background-color: {surface}; border: 2px dashed {border_hi}; border-radius: 10px; color: {muted}; }}

    QGroupBox {{
        border: 1px solid {border}; border-radius: 8px; margin-top: 12px;
        padding-top: 10px; color: {muted}; font-size: 11px;
    }}
    QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; color: {accent2}; }}

    QRadioButton, QCheckBox {{ color: {text}; spacing: 8px; }}
    QCheckBox::indicator, QRadioButton::indicator {{
        width: 16px; height: 16px; border: 1px solid {border_hi}; background: {surface2};
    }}
    QCheckBox::indicator {{ border-radius: 3px; }}
    QRadioButton::indicator {{ border-radius: 8px; }}
    QCheckBox::indicator:checked, QRadioButton::indicator:checked {{
        background: {accent}; border-color: {accent};
    }}

    QProgressBar {{
        background-color: {surface2}; border: 1px solid {border_hi}; border-radius: 5px;
        text-align: center; color: {text}; height: 18px;
    }}
    QProgressBar::chunk {{ background-color: {ok}; border-radius: 4px; }}

    QListWidget {{
        background-color: {surface}; color: {text}; border: 1px solid {border};
        border-radius: 6px; padding: 4px;
    }}
    QListWidget::item {{ padding: 6px; border-radius: 4px; }}
    QListWidget::item:selected {{ background-color: {accent}; color: {on_accent}; }}
    QListWidget::item:hover {{ background-color: {surface_hi}; }}

    QPlainTextEdit#console, QTextEdit#console {{
        background-color: {_mix(bg, fg, 0.04)}; color: {_mix(fg, bg, 0.12)};
        font-family: "IosevkaTerm Nerd Font", "Iosevka Nerd Font", monospace; font-size: 12px;
        border: 1px solid {border};
    }}

    QScrollBar:vertical {{ background: {bg}; width: 9px; border-radius: 4px; }}
    QScrollBar::handle:vertical {{ background: {border_hi}; border-radius: 4px; min-height: 20px; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    QScrollBar:horizontal {{ background: {bg}; height: 9px; border-radius: 4px; }}
    QScrollBar::handle:horizontal {{ background: {border_hi}; border-radius: 4px; min-width: 20px; }}
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}

    QStatusBar {{ background-color: {surface}; color: {muted}; font-size: 11px; }}
    QStatusBar::item {{ border: none; }}
    QToolTip {{ background: {surface2}; color: {text}; border: 1px solid {accent}; }}
    """
