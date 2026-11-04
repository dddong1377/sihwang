import datetime
import hmac
import html
import json
import math
import os
import re
import tempfile
import time
from zoneinfo import ZoneInfo

import requests
import streamlit as st
import streamlit.components.v1 as components


st.set_page_config(page_title="MTNW 주간 정리", layout="wide")

KST = ZoneInfo("Asia/Seoul")
WEEKDAYS = "월화수목금토일"
DAY_LABELS = "월화수목금"


# ===============================================================
# 1. 설정
# ===============================================================

GROQ_MODEL = "openai/gpt-oss-120b"
GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_TIMEOUT = 180

# 한 요청(입력 + 최대 출력)의 안전 예산 (TPM 8,000 기준)
GROQ_TPM_SAFE_BUDGET = 7500
GROQ_REASONING_EFFORT = "low"
CHARS_PER_TOKEN_ESTIMATE = 1.65

SYNTH_MAX_TOKENS = 1500
HEADLINE_MAX_TOKENS = 900
HEADLINE_TEMPERATURE = 0.8

# 장전 시황 페이지와 같은 파일을 써서 하루 사용량을 함께 집계합니다.
GROQ_DAILY_TOKEN_LIMIT = 200_000
USAGE_FILE = os.path.join(tempfile.gettempdir(), "mtnw_groq_usage.json")

# 다음 주 일정: 하루에 표시할 최대 행 수
SCHEDULE_PER_DAY = 6

TOSS_CALENDAR_BASE = "https://wts-cert-api.tossinvest.com"

# 법적·윤리적 위험이 있는 표현: 포함된 제목은 코드가 버립니다.
BANNED_HEADLINE_WORDS = (
    "무조건", "반드시", "100%", "수익 보장", "수익보장",
    "원금 보장", "확정 수익", "지금 사라", "당장 사라",
    "지금 팔라", "당장 팔라", "사야", "팔아야", "담아야",
    "대박", "폭망", "패닉", "떡상 확정", "급등 확정",
    "100배", "인생역전", "폭등", "대폭락",
)

DEFAULT_REASON_MARKERS = (
    "시가총액 상위 100위",
    "토스증권 당일 등락률 순위 기준",
)


# ===============================================================
# 2. 공통: Secrets / 로그인 / 토큰 사용량
# ===============================================================

def secret(key, default=""):
    try:
        value = st.secrets.get(key)
    except Exception:
        value = None

    return str(value) if value else os.getenv(key, default)


def login():
    if st.session_state.get("pw_ok"):
        return True

    expected = secret("app_password")

    if not expected:
        st.error("Streamlit Secrets에 app_password를 설정하세요.")
        return False

    def verify():
        entered = st.session_state.get("pw", "").encode()

        st.session_state["pw_ok"] = hmac.compare_digest(
            entered,
            expected.encode(),
        )

        st.session_state["pw"] = ""

    st.title("MTNW 시황")

    st.text_input(
        "비밀번호",
        type="password",
        key="pw",
        on_change=verify,
    )

    if st.session_state.get("pw_ok") is False:
        st.error("비밀번호가 올바르지 않습니다.")

    return False


if not login():
    st.stop()


def estimate_tokens(text):
    if not text:
        return 0

    return math.ceil(len(text) / CHARS_PER_TOKEN_ESTIMATE)


def load_usage():
    today = datetime.datetime.now(KST).strftime("%Y-%m-%d")

    try:
        with open(USAGE_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)
    except Exception:
        data = {}

    if not isinstance(data, dict) or data.get("date") != today:
        data = {"date": today, "tokens": 0, "calls": 0}

    return data


def add_usage(tokens):
    try:
        tokens = int(tokens)
    except (TypeError, ValueError):
        return

    data = load_usage()

    data["tokens"] = int(data.get("tokens", 0)) + tokens
    data["calls"] = int(data.get("calls", 0)) + 1

    try:
        with open(USAGE_FILE, "w", encoding="utf-8") as file:
            json.dump(data, file)
    except Exception:
        pass


def show_token_status(box):
    try:
        usage = load_usage()
        used = int(usage.get("tokens", 0))
        remaining = max(0, GROQ_DAILY_TOKEN_LIMIT - used)

        with box.container():
            with st.container(border=True):
                col1, col2, col3 = st.columns(3)

                col1.metric("오늘 남은 토큰 (추정)", f"{remaining:,}")
                col2.metric(
                    "오늘 사용 토큰",
                    f"{used:,} / {GROQ_DAILY_TOKEN_LIMIT:,}",
                )
                col3.metric("오늘 API 호출", f"{int(usage.get('calls', 0))}회")

                st.progress(min(1.0, used / GROQ_DAILY_TOKEN_LIMIT))

                st.caption(
                    "장전 시황 페이지와 함께 집계한 추정치입니다. "
                    "정확한 잔량은 Groq 콘솔의 limits 페이지에서 확인하세요."
                )
    except Exception:
        pass


# ===============================================================
# 3. Groq 호출
# ===============================================================

def parse_reset_seconds(value):
    if not value:
        return None

    value = str(value).strip().lower().strip(". ")

    try:
        if value.endswith("ms"):
            return float(value[:-2]) / 1000

        minutes = re.search(r"(\d+(?:\.\d+)?)m(?!s)", value)
        seconds = re.search(r"(\d+(?:\.\d+)?)s", value)

        if minutes or seconds:
            total = 0

            if minutes:
                total += float(minutes.group(1)) * 60

            if seconds:
                total += float(seconds.group(1))

            return total

        return float(value)

    except (TypeError, ValueError):
        return None


def sleep_with_countdown(seconds, label):
    seconds = int(max(0, math.ceil(seconds)))

    if seconds <= 0:
        return

    box = st.empty()

    for remaining in range(seconds, 0, -1):
        box.caption(f"⏳ {label} · {remaining}초 대기")
        time.sleep(1)

    box.empty()


def wait_for_tpm(next_tokens):
    """직전 호출의 남은 분당 토큰이 부족하면 초기화될 때까지 기다립니다."""

    last = st.session_state.get("wk_last_rate")

    if not last:
        return

    try:
        remaining = int(last.get("remaining"))
    except (TypeError, ValueError):
        remaining = None

    if remaining is not None and remaining >= next_tokens:
        return

    reset = parse_reset_seconds(last.get("reset"))

    if reset is None:
        reset = 60

    elapsed = time.time() - last.get("at", time.time())

    if elapsed >= reset:
        return

    sleep_with_countdown(
        min(max(2, math.ceil(reset - elapsed) + 3), 90),
        "Groq 토큰 제한 초기화 대기",
    )


def groq_api_key():
    key = ""

    try:
        key = st.secrets.get("GROQ_API_KEY", "")
    except Exception:
        pass

    if not key:
        key = os.environ.get("GROQ_API_KEY", "")

    return str(key).strip()


