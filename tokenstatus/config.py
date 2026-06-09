from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any


APP_NAME = "TokenStatus"


def _config_dir() -> Path:
    """Per-OS application data directory."""
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    if sys.platform.startswith("win"):
        base = os.environ.get("APPDATA") or str(Path.home())
        return Path(base) / APP_NAME
    # Linux / other — follow XDG.
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / APP_NAME


CONFIG_DIR = _config_dir()
CONFIG_PATH = CONFIG_DIR / "config.json"


@dataclass
class ProviderConfig:
    enabled: bool = True
    log_dir: str = ""
    window_minutes: int = 300
    token_limit: int = 500_000
    message_limit: int = 200
    # claude.ai web API ("official" mode) — Claude only, ignored elsewhere.
    use_api: bool = False
    org_id: str = ""


DISPLAY_MODES = ("standard", "compact", "minimal", "overlay")


@dataclass
class Config:
    refresh_seconds: int = 30
    autostart: bool = False
    show_window_on_click: bool = True
    tray_show_max_only: bool = True
    display_mode: str = "standard"  # standard | compact | minimal | overlay
    overlay_x: int = -1   # -1 means "place at default (top-right)"
    overlay_y: int = -1
    overlay_w: int = 240
    overlay_h: int = 110
    overlay_opacity: float = 0.85
    overlay_locked: bool = False  # lock position + size (disable drag/resize)
    claude: ProviderConfig = field(default_factory=lambda: ProviderConfig(
        log_dir=str(Path.home() / ".claude" / "projects"),
        token_limit=29_000_000,  # Max 5x 기준. 다른 플랜이면 설정창 프리셋 / 🎯 보정 사용.
    ))
    codex: ProviderConfig = field(default_factory=lambda: ProviderConfig(
        log_dir=str(Path.home() / ".codex" / "sessions"),
        token_limit=100,  # Codex는 자체 % 보고하므로 한도는 100으로 고정.
    ))
    gemini: ProviderConfig = field(default_factory=lambda: ProviderConfig(
        log_dir=str(Path.home() / ".gemini"),
        message_limit=1500,
    ))

    @classmethod
    def load(cls) -> "Config":
        if not CONFIG_PATH.exists():
            cfg = cls()
            cfg.save()
            return cfg
        try:
            raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return cls()
        return cls._from_dict(raw)

    @classmethod
    def _from_dict(cls, raw: dict[str, Any]) -> "Config":
        cfg = cls()
        for key in ("refresh_seconds", "autostart", "show_window_on_click",
                    "tray_show_max_only", "display_mode",
                    "overlay_x", "overlay_y", "overlay_w", "overlay_h",
                    "overlay_opacity", "overlay_locked"):
            if key in raw:
                setattr(cfg, key, raw[key])
        if cfg.display_mode not in DISPLAY_MODES:
            cfg.display_mode = "standard"
        for provider in ("claude", "codex", "gemini"):
            if provider in raw and isinstance(raw[provider], dict):
                current = getattr(cfg, provider)
                for k, v in raw[provider].items():
                    if hasattr(current, k):
                        setattr(current, k, v)
        return cfg

    def save(self) -> None:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        data = {
            "refresh_seconds": self.refresh_seconds,
            "autostart": self.autostart,
            "show_window_on_click": self.show_window_on_click,
            "tray_show_max_only": self.tray_show_max_only,
            "display_mode": self.display_mode,
            "overlay_x": self.overlay_x,
            "overlay_y": self.overlay_y,
            "overlay_w": self.overlay_w,
            "overlay_h": self.overlay_h,
            "overlay_opacity": self.overlay_opacity,
            "overlay_locked": self.overlay_locked,
            "claude": asdict(self.claude),
            "codex": asdict(self.codex),
            "gemini": asdict(self.gemini),
        }
        CONFIG_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


# Empirically calibrated against claude.ai's own % indicator (Max 5x ≈ 29M weighted
# tokens per 5h window, measured on a real account). Earlier estimates were ~4x too
# small. For exact alignment with any specific account, use the 🎯 calibration UI.
CLAUDE_PRESETS: dict[str, int | None] = {
    "Pro": 6_000_000,
    "Max 5x": 29_000_000,
    "Max 20x": 116_000_000,
    "Custom": None,
}

GEMINI_PRESETS: dict[str, int | None] = {
    "Free (60/min · ~1500/day)": 1500,
    "Tier 1": 5000,
    "Custom": None,
}
