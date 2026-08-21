from __future__ import annotations

from pathlib import Path
from uuid import uuid4
from typing import Callable

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)
from PyQt6.QtGui import QGuiApplication

from .. import autostart
from ..config import AccountConfig, CLAUDE_PRESETS, GEMINI_PRESETS, Config, ProviderConfig
from ..readers import read_claude
from ..readers.claude_api import (
    delete_session_key,
    discover_org,
    fetch_usage,
    load_session_key,
    save_session_key,
)
from .style import ACCENT, GLOBAL_QSS, MUTED


def _style_combo_popup(combo: QComboBox) -> None:
    combo.view().setStyleSheet(
        "QAbstractItemView { background: #FFFFFF; color: #1F2937; "
        "border: 1px solid #94A3B8; outline: none; padding: 4px; } "
        "QAbstractItemView::item { min-height: 30px; padding: 4px 8px; } "
        "QAbstractItemView::item:selected { background: #FFEDD5; color: #9A3412; }"
    )


class ProviderChoiceRow(QFrame):
    selected = pyqtSignal(str)

    def __init__(self, kind: str, name: str, description: str) -> None:
        super().__init__()
        self.kind = kind
        self.setObjectName("providerChoiceRow")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(72)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 11, 14, 11)
        layout.setSpacing(12)

        badge = QLabel(name[0])
        badge.setObjectName(f"providerBadge_{kind}")
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setFixedSize(38, 38)
        layout.addWidget(badge)

        copy = QVBoxLayout()
        copy.setSpacing(2)
        name_label = QLabel(name)
        name_label.setObjectName("providerChoiceName")
        description_label = QLabel(description)
        description_label.setObjectName("providerChoiceDescription")
        copy.addWidget(name_label)
        copy.addWidget(description_label)
        layout.addLayout(copy, 1)

        action = QLabel("선택")
        action.setObjectName("providerChoiceAction")
        layout.addWidget(action)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self.selected.emit(self.kind)
            event.accept()
            return
        super().mouseReleaseEvent(event)


class AccountTypeDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.selected_kind: str | None = None
        self.setWindowTitle("계정 추가")
        self.setModal(True)
        self.setObjectName("accountTypeDialog")
        self.setStyleSheet(GLOBAL_QSS)
        self.setFixedWidth(460)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 22, 24, 20)
        outer.setSpacing(16)

        title = QLabel("서비스 선택")
        title.setObjectName("dialogTitle")
        outer.addWidget(title)

        subtitle = QLabel("추가할 계정의 서비스를 선택하세요.")
        subtitle.setObjectName("subtitle")
        outer.addWidget(subtitle)

        choices = QVBoxLayout()
        choices.setSpacing(8)
        for kind, name, description in (
            ("claude", "Claude", "Claude Code 사용량 및 초기화 시간"),
            ("codex", "Codex", "Codex 세션이 보고한 공식 사용률"),
            ("gemini", "Gemini", "Gemini CLI 메시지 사용량 추정"),
        ):
            row = ProviderChoiceRow(kind, name, description)
            row.selected.connect(self._select)
            choices.addWidget(row)
        outer.addLayout(choices)

        cancel = QPushButton("취소")
        cancel.clicked.connect(self.reject)
        outer.addWidget(cancel, 0, Qt.AlignmentFlag.AlignRight)

    def _select(self, kind: str) -> None:
        self.selected_kind = kind
        self.accept()


