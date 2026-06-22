"""Dark theme — palette, Qt Style Sheet, app icon, and small UI helpers.

Outlaw aesthetic: near-black gunmetal background, amber/gold accents, rounded
panels, gradient buttons and progress, glowing focus rings.
"""

from __future__ import annotations

import math

from PyQt6.QtCore import QPointF, Qt
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QIcon,
    QLinearGradient,
    QPainter,
    QPalette,
    QPen,
    QPixmap,
    QPolygonF,
)
from PyQt6.QtWidgets import QApplication


COLORS = {
    "bg":         "#0b0e14",
    "bg_alt":     "#11151f",
    "panel":      "#161b27",
    "panel_hi":   "#1d2433",
    "border":     "#2a3242",
    "border_hi":  "#3a4456",
    "text":       "#e8eaed",
    "muted":      "#838c9c",
    "accent":     "#e0b341",   # outlaw gold
    "accent2":    "#f4d27a",   # light gold
    "accent_dim": "#7d6526",
    "ok":         "#5cc97a",
    "warn":       "#e8a84c",
    "err":        "#e0625e",
    "info":       "#6aa6e8",
    "thought_bg": "#10141d",
    "code_bg":    "#0c0f16",
    "code_border": "#2c3547",
}


# P1 — optional "Green Phosphor" palette for the dev tool, mirroring the desktop
# shell's classic green-terminal look. Same keys as COLORS so the QSS template
# and palette code work unchanged. Selected when config ui.theme == "green";
# the default stays gold-on-gunmetal (the Outlaw house style).
COLORS_GREEN = {
    "bg":         "#050705",
    "bg_alt":     "#0a0e0a",
    "panel":      "#0a0e0a",
    "panel_hi":   "#0d130d",
    "border":     "#143d0d",
    "border_hi":  "#1a8000",
    "text":       "#b9ffb0",
    "muted":      "#5a8f52",
    "accent":     "#33ff00",   # phosphor green
    "accent2":    "#7dff66",   # light green
    "accent_dim": "#1a8000",
    "ok":         "#33ff00",
    "warn":       "#ffd23b",
    "err":        "#ff5a5a",
    "info":       "#5ad0a0",
    "thought_bg": "#07120a",
    "code_bg":    "#030803",
    "code_border": "#143d0d",
}


# Phase 14h — "Broken" palette, mirroring the desktop shell's body.theme-broken:
# a sickly washed-out phosphor with failing-red hairlines. Same keys as COLORS so
# the QSS template + palette code work unchanged. Selected when the effective
# theme is "broken" (usually via "Match system theme" when the desktop is broken).
COLORS_BROKEN = {
    "bg":         "#060806",
    "bg_alt":     "#0b0f0b",
    "panel":      "#0b0f0b",
    "panel_hi":   "#0e140e",
    "border":     "#6b1b1b",   # failing red hairlines
    "border_hi":  "#8a2a2a",
    "text":       "#9ad48f",
    "muted":      "#5f7d5a",
    "accent":     "#4be04b",   # sickly, washed-out phosphor
    "accent2":    "#6be06b",
    "accent_dim": "#2a7a2a",
    "ok":         "#6be06b",
    "warn":       "#ffae3b",
    "err":        "#ff3b3b",
    "info":       "#5ad0a0",
    "thought_bg": "#0a120a",
    "code_bg":    "#050805",
    "code_border": "#6b1b1b",
}

_PALETTES = {"gold": COLORS, "green": COLORS_GREEN, "broken": COLORS_BROKEN}


def _read_system_theme() -> str:
    """Read the desktop's chosen theme from ~/.outlaw-theme (green|gold|broken),
    which the Outlaw OS shell mirrors there. Default 'gold' (CodeMaker's house
    style) when absent/unreadable — e.g. running off a non-Outlaw machine."""
    try:
        from pathlib import Path
        raw = (Path.home() / ".outlaw-theme").read_text(encoding="utf-8").strip().lower()
        if raw in _PALETTES:
            return raw
    except Exception:  # noqa: BLE001 — theme read must never crash startup
        pass
    return "gold"


