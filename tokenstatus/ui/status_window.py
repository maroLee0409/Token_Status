from __future__ import annotations

import time
from datetime import datetime
from typing import Callable

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import (
    QFrame,
    QGridLayout,
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
        return f"{int(diff / 60)}분 전"
    if diff < 86400:
        return f"{int(diff / 3600)}시간 전"
    return f"{int(diff / 86400)}일 전"


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
        return f"{n / 1_000_000:.1f}M {unit}"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K {unit}"
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
        self._layout.setSpacing(7)

        top = QHBoxLayout()
        self.name_label = QLabel("-")
        self.name_label.setObjectName("providerName")
        self.percent_label = QLabel("-")
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

        self.window_label = QLabel("-")
        self.window_label.setObjectName("metaLabel")
        self._layout.addWidget(self.window_label)

        self.detail_label = QLabel("-")
        self.detail_label.setObjectName("metaLabel")
        self.detail_label.setWordWrap(True)
        self._layout.addWidget(self.detail_label)

        self.secondary_label = QLabel("")
        self.secondary_label.setObjectName("metaLabel")
        self._layout.addWidget(self.secondary_label)

    def set_display_mode(self, mode: str) -> None:
        self._mode = mode if mode in ("standard", "compact", "minimal") else "standard"
        show_bar = self._mode != "minimal"
        show_meta = self._mode == "standard"
        self.bar.setVisible(show_bar)
        self.window_label.setVisible(show_meta)
        self.detail_label.setVisible(show_meta)
        self.secondary_label.setVisible(show_meta)

        if self._mode == "standard":
            self._layout.setContentsMargins(16, 14, 16, 14)
            self.bar.setFixedHeight(10)
            self.percent_label.setStyleSheet("font-size: 28px; font-weight: 750;")
        elif self._mode == "compact":
            self._layout.setContentsMargins(14, 10, 14, 10)
            self.bar.setFixedHeight(7)
            self.percent_label.setStyleSheet("font-size: 22px; font-weight: 750;")
        else:
            self._layout.setContentsMargins(14, 9, 14, 9)
            self.percent_label.setStyleSheet("font-size: 18px; font-weight: 750;")

    def update_snapshot(self, snap: ProviderSnapshot | None) -> None:
        if snap is None:
            self.name_label.setText("-")
            self.percent_label.setText("-")
            self.bar.setValue(0)
            self.window_label.setText("비활성")
            self.detail_label.setText("")
            self.secondary_label.setText("")
            return

        self.name_label.setText(snap.name)
        pct = snap.percent_clamped()
        self.percent_label.setText(f"{pct:.1f}%")
        self.bar.setValue(min(100, int(pct)))
        self.bar.setStyleSheet(
            f"QProgressBar::chunk {{ background: {bar_color(pct)}; border-radius: 5px; }}"
        )

        if not snap.available:
            self.window_label.setText("측정 불가")
            self.detail_label.setText(snap.note or snap.error or "")
            self.secondary_label.setText("")
            return

        used_txt = _fmt_number(snap.used, snap.unit)
        limit_txt = _fmt_number(snap.limit, snap.unit) if snap.limit else "-"
        self.window_label.setText(snap.window_label)
        self.detail_label.setText(
            f"사용 {used_txt} / 한도 {limit_txt} · 마지막 활동 {_fmt_age(snap.last_activity)}\n"
            f"{snap.note}"
        )

        bits: list[str] = []
        if snap.secondary_percent is not None and snap.secondary_label:
            bits.append(f"{snap.secondary_label}: {snap.secondary_percent:.1f}%")
        if snap.resets_at:
            bits.append(f"초기화: {_fmt_resets(snap.resets_at)}")
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
        self.setMinimumSize(520, 360)
        self.resize(700, 460)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, False)
        self._cards: dict[str, ProviderCard] = {}
        self._last_update: float = 0.0
        self._build()

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 14)
        outer.setSpacing(12)

        header = QHBoxLayout()
        title = QLabel("Token Status")
        title.setObjectName("title")
        self.subtitle = QLabel("실시간 대시보드")
        self.subtitle.setObjectName("subtitle")
        gear = QPushButton("설정")
        gear.setToolTip("계정 및 표시 설정 열기")
        gear.setMinimumWidth(64)
        gear.setFixedHeight(34)
        gear.clicked.connect(self._on_settings)
        refresh = QPushButton("새로고침")
        refresh.setToolTip("사용량 지금 갱신")
        refresh.setFixedHeight(34)
        refresh.clicked.connect(self._on_refresh)
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(self.subtitle)
        header.addSpacing(8)
        header.addWidget(refresh)
        header.addSpacing(6)
        header.addWidget(gear)
        outer.addLayout(header)

        summary = QFrame()
        summary.setObjectName("summaryBand")
        summary_layout = QHBoxLayout(summary)
        summary_layout.setContentsMargins(16, 12, 16, 12)
        summary_layout.setSpacing(18)
        self.max_label = QLabel("No data")
        self.max_label.setObjectName("summaryMetric")
        self.count_label = QLabel("0 active")
        self.count_label.setObjectName("summaryMeta")
        self.reset_label = QLabel("Reset time unknown")
        self.reset_label.setObjectName("summaryMeta")
        summary_layout.addWidget(self.max_label)
        summary_layout.addWidget(self.count_label)
        summary_layout.addStretch(1)
        summary_layout.addWidget(self.reset_label)
        outer.addWidget(summary)

        self.cards_wrap = QWidget()
        self.cards_layout = QGridLayout(self.cards_wrap)
        self.cards_layout.setContentsMargins(0, 0, 0, 0)
        self.cards_layout.setHorizontalSpacing(10)
        self.cards_layout.setVerticalSpacing(10)
        outer.addWidget(self.cards_wrap, 0, Qt.AlignmentFlag.AlignTop)
        outer.addStretch(1)

    def set_display_mode(self, mode: str) -> None:
        self._display_mode = mode
        for card in self._cards.values():
            card.set_display_mode(mode)

    def apply_snapshots(self, snapshots: dict) -> None:
        self._last_update = time.time()
        self.subtitle.setText(f"마지막 갱신 {datetime.now().strftime('%H:%M:%S')}")
        visible = [
            (str(k), s) for k, s in snapshots.items()
            if not str(k).startswith("_") and s is not None
        ]
        visible_keys = {k for k, _s in visible}

        for key, _snap in visible:
            if key not in self._cards:
                self._cards[key] = ProviderCard(key, display_mode=self._display_mode)
        for key in list(self._cards.keys()):
            if key not in visible_keys:
                card = self._cards.pop(key)
                self.cards_layout.removeWidget(card)
                card.deleteLater()

        while self.cards_layout.count():
            item = self.cards_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.setParent(None)

        active = [s for _k, s in visible if s.available]
        if active:
            max_snap = max(active, key=lambda s: s.percent_clamped())
            self.max_label.setText(
                f"최고 사용률 · {max_snap.name} {max_snap.percent_clamped():.1f}%"
            )
            self.count_label.setText(f"활성 계정 {len(active)}개")
            reset_candidates = [s for s in active if s.resets_at and s.resets_at > time.time()]
            next_reset = min(reset_candidates, key=lambda s: s.resets_at) if reset_candidates else None
            reset = _fmt_resets(next_reset.resets_at) if next_reset else ""
            self.reset_label.setText(
                f"가장 가까운 초기화 · {next_reset.name} {reset}"
                if reset and next_reset else "초기화 시간 없음"
            )
        else:
            self.max_label.setText("No data")
            self.count_label.setText("0 active")
            self.reset_label.setText("초기화 시간 없음")

        for idx, (key, snap) in enumerate(visible):
            card = self._cards[key]
            card.setVisible(True)
            card.update_snapshot(snap)
            self.cards_layout.addWidget(card, idx // 2, idx % 2)

    def show_near_tray(self) -> None:
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            self.show()
            self.activateWindow()
            return
        geom = screen.availableGeometry()
        w = min(max(self.minimumWidth(), self.width()), geom.width() - 24)
        content_h = 14 + 34 + 12 + 88 + 12 + self.cards_wrap.sizeHint().height() + 28
        h = min(max(self.minimumHeight(), content_h), geom.height() - 24)
        x = max(geom.left() + 12, geom.right() - w - 12)
        y = max(geom.top() + 12, geom.bottom() - h - 12)
        self.setGeometry(x, y, w, h)
        self.show()
        self.raise_()
        self.activateWindow()