class ProviderTab(QWidget):
    def __init__(
        self,
        provider_key: str,
        pc: ProviderConfig,
        *,
        title: str,
        unit_label: str,
        limit_field: str,
        presets: dict[str, int | None] | None = None,
        help_text: str = "",
        codex_mode: bool = False,
        show_calibrate: bool = False,
        api_only: bool = False,
    ) -> None:
        super().__init__()
        self.provider_key = provider_key
        self.pc = pc
        self.unit_label = unit_label
        self.limit_field = limit_field
        self.codex_mode = codex_mode
        self.presets = presets
        self.show_calibrate = show_calibrate
        self.api_only = api_only
        # Generic widgets that api_only mode skips — keep attrs so write_back is safe.
        self.limit_spin = None
        self.preset_box = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        if help_text:
            tip = QLabel(help_text)
            tip.setWordWrap(True)
            tip.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
            layout.addWidget(tip)

        form = QFormLayout()
        form.setSpacing(10)

        self.enabled = QCheckBox(f"{title} 모니터링 사용")
        self.enabled.setChecked(pc.enabled)
        form.addRow("", self.enabled)

        # api_only: show just the enable toggle + the claude.ai direct-integration
        # section. The local-log limit/window/calibration clutter is hidden (the
        # reader still uses sensible defaults as an automatic fallback).
        if api_only:
            layout.addLayout(form)
            self._build_api_section(layout)
            layout.addStretch(1)
            return

        self.log_dir = QLineEdit(pc.log_dir)
        browse = QPushButton("폴더 선택…")
        browse.clicked.connect(self._browse)
        path_row = QHBoxLayout()
        path_row.addWidget(self.log_dir, 1)
        path_row.addWidget(browse)
        path_wrap = QWidget()
        path_wrap.setLayout(path_row)
        form.addRow("로그 폴더", path_wrap)

        self.window_min = QSpinBox()
        self.window_min.setRange(15, 10080)
        self.window_min.setSingleStep(15)
        self.window_min.setValue(pc.window_minutes)
        self.window_min.setSuffix(" 분")
        form.addRow("측정 윈도우", self.window_min)

        if codex_mode:
            note = QLabel("Codex는 사용량을 자체적으로 % 단위로 보고합니다. 한도 설정 불필요.")
            note.setWordWrap(True)
            note.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
            form.addRow("", note)
            self.limit_spin = None
            self.preset_box = None
        else:
            self.limit_spin = QSpinBox()
            self.limit_spin.setRange(1, 1_000_000_000)
            self.limit_spin.setSingleStep(100_000 if limit_field == "token_limit" else 100)
            self.limit_spin.setGroupSeparatorShown(True)
            self.limit_spin.setValue(getattr(pc, limit_field))
            self.limit_spin.setSuffix(f" {unit_label}")
            form.addRow("100% 한도", self.limit_spin)

            if presets:
                self.preset_box = QComboBox()
                _style_combo_popup(self.preset_box)
                self.preset_box.addItems(list(presets.keys()))
                # Pre-select preset that matches current value, else "Custom".
                current_value = int(self.limit_spin.value())
                match = next(
                    (name for name, v in presets.items() if v == current_value), "Custom"
                )
                self.preset_box.blockSignals(True)
                self.preset_box.setCurrentText(match)
                self.preset_box.blockSignals(False)
                self.preset_box.currentTextChanged.connect(self._apply_preset)
                form.addRow("프리셋", self.preset_box)
            else:
                self.preset_box = None

        layout.addLayout(form)

        if self.show_calibrate:
            self._build_api_section(layout)
            self._build_calibration(layout)

        layout.addStretch(1)

    def _build_api_section(self, parent_layout) -> None:
        box = QGroupBox("🔑 claude.ai 직접 연동 (정확)")
        box.setStyleSheet(
            f"QGroupBox {{ font-weight: 600; margin-top: 14px; padding-top: 12px; "
            f"border: 1px solid {ACCENT}40; border-radius: 8px; }}"
            f"QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 6px; color: {ACCENT}; }}"
        )
        v = QVBoxLayout(box)
        v.setSpacing(8)

        warn = QLabel(
            "claude.ai 비공개 엔드포인트 직접 호출 — 표시값이 사이트와 100% 일치.\n"
            "주의: Anthropic ToS 회색지대. 본인 계정/본인 사용에 한해 사용 권장.\n"
            "세션 키는 OS 보안 저장소(Windows 자격 증명 관리자 / macOS 키체인)에 안전 저장됩니다."
        )
        warn.setWordWrap(True)
        warn.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
        v.addWidget(warn)

        self.api_enabled = QCheckBox("claude.ai API 사용 (실패 시 로컬 로그로 자동 폴백)")
        self.api_enabled.setChecked(self.pc.use_api)
        v.addWidget(self.api_enabled)

        help_row = QLabel(
            "세션 키 얻는 법: claude.ai 로그인 → F12 → Application → Cookies → claude.ai → "
            "<code>sessionKey</code> 값 복사 (sk-ant- 로 시작)"
        )
        help_row.setWordWrap(True)
        help_row.setStyleSheet(f"color: {MUTED}; font-size: 10px;")
        v.addWidget(help_row)

        row = QHBoxLayout()
        row.addWidget(QLabel("세션 키:"))
        self.api_key_input = QLineEdit()
        self.api_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key_input.setPlaceholderText("(저장된 키 있음)" if load_session_key() else "sessionKey 값 붙여넣기")
        row.addWidget(self.api_key_input, 1)
        v.addLayout(row)

        row2 = QHBoxLayout()
        self.api_test_btn = QPushButton("연결 테스트")
        self.api_test_btn.setObjectName("primary")
        self.api_test_btn.clicked.connect(self._api_test)
        self.api_clear_btn = QPushButton("저장된 키 삭제")
        self.api_clear_btn.clicked.connect(self._api_clear)
        row2.addWidget(self.api_test_btn)
        row2.addWidget(self.api_clear_btn)
        row2.addStretch(1)
        v.addLayout(row2)

        self.api_result = QLabel("")
        self.api_result.setWordWrap(True)
        self.api_result.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
        v.addWidget(self.api_result)

        parent_layout.addWidget(box)

    def _api_test(self) -> None:
        # If user just typed a new key, persist it before testing.
        typed = self.api_key_input.text().strip()
        if typed:
            save_session_key(typed)
            self.api_key_input.clear()
            self.api_key_input.setPlaceholderText("(저장된 키 있음)")
        key = load_session_key()
        if not key:
            self.api_result.setText("⚠ 세션 키가 비어있습니다. 위에 붙여넣고 다시 누르세요.")
            self.api_result.setStyleSheet("color: #B91C1C; font-size: 11px;")
            return

        self.api_result.setText("⏳ 호출 중...")
        self.api_result.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
        QApplication = self.window().style().__class__  # keep Qt import optional
        from PyQt6.QtWidgets import QApplication as _QApp
        _QApp.processEvents()

        # First discover org if not cached
        org_id = self.pc.org_id
        if not org_id:
            org_id, msg = discover_org(key)
            if not org_id:
                self.api_result.setText(f"✗ {msg}")
                self.api_result.setStyleSheet("color: #B91C1C; font-size: 11px;")
                return
            self.pc.org_id = org_id

        usage, err = fetch_usage(key, org_id)
        if usage is None:
            self.api_result.setText(f"✗ {err}")
            self.api_result.setStyleSheet("color: #B91C1C; font-size: 11px;")
            return

        lines = [
            f"✓ 연결 성공 — 조직 ID: {usage.org_id[:8]}...",
            f"5h:  {usage.five_hour_pct:.1f}%",
            f"주간: {usage.seven_day_pct:.1f}%",
        ]
        if usage.seven_day_sonnet_pct is not None:
            lines.append(f"Sonnet 주간: {usage.seven_day_sonnet_pct:.1f}%")
        if usage.seven_day_opus_pct is not None:
            lines.append(f"Opus 주간: {usage.seven_day_opus_pct:.1f}%")
        self.api_result.setText("\n".join(lines))
        self.api_result.setStyleSheet(f"color: {ACCENT}; font-size: 11px;")

    def _api_clear(self) -> None:
        delete_session_key()
        self.pc.org_id = ""
        self.api_key_input.setPlaceholderText("sessionKey 값 붙여넣기")
        self.api_key_input.clear()
        self.api_result.setText("저장된 세션 키와 조직 ID 삭제됨.")
        self.api_result.setStyleSheet(f"color: {MUTED}; font-size: 11px;")

    def _build_calibration(self, parent_layout) -> None:
        box = QGroupBox("🎯 claude.ai 기준으로 보정")
        box.setStyleSheet(
            f"QGroupBox {{ font-weight: 600; margin-top: 14px; padding-top: 12px; "
            f"border: 1px solid {ACCENT}40; border-radius: 8px; }}"
            f"QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 6px; color: {ACCENT}; }}"
        )
        v = QVBoxLayout(box)
        v.setSpacing(8)

        tip = QLabel(
            "claude.ai 웹사이트에 표시된 현재 사용량 % 를 입력하고 [보정] 을 누르면\n"
            "지금 측정값을 기준으로 100% 한도를 역산해서 자동으로 맞춥니다."
        )
        tip.setWordWrap(True)
        tip.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
        v.addWidget(tip)

        row = QHBoxLayout()
        row.addWidget(QLabel("웹사이트 % :"))
        self.calib_input = QDoubleSpinBox()
        self.calib_input.setRange(0.1, 100.0)
        self.calib_input.setSingleStep(1.0)
        self.calib_input.setDecimals(1)
        self.calib_input.setValue(60.0)
        self.calib_input.setSuffix(" %")
        row.addWidget(self.calib_input)

        self.calib_btn = QPushButton("지금 보정")
        self.calib_btn.setObjectName("primary")
        self.calib_btn.clicked.connect(self._do_calibrate)
        row.addWidget(self.calib_btn)
        row.addStretch(1)
        v.addLayout(row)

        self.calib_result = QLabel("")
        self.calib_result.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
        v.addWidget(self.calib_result)

        parent_layout.addWidget(box)

    def _do_calibrate(self) -> None:
        target_pct = float(self.calib_input.value())
        if target_pct <= 0:
            return
        # Apply current edits to pc so the reader uses the same window/dir as the UI shows.
        self.write_back()
        snap = read_claude(self.pc.log_dir, self.pc.window_minutes, self.pc.token_limit)
        if not snap.available or snap.used <= 0:
            self.calib_result.setText(
                "측정값이 0입니다. Claude Code 활동이 측정 윈도우 안에 있는지 확인하세요."
            )
            self.calib_result.setStyleSheet("color: #B91C1C; font-size: 11px;")
            return
        new_limit = int(round(snap.used / (target_pct / 100.0)))
        if self.limit_spin is not None:
            self.limit_spin.setValue(new_limit)
        # Also write into the dataclass so the value sticks even if the user
        # closes the window without clicking "저장".
        setattr(self.pc, self.limit_field, new_limit)
        if self.preset_box is not None:
            self.preset_box.blockSignals(True)
            self.preset_box.setCurrentText("Custom")
            self.preset_box.blockSignals(False)
        self.calib_result.setText(
            f"✓ 보정 완료 — 현재 측정 {snap.used:,} → 한도 {new_limit:,} ({target_pct:.1f}% 기준).\n"
            "저장 후 새로고침 하면 표시값이 일치합니다."
        )
        self.calib_result.setStyleSheet(f"color: {ACCENT}; font-size: 11px;")

    def _browse(self) -> None:
        start = self.log_dir.text() or str(Path.home())
        chosen = QFileDialog.getExistingDirectory(self, "로그 폴더 선택", start)
        if chosen:
            self.log_dir.setText(chosen)

    def _apply_preset(self, name: str) -> None:
        if not self.presets or self.limit_spin is None:
            return
        val = self.presets.get(name)
        if val is not None:
            self.limit_spin.setValue(val)

    def write_back(self) -> None:
        self.pc.enabled = self.enabled.isChecked()
        if not self.api_only:
            self.pc.log_dir = self.log_dir.text().strip()
            self.pc.window_minutes = int(self.window_min.value())
            if self.limit_spin is not None:
                setattr(self.pc, self.limit_field, int(self.limit_spin.value()))
        # claude.ai API section (only present on Claude tab)
        if hasattr(self, "api_enabled"):
            self.pc.use_api = self.api_enabled.isChecked()
            typed = self.api_key_input.text().strip()
            if typed:
                save_session_key(typed)
                self.api_key_input.clear()


