"""Always-on-top HUD overlay.

Frameless, semi-transparent, draggable. Shows each enabled provider as a single
row with name, a gradient usage bar, and percentage.
"""
from __future__ import annotations

from typing import Callable

from PyQt6.QtCore import QPoint, QPointF, QRect, Qt
from PyQt6.QtGui import (
    QAction,
    QColor,
    QFont,
    QFontDatabase,
    QFontMetrics,
    QGuiApplication,
    QLinearGradient,
    QMouseEvent,
    QPainter,
    QPaintEvent,
)
from PyQt6.QtWidgets import QMenu, QWidget

from ..readers import ProviderSnapshot


# Short display names for compactness
_NAMES = {
    "claude": "Claude",
    "codex": "Codex",
    "gemini": "Gemini",
}


def _bar_gradient(percent: float, rect: QRect) -> QLinearGradient:
    """Vertical gradient: bright top, darker bottom. Color shifts with usage level."""
    if percent >= 90:
        top, bot = QColor(255, 99, 99), QColor(180, 30, 30)
    elif percent >= 70:
        top, bot = QColor(255, 196, 90), QColor(200, 130, 20)
    else:
        top, bot = QColor(120, 220, 120), QColor(40, 140, 40)
    g = QLinearGradient(QPointF(rect.topLeft()), QPointF(rect.bottomLeft()))
    g.setColorAt(0.0, top)
    g.setColorAt(1.0, bot)
    return g


