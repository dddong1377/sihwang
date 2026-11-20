"""주식 대결(베타) 저장소.

시황글 저장소와 같은 Gist(GIST_TOKEN / GIST_ID)에 별도 파일(mtnw_arena.json)로 보관합니다.
Gist를 쓸 수 없으면 서버 임시 폴더에 저장합니다(앱 재시작 시 사라질 수 있음).
참가자가 20명 안팎인 베타용 구조이며, 저장 직전에 다시 읽어 합치는 방식으로 덮어쓰기를 줄입니다.
"""

import datetime
import hashlib
import hmac
import json
import os
import re
import secrets
import tempfile
from zoneinfo import ZoneInfo

import requests

try:
    import streamlit as st
except Exception:  # 테스트 환경
    st = None

KST = ZoneInfo("Asia/Seoul")

ARENA_FILE = "mtnw_arena.json"
LOCAL_FILE = os.path.join(tempfile.gettempdir(), "mtnw_arena.json")

NICK_PATTERN = re.compile(r"[0-9A-Za-z가-힣_]{2,12}")
MAX_FAILS = 5
LOCK_MINUTES = 5


# ---------------------------------------------------------------
# 저장소 입출력
# ---------------------------------------------------------------

def _secret(key):
    value = None

    if st is not None:
        try:
            value = st.secrets.get(key)
        except Exception:
            value = None

    return str(value or os.getenv(key, "")).strip()


def _gist_ready():
    return bool(_secret("GIST_TOKEN") and _secret("GIST_ID"))


def _headers():
    return {
        "Authorization": f"Bearer {_secret('GIST_TOKEN')}",
        "Accept": "application/vnd.github+json",
    }


def _url():
    return f"https://api.github.com/gists/{_secret('GIST_ID')}"


def blank():
    return {
        "users": {},
        "rounds": {},
        "picks": {},
        "prizes": {"list": [], "current": None},
    }


def _normalize(data):
    base = blank()

    if not isinstance(data, dict):
        return base

    for key, default in base.items():
        if not isinstance(data.get(key), type(default)):
            data[key] = default

    if not isinstance(data["prizes"].get("list"), list):
        data["prizes"]["list"] = []

    data["prizes"].setdefault("current", None)

    return data


def _read_gist():
    response = requests.get(_url(), headers=_headers(), timeout=8)
    response.raise_for_status()

    file = response.json().get("files", {}).get(ARENA_FILE)

    if not file:
        return {}

    content = file.get("content", "")

    return json.loads(content) if content.strip() else {}


def _write_gist(data):
    response = requests.patch(
        _url(),
        headers=_headers(),
        json={"files": {ARENA_FILE: {"content": json.dumps(data, ensure_ascii=False)}}},
        timeout=8,
    )
    response.raise_for_status()


def _read_local():
    try:
        with open(LOCAL_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _write_local(data):
    """임시 파일에 먼저 쓰고 교체해서, 쓰다 실패해도 기존 파일이 깨지지 않게 합니다."""

    temp = LOCAL_FILE + ".tmp"

    try:
        text = json.dumps(data, ensure_ascii=False)

        with open(temp, "w", encoding="utf-8") as f:
            f.write(text)

        os.replace(temp, LOCAL_FILE)
    except Exception:
        pass


def load():
    if _gist_ready():
        try:
            return _normalize(_read_gist())
        except Exception:
            pass

    return _normalize(_read_local())


def save(data):
    _write_local(data)

    if _gist_ready():
        _write_gist(data)  # 실패하면 예외를 올려 저장 실패를 알 수 있게 함


def update(mutator):
    """저장 직전에 다시 읽고(mutator로 고친 뒤) 저장합니다. mutator의 반환값을 그대로 돌려줍니다."""

    data = load()
    result = mutator(data)
    save(data)

    return result


# ---------------------------------------------------------------
# 닉네임 · PIN (data를 직접 고치므로 update() 안에서 쓰세요)
# ---------------------------------------------------------------

def nick_key(nick):
    return str(nick or "").strip().lower()


def hash_pin(pin, salt):
    return hashlib.sha256(f"{salt}:{pin}".encode("utf-8")).hexdigest()


def check_nick(nick):
    nick = str(nick or "").strip()

    if not NICK_PATTERN.fullmatch(nick):
        return "닉네임은 한글·영문·숫자·밑줄(_) 2~12자로 정해 주세요."

    return ""


def check_pin(pin):
    if not re.fullmatch(r"\d{4}", str(pin or "")):
        return "PIN은 숫자 4자리입니다."

    return ""


def register(data, nick, pin):
    """(성공 여부, 안내 문구)"""

    nick = str(nick or "").strip()
    pin = str(pin or "")

    problem = check_nick(nick) or check_pin(pin)

    if problem:
        return False, problem

    key = nick_key(nick)

    if key in data["users"]:
        return False, "이미 사용 중인 닉네임입니다."

    salt = secrets.token_hex(8)

    data["users"][key] = {
        "nick": nick,
        "salt": salt,
        "pin_hash": hash_pin(pin, salt),
        "created": datetime.datetime.now(KST).isoformat(),
        "fails": 0,
        "locked_until": "",
    }

    return True, "가입했습니다."


def verify(data, nick, pin, now=None):
    """(성공 여부, 안내 문구). 연속 5번 틀리면 5분간 잠급니다."""

    now = now or datetime.datetime.now(KST)
    user = data["users"].get(nick_key(nick))

    if not user:
        return False, "없는 닉네임입니다. '처음이에요'에서 먼저 가입해 주세요."

    locked = user.get("locked_until") or ""

    if locked:
        try:
            until = datetime.datetime.fromisoformat(locked)
        except ValueError:
            until = None

        if until and now < until:
            minutes = int((until - now).total_seconds() // 60) + 1
            return False, f"PIN을 여러 번 틀려 잠겼습니다. 약 {minutes}분 뒤에 다시 시도해 주세요."

    entered = hash_pin(str(pin or ""), user["salt"])

    if hmac.compare_digest(entered, user["pin_hash"]):
        user["fails"] = 0
        user["locked_until"] = ""
        return True, "로그인했습니다."

    user["fails"] = int(user.get("fails", 0)) + 1

    if user["fails"] >= MAX_FAILS:
        user["fails"] = 0
        user["locked_until"] = (now + datetime.timedelta(minutes=LOCK_MINUTES)).isoformat()
        return False, f"PIN을 {MAX_FAILS}번 틀려 {LOCK_MINUTES}분간 잠금됩니다."

    return False, f"PIN이 맞지 않습니다. ({user['fails']}/{MAX_FAILS})"
