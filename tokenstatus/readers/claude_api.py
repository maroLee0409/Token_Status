"""claude.ai web API client — fetches the exact usage % shown on claude.ai.

Auth: sessionKey 쿠키를 브라우저에서 복사해 쓴다. 계정마다 별도 슬롯
(`claude_session_key::<account_id>`)으로 Windows 자격 증명 관리자 / macOS 키체인에
저장한다 (config.json 에는 절대 남기지 않음).

  GET /api/account -> {"email_address": "...", "memberships": [{"organization": {...}}]}

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
# 레거시(계정 개념 도입 이전) 전역 슬롯. 마이그레이션 원본으로만 남겨둔다.
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


def _explain_403(resp) -> str:
    """403 의 실제 원인을 본문에서 읽어낸다.

    Cloudflare 차단과 세션 키 무효는 조치가 완전히 다르다. 전자는 기다리면
    되고, 후자는 키를 다시 등록해야 한다.
    """
    try:
        err = (resp.json() or {}).get("error") or {}
    except ValueError:
        return "Cloudflare 차단 (403). 잠시 후 재시도하세요."
    code = ((err.get("details") or {}).get("error_code") or "").lower()
    if code == "account_session_invalid" or "invalid authorization" in \
            str(err.get("message", "")).lower():
        return ("세션 키가 무효합니다 (403). 브라우저에서 다른 계정으로 로그인하면 "
                "기존 키가 만료됩니다 — 해당 계정으로 claude.ai 에 로그인한 뒤 "
                "sessionKey 를 다시 등록하세요.")
    msg = str(err.get("message") or "").strip()
    return f"권한 오류 (403){': ' + msg[:80] if msg else ''}"


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


@dataclass
class OrgInfo:
    uuid: str
    name: str
    usable: bool = True   # 사용량 조회가 가능한 조직인지 (chat capability 보유)


@dataclass
class AccountInfo:
    email: str
    full_name: str
    orgs: list


# ---------- keyring storage ----------

def _slot(account_id: str) -> str:
    """계정별 keyring 사용자명. account_id 가 비면 레거시 전역 슬롯."""
    account_id = (account_id or "").strip()
    return f"{KEYRING_USERNAME}::{account_id}" if account_id else KEYRING_USERNAME


def save_session_key(value: str, account_id: str = "") -> bool:
    if not keyring:
        return False
    try:
        keyring.set_password(KEYRING_SERVICE, _slot(account_id), value or "")
        return True
    except Exception:
        return False


def load_session_key(account_id: str = "") -> str:
    """계정 전용 키를 반환. 폴백 없음 — 계정마다 반드시 자기 키를 써야 한다."""
    if not keyring:
        return ""
    try:
        return keyring.get_password(KEYRING_SERVICE, _slot(account_id)) or ""
    except Exception:
        return ""


def delete_session_key(account_id: str = "") -> None:
    if not keyring:
        return
    try:
        keyring.delete_password(KEYRING_SERVICE, _slot(account_id))
    except Exception:
        pass


def migrate_legacy_session_key(account_id: str) -> bool:
    """레거시 전역 키를 지정 계정 슬롯으로 1회 이관. 옮겼으면 True."""
    if not keyring or not account_id:
        return False
    if load_session_key(account_id):
        return False           # 이미 자기 키가 있으면 건드리지 않는다
    legacy = load_session_key("")
    if not legacy:
        return False
    return save_session_key(legacy, account_id)


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


def fetch_account(session_key: str) -> tuple[Optional[AccountInfo], str]:
    """세션 키의 주인(이메일)과 소속 조직 목록을 반환. (None, 오류메시지) on failure.

    /api/organizations 는 이메일을 주지 않아 어떤 계정 키인지 구분이 안 된다.
    /api/account 는 email_address + memberships 를 한 번에 주므로 계정 선택 UI의 기준으로 쓴다.
    """
    try:
        s = _make_session(session_key)
        r = s.get(f"{_BASE}/api/account", timeout=10)
    except RuntimeError as e:
        return None, str(e)
    except Exception as e:  # noqa: BLE001
        return None, f"네트워크 오류: {e}"

    if r.status_code == 401:
        return None, "세션 키가 만료/잘못됨 (401). claude.ai 에서 다시 복사해 오세요."
    if r.status_code == 403:
        return None, _explain_403(r)
    if r.status_code != 200:
        return None, f"HTTP {r.status_code}: {r.text[:120]}"

    try:
        d = r.json()
    except ValueError:
        return None, "응답이 JSON이 아닙니다 (로그인 페이지 가능성)."

    orgs: list[OrgInfo] = []
    for m in d.get("memberships") or []:
        org = m.get("organization") if isinstance(m, dict) else None
        if not isinstance(org, dict) or not org.get("uuid"):
            continue
        caps = org.get("capabilities") or []
        # api_individual 전용 조직은 usage 엔드포인트가 403 을 준다. chat 보유 조직만 유효.
        orgs.append(OrgInfo(
            uuid=str(org["uuid"]),
            name=str(org.get("name") or ""),
            usable="chat" in caps,
        ))
    if not orgs:
        return None, "소속 조직이 없습니다."

    return AccountInfo(
        email=str(d.get("email_address") or ""),
        full_name=str(d.get("full_name") or ""),
        orgs=orgs,
    ), ""


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
        return None, _explain_403(r)
    if r.status_code != 200:
        return None, f"HTTP {r.status_code}: {r.text[:120]}"

    try:
        data = r.json()
    except ValueError:
        return None, "응답이 JSON이 아닙니다 (로그인 페이지 가능성)."

    if not isinstance(data, list) or not data:
        return None, "조직이 발견되지 않았습니다."
    # 사용량 조회가 가능한(chat) 조직 우선. api 전용 조직을 고르면 403 이 난다.
    org = next((o for o in data if "chat" in (o.get("capabilities") or [])), data[0])
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
        return None, _explain_403(r)
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
