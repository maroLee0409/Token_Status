"""계정별 Claude Code 프로필 관리.

CLAUDE_CONFIG_DIR 을 계정마다 따로 두면 설정·로그인 세션·대화 기록·MCP 가
전부 그 폴더 안으로 분리된다. 인증은 공식 `claude auth login` 이 처리하고,
이 모듈은 토큰 파일을 읽지도 쓰지도 않는다 — 어느 폴더를 볼지만 정해준다.

빈 문자열은 "기본 프로필" 을 뜻한다. 즉 CLAUDE_CONFIG_DIR 을 아예 넘기지 않고
기존 ~/.claude 를 그대로 쓴다. 이미 로그인돼 있는 계정을 건드리지 않기 위함.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from .config import CONFIG_DIR

log = logging.getLogger(__name__)

PROFILES_ROOT = CONFIG_DIR / "claude-profiles"

_IS_WIN = sys.platform.startswith("win")
_CREATE_NEW_CONSOLE = 0x00000010
_CREATE_NO_WINDOW = 0x08000000


# ---------- 경로 ----------

def sanitize_name(label: str) -> str:
    """표시 이름을 폴더명으로 쓸 수 있게 다듬는다."""
    name = re.sub(r"[^0-9A-Za-z가-힣_-]+", "-", (label or "").strip()).strip("-")
    return (name or "profile").lower()[:40]


def profile_path(name: str) -> Path:
    return PROFILES_ROOT / sanitize_name(name)


def unique_profile_path(label: str) -> Path:
    """이미 있는 폴더와 겹치지 않는 새 프로필 경로."""
    base = profile_path(label)
    if not base.exists():
        return base
    for n in range(2, 100):
        candidate = base.with_name(f"{base.name}-{n}")
        if not candidate.exists():
            return candidate
    return base


def is_default(config_dir: str) -> bool:
    """빈 값이면 기존 홈(~/.claude) 을 쓰는 기본 프로필."""
    return not (config_dir or "").strip()


# TokenStatus 자체가 Claude Code 안에서 실행되면(터미널에서 띄운 경우) 이 표식들이
# 앱에 상속되고, 앱이 띄운 Claude 는 자기를 "자식 세션" 으로 오인한다. 그 결과
# 대화 기록 저장이 꺼지는 등 정상 세션과 다르게 동작한다. 실행 전에 걷어낸다.
_SESSION_MARKERS = (
    "CLAUDECODE",
    "CLAUDE_CODE_CHILD_SESSION",
    "CLAUDE_CODE_ENTRYPOINT",
    "CLAUDE_CODE_EXECPATH",
    "CLAUDE_CODE_MESSAGING_SOCKET",
    "CLAUDE_CODE_MESSAGING_TOKEN",
    "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_CODE_SSE_PORT",
    "CLAUDE_EFFORT",
    "CLAUDE_PID",
)


def env_for(config_dir: str) -> dict[str, str]:
    """자식 프로세스에 넘길 환경. 앱 자신의 os.environ 은 절대 건드리지 않는다."""
    env = dict(os.environ)
    for marker in _SESSION_MARKERS:
        env.pop(marker, None)
    if is_default(config_dir):
        env.pop("CLAUDE_CONFIG_DIR", None)
    else:
        env["CLAUDE_CONFIG_DIR"] = str(Path(config_dir))
    return env


def projects_dir(config_dir: str) -> str:
    """이 프로필의 대화 로그 폴더. 사용량 판독기의 log_dir 로 그대로 쓴다.

    기본 배치는 ~/.claude/projects, 프로필 배치는 <프로필>/projects 다.
    """
    if is_default(config_dir):
        return str(Path.home() / ".claude" / "projects")
    return str(Path(config_dir) / "projects")


def credentials_exist(config_dir: str) -> bool:
    """로그인 흔적이 있는지 (내용은 읽지 않는다)."""
    root = Path.home() / ".claude" if is_default(config_dir) else Path(config_dir)
    return (root / ".credentials.json").is_file()


# ---------- claude 실행파일 ----------

def find_claude() -> str:
    """claude 실행 경로. 못 찾으면 빈 문자열."""
    for candidate in (("claude.cmd", "claude.exe", "claude") if _IS_WIN else ("claude",)):
        found = shutil.which(candidate)
        if found:
            return found
    # npm 전역 설치 기본 위치 (PATH 가 안 잡힌 경우)
    guesses = []
    if _IS_WIN:
        appdata = os.environ.get("APPDATA")
        if appdata:
            guesses.append(Path(appdata) / "npm" / "claude.cmd")
    guesses.append(Path.home() / ".local" / "bin" / ("claude.exe" if _IS_WIN else "claude"))
    for g in guesses:
        if g.is_file():
            return str(g)
    return ""


def _popen_args(exe: str, args: list[str]) -> list[str]:
    """.cmd 셰임은 cmd.exe 를 거쳐야 안전하게 실행된다."""
    if _IS_WIN and exe.lower().endswith((".cmd", ".bat")):
        return ["cmd", "/c", exe, *args]
    return [exe, *args]


# ---------- 인증 상태 ----------

def auth_status(config_dir: str, timeout: float = 20.0) -> tuple[dict | None, str]:
    """`claude auth status --json` 결과. (데이터, 오류메시지)"""
    exe = find_claude()
    if not exe:
        return None, "claude 실행 파일을 찾을 수 없습니다. Claude Code 가 설치돼 있는지 확인하세요."
    if not is_default(config_dir):
        try:
            Path(config_dir).mkdir(parents=True, exist_ok=True)
        except OSError as e:
            return None, f"프로필 폴더를 만들 수 없습니다: {e}"
    try:
        proc = subprocess.run(
            _popen_args(exe, ["auth", "status", "--json"]),
            env=env_for(config_dir),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=_CREATE_NO_WINDOW if _IS_WIN else 0,
        )
    except subprocess.TimeoutExpired:
        return None, "응답이 없습니다 (시간 초과)."
    except OSError as e:
        return None, f"실행 실패: {e}"
    out = (proc.stdout or "").strip()
    start = out.find("{")
    if start >= 0:
        try:
            return json.loads(out[start:]), ""
        except json.JSONDecodeError:
            pass
    err = (proc.stderr or "").strip() or out or f"종료 코드 {proc.returncode}"
    return None, err[:300]


def describe_status(data: dict | None) -> str:
    """auth status 결과를 한 줄로."""
    if not data:
        return ""
    if not data.get("loggedIn"):
        return "미로그인"
    email = data.get("email") or "(이메일 없음)"
    parts = [email]
    if data.get("orgName"):
        parts.append(str(data["orgName"]))
    if data.get("subscriptionType"):
        parts.append(str(data["subscriptionType"]))
    return " · ".join(parts)


# ---------- 새 콘솔로 실행 ----------

def _spawn_console(exe: str, args: list[str], config_dir: str,
                   cwd: str | None, keep_open: bool) -> tuple[bool, str]:
    env = env_for(config_dir)
    try:
        if _IS_WIN:
            # /k 는 명령이 끝나도 창을 남긴다 (로그인 결과를 눈으로 확인해야 하므로).
            cmdline = ["cmd", "/k" if keep_open else "/c", exe, *args]
            subprocess.Popen(cmdline, env=env, cwd=cwd or None,
                             creationflags=_CREATE_NEW_CONSOLE, close_fds=True)
        elif sys.platform == "darwin":
            inner = " ".join(_quote_posix(x) for x in [exe, *args])
            prefix = "" if is_default(config_dir) else \
                f"export CLAUDE_CONFIG_DIR={_quote_posix(config_dir)}; "
            cd = f"cd {_quote_posix(cwd)}; " if cwd else ""
            script = f'tell app "Terminal" to do script "{prefix}{cd}{inner}"'
            subprocess.Popen(["osascript", "-e", script,
                              "-e", 'tell app "Terminal" to activate'])
        else:
            for term, flag in (("x-terminal-emulator", "-e"), ("gnome-terminal", "--"),
                               ("konsole", "-e"), ("xterm", "-e")):
                if shutil.which(term):
                    subprocess.Popen([term, flag, exe, *args], env=env, cwd=cwd or None)
                    break
            else:
                return False, "터미널 프로그램을 찾지 못했습니다."
    except OSError as e:
        log.exception("claude 실행 실패")
        return False, f"실행 실패: {e}"
    return True, ""


def _quote_posix(value: str) -> str:
    return "'" + str(value).replace("'", "'\''") + "'"


def login(config_dir: str) -> tuple[bool, str]:
    """브라우저 OAuth 로그인. 대화형이라 새 콘솔 창에서 돌린다."""
    exe = find_claude()
    if not exe:
        return False, "claude 실행 파일을 찾을 수 없습니다."
    if not is_default(config_dir):
        try:
            Path(config_dir).mkdir(parents=True, exist_ok=True)
        except OSError as e:
            return False, f"프로필 폴더를 만들 수 없습니다: {e}"
    return _spawn_console(exe, ["auth", "login"], config_dir, None, keep_open=True)


def launch(config_dir: str, cwd: str | None = None,
           resume_id: str = "") -> tuple[bool, str]:
    """이 프로필로 Claude Code 실행. resume_id 를 주면 그 대화를 이어서 연다."""
    exe = find_claude()
    if not exe:
        return False, "claude 실행 파일을 찾을 수 없습니다."
    args = ["--resume", resume_id] if resume_id else []
    return _spawn_console(exe, args, config_dir, cwd, keep_open=False)


# ---------- 대화 이어받기 ----------

@dataclass
class Session:
    """프로필 안에 저장된 대화 한 건."""
    id: str
    config_dir: str      # 이 대화가 속한 프로필
    project: str         # projects/ 아래 폴더명 (작업 경로를 인코딩한 것)
    cwd: str             # 원래 작업 폴더
    mtime: float
    title: str


def _read_session_meta(path: Path) -> tuple[str, str]:
    """세션 파일에서 (cwd, 첫 사용자 메시지) 를 뽑는다. 앞부분만 읽는다."""
    cwd = ""
    title = ""
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            for _ in range(400):
                line = f.readline()
                if not line:
                    break
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not cwd and d.get("cwd"):
                    cwd = str(d["cwd"])
                if not title and d.get("type") == "user":
                    content = (d.get("message") or {}).get("content")
                    if isinstance(content, str):
                        title = content
                    elif isinstance(content, list):
                        for block in content:
                            if isinstance(block, dict) and block.get("type") == "text":
                                title = str(block.get("text", ""))
                                break
                if cwd and title:
                    break
    except OSError:
        pass
    title = " ".join(title.split())[:50]
    return cwd, title


def list_sessions(config_dir: str, limit: int = 8) -> list[Session]:
    """이 프로필의 최근 대화들 (최신순)."""
    root = Path(projects_dir(config_dir))
    if not root.is_dir():
        return []
    try:
        files = sorted(root.glob("*/*.jsonl"),
                       key=lambda p: p.stat().st_mtime, reverse=True)[:limit]
    except OSError:
        return []
    out: list[Session] = []
    for f in files:
        cwd, title = _read_session_meta(f)
        out.append(Session(
            id=f.stem, config_dir=config_dir, project=f.parent.name,
            cwd=cwd, mtime=f.stat().st_mtime, title=title or f.stem[:8],
        ))
    return out


def latest_session(config_dirs: list[str]) -> Session | None:
    """여러 프로필을 통틀어 가장 최근 대화."""
    best: Session | None = None
    for cd in config_dirs:
        for sess in list_sessions(cd, limit=1):
            if best is None or sess.mtime > best.mtime:
                best = sess
    return best


def transfer_session(session: Session, dst_config_dir: str) -> tuple[bool, str]:
    """대화 파일을 대상 프로필로 복사한다. 자격증명은 건드리지 않는다.

    같은 대화를 다른 계정에서 이어서 열기 위한 것이라, 옮기는 것은 기록뿐이다.
    이미 같은 이름의 파일이 있으면 최신 것으로 덮어쓴다 (같은 대화의 뒷부분).
    """
    src = Path(projects_dir(session.config_dir)) / session.project / f"{session.id}.jsonl"
    if not src.is_file():
        return False, "원본 대화 파일을 찾을 수 없습니다."
    dst_dir = Path(projects_dir(dst_config_dir)) / session.project
    try:
        dst_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst_dir / src.name)
    except OSError as e:
        log.exception("세션 복사 실패")
        return False, f"대화를 옮기지 못했습니다: {e}"
    return True, ""


def resume_in_profile(session: Session, dst_config_dir: str) -> tuple[bool, str]:
    """대화를 대상 프로필로 옮기고 그 계정으로 이어서 연다."""
    ok, err = transfer_session(session, dst_config_dir)
    if not ok:
        return False, err
    return launch(dst_config_dir, cwd=session.cwd or None, resume_id=session.id)

# ---------- 계정 금고 (실행 중인 세션의 계정 바꾸기) ----------
#
# Claude Code 는 config 폴더당 계정을 1개만 저장한다. 그런데 실행 중인 세션은
# 요청할 때마다 .credentials.json 을 디스크에서 다시 읽는다 (실측 확인).
# 그래서 파일을 갈아끼우면 이미 떠 있는 창들도 다음 요청부터 그 계정으로 동작한다.
#
# 단 계정 표시(이메일·조직)는 .claude.json 의 oauthAccount 에서 오고, 플랜은
# .credentials.json 에서 온다. 둘 중 하나만 바꾸면 "회사 이메일 · max 플랜" 같은
# 존재하지 않는 조합이 표시된다. 그래서 항상 둘을 함께 교체한다.
#
# 주의: 이건 공식 API 가 아니라 관찰된 내부 동작이다. 업데이트로 깨질 수 있다.

VAULT_ROOT = CONFIG_DIR / "account-vault"
_SWAP_BACKUP = CONFIG_DIR / "account-vault" / "_backup"
# 토큰 만료가 임박하면 세션이 갱신 결과를 파일에 되쓰면서 교체본과 충돌할 수 있다.
_EXPIRY_GUARD_SEC = 600


def _cred_path(config_dir: str) -> Path:
    root = Path.home() / ".claude" if is_default(config_dir) else Path(config_dir)
    return root / ".credentials.json"


def _claude_json_path(config_dir: str) -> Path:
    if is_default(config_dir):
        return Path.home() / ".claude.json"
    return Path(config_dir) / ".claude.json"


def read_identity(config_dir: str) -> dict | None:
    """이 프로필이 '누구' 로 표시되는지 (.claude.json 의 oauthAccount)."""
    try:
        data = json.loads(_claude_json_path(config_dir).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    acc = data.get("oauthAccount")
    return acc if isinstance(acc, dict) else None


def identity_email(config_dir: str) -> str:
    acc = read_identity(config_dir) or {}
    return str(acc.get("emailAddress") or "")


def _write_identity(config_dir: str, account: dict) -> None:
    """oauthAccount 만 갈아끼운다. 나머지 설정(프로젝트 신뢰·MCP 등)은 보존."""
    path = _claude_json_path(config_dir)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {}
    data["oauthAccount"] = account
    tmp = path.with_suffix(".json.tsnew")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def vault_slot(slot: str) -> Path:
    return VAULT_ROOT / sanitize_name(slot)


def vault_has(slot: str) -> bool:
    d = vault_slot(slot)
    return (d / ".credentials.json").is_file() and (d / "oauthAccount.json").is_file()


def vault_email(slot: str) -> str:
    try:
        acc = json.loads((vault_slot(slot) / "oauthAccount.json").read_text(encoding="utf-8"))
        return str(acc.get("emailAddress") or "")
    except (OSError, json.JSONDecodeError):
        return ""


def vault_capture(slot: str, config_dir: str) -> tuple[bool, str]:
    """이 프로필의 현재 로그인 상태를 금고에 담는다 (토큰 갱신분까지 최신으로)."""
    cred = _cred_path(config_dir)
    acc = read_identity(config_dir)
    if not cred.is_file():
        return False, "이 프로필에 로그인 정보가 없습니다."
    if not acc:
        return False, "계정 정보(.claude.json)를 읽지 못했습니다."
    d = vault_slot(slot)
    try:
        d.mkdir(parents=True, exist_ok=True)
        shutil.copy2(cred, d / ".credentials.json")
        (d / "oauthAccount.json").write_text(
            json.dumps(acc, indent=2, ensure_ascii=False), encoding="utf-8")
    except OSError as e:
        log.exception("금고 저장 실패")
        return False, f"저장 실패: {e}"
    return True, ""


def _expiry_ok(cred_file: Path) -> tuple[bool, str]:
    try:
        d = json.loads(cred_file.read_text(encoding="utf-8"))["claudeAiOauth"]
    except (OSError, json.JSONDecodeError, KeyError):
        return False, "자격증명 형식을 읽을 수 없습니다."
    now = time.time()
    if d.get("refreshTokenExpiresAt", 0) / 1000 <= now:
        return False, "이 계정의 로그인이 만료됐습니다. 다시 로그인해야 합니다."
    left = d.get("expiresAt", 0) / 1000 - now
    if 0 < left < _EXPIRY_GUARD_SEC:
        return False, (f"토큰 만료가 {int(left/60)}분 남아 지금 교체하면 충돌할 수 있습니다. "
                       "잠시 후 다시 시도하세요.")
    return True, ""


def switch_account(slot: str, config_dir: str = "",
                   capture_slot: str = "") -> tuple[bool, str]:
    """금고의 계정을 대상 프로필에 적용한다.

    capture_slot 을 주면 교체 전에 현재 상태를 그 슬롯으로 먼저 갈무리한다
    (그 사이 갱신된 토큰을 잃지 않기 위함).
    """
    src = vault_slot(slot)
    if not vault_has(slot):
        return False, "금고에 이 계정이 저장돼 있지 않습니다."
    ok, err = _expiry_ok(src / ".credentials.json")
    if not ok:
        return False, err

    if capture_slot and capture_slot != slot:
        vault_capture(capture_slot, config_dir)   # 실패해도 교체는 진행

    cred = _cred_path(config_dir)
    try:
        _SWAP_BACKUP.mkdir(parents=True, exist_ok=True)
        if cred.is_file():
            shutil.copy2(cred, _SWAP_BACKUP / ".credentials.json")
        prev = read_identity(config_dir)
        if prev:
            (_SWAP_BACKUP / "oauthAccount.json").write_text(
                json.dumps(prev, indent=2, ensure_ascii=False), encoding="utf-8")

        cred.parent.mkdir(parents=True, exist_ok=True)
        tmp = cred.with_suffix(".json.tsnew")
        shutil.copy2(src / ".credentials.json", tmp)
        tmp.replace(cred)
        _write_identity(config_dir, json.loads(
            (src / "oauthAccount.json").read_text(encoding="utf-8")))
    except OSError as e:
        log.exception("계정 교체 실패")
        return False, f"교체 실패: {e}"
    return True, ""


def undo_switch(config_dir: str = "") -> tuple[bool, str]:
    """직전 교체를 되돌린다."""
    cred_bak = _SWAP_BACKUP / ".credentials.json"
    acc_bak = _SWAP_BACKUP / "oauthAccount.json"
    if not cred_bak.is_file():
        return False, "되돌릴 백업이 없습니다."
    try:
        shutil.copy2(cred_bak, _cred_path(config_dir))
        if acc_bak.is_file():
            _write_identity(config_dir, json.loads(acc_bak.read_text(encoding="utf-8")))
    except OSError as e:
        return False, f"되돌리기 실패: {e}"
    return True, ""
