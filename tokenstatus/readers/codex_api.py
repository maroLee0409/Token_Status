"""Codex live usage client.

Codex CLI keeps a ChatGPT OAuth credential in ``$CODEX_HOME/auth.json`` and
asks ``/backend-api/wham/usage`` for its ``/status`` numbers.  Session logs
only carry rate limits from the last turn, so they go stale the moment Codex
is idle; the live endpoint does not.

The token is never refreshed here — Codex rotates it itself and a competing
refresh would invalidate the CLI's copy.  On any failure the caller falls back
to the session logs.  Credentials are never logged or returned.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None  # type: ignore


_USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"


@dataclass
class CodexUsage:
    primary_pct: float
    primary_window_minutes: int
    primary_resets_at: Optional[float]
    secondary_pct: Optional[float]
    secondary_window_minutes: int
    secondary_resets_at: Optional[float]
    plan_type: str
    limit_reached: bool


def auth_path(log_dir: str) -> Path:
    """``auth.json`` next to the sessions folder (honours a custom CODEX_HOME)."""
    root = Path(log_dir) if (log_dir or "").strip() else Path.home() / ".codex" / "sessions"
    if root.name.lower() == "sessions":
        root = root.parent
    return root / "auth.json"


def _read_tokens(path: Path) -> tuple[dict[str, Any] | None, str]:
    try:
        tokens = json.loads(path.read_text(encoding="utf-8")).get("tokens")
    except FileNotFoundError:
        return None, "Codex 로그인 정보 없음"
    except (OSError, json.JSONDecodeError, AttributeError):
        return None, "Codex auth.json 을 읽을 수 없습니다."
    if not isinstance(tokens, dict) or not tokens.get("access_token"):
        return None, "Codex ChatGPT 로그인이 아닙니다 (API 키 모드)"
    return tokens, ""


def _window(raw: Any, now: float) -> tuple[Optional[float], int, Optional[float]]:
    if not isinstance(raw, dict):
        return None, 0, None
    try:
        pct = float(raw.get("used_percent"))
    except (TypeError, ValueError):
        pct = None
    minutes = int(raw.get("limit_window_seconds") or 0) // 60
    resets = raw.get("reset_at")
    if resets is None and raw.get("reset_after_seconds") is not None:
        resets = now + float(raw["reset_after_seconds"])
    return pct, minutes, float(resets) if resets is not None else None


def fetch_codex_usage(log_dir: str) -> tuple[CodexUsage | None, str]:
    if requests is None:
        return None, "requests 라이브러리가 설치되지 않았습니다."
    tokens, err = _read_tokens(auth_path(log_dir))
    if tokens is None:
        return None, err
    headers = {
        "Authorization": f"Bearer {tokens['access_token']}",
        "User-Agent": "codex_cli_rs",
    }
    if tokens.get("account_id"):
        headers["ChatGPT-Account-Id"] = str(tokens["account_id"])
    try:
        response = requests.get(_USAGE_URL, headers=headers, timeout=10)
    except Exception as exc:  # noqa: BLE001
        return None, f"Codex 사용량 네트워크 오류: {exc}"
    if response.status_code == 401:
        return None, "Codex 로그인 만료 — Codex 를 한 번 실행하면 갱신됩니다"
    if response.status_code != 200:
        return None, f"Codex 사용량 조회 실패 (HTTP {response.status_code})"
    try:
        data = response.json()
    except ValueError:
        return None, "Codex 사용량 응답이 JSON이 아닙니다."

    rate = data.get("rate_limit") if isinstance(data, dict) else None
    if not isinstance(rate, dict):
        return None, "Codex 응답에 사용량 데이터가 없습니다."
    now = time.time()
    p_pct, p_min, p_reset = _window(rate.get("primary_window"), now)
    s_pct, s_min, s_reset = _window(rate.get("secondary_window"), now)
    if p_pct is None and s_pct is None:
        return None, "Codex 응답에 사용량 데이터가 없습니다."
    return CodexUsage(
        primary_pct=p_pct or 0.0,
        primary_window_minutes=p_min,
        primary_resets_at=p_reset,
        secondary_pct=s_pct,
        secondary_window_minutes=s_min,
        secondary_resets_at=s_reset,
        plan_type=str(data.get("plan_type") or "unknown"),
        limit_reached=bool(rate.get("limit_reached")),
    ), ""
