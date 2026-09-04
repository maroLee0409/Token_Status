"""Claude Code OAuth usage client.

Claude Code already keeps a renewable OAuth credential in each
``CLAUDE_CONFIG_DIR``.  Reusing that credential avoids depending on the
short-lived claude.ai browser ``sessionKey`` cookie.  Credentials are never
logged or returned to callers.

The endpoint and refresh payload mirror the installed Claude Code client.  A
browser session key remains available as a legacy fallback in ``claude_api``.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

try:
    import requests
except ImportError:  # pragma: no cover - surfaced as a friendly runtime error
    requests = None  # type: ignore

from .claude_api import ApiUsage, _parse_iso


_USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
_TOKEN_URL = "https://platform.claude.com/v1/oauth/token"
# Claude Code's public OAuth client id. A credential-provided clientId wins so
# a future Claude release can migrate without requiring an app update.
_DEFAULT_CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
_OAUTH_BETA = "oauth-2025-04-20"
_REFRESH_EARLY_SEC = 120
_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def credentials_path(config_dir: str) -> Path:
    root = Path(config_dir) if (config_dir or "").strip() else Path.home() / ".claude"
    return root / ".credentials.json"


def oauth_credentials_available(config_dir: str) -> bool:
    """Whether this profile contains a structurally usable OAuth credential."""
    data, _ = _read_oauth(credentials_path(config_dir))
    return bool(data and data.get("accessToken") and data.get("refreshToken"))


def _path_lock(path: Path) -> threading.Lock:
    key = str(path.resolve())
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, threading.Lock())


def _read_oauth(path: Path) -> tuple[dict[str, Any] | None, str]:
    try:
        root = json.loads(path.read_text(encoding="utf-8"))
        oauth = root.get("claudeAiOauth")
        if not isinstance(oauth, dict):
            return None, "Claude OAuth 자격증명 형식이 올바르지 않습니다."
        return oauth, ""
    except FileNotFoundError:
        return None, "이 실행 프로필은 Claude Code 로그인이 필요합니다."
    except (OSError, json.JSONDecodeError):
        return None, "Claude OAuth 자격증명을 읽을 수 없습니다."


def _request_headers(access_token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {access_token}",
        "anthropic-beta": _OAUTH_BETA,
        "Content-Type": "application/json",
        "User-Agent": "claude-code",
    }


def _refresh_oauth(path: Path, oauth: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    """Refresh and atomically persist one profile's OAuth credential.

    Before replacing the file we re-read it. If Claude Code refreshed the same
    profile concurrently, its newer credential wins and our response is
    discarded. This prevents an older monitor response from overwriting the
    CLI's token rotation.
    """
    if requests is None:
        return None, "requests 라이브러리가 설치되지 않았습니다."
    refresh_token = str(oauth.get("refreshToken") or "")
    if not refresh_token:
        return None, "Claude OAuth refresh token이 없습니다. 다시 로그인해야 합니다."
    refresh_expires = float(oauth.get("refreshTokenExpiresAt") or 0)
    if refresh_expires and refresh_expires <= time.time() * 1000:
        return None, "Claude OAuth 로그인이 만료됐습니다. 이 프로필만 다시 로그인하세요."

    payload = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": str(oauth.get("clientId") or _DEFAULT_CLIENT_ID),
        "scope": " ".join(oauth.get("scopes") or []),
    }
    try:
        response = requests.post(
            _TOKEN_URL,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=15,
        )
    except Exception as exc:  # noqa: BLE001
        return None, f"OAuth 갱신 네트워크 오류: {exc}"
    if response.status_code != 200:
        if response.status_code in (400, 401, 403):
            return None, "Claude OAuth 갱신이 거부됐습니다. 이 프로필만 다시 로그인하세요."
        return None, f"Claude OAuth 갱신 실패 (HTTP {response.status_code})"
    try:
        refreshed = response.json()
    except ValueError:
        return None, "Claude OAuth 갱신 응답이 JSON이 아닙니다."
    access_token = str(refreshed.get("access_token") or "")
    if not access_token:
        return None, "Claude OAuth 갱신 응답에 access token이 없습니다."

    # Claude Code may have refreshed this profile while our request was in
    # flight. Never replace a newer rotated refresh token.
    latest, err = _read_oauth(path)
    if latest is None:
        return None, err
    if str(latest.get("refreshToken") or "") != refresh_token:
        return latest, ""

    try:
        root = json.loads(path.read_text(encoding="utf-8"))
        updated = dict(latest)
        updated["accessToken"] = access_token
        updated["refreshToken"] = str(refreshed.get("refresh_token") or refresh_token)
        updated["expiresAt"] = int(
            time.time() * 1000 + float(refreshed.get("expires_in") or 3600) * 1000
        )
        if refreshed.get("refresh_token_expires_in") is not None:
            updated["refreshTokenExpiresAt"] = int(
                time.time() * 1000
                + float(refreshed["refresh_token_expires_in"]) * 1000
            )
        scopes = refreshed.get("scope")
        if isinstance(scopes, str):
            updated["scopes"] = scopes.split()
        root["claudeAiOauth"] = updated

        tmp = path.with_name(path.name + ".tokenstatus-new")
        tmp.write_text(json.dumps(root, indent=2, ensure_ascii=False), encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        tmp.replace(path)
        return updated, ""
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None, "갱신된 Claude OAuth 자격증명을 저장하지 못했습니다."


def _ensure_access_token(path: Path) -> tuple[str, str]:
    with _path_lock(path):
        oauth, err = _read_oauth(path)
        if oauth is None:
            return "", err
        expires_at = float(oauth.get("expiresAt") or 0) / 1000
        if oauth.get("accessToken") and expires_at > time.time() + _REFRESH_EARLY_SEC:
            return str(oauth["accessToken"]), ""
        oauth, err = _refresh_oauth(path, oauth)
        if oauth is None:
            return "", err
        return str(oauth.get("accessToken") or ""), ""


def fetch_oauth_usage(config_dir: str) -> tuple[ApiUsage | None, str]:
    """Fetch exact usage using this Claude Code profile's renewable OAuth."""
    if requests is None:
        return None, "requests 라이브러리가 설치되지 않았습니다."
    path = credentials_path(config_dir)
    token, err = _ensure_access_token(path)
    if not token:
        return None, err

    def request_usage(access_token: str):
        return requests.get(
            _USAGE_URL,
            headers=_request_headers(access_token),
            timeout=10,
        )

    try:
        response = request_usage(token)
    except Exception as exc:  # noqa: BLE001
        return None, f"OAuth 사용량 네트워크 오류: {exc}"

    # An access token can be revoked just before expiresAt. Refresh once, while
    # retaining all other failures for the normal legacy/local fallback.
    if response.status_code == 401:
        with _path_lock(path):
            oauth, read_err = _read_oauth(path)
            if oauth is None:
                return None, read_err
            oauth["expiresAt"] = 0
            oauth, refresh_err = _refresh_oauth(path, oauth)
            if oauth is None:
                return None, refresh_err
            token = str(oauth.get("accessToken") or "")
        try:
            response = request_usage(token)
        except Exception as exc:  # noqa: BLE001
            return None, f"OAuth 사용량 네트워크 오류: {exc}"

    if response.status_code in (401, 403):
        return None, f"Claude OAuth 사용량 권한 오류 ({response.status_code})"
    if response.status_code != 200:
        return None, f"Claude OAuth 사용량 조회 실패 (HTTP {response.status_code})"
    try:
        data = response.json()
    except ValueError:
        return None, "Claude OAuth 사용량 응답이 JSON이 아닙니다."

    def section(name: str) -> tuple[float | None, float | None]:
        value = data.get(name)
        if not isinstance(value, dict):
            return None, None
        utilization = value.get("utilization")
        try:
            percent = float(utilization) if utilization is not None else None
        except (TypeError, ValueError):
            percent = None
        return percent, _parse_iso(value.get("resets_at"))

    five_hour, five_reset = section("five_hour")
    seven_day, seven_reset = section("seven_day")
    sonnet, _ = section("seven_day_sonnet")
    opus, _ = section("seven_day_opus")
    if five_hour is None and seven_day is None:
        return None, "Claude OAuth 응답에 사용량 데이터가 없습니다."
    return ApiUsage(
        five_hour_pct=five_hour or 0.0,
        five_hour_resets_at=five_reset,
        seven_day_pct=seven_day or 0.0,
        seven_day_resets_at=seven_reset,
        seven_day_sonnet_pct=sonnet,
        seven_day_opus_pct=opus,
        org_id="",
        org_name="",
    ), ""