class GeneralTab(QWidget):
    def __init__(self, cfg: Config, on_reset_overlay: Callable[[], None] | None = None) -> None:
        super().__init__()
        self.cfg = cfg
        self._on_reset_overlay = on_reset_overlay
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        form = QFormLayout()
        form.setSpacing(10)

        self.refresh = QSpinBox()
        self.refresh.setRange(5, 3600)
        self.refresh.setValue(cfg.refresh_seconds)
        self.refresh.setSuffix(" 초")
        form.addRow("새로고침 주기", self.refresh)

        self.display_mode = QComboBox()
        _style_combo_popup(self.display_mode)
        self.display_mode.addItem("표준", "standard")
        self.display_mode.addItem("간소", "compact")
        self.display_mode.addItem("최소", "minimal")
        self.display_mode.addItem("오버레이", "overlay")
        modes = ["standard", "compact", "minimal", "overlay"]
        current_idx = modes.index(cfg.display_mode) if cfg.display_mode in modes else 0
        self.display_mode.setCurrentIndex(current_idx)
        form.addRow("표시 모드", self.display_mode)

        self.overlay_opacity = QDoubleSpinBox()
        self.overlay_opacity.setRange(0.20, 1.00)
        self.overlay_opacity.setSingleStep(0.05)
        self.overlay_opacity.setDecimals(2)
        self.overlay_opacity.setValue(cfg.overlay_opacity)
        form.addRow("오버레이 투명도", self.overlay_opacity)

        self.overlay_locked = QCheckBox("오버레이 위치 잠금 (드래그 차단)")
        self.overlay_locked.setChecked(cfg.overlay_locked)
        form.addRow("", self.overlay_locked)

        self.reset_overlay_btn = QPushButton("오버레이 위치 초기화 (우상단으로)")
        self.reset_overlay_btn.setToolTip(
            "모니터를 바꾸거나 해상도가 변해 오버레이가 화면 밖으로 사라졌을 때 되돌립니다"
        )
        self.reset_overlay_btn.clicked.connect(self._reset_overlay)
        form.addRow("오버레이 위치", self.reset_overlay_btn)

        self.autostart_cb = QCheckBox("Windows 시작 시 자동 실행")
        self.autostart_cb.setChecked(autostart.is_enabled() or cfg.autostart)
        form.addRow("", self.autostart_cb)

        self.tray_max_only = QCheckBox("트레이 아이콘에 최대값만 표시 (꺼두면 평균)")
        self.tray_max_only.setChecked(cfg.tray_show_max_only)
        form.addRow("", self.tray_max_only)

        self.show_on_click = QCheckBox("트레이 좌클릭 시 상태창 열기")
        self.show_on_click.setChecked(cfg.show_window_on_click)
        form.addRow("", self.show_on_click)

        layout.addLayout(form)

        warn = QLabel(
            "ℹ️ Claude / Gemini의 100% 는 공식 한도가 아니라 위에서 설정한 값 기준입니다.\n"
            "Codex만 OpenAI가 직접 보고하는 % 를 사용합니다."
        )
        warn.setWordWrap(True)
        warn.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
        layout.addWidget(warn)
        layout.addStretch(1)

    def _reset_overlay(self) -> None:
        if self._on_reset_overlay is not None:
            self._on_reset_overlay()
        self.reset_overlay_btn.setText("✓ 우상단으로 이동됨")
        self.reset_overlay_btn.setEnabled(False)

    def write_back(self) -> None:
        self.cfg.refresh_seconds = int(self.refresh.value())
        self.cfg.autostart = self.autostart_cb.isChecked()
        self.cfg.tray_show_max_only = self.tray_max_only.isChecked()
        self.cfg.show_window_on_click = self.show_on_click.isChecked()
        self.cfg.display_mode = self.display_mode.currentData() or "standard"
        self.cfg.overlay_opacity = float(self.overlay_opacity.value())
        self.cfg.overlay_locked = self.overlay_locked.isChecked()