def groq_chat(prompt, max_tokens, temperature, stage):
    api_key = groq_api_key()

    if not api_key:
        raise RuntimeError(
            "Groq API 키가 설정되지 않았습니다. "
            "Streamlit Secrets에 GROQ_API_KEY를 등록해 주세요."
        )

    input_estimate = estimate_tokens(prompt)
    safe_max = min(max_tokens, GROQ_TPM_SAFE_BUDGET - input_estimate)

    if safe_max < 500:
        raise RuntimeError(
            f"{stage}: 입력이 너무 깁니다(추정 {input_estimate:,}토큰)."
        )

    wait_for_tpm(input_estimate + safe_max)

    payload = {
        "model": GROQ_MODEL,
        "messages": [
            {
                "role": "system",
                "content": (
                    "당신은 한국 주식시장 전문 시황 에디터입니다. "
                    "제공된 자료에 없는 사실이나 수치를 만들어내지 마세요."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": temperature,
        "max_tokens": safe_max,
    }

    if GROQ_REASONING_EFFORT:
        payload["reasoning_effort"] = GROQ_REASONING_EFFORT
        payload["include_reasoning"] = False

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    response = None

    for attempt in range(3):
        try:
            response = requests.post(
                GROQ_API_URL,
                headers=headers,
                json=payload,
                timeout=GROQ_TIMEOUT,
            )
        except requests.exceptions.RequestException as error:
            raise RuntimeError(
                f"{stage}: Groq 네트워크 요청 실패: {str(error)[:200]}"
            )

        st.session_state["wk_last_rate"] = {
            "remaining": response.headers.get("x-ratelimit-remaining-tokens"),
            "reset": response.headers.get("x-ratelimit-reset-tokens"),
            "at": time.time(),
        }

        if response.status_code == 200:
            break

        body = response.text[:1500]
        lowered = body.lower()

        # 추론 옵션을 지원하지 않으면 빼고 재시도
        if (
            response.status_code == 400
            and "reasoning_effort" in payload
            and "reasoning" in lowered
        ):
            payload.pop("reasoning_effort", None)
            payload.pop("include_reasoning", None)
            continue

        # 분당 토큰 제한: 안내된 시간만큼 기다린 뒤 재시도
        if (
            response.status_code == 429
            and attempt < 2
            and ("tokens per minute" in lowered or "tpm" in lowered)
        ):
            wait = None

            retry_after = response.headers.get("retry-after")

            if retry_after:
                try:
                    wait = float(retry_after)
                except ValueError:
                    wait = None

            if wait is None:
                found = re.search(r"try again in\s+([0-9hms.]+)", body, re.I)

                if found:
                    wait = parse_reset_seconds(found.group(1))

            sleep_with_countdown(
                min((wait if wait is not None else 20) + 2, 90),
                f"{stage} 토큰 제한 대기",
            )
            continue

        hint = (
            " (일일 사용 한도일 수 있습니다)"
            if response.status_code == 429
            and ("per day" in lowered or "daily" in lowered)
            else ""
        )

        raise RuntimeError(
            f"{stage}: Groq HTTP {response.status_code}{hint}: {body[:300]}"
        )

    if response is None or response.status_code != 200:
        raise RuntimeError(f"{stage}: Groq 응답을 받지 못했습니다.")

    data = response.json()
    usage = data.get("usage") or {}

    # 잘린 응답이라도 처리된 토큰은 하루 한도에서 차감되므로 기록
    add_usage(usage.get("total_tokens"))

    choices = data.get("choices") or []

    if not choices:
        raise RuntimeError(f"{stage}: Groq 응답에 choices가 없습니다.")

    choice = choices[0]

    if choice.get("finish_reason") == "length":
        raise RuntimeError(
            f"{stage}: 출력 한도에 도달해 응답이 잘렸습니다. 다시 시도해 주세요."
        )

    result = (choice.get("message") or {}).get("content")

    if isinstance(result, list):
        result = "\n".join(
            str(item.get("text", ""))
            for item in result
            if isinstance(item, dict)
        )

    result = str(result or "").strip()

    if not result:
        raise RuntimeError(f"{stage}: Groq 응답이 비어 있습니다.")

    return result


# ===============================================================
# 4. 시황글 파싱 (붙여넣은 마크다운에서 필요한 부분만 뽑습니다)
# ===============================================================

HEADLINE_LINE = re.compile(
    r"📌\s*\*\*[^\n*]*한 줄 요약[^\n*]*\*\*\s*[:：]?\s*(.+)"
)


def clean_cell(text):
    return re.sub(r"[*`]", "", str(text or "")).strip()


def cut(text, limit):
    text = str(text or "").strip()

    if len(text) <= limit:
        return text

    return text[:max(0, limit - 1)].rstrip() + "…"


def table_cell(text):
    return str(text or "").replace("|", "/").replace("\n", " ").strip()


def arrow(value):
    if value is not None and value > 0:
        return "🔺"

    if value is not None and value < 0:
        return "🔻"

    return "➖"


def headline_of(text):
    found = HEADLINE_LINE.search(text or "")

    return clean_cell(found.group(1)) if found else ""


def find_section(text, keywords):
    """제목 줄(#)에 keywords 중 하나가 들어 있는 구간을 다음 같은 단계 제목 전까지 돌려줍니다."""

    lines = str(text or "").splitlines()

    start = None
    level = 0

    for index, line in enumerate(lines):
        stripped = line.strip()

        if stripped.startswith("#") and any(k in stripped for k in keywords):
            start = index
            level = len(stripped) - len(stripped.lstrip("#"))
            break

    if start is None:
        return ""

    body = []

    for line in lines[start + 1:]:
        stripped = line.strip()

        if stripped.startswith("#"):
            current = len(stripped) - len(stripped.lstrip("#"))

            if current <= level:
                break

        body.append(line)

    return "\n".join(body).strip()


def split_row(line):
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def is_separator(cells):
    return bool(cells) and all(
        re.fullmatch(r":?-+:?", cell.replace(" ", ""))
        for cell in cells
        if cell
    ) and any(cells)


def first_table(section):
    """구간 안의 첫 번째 표(머리글 포함)를 행 목록으로 돌려줍니다."""

    rows = []
    started = False

    for line in str(section or "").splitlines():
        stripped = line.strip()

        if stripped.startswith("|"):
            started = True
            cells = split_row(stripped)

            if is_separator(cells):
                continue

            rows.append(cells)

        elif started:
            break

    return rows


def parse_pct(cell):
    found = re.search(r"([+-]?\d+(?:\.\d+)?)\s*%", clean_cell(cell))

    return float(found.group(1)) if found else None


def column_index(header, *keys):
    for index, name in enumerate(header):
        if any(key in name for key in keys):
            return index

    return None


def parse_stock_table(section):
    rows = first_table(section)

    if len(rows) < 2:
        return []

    header = rows[0]

    i_name = column_index(header, "종목")
    i_pct = column_index(header, "등락률")
    i_reason = column_index(header, "이유")
    i_theme = column_index(header, "테마")

    if i_name is None or i_pct is None:
        return []

    result = []

    for row in rows[1:]:
        if len(row) <= max(i_name, i_pct):
            continue

        name = clean_cell(row[i_name])
        pct = parse_pct(row[i_pct])

        if not name or pct is None or "데이터 없음" in name:
            continue

        reason = (
            clean_cell(row[i_reason])
            if i_reason is not None and len(row) > i_reason
            else ""
        )

        if any(marker in reason for marker in DEFAULT_REASON_MARKERS):
            reason = ""

        theme = (
            clean_cell(row[i_theme])
            if i_theme is not None and len(row) > i_theme
            else ""
        )

        result.append({
            "name": name,
            "pct": pct,
            "reason": reason,
            "theme": theme,
        })

    return result


def parse_watch_table(section):
    """장전 시황글의 '주목 종목' 표에서 (종목, 한 줄 이유)를 뽑습니다."""

    rows = first_table(section)

    if len(rows) < 2:
        return []

    header = rows[0]

    i_name = column_index(header, "종목")
    i_reason = column_index(header, "이유")

    if i_name is None:
        return []

    result = []

    for row in rows[1:]:
        if len(row) <= i_name:
            continue

        name = clean_cell(row[i_name])

        if not name or "확인 불가" in name:
            continue

        reason = (
            clean_cell(row[i_reason])
            if i_reason is not None and len(row) > i_reason
            else ""
        )

        result.append({"name": name, "reason": reason})

    return result


def parse_index_pcts(aft_text):
    """오후 시황글의 '장중 지수 현황' 표에서 코스피·코스닥 등락률을 뽑습니다."""

    section = find_section(aft_text, ["장중 지수 현황"])
    rows = first_table(section)

    result = {}

    for row in rows[1:]:
        if len(row) < 3:
            continue

        label = clean_cell(row[0])
        pct = parse_pct(row[-1])

        if pct is None:
            continue

        if "코스피" in label:
            result["코스피"] = pct

        elif "코스닥" in label:
            result["코스닥"] = pct

    return result


def related_stock_names(text):
    """'관련 종목: A, B' 줄에서 종목명을 뽑습니다."""

    names = []

    for found in re.finditer(r"관련 종목\s*[:：]\s*(.+)", str(text or "")):
        for token in re.split(r"[,，/·、]|\s및\s", found.group(1)):
            name = clean_cell(token)

            if (
                len(name) >= 2
                and "확인 불가" not in name
                and name not in ("없음", "-")
            ):
                names.append(name)

    return names


def name_key(name):
    return re.sub(r"\s+", "", name).upper()


def week_composite(pcts):
    values = [value for value in pcts if value is not None]

    if not values:
        return None

    total = 1.0

    for value in values:
        total *= 1 + value / 100

    return (total - 1) * 100


def collect_week(days):
    """요일별 시황글에서 급등·급락·주목 종목, 지수 등락률을 모읍니다."""

    surge = {}
    plunge = {}
    watch = {}
    index_pcts = {"코스피": {}, "코스닥": {}}

    for day in days:
        i = day["i"]
        pre = day["pre"]
        aft = day["aft"]

        if aft:
            for label, pct in parse_index_pcts(aft).items():
                index_pcts[label][i] = pct

            for row in parse_stock_table(find_section(aft, ["급등 특징주"])):
                if row["pct"] <= 0:
                    continue

                key = name_key(row["name"])
                item = surge.setdefault(key, {
                    "name": row["name"], "best": None, "days": set(),
                    "theme": "", "reason": "",
                })

                item["days"].add(i)

                if item["best"] is None or row["pct"] > item["best"][0]:
                    item["best"] = (row["pct"], i)
                    item["theme"] = row["theme"]
                    item["reason"] = row["reason"]

            for row in parse_stock_table(find_section(aft, ["급락 특징주"])):
                if row["pct"] >= 0:
                    continue

                key = name_key(row["name"])
                item = plunge.setdefault(key, {
                    "name": row["name"], "best": None, "days": set(),
                    "theme": "", "reason": "",
                })

                item["days"].add(i)

                if item["best"] is None or row["pct"] < item["best"][0]:
                    item["best"] = (row["pct"], i)
                    item["theme"] = row["theme"]
                    item["reason"] = row["reason"]

        # 주목 종목: 장전 '주목 종목' 표 + 장전·오후 이슈의 '관련 종목'
        mentioned = []

        if pre:
            section = find_section(pre, ["주목 종목", "언급 TOP10", "종목·테마"])

            for row in parse_watch_table(section):
                mentioned.append((row["name"], row["reason"]))

            for name in related_stock_names(pre):
                mentioned.append((name, ""))

        if aft:
            for name in related_stock_names(aft):
                mentioned.append((name, ""))

        for name, reason in mentioned:
            key = name_key(name)
            item = watch.setdefault(key, {
                "name": name, "days": set(), "total": 0, "reason": "",
            })

            item["days"].add(i)
            item["total"] += 1

            if reason:
                item["reason"] = reason

    surge_top = sorted(
        surge.values(),
        key=lambda item: (-item["best"][0], -len(item["days"])),
    )[:3]

    plunge_top = sorted(
        plunge.values(),
        key=lambda item: (item["best"][0], -len(item["days"])),
    )[:3]

    watch_top = sorted(
        watch.values(),
        key=lambda item: (-len(item["days"]), -item["total"], min(item["days"])),
    )[:3]

    return {
        "surge": surge_top,
        "plunge": plunge_top,
        "watch": watch_top,
        "index": index_pcts,
    }


# ===============================================================
# 5. AI 프롬프트 (핵심 이슈 정리 / 주간 헤드라인)
# ===============================================================

SYNTH_PROMPT = """# 역할
너는 증권사 리서치센터 시황 담당이자 경제지 증권부 데스크다.
이번 주(월~금) 장전·오후 시황글에서 뽑은 자료를 읽고 '주간 핵심 정리'의 재료를 만든다.

# 원칙
1. 입력 자료에 있는 사실·수치·종목명만 쓴다. 새로운 사실을 만들지 않는다.
2. 같은 주제를 여러 날 다룬 경우 하나의 이슈로 합치고, 한 주 동안 가장 비중 있게 반복된 이슈 3개를 고른다. 3개가 안 되면 있는 만큼만 쓴다.
3. 요일 순서를 근거로 한 주의 흐름(어떻게 시작해 어떻게 끝났는지)을 쓸 수 있지만, 입력에 없는 원인을 단정하지 않는다.
4. 매수·매도 권유, 수익 보장, 급등 예고 같은 표현은 쓰지 않는다.
5. 확인할 수 없는 내용은 쓰지 않는다.

# 입력 자료
__MATERIAL__

# 출력 형식 (아래 형식 그대로만 출력하고, 다른 설명은 쓰지 않는다)
이슈1: 제목(25자 이내) || 내용 2문장(120자 이내, 사실만) || 관련 종목(입력에 나온 것만, 쉼표 구분, 없으면 -) || 방향(상승/하락/중립)
이슈2: 제목 || 내용 || 관련 종목 || 방향
이슈3: 제목 || 내용 || 관련 종목 || 방향
흐름: 이번 주 시장 흐름 2문장(120자 이내)
체크: 입력의 '체크'·리스크에서만 뽑은 다음 주 관전 포인트 || 포인트 || 포인트 (최대 3개, 근거가 없으면 -)
"""


WEEKLY_HEADLINE_PROMPT = """# 역할
너는 여의도 증권가와 경제지 헤드라인을 쥐락펴락하는 최고의 스타 기자다.
증권가 리포트는 제목이 전부다. 네가 쓴 제목은 독자가 '이 한 주'를 안 보고는 못 배기게 만든다.
단, 법적으로 문제 될 표현은 절대 쓰지 않는다.

# 임무
아래 '이번 주 팩트'를 보고 주간 핵심 정리 리포트 맨 위에 올릴 한 줄 제목 3개를 쓴다.

# 제목 기법 (3개가 서로 다른 각도가 되게 쓴다)
- 시작과 끝의 대비: 월요일의 분위기와 금요일의 분위기가 어떻게 달라졌는지 한 줄에 담는다
- 승자와 패자: 한 주를 끌고 간 쪽과 발목을 잡은 쪽의 엇갈림
- 숫자 한 방: 팩트에 있는 숫자 하나만 콕 집어 쓴다
- 질문·여운: 다음 주가 궁금해지게 여지를 남긴다 (예측·단정은 금지)
- 증권사 리포트 제목처럼 짧고 단단하게 쓴다. 사실과 맞을 때만 이런 틀을 응용한다: "○○는 웃고 △△는 울었다", "한 주를 가른 ○○", "○○가 쥔 한 주…다음 주는?"
- "~ 마감", "~ 혼조", "주간 시황 정리" 같은 뻔한 요약은 쓰지 않는다

# 법적·윤리 안전 규칙 (반드시 지킨다)
1. 팩트에 있는 사실·수치·종목명만 쓴다. 새로 지어내지 않는다.
2. 다음 주 시장을 단정해 예측하지 않는다. "무조건", "반드시", "확정" 같은 표현을 쓰지 않는다.
3. 특정 종목의 매수·매도·보유를 권유하지 않는다. 수익 보장, "지금 사라/팔아라" 류는 금지한다.
4. 공포·패닉을 부추기지 않는다. "폭망", "붕괴 임박" 같은 선동 표현은 쓰지 않고, 확인된 하락은 사실 그대로 쓴다.
5. 특정 기업·인물을 비방하거나 근거 없는 의혹을 제기하지 않는다.
6. 주간 등락률은 일간 값을 합성한 근사치이므로 정확한 주간 수익률처럼 쓰지 않는다. 숫자를 쓸 때는 일간 수치를 쓰거나 '안팎'처럼 완화해서 쓴다.
7. 낚시가 되면 안 된다. 제목이 던진 궁금증은 본문에서 풀려야 한다.

# 이번 주 팩트
__FACTS__

# 출력 형식
- 제목 3개, 한 줄에 하나씩
- 각 18~40자 내외
- 번호·따옴표·설명·머리말 없이 제목 문장만 출력
- 이모지는 많아야 1개, 없어도 된다
"""


def day_material(day, scale):
    """요일 하나의 시황글에서 AI에 넘길 핵심 구간만 추립니다."""

    def squeeze(text, limit):
        text = re.sub(r"\n{2,}", "\n", str(text or "")).strip()

        return text[:max(40, int(limit * scale))]

    date = day["date"]
    lines = [f"[{DAY_LABELS[day['i']]} {date.month}/{date.day}]"]

    pre = day["pre"]
    aft = day["aft"]

    head = headline_of(pre)

    if head:
        lines.append("장전 요약: " + squeeze(head, 90))

    core = find_section(pre, ["핵심 포인트", "핵심 이슈"])

    if core:
        lines.append("장전 핵심:\n" + squeeze(core, 520))

    head = headline_of(aft)

    if head:
        lines.append("오후 요약: " + squeeze(head, 90))

    issues = find_section(aft, ["주요 경제 이슈"])

    if issues:
        lines.append("오후 이슈:\n" + squeeze(issues, 640))

    themes = find_section(aft, ["뜨는 테마"])

    if themes:
        lines.append("오후 테마: " + squeeze(" ".join(themes.split()), 200))

    # 제목 줄을 찾지 못한 글은 원문 앞부분을 그대로 넘김
    if len(lines) == 1:
        if pre:
            lines.append("장전 원문:\n" + squeeze(pre, 600))

        if aft:
            lines.append("오후 원문:\n" + squeeze(aft, 700))

    return "\n".join(lines)


def build_synth_prompt(days):
    active = [day for day in days if day["pre"] or day["aft"]]

    scale = 1.0

    while scale >= 0.3:
        material = "\n\n".join(day_material(day, scale) for day in active)
        prompt = SYNTH_PROMPT.replace("__MATERIAL__", material)

        if estimate_tokens(prompt) + SYNTH_MAX_TOKENS <= GROQ_TPM_SAFE_BUDGET:
            return prompt

        scale *= 0.85

    raise RuntimeError(
        "붙여넣은 글이 너무 길어 한 번에 정리할 수 없습니다. "
        "일부 요일의 글을 줄여서 다시 시도해 주세요."
    )


def parse_synthesis(text):
    issues = []
    flow = ""
    checks = []

    for raw_line in str(text or "").splitlines():
        line = raw_line.strip().lstrip("-*• ").replace("**", "")

        found = re.match(r"^이슈\s*\d*\s*[:：]\s*(.+)$", line)

        if found:
            parts = [part.strip() for part in found.group(1).split("||")]

            while len(parts) < 4:
                parts.append("")

            issues.append({
                "title": parts[0],
                "content": parts[1],
                "stocks": parts[2],
                "direction": parts[3],
            })
            continue

        found = re.match(r"^흐름\s*[:：]\s*(.+)$", line)

        if found:
            flow = found.group(1).strip()
            continue

        found = re.match(r"^체크\s*[:：]\s*(.+)$", line)

        if found:
            checks = [
                part.strip()
                for part in found.group(1).split("||")
                if part.strip() and part.strip() != "-"
            ]

    issues = [issue for issue in issues if issue["title"]]

    return issues[:3], flow, checks[:3]


def parse_headlines(text):
    """AI 응답에서 제목을 뽑고, 금칙어가 들어간 줄은 제외합니다."""

    headlines = []

    for line in str(text or "").splitlines():
        line = line.strip()
        line = re.sub(r"^(?:[-*•]|\d+[.)])\s*", "", line)
        line = line.strip(" \t\"'“”‘’「」*")

        if len(line) < 5:
            continue

        if any(word in line for word in BANNED_HEADLINE_WORDS):
            continue

        headlines.append(line)

    return headlines[:3]


def pct_text(value):
    return "-" if value is None else f"{value:+.2f}%"


def build_facts(days, week, issues, flow):
    """제목을 쓸 때 AI에 보여 줄 '이번 주 팩트'(코드가 만든 수치 + 정리된 이슈)."""

    lines = []

    for label in ("코스피", "코스닥"):
        daily = week["index"][label]

        if not daily:
            continue

        parts = [
            f"{DAY_LABELS[i]} {pct_text(daily[i])}"
            for i in sorted(daily)
        ]

        composite = week_composite(list(daily.values()))

        lines.append(
            f"{label} 일간 등락률(오후 시황 작성 시점): " + ", ".join(parts)
            + f" / 합성 근사 {pct_text(composite)}"
        )

    if week["surge"]:
        top = week["surge"][0]
        pct, index = top["best"]
        lines.append(
            f"주간 최대 일간 상승 종목: {top['name']} {pct:+.2f}% ({DAY_LABELS[index]}요일)"
        )

    if week["plunge"]:
        top = week["plunge"][0]
        pct, index = top["best"]
        lines.append(
            f"주간 최대 일간 하락 종목: {top['name']} {pct:+.2f}% ({DAY_LABELS[index]}요일)"
        )

    for number, issue in enumerate(issues, start=1):
        lines.append(
            f"핵심 이슈{number}: {issue['title']} - {issue['content']}"
        )

    if flow:
        lines.append(f"주간 흐름: {flow}")

    if not lines:
        lines.append("정리된 팩트가 없습니다. 제목을 쓰지 말고 '확인 불가'라고만 출력하세요.")

    return "\n".join(lines)


# ===============================================================
# 6. 다음 주 일정 (토스 증시 캘린더)
# ===============================================================

def calendar_session_headers():
    cookie = (
        secret("TOSS_CALENDAR_COOKIE").strip()
        or secret("TOSS_NEWS_COOKIE").strip()
        or secret("TOSS_INDICATOR_COOKIE").strip()
    )
    xsrf_token = (
        secret("TOSS_CALENDAR_XSRF_TOKEN").strip()
        or secret("TOSS_NEWS_XSRF_TOKEN").strip()
        or secret("TOSS_INDICATOR_XSRF_TOKEN").strip()
    )
    app_version = (
        secret("TOSS_CALENDAR_APP_VERSION").strip()
        or secret("TOSS_NEWS_APP_VERSION").strip()
        or secret("TOSS_INDICATOR_APP_VERSION").strip()
    )
    browser_tab_id = (
        secret("TOSS_CALENDAR_BROWSER_TAB_ID").strip()
        or secret("TOSS_NEWS_BROWSER_TAB_ID").strip()
        or secret("TOSS_INDICATOR_BROWSER_TAB_ID").strip()
    )

    missing = []

    if not cookie:
        missing.append("TOSS_CALENDAR_COOKIE(또는 TOSS_NEWS_COOKIE)")

    if not xsrf_token:
        missing.append("TOSS_CALENDAR_XSRF_TOKEN(또는 TOSS_NEWS_XSRF_TOKEN)")

    if not app_version:
        missing.append("TOSS_CALENDAR_APP_VERSION(또는 TOSS_NEWS_APP_VERSION)")

    if missing:
        raise RuntimeError(
            "토스 캘린더 세션 정보가 Secrets에 없습니다: " + ", ".join(missing)
        )

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
        "Origin": "https://www.tossinvest.com",
        "Referer": "https://www.tossinvest.com/calendar",
        "app-version": app_version,
        "x-xsrf-token": xsrf_token,
        "Cookie": cookie,
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-site",
    }

    if browser_tab_id:
        headers["browser-tab-id"] = browser_tab_id

    return headers


def fetch_calendar_month(year_month):
    try:
        response = requests.post(
            f"{TOSS_CALENDAR_BASE}/api/v4/calendar/monthly/{year_month}",
            data=b"",
            headers=calendar_session_headers(),
            timeout=(5, 15),
        )
    except requests.RequestException as error:
        raise RuntimeError(f"토스 캘린더 연결 실패: {error}") from error

    if response.status_code in (401, 403, 429):
        raise RuntimeError(
            f"토스 캘린더 접근 제한(HTTP {response.status_code}). "
            "브라우저 세션이 만료됐을 수 있으니 Cookie/XSRF 값을 갱신해 주세요."
        )

    if not response.ok:
        raise RuntimeError(
            f"토스 캘린더 조회 실패(HTTP {response.status_code}): "
            f"{response.text[:200]}"
        )

    try:
        payload = response.json()
    except ValueError as error:
        raise RuntimeError("토스 캘린더 응답이 JSON 형식이 아닙니다.") from error

    events = (payload.get("result") or {}).get("events")

    if not isinstance(events, list):
        raise RuntimeError("토스 캘린더 응답에 result.events 배열이 없습니다.")

    return events


def cal_country(value):
    value = str(value or "").lower()

    if value in ("kr", "korea", "kor"):
        return "국내"

    if value in ("us", "usa", "united_states"):
        return "미국"

    return value.upper() if value else "국가 미표기"


def cal_kst_hhmm(value):
    value = str(value or "").strip()

    if not value:
        return ""

    try:
        dt = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))

        if dt.tzinfo:
            dt = dt.astimezone(KST)

        return dt.strftime("%H:%M")

    except ValueError:
        return ""


