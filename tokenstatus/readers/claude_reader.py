from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from .base import ProviderSnapshot, cache_get, cache_put, cache_evict_missing
from .claude_api import fetch_usage, load_session_key
from .claude_oauth import fetch_oauth_usage


def detect_local_account(log_dir: str) -> str:
    """log_dir 이 속한 Claude Code 설치의 로그인 이메일을 찾는다.

    로컬 JSONL 에는 계정 식별자가 없다(sessionId 뿐). 대신 같은 설치의
    .claude.json 안 oauthAccount.emailAddress 로 "이 폴더 = 어느 계정" 을 알아낸다.
    기본 배치(~/.claude/projects)와 CLAUDE_CONFIG_DIR 배치 둘 다 지원.
    """
    root = Path(log_dir)
    if root.name.lower() != "projects":
        # ~/.codex/sessions 같은 남의 폴더에서 홈의 .claude.json 을 주워오지 않도록.
        return ""
    # 기본: ~/.claude/projects → ~/.claude.json / CLAUDE_CONFIG_DIR: <dir>/projects → <dir>/.claude.json
    for candidate in (root.parent.parent / ".claude.json", root.parent / ".claude.json"):
        try:
            if not candidate.is_file():
                continue
            data = json.loads(candidate.read_text(encoding="utf-8", errors="replace"))
        except (OSError, json.JSONDecodeError):
            continue
        account = data.get("oauthAccount")
        if isinstance(account, dict) and account.get("emailAddress"):
            return str(account["emailAddress"])
    return ""


def _parse_ts(s: str) -> float:
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s).timestamp()
    except (ValueError, AttributeError):
        return 0.0


# Weights aligned with Anthropic public pricing ratios. Cache reads are billed at
# ~10% of normal input, so naive summing wildly overstates real quota usage.
_W_INPUT = 1.0
_W_OUTPUT = 1.0
_W_CACHE_WRITE = 1.25
_W_CACHE_READ = 0.1


def _scan_file(path: Path) -> list[tuple[float, float, int]]:
    """Return [(timestamp, weighted_tokens, message_count)] for each usage line."""
    entries: list[tuple[float, float, int]] = []
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line or not line.startswith("{") or '"usage"' not in line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                msg = obj.get("message")
                if not isinstance(msg, dict):
                    continue
                usage = msg.get("usage")
                if not isinstance(usage, dict):
                    continue
                inp = int(usage.get("input_tokens") or 0)
                out = int(usage.get("output_tokens") or 0)
                cw = int(usage.get("cache_creation_input_tokens") or 0)
                cr = int(usage.get("cache_read_input_tokens") or 0)
                weighted = (
                    inp * _W_INPUT
                    + out * _W_OUTPUT
                    + cw * _W_CACHE_WRITE
                    + cr * _W_CACHE_READ
                )
                if weighted <= 0:
                    continue
                ts = _parse_ts(obj.get("timestamp", ""))
                if ts > 0:
                    entries.append((ts, weighted, 1))
    except OSError:
        return []
    return entries


def _compute_active_block(
    entries: list[tuple[float, float, int]], block_hours: float = 5.0
) -> tuple[float | None, float, int, float]:
    """Anthropic-style fixed session blocks.

    A block starts at the timestamp of the first message after any expired block,
    and lasts exactly `block_hours` from that start. Subsequent messages within
    the block window count toward it. The next message after the block expires
    starts a new block.

    Returns (block_start, weighted_in_block, msgs_in_block, last_msg_ts_in_block).
    block_start is None if no block is currently active (last block expired).
    """
    if not entries:
        return None, 0.0, 0, 0.0
    entries = sorted(entries, key=lambda x: x[0])
    block_dur = block_hours * 3600.0
    now = time.time()

    cur_start = entries[0][0]
    cur_w = 0.0
    cur_m = 0
    cur_last = entries[0][0]
    for ts, w, m in entries:
        if ts <= cur_start + block_dur:
            cur_w += w
            cur_m += m
            cur_last = ts
        else:
            # current block expired at cur_start + block_dur; start new block at this msg
            cur_start = ts
            cur_w = w
            cur_m = m
            cur_last = ts

    if now <= cur_start + block_dur:
        return cur_start, cur_w, cur_m, cur_last
    # Most recent block already expired.
    return None, 0.0, 0, cur_last