class AccountsTab(QWidget):
    def __init__(self, cfg: Config) -> None:
        super().__init__()
        self.cfg = cfg
        self._build()
        self._refresh_list()

    def _build(self) -> None:
        outer = QHBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 16)
        outer.setSpacing(12)

        left = QVBoxLayout()
        self.list = QListWidget()
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.list.currentRowChanged.connect(self._load_selected)
        left.addWidget(self.list, 1)

        add_account = QPushButton("+  계정 추가")
        add_account.setObjectName("accountAdd")
        add_account.setToolTip("Claude, Codex 또는 Gemini 계정 추가")
        add_account.setMinimumWidth(210)
        add_account.clicked.connect(self._choose_account_type)
        left.addWidget(add_account)

        self.delete_btn = QPushButton("선택 삭제")
        self.delete_btn.clicked.connect(self._delete_selected)
        self.delete_btn.setMinimumWidth(210)
        left.addWidget(self.delete_btn)
        outer.addLayout(left, 1)

        form_wrap = QWidget()
        form = QFormLayout(form_wrap)
        form.setSpacing(10)
        self.enabled = QCheckBox("모니터링 사용")
        form.addRow("", self.enabled)
        self.label = QLineEdit()
        form.addRow("표시 이름", self.label)
        self.kind = QComboBox()
        _style_combo_popup(self.kind)
        self.kind.addItem("Claude", "claude")
        self.kind.addItem("Codex", "codex")
        self.kind.addItem("Gemini", "gemini")
        self.provider_info = QLabel("")
        self.provider_info.setObjectName("subtitle")
        self.provider_info.setWordWrap(True)
        form.addRow("종류", self.kind)
        self.log_dir = QLineEdit()
        form.addRow("", self.provider_info)
        browse = QPushButton("폴더 선택")
        browse.clicked.connect(self._browse)
        path_row = QHBoxLayout()
        path_row.addWidget(self.log_dir, 1)
        path_row.addWidget(browse)
        path_wrap = QWidget()
        path_wrap.setLayout(path_row)
        form.addRow("로그 폴더", path_wrap)
        self.window_min = QSpinBox()
        self.window_min.setRange(15, 10080)
        self.window_min.setSingleStep(15)
        self.window_min.setSuffix(" 분")
        form.addRow("측정 창", self.window_min)
        self.token_limit = QSpinBox()
        self.token_limit.setRange(1, 1_000_000_000)
        self.token_limit.setGroupSeparatorShown(True)
        form.addRow("토큰 한도", self.token_limit)
        self.message_limit = QSpinBox()
        self.message_limit.setRange(1, 1_000_000_000)
        self.message_limit.setGroupSeparatorShown(True)
        form.addRow("메시지 한도", self.message_limit)
        self.use_api = QCheckBox("Claude API 사용")
        form.addRow("", self.use_api)
        self.form = form
        outer.addWidget(form_wrap, 2)
        outer.setStretch(0, 0)
        outer.setStretch(1, 1)
        self.list.setFixedWidth(210)
        form_wrap.setMinimumWidth(460)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        for row in range(form.rowCount()):
            label_item = form.itemAt(row, QFormLayout.ItemRole.LabelRole)
            if label_item is not None and label_item.widget() is not None:
                label_item.widget().setMinimumWidth(92)

        for widget in (
            self.enabled, self.label, self.kind, self.log_dir, self.window_min,
            self.token_limit, self.message_limit, self.use_api,
        ):
            signal = getattr(widget, "textChanged", None) or getattr(widget, "valueChanged", None)
            if signal is not None:
                signal.connect(self._save_selected)
        self.enabled.toggled.connect(self._save_selected)
        self.kind.currentIndexChanged.connect(self._save_selected)
        self.kind.currentIndexChanged.connect(self._update_provider_fields)
        self.use_api.toggled.connect(self._save_selected)
        self.use_api.toggled.connect(self._update_provider_fields)

    def _default_config(self, kind: str) -> ProviderConfig:
        if kind == "codex":
            return ProviderConfig(log_dir=str(Path.home() / ".codex" / "sessions"), token_limit=100)
        if kind == "gemini":
            return ProviderConfig(log_dir=str(Path.home() / ".gemini"), message_limit=1500)
        return ProviderConfig(log_dir=str(Path.home() / ".claude" / "projects"), token_limit=29_000_000)

    def _choose_account_type(self) -> None:
        dialog = AccountTypeDialog(self)
        if dialog.exec() and dialog.selected_kind:
            self._add_account(dialog.selected_kind)

    def _add_account(self, kind: str) -> None:
        label = {"claude": "Claude", "codex": "Codex", "gemini": "Gemini"}[kind]
        account = AccountConfig(
            id=f"{kind}_{uuid4().hex[:8]}",
            kind=kind,
            label=f"{label} {len(self.cfg.providers) + 1}",
            config=self._default_config(kind),
        )
        self.cfg.providers.append(account)
        self._refresh_list()
        self.list.setCurrentRow(len(self.cfg.providers) - 1)

    def _delete_selected(self) -> None:
        row = self.list.currentRow()
        if row < 0 or row >= len(self.cfg.providers):
            return
        self.cfg.providers.pop(row)
        self._refresh_list()
        self.list.setCurrentRow(min(row, len(self.cfg.providers) - 1))

    def _refresh_list(self) -> None:
        self.list.blockSignals(True)
        self.list.clear()
        for account in self.cfg.providers:
            state = "" if account.enabled else " (꺼짐)"
            self.list.addItem(f"{account.label or account.kind.title()} · {account.kind}{state}")
        self.list.blockSignals(False)
        self._load_selected(self.list.currentRow())

    def _load_selected(self, row: int) -> None:
        enabled = 0 <= row < len(self.cfg.providers)
        for widget in (
            self.enabled, self.label, self.kind, self.log_dir, self.window_min,
            self.token_limit, self.message_limit, self.use_api, self.delete_btn,
        ):
            widget.setEnabled(enabled)
        if not enabled:
            return
        account = self.cfg.providers[row]
        pc = account.config
        self.enabled.blockSignals(True)
        self.label.blockSignals(True)
        self.kind.blockSignals(True)
        self.log_dir.blockSignals(True)
        self.window_min.blockSignals(True)
        self.token_limit.blockSignals(True)
        self.message_limit.blockSignals(True)
        self.use_api.blockSignals(True)
        self.enabled.setChecked(account.enabled)
        self.label.setText(account.label)
        self.kind.setCurrentIndex(max(0, self.kind.findData(account.kind)))
        self.log_dir.setText(pc.log_dir)
        self.window_min.setValue(pc.window_minutes)
        self.token_limit.setValue(pc.token_limit)
        self.message_limit.setValue(pc.message_limit)
        self.use_api.setChecked(pc.use_api)
        self.enabled.blockSignals(False)
        self.label.blockSignals(False)
        self.kind.blockSignals(False)
        self.log_dir.blockSignals(False)
        self.window_min.blockSignals(False)
        self.token_limit.blockSignals(False)
        self.message_limit.blockSignals(False)
        self.use_api.blockSignals(False)
        self._update_provider_fields()

    def _update_provider_fields(self, *_args) -> None:
        kind = self.kind.currentData() or "claude"
        descriptions = {
            "claude": (
                "공식 API 사용 시 Claude가 보고한 사용량을 표시합니다. "
                "끄면 로컬 로그와 토큰 한도로 추정합니다."
            ),
            "codex": "Codex 세션 로그가 보고한 사용률과 초기화 시간을 그대로 표시합니다.",
            "gemini": "로컬 기록의 메시지 수를 설정한 한도와 비교한 추정값입니다.",
        }
        self.provider_info.setText(descriptions[kind])
        for widget, visible in (
            (self.token_limit, kind == "claude" and not self.use_api.isChecked()),
            (self.message_limit, kind == "gemini"),
            (self.use_api, kind == "claude"),
        ):
            widget.setVisible(visible)
            label = self.form.labelForField(widget)
            if label is not None:
                label.setVisible(visible)

    def _save_selected(self, *_args) -> None:
        row = self.list.currentRow()
        if row < 0 or row >= len(self.cfg.providers):
            return
        account = self.cfg.providers[row]
        account.enabled = self.enabled.isChecked()
        account.label = self.label.text().strip() or self.kind.currentText()
        account.kind = self.kind.currentData() or "claude"
        pc = account.config
        pc.log_dir = self.log_dir.text().strip()
        pc.window_minutes = int(self.window_min.value())
        pc.token_limit = int(self.token_limit.value())
        pc.message_limit = int(self.message_limit.value())
        pc.use_api = self.use_api.isChecked()
        current = self.list.currentRow()
        self._refresh_list()
        self.list.setCurrentRow(current)

    def _browse(self) -> None:
        start = self.log_dir.text() or str(Path.home())
        chosen = QFileDialog.getExistingDirectory(self, "로그 폴더 선택", start)
        if chosen:
            self.log_dir.setText(chosen)

    def write_back(self) -> None:
        self._save_selected()