def calendar_event_row(event):
    """캘린더 원본 이벤트 → 표시용 한 행. 캘린더에 있는 값만 사용합니다."""

    group = str((event.get("id") or {}).get("group") or "")
    view = event.get("view") or {}
    title = str(view.get("title") or "일정명 확인 불가").strip()

    row = {
        "date": datetime.date.fromisoformat(event["date"]),
        "rank": 2,
        "kind": "기타",
        "title": title,
        "time": "",
        "note": "",
    }

    if group == "HOLIDAY":
        row["rank"] = -1
        row["kind"] = "휴장·공휴일"

    elif group == "ECONOMIC":
        indicator = view.get("economicIndicatorValue") or {}

        row["rank"] = 0
        row["kind"] = f"경제지표·{cal_country(indicator.get('countryType'))}"

        raw_time = str(indicator.get("time") or "").strip()

        # 00:00은 실제 시각이 아닌 기본값일 수 있어 표시하지 않음
        if raw_time and raw_time not in ("00:00:00", "00:00"):
            row["time"] = raw_time[:5]

        unit = str(indicator.get("unit") or "").strip()
        prefix = str(indicator.get("unitPrefix") or "").strip()
        notes = []

        if indicator.get("forecast") is not None:
            notes.append(f"예상 {prefix}{indicator['forecast']}{unit}")

        if indicator.get("historical") is not None:
            notes.append(f"이전 {prefix}{indicator['historical']}{unit}")

        row["note"] = " · ".join(notes)

    elif group in ("USD_EARNINGS_ANNOUNCEMENT", "KRX_EARNINGS_ANNOUNCEMENT"):
        earnings = event.get("stockEarnings") or {}
        company = str(
            earnings.get("companyName") or title.replace(" 실적발표", "")
        ).strip()

        row["rank"] = 1
        row["kind"] = f"기업 실적·{cal_country(earnings.get('countryType'))}"
        row["title"] = f"{company} 실적 발표"
        row["time"] = cal_kst_hhmm(earnings.get("announceDateTime"))
        row["note"] = str(earnings.get("announceMarketStatusText") or "").strip()

    return row


