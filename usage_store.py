import datetime
import json
import os
import tempfile
import time
from zoneinfo import ZoneInfo

import requests
import streamlit as st

KST = ZoneInfo("Asia/Seoul")

GIST_FILE = "mtnw_groq_usage.json"
LOCAL_FILE = os.path.join(tempfile.gettempdir(), "mtnw_groq_usage.json")

CACHE_SECONDS = 5
_CACHE = {"at": 0.0, "data": None}


def _secret(key):
    try:
        value = st.secrets.get(key)
    except Exception:
        value = None

    return str(value or os.getenv(key, "")).strip()


def _today():
    return datetime.datetime.now(KST).strftime("%Y-%m-%d")


def _fresh():
    return {"date": _today(), "tokens": 0, "calls": 0}


def _gist_ready():
    return bool(_secret("GIST_TOKEN") and _secret("GIST_ID"))


def _gist_headers():
    return {
        "Authorization": f"Bearer {_secret('GIST_TOKEN')}",
        "Accept": "application/vnd.github+json",
    }


def _gist_url():
    return f"https://api.github.com/gists/{_secret('GIST_ID')}"


def _read_gist():
    response = requests.get(_gist_url(), headers=_gist_headers(), timeout=8)
    response.raise_for_status()

    content = response.json()["files"][GIST_FILE]["content"]

    return json.loads(content) if content.strip() else {}


def _write_gist(data):
    response = requests.patch(
        _gist_url(),
        headers=_gist_headers(),
        json={"files": {GIST_FILE: {"content": json.dumps(data)}}},
        timeout=8,
    )
    response.raise_for_status()


def _read_local():
    try:
        with open(LOCAL_FILE, "r", encoding="utf-8") as file:
            return json.load(file)
    except Exception:
        return {}


def _write_local(data):
    try:
        with open(LOCAL_FILE, "w", encoding="utf-8") as file:
            json.dump(data, file)
    except Exception:
        pass


def load_usage(force=False):
    """오늘 사용량. Gist가 설정돼 있으면 모든 사용자가 같은 값을 봅니다."""

    if (
        not force
        and _CACHE["data"] is not None
        and time.time() - _CACHE["at"] < CACHE_SECONDS
    ):
        data = _CACHE["data"]
    else:
        try:
            data = _read_gist() if _gist_ready() else _read_local()
        except Exception:
            data = _read_local()

    if not isinstance(data, dict) or data.get("date") != _today():
        data = _fresh()

    _CACHE["data"] = data
    _CACHE["at"] = time.time()

    return data


def add_usage(tokens):
    try:
        tokens = int(tokens)
    except (TypeError, ValueError):
        return

    data = dict(load_usage(force=True))

    data["tokens"] = int(data.get("tokens", 0)) + tokens
    data["calls"] = int(data.get("calls", 0)) + 1

    _write_local(data)

    if _gist_ready():
        try:
            _write_gist(data)
        except Exception:
            pass

    _CACHE["data"] = data
    _CACHE["at"] = time.time()
