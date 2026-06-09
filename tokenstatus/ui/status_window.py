from __future__ import annotations

import time
from datetime import datetime
from typing import Callable

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..readers import ProviderSnapshot
from .style import GLOBAL_QSS, MUTED, bar_color


def _fmt_age(ts: float | None) -> str:
    if not ts:
        return "기록 없음"
    diff = time.time() - ts
    if diff < 60:
        return f"{int(diff)}초 전"
    if diff < 3600:
        return f"{int(diff/60)}분 전"
    if diff < 86400:
        return f"{int(diff/3600)}시간 전"
    return f"{int(diff/86400)}일 전"


def _fmt_resets(ts: float | None) -> str:
    if not ts:
        return ""
    try:
        return datetime.fromtimestamp(ts).strftime("%m/%d %H:%M")
    except (OSError, ValueError):
        return ""


def _fmt_number(n: int, unit: str) -> str:
    if unit == "%":
        return f"{n}%"
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M {unit}"
    if n >= 1_000:
        return f"{n/1_000:.1f}K {unit}"
    return f"{n:,} {unit}"


class ProviderCard(QFrame):
    def __init__(self, key: str, display_mode: str = "standard") -> None:
        super().__init__()
        self.setObjectName("card")
        self.key = key
        self._mode = display_mode
        self._build()
        self.set_display_mode(display_mode)

    def _build(self) -> None:
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(16, 14, 16, 14)
        self._layout.setSpacing(6)

        top = QHBoxLayout()
        self.name_label = QLabel("—")
        self.name_label.setObjectName("providerName")
        self.percent_label = QLabel("—")
        self.percent_label.setObjectName("bigPercent")
        self.percent_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        top.addWidget(self.name_label)
        top.addStretch(1)
        top.addWidget(self.percent_label)
        self._layout.addLayout(top)

        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        self.bar.setTextVisible(False)
        self._layout.addWidget(self.bar)

        self.window_label = QLabel("—")
        self.window_label.setObjectName("metaLabel")
        self._layout.addWidget(self.window_label)

        self.detail_label = QLabel("—")
        self.detail_label.setObjectName("metaLabel")
        self.detail_label.setWordWrap(True)
        self._layout.addWidget(self.detail_label)

        self.secondary_label = QLabel("")
        self.secondary_label.setObjectName("metaLabel")
        self._layout.addWidget(self.secondary_label)

    def set_display_mode(self, mode: str) -> None:
        self._mode = mode if mode in ("standard", "compact", "minimal") else "standard"
        # Standard: all widgets, full padding. Compact: hide details/secondary, smaller padding,
        # thinner bar. Minimal: hide bar + all meta, just name + %.
        show_bar = self._mode != "minimal"
        show_meta = self._mode == "standard"
        self.bar.setVisible(show_bar)
        self.window_label.setVisible(show_meta)
        self.detail_label.setVisible(show_meta)
        self.secondary_label.setVisible(show_meta)

        if self._mode == "standard":
            self._layout.setContentsMargins(16, 14, 16, 14)
            self._layout.setSpacing(6)
            self.bar.setFixedHeight(10)
            self.percent_label.setStyleSheet("font-size: 28px; font-weight: 700;")
        elif self._mode == "compact":
            self._layout.setContentsMargins(14, 10, 14, 10)
            self._layout.setSpacing(4)
            self.bar.setFixedHeight(6)
            self.percent_label.setStyleSheet("font-size: 22px; font-weight: 700;")
        else:  # minimal
            self._layout.setContentsMargins(14, 8, 14, 8)
            self._layout.setSpacing(0)
            self.percent_label.setStyleSheet("font-size: 18px; font-weight: 700;")

    def update_snapshot(self, snap: ProviderSnapshot | None) -> None:
        if snap is None:
            self.name_label.setText("—")
            self.percent_label.setText("—")
            self.bar.setValue(0)
            self.window_label.setText("비활성화됨")
            self.detail_label.setText("")
            self.secondary_label.setText("")
            return

        self.name_label.setText(snap.name)
        pct = snap.percent_clamped()
        self.percent_label.setText(f"{pct:.1f}%")
        self.bar.setValue(min(100, int(pct)))
        c = bar_color(pct)
        self.bar.setStyleSheet(
            f"QProgressBar::chunk {{ background: {c}; border-radius: 5px; }}"
        )

        if not snap.available:
            self.window_label.setText("측정 불가")
            self.detail_label.setText(snap.note or snap.error or "")
            self.secondary_label.setText("")
            return

        self.window_label.setText(snap.window_label)
        used_txt = _fmt_number(snap.used, snap.unit)
        limit_txt = _fmt_number(snap.limit, snap.unit) if snap.limit else "—"
        activity = _fmt_age(snap.last_activity)
        self.detail_label.setText(
            f"사용: {used_txt} / 한도: {limit_txt} · 마지막 활동: {activity}\n{snap.note}"
        )

        bits: list[str] = []
        if snap.secondary_percent is not None and snap.secondary_label:
            bits.append(f"{snap.secondary_label}: {snap.secondary_percent:.1f}%")
        if snap.resets_at:
            bits.append(f"리셋: {_fmt_resets(snap.resets_at)}")
        self.secondary_label.setText("  ·  ".join(bits))