def day_text(day):
    return f"{day.month}/{day.day}({WEEKDAYS[day.weekday()]})"


def fetch_schedule_markdown(next_monday):
    """다음 주 월~금 일정을 표로 만듭니다. (표, 전체 건수)"""

    start = next_monday
    end = next_monday + datetime.timedelta(days=4)

    months = sorted({start.strftime("%Y-%m"), end.strftime("%Y-%m")})

    events = []

    for month in months:
        events.extend(fetch_calendar_month(month))

    rows = []

    for event in events:
        if not isinstance(event, dict):
            continue

        date_text = str(event.get("date") or "")

        if not (start.isoformat() <= date_text <= end.isoformat()):
            continue

        if event.get("excludeFromAll", False):
            continue

        group = str((event.get("id") or {}).get("group") or "")

        if group == "ETC":
            continue

        if (event.get("view") or {}).get("title") == "소식이 없어요":
            continue

        rows.append(calendar_event_row(event))

    if not rows:
        return "토스 증시 캘린더에 확인된 일정이 없습니다.", 0

    rows.sort(
        key=lambda row: (
            row["date"], row["rank"], row["time"] or "99:99", row["title"]
        )
    )

    by_day = {}

    for row in rows:
        by_day.setdefault(row["date"], []).append(row)

    lines = [
        "| 날짜 | 구분 | 일정 | 시각 | 비고 |",
        "|---|---|---|---|---|",
    ]

    for day in sorted(by_day):
        items = by_day[day]

        for row in items[:SCHEDULE_PER_DAY]:
            lines.append(
                f"| {day_text(day)} | {table_cell(row['kind'])} "
                f"| {table_cell(row['title'])} | {row['time'] or '-'} "
                f"| {table_cell(row['note']) or '-'} |"
            )

        if len(items) > SCHEDULE_PER_DAY:
            lines.append(
                f"| {day_text(day)} | 기타 | 외 {len(items) - SCHEDULE_PER_DAY}건 "
                "| - | 토스 증시 캘린더에서 확인 |"
            )

    return "\n".join(lines), len(rows)


