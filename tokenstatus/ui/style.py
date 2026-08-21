"""Shared QSS — light, soft, accent-on-action. Keeps the app calm during dev."""

ACCENT = "#F97316"        # warm orange (EasyClaw-ish)
ACCENT_DARK = "#EA580C"
BG = "#F7F8FA"
CARD = "#FFFFFF"
BORDER = "#CBD5E1"
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
QDialog, QInputDialog {{
    background: {BG};
}}
QFrame#card {{
    background: {CARD};
    border: 1px solid {BORDER};
    border-radius: 8px;
}}
QFrame#summaryBand {{
    background: #111827;
    border: 1px solid #0F172A;
    border-radius: 8px;
}}
QLabel#title {{
    font-size: 20px;
    font-weight: 700;
}}
QLabel#subtitle {{
    color: {MUTED};
    font-size: 12px;
}}
QLabel#dialogTitle {{
    font-size: 18px;
    font-weight: 700;
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
QLabel#summaryMetric {{
    color: white;
    font-size: 24px;
    font-weight: 750;
}}
QLabel#summaryMeta {{
    color: #CBD5E1;
    font-size: 12px;
    font-weight: 600;
}}
QPushButton {{
    background: {CARD};
    border: 1px solid #94A3B8;
    border-radius: 8px;
    min-height: 24px;
    padding: 7px 14px;
    font-size: 13px;
}}
QPushButton:hover {{
    border-color: {ACCENT};
    background: #FFF7ED;
}}
QPushButton:pressed {{
    background: #FFEDD5;
}}
QPushButton:disabled {{
    color: #94A3B8;
    background: #F1F5F9;
    border-color: #CBD5E1;
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
QPushButton#accountAdd {{
    background: #FFF7ED;
    border: 1px solid {ACCENT_DARK};
    color: #9A3412;
    font-weight: 700;
}}
QPushButton#accountAdd:hover {{
    background: #FFEDD5;
    border-color: #C2410C;
    color: #7C2D12;
}}
QPushButton#accountAdd:pressed {{
    background: #FED7AA;
    color: #7C2D12;
}}
QFrame#providerChoiceRow {{
    background: white;
    border: 1px solid #CBD5E1;
    border-radius: 8px;
}}
QFrame#providerChoiceRow:hover {{
    background: #FFF7ED;
    border: 2px solid {ACCENT};
}}
QLabel#providerChoiceName {{
    font-size: 14px;
    font-weight: 700;
    color: #111827;
}}
QLabel#providerChoiceDescription {{
    font-size: 11px;
    color: {MUTED};
}}
QLabel#providerChoiceAction {{
    font-size: 12px;
    font-weight: 700;
    color: #C2410C;
}}
QLabel#providerBadge_claude, QLabel#providerBadge_codex, QLabel#providerBadge_gemini {{
    border-radius: 7px;
    font-size: 16px;
    font-weight: 800;
}}
QLabel#providerBadge_claude {{
    background: #F3E8FF;
    color: #7E22CE;
}}
QLabel#providerBadge_codex {{
    background: #DCFCE7;
    color: #166534;
}}
QLabel#providerBadge_gemini {{
    background: #DBEAFE;
    color: #1D4ED8;
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
    border: 1px solid #94A3B8;
    border-radius: 6px;
    min-height: 28px;
    padding: 5px 9px;
    selection-background-color: {ACCENT};
}}
QSpinBox::up-button, QSpinBox::down-button,
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{
    width: 0px;
    height: 0px;
    border: none;
}}
QComboBox::drop-down {{
    width: 0px;
    border: none;
}}
QComboBox::down-arrow {{
    image: none;
    width: 0px;
    height: 0px;
}}
QLineEdit:hover, QSpinBox:hover, QComboBox:hover {{
    border-color: #64748B;
}}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus {{
    border: 2px solid {ACCENT};
    padding: 4px 8px;
}}
QAbstractItemView {{
    background: white;
    color: {TEXT};
    border: 1px solid #94A3B8;
    selection-background-color: #FFEDD5;
    selection-color: {TEXT};
    outline: none;
}}
QListWidget {{
    background: white;
    border: 1px solid #94A3B8;
    border-radius: 7px;
    padding: 4px;
    outline: none;
}}
QListWidget::item {{
    min-height: 32px;
    padding: 5px 8px;
    border-radius: 5px;
}}
QListWidget::item:hover {{
    background: #F1F5F9;
}}
QListWidget::item:selected {{
    background: #FFEDD5;
    color: #9A3412;
    font-weight: 600;
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
