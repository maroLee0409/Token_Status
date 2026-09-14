from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from .base import ProviderSnapshot
from .codex_api import fetch_codex_usage


_RECENT_FILES_TO_SCAN = 25


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
            chunk = min(size, 256 * 1024)
            f.seek(max(0, size - chunk))
            tail = f.read().decode("utf-8", errors="replace")
    except OSError:
        return None

    for line in reversed(tail.splitlines()):
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
        rate_limits = payload.get("rate_limits")
        if isinstance(rate_limits, dict):
            rate_limits["_timestamp"] = _parse_ts(obj.get("timestamp", ""))
            return rate_limits
    return None


def _fmt_window(minutes: int | float | None) -> str:
    try:
        minutes_i = int(minutes or 0)
    except (TypeError, ValueError):
        minutes_i = 0
    if minutes_i <= 0:
        return "Codex official"
    if minutes_i % 10080 == 0:
        weeks = minutes_i // 10080
        return f"{weeks}w (Codex official)"
    if minutes_i % 1440 == 0:
        days = minutes_i // 1440
        return f"{days}d (Codex official)"
    if minutes_i % 60 == 0:
        hours = minutes_i // 60
        return f"{hours}h (Codex official)"
    return f"{minutes_i}m (Codex official)"


def read_codex(log_dir: str, window_minutes: int, token_limit: int) -> ProviderSnapshot:
    """Live usage from the Codex backend, falling back to session logs."""
    usage, err = fetch_codex_usage(log_dir)
    if usage is None:
        snap = _read_codex_logs(log_dir)
        if snap.available and err:
            snap.note = f"{snap.note} | 로그 기준 ({err})" if snap.note else f"로그 기준 ({err})"
        return snap

    snap = ProviderSnapshot(name="Codex", unit="%", available=True, limit=100)
    snap.percent = usage.primary_pct
    snap.used = int(usage.primary_pct)
    snap.resets_at = usage.primary_resets_at
    snap.window_label = _fmt_window(usage.primary_window_minutes)
    snap.secondary_percent = usage.secondary_pct
    if usage.secondary_pct is not None:
        snap.secondary_label = _fmt_window(usage.secondary_window_minutes)
    notes = [f"plan: {usage.plan_type}", "Codex live usage"]
    if usage.limit_reached:
        notes.append("limit reached")
    snap.note = " | ".join(notes)
    try:
        latest = max(Path(log_dir).rglob("rollout-*.jsonl"), key=lambda p: p.stat().st_mtime)
        snap.last_activity = latest.stat().st_mtime
    except (ValueError, OSError):
        pass
    return snap


def _read_codex_logs(log_dir: str) -> ProviderSnapshot:
    snap = ProviderSnapshot(
        name="Codex",
        unit="%",
        window_label="Codex official",
    )
    root = Path(log_dir)
    if not root.exists():
        snap.note = f"Log folder not found: {log_dir}"
        return snap

    files = sorted(
        root.rglob("rollout-*.jsonl"),
        key=lambda p: p.stat().st_mtime if p.exists() else 0,
        reverse=True,
    )
    if not files:
        snap.available = True
        snap.note = "No Codex sessions found"
        return snap

    candidates: list[tuple[float, dict, Path]] = []
    last_path: Path | None = None
    for f in files[:_RECENT_FILES_TO_SCAN]:
        rate = _latest_rate_limit(f)
        if rate is not None:
            candidates.append((float(rate.get("_timestamp") or 0.0), rate, f))
        elif last_path is None:
            last_path = f

    if not candidates:
        snap.available = True
        snap.note = "No token_count rate-limit event in recent Codex sessions"
        try:
            snap.last_activity = files[0].stat().st_mtime
        except OSError:
            pass
        return snap

    _ts, rate, last_path = max(candidates, key=lambda item: item[0])
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
    snap.secondary_label = _fmt_window(secondary.get("window_minutes")) if secondary else None
    snap.window_label = _fmt_window(primary.get("window_minutes"))

    plan = rate.get("plan_type") or "unknown"
    notes = [f"plan: {plan}", "Codex reported rate_limits"]
    if primary_stale:
        notes.append("window reset")
    snap.note = " | ".join(notes)
    snap.last_activity = rate.get("_timestamp") or last_path.stat().st_mtime
    return snap