def load_schedule(next_monday):
    """버튼 콜백: 다음 주 일정을 일정 칸에 채웁니다."""

    state = st.session_state

    try:
        markdown, count = fetch_schedule_markdown(next_monday)

        end = next_monday + datetime.timedelta(days=4)

        state["wk_schedule"] = markdown
        state["wk_schedule_note"] = (
            f"토스 증시 캘린더 조회 완료 · {day_text(next_monday)}~{day_text(end)} "
            f"· 일정 {count}건 · {datetime.datetime.now(KST).strftime('%H:%M:%S')}"
        )

    except Exception as error:
        state["wk_schedule_note"] = (
            f"일정 불러오기 실패: {str(error)[:300]} · 직접 입력해 주세요."
        )


# ===============================================================
# 7. 결과 조립
# ===============================================================

def index_table_md(week, days):
    if not any(week["index"][label] for label in ("코스피", "코스닥")):
        return ""

    header = ["지수"] + [
        f"{DAY_LABELS[day['i']]} {day['date'].month}/{day['date'].day}"
        for day in days
    ] + ["주간(합성 근사)"]

    lines = [
        "| " + " | ".join(header) + " |",
        "|" + "|".join(["---"] + ["---:"] * (len(header) - 1)) + "|",
    ]

    for label in ("코스피", "코스닥"):
        daily = week["index"][label]

        if not daily:
            continue

        cells = [label]

        for day in days:
            value = daily.get(day["i"])
            cells.append("-" if value is None else f"{arrow(value)} {value:+.2f}%")

        composite = week_composite(list(daily.values()))
        cells.append("-" if composite is None else f"{arrow(composite)} {composite:+.2f}%")

        lines.append("| " + " | ".join(cells) + " |")

    return "\n".join(lines)


