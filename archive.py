import datetime
import json
import os
import re
import tempfile
from zoneinfo import ZoneInfo

import requests
import streamlit as st

KST = ZoneInfo("Asia/Seoul")

GIST_FILE = "mtnw_archive.json"
LOCAL_FILE = os.path.join(tempfile.gettempdir(), "mtnw_archive.json")

# 시황글과 같은 저장소에 자동 실행 설정·기록도 함께 보관합니다.
# (날짜 키가 아닌 "_" 로 시작하는 키라서 글 목록에는 섞이지 않습니다.)
CONFIG_KEY = "_config"
RUNS_KEY = "_runs"

DEFAULT_TIMES = {"pre": "08:00", "aft": "14:30", "week": "15:40"}


def _secret(key):
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


def _read_gist():
    response = requests.get(_url(), headers=_headers(), timeout=8)
    response.raise_for_status()

    file = response.json().get("files", {}).get(GIST_FILE)

    if not file:
        return {}

    content = file.get("content", "")

    return json.loads(content) if content.strip() else {}


def _write_gist(data):
    response = requests.patch(
        _url(),
        headers=_headers(),
        json={
            "files": {
                GIST_FILE: {"content": json.dumps(data, ensure_ascii=False)}
            }
        },
        timeout=8,
    )
    response.raise_for_status()


def _read_local():
    try:
        with open(LOCAL_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_local(data):
    try:
        with open(LOCAL_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception:
        pass


def _load():
    if _gist_ready():
        try:
            data = _read_gist()
            return data if isinstance(data, dict) else {}
        except Exception:
            pass

    return _read_local()


def _save(data):
    _write_local(data)

    if _gist_ready():
        _write_gist(data)  # 실패하면 예외를 그대로 올려 저장 실패를 알 수 있게 함


def save_post(kind, markdown, day=None):
    """kind: 'pre' 또는 'aft'. day: datetime.date (기본 오늘)"""

    day = day or datetime.datetime.now(KST).date()

    data = _load()
    data.setdefault(day.isoformat(), {})[kind] = markdown

    _save(data)


def load_post(kind, day):
    return _load().get(day.isoformat(), {}).get(kind, "")


def list_posts(kind, limit=30):
    """저장된 글을 최신 날짜순으로 [(날짜 ISO, 본문), ...] 돌려줍니다."""

    data = _load()

    items = [
        (day, value[kind])
        for day, value in data.items()
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(day))
        and isinstance(value, dict)
        and value.get(kind)
    ]

    items.sort(reverse=True)

    return items[:limit]


# ---------------------------------------------------------------
# 자동 실행 설정 · 실행 기록
# ---------------------------------------------------------------

def valid_hhmm(value):
    return bool(re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", str(value or "")))


def load_config():
    """{'enabled': bool, 'times': {'pre': 'HH:MM', 'aft': ..., 'week': ...}}"""

    saved = _load().get(CONFIG_KEY)

    config = {"enabled": False, "times": dict(DEFAULT_TIMES)}

    if isinstance(saved, dict):
        config["enabled"] = bool(saved.get("enabled"))

        times = saved.get("times")

        if isinstance(times, dict):
            for kind in DEFAULT_TIMES:
                if valid_hhmm(times.get(kind)):
                    config["times"][kind] = times[kind]

    return config


def save_config(config):
    data = _load()
    data[CONFIG_KEY] = {
        "enabled": bool(config.get("enabled")),
        "times": {
            kind: (
                config.get("times", {}).get(kind)
                if valid_hhmm(config.get("times", {}).get(kind))
                else DEFAULT_TIMES[kind]
            )
            for kind in DEFAULT_TIMES
        },
    }

    _save(data)


def record_run(kind, ok, message, day=None):
    """자동 실행 결과 기록. ok: True 성공 / False 실패 / None 실행 중."""

    day = day or datetime.datetime.now(KST).date()

    data = _load()
    runs = data.setdefault(RUNS_KEY, {})

    today_runs = runs.setdefault(day.isoformat(), {})

    previous = today_runs.get(kind)
    attempts = int(previous.get("attempts", 0)) if isinstance(previous, dict) else 0

    # 시작 기록(ok=None)이 들어올 때마다 시도 횟수를 1 늘립니다.
    if ok is None:
        attempts += 1

    today_runs[kind] = {
        "ok": ok,
        "message": str(message or "")[:300],
        "at": datetime.datetime.now(KST).isoformat(timespec="seconds"),
        "attempts": attempts,
    }

    # 최근 14일 기록만 보관
    for old in sorted(runs)[:-14]:
        runs.pop(old, None)

    _save(data)


def load_run(kind, day=None):
    day = day or datetime.datetime.now(KST).date()

    runs = _load().get(RUNS_KEY, {})

    if not isinstance(runs, dict):
        return None

    value = runs.get(day.isoformat(), {}).get(kind)

    return value if isinstance(value, dict) else None