class SettingsWindow(QWidget):
    def __init__(
        self,
        cfg: Config,
        on_save: Callable[[], None],
        on_reset_overlay: Callable[[], None] | None = None,
    ) -> None:
        super().__init__()
        self.cfg = cfg
        self._on_save = on_save
        self._on_reset_overlay = on_reset_overlay
        self.setWindowTitle("Token Status — 설정")
        self.setObjectName("root")
        self.setStyleSheet(GLOBAL_QSS)
        self.setMinimumSize(720, 500)
        self.resize(820, 580)
        self._build()

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 12, 14, 12)
        outer.setSpacing(10)

        title = QLabel("설정")
        title.setObjectName("title")
        outer.addWidget(title)

        self.tabs = QTabWidget()
        self.general_tab = GeneralTab(self.cfg, on_reset_overlay=self._on_reset_overlay)
        self.accounts_tab = AccountsTab(self.cfg)
        self.claude_tab = ProviderTab(
            "claude", self.cfg.claude,
            title="Claude Code", unit_label="tokens", limit_field="token_limit",
            api_only=True,
            help_text=(
                "claude.ai 계정에 직접 연동해 웹사이트와 동일한 사용량을 표시합니다.\n"
                "아래에 세션 키만 넣으면 끝 — 별도 한도/윈도우 설정이 필요 없습니다."
            ),
        )
        self.codex_tab = ProviderTab(
            "codex", self.cfg.codex,
            title="Codex", unit_label="%", limit_field="token_limit",
            codex_mode=True,
            help_text="~/.codex/sessions 의 token_count 이벤트에서 used_percent 를 직접 사용합니다.",
        )
        self.gemini_tab = ProviderTab(
            "gemini", self.cfg.gemini,
            title="Gemini", unit_label="messages", limit_field="message_limit",
            presets=GEMINI_PRESETS,
            help_text="Gemini CLI는 토큰 카운트를 기록하지 않아 메시지 수 기반 추정입니다.",
        )
        self.tabs.addTab(self._scrollable(self.general_tab), "일반")
        self.tabs.addTab(self._scrollable(self.accounts_tab), "추가 계정")
        self.tabs.addTab(self._scrollable(self.claude_tab), "Claude")
        self.tabs.addTab(self._scrollable(self.codex_tab), "Codex")
        self.tabs.addTab(self._scrollable(self.gemini_tab), "Gemini")
        while self.tabs.count() > 2:
            self.tabs.removeTab(2)
        self.tabs.setTabText(1, "계정 관리")
        outer.addWidget(self.tabs, 1)

        btns = QHBoxLayout()
        log_btn = QPushButton("로그 보기")
        log_btn.setToolTip("문제 발생 시 자세한 오류 기록을 텍스트 에디터로 엽니다")
        log_btn.clicked.connect(self._open_log)
        cancel = QPushButton("취소")
        cancel.clicked.connect(self.close)
        save = QPushButton("저장")
        save.setObjectName("primary")
        save.clicked.connect(self._save)
        btns.addWidget(log_btn)
        btns.addStretch(1)
        btns.addWidget(cancel)
        btns.addWidget(save)
        outer.addLayout(btns)

        self._fit_to_screen()

    @staticmethod
    def _scrollable(inner: QWidget) -> QScrollArea:
        """Wrap a tab page so its content scrolls instead of forcing the whole
        window taller than the screen (which would push the save button off-screen).

        The viewport is kept transparent so the white tab pane shows through —
        otherwise the scroll area paints with the OS dark-mode palette and the
        light theme turns black.
        """
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.Shape.NoFrame)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        area.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        area.viewport().setStyleSheet("background: transparent;")
        inner.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        area.setWidget(inner)
        return area

    def _fit_to_screen(self) -> None:
        """Keep the window within the available screen area and centered, so the
        bottom button row is never hidden behind the taskbar or off-screen."""
        screen = QGuiApplication.primaryScreen()
        if not screen:
            return
        avail = screen.availableGeometry()
        w = min(self.width(), avail.width() - 40)
        h = min(self.height(), avail.height() - 40)
        self.resize(max(360, w), max(320, h))
        x = avail.left() + (avail.width() - self.width()) // 2
        y = avail.top() + (avail.height() - self.height()) // 2
        self.move(x, y)

    def _open_log(self) -> None:
        import os, subprocess, sys
        from ..logger import log_path
        p = log_path()
        if not p.exists():
            QMessageBox.information(self, "로그", f"로그 파일이 아직 없습니다: {p}")
            return
        try:
            if sys.platform.startswith("win"):
                os.startfile(str(p))  # type: ignore[attr-defined]  # Windows only
            elif sys.platform == "darwin":
                subprocess.run(["open", str(p)], check=False)
            else:
                subprocess.run(["xdg-open", str(p)], check=False)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "로그 열기 실패", str(e))

    def _save(self) -> None:
        import logging, traceback
        log = logging.getLogger(__name__)
        # Wrap every step so silent exceptions surface in the log AND a dialog.
        try:
            self.general_tab.write_back()
            self.accounts_tab.write_back()
        except Exception as e:  # noqa: BLE001
            log.exception("write_back failed")
            from ..logger import log_path
            QMessageBox.critical(
                self, "저장 실패",
                f"설정 위젯 값 수집 중 오류:\n{e}\n\n로그: {log_path()}"
            )
            return
        try:
            self.cfg.save()
        except OSError as e:
            log.exception("config save failed")
            QMessageBox.critical(self, "저장 실패", f"설정 파일 저장 중 오류: {e}")
            return
        try:
            autostart.apply(self.cfg.autostart)
        except OSError as e:
            log.exception("autostart apply failed")
            QMessageBox.warning(self, "자동 시작 설정 실패", str(e))
        try:
            self._on_save()
        except Exception as e:  # noqa: BLE001
            log.exception("on_save callback failed")
            QMessageBox.warning(
                self, "저장은 됐는데 적용 중 오류",
                f"{e}\n\n저장 자체는 완료. 앱 재시작하면 반영됩니다."
            )
        log.info("settings saved successfully")
        self.close()
