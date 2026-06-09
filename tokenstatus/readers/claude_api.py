"""claude.ai web API client — fetches the exact usage % shown on claude.ai.

Auth: a single sessionKey cookie copied from the user's logged-in browser.
The key is stored in Windows Credential Manager via the `keyring` library
(never in plain config.json).

Endpoint shape (cross-verified from ClaudeMeter + lugia19's Claude-Usage-Extension):
  GET /api/organizations                            -> [{"uuid": "...", "name": "..."}]
  GET /api/organizations/{org_id}/usage             ->
    {
      "five_hour":        {"utilization": 42.7, "resets_at": "2026-05-20T18:00:00Z"},
      "seven_day":        {"utilization": 18.3, "resets_at": "..."},
      "seven_day_sonnet": {"utilization": 12.0, "resets_at": "..."},  # optional
      "seven_day_opus":   {"utilization":  5.0, "resets_at": "..."}   # optional
    }
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

try:
    import requests
except ImportError:  # pragma: no cover — surfaced at runtime in settings UI
    requests = None  # type: ignore

try:
    import keyring
except ImportError:  # pragma: no cover
    keyring = None  # type: ignore


KEYRING_SERVICE = "TokenStatus"
KEYRING_USERNAME = "claude_session_key"

_BASE = "https://claude.ai"

# Browser-like headers to avoid Cloudflare 403. Copied from ClaudeMeter's NetworkService.
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/127.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://claude.ai/",
    "Origin": "https://claude.ai",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Dest": "empty",
}


@dataclass
class ApiUsage:
    five_hour_pct: float
    five_hour_resets_at: Optional[float]   # unix ts
    seven_day_pct: float
    seven_day_resets_at: Optional[float]
    seven_day_sonnet_pct: Optional[float]
    seven_day_opus_pct: Optional[float]
    org_id: str
    org_name: str = ""


# ---------- keyring storage ----------

def save_session_key(value: str) -> bool:
    if not keyring:
        return False
    try:
        keyring.set_password(KEYRING_SERVICE, KEYRING_USERNAME, value or "")
        return True
    except Exception:
        return False


def load_session_key() -> str:
    if not keyring:
        return ""
    try:
        return keyring.get_password(KEYRING_SERVICE, KEYRING_USERNAME) or ""
    except Exception:
        return ""


def delete_session_key() -> None:
    if not keyring:
        return
    try:
        keyring.delete_password(KEYRING_SERVICE, KEYRING_USERNAME)
    except Exception:
        pass


# ---------- HTTP ----------

def _parse_iso(ts: str | None) -> Optional[float]:
    if not ts:
        return None
    try:
        s = ts.replace("Z", "+00:00")
        return datetime.fromisoformat(s).timestamp()
    except (ValueError, TypeError):
        return None


def _make_session(session_key: str):
    if requests is None:
        raise RuntimeError("requests 라이브러리가 설치되지 않았습니다. install.bat 재실행 필요.")
    s = requests.Session()
    s.cookies.set("sessionKey", session_key, domain="claude.ai")
    s.headers.update(_HEADERS)
    return s


def discover_org(session_key: str) -> tuple[Optional[str], str]:
    """Return (uuid, name) for the user's first organization, or (None, error_message)."""
    try:
        s = _make_session(session_key)
        r = s.get(f"{_BASE}/api/organizations", timeout=10)
    except RuntimeError as e:
        return None, str(e)
    except Exception as e:  # noqa: BLE001
        return None, f"네트워크 오류: {e}"

    if r.status_code == 401:
        return None, "세션 키가 만료/잘못됨 (401). claude.ai 에서 다시 복사해 오세요."
    if r.status_code == 403:
        return None, "Cloudflare 차단 (403). 잠시 후 재시도하거나 User-Agent 검토 필요."
    if r.status_code != 200:
        return None, f"HTTP {r.status_code}: {r.text[:120]}"

    try:
        data = r.json()
    except ValueError:
        return None, "응답이 JSON이 아닙니다 (로그인 페이지 가능성)."

    if not isinstance(data, list) or not data:
        return None, "조직이 발견되지 않았습니다."
    org = data[0]
    return org.get("uuid"), org.get("name", "")


def fetch_usage(session_key: str, org_id: str) -> tuple[Optional[ApiUsage], str]:
    """Fetch usage. Returns (usage, error_msg). usage=None means failure."""
    if not session_key:
        return None, "세션 키 없음"
    if not org_id:
        org_id, name_or_err = discover_org(session_key)
        if not org_id:
            return None, name_or_err  # error message
    else:
        name_or_err = ""

    try:
        s = _make_session(session_key)
        r = s.get(f"{_BASE}/api/organizations/{org_id}/usage", timeout=10)
    except Exception as e:  # noqa: BLE001
        return None, f"네트워크 오류: {e}"

    if r.status_code == 401:
        return None, "세션 키 만료 (401)"
    if r.status_code == 403:
        return None, "Cloudflare 차단 (403)"
    if r.status_code == 404:
        return None, "조직 ID 잘못됨 (404). 자동 재검색하세요."
    if r.status_code != 200:
        return None, f"HTTP {r.status_code}"

    try:
        d = r.json()
    except ValueError:
        return None, "응답이 JSON이 아님"

    def _util(key: str) -> tuple[Optional[float], Optional[float]]:
        sec = d.get(key)
        if not isinstance(sec, dict):
            return None, None
        u = sec.get("utilization")
        return (float(u) if u is not None else None), _parse_iso(sec.get("resets_at"))

    fh_u, fh_r = _util("five_hour")
    sd_u, sd_r = _util("seven_day")
    son_u, _ = _util("seven_day_sonnet")
    opu_u, _ = _util("seven_day_opus")

    if fh_u is None and sd_u is None:
        return None, "응답에 사용량 데이터 없음"

    return ApiUsage(
        five_hour_pct=fh_u or 0.0,
        five_hour_resets_at=fh_r,
        seven_day_pct=sd_u or 0.0,
        seven_day_resets_at=sd_r,
        seven_day_sonnet_pct=son_u,
        seven_day_opus_pct=opu_u,
        org_id=org_id,
        org_name=name_or_err if isinstance(name_or_err, str) else "",
    ), ""
