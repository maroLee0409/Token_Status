"""Shared QSS — light, soft, accent-on-action. Keeps the app calm during dev."""

ACCENT = "#F97316"        # warm orange (EasyClaw-ish)
ACCENT_DARK = "#EA580C"
BG = "#F7F8FA"
CARD = "#FFFFFF"
BORDER = "#E5E7EB"
TEXT = "#1F2937"
MUTED = "#6B7280"
GREEN = "#22C55E"
AMBER = "#F59E0B"
RED = "#EF4444"


def bar_color(percent: float) -> str:
    if percent >= 90:
        return RED
    if percent >= 70:
        return AMBER
    return GREEN


GLOBAL_QSS = f"""
* {{
    font-family: "Segoe UI", "Apple SD Gothic Neo", "Malgun Gothic", sans-serif;
    color: {TEXT};
}}
QWidget#root {{
    background: {BG};
}}
QFrame#card {{
    background: {CARD};
    border: 1px solid {BORDER};
    border-radius: 12px;
}}
QLabel#title {{
    font-size: 18px;
    font-weight: 700;
}}
QLabel#subtitle {{
    color: {MUTED};
    font-size: 12px;
}}
QLabel#bigPercent {{
    font-size: 28px;
    font-weight: 700;
}}
QLabel#providerName {{
    font-size: 14px;
    font-weight: 600;
}}
QLabel#metaLabel {{
    color: {MUTED};
    font-size: 11px;
}}
QPushButton {{
    background: {CARD};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 6px 14px;
    font-size: 13px;
}}
QPushButton:hover {{
    border-color: {ACCENT};
}}
QPushButton#primary {{
    background: {ACCENT};
    border: 1px solid {ACCENT_DARK};
    color: white;
    font-weight: 600;
}}
QPushButton#primary:hover {{
    background: {ACCENT_DARK};
}}
QTabWidget::pane {{
    border: 1px solid {BORDER};
    border-radius: 10px;
    background: {CARD};
    top: -1px;
}}
QTabBar::tab {{
    background: transparent;
    padding: 8px 16px;
    border: none;
    color: {MUTED};
    font-weight: 600;
}}
QTabBar::tab:selected {{
    color: {ACCENT};
    border-bottom: 2px solid {ACCENT};
}}
QLineEdit, QSpinBox, QComboBox {{
    background: {CARD};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 5px 8px;
}}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus {{
    border-color: {ACCENT};
}}
QCheckBox {{
    spacing: 8px;
}}
QProgressBar {{
    background: {BG};
    border: 1px solid {BORDER};
    border-radius: 6px;
    height: 10px;
    text-align: center;
    font-size: 0px;
}}
QProgressBar::chunk {{
    border-radius: 5px;
}}
"""