class OverlayWindow(QWidget):
    # Edge-grab thickness in pixels for resize handles around the borderless window.
    _RESIZE_MARGIN = 6

    def __init__(
        self,
        *,
        on_open_settings: Callable[[], None],
        on_open_status: Callable[[], None],
        on_switch_mode: Callable[[str], None],
        on_quit: Callable[[], None],
        on_position_changed: Callable[[int, int], None],
        on_size_changed: Callable[[int, int], None] | None = None,
        on_lock_changed: Callable[[bool], None] | None = None,
        opacity: float = 0.85,
        locked: bool = False,
        width: int = 220,
        height: int = 96,
    ) -> None:
        super().__init__()
        self._on_open_settings = on_open_settings
        self._on_open_status = on_open_status
        self._on_switch_mode = on_switch_mode
        self._on_quit = on_quit
        self._on_position_changed = on_position_changed
        self._on_size_changed = on_size_changed
        self._on_lock_changed = on_lock_changed
        self._locked = locked
        self._snapshots: dict[str, ProviderSnapshot] = {}
        self._drag_offset: QPoint | None = None
        self._resize_edge: str | None = None
        self._resize_start_geo: QRect | None = None
        self._resize_start_global: QPoint | None = None

        self.setWindowFlags(self._compose_flags(locked))
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setWindowOpacity(opacity)
        self.setMouseTracking(True)
        self.setMinimumSize(140, 50)

        self.resize(max(width, 140), max(height, 50))

    # ---------- public API ----------
    def set_snapshots(self, snapshots: dict[str, ProviderSnapshot]) -> None:
        self._snapshots = snapshots
        self.update()

    def set_opacity(self, opacity: float) -> None:
        self.setWindowOpacity(max(0.2, min(1.0, opacity)))

    @staticmethod
    def _compose_flags(locked: bool) -> "Qt.WindowType":
        flags = (
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        if locked:
            # Click-through: mouse events pass straight to the window behind it,
            # so a locked overlay never blocks what's underneath.
            flags |= Qt.WindowType.WindowTransparentForInput
        return flags

    def set_locked(self, locked: bool) -> None:
        if locked == self._locked and self.windowFlags() == self._compose_flags(locked):
            self._locked = locked
            return
        self._locked = locked
        was_visible = self.isVisible()
        geo = self.geometry()
        # Toggling this flag forces the native window to be recreated; preserve
        # geometry and re-show so the overlay doesn't vanish or jump.
        self.setWindowFlags(self._compose_flags(locked))
        self.setGeometry(geo)
        if locked:
            self.unsetCursor()
        if was_visible:
            self.show()

    def place_default(self) -> None:
        """If config has no saved position, anchor to top-right of primary screen."""
        screen = QGuiApplication.primaryScreen()
        if not screen:
            return
        geom = screen.availableGeometry()
        x = geom.right() - self.width() - 16
        y = geom.top() + 16
        self.move(x, y)

    def _snap_to_default(self) -> None:
        """Move to the default spot and persist the resulting position."""
        self.place_default()
        pos = self.pos()
        self._on_position_changed(pos.x(), pos.y())

    def reset_position(self) -> None:
        """Force the overlay back to the top-right of the primary screen."""
        self._snap_to_default()

    def ensure_on_screen(self) -> None:
        """Snap back to default if the window isn't meaningfully visible on any
        screen — e.g. a monitor was disconnected or the resolution changed and the
        saved position now lands in dead space."""
        frame = self.frameGeometry()
        for screen in QGuiApplication.screens():
            inter = screen.availableGeometry().intersected(frame)
            # Require a visible chunk so a 1px sliver doesn't count as "on screen".
            if inter.width() >= 48 and inter.height() >= 24:
                return
        self._snap_to_default()

    # ---------- painting ----------
    @staticmethod
    def _pick_font_family() -> str:
        # Prefer Windows 11's variable font, fall back gracefully.
        families = set(QFontDatabase.families())
        for cand in ("Segoe UI Variable Display", "Segoe UI Variable",
                     "Segoe UI", "Inter", "Arial"):
            if cand in families:
                return cand
        return "Sans Serif"

    def paintEvent(self, event: QPaintEvent) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        bg_rect = self.rect().adjusted(0, 0, -1, -1)
        p.setBrush(QColor(18, 22, 32, 220))
        p.setPen(QColor(255, 255, 255, 45))
        p.drawRoundedRect(bg_rect, 10, 10)

        rows = [
            (k, s) for k, s in self._snapshots.items()
            if s is not None and s.available
        ]
        n = max(1, len(rows))
        pad_v = max(4, self.height() // 14)
        pad_h = max(8, self.width() // 18)
        avail_h = self.height() - 2 * pad_v
        # Scale row height freely with window — no upper cap so resizing actually does something.
        row_h = max(14, avail_h // n)

        # Font size = ~45% of row height. Will be shrunk below to fit name column.
        font_pt = max(8, int(row_h * 0.45))
        family = self._pick_font_family()
        name_font = QFont(family, font_pt)
        name_font.setWeight(QFont.Weight.Medium)
        name_font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
        pct_font = QFont(family, font_pt)
        pct_font.setWeight(QFont.Weight.DemiBold)
        pct_font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)

        # Column widths as proportion of window width.
        name_w = max(50, int(self.width() * 0.28))
        pct_w = max(40, int(self.width() * 0.20))

        # Shrink font until every provider name fits in name_w.
        names_to_fit = [_NAMES.get(k, s.name) for k, s in rows] or ["Claude"]
        while font_pt > 7:
            metrics = QFontMetrics(name_font)
            widest = max(metrics.horizontalAdvance(name) for name in names_to_fit)
            if widest <= name_w - 6:
                break
            font_pt -= 1
            name_font.setPointSize(font_pt)
            pct_font.setPointSize(font_pt)

        # Bar height proportional to row, but with a sensible floor & ceiling.
        bar_h = max(5, min(int(row_h * 0.28), max(8, row_h - font_pt - 4)))
        bar_left = pad_h + name_w + 6
        bar_right = self.width() - pad_h - pct_w - 6

        if not rows:
            p.setPen(QColor(200, 200, 220, 200))
            p.setFont(QFont(family, font_pt))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "측정 중...")
            return

        used_h = row_h * n
        y = pad_v + max(0, (avail_h - used_h) // 2)
        radius = bar_h // 2  # pill shape

        for key, snap in rows:
            name = _NAMES.get(key, snap.name)
            pct = snap.percent_clamped()

            name_rect = QRect(pad_h, y, name_w, row_h)
            p.setFont(name_font)
            p.setPen(QColor(232, 235, 245, 240))
            p.drawText(name_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, name)

            bar_y = y + (row_h - bar_h) // 2
            bar_rect = QRect(bar_left, bar_y, max(20, bar_right - bar_left), bar_h)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(0, 0, 0, 130))
            p.drawRoundedRect(bar_rect, radius, radius)
            fill_w = int(bar_rect.width() * min(1.0, pct / 100.0))
            if fill_w > 0:
                fill_rect = QRect(bar_rect.x(), bar_rect.y(),
                                  max(bar_h, fill_w), bar_rect.height())
                p.setBrush(_bar_gradient(pct, fill_rect))
                p.drawRoundedRect(fill_rect, radius, radius)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QColor(255, 255, 255, 35))
            p.drawRoundedRect(bar_rect, radius, radius)

            pct_rect = QRect(bar_right + 6, y, pct_w, row_h)
            p.setFont(pct_font)
            p.setPen(QColor(255, 255, 255, 248))
            p.drawText(pct_rect, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                       f"{pct:.0f}%")

            y += row_h

    # ---------- interaction ----------
    def _edge_at(self, pos: QPoint) -> str | None:
        m = self._RESIZE_MARGIN
        x, y = pos.x(), pos.y()
        w, h = self.width(), self.height()
        on_l = x <= m
        on_r = x >= w - m
        on_t = y <= m
        on_b = y >= h - m
        if on_l and on_t: return "tl"
        if on_r and on_t: return "tr"
        if on_l and on_b: return "bl"
        if on_r and on_b: return "br"
        if on_l: return "l"
        if on_r: return "r"
        if on_t: return "t"
        if on_b: return "b"
        return None

    _CURSOR_MAP = {
        "l": Qt.CursorShape.SizeHorCursor,
        "r": Qt.CursorShape.SizeHorCursor,
        "t": Qt.CursorShape.SizeVerCursor,
        "b": Qt.CursorShape.SizeVerCursor,
        "tl": Qt.CursorShape.SizeFDiagCursor,
        "br": Qt.CursorShape.SizeFDiagCursor,
        "tr": Qt.CursorShape.SizeBDiagCursor,
        "bl": Qt.CursorShape.SizeBDiagCursor,
    }

    def _update_cursor(self, pos: QPoint) -> None:
        if self._locked:
            self.unsetCursor()
            return
        edge = self._edge_at(pos)
        if edge:
            self.setCursor(self._CURSOR_MAP[edge])
        else:
            self.unsetCursor()

    def mousePressEvent(self, e: QMouseEvent) -> None:
        if e.button() != Qt.MouseButton.LeftButton or self._locked:
            return
        edge = self._edge_at(e.position().toPoint())
        if edge:
            self._resize_edge = edge
            self._resize_start_geo = QRect(self.geometry())
            self._resize_start_global = e.globalPosition().toPoint()
        else:
            self._drag_offset = e.globalPosition().toPoint() - self.frameGeometry().topLeft()
        e.accept()

    def mouseMoveEvent(self, e: QMouseEvent) -> None:
        if self._resize_edge is not None and self._resize_start_geo is not None:
            self._apply_resize(e.globalPosition().toPoint())
            e.accept()
            return
        if self._drag_offset is not None and e.buttons() & Qt.MouseButton.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag_offset)
            e.accept()
            return
        # Hover — update cursor based on edge proximity
        self._update_cursor(e.position().toPoint())

    def _apply_resize(self, global_pos: QPoint) -> None:
        assert self._resize_start_geo is not None and self._resize_start_global is not None
        dx = global_pos.x() - self._resize_start_global.x()
        dy = global_pos.y() - self._resize_start_global.y()
        start = self._resize_start_geo
        x, y, w, h = start.x(), start.y(), start.width(), start.height()
        edge = self._resize_edge or ""
        min_w, min_h = self.minimumSize().width(), self.minimumSize().height()
        if "l" in edge:
            new_w = max(min_w, w - dx)
            x = x + (w - new_w)
            w = new_w
        elif "r" in edge:
            w = max(min_w, w + dx)
        if "t" in edge:
            new_h = max(min_h, h - dy)
            y = y + (h - new_h)
            h = new_h
        elif "b" in edge:
            h = max(min_h, h + dy)
        self.setGeometry(x, y, w, h)

    def mouseReleaseEvent(self, e: QMouseEvent) -> None:
        if self._resize_edge is not None:
            self._resize_edge = None
            self._resize_start_geo = None
            self._resize_start_global = None
            if self._on_size_changed:
                self._on_size_changed(self.width(), self.height())
            pos = self.pos()
            self._on_position_changed(pos.x(), pos.y())
            e.accept()
            return
        if self._drag_offset is not None:
            pos = self.pos()
            self._on_position_changed(pos.x(), pos.y())
            self._drag_offset = None
            e.accept()

    def mouseDoubleClickEvent(self, e: QMouseEvent) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self._on_open_settings()
            e.accept()

    def leaveEvent(self, e) -> None:
        self.unsetCursor()

    def contextMenuEvent(self, event) -> None:
        menu = QMenu(self)
        menu.addAction("상태창 열기", self._on_open_status)
        menu.addSeparator()
        lock = QAction("위치 잠금" if not self._locked else "위치 잠금 해제", self)
        lock.triggered.connect(self._toggle_lock)
        menu.addAction(lock)
        menu.addAction("위치 초기화 (우상단으로)", self.reset_position)
        menu.addSeparator()
        switch = menu.addMenu("표시 모드 변경")
        switch.addAction("표준", lambda: self._on_switch_mode("standard"))
        switch.addAction("간소", lambda: self._on_switch_mode("compact"))
        switch.addAction("최소", lambda: self._on_switch_mode("minimal"))
        menu.addSeparator()
        menu.addAction("설정...", self._on_open_settings)
        menu.addSeparator()
        menu.addAction("종료", self._on_quit)
        menu.exec(event.globalPos())

    def _toggle_lock(self) -> None:
        new_locked = not self._locked
        if self._on_lock_changed is not None:
            # Let the app be the single source of truth (persist + apply + sync tray).
            self._on_lock_changed(new_locked)
        else:
            self.set_locked(new_locked)