def _palette_for(ui_config: dict) -> dict:
    """Pick the colour palette. 'system' (the default) follows the desktop's
    chosen theme so the Dev tool matches the rest of the OS; 'gold' / 'green' /
    'broken' pin a specific look regardless of the system."""
    theme = (ui_config or {}).get("theme") or "system"
    if theme == "system":
        theme = _read_system_theme()
    return _PALETTES.get(theme, COLORS)


QSS = """
* {{
    outline: none;
}}
QWidget {{
    background-color: {bg};
    color: {text};
    font-family: "{font_family}";
    font-size: {font_size}pt;
}}
QMainWindow, QDialog {{ background-color: {bg}; }}
QToolTip {{
    background-color: {panel_hi};
    color: {text};
    border: 1px solid {accent_dim};
    padding: 4px 6px;
    border-radius: 4px;
}}

/* ---- Header ---- */
QFrame#AppHeader {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #161c2a, stop:1 #0e1219);
    border: none;
    border-bottom: 1px solid {border};
}}
QLabel#AppTitle {{
    color: {accent2};
    font-size: {title_size}pt;
    font-weight: 800;
    letter-spacing: 2px;
}}
QLabel#AppTagline {{ color: {muted}; font-size: {small_size}pt; }}
QLabel#StatusDot {{ font-size: {font_size}pt; }}

/* ---- Panels ---- */
QFrame#PanelHeader {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 {panel_hi}, stop:1 {bg_alt});
    border: none;
    border-bottom: 1px solid {border};
}}
QLabel#PanelTitle {{
    color: {accent};
    font-weight: 700;
    letter-spacing: 1px;
}}

/* ---- Text surfaces ---- */
QTextEdit, QPlainTextEdit, QListWidget, QTreeWidget {{
    background-color: {panel};
    color: {text};
    border: 1px solid {border};
    border-radius: 8px;
    selection-background-color: {accent_dim};
    selection-color: {text};
    padding: 4px;
}}
QTextEdit#ThoughtView {{
    background-color: {thought_bg};
    color: #aeb6c4;
    border: 1px solid {border};
}}
QTextEdit#CodeView, QPlainTextEdit#TerminalLog {{
    background-color: {code_bg};
    color: {text};
    font-family: "{mono_family}";
}}

/* ---- Inputs ---- */
QLineEdit {{
    background-color: {panel};
    border: 1px solid {border};
    border-radius: 8px;
    padding: 9px 12px;
    color: {text};
    selection-background-color: {accent_dim};
}}
QLineEdit:focus {{ border: 1px solid {accent}; }}
QLineEdit:disabled {{ color: {muted}; background-color: {bg_alt}; }}

/* ---- Buttons ---- */
QPushButton {{
    background-color: {panel_hi};
    border: 1px solid {border};
    border-radius: 8px;
    padding: 8px 16px;
    color: {text};
}}
QPushButton:hover  {{ background-color: {border}; border-color: {border_hi}; }}
QPushButton:pressed{{ background-color: {bg_alt}; }}
QPushButton:disabled {{ color: {muted}; background-color: {bg_alt}; border-color: {border}; }}
QPushButton#PrimaryButton {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 {accent2}, stop:1 {accent});
    color: #1a1407;
    border: none;
    font-weight: 700;
}}
QPushButton#PrimaryButton:hover {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #ffe08a, stop:1 {accent2});
}}
QPushButton#PrimaryButton:disabled {{
    background: {bg_alt}; color: {muted};
}}
QPushButton#DangerButton {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #e8736f, stop:1 {err});
    color: #1a0707; border: none; font-weight: 700;
}}

/* ---- Progress ---- */
QProgressBar {{
    background-color: {bg_alt};
    border: 1px solid {border};
    border-radius: 8px;
    text-align: center;
    color: {text};
    height: 18px;
    font-size: {small_size}pt;
}}
QProgressBar::chunk {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 {accent_dim}, stop:0.5 {accent}, stop:1 {accent2});
    border-radius: 6px;
    margin: 1px;
}}

/* ---- Tabs ---- */
QTabWidget::pane {{ border: 1px solid {border}; border-radius: 8px; top: -1px; }}
QTabBar::tab {{
    background: {bg_alt};
    color: {muted};
    padding: 7px 16px;
    border: 1px solid {border};
    border-bottom: none;
    border-top-left-radius: 7px;
    border-top-right-radius: 7px;
    margin-right: 2px;
}}
QTabBar::tab:selected {{ background: {panel_hi}; color: {accent2}; }}
QTabBar::tab:hover {{ color: {text}; }}

/* ---- Trees / lists ---- */
QTreeWidget::item, QListWidget::item {{ padding: 3px 2px; border-radius: 4px; }}
QTreeWidget::item:selected, QListWidget::item:selected {{
    background-color: {accent_dim}; color: {text};
}}
QTreeWidget::item:hover, QListWidget::item:hover {{ background-color: {panel_hi}; }}

/* ---- Menu / status ---- */
QMenuBar {{ background: {bg_alt}; color: {text}; border-bottom: 1px solid {border}; }}
QMenuBar::item:selected {{ background: {panel_hi}; color: {accent2}; }}
QMenu {{ background: {panel}; color: {text}; border: 1px solid {border}; }}
QMenu::item:selected {{ background: {accent_dim}; }}
QStatusBar {{ background: {bg_alt}; color: {muted}; border-top: 1px solid {border}; }}
QStatusBar QLabel {{ padding: 0 6px; }}

/* ---- Splitter ---- */
QSplitter::handle {{ background: {border}; }}
QSplitter::handle:horizontal {{ width: 3px; }}
QSplitter::handle:vertical {{ height: 3px; }}
QSplitter::handle:hover {{ background: {accent_dim}; }}

/* ---- Scrollbars ---- */
QScrollBar:vertical {{ background: {bg_alt}; width: 11px; margin: 2px; border-radius: 5px; }}
QScrollBar::handle:vertical {{ background: {border_hi}; border-radius: 5px; min-height: 28px; }}
QScrollBar::handle:vertical:hover {{ background: {accent_dim}; }}
QScrollBar:horizontal {{ background: {bg_alt}; height: 11px; margin: 2px; border-radius: 5px; }}
QScrollBar::handle:horizontal {{ background: {border_hi}; border-radius: 5px; min-width: 28px; }}
QScrollBar::handle:horizontal:hover {{ background: {accent_dim}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}

/* ---- Combo boxes (C15 — were unstyled Fusion grey before) ---- */
QComboBox {{
    background-color: {panel};
    border: 1px solid {border};
    border-radius: 8px;
    padding: 8px 12px;
    color: {text};
    min-height: 18px;
}}
QComboBox:hover {{ border-color: {border_hi}; }}
QComboBox:focus {{ border: 1px solid {accent}; }}
QComboBox:disabled {{ color: {muted}; background-color: {bg_alt}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox::down-arrow {{
    image: none;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid {muted};
    margin-right: 9px;
}}
QComboBox QAbstractItemView {{
    background-color: {panel};
    color: {text};
    border: 1px solid {border};
    border-radius: 8px;
    selection-background-color: {accent_dim};
    selection-color: {text};
    padding: 4px;
    outline: none;
}}

/* ---- Check boxes / radio buttons (C15) ---- */
QCheckBox, QRadioButton {{ color: {text}; spacing: 8px; padding: 3px 0; }}
QCheckBox::indicator, QRadioButton::indicator {{
    width: 16px; height: 16px;
    border: 1px solid {border_hi};
    background-color: {panel};
}}
QCheckBox::indicator {{ border-radius: 4px; }}
QRadioButton::indicator {{ border-radius: 9px; }}
QCheckBox::indicator:hover, QRadioButton::indicator:hover {{ border-color: {accent}; }}
QCheckBox::indicator:checked, QRadioButton::indicator:checked {{
    background-color: {accent}; border-color: {accent};
}}
QCheckBox:disabled, QRadioButton:disabled {{ color: {muted}; }}

/* ---- Spin boxes (C15) ---- */
QSpinBox, QDoubleSpinBox {{
    background-color: {panel};
    border: 1px solid {border};
    border-radius: 8px;
    padding: 7px 10px;
    color: {text};
}}
QSpinBox:focus, QDoubleSpinBox:focus {{ border: 1px solid {accent}; }}
QSpinBox:disabled, QDoubleSpinBox:disabled {{ color: {muted}; background-color: {bg_alt}; }}

/* ---- Group boxes (C15 — titled, with breathing room) ---- */
QGroupBox {{
    border: 1px solid {border};
    border-radius: 10px;
    margin-top: 16px;
    padding: 16px 14px 12px 14px;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 12px;
    padding: 0 6px;
    color: {accent};
    font-weight: 700;
    letter-spacing: 1px;
}}

/* ---- Scroll areas: no stray panel box, inherit the window (C15) ---- */
QScrollArea {{ border: none; background: transparent; }}
"""


