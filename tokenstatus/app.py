from __future__ import annotations

import sys

from PyQt6.QtCore import QObject, QSharedMemory, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QAction, QIcon, QPixmap
from PyQt6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon

import logging

from .config import Config
from .icon_renderer import render_tray_icon
from .logger import setup as setup_logging
from .monitor import Monitor
from .readers import ProviderSnapshot
from .ui.overlay_window import OverlayWindow
from .ui.settings_window import SettingsWindow
from .ui.status_window import StatusWindow

log = logging.getLogger(__name__)


class _Bridge(QObject):
    """Marshal snapshot updates from the monitor thread onto the Qt main thread."""
    snapshots_ready = pyqtSignal(dict)


class TokenStatusApp:
    def __init__(self, argv: list[str]) -> None:
        log_file = setup_logging()
        log.info("=== TokenStatus starting ===")
        log.info(f"log file: {log_file}")

        self.qt = QApplication(argv)
        self.qt.setQuitOnLastWindowClosed(False)

        # Single-instance guard. If the named segment already exists, another
        # instance is running; bail out cleanly.
        self._lock = QSharedMemory("TokenStatus_SingleInstance_v1")
        if not self._lock.create(1):
            self._already_running = True
            return
        self._already_running = False

        self.cfg = Config.load()
        self.monitor = Monitor(lambda: self.cfg)
        self.bridge = _Bridge()
        self.bridge.snapshots_ready.connect(self._on_snapshots)
        self.monitor.add_listener(lambda snaps: self.bridge.snapshots_ready.emit(snaps))

        self.status_window = StatusWindow(
            on_settings=self._show_settings,
            on_refresh=self._refresh_now,
            display_mode=self.cfg.display_mode if self.cfg.display_mode != "overlay" else "standard",
        )
        self.settings_window: SettingsWindow | None = None
        self.overlay_window: OverlayWindow | None = None

        app_icon = self._icon_for(None)
        self.qt.setWindowIcon(app_icon)
        self.status_window.setWindowIcon(app_icon)

        self.tray = QSystemTrayIcon()
        self.tray.setIcon(app_icon)
        self.tray.setToolTip("Token Status — 측정 중…")
        self.tray.activated.connect(self._on_tray_activated)
        self._build_tray_menu()
        self.tray.show()

        # Recover the overlay automatically when monitors/resolution change.
        self._wire_screen_watch()

    # ---------- tray ----------
    def _build_tray_menu(self) -> None:
        menu = QMenu()

        self.action_open = QAction("상태창 열기")
        self.action_open.triggered.connect(self._show_status)
        menu.addAction(self.action_open)

        self.action_refresh = QAction("지금 새로고침")
        self.action_refresh.triggered.connect(self._refresh_now)
        menu.addAction(self.action_refresh)

        self.action_reset_overlay = QAction("오버레이 위치 초기화")
        self.action_reset_overlay.setToolTip(
            "모니터를 바꾸거나 해상도가 변해 오버레이가 사라졌을 때 우상단으로 되돌립니다"
        )
        self.action_reset_overlay.triggered.connect(self._reset_overlay_pos)
        menu.addAction(self.action_reset_overlay)

        self.action_lock = QAction("오버레이 잠금 (클릭 통과)")
        self.action_lock.setCheckable(True)
        self.action_lock.setChecked(self.cfg.overlay_locked)
        self.action_lock.setToolTip(
            "잠그면 오버레이가 마우스 클릭을 통과시켜 뒤에 있는 창을 정상적으로 누를 수 있습니다"
        )
        self.action_lock.toggled.connect(self._set_overlay_locked)
        menu.addAction(self.action_lock)

        menu.addSeparator()

        self.action_settings = QAction("설정…")
        self.action_settings.triggered.connect(self._show_settings)
        menu.addAction(self.action_settings)

        menu.addSeparator()
        self.action_quit = QAction("종료")
        self.action_quit.triggered.connect(self._quit)
        menu.addAction(self.action_quit)

        self.tray_menu = menu
        self.tray.setContextMenu(menu)

    def _icon_for(self, percent: float | None) -> QIcon:
        png_bytes = render_tray_icon(percent, size=64)
        pixmap = QPixmap()
        pixmap.loadFromData(png_bytes, "PNG")
        return QIcon(pixmap)

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            if self.cfg.show_window_on_click:
                self._toggle_status()

    # ---------- windows ----------
    def _show_status(self) -> None:
        self.status_window.show_near_tray()

    def _toggle_status(self) -> None:
        if self.status_window.isVisible():
            self.status_window.hide()
        else:
            self._show_status()

    def _show_settings(self) -> None:
        if self.settings_window is not None and self.settings_window.isVisible():
            self.settings_window.raise_()
            self.settings_window.activateWindow()
            return
        self.settings_window = SettingsWindow(
            self.cfg,
            on_save=self._on_settings_saved,
            on_reset_overlay=self._reset_overlay_pos,
        )
        self.settings_window.show()

    def _on_settings_saved(self) -> None:
        try:
            log.info(
                "settings saved: mode=%s use_api=%s org=%s",
                self.cfg.display_mode, self.cfg.claude.use_api,
                (self.cfg.claude.org_id[:8] + "...") if self.cfg.claude.org_id else "(none)",
            )
            self._apply_display_mode()
            self._refresh_now()
        except Exception:
            log.exception("_on_settings_saved failed")
            raise

    def _apply_display_mode(self) -> None:
        mode = self.cfg.display_mode
        if mode == "overlay":
            self.status_window.hide()
            self._ensure_overlay()
            assert self.overlay_window is not None
            self.overlay_window.set_opacity(self.cfg.overlay_opacity)
            self.overlay_window.set_locked(self.cfg.overlay_locked)
            if hasattr(self, "action_lock") and self.action_lock.isChecked() != self.cfg.overlay_locked:
                self.action_lock.blockSignals(True)
                self.action_lock.setChecked(self.cfg.overlay_locked)
                self.action_lock.blockSignals(False)
            self.overlay_window.show()
            # If the saved position is now off-screen, pull it back into view.
            self.overlay_window.ensure_on_screen()
        else:
            if self.overlay_window is not None:
                self.overlay_window.hide()
            self.status_window.set_display_mode(mode)

    def _ensure_overlay(self) -> None:
        if self.overlay_window is not None:
            return
        self.overlay_window = OverlayWindow(
            on_open_settings=self._show_settings,
            on_open_status=self._show_status_force,
            on_switch_mode=self._switch_mode_from_overlay,
            on_quit=self._quit,
            on_position_changed=self._save_overlay_pos,
            on_size_changed=self._save_overlay_size,
            on_lock_changed=self._set_overlay_locked,
            opacity=self.cfg.overlay_opacity,
            locked=self.cfg.overlay_locked,
            width=self.cfg.overlay_w,
            height=self.cfg.overlay_h,
        )
        if self.cfg.overlay_x >= 0 and self.cfg.overlay_y >= 0:
            self.overlay_window.move(self.cfg.overlay_x, self.cfg.overlay_y)
        else:
            self.overlay_window.place_default()

    def _save_overlay_pos(self, x: int, y: int) -> None:
        self.cfg.overlay_x = x
        self.cfg.overlay_y = y
        try:
            self.cfg.save()
        except OSError:
            pass

    def _save_overlay_size(self, w: int, h: int) -> None:
        self.cfg.overlay_w = w
        self.cfg.overlay_h = h
        try:
            self.cfg.save()
        except OSError:
            pass

    def _reset_overlay_pos(self) -> None:
        """Snap the overlay back to the top-right of the primary screen.

        Reachable from the tray even when the overlay is off-screen and can't be
        right-clicked. If the overlay isn't live yet (non-overlay mode), just clear
        the saved coords so it lands at the default next time it appears.
        """
        if self.overlay_window is not None:
            self.overlay_window.reset_position()
            self.overlay_window.show()
            self.overlay_window.raise_()
        else:
            self.cfg.overlay_x = -1
            self.cfg.overlay_y = -1
            try:
                self.cfg.save()
            except OSError:
                pass

    def _set_overlay_locked(self, locked: bool) -> None:
        """Single source of truth for the overlay lock state. Persists the config,
        applies click-through to the live overlay, and keeps the tray toggle in sync.
        Reachable from the tray, the overlay context menu, and the settings window.
        """
        locked = bool(locked)
        self.cfg.overlay_locked = locked
        if self.overlay_window is not None:
            self.overlay_window.set_locked(locked)
        if hasattr(self, "action_lock") and self.action_lock.isChecked() != locked:
            self.action_lock.blockSignals(True)
            self.action_lock.setChecked(locked)
            self.action_lock.blockSignals(False)
        try:
            self.cfg.save()
        except OSError:
            pass

    def _on_screens_changed(self, *_) -> None:
        """A monitor was added/removed or its geometry changed. Re-check the overlay
        after a short delay so Qt has finished settling the new screen layout."""
        if self.overlay_window is not None and self.overlay_window.isVisible():
            QTimer.singleShot(200, self.overlay_window.ensure_on_screen)

    def _watch_screen(self, screen) -> None:
        screen.geometryChanged.connect(self._on_screens_changed)
        screen.availableGeometryChanged.connect(self._on_screens_changed)

    def _wire_screen_watch(self) -> None:
        """Auto-recover the overlay when the display configuration changes."""
        self.qt.screenRemoved.connect(self._on_screens_changed)
        self.qt.primaryScreenChanged.connect(self._on_screens_changed)
        self.qt.screenAdded.connect(self._on_screen_added)
        for screen in self.qt.screens():
            self._watch_screen(screen)

    def _on_screen_added(self, screen) -> None:
        self._watch_screen(screen)
        self._on_screens_changed()

    def _switch_mode_from_overlay(self, mode: str) -> None:
        self.cfg.display_mode = mode
        try:
            self.cfg.save()
        except OSError:
            pass
        self._apply_display_mode()
        if mode != "overlay":
            self._show_status_force()

    def _show_status_force(self) -> None:
        # Force show even when in overlay mode (e.g. user clicked tray or context menu).
        self.status_window.show_near_tray()

    def _refresh_now(self) -> None:
        self.monitor.refresh_now()

    def _quit(self) -> None:
        self.monitor.stop()
        self.tray.hide()
        self.qt.quit()

    # ---------- updates ----------
    def _on_snapshots(self, snapshots: dict) -> None:
        self.status_window.apply_snapshots(snapshots)
        if self.overlay_window is not None and self.overlay_window.isVisible():
            self.overlay_window.set_snapshots(snapshots)

        provider_snaps: list[ProviderSnapshot] = [
            s for k, s in snapshots.items() if not k.startswith("_") and s.available
        ]
        percents = [s.percent_clamped() for s in provider_snaps]
        if percents:
            agg = max(percents) if self.cfg.tray_show_max_only else sum(percents) / len(percents)
        else:
            agg = None

        self.tray.setIcon(self._icon_for(agg))

        # Tooltip with full breakdown.
        lines = []
        for key in ("claude", "codex", "gemini"):
            snap = snapshots.get(key)
            if not snap:
                continue
            if not snap.available:
                lines.append(f"{snap.name}: 측정 불가")
            else:
                lines.append(f"{snap.name}: {snap.percent_clamped():.1f}%")
        if agg is not None:
            label = "최대" if self.cfg.tray_show_max_only else "평균"
            header = f"Token Status — {label} {agg:.1f}%"
        else:
            header = "Token Status — 데이터 없음"
        self.tray.setToolTip(header + "\n" + "\n".join(lines))

    # ---------- run ----------
    def run(self, *, show_window: bool) -> int:
        if self._already_running:
            # Brief notification then exit so double-clicks don't spawn a 2nd tray icon.
            QMessageBox.information(
                None, "Token Status",
                "Token Status가 이미 실행 중입니다. 트레이 아이콘을 확인하세요."
            )
            return 0
        if not QSystemTrayIcon.isSystemTrayAvailable():
            QMessageBox.critical(
                None, "Token Status", "이 시스템에 시스템 트레이가 없어 실행할 수 없습니다."
            )
            return 2
        self.monitor.start()
        if self.cfg.display_mode == "overlay":
            self._apply_display_mode()
        elif show_window:
            QTimer.singleShot(200, self._show_status)
        return self.qt.exec()


def main(argv: list[str] | None = None) -> int:
    argv = list(argv if argv is not None else sys.argv)
    tray_only = "--tray" in argv
    if tray_only:
        argv.remove("--tray")
    app = TokenStatusApp(argv)
    return app.run(show_window=not tray_only)
