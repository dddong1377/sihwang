import datetime
import json
import os
import tempfile
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")
ARCHIVE_FILE = os.path.join(tempfile.gettempdir(), "mtnw_archive.json")


def _load():
    try:
        with open(ARCHIVE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_post(kind, markdown, day=None):
    """kind: 'pre' 또는 'aft'. day: datetime.date (기본 오늘)"""
    day = day or datetime.datetime.now(KST).date()
    data = _load()
    data.setdefault(day.isoformat(), {})[kind] = markdown
    with open(ARCHIVE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


def load_post(kind, day):
    return _load().get(day.isoformat(), {}).get(kind, "")
