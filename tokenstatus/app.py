from __future__ import annotations

import sys
import time
from pathlib import Path

from PyQt6.QtCore import QObject, QSharedMemory, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QAction, QColor, QIcon, QPalette, QPixmap
from PyQt6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon

import logging

from . import claude_profile
from .config import Config
from .icon_renderer import render_tray_icon
from .logger import setup as setup_logging
from .monitor import Monitor
from .readers import ProviderSnapshot
from .ui.overlay_window import OverlayWindow
from .ui.settings_window import SettingsWindow
from .ui.status_window import StatusWindow
from .ui.style import GLOBAL_QSS

log = logging.getLogger(__name__)


class _Bridge(QObject):
    """Marshal snapshot updates from the monitor thread onto the Qt main thread."""
    snapshots_ready = pyqtSignal(dict)


class TokenStatusApp:
    RESTART_FLAG = "--restarted"

    def __init__(self, argv: list[str]) -> None:
        argv = list(argv)
        restarted = self.RESTART_FLAG in argv
        if restarted:
            argv.remove(self.RESTART_FLAG)
        log_file = setup_logging()
        log.info("=== TokenStatus starting ===")
        log.info(f"log file: {log_file}")

        self.qt = QApplication(argv)
        self.qt.setQuitOnLastWindowClosed(False)
        self.qt.setStyle("Fusion")
        palette = QPalette()
        palette.setColor(QPalette.ColorRole.Window, QColor("#F7F8FA"))
        palette.setColor(QPalette.ColorRole.WindowText, QColor("#1F2937"))
        palette.setColor(QPalette.ColorRole.Base, QColor("#FFFFFF"))
        palette.setColor(QPalette.ColorRole.AlternateBase, QColor("#F1F5F9"))
        palette.setColor(QPalette.ColorRole.ToolTipBase, QColor("#F8FAFC"))
        palette.setColor(QPalette.ColorRole.ToolTipText, QColor("#111827"))
        palette.setColor(QPalette.ColorRole.Text, QColor("#1F2937"))
        palette.setColor(QPalette.ColorRole.Button, QColor("#FFFFFF"))
        palette.setColor(QPalette.ColorRole.ButtonText, QColor("#1F2937"))
        palette.setColor(QPalette.ColorRole.Highlight, QColor("#FFEDD5"))
        palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#9A3412"))
        palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor("#94A3B8"))
        palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor("#94A3B8"))
        self.qt.setPalette(palette)
        self.qt.setStyleSheet(GLOBAL_QSS)

        # Single-instance guard. If the named segment already exists, another
        # instance is running; bail out cleanly.
        self._lock = QSharedMemory("TokenStatus_SingleInstance_v1")
        # 재시작으로 뜬 경우엔 이전 프로세스가 완전히 내려갈 때까지 잠깐 기다린다.
        deadline = time.time() + (5.0 if restarted else 0.0)
        while not self._lock.create(1):
            if time.time() >= deadline:
                self._already_running = True
                return
            time.sleep(0.2)
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

        # 계정 목록은 설정에서 바뀌므로 열릴 때마다 다시 만든다.
        self.menu_launch = QMenu("Claude 실행")
        self.menu_launch.aboutToShow.connect(self._rebuild_launch_menu)
        menu.addMenu(self.menu_launch)

        # 실행 중인 창들의 계정을 통째로 바꾼다 (새 창을 띄우지 않음).
        self.menu_switch = QMenu("계정 바꾸기")
        self.menu_switch.aboutToShow.connect(self._rebuild_switch_menu)
        menu.addMenu(self.menu_switch)

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

        self.action_restart = QAction("재시작")
        self.action_restart.setToolTip("설정을 저장하고 앱을 다시 실행합니다 (코드 수정 반영용)")
        self.action_restart.triggered.connect(self._restart_from_menu)
        menu.addAction(self.action_restart)

        menu.addSeparator()
        self.action_quit = QAction("종료")
        self.action_quit.triggered.connect(self._quit)
        menu.addAction(self.action_quit)

        self.tray_menu = menu
        self.tray.setContextMenu(menu)

    def _rebuild_launch_menu(self) -> None:
        """계정별 실행 + 마지막 대화를 다른 계정으로 이어받기."""
        self.menu_launch.clear()
        accounts = [a for a in self.cfg.providers if a.kind == "claude"]
        if not accounts:
            empty = QAction("(등록된 Claude 계정 없음)", self.menu_launch)
            empty.setEnabled(False)
            self.menu_launch.addAction(empty)
            return

        snapshots = self.monitor.snapshots
        for account in accounts:
            pc = account.config
            ready = claude_profile.credentials_exist(pc.claude_config_dir)
            name = account.label or account.kind.title()
            snap = snapshots.get(account.id)
            if snap is not None and snap.available:
                name += f"   {snap.percent_clamped():.0f}%"
            if not ready and not claude_profile.is_default(pc.claude_config_dir):
                name += "  — 로그인 필요"
            # 기본 프로필은 '계정 바꾸기' 로 내용이 바뀔 수 있다. 이름이 거짓말을
            # 하지 않도록, 지금 그 폴더에 실제로 들어있는 계정을 함께 보여준다.
            tip = pc.profile_status
            if claude_profile.is_default(pc.claude_config_dir):
                actual = claude_profile.identity_email("")
                expected = claude_profile.vault_email(account.id) or pc.account_email
                if actual and expected and actual != expected:
                    name += f"  ⚠ 지금은 {actual}"
                    tip = f"이 항목은 기본 프로필을 가리키는데, 현재 {actual} 로 바뀌어 있습니다."
                elif actual:
                    tip = tip or actual
            action = QAction(name, self.menu_launch)
            action.setEnabled(ready)
            if tip:
                action.setToolTip(tip)
            action.triggered.connect(
                lambda _checked=False, cd=pc.claude_config_dir, lbl=name:
                self._launch_profile(cd, lbl)
            )
            self.menu_launch.addAction(action)

        # 한도가 찬 계정에서 하던 대화를, 이미 로그인된 다른 계정으로 옮겨 이어 연다.
        # 자격증명은 건드리지 않고 대화 기록만 복사하므로 재승인이 없다.
        ready_accounts = [a for a in accounts
                          if claude_profile.credentials_exist(a.config.claude_config_dir)]
        if len(ready_accounts) < 2:
            return
        session = claude_profile.latest_session(
            [a.config.claude_config_dir for a in ready_accounts])
        if session is None:
            return

        self.menu_launch.addSeparator()
        header = QAction(f"마지막 대화: {session.title}", self.menu_launch)
        header.setEnabled(False)
        self.menu_launch.addAction(header)
        for account in ready_accounts:
            if account.config.claude_config_dir == session.config_dir:
                continue    # 이미 이 계정 것이다
            label = account.label or account.kind.title()
            action = QAction(f"    ↪ {label} 계정으로 이어받기", self.menu_launch)
            action.setToolTip(f"{session.cwd}\n대화를 복사해 {label} 계정으로 이어서 엽니다.")
            action.triggered.connect(
                lambda _checked=False, sess=session,
                cd=account.config.claude_config_dir, lbl=label:
                self._resume_in_profile(sess, cd, lbl)
            )
            self.menu_launch.addAction(action)

    def _resume_in_profile(self, session, config_dir: str, label: str) -> None:
        ok, err = claude_profile.resume_in_profile(session, config_dir)
        if ok:
            log.info("대화 이어받기: %s → %s", session.id, label)
            self.tray.showMessage(
                "Token Status",
                f"{label} 계정으로 대화를 이어서 열었습니다.",
                QSystemTrayIcon.MessageIcon.Information, 3000)
        else:
            log.error("이어받기 실패 (%s): %s", label, err)
            self.tray.showMessage(
                "Token Status", f"이어받기 실패: {err}",
                QSystemTrayIcon.MessageIcon.Warning, 5000)

    def _launch_profile(self, config_dir: str, label: str) -> None:
        ok, err = claude_profile.launch(config_dir)
        if ok:
            log.info("Claude 실행: %s", label)
            self.tray.showMessage(
                "Token Status", f"{label} 프로필로 Claude Code 를 실행했습니다.",
                QSystemTrayIcon.MessageIcon.Information, 3000)
        else:
            log.error("Claude 실행 실패 (%s): %s", label, err)
            self.tray.showMessage(
                "Token Status", f"실행 실패: {err}",
                QSystemTrayIcon.MessageIcon.Warning, 5000)

    def _rebuild_switch_menu(self) -> None:
        """지금 떠 있는 Claude 창 전부의 계정을 바꾼다.

        Claude Code 는 요청할 때마다 자격증명 파일을 다시 읽으므로, 기본
        프로필(~/.claude)의 자격증명과 계정정보를 함께 갈아끼우면 이미 열려
        있는 창들도 다음 요청부터 그 계정으로 동작한다.
        """
        self.menu_switch.clear()
        accounts = [a for a in self.cfg.providers if a.kind == "claude"]
        current_email = claude_profile.identity_email("")

        current_slot = ""
        rows = []
        for account in accounts:
            slot = account.id
            if not claude_profile.vault_has(slot):
                # 이 계정의 프로필에 로그인이 있으면 금고에 담아둔다.
                claude_profile.vault_capture(slot, account.config.claude_config_dir)
            email = claude_profile.vault_email(slot) or account.config.account_email
            is_current = bool(email) and email == current_email
            if is_current:
                current_slot = slot
            rows.append((account, slot, email, is_current))

        usable = [r for r in rows if claude_profile.vault_has(r[1])]
        if not usable:
            empty = QAction("(저장된 계정 없음 — 각 계정으로 한 번씩 로그인하세요)",
                            self.menu_switch)
            empty.setEnabled(False)
            self.menu_switch.addAction(empty)
            return

        for account, slot, email, is_current in usable:
            mark = "✓ " if is_current else "     "
            name = f"{mark}{account.label or account.kind.title()}"
            if email:
                name += f"   {email}"
            action = QAction(name, self.menu_switch)
            action.setEnabled(not is_current)
            action.setToolTip("지금 열려 있는 모든 Claude 창이 이 계정으로 바뀝니다."
                              if not is_current else "지금 사용 중인 계정입니다.")
            action.triggered.connect(
                lambda _c=False, sl=slot, lbl=(account.label or slot), cur=current_slot:
                self._switch_account(sl, lbl, cur))
            self.menu_switch.addAction(action)

        self.menu_switch.addSeparator()
        undo = QAction("방금 바꾼 것 되돌리기", self.menu_switch)
        undo.triggered.connect(self._undo_switch)
        self.menu_switch.addAction(undo)

    def _switch_account(self, slot: str, label: str, current_slot: str) -> None:
        ok, err = claude_profile.switch_account(slot, "", capture_slot=current_slot)
        if ok:
            log.info("계정 교체: → %s", label)
            self.tray.showMessage(
                "Token Status",
                f"{label} 계정으로 바꿨습니다.\n"
                f"열려 있는 Claude 창은 다음 메시지부터 이 계정으로 동작합니다.",
                QSystemTrayIcon.MessageIcon.Information, 4000)
            self.monitor.refresh_now()
        else:
            log.error("계정 교체 실패 (%s): %s", label, err)
            self.tray.showMessage("Token Status", f"계정 교체 실패: {err}",
                                  QSystemTrayIcon.MessageIcon.Warning, 6000)

    def _undo_switch(self) -> None:
        ok, err = claude_profile.undo_switch("")
        msg = ("계정을 이전 상태로 되돌렸습니다." if ok else f"되돌리기 실패: {err}")
        self.tray.showMessage(
            "Token Status", msg,
            QSystemTrayIcon.MessageIcon.Information if ok
            else QSystemTrayIcon.MessageIcon.Warning, 4000)
        if ok:
            self.monitor.refresh_now()

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
            on_restart=self._restart,
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
            on_visibility_changed=self._save_overlay_visibility,
            on_size_changed=self._save_overlay_size,
            on_lock_changed=self._set_overlay_locked,
            opacity=self.cfg.overlay_opacity,
            locked=self.cfg.overlay_locked,
            visible_provider_ids=set(self.cfg.overlay_visible_ids),
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

    def _save_overlay_visibility(self, visible_ids: set[str]) -> None:
        self.cfg.overlay_visible_ids = sorted(visible_ids)
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

    def _restart_from_menu(self) -> None:
        """트레이 메뉴에서의 재시작 — 현재 설정을 먼저 저장하고 다시 띄운다."""
        try:
            self.cfg.save()
        except OSError:
            log.exception("restart: config save failed")
        try:
            self._restart()
        except Exception as e:  # noqa: BLE001
            log.exception("restart failed")
            QMessageBox.critical(None, "재시작 실패", str(e))

    def _restart(self) -> None:
        """현재 프로세스를 새로 띄우고 자신은 종료한다.

        소스를 고친 뒤 트레이에서 바로 반영하려고 쓴다. pythonw.exe 로 떠 있으면
        같은 인터프리터로, 빌드된 exe 면 그 exe 로 다시 실행한다.
        """
        import subprocess

        # 자식이 단일 인스턴스 가드에 막히지 않도록 우리 쪽 세그먼트를 먼저 놓아준다.
        self._lock.detach()

        if getattr(sys, "frozen", False):
            cmd = [sys.executable] + sys.argv[1:]
        else:
            launcher = Path(__file__).resolve().parent.parent / "launcher.py"
            cmd = ([sys.executable, str(launcher)] if launcher.is_file()
                   else [sys.executable, "-m", "tokenstatus"]) + sys.argv[1:]
        if self.RESTART_FLAG not in cmd:
            cmd.append(self.RESTART_FLAG)

        flags = 0
        if sys.platform.startswith("win"):
            # 부모가 죽어도 살아남도록 새 프로세스 그룹으로 띄운다.
            flags = getattr(subprocess, "DETACHED_PROCESS", 0) | \
                getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        subprocess.Popen(cmd, cwd=str(Path(__file__).resolve().parent.parent),
                         close_fds=True, creationflags=flags)
        self._quit()

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
        for key, snap in snapshots.items():
            if str(key).startswith("_") or not snap:
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
