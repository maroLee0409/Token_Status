from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from .base import ProviderSnapshot


def _parse_ts(s: str) -> float:
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s).timestamp()
    except (ValueError, AttributeError):
        return 0.0


def _latest_rate_limit(path: Path) -> Optional[dict]:
    """Walk a JSONL file from the bottom and return the most recent rate_limits dict."""
    try:
        with path.open("rb") as f:
            f.seek(0, 2)
            size = f.tell()
            # Read in chunks from the end backwards (cap at 256KB for safety).
            chunk = min(size, 256 * 1024)
            f.seek(max(0, size - chunk))
            tail = f.read().decode("utf-8", errors="replace")
    except OSError:
        return None
    lines = tail.splitlines()
    for line in reversed(lines):
        line = line.strip()
        if not line or '"token_count"' not in line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        payload = obj.get("payload")
        if not isinstance(payload, dict) or payload.get("type") != "token_count":
            continue
        rl = payload.get("rate_limits")
        if isinstance(rl, dict):
            rl["_timestamp"] = _parse_ts(obj.get("timestamp", ""))
            return rl
    return None


def read_codex(log_dir: str, window_minutes: int, token_limit: int) -> ProviderSnapshot:
    snap = ProviderSnapshot(
        name="Codex",
        unit="%",
        window_label="5시간 (Codex 공식)",
    )
    root = Path(log_dir)
    if not root.exists():
        snap.note = f"로그 폴더 없음: {log_dir}"
        return snap

    files = sorted(
        root.rglob("rollout-*.jsonl"),
        key=lambda p: p.stat().st_mtime if p.exists() else 0,
        reverse=True,
    )
    if not files:
        snap.available = True
        snap.note = "Codex 세션 없음"
        return snap

    # Look at the 5 most recent files until we find a token_count event.
    rate = None
    last_path = None
    for f in files[:5]:
        rate = _latest_rate_limit(f)
        if rate is not None:
            last_path = f
            break

    if rate is None:
        snap.available = True
        snap.note = "최근 세션에 token_count 이벤트 없음"
        if last_path is None and files:
            try:
                snap.last_activity = files[0].stat().st_mtime
            except OSError:
                pass
        return snap

    primary = rate.get("primary") or {}
    secondary = rate.get("secondary") or {}
    now = time.time()
    primary_pct = float(primary.get("used_percent") or 0.0)
    primary_resets = primary.get("resets_at")
    primary_stale = bool(primary_resets and now >= float(primary_resets))
    if primary_stale:
        primary_pct = 0.0

    secondary_pct = float(secondary.get("used_percent") or 0.0) if secondary else None
    secondary_resets = secondary.get("resets_at") if secondary else None
    if secondary_pct is not None and secondary_resets and now >= float(secondary_resets):
        secondary_pct = 0.0

    snap.available = True
    snap.percent = primary_pct
    snap.limit = 100
    snap.used = int(primary_pct)
    snap.resets_at = primary_resets
    snap.secondary_percent = secondary_pct
    snap.secondary_label = (
        f"주간 (≈{(secondary.get('window_minutes') or 0) // 60}h)"
        if secondary else None
    )
    plan = rate.get("plan_type") or "?"
    notes = [f"플랜: {plan}", "Codex 자체 보고 %"]
    if primary_stale:
        notes.append("윈도우 리셋됨")
    snap.note = " · ".join(notes)
    snap.last_activity = rate.get("_timestamp") or (last_path.stat().st_mtime if last_path else None)
    return snap