def movers_table_md(rows):
    if not rows:
        return "- 붙여넣은 오후 시황글에서 읽을 수 있는 종목이 없습니다."

    lines = [
        "| 순위 | 종목 | 최대 등락률 | 일자 | 등장 | 관련 테마 | 비고 |",
        "|---:|---|---:|---|---:|---|---|",
    ]

    for rank, item in enumerate(rows, start=1):
        pct, index = item["best"]

        lines.append(
            f"| {rank} | **{table_cell(item['name'])}** | **{pct:+.2f}%** "
            f"| {DAY_LABELS[index]} | {len(item['days'])}일 "
            f"| {table_cell(item['theme']) or '-'} "
            f"| {table_cell(cut(item['reason'], 40)) or '-'} |"
        )

    return "\n".join(lines)


def watch_table_md(rows):
    if not rows:
        return "- 붙여넣은 글에서 읽을 수 있는 주목 종목이 없습니다."

    lines = [
        "| 순위 | 종목 | 언급 일수 | 언급 요일 | 비고(장전 시황 기준) |",
        "|---:|---|---:|---|---|",
    ]

    for rank, item in enumerate(rows, start=1):
        days = "·".join(DAY_LABELS[i] for i in sorted(item["days"]))

        lines.append(
            f"| {rank} | **{table_cell(item['name'])}** | {len(item['days'])}일 "
            f"| {days} | {table_cell(cut(item['reason'], 40)) or '-'} |"
        )

    return "\n".join(lines)