def apply_dark_theme(app: QApplication, ui_config: dict) -> None:
    font_family = ui_config.get("font_family", "Cascadia Mono")
    mono_family = ui_config.get("mono_family", font_family)
    font_size = int(ui_config.get("font_size", 11))
    colors = _palette_for(ui_config)

    app.setStyle("Fusion")

    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(colors["bg"]))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(colors["text"]))
    palette.setColor(QPalette.ColorRole.Base, QColor(colors["panel"]))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(colors["bg_alt"]))
    palette.setColor(QPalette.ColorRole.Text, QColor(colors["text"]))
    palette.setColor(QPalette.ColorRole.Button, QColor(colors["panel_hi"]))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(colors["text"]))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(colors["accent_dim"]))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor(colors["text"]))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(colors["panel_hi"]))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor(colors["text"]))
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor(colors["muted"]))
    app.setPalette(palette)

    app.setFont(QFont(font_family, font_size))
    app.setStyleSheet(
        QSS.format(
            font_family=font_family,
            mono_family=mono_family,
            font_size=font_size,
            small_size=max(8, font_size - 2),
            title_size=font_size + 5,
            **colors,
        )
    )
    app.setWindowIcon(make_app_icon())


def make_app_icon(size: int = 64) -> QIcon:
    """Draw a gold sheriff-star on a dark rounded tile — the Outlaw mark."""
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

    # Rounded dark tile
    p.setBrush(QBrush(QColor("#12161f")))
    p.setPen(QPen(QColor(COLORS["border_hi"]), 2))
    p.drawRoundedRect(2, 2, size - 4, size - 4, 12, 12)

    # Gold star
    cx, cy = size / 2, size / 2
    outer, inner = size * 0.34, size * 0.15
    star = _star_polygon(cx, cy, outer, inner, points=5, rotation=-math.pi / 2)
    grad = QLinearGradient(0, cy - outer, 0, cy + outer)
    grad.setColorAt(0.0, QColor(COLORS["accent2"]))
    grad.setColorAt(1.0, QColor(COLORS["accent"]))
    p.setBrush(QBrush(grad))
    p.setPen(QPen(QColor("#6b531c"), 1.5))
    p.drawPolygon(star)

    # Center dot
    p.setBrush(QBrush(QColor("#12161f")))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawEllipse(QPointF(cx, cy), size * 0.045, size * 0.045)

    p.end()
    return QIcon(pm)


def _star_polygon(cx: float, cy: float, outer: float, inner: float,
                  points: int = 5, rotation: float = 0.0) -> QPolygonF:
    poly = QPolygonF()
    step = math.pi / points
    for i in range(points * 2):
        r = outer if i % 2 == 0 else inner
        ang = rotation + i * step
        poly.append(QPointF(cx + r * math.cos(ang), cy + r * math.sin(ang)))
    return poly


def dot(color_key: str) -> str:
    """Return a colored bullet for status labels."""
    return f'<span style="color:{COLORS.get(color_key, COLORS["muted"])};">●</span>'