def _snapshot_from_api(api_usage, account_email: str = "", source: str = "claude.ai API") -> ProviderSnapshot:
    snap = ProviderSnapshot(
        name="Claude Code",
        unit="%",
        window_label="5h (claude.ai 공식)",
        limit=100,
    )
    snap.available = True
    snap.percent = api_usage.five_hour_pct
    snap.used = int(api_usage.five_hour_pct)
    snap.resets_at = api_usage.five_hour_resets_at
    snap.secondary_percent = api_usage.seven_day_pct
    snap.secondary_label = "주간"
    bits = [f"{source} · {account_email}" if account_email else source]
    if api_usage.seven_day_sonnet_pct is not None:
        bits.append(f"Sonnet 주간 {api_usage.seven_day_sonnet_pct:.1f}%")
    if api_usage.seven_day_opus_pct is not None:
        bits.append(f"Opus 주간 {api_usage.seven_day_opus_pct:.1f}%")
    snap.note = " · ".join(bits)
    return snap


def read_claude(log_dir: str, window_minutes: int, token_limit: int,
                *, use_api: bool = False, org_id: str = "",
                account_id: str = "", account_email: str = "",
                claude_config_dir: str = "") -> ProviderSnapshot:
    # Prefer the renewable OAuth credential already owned by this isolated
    # Claude Code profile. The browser sessionKey is legacy fallback only.
    fallback_reason = ""
    if use_api:
        usage, oauth_err = fetch_oauth_usage(claude_config_dir)
        if usage is not None:
            return _snapshot_from_api(
                usage, account_email or detect_local_account(log_dir),
                source="Claude Code OAuth",
            )
        session_key = load_session_key(account_id)
        if session_key:
            usage, err = fetch_usage(session_key, org_id)
            if usage is not None:
                return _snapshot_from_api(usage, account_email, source="claude.ai 세션(호환)")
            fallback_reason = (
                f"OAuth 실패({oauth_err}) · 웹 세션 실패({err}) → 로컬 로그"
            )
        else:
            fallback_reason = f"OAuth 실패({oauth_err}) → 로컬 로그"

    block_hours = max(1, window_minutes / 60.0)
    snap = ProviderSnapshot(
        name="Claude Code",
        unit="tokens",
        window_label=f"{block_hours:.0f}h 세션 블록",
        limit=token_limit,
    )
    root = Path(log_dir)
    if not root.exists():
        snap.note = " · ".join(filter(None, [fallback_reason, f"로그 폴더 없음: {log_dir}"]))
        return snap

    paths_seen: set[str] = set()
    files = list(root.rglob("*.jsonl"))
    if not files:
        snap.available = True
        snap.note = " · ".join(filter(None, [fallback_reason, "JSONL 로그 없음 (Claude Code 사용 기록 없음)"]))
        return snap

    all_entries: list[tuple[float, float, int]] = []
    for f in files:
        try:
            stat = f.stat()
        except OSError:
            continue
        key = str(f)
        paths_seen.add(key)
        cached = cache_get(key, stat.st_mtime)
        if cached is None:
            cached = _scan_file(f)
            cache_put(key, stat.st_mtime, cached)
        all_entries.extend(cached)

    cache_evict_missing(paths_seen)

    block_start, block_w, block_m, last_act = _compute_active_block(all_entries, block_hours)
    snap.available = True
    snap.last_activity = last_act or None

    if block_start is None:
        snap.used = 0
        snap.percent = 0.0
        snap.note = " · ".join(filter(None, [fallback_reason, "활성 세션 블록 없음 · 다음 메시지가 새 블록 시작"]))
        return snap

    snap.used = int(block_w)
    if token_limit > 0:
        snap.percent = (block_w / token_limit) * 100.0
    snap.resets_at = block_start + block_hours * 3600.0
    # Window label includes block-start clock time so user can correlate w/ claude.ai
    try:
        from datetime import datetime
        start_clk = datetime.fromtimestamp(block_start).strftime("%H:%M")
        snap.window_label = f"{block_hours:.0f}h 블록 (시작 {start_clk})"
    except (OSError, ValueError):
        pass
    bits = [f"가중 토큰 (캐시 read ×0.1) · 메시지 {block_m}회"]
    local_email = detect_local_account(log_dir)
    if local_email:
        bits.append(f"로그 계정: {local_email}")
    if fallback_reason:
        bits.insert(0, fallback_reason)
    snap.note = " · ".join(bits)
    return snap