def build_report(monday, headline, days, week, issues, flow, checks, schedule, ai_error):
    friday = monday + datetime.timedelta(days=4)

    lines = [
        f"# 📅 {monday.year}년 {monday.month}월 {monday.day}일 ~ "
        f"{friday.month}월 {friday.day}일 | 주간 핵심 정리",
        "",
        f"📌 **한 줄 요약**: {headline}",
        "",
    ]

    table = index_table_md(week, days)

    if table:
        lines += ["### 📊 한 주 시장 성적표", "", table, ""]

        lines += [
            "※ 오후 시황 작성 시점의 일간 등락률이며 실제 종가와 다를 수 있습니다. "
            "주간 값은 일간 등락률을 합성한 근사치입니다.",
            "",
        ]

    if flow:
        lines += [f"📝 **주간 흐름**: {flow}", ""]

    lines += ["### 🔥 이번 주 핵심 이슈 TOP 3", ""]

    if issues:
        for number, issue in enumerate(issues, start=1):
            direction = issue["direction"]
            mark = "🔺" if "상승" in direction else "🔻" if "하락" in direction else "➖"

            lines.append(f"**{number}. {mark} {issue['title']}**")

            if issue["content"]:
                lines.append(f"- 내용: {issue['content']}")

            stocks = issue["stocks"]

            if stocks and stocks != "-":
                lines.append(f"- 📌 관련 종목: {stocks}")

            lines.append("")

    else:
        lines += [f"- 이슈 정리를 만들지 못했습니다. {ai_error}".strip(), ""]

    lines += [
        "### 🔺 주간 급등 TOP 3 (일간 상승률 기준)",
        "",
        movers_table_md(week["surge"]),
        "",
        "### 🔻 주간 급락 TOP 3 (일간 하락률 기준)",
        "",
        movers_table_md(week["plunge"]),
        "",
        "### 📌 주간 주목 종목 TOP 3 (언급 일수 기준)",
        "",
        watch_table_md(week["watch"]),
        "",
        "### 📆 다음 주 장 체크 일정",
        "",
    ]

    schedule = str(schedule or "").strip()

    lines += [schedule if schedule else "- 일정이 입력되지 않았습니다.", ""]

    if checks:
        lines += ["### 👀 다음 주 관전 포인트 (이번 주 시황글의 체크 기준)", ""]

        for item in checks:
            lines.append(f"- {item}")

        lines.append("")

    lines += [
        "※ 이 글은 이번 주 장전·오후 시황글을 종합한 참고 자료이며 투자 권유가 아닙니다.",
    ]

    return "\n".join(lines)


# ===============================================================
# 8. 실행
# ===============================================================

def request_generate(next_monday):
    state = st.session_state

    if not state.get("wk_schedule", "").strip():
        load_schedule(next_monday)

    state["wk_run"] = True


def run_weekly(monday, status):
    state = st.session_state

    days = []

    for i in range(5):
        days.append({
            "i": i,
            "date": monday + datetime.timedelta(days=i),
            "pre": state.get(f"wk_pre_{i}", "").strip(),
            "aft": state.get(f"wk_aft_{i}", "").strip(),
        })

    if not any(day["pre"] or day["aft"] for day in days):
        raise RuntimeError(
            "붙여넣은 시황글이 없습니다. ① 요일 탭에 장전·오후 시황글을 붙여넣어 주세요."
        )

    status.write("📊 요일별 시황글에서 지수·급등락·주목 종목을 정리하는 중...")

    week = collect_week(days)

    status.write(
        f"✅ 급등 {len(week['surge'])}·급락 {len(week['plunge'])}·"
        f"주목 {len(week['watch'])}종목 정리"
    )

    # 1) 핵심 이슈 정리
    issues, flow, checks, ai_error = [], "", [], ""

    try:
        prompt = build_synth_prompt(days)

        status.write("🧠 이번 주 핵심 이슈를 정리하는 중...")

        raw = groq_chat(prompt, SYNTH_MAX_TOKENS, 0.2, "주간 이슈 정리")

        state["wk_ai_raw"] = raw

        issues, flow, checks = parse_synthesis(raw)

        if not issues:
            ai_error = "AI 응답 형식을 해석하지 못했습니다(아래 'AI 원문' 참고)."

    except Exception as error:
        ai_error = str(error)[:300]
        status.write(f"⚠️ 이슈 정리 실패: {ai_error}")

    # 2) 한 줄 요약(제목)
    headline, alternatives = "", []

    try:
        facts = build_facts(days, week, issues, flow)
        prompt = WEEKLY_HEADLINE_PROMPT.replace("__FACTS__", facts)

        status.write("✍️ 주간 헤드라인을 쓰는 중...")

        raw = groq_chat(
            prompt,
            HEADLINE_MAX_TOKENS,
            HEADLINE_TEMPERATURE,
            "주간 헤드라인",
        )

        titles = parse_headlines(raw)

        if titles:
            headline = titles[0]
            alternatives = titles[1:]

        else:
            status.write("⚠️ 사용 가능한 헤드라인이 없어 기본 문구를 씁니다.")

    except Exception as error:
        status.write(f"⚠️ 헤드라인 작성 실패: {str(error)[:200]}")

    if not headline:
        headline = (
            issues[0]["title"] if issues else "이번 주 시장 핵심 정리"
        )

    state["wk_report"] = build_report(
        monday,
        headline,
        days,
        week,
        issues,
        flow,
        checks,
        state.get("wk_schedule", ""),
        ai_error,
    )

    state["wk_alts"] = alternatives

    state["wk_note"] = (
        f"생성 완료 · {datetime.datetime.now(KST).strftime('%H:%M:%S')}"
        + (f" · 일부 실패: {ai_error}" if ai_error else "")
    )
    state["wk_note_level"] = "warn" if ai_error else "ok"


def run_with_status(monday):
    state = st.session_state

    with st.status("주간 정리 생성 중... (약 1~2분)", expanded=True) as status:
        try:
            run_weekly(monday, status)

            status.update(label="완료", state="complete", expanded=False)

        except Exception as error:
            state["wk_note"] = f"생성 실패: {error}"
            state["wk_note_level"] = "error"

            status.update(label="오류로 중단됨", state="error", expanded=True)


# ===============================================================
# 9. 마크다운 → HTML / 복사 버튼
# ===============================================================

def md_to_html(markdown):
    """마크다운을 간단한 HTML로 변환(표 머리글 회색, 열 너비는 글자 길이에 비례)."""

    import unicodedata

    output = []
    table_rows = []
    para = []

    def inline(text):
        safe = html.escape(text)
        return re.sub(r"\*\*(.*?)\*\*", r"<b>\1</b>", safe)

    def visual_width(text):
        text = text.replace("*", "")
        return sum(
            2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
            for ch in text
        )

    def flush_para():
        if para:
            output.append("<p style='margin:0 0 10px'>" + "<br>".join(para) + "</p>")
            para.clear()

    def flush_table():
        if not table_rows:
            return

        columns = max(len(row) for row in table_rows)

        weights = []

        for index in range(columns):
            longest = max(
                visual_width(row[index]) if index < len(row) else 0
                for row in table_rows
            )
            weights.append(max(longest, 6) + 3)

        total = sum(weights)
        widths = [round(weight * 100 / total) for weight in weights]
        widths[-1] = 100 - sum(widths[:-1])

        output.append(
            "<table border='1' cellpadding='4' cellspacing='0' "
            "style='border-collapse:collapse;margin:0 0 10px;width:100%'>"
        )

        for row_index, row in enumerate(table_rows):
            cells_html = []

            for col_index in range(columns):
                cell = row[col_index] if col_index < len(row) else ""
                safe = inline(cell)

                numeric = bool(
                    re.fullmatch(r"[\s\d,.+\-%원🔺🔻➖▲▼]+", cell.replace("*", ""))
                )

                wrap_style = (
                    "white-space:nowrap" if numeric
                    else "word-break:break-all;overflow-wrap:anywhere"
                )

                if row_index == 0:
                    cells_html.append(
                        f"<th width='{widths[col_index]}%' "
                        f"style='width:{widths[col_index]}%;"
                        f"background-color:#F2F3F5;font-weight:normal;"
                        f"text-align:left;{wrap_style}'>{safe}</th>"
                    )
                else:
                    cells_html.append(f"<td style='{wrap_style}'>{safe}</td>")

            output.append("<tr>" + "".join(cells_html) + "</tr>")

        output.append("</table>")
        table_rows.clear()

    for line in markdown.splitlines():
        if line.startswith("|"):
            flush_para()

            cells = [cell.strip() for cell in line.split("|")[1:-1]]

            if all(re.fullmatch(r":?-+:?", cell) for cell in cells):
                continue

            table_rows.append(cells)
            continue

        flush_table()

        if line.startswith("# "):
            flush_para()
            output.append(
                "<h1 style='margin:0 0 12px'>" + html.escape(line[2:]) + "</h1>"
            )

        elif line.startswith("### "):
            flush_para()
            output.append(
                "<h3 style='margin:18px 0 6px'>" + html.escape(line[4:]) + "</h3>"
            )

        elif line.strip():
            indent = len(line) - len(line.lstrip(" "))
            para.append("&nbsp;" * indent + inline(line.strip()))

        else:
            flush_para()

    flush_para()
    flush_table()

    return "\n".join(output)


