from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

from .base import ProviderSnapshot, cache_get, cache_put, cache_evict_missing


def _parse_ts(s: str) -> float:
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s).timestamp()
    except (ValueError, AttributeError):
        return 0.0


def _user_message_times(path: Path) -> list[float]:
    out: list[float] = []
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return out
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return out
    if isinstance(data, list):
        # logs.json format — list of {type, timestamp}
        for item in data:
            if not isinstance(item, dict):
                continue
            if item.get("type") != "user":
                continue
            ts = _parse_ts(item.get("timestamp", ""))
            if ts > 0:
                out.append(ts)
    elif isinstance(data, dict) and isinstance(data.get("messages"), list):
        for m in data["messages"]:
            if not isinstance(m, dict):
                continue
            if m.get("type") not in ("user", "human"):
                continue
            ts = _parse_ts(m.get("timestamp", ""))
            if ts > 0:
                out.append(ts)
    return out


def read_gemini(log_dir: str, window_minutes: int, message_limit: int) -> ProviderSnapshot:
    snap = ProviderSnapshot(
        name="Gemini",
        unit="messages",
        window_label=f"최근 {window_minutes // 60}시간 (메시지 수)",
        limit=message_limit,
    )
    root = Path(log_dir)
    if not root.exists():
        snap.note = f"로그 폴더 없음: {log_dir}"
        return snap

    # Gemini CLI logs are scattered under tmp/*/logs.json and tmp/*/chats/*.json
    candidates = list(root.rglob("logs.json")) + list(root.rglob("session-*.json"))
    if not candidates:
        snap.available = True
        snap.note = "Gemini CLI 로그 없음"
        return snap

    now = time.time()
    window_start = now - window_minutes * 60
    total_msgs = 0
    last_activity = 0.0
    paths_seen: set[str] = set()

    for f in candidates:
        try:
            stat = f.stat()
        except OSError:
            continue
        key = str(f)
        paths_seen.add(key)
        cached = cache_get(key, stat.st_mtime)
        if cached is None:
            cached = _user_message_times(f)
            cache_put(key, stat.st_mtime, cached)
        for ts in cached:
            if ts >= window_start:
                total_msgs += 1
            if ts > last_activity:
                last_activity = ts

    cache_evict_missing(paths_seen)

    snap.available = True
    snap.used = total_msgs
    snap.last_activity = last_activity or None
    if message_limit > 0:
        snap.percent = (total_msgs / message_limit) * 100.0
    snap.note = "토큰 X · 메시지 수 기반 (Gemini CLI는 토큰 카운트 미기록)"
    return snap
