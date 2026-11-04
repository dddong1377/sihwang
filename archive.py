import datetime
import json
import os
import tempfile
from zoneinfo import ZoneInfo

import requests
import streamlit as st

KST = ZoneInfo("Asia/Seoul")

GIST_FILE = "mtnw_archive.json"
LOCAL_FILE = os.path.join(tempfile.gettempdir(), "mtnw_archive.json")


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


def save_post(kind, markdown, day=None):
    """kind: 'pre' 또는 'aft'. day: datetime.date (기본 오늘)"""

    day = day or datetime.datetime.now(KST).date()

    data = _load()
    data.setdefault(day.isoformat(), {})[kind] = markdown

    _write_local(data)

    if _gist_ready():
        _write_gist(data)  # 실패하면 예외를 그대로 올려 저장 실패를 알 수 있게 함


def load_post(kind, day):
    return _load().get(day.isoformat(), {}).get(kind, "")