def copy_buttons(markdown):
    payload = json.dumps(
        {"md": markdown, "html": md_to_html(markdown)},
        ensure_ascii=False,
    ).replace("</", "<\\/")

    page = f"""
    <button id="rich">복사 (서식 포함)</button>
    <button id="plain">복사 (마크다운)</button>
    <span id="status"></span>

    <script>
    const P = {payload};
    const s = document.getElementById('status');

    document.getElementById('rich').onclick = async () => {{
        try {{
            await navigator.clipboard.write([
                new ClipboardItem({{
                    'text/html': new Blob([P.html], {{type: 'text/html'}}),
                    'text/plain': new Blob([P.md], {{type: 'text/plain'}})
                }})
            ]);
            s.textContent = ' 복사 완료';
        }} catch (e) {{
            s.textContent = ' 복사 실패';
        }}
    }};

    document.getElementById('plain').onclick = async () => {{
        try {{
            await navigator.clipboard.writeText(P.md);
            s.textContent = ' 복사 완료';
        }} catch (e) {{
            s.textContent = ' 복사 실패';
        }}
    }};
    </script>
    """

    components.html(page, height=45)


# ===============================================================
# 10. 화면
# ===============================================================

def main():
    state = st.session_state

    st.title("MTNW 주간 정리")

    token_box = st.empty()
    show_token_status(token_box)

    st.caption(
        "금요일 오후에 이번 주 장전·오후 시황글을 붙여넣으면, 핵심 이슈 TOP3와 "
        "급등·급락·주목 종목 TOP3, 다음 주 일정을 한 편의 주간 정리로 만듭니다. "
        "붙여넣은 글과 결과는 따로 저장하지 않으며, 화면을 새로고침하면 사라집니다."
    )

    # -----------------------------------------------------------
    # ① 시황글 붙여넣기
    # -----------------------------------------------------------

    st.markdown("### 🗓️ ① 이번 주 시황글 붙여넣기")

    with st.container(border=True):
        base = st.date_input(
            "기준 주 (해당 주의 아무 날짜)",
            value=datetime.datetime.now(KST).date(),
            key="wk_base",
        )

        monday = base - datetime.timedelta(days=base.weekday())
        next_monday = monday + datetime.timedelta(days=7)

        days = [monday + datetime.timedelta(days=i) for i in range(5)]

        tabs = st.tabs(
            [f"{DAY_LABELS[i]} {day.month}/{day.day}" for i, day in enumerate(days)]
        )

        for i, tab in enumerate(tabs):
            with tab:
                col_pre, col_aft = st.columns(2)

                col_pre.text_area(
                    "장전 시황글 (한눈에 보이는 시황글)",
                    key=f"wk_pre_{i}",
                    height=240,
                )

                col_aft.text_area(
                    "오후 시황글",
                    key=f"wk_aft_{i}",
                    height=240,
                )

        pre_count = sum(1 for i in range(5) if state.get(f"wk_pre_{i}", "").strip())
        aft_count = sum(1 for i in range(5) if state.get(f"wk_aft_{i}", "").strip())

        st.caption(
            f"입력 현황: 장전 {pre_count}/5 · 오후 {aft_count}/5 "
            "(휴장일은 비워 두면 됩니다. '복사 (마크다운)'으로 복사한 글을 붙여넣으세요.)"
        )

    # -----------------------------------------------------------
    # ② 다음 주 일정
    # -----------------------------------------------------------

    st.markdown("### 📆 ② 다음 주 장 체크 일정")

    with st.container(border=True):
        col_btn, col_note = st.columns([1.8, 5], vertical_alignment="center")

        col_btn.button(
            "🔄 토스 캘린더에서 불러오기",
            on_click=load_schedule,
            args=(next_monday,),
        )

        if state.get("wk_schedule_note"):
            col_note.caption(state["wk_schedule_note"])

        st.text_area(
            f"다음 주({day_text(next_monday)}~) 일정 (자유롭게 수정 가능)",
            key="wk_schedule",
            height=220,
        )

    # -----------------------------------------------------------
    # ③ 생성
    # -----------------------------------------------------------

    st.markdown("### ✨ ③ 주간 핵심 정리 생성")

    with st.container(border=True):
        st.button(
            "✨ 주간 핵심 정리 생성",
            type="primary",
            on_click=request_generate,
            args=(next_monday,),
            use_container_width=True,
            help="일정 칸이 비어 있으면 토스 캘린더에서 먼저 불러옵니다.",
        )

        if state.pop("wk_run", False):
            run_with_status(monday)
            show_token_status(token_box)

        if state.get("wk_note"):
            level = state.get("wk_note_level")

            if level == "error":
                st.error(state["wk_note"])
            elif level == "warn":
                st.warning(state["wk_note"])
            else:
                st.success(state["wk_note"])

    # -----------------------------------------------------------
    # ④ 결과
    # -----------------------------------------------------------

    st.markdown("### 📝 ④ 결과")

    st.text_area(
        "주간 핵심 정리 (수정 가능 · 복사 버튼은 수정한 내용으로 복사됩니다)",
        key="wk_report",
        height=560,
    )

    alternatives = state.get("wk_alts") or []

    if alternatives:
        with st.expander(
            "✍️ 다른 제목 후보 (마음에 들면 위 '한 줄 요약'을 직접 바꿔 쓰세요)",
            expanded=False,
        ):
            for item in alternatives:
                st.markdown(f"- {item}")

    if state.get("wk_ai_raw"):
        with st.expander("AI 원문 보기 (이슈 정리 응답)", expanded=False):
            st.code(state["wk_ai_raw"])

    report = state.get("wk_report", "").strip()

    if report:
        copy_buttons(report)

        with st.container(border=True):
            st.markdown(report)


main()
