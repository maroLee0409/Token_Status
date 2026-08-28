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
    QRadioButton,
    QScrollArea,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)
from PyQt6.QtGui import QGuiApplication

from .. import autostart
from .. import claude_profile
from ..config import AccountConfig, CLAUDE_PRESETS, GEMINI_PRESETS, Config, ProviderConfig
from ..readers import detect_local_account, read_claude
from ..readers.claude_api import (
    delete_session_key,
    discover_org,
    fetch_account,
    fetch_usage,
    load_session_key,
    save_session_key,
)
from .style import ACCENT, ACCENT_DARK, GLOBAL_QSS, MUTED


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
        self._current_row = -1   # 행이 실제로 바뀔 때만 세션 키 입력칸을 비우기 위함
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
        self.list.setMinimumHeight(220)
        left.addWidget(self.list, 0)

        add_account = QPushButton("+  계정 추가")
        add_account.setObjectName("accountAdd")
        add_account.setToolTip("Claude, Codex 또는 Gemini 계정 추가")
        add_account.setMinimumWidth(210)
        add_account.clicked.connect(self._choose_account_type)
        left.addWidget(add_account)

        # 목록 순서 = 오버레이/상태창에 표시되는 순서.
        move_row = QHBoxLayout()
        self.move_up_btn = QPushButton("↑ 위로")
        self.move_up_btn.setToolTip("표시 순서를 위로 (오버레이에서 더 먼저 나옵니다)")
        self.move_up_btn.clicked.connect(lambda: self._move_selected(-1))
        self.move_down_btn = QPushButton("↓ 아래로")
        self.move_down_btn.setToolTip("표시 순서를 아래로")
        self.move_down_btn.clicked.connect(lambda: self._move_selected(1))
        move_row.addWidget(self.move_up_btn)
        move_row.addWidget(self.move_down_btn)
        left.addLayout(move_row)

        self.delete_btn = QPushButton("선택 삭제")
        self.delete_btn.clicked.connect(self._delete_selected)
        self.delete_btn.setMinimumWidth(210)
        left.addWidget(self.delete_btn)
        left.addStretch(1)
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
        form.addRow("", self.provider_info)
        # 계정 연동이 이 탭의 핵심이므로 로그 폴더/한도보다 먼저 보이게 둔다.
        self.use_api = QCheckBox("claude.ai 실시간 사용량 사용 (권장)")
        self.link_box = self._build_link_box()
        form.addRow(self.link_box)
        self.profile_box = self._build_profile_box()
        form.addRow(self.profile_box)
        self.log_dir = QLineEdit()
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

    # ---------- Claude Code 실행 프로필 ----------

    def _build_profile_box(self) -> QGroupBox:
        """계정별 CLAUDE_CONFIG_DIR 관리 + 실행 버튼.

        인증은 공식 `claude auth login` 이 처리하고 이 앱은 토큰을 만지지 않는다.
        기본 프로필(빈 경로)은 이미 로그인된 ~/.claude 를 그대로 쓴다.
        """
        box = QGroupBox("🚀 Claude Code 실행 프로필")
        box.setStyleSheet(
            f"QGroupBox {{ font-weight: 600; margin-top: 12px; padding-top: 12px; "
            f"border: 1px solid {ACCENT}40; border-radius: 8px; }}"
            f"QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 6px; color: {ACCENT}; }}"
        )
        v = QVBoxLayout(box)
        v.setSpacing(7)

        self.profile_default = QRadioButton("기본 프로필 — 지금 로그인돼 있는 계정 그대로 사용")
        self.profile_default.setToolTip(
            "CLAUDE_CONFIG_DIR 을 지정하지 않습니다. 로그인이 필요 없습니다.")
        self.profile_custom = QRadioButton("전용 프로필 — 이 계정만의 폴더를 따로 둠")
        self.profile_custom.setToolTip(
            "최초 1회만 로그인하면 이후에는 버튼만으로 실행됩니다.")
        for rb in (self.profile_default, self.profile_custom):
            rb.setMinimumHeight(22)
            v.addWidget(rb)
        self.profile_default.toggled.connect(self._profile_mode_changed)

        path_row = QHBoxLayout()
        self.profile_path = QLabel("")
        self.profile_path.setWordWrap(True)
        self.profile_path.setStyleSheet(f"color: {MUTED}; font-size: 10px;")
        self.profile_open_btn = QPushButton("폴더 열기")
        self.profile_open_btn.setMaximumWidth(90)
        self.profile_open_btn.clicked.connect(self._profile_open_folder)
        path_row.addWidget(self.profile_path, 1)
        path_row.addWidget(self.profile_open_btn)
        v.addLayout(path_row)

        self.profile_who = QLabel("")
        self.profile_who.setWordWrap(True)
        v.addWidget(self.profile_who)

        btn_row = QHBoxLayout()
        self.profile_launch_btn = QPushButton("▶ 이 계정으로 실행")
        self.profile_launch_btn.setStyleSheet(
            f"QPushButton {{ background: {ACCENT}; color: white; font-weight: 600; "
            f"border: 1px solid {ACCENT_DARK}; border-radius: 8px; "
            f"padding: 7px 14px; font-size: 13px; }}"
            f"QPushButton:hover {{ background: {ACCENT_DARK}; }}"
            f"QPushButton:pressed {{ background: #C2410C; }}"
            f"QPushButton:disabled {{ background: #F1F5F9; color: #94A3B8; border-color: #CBD5E1; }}"
        )
        self.profile_launch_btn.setMinimumHeight(34)
        self.profile_launch_btn.setMinimumWidth(150)
        self.profile_launch_btn.clicked.connect(self._profile_launch)
        self.profile_login_btn = QPushButton("로그인")
        self.profile_login_btn.setMinimumHeight(34)
        self.profile_login_btn.setToolTip(
            "새 콘솔 창에서 공식 OAuth 로그인을 시작합니다 (최초 1회)")
        self.profile_login_btn.clicked.connect(self._profile_login)
        self.profile_check_btn = QPushButton("상태 확인")
        self.profile_check_btn.setMinimumHeight(34)
        self.profile_check_btn.clicked.connect(self._profile_check)
        btn_row.addWidget(self.profile_launch_btn)
        btn_row.addWidget(self.profile_login_btn)
        btn_row.addWidget(self.profile_check_btn)
        btn_row.addStretch(1)
        v.addLayout(btn_row)

        self.profile_mismatch = QLabel("")
        self.profile_mismatch.setWordWrap(True)
        self.profile_mismatch.setVisible(False)
        self.profile_mismatch.setStyleSheet("color: #B45309; font-size: 10px;")
        v.addWidget(self.profile_mismatch)

        self.profile_hint = QLabel(
            "이 설정은 [이 계정으로 실행] 이 어느 계정으로 뜨는지만 정합니다. "
            "표시되는 사용량 숫자는 위 🔗 계정 연동의 세션 키가 결정합니다 — 둘은 별개입니다."
        )
        self.profile_hint.setWordWrap(True)
        self.profile_hint.setStyleSheet(f"color: {MUTED}; font-size: 10px;")
        v.addWidget(self.profile_hint)
        return box

    def _refresh_profile_mismatch(self, account: AccountConfig) -> None:
        """실행 계정과 사용량 출처가 다르면, 숫자가 안 바뀌는 이유를 짚어준다."""
        pc = account.config
        run_as = (pc.profile_status or "").split(" · ")[0].strip()
        msg = ""
        if pc.use_api and not pc.account_email:
            msg = ("⚠ 사용량 출처가 비어 있습니다 — 🔗 계정 연동에서 이 계정의 "
                   "세션 키를 등록해야 숫자가 나옵니다.")
        elif pc.use_api and run_as and pc.account_email and run_as != pc.account_email:
            msg = (f"⚠ 실행 계정({run_as}) 과 사용량 출처({pc.account_email}) 가 다릅니다. "
                   f"표시되는 숫자는 {pc.account_email} 것입니다 — 바꾸려면 🔗 계정 연동의 "
                   f"세션 키를 {run_as} 것으로 교체하세요.")
        elif (not pc.use_api
                and not claude_profile.is_default(pc.claude_config_dir)
                and not claude_profile.credentials_exist(pc.claude_config_dir)):
            msg = ("⚠ 이 프로필은 아직 로그인 전이라 읽을 로그가 없습니다. "
                   "[로그인] 을 한 번 눌러주세요.")
        self.profile_mismatch.setText(msg)
        self.profile_mismatch.setVisible(bool(msg))

    def _set_profile_result(self, text: str, ok: bool = True) -> None:
        self.profile_who.setText(text)
        color = ACCENT if ok else "#B91C1C"
        self.profile_who.setStyleSheet(f"color: {color}; font-size: 11px;")

    def _refresh_profile_box(self, account: AccountConfig) -> None:
        pc = account.config
        custom = not claude_profile.is_default(pc.claude_config_dir)
        self.profile_default.blockSignals(True)
        self.profile_custom.blockSignals(True)
        self.profile_custom.setChecked(custom)
        self.profile_default.setChecked(not custom)
        self.profile_default.blockSignals(False)
        self.profile_custom.blockSignals(False)

        if custom:
            self.profile_path.setText(pc.claude_config_dir)
        else:
            self.profile_path.setText(str(Path.home() / ".claude") + "  (기본)")
        self.profile_login_btn.setEnabled(custom)

        logged_in = claude_profile.credentials_exist(pc.claude_config_dir)
        if pc.profile_status:
            self._set_profile_result("✓ " + pc.profile_status)
        elif logged_in:
            self._set_profile_result(
                "로그인돼 있음 — [상태 확인] 을 누르면 어느 계정인지 표시됩니다.")
            self.profile_who.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
        else:
            self._set_profile_result(
                "아직 로그인하지 않은 프로필입니다. [로그인] 을 한 번만 눌러주세요.", False)
        self.profile_launch_btn.setEnabled(logged_in or not custom)
        self._refresh_profile_mismatch(account)

    def _profile_mode_changed(self, _checked: bool = False) -> None:
        account = self._current_account()
        if account is None:
            return
        pc = account.config
        if self.profile_custom.isChecked():
            if claude_profile.is_default(pc.claude_config_dir):
                # 폴더명은 만들 때 한 번만 정한다. 이후 표시 이름을 바꿔도 경로를
                # 따라 바꾸면 이미 로그인해둔 폴더를 잃어버린다.
                pc.claude_config_dir = str(
                    claude_profile.unique_profile_path(account.label or account.id))
        else:
            pc.claude_config_dir = ""
        pc.profile_status = ""
        # 사용량 판독도 같은 프로필을 보도록 맞춰준다.
        pc.log_dir = claude_profile.projects_dir(pc.claude_config_dir)
        self.log_dir.blockSignals(True)
        self.log_dir.setText(pc.log_dir)
        self.log_dir.blockSignals(False)
        self._refresh_profile_box(account)
        self._warn_duplicate(account)

    def _profile_open_folder(self) -> None:
        from PyQt6.QtCore import QUrl
        from PyQt6.QtGui import QDesktopServices

        account = self._current_account()
        if account is None:
            return
        target = Path(account.config.claude_config_dir or (Path.home() / ".claude"))
        try:
            target.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            self._set_profile_result(f"✗ 폴더를 열 수 없습니다: {e}", False)
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))

    def _profile_login(self) -> None:
        account = self._current_account()
        if account is None:
            return
        pc = account.config
        if claude_profile.is_default(pc.claude_config_dir):
            self._set_profile_result(
                "기본 프로필은 이미 로그인돼 있어 로그인이 필요 없습니다.", False)
            return
        ok, err = claude_profile.login(pc.claude_config_dir)
        if not ok:
            self._set_profile_result(f"✗ {err}", False)
            return
        self._set_profile_result(
            "새 콘솔 창에서 로그인을 진행하세요. 브라우저 승인이 끝나면 [상태 확인] 을 누르면 됩니다.")
        self.profile_who.setStyleSheet(f"color: {MUTED}; font-size: 11px;")

    def _profile_check(self) -> None:
        from PyQt6.QtWidgets import QApplication

        account = self._current_account()
        if account is None:
            return
        pc = account.config
        self._set_profile_result("⏳ 확인 중...")
        self.profile_who.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
        QApplication.processEvents()

        data, err = claude_profile.auth_status(pc.claude_config_dir)
        if data is None:
            pc.profile_status = ""
            self._set_profile_result(f"✗ {err}", False)
            return
        if not data.get("loggedIn"):
            pc.profile_status = ""
            self._set_profile_result(
                "미로그인 — [로그인] 을 눌러 이 프로필에 계정을 연결하세요.", False)
            self.profile_launch_btn.setEnabled(
                claude_profile.is_default(pc.claude_config_dir))
            return
        pc.profile_status = claude_profile.describe_status(data)
        self._set_profile_result("✓ " + pc.profile_status)
        self.profile_launch_btn.setEnabled(True)
        self._refresh_profile_mismatch(account)
        if self._is_default_label(account) and data.get("email"):
            account.label = str(data["email"]).split("@")[0] or account.label
            self.label.blockSignals(True)
            self.label.setText(account.label)
            self.label.blockSignals(False)
        self._refresh_list_keep_row(self.list.currentRow())

    def _profile_launch(self) -> None:
        account = self._current_account()
        if account is None:
            return
        ok, err = claude_profile.launch(account.config.claude_config_dir)
        if ok:
            self._set_profile_result("▶ 새 창에서 Claude Code 를 실행했습니다.")
        else:
            self._set_profile_result(f"✗ {err}", False)

    # ---------- claude.ai 계정 연동 ----------

    def _build_link_box(self) -> QGroupBox:
        box = QGroupBox("🔗 claude.ai 계정 연동")
        box.setStyleSheet(
            f"QGroupBox {{ font-weight: 600; margin-top: 12px; padding-top: 12px; "
            f"border: 1px solid {ACCENT}40; border-radius: 8px; }}"
            f"QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 6px; color: {ACCENT}; }}"
        )
        v = QVBoxLayout(box)
        v.setSpacing(7)

        self.use_api.setMinimumHeight(24)
        v.addWidget(self.use_api)

        self.link_who = QLabel("")
        self.link_who.setWordWrap(True)
        v.addWidget(self.link_who)

        hint = QLabel(
            "계정마다 키가 따로 저장됩니다. claude.ai 로그인 → F12 → "
            "Application → Cookies → sessionKey 복사."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {MUTED}; font-size: 10px;")
        v.addWidget(hint)

        key_row = QHBoxLayout()
        self.session_key = QLineEdit()
        self.session_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.session_key.setMinimumWidth(180)
        self.session_key.returnPressed.connect(self._link_verify)
        key_row.addWidget(QLabel("세션 키"))
        key_row.addWidget(self.session_key, 1)
        v.addLayout(key_row)

        btn_row = QHBoxLayout()
        self.link_verify_btn = QPushButton("계정 확인 / 연결")
        self.link_verify_btn.setObjectName("primary")
        self.link_verify_btn.setStyleSheet(
            f"QPushButton {{ background: {ACCENT}; color: white; font-weight: 600; "
            f"border: 1px solid {ACCENT_DARK}; border-radius: 8px; "
            f"padding: 7px 14px; font-size: 13px; }}"
            f"QPushButton:hover {{ background: {ACCENT_DARK}; border-color: {ACCENT_DARK}; }}"
            f"QPushButton:pressed {{ background: #C2410C; }}"
            f"QPushButton:disabled {{ background: #F1F5F9; color: #94A3B8; "
            f"border-color: #CBD5E1; }}"
        )
        self.link_verify_btn.setMinimumWidth(140)
        self.link_verify_btn.setMinimumHeight(34)
        self.link_verify_btn.clicked.connect(self._link_verify)
        self.link_clear_btn = QPushButton("연결 해제")
        self.link_clear_btn.setMinimumWidth(110)
        self.link_clear_btn.setMinimumHeight(34)
        self.link_clear_btn.clicked.connect(self._link_clear)
        btn_row.addWidget(self.link_verify_btn)
        btn_row.addWidget(self.link_clear_btn)
        btn_row.addStretch(1)
        v.addLayout(btn_row)

        org_row = QHBoxLayout()
        self.org_box = QComboBox()
        _style_combo_popup(self.org_box)
        self.org_box.currentIndexChanged.connect(self._org_changed)
        org_row.addWidget(QLabel("조직"))
        org_row.addWidget(self.org_box, 1)
        v.addLayout(org_row)

        self.link_result = QLabel("")
        self.link_result.setWordWrap(True)
        self.link_result.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
        v.addWidget(self.link_result)
        return box

    def _current_account(self) -> AccountConfig | None:
        row = self.list.currentRow()
        if row < 0 or row >= len(self.cfg.providers):
            return None
        return self.cfg.providers[row]

    def _set_link_result(self, text: str, ok: bool = True) -> None:
        self.link_result.setText(text)
        color = ACCENT if ok else "#B91C1C"
        self.link_result.setStyleSheet(f"color: {color}; font-size: 11px;")

    def _refresh_link_box(self, account: AccountConfig) -> None:
        """선택된 계정 기준으로 연동 상태 표시를 다시 그린다."""
        pc = account.config
        has_key = bool(load_session_key(account.id))
        self.session_key.setPlaceholderText(
            "(이 계정의 키가 저장돼 있음 — 바꾸려면 새 키 붙여넣기)"
            if has_key else "sessionKey 값 붙여넣기"
        )

        if pc.account_email:
            who = f"✓ 연결된 계정: <b>{pc.account_email}</b>"
            if pc.org_name:
                who += f" · {pc.org_name}"
            self.link_who.setStyleSheet(f"color: {ACCENT}; font-size: 11px;")
        elif has_key:
            who = ("세션 키 저장됨 — [계정 확인] 을 누르면 어느 계정인지 표시됩니다."
                   + (f" (조직 {pc.org_id[:8]}…)" if pc.org_id else ""))
            self.link_who.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
        else:
            local = detect_local_account(pc.log_dir)
            who = "미연동 — 로컬 로그 추정값으로 표시됩니다."
            if local:
                who += f" (로그 폴더 계정: {local})"
            self.link_who.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
        self.link_who.setText(who)

        self.org_box.blockSignals(True)
        self.org_box.clear()
        if pc.org_id:
            self.org_box.addItem(pc.org_name or pc.org_id[:8] + "…", pc.org_id)
        else:
            self.org_box.addItem("(계정 확인 후 선택)", "")
        self.org_box.setCurrentIndex(0)
        self.org_box.blockSignals(False)
        self.org_box.setEnabled(bool(pc.org_id))

        self._warn_duplicate(account)

    def _warn_duplicate(self, account: AccountConfig) -> None:
        """같은 계정을 두 번 등록했거나, 로컬 모드 두 항목이 같은 폴더를 보는 상황 경고.

        로컬 JSONL 에는 계정 식별자가 없어서 log_dir 이 같으면 두 항목은 반드시
        같은 숫자를 보여준다 — "다른 계정을 보고 있다" 는 착각의 원인이라 짚어준다.
        """
        if account.kind != "claude":
            self.link_result.setText("")
            return
        pc = account.config
        for other in self.cfg.providers:
            if other is account or other.kind != "claude":
                continue
            opc = other.config
            if pc.account_email and opc.account_email == pc.account_email and opc.org_id == pc.org_id:
                self._set_link_result(
                    f"⚠ '{other.label}' 도 같은 계정·조직에 연결돼 있습니다. 값이 똑같이 나옵니다.",
                    False)
                return
            if (not pc.use_api and not opc.use_api
                    and pc.log_dir and opc.log_dir.lower() == pc.log_dir.lower()):
                self._set_link_result(
                    f"⚠ '{other.label}' 와 로그 폴더가 같습니다. 로컬 로그에는 계정 구분이 없어 "
                    f"두 항목이 항상 같은 값을 표시합니다. 계정을 나누려면 각각 세션 키를 등록하세요.",
                    False)
                return
        claude_count = sum(1 for a in self.cfg.providers if a.kind == "claude")
        if not pc.use_api and claude_count > 1:
            self._set_link_result(
                "ℹ 로컬 로그에는 계정 정보가 없어 이 항목은 계정을 구분하지 못합니다. "
                "세션 키를 등록하면 해당 계정의 실제 사용량이 표시됩니다.")
            return
        self.link_result.setText("")

    def _link_verify(self) -> None:
        from PyQt6.QtWidgets import QApplication

        account = self._current_account()
        if account is None:
            return
        pc = account.config

        typed = self.session_key.text().strip()
        if typed:
            save_session_key(typed, account.id)
            self.session_key.clear()
        key = load_session_key(account.id)
        if not key:
            self._set_link_result("⚠ 세션 키가 비어 있습니다. 위에 붙여넣고 다시 누르세요.", False)
            return

        self._set_link_result("⏳ 계정 확인 중...")
        QApplication.processEvents()

        info, err = fetch_account(key)
        if info is None:
            self._set_link_result(f"✗ {err}", False)
            return

        usable = [o for o in info.orgs if o.usable]
        if not usable:
            self._set_link_result("✗ 사용량을 조회할 수 있는 조직이 없습니다 (API 전용 계정).", False)
            return

        pc.account_email = info.email
        if pc.org_id not in [o.uuid for o in usable]:
            pc.org_id = usable[0].uuid
        pc.org_name = next(o.name for o in usable if o.uuid == pc.org_id)
        pc.use_api = True
        self.use_api.blockSignals(True)
        self.use_api.setChecked(True)
        self.use_api.blockSignals(False)

        self.org_box.blockSignals(True)
        self.org_box.clear()
        for o in usable:
            self.org_box.addItem(o.name or o.uuid[:8] + "…", o.uuid)
        self.org_box.setCurrentIndex([o.uuid for o in usable].index(pc.org_id))
        self.org_box.blockSignals(False)
        self.org_box.setEnabled(True)

        if self._is_default_label(account):
            account.label = info.email.split("@")[0] or account.label
            self.label.blockSignals(True)
            self.label.setText(account.label)
            self.label.blockSignals(False)

        usage, uerr = fetch_usage(key, pc.org_id)
        if usage is None:
            self._set_link_result(f"계정은 확인됐지만 사용량 조회 실패: {uerr}", False)
        else:
            self._set_link_result(
                f"✓ {info.email} 연결됨 — 5h {usage.five_hour_pct:.1f}% · "
                f"주간 {usage.seven_day_pct:.1f}%")

        self.link_who.setText(f"✓ 연결된 계정: <b>{pc.account_email}</b> · {pc.org_name}")
        self.link_who.setStyleSheet(f"color: {ACCENT}; font-size: 11px;")
        self.session_key.setPlaceholderText("(이 계정의 키가 저장돼 있음 — 바꾸려면 새 키 붙여넣기)")
        self._update_provider_fields()
        self._refresh_profile_mismatch(account)
        row = self.list.currentRow()
        self._refresh_list_keep_row(row)

    def _is_default_label(self, account: AccountConfig) -> bool:
        """사용자가 직접 지은 이름은 덮어쓰지 않는다."""
        base = {"claude": "Claude", "codex": "Codex", "gemini": "Gemini"}[account.kind]
        label = (account.label or "").strip()
        if not label or label == base:
            return True
        return label.startswith(base + " ") and label[len(base) + 1:].strip().isdigit()

    def _org_changed(self, _index: int) -> None:
        account = self._current_account()
        if account is None:
            return
        uuid = self.org_box.currentData() or ""
        if not uuid:
            return
        account.config.org_id = uuid
        account.config.org_name = self.org_box.currentText()

    def _link_clear(self) -> None:
        account = self._current_account()
        if account is None:
            return
        delete_session_key(account.id)
        pc = account.config
        pc.org_id = ""
        pc.org_name = ""
        pc.account_email = ""
        pc.use_api = False
        self.use_api.blockSignals(True)
        self.use_api.setChecked(False)
        self.use_api.blockSignals(False)
        self.session_key.clear()
        self._refresh_link_box(account)
        self._set_link_result("이 계정의 세션 키와 조직 정보를 삭제했습니다.")
        self._update_provider_fields()

    def _refresh_list_keep_row(self, row: int) -> None:
        self._refresh_list()
        self.list.setCurrentRow(row)

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

    def _move_selected(self, delta: int) -> None:
        """선택한 계정을 목록에서 한 칸 옮긴다. providers 순서가 곧 표시 순서다."""
        row = self.list.currentRow()
        new_row = row + delta
        if row < 0 or not (0 <= new_row < len(self.cfg.providers)):
            return
        providers = self.cfg.providers
        providers[row], providers[new_row] = providers[new_row], providers[row]
        self._current_row = -1          # 행이 바뀌었으니 폼을 새로 읽게 한다
        self._refresh_list()
        self.list.setCurrentRow(new_row)

    def _update_move_buttons(self, row: int) -> None:
        last = len(self.cfg.providers) - 1
        self.move_up_btn.setEnabled(0 < row <= last)
        self.move_down_btn.setEnabled(0 <= row < last)

    def _delete_selected(self) -> None:
        row = self.list.currentRow()
        if row < 0 or row >= len(self.cfg.providers):
            return
        self.cfg.providers.pop(row)
        self._refresh_list()
        self.list.setCurrentRow(min(row, len(self.cfg.providers) - 1))

    @staticmethod
    def _list_text(account: AccountConfig) -> str:
        state = "" if account.enabled else " (꺼짐)"
        who = account.config.account_email or account.kind
        return f"{account.label or account.kind.title()} · {who}{state}"

    def _refresh_list(self) -> None:
        self.list.blockSignals(True)
        self.list.clear()
        for account in self.cfg.providers:
            self.list.addItem(self._list_text(account))
        self.list.blockSignals(False)
        if self.list.currentRow() >= 0:
            self._load_selected(self.list.currentRow())

    def _load_selected(self, row: int) -> None:
        enabled = 0 <= row < len(self.cfg.providers)
        for widget in (
            self.enabled, self.label, self.kind, self.log_dir, self.window_min,
            self.token_limit, self.message_limit, self.use_api, self.delete_btn,
            self.session_key, self.link_verify_btn, self.link_clear_btn,
            self.profile_default, self.profile_custom, self.profile_open_btn,
            self.profile_launch_btn, self.profile_login_btn, self.profile_check_btn,
        ):
            widget.setEnabled(enabled)
        self._update_move_buttons(row)
        if not enabled:
            self._current_row = -1
            return
        if row != self._current_row:
            # 다른 계정으로 넘어갈 때만 입력 중이던 키를 버린다 (이름 수정 중엔 유지).
            self.session_key.clear()
            self.link_result.setText("")
            self._current_row = row
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
        self._refresh_link_box(account)
        self._refresh_profile_box(account)
        self._update_provider_fields()

    def _update_provider_fields(self, *_args) -> None:
        kind = self.kind.currentData() or "claude"
        descriptions = {
            "claude": (
                "claude.ai 계정을 연결하면 그 계정의 실제 사용량을 표시합니다. "
                "연결하지 않으면 로그 폴더 기준 추정값이며, 계정 구분이 되지 않습니다."
            ),
            "codex": "Codex 세션 로그가 보고한 사용률과 초기화 시간을 그대로 표시합니다.",
            "gemini": "로컬 기록의 메시지 수를 설정한 한도와 비교한 추정값입니다.",
        }
        self.provider_info.setText(descriptions[kind])
        self.link_box.setVisible(kind == "claude")
        self.profile_box.setVisible(kind == "claude")
        for widget, visible in (
            (self.token_limit, kind == "claude" and not self.use_api.isChecked()),
            (self.message_limit, kind == "gemini"),
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
        # 목록 전체를 다시 만들면 QLineEdit 이 잠깐 비활성화되며 포커스를 잃는다.
        # (표시 이름을 한 글자 치면 입력이 끊기던 원인) 해당 줄만 갱신한다.
        item = self.list.item(row)
        if item is not None:
            item.setText(self._list_text(account))

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
        on_restart: Callable[[], None] | None = None,
    ) -> None:
        super().__init__()
        self.cfg = cfg
        self._on_save = on_save
        self._on_reset_overlay = on_reset_overlay
        self._on_restart = on_restart
        self.setWindowTitle("Token Status — 설정")
        self.setObjectName("root")
        self.setStyleSheet(GLOBAL_QSS)
        self.setMinimumSize(720, 520)
        self.resize(900, 700)
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
        self.restart_btn = QPushButton("저장 후 재시작")
        self.restart_btn.setToolTip("설정을 저장하고 앱을 다시 실행합니다 (코드 수정 반영용)")
        self.restart_btn.clicked.connect(self._save_and_restart)
        self.restart_btn.setVisible(self._on_restart is not None)
        cancel = QPushButton("취소")
        cancel.clicked.connect(self.close)
        save = QPushButton("저장")
        save.setObjectName("primary")
        save.clicked.connect(self._save)
        btns.addWidget(log_btn)
        btns.addWidget(self.restart_btn)
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
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
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

    def _save_and_restart(self) -> None:
        if self._on_restart is None:
            return
        if not self._save():
            return                      # 저장 실패 시엔 재시작하지 않는다
        try:
            self._on_restart()
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "재시작 실패", str(e))

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

    def _save(self) -> bool:
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
            return False
        try:
            self.cfg.save()
        except OSError as e:
            log.exception("config save failed")
            QMessageBox.critical(self, "저장 실패", f"설정 파일 저장 중 오류: {e}")
            return False
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
        return True