class StatusWindow(QWidget):
    def __init__(
        self,
        on_settings: Callable[[], None],
        on_refresh: Callable[[], None],
        display_mode: str = "standard",
    ) -> None:
        super().__init__()
        self._on_settings = on_settings
        self._on_refresh = on_refresh
        self._display_mode = display_mode
        self.setWindowTitle("Token Status")
        self.setObjectName("root")
        self.setStyleSheet(GLOBAL_QSS)
        self.setMinimumWidth(280)
        self.resize(420, self.sizeHint().height())
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, False)
        self._cards: dict[str, ProviderCard] = {}
        self._last_update: float = 0.0
        self._build()

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 12, 14, 12)
        outer.setSpacing(10)

        header = QHBoxLayout()
        title = QLabel("토큰 사용량")
        title.setObjectName("title")
        self.subtitle = QLabel("실시간 모니터링")
        self.subtitle.setObjectName("subtitle")
        gear = QPushButton("⚙")
        gear.setToolTip("설정")
        gear.setFixedSize(28, 28)
        gear.setStyleSheet(
            "QPushButton { font-size: 16px; padding: 0px; border-radius: 14px; }"
        )
        gear.clicked.connect(self._on_settings)
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(self.subtitle)
        header.addSpacing(8)
        header.addWidget(gear)
        outer.addLayout(header)

        for key in ("claude", "codex", "gemini"):
            card = ProviderCard(key, display_mode=self._display_mode)
            self._cards[key] = card
            outer.addWidget(card)

        outer.addStretch(1)

        btns = QHBoxLayout()
        refresh = QPushButton("새로고침")
        refresh.clicked.connect(self._on_refresh)
        settings = QPushButton("설정")
        settings.setObjectName("primary")
        settings.clicked.connect(self._on_settings)
        btns.addWidget(refresh)
        btns.addStretch(1)
        btns.addWidget(settings)
        outer.addLayout(btns)

    def set_display_mode(self, mode: str) -> None:
        self._display_mode = mode
        for card in self._cards.values():
            card.set_display_mode(mode)
        self.adjustSize()

    def apply_snapshots(self, snapshots: dict) -> None:
        self._last_update = time.time()
        self.subtitle.setText(f"마지막 갱신: {datetime.now().strftime('%H:%M:%S')}")
        for key, card in self._cards.items():
            snap = snapshots.get(key)
            if snap is None:
                card.setVisible(False)
            else:
                card.setVisible(True)
                card.update_snapshot(snap)
        # Shrink window to fit the visible cards.
        self.adjustSize()

    def show_near_tray(self) -> None:
        """Position the window in the bottom-right, but always fully on-screen."""
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            self.show()
            self.activateWindow()
            return
        geom = screen.availableGeometry()
        size = self.sizeHint()
        # Cap width to fit the work area (taskbar excluded).
        w = min(max(self.minimumWidth(), size.width()), geom.width() - 24)
        h = min(size.height(), geom.height() - 24)
        x = max(geom.left() + 12, geom.right() - w - 12)
        y = max(geom.top() + 12, geom.bottom() - h - 12)
        self.setGeometry(x, y, w, h)
        self.show()
        self.raise_()
        self.activateWindow()
