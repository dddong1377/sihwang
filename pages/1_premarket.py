import datetime
import email.utils
import hmac
import html
import json
import math
import os
import re
import time
import xml.etree.ElementTree as ET
from zoneinfo import ZoneInfo

import requests
import streamlit as st
import streamlit.components.v1 as components


st.set_page_config(page_title="MTNW 장전 시황", layout="wide")

KST = ZoneInfo("Asia/Seoul")
WEEKDAYS = "월화수목금토일"


# ===============================================================
# 1. Groq API 설정 및 요청량 제어
# ===============================================================

GROQ_MODEL = "openai/gpt-oss-120b"
GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_TIMEOUT = 180

# Groq TPM 8,000 제한에 여유를 두는 안전 예산
GROQ_TPM_SAFE_BUDGET = 7500

# 상세 리포트와 압축본을 별도 호출
GROQ_DETAIL_MAX_TOKENS = 3500
GROQ_COMPRESS_MAX_TOKENS = 1500

# 뉴스 최대 전달 건수
MAX_NEWS_ITEMS = 15

# 이전 실제 사용량을 반영한 보수적 토큰 추정치
# 실제 토큰 수와 차이가 있을 수 있으므로 안전 예산도 함께 적용
CHARS_PER_TOKEN_ESTIMATE = 1.65

# 토큰 제한 헤더가 없을 때 사용할 기본 대기 시간
GROQ_DEFAULT_WAIT_SECONDS = 60


def get_groq_api_key():
    api_key = ""

    try:
        api_key = st.secrets.get("GROQ_API_KEY", "")
    except Exception:
        pass

    if not api_key:
        api_key = os.environ.get("GROQ_API_KEY", "")

    return str(api_key).strip()


def get_groq_headers():
    api_key = get_groq_api_key()

    if not api_key:
        raise RuntimeError(
            "Groq API 키가 설정되지 않았습니다. "
            "Streamlit Secrets에 GROQ_API_KEY를 등록해 주세요."
        )

    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


def estimate_tokens(text):
    """실제 토큰 수가 아닌 보수적인 추정치."""

    if not text:
        return 0

    return math.ceil(
        len(text) / CHARS_PER_TOKEN_ESTIMATE
    )


def get_rate_limit_info(headers):
    header_map = {
        key.lower(): value
        for key, value in headers.items()
    }

    keys = [
        "x-ratelimit-limit-requests",
        "x-ratelimit-remaining-requests",
        "x-ratelimit-reset-requests",
        "x-ratelimit-limit-tokens",
        "x-ratelimit-remaining-tokens",
        "x-ratelimit-reset-tokens",
        "retry-after",
        "x-request-id",
    ]

    return {
        key: header_map.get(key, "응답에 없음")
        for key in keys
    }


def calculate_safe_output_tokens(
    prompt,
    requested_max_tokens,
    budget=GROQ_TPM_SAFE_BUDGET,
):
    """
    입력 추정량을 기준으로 출력 토큰을 자동 조정한다.
    TPM 안전 예산에서 입력 추정량을 제외한 만큼만 출력 토큰을 요청한다.
    """

    input_estimate = estimate_tokens(prompt)

    available_output = budget - input_estimate

    if available_output < 500:
        raise RuntimeError(
            "프롬프트 입력량이 너무 많습니다. "
            f"입력 토큰 추정치: {input_estimate:,}개. "
            "뉴스 수집량이나 원문 분량을 줄여 주세요."
        )

    safe_output = min(
        requested_max_tokens,
        available_output,
    )

    return max(500, safe_output)


def parse_reset_seconds(value):
    """
    Groq 제한 헤더의 시간 값을 초 단위로 변환한다.
    예: 50.572s, 1m20s, 2m
    """

    if not value:
        return None

    value = str(value).strip().lower()

    try:
        if value.endswith("ms"):
            return float(value[:-2]) / 1000

        if value.endswith("s") and re.fullmatch(
            r"\d+(?:\.\d+)?s",
            value,
        ):
            return float(value[:-1])

        minutes = re.search(r"(\d+(?:\.\d+)?)m", value)
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


def wait_for_next_groq_call(first_diagnostic, next_prompt, next_output_tokens):
    """
    첫 번째 호출의 토큰 잔여량과 리셋 시간을 확인한다.
    두 번째 호출에 필요한 토큰이 부족하면 제한 시간만큼 대기한다.
    """

    rate_limits = (
        first_diagnostic.get("rate_limits") or {}
    )

    remaining_value = rate_limits.get(
        "x-ratelimit-remaining-tokens"
    )

    reset_value = rate_limits.get(
        "x-ratelimit-reset-tokens"
    )

    next_required = (
        estimate_tokens(next_prompt) + next_output_tokens
    )

    try:
        remaining_tokens = int(remaining_value)
    except (TypeError, ValueError):
        remaining_tokens = None

    reset_seconds = parse_reset_seconds(reset_value)

    # 잔여 토큰이 충분하다면 대기하지 않는다.
    if (
        remaining_tokens is not None
        and remaining_tokens >= next_required
    ):
        return 0

    # 제한 헤더의 리셋 시간이 확인되면 해당 시간만큼 대기
    if reset_seconds is not None:
        wait_seconds = max(2, math.ceil(reset_seconds) + 2)
    else:
        wait_seconds = GROQ_DEFAULT_WAIT_SECONDS

    # 비정상적으로 긴 대기 방지
    wait_seconds = min(wait_seconds, 180)

    return wait_seconds


# ===============================================================
# 2. Google News RSS 설정 및 수집
# ===============================================================

GOOGLE_NEWS_RSS_URL = (
    "https://news.google.com/rss/search"
)

NEWS_QUERIES = [
    ("국내 증시", "한국 증시 코스피 코스닥 주식 시장"),
    ("반도체", "삼성전자 SK하이닉스 반도체 HBM"),
    ("미국 증시", "미국 증시 나스닥 S&P500 다우존스"),
    ("글로벌 경제", "미국 금리 연준 달러 환율 국제유가"),
    ("기업·산업", "국내 기업 실적 공시 산업 주식"),
    ("해외 경제", "중국 일본 유럽 경제 증시"),
]

NEWS_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/156.0.0.0 Safari/537.36"
    )
}


def parse_news_date(value):
    if not value:
        return None

    try:
        dt = email.utils.parsedate_to_datetime(value)

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=KST)
        else:
            dt = dt.astimezone(KST)

        return dt

    except Exception:
        return None


@st.cache_data(ttl=300, show_spinner=False)
def fetch_google_news_rss(query, display_name):
    params = {
        "q": f"{query} when:1d",
        "hl": "ko",
        "gl": "KR",
        "ceid": "KR:ko",
    }

    response = requests.get(
        GOOGLE_NEWS_RSS_URL,
        params=params,
        headers=NEWS_HEADERS,
        timeout=20,
    )

    if response.status_code != 200:
        return []

    try:
        root = ET.fromstring(response.content)
    except ET.ParseError:
        return []

    now = datetime.datetime.now(KST)
    cutoff = now - datetime.timedelta(hours=24)

    results = []

    for item in root.findall(".//item"):
        title = item.findtext("title", default="").strip()
        link = item.findtext("link", default="").strip()
        pub_date = item.findtext("pubDate", default="").strip()
        source_element = item.find("source")

        publisher = ""

        if source_element is not None:
            publisher = (source_element.text or "").strip()

        published = parse_news_date(pub_date)

        if not title or not link or published is None:
            continue

        if published < cutoff or published > now:
            continue

        results.append({
            "category": display_name,
            "title": title,
            "publisher": publisher or "언론사 확인 불가",
            "published": published.strftime("%Y-%m-%d %H:%M"),
            "link": link,
        })

    return results


def collect_news():
    all_news = []
    seen = set()
    errors = []

    for display_name, query in NEWS_QUERIES:
        try:
            items = fetch_google_news_rss(
                query,
                display_name,
            )

            for item in items:
                key = re.sub(
                    r"\s+",
                    "",
                    item["title"].lower(),
                )

                if key in seen:
                    continue

                seen.add(key)
                all_news.append(item)

        except Exception as error:
            errors.append(
                f"{display_name}: {str(error)[:120]}"
            )

    all_news.sort(
        key=lambda x: x["published"],
        reverse=True,
    )

    return all_news, errors


def news_to_text(news, limit=MAX_NEWS_ITEMS):
    if not news:
        return (
            "최근 24시간 이내 수집된 뉴스가 없습니다. "
            "뉴스 관련 사실은 임의로 작성하지 말고 확인 불가로 표시하세요."
        )

    lines = []

    selected_news = news[:limit]

    for index, item in enumerate(selected_news, start=1):
        lines.append(
            f"[{index}] [{item['category']}] "
            f"{item['title']}\n"
            f"언론사: {item['publisher']}\n"
            f"게시 시각: {item['published']}\n"
            f"링크: {item['link']}"
        )

    return "\n\n".join(lines)


def build_news_context_within_budget(
    news,
    base_prompt,
    max_output_tokens,
):
    """
    뉴스 개수를 1건씩 줄이며 입력량을 계산한다.
    입력 추정치 + 출력 토큰이 TPM 안전 예산 안에 들어오도록 조정한다.
    """

    count = min(len(news), MAX_NEWS_ITEMS)

    while count >= 0:
        context = news_to_text(news, count)

        prompt = base_prompt.replace(
            "__NEWS_CONTEXT__",
            context,
        )

        estimated_input = estimate_tokens(prompt)

        estimated_total = (
            estimated_input + max_output_tokens
        )

        if estimated_total <= GROQ_TPM_SAFE_BUDGET:
            return prompt, count, estimated_input

        if count == 0:
            break

        count -= 1

    raise RuntimeError(
        "뉴스를 모두 줄여도 API 요청량이 안전 범위를 초과합니다. "
        "프롬프트 자체의 분량을 줄여야 합니다."
    )


# ===============================================================
# 3. Toss 지수 API 설정
# ===============================================================

TOSS_INDICATOR_URL = (
    "https://wts-cert-api.tossinvest.com"
    "/api/v1/dashboard/wts/overview/indicator/index"
)

TOSS_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/156.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://www.tossinvest.com",
    "Referer": "https://www.tossinvest.com/",
}


TOSS_INDEX_ITEMS = (
    ("코스피", "KGG01P", 2, "extra"),
    ("코스닥", "QGG01P", 2, "extra"),
    ("다우존스", "DJI.DJI", 2, "index"),
    ("S&P500", "SPX.CBI", 2, "index"),
    ("나스닥", "COMP.NAI", 2, "index"),
    ("필라델피아 반도체", "SOX.NAI", 2, "extra"),
    ("VIX", "RGI..VIX", 2, "extra"),
)

EXCLUDED_FUTURES_CODES = {
    "RFU.NQc1",
    "RFU.ESc1",
    "RFU.RTYc1",
}

MAIN_INDEX_LABELS = (
    "다우존스",
    "S&P500",
    "나스닥",
    "영국 FTSE100",
    "프랑스 CAC40",
    "독일 DAX",
    "유로스톡스50",
)

INDEX_NAME_ALIASES = {
    "다우존스": (
        "다우존스", "다우 산업", "Dow Jones",
        "Dow Jones Industrial Average",
    ),
    "S&P500": (
        "S&P 500", "S&P500", "S&P 500 지수", "S&P 500 Index",
    ),
    "나스닥": (
        "나스닥", "나스닥 종합", "NASDAQ", "NASDAQ Composite",
    ),
    "영국 FTSE100": (
        "영국 FTSE100", "FTSE 100", "FTSE100",
    ),
    "프랑스 CAC40": (
        "프랑스 CAC40", "CAC 40", "CAC40",
    ),
    "독일 DAX": (
        "독일 DAX", "DAX", "DAX 40",
    ),
    "유로스톡스50": (
        "유로스톡스50", "Euro Stoxx 50", "EURO STOXX 50",
    ),
    "원/달러 환율": (
        "원/달러", "USD/KRW", "달러/원", "미국 달러",
    ),
    "WTI 유가": (
        "WTI", "WTI 유", "서부 텍사스산 원유",
    ),
    "미 10년물 금리(%)": (
        "미국 10년물", "미 10년물", "US 10Y", "미국 국채 10년",
    ),
    "VIX": ("VIX", "변동성지수"),
    "달러인덱스": (
        "달러인덱스", "달러 지수", "Dollar Index", "DXY",
    ),
    "비트코인": ("비트코인", "Bitcoin", "BTC"),
    "금": ("금", "Gold", "금 선물"),
}


# ===============================================================
# 4. 공통 설정 / 로그인
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


# ===============================================================
# 5. Toss API 요청 헤더
# ===============================================================

def toss_session_headers():
    headers = dict(TOSS_HEADERS)

    cookie = (
        secret("TOSS_INDICATOR_COOKIE")
        or secret("TOSS_NEWS_COOKIE")
        or secret("TOSS_CALENDAR_COOKIE")
    )

    xsrf_token = (
        secret("TOSS_INDICATOR_XSRF_TOKEN")
        or secret("TOSS_NEWS_XSRF_TOKEN")
        or secret("TOSS_CALENDAR_XSRF_TOKEN")
    )

    app_version = (
        secret("TOSS_INDICATOR_APP_VERSION")
        or secret("TOSS_NEWS_APP_VERSION")
        or secret("TOSS_CALENDAR_APP_VERSION")
    )

    browser_tab_id = (
        secret("TOSS_INDICATOR_BROWSER_TAB_ID")
        or secret("TOSS_NEWS_BROWSER_TAB_ID")
        or secret("TOSS_CALENDAR_BROWSER_TAB_ID")
    )

    if cookie:
        headers["Cookie"] = cookie

    if xsrf_token:
        headers["x-xsrf-token"] = xsrf_token

    if app_version:
        headers["x-app-version"] = app_version

    if browser_tab_id:
        headers["x-browser-tab-id"] = browser_tab_id

    return headers


# ===============================================================
# 6. Toss 지수 API 조회
# ===============================================================

@st.cache_data(ttl=60, show_spinner=False)
def get_toss_indicators():
    response = requests.get(
        TOSS_INDICATOR_URL,
        params={"market": "kr"},
        headers=toss_session_headers(),
        timeout=15,
    )

    if response.status_code != 200:
        raise RuntimeError(
            f"Toss 지수 API HTTP {response.status_code}: "
            f"{response.text[:300]}"
        )

    data = response.json()

    if not isinstance(data, dict):
        raise RuntimeError("Toss API 응답 형식이 올바르지 않습니다.")

    result = data.get("result") or {}
    infos = result.get("majorIndicatorInfos")

    if not isinstance(infos, list):
        raise RuntimeError(
            "Toss API 응답에서 majorIndicatorInfos를 찾을 수 없습니다."
        )

    return infos


# ===============================================================
# 7. 데이터 변환
# ===============================================================

def safe_float(value):
    try:
        if value is None:
            return None

        if isinstance(value, str):
            value = value.replace(",", "").replace("%", "").strip()

            if not value:
                return None

        number = float(value)

        if not (number == number):
            return None

        return number

    except (TypeError, ValueError):
        return None


def parse_timestamp(value):
    if value is None or isinstance(value, bool):
        return None

    try:
        if isinstance(value, (int, float)):
            timestamp = float(value)

            if timestamp > 10_000_000_000:
                timestamp /= 1000

            dt = datetime.datetime.fromtimestamp(
                timestamp,
                tz=KST,
            )

        elif isinstance(value, str):
            value = value.strip()

            if not value:
                return None

            if re.fullmatch(r"\d{10,13}", value):
                return parse_timestamp(int(value))

            elif re.fullmatch(r"\d{8}", value):
                dt = datetime.datetime.strptime(
                    value,
                    "%Y%m%d",
                ).replace(tzinfo=KST)

            elif re.fullmatch(r"\d{14}", value):
                dt = datetime.datetime.strptime(
                    value,
                    "%Y%m%d%H%M%S",
                ).replace(tzinfo=KST)

            else:
                dt = datetime.datetime.fromisoformat(
                    value.replace("Z", "+00:00")
                )

                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=KST)
                else:
                    dt = dt.astimezone(KST)

        else:
            return None

        now = datetime.datetime.now(KST)

        if dt.year < 2000:
            return None

        if dt > now + datetime.timedelta(minutes=2):
            return None

        return dt

    except (
        ValueError,
        TypeError,
        OverflowError,
        OSError,
    ):
        return None


def find_candle_timestamps(value, results=None):
    if results is None:
        results = []

    timestamp_keys = {
        "timestamp", "time", "datetime", "date",
        "candleTime", "candleDate", "tradingTime",
        "tradingDate", "startTime", "endTime",
        "startDate", "endDate", "baseDateTime",
        "localDateTime", "tradeDate", "chartTime",
        "x", "t",
    }

    if isinstance(value, dict):
        for key, item in value.items():
            if str(key) in timestamp_keys:
                dt = parse_timestamp(item)

                if dt is not None:
                    results.append(dt)

            if isinstance(item, (dict, list)):
                find_candle_timestamps(item, results)

    elif isinstance(value, list):
        for item in value:
            if isinstance(item, (dict, list)):
                find_candle_timestamps(item, results)

    return results


def format_timestamp(value):
    dt = parse_timestamp(value)

    if dt is None:
        return "확인 불가"

    return dt.strftime("%Y-%m-%d %H:%M")


def get_info_name(info):
    return str(
        info.get("displayName")
        or info.get("name")
        or info.get("code")
        or ""
    ).strip()


def get_info_price(info):
    price = info.get("price") or {}

    if not isinstance(price, dict):
        price = {}

    latest = safe_float(price.get("latestPrice"))
    base = safe_float(price.get("basePrice"))

    return latest, base


def get_info_timestamp(info):
    mini_chart = info.get("miniChart") or {}

    if not isinstance(mini_chart, dict):
        return "확인 불가"

    candles = mini_chart.get("candles")

    if not isinstance(candles, list) or not candles:
        return "확인 불가"

    timestamps = find_candle_timestamps(candles)

    if not timestamps:
        return "확인 불가"

    return max(timestamps).strftime("%Y-%m-%d %H:%M")


# ===============================================================
# 8. 선물 지수 필터
# ===============================================================

def is_futures_index(info):
    code = str(info.get("code") or "").strip()
    name = get_info_name(info).lower()

    if code in EXCLUDED_FUTURES_CODES:
        return True

    if code.upper().startswith("RFU."):
        return True

    futures_keywords = ("선물", "futures", "future")

    return any(keyword in name for keyword in futures_keywords)


# ===============================================================
# 9. Toss 데이터 행 구성
# ===============================================================

def build_toss_rows(infos):
    by_code = {}

    for info in infos:
        if not isinstance(info, dict):
            continue

        code = str(info.get("code") or "").strip()

        if not code or is_futures_index(info):
            continue

        by_code[code] = info

    rows = []

    for label, code, digits, kind in TOSS_INDEX_ITEMS:
        info = by_code.get(code)

        if not info:
            rows.append({
                "label": label,
                "kind": kind,
                "text": "확인 불가",
                "date": "확인 불가",
                "note": "Toss 응답에서 해당 지수 미확인",
                "code": code,
            })
            continue

        latest, base = get_info_price(info)
        date_text = get_info_timestamp(info)

        if latest is None:
            text = "확인 불가"
            note = "현재가 데이터 없음"

        elif base is None or base == 0:
            text = f"{latest:,.{digits}f} (등락 확인 불가)"
            note = "기준가 데이터 없음"

        else:
            change = latest - base
            pct = (change / base) * 100

            arrow = (
                "🔺" if change > 0
                else "🔻" if change < 0
                else "➖"
            )

            text = (
                f"{latest:,.{digits}f} "
                f"({change:+,.{digits}f}, {pct:+.2f}%) {arrow}"
            )
            note = ""

        rows.append({
            "label": label,
            "kind": kind,
            "text": text,
            "date": date_text,
            "note": note,
            "code": code,
        })

    known_codes = {item[1] for item in TOSS_INDEX_ITEMS}

    for info in infos:
        if not isinstance(info, dict):
            continue

        code = str(info.get("code") or "").strip()

        if not code or code in known_codes:
            continue

        if is_futures_index(info):
            continue

        display_name = get_info_name(info)

        if not display_name:
            continue

        latest, base = get_info_price(info)
        date_text = get_info_timestamp(info)

        if latest is None:
            text = "확인 불가"
            note = "현재가 데이터 없음"

        elif base is None or base == 0:
            text = f"{latest:,.2f} (등락 확인 불가)"
            note = "기준가 데이터 없음"

        else:
            change = latest - base
            pct = (change / base) * 100

            arrow = (
                "🔺" if change > 0
                else "🔻" if change < 0
                else "➖"
            )

            text = (
                f"{latest:,.2f} "
                f"({change:+,.2f}, {pct:+.2f}%) {arrow}"
            )
            note = ""

        rows.append({
            "label": display_name,
            "kind": "extra",
            "text": text,
            "date": date_text,
            "note": note,
            "code": code,
        })

    return rows


# ===============================================================
# 10. 해외 주요 지수 표 구성
# ===============================================================

def build_main_index_rows(rows):
    result = []

    row_by_label = {
        row["label"]: row
        for row in rows
    }

    for label in MAIN_INDEX_LABELS:
        found = row_by_label.get(label)

        if found:
            result.append(found)
            continue

        aliases = INDEX_NAME_ALIASES.get(label, ())
        matched = None

        for row in rows:
            row_name = str(row.get("label", "")).lower()

            if any(alias.lower() == row_name for alias in aliases):
                matched = row
                break

        if matched:
            result.append({
                **matched,
                "label": label,
            })
        else:
            result.append({
                "label": label,
                "kind": "index",
                "text": "확인 불가",
                "date": "확인 불가",
                "note": "Toss API 응답에서 해당 지수 미확인",
                "code": "-",
            })

    return result


def indices_markdown(rows):
    lines = [
        "| 구분 | 종가/현재가 (전일 대비) | 기준 시각 | 비고 |",
        "|---|---|---|---|",
    ]

    for row in rows:
        lines.append(
            f"| {row['label']} | {row['text']} | "
            f"{row['date']} | {row['note']} |"
        )

    return "\n".join(lines)


def fetch_indices():
    state = st.session_state

    try:
        infos = get_toss_indicators()
        rows = build_toss_rows(infos)

        state["pm_indices"] = rows
        state["pm_main_indices"] = build_main_index_rows(rows)

        state["pm_indices_note"] = (
            f"Toss 조회 완료 · "
            f"{datetime.datetime.now(KST).strftime('%H:%M:%S')} · "
            f"응답 지표 {len(infos)}개 · 선물 지수 제외"
        )

    except Exception as error:
        state["pm_indices_note"] = f"Toss 지수 조회 실패: {error}"


# ===============================================================
# 11. 프롬프트
# ===============================================================

PREMARKET_PROMPT = """# 역할
너는 경제신문 증권부 기자, 증권사 애널리스트, 네이버 SEO 전문가,
검색광고 마케터, 유튜브 PD를 겸하는 "MTNW 시황 에디터"다.
국내외 최신 주식 뉴스를 조사해 MTNW 광고팀·콘텐츠팀·방송팀이
바로 쓸 수 있는 "한눈에 보이는 데일리 시황 리포트"를 작성한다.

# 실행 모드: [장전]
- 중심 내용: 미국·유럽 마감 결과, 간밤 주요 이슈, 오늘 국내 증시 전망, 오늘 일정
- 지수 표기: 미국·유럽 종가 기준 (휴장이면 사유 표기)
- 키워드: 개장 직후 검색 급증이 예상되는 키워드 우선
- 오늘 날짜: __TODAY__

# 조사 원칙
1. 제공된 뉴스 자료와 지수 자료를 최우선으로 사용한다.
2. 뉴스는 게시 시각이 오늘 기준 최근 24시간 이내인 자료만 사용한다.
3. RSS에 수집되지 않은 기사의 내용은 확인된 사실처럼 작성하지 않는다.
4. 기사 제목만 확인된 경우 세부 내용은 추정하지 않는다.
5. 같은 뉴스는 하나로 통합한다. 기사 문장을 그대로 복사하지 않는다.
6. 확인되지 않은 루머, 증시와 무관한 정치 뉴스는 제외한다.
7. 확인 불가한 수치는 추정하지 말고 "확인 불가"로 표기한다.
8. 한국 개인투자자 관점에서 우선순위를 정한다.
9. 출처 링크는 제공된 뉴스 자료에 있는 링크만 사용한다.
10. 수급 데이터가 제공되지 않으면 외국인·기관·개인 수급을 확인 불가로 표시한다.
11. 실시간 검색 순위 데이터가 없으면 검색량 순위인 것처럼 꾸며내지 않는다.
12. 키워드 순위는 실제 검색량 데이터가 아니라 뉴스 노출 기반 후보라고 명시한다.

# 지수 데이터 규칙
- 아래 [확인된 Toss 지수 데이터]를 기준 수치로 사용한다.
- Toss 데이터에 없는 지수는 임의로 채우지 않는다.
- 기준 시각을 확인하고 최신 종가인지 실시간 지표인지 구분한다.
- 비고에 미확인 또는 데이터 없음으로 표시된 항목은 임의로 채우지 않는다.
- 표기 형식: 지수명 : 지수값 (전일 대비 ±포인트, ±%)
- 대상: 다우, S&P500 현물, 나스닥 현물, FTSE100, CAC40, DAX, 유로스톡스50
- 선물 지수는 사용하지 않는다. S&P500은 반드시 현물 지수만 사용한다.

[확인된 Toss 지수 데이터]
__INDEX_TABLE__

# 최근 24시간 뉴스 자료
아래는 Google News RSS에서 수집한 기사 목록이다.
제목·언론사·게시 시각·링크를 근거로 작성하라.
기사 본문을 확인하지 못한 경우 세부 사실을 만들어내지 마라.

__NEWS_CONTEXT__

# 작성 원칙
- 전체 분량은 핵심 위주로 작성한다.
- 문장은 한 줄 요약형, 표·불릿 위주
- 키워드는 검색광고에 바로 넣을 수 있는 단어형만 사용
- 주요 주장에는 [번호]로 출처를 붙이고 하단 출처 목록과 연결
- 검색 과정 설명 없이 최종 리포트만 작성

# 상세 리포트 출력 형식

## 📅 YYYY년 MM월 DD일 (요일) | MTNW Market Intelligence [장전]
> 📌 **오늘 한 줄 요약**: (20~30자)

### 1. 🌎 해외 증시
| 지수 | 종가/현재가 | 전일 대비 |
|---|---|---|
(7개 지수, 확인 불가 시 사유 표기)

### 2. 🔥 핵심 이슈 TOP5
각 이슈는 아래 4줄:
- **제목** (시장 영향도 ⭐1~5 / 콘텐츠 점수 00/100)
- 무슨 일: 1문장
- 왜 중요한가 + 관련 종목·테마: 1문장
- 향후 일정/리스크: 1문장 [출처번호]

### 3. 🔍 검색 키워드
**뉴스 기반 관심 키워드 TOP20**
| 순위 | 키워드 | 검색 의도 | 광고/SEO 활용 |

**주요 급상승 후보 TOP10**
| 순위 | 키워드 | 뉴스 노출 이유 |

※ 실제 검색량 순위가 아닌 뉴스 노출 기반 후보라고 표시한다.

### 4. 📈 종목·테마·수급
- **언급 TOP10 종목**: 종목 | 이유 | 방송 적합도
- **강세 테마 TOP5**: 테마 | 관련 종목 | 지속 가능성
- **관심 업종 TOP5**: 업종 | 이유
- **수급**: 외국인 / 기관 / 개인 순매수·순매도 요약
  - 실제 수급 데이터가 없으면 확인 불가

### 5. 🌐 글로벌 이슈
미국·중국·일본·유럽·원자재·환율 관련 내용을 각 2~3문장.
제공된 자료에 없는 수치는 확인 불가로 표기한다.

### 6. 📆 오늘 체크 일정
| 시간 | 일정 | 시장 영향 |

### 7. 🎬 오늘 반드시 만들 콘텐츠 TOP3
| 순위 | 뉴스 | 추천 형식 | 핵심 키워드 | 썸네일 문구 |

### 8. 💰 광고 키워드
| 구분 | 키워드 | 전환 가능성 |

### 9. ✍️ 콘텐츠 아이디어
- 유튜브 제목 5개
- 블로그 제목 10개
- 숏폼 아이디어 10개
- 카드뉴스 주제 5개
- 썸네일 문구 20개

### 10. 🎙️ 방송 추천 종목 TOP5 + 오늘 꼭 봐야 할 종목 TOP5
| 종목 | 이유 | 주의점 |

### 11. 🔄 전일 대비 변화
전일 자료가 없으면 비교 불가라고 표시한다.
새로 등장 / 사라진 키워드, 강해진·약해진 테마

### 12. 📎 출처
| 번호 | 언론사 | 기사 제목 | 링크 |
수집된 기사 중 실제 사용한 자료를 출처로 연결한다.

# 최종 점검
- 지수 수치와 기준 시각을 재확인했는가?
- 24시간 초과 기사나 미확인 루머가 없는가?
- 실제 검색량이 없는 키워드를 검색량 순위로 오인하게 하지 않았는가?
- 수급 데이터가 없으면 확인 불가로 처리했는가?
- 확인되지 않은 종목·일정·수치를 만들지 않았는가?

중요:
- 상세 리포트만 작성한다.
- 압축본은 작성하지 않는다.
- 구분자나 인사말, 작성 과정 설명 없이 상세 리포트만 출력한다.
"""


COMPRESS_PROMPT = """# 역할
너는 바쁜 투자자가 5초 만에 읽는 "MTNW 시황 압축 에디터"다.
아래 [원문 시황글]을 읽고 핵심만 남긴 시황글로 압축한다.

# 압축 원칙
1. 원문에 있는 사실만 사용한다. 새로운 수치·종목·뉴스를 추가하지 않는다.
2. "확인 불가", "추정", "후보"는 단정하지 않는다.
3. 모바일 1화면 반 이내로 압축한다.
4. 문장은 한 줄 40자 내외로 작성한다.
5. 핵심 포인트마다 관련 종목을 붙인다.
6. 핵심 키워드·수치·종목명은 **굵게** 표시한다.
7. 상승은 🔺, 하락은 🔻, 중립은 ➖로 표시한다.
8. 가장 중요한 이슈 1개에는 🔥 표시한다.
9. 표는 지수표 1개만 허용한다.
10. 출처는 "출처: 언론사명 나열" 한 줄로 작성한다.

# 출력 형식

## 📅 YYYY년 MM월 DD일 (요일) | 장전 시황

> 📌 **한 줄 요약**: (20~30자)

### 🌎 지수 한눈에
| 지수 | 등락 |
|---|---|
(원문에 있는 미국 3대 지수 + 유럽 4개 + 필요 시 코스피·코스닥)

### 🔥 오늘의 핵심 포인트 TOP3
각 포인트 3줄:
**① 핵심 문장** 🔺/🔻/➖
- 내용: 한 줄
- 📌 관련 종목: 원문에 있는 종목만
- ⏰ 체크: 일정 또는 리스크

### 📈 오늘 주목 종목 TOP5
| 종목 | 한 줄 이유 | 주의 |

### 🏷️ 강세·관심 테마
최대 5개, 한 줄로 나열

### 💰 수급
외국인 / 기관 / 개인 (원문에 없으면 확인 불가)

### 📆 오늘·이번 주 일정
최대 5개

### 🔍 핵심 키워드 10
원문에 등장한 단어형 키워드

### ⚠️ 한 줄 주의

# 원문 시황글
__SOURCE__
"""


# ===============================================================
# 12. Groq 오류 분석
# ===============================================================

def parse_groq_error(response):
    try:
        data = response.json()
    except Exception:
        data = {}

    error_data = data.get("error", {})

    if isinstance(error_data, dict):
        error_code = str(error_data.get("code", ""))
        error_type = str(error_data.get("type", ""))
        error_message = str(error_data.get("message", ""))
    else:
        error_code = ""
        error_type = ""
        error_message = str(error_data)

    return {
        "http_status": response.status_code,
        "error_code": error_code,
        "error_type": error_type,
        "error_message": error_message,
        "raw_response": response.text[:3000],
    }


def classify_groq_error(status_code, error_code="", error_message=""):
    message = f"{error_code} {error_message}".lower()

    if status_code == 401:
        return "invalid_key"

    if status_code == 403:
        return "permission"

    if status_code == 404:
        return "model"

    if status_code == 413:
        if (
            "tokens per minute" in message
            or "tpm" in message
            or "request too large" in message
        ):
            return "tpm"

        return "request_too_large"

    if status_code == 429:
        if "tokens per minute" in message or "tpm" in message:
            return "tpm"

        if "requests per minute" in message or "rpm" in message:
            return "rpm"

        if "daily" in message or "per day" in message:
            return "daily"

        if "quota" in message:
            return "quota"

        return "rate_limit_unknown"

    if status_code >= 500:
        return "server"

    return "other"


def friendly_groq_error(error_info):
    error_type = classify_groq_error(
        error_info.get("http_status", 0),
        error_info.get("error_code", ""),
        error_info.get("error_message", ""),
    )

    messages = {
        "invalid_key": (
            "Groq API 인증 오류입니다. "
            "Groq Console에서 API 키를 확인해 주세요."
        ),
        "permission": (
            "Groq API 권한 오류입니다. "
            "API 키와 모델 접근 권한을 확인해 주세요."
        ),
        "model": (
            "요청한 Groq 모델을 찾을 수 없거나 사용이 중단되었습니다. "
            "모델명을 확인해 주세요."
        ),
        "tpm": (
            "분당 토큰 제한(TPM)을 초과했습니다. "
            "뉴스 입력량이나 출력 토큰을 더 줄여야 합니다."
        ),
        "request_too_large": (
            "Groq가 요청 크기를 초과한 것으로 판단했습니다. "
            "프롬프트 분량을 줄여 주세요."
        ),
        "rpm": (
            "분당 요청 횟수 제한(RPM)에 도달했습니다. "
            "제한 시간이 지난 뒤 다시 시도해 주세요."
        ),
        "daily": (
            "일일 API 사용량 제한에 도달했을 가능성이 있습니다. "
            "Groq Console에서 사용량을 확인해 주세요."
        ),
        "quota": (
            "API 사용량 또는 할당량 제한에 도달했습니다. "
            "Groq Console의 사용량 정보를 확인해 주세요."
        ),
        "rate_limit_unknown": (
            "HTTP 429 제한 오류입니다. "
            "오류 원문과 제한 헤더를 확인해 주세요."
        ),
        "server": (
            "Groq 서버 오류입니다. 잠시 후 다시 시도해 주세요."
        ),
        "other": (
            "Groq API 요청 중 오류가 발생했습니다."
        ),
    }

    return messages.get(error_type, messages["other"])


# ===============================================================
# 13. Groq API 호출 및 사용량 진단
# ===============================================================

def call_groq(
    prompt,
    max_tokens,
    stage="Groq API",
):
    headers = get_groq_headers()

    safe_max_tokens = calculate_safe_output_tokens(
        prompt,
        max_tokens,
    )

    input_estimate = estimate_tokens(prompt)

    diagnostic = {
        "stage": stage,
        "model": GROQ_MODEL,
        "input_characters": len(prompt),
        "estimated_input_tokens": input_estimate,
        "requested_output_tokens": max_tokens,
        "actual_max_output_tokens": safe_max_tokens,
        "estimated_total_tokens": (
            input_estimate + safe_max_tokens
        ),
        "actual_prompt_tokens": None,
        "actual_completion_tokens": None,
        "actual_total_tokens": None,
        "finish_reason": None,
        "request_id": None,
        "rate_limits": {},
        "error": None,
        "called_at": datetime.datetime.now(KST).strftime(
            "%Y-%m-%d %H:%M:%S"
        ),
    }

    state = st.session_state

    if "pm_api_diagnostics" not in state:
        state["pm_api_diagnostics"] = []

    state["pm_api_diagnostics"].append(diagnostic)
    state["pm_api_diagnostic"] = diagnostic

    payload = {
        "model": GROQ_MODEL,
        "messages": [
            {
                "role": "system",
                "content": (
                    "당신은 한국 주식시장 전문 시황 에디터입니다. "
                    "제공된 자료에 없는 사실이나 수치를 만들어내지 마세요. "
                    "확인할 수 없는 정보는 확인 불가라고 표시하세요."
                ),
            },
            {
                "role": "user",
                "content": prompt,
            },
        ],
        "temperature": 0.2,
        "max_tokens": safe_max_tokens,
    }

    try:
        response = requests.post(
            GROQ_API_URL,
            headers=headers,
            json=payload,
            timeout=GROQ_TIMEOUT,
        )

        diagnostic["rate_limits"] = get_rate_limit_info(
            response.headers
        )

        diagnostic["request_id"] = (
            response.headers.get("x-request-id")
            or response.headers.get("request-id")
        )

        if response.status_code != 200:
            error_info = parse_groq_error(response)

            diagnostic["error"] = error_info

            state["pm_api_diagnostic"] = diagnostic

            raise RuntimeError(
                friendly_groq_error(error_info)
                + "\n\n"
                + f"HTTP 상태: {error_info['http_status']}"
                + "\n"
                + f"오류 코드: {error_info['error_code'] or '없음'}"
                + "\n"
                + f"오류 유형: {error_info['error_type'] or '없음'}"
                + "\n"
                + f"오류 원문: "
                + (
                    error_info["error_message"]
                    or error_info["raw_response"]
                )
            )

        data = response.json()
        usage = data.get("usage") or {}

        diagnostic["actual_prompt_tokens"] = usage.get(
            "prompt_tokens"
        )

        diagnostic["actual_completion_tokens"] = usage.get(
            "completion_tokens"
        )

        diagnostic["actual_total_tokens"] = usage.get(
            "total_tokens"
        )

        choices = data.get("choices") or []

        if not choices:
            raise RuntimeError(
                "Groq 응답에 choices 항목이 없습니다."
            )

        choice = choices[0]

        diagnostic["finish_reason"] = choice.get(
            "finish_reason"
        )

        message = choice.get("message") or {}
        result = message.get("content")

        if isinstance(result, list):
            result = "\n".join(
                str(item.get("text", ""))
                for item in result
                if isinstance(item, dict)
            )

        result = str(result or "").strip()

        if not result:
            raise RuntimeError(
                "Groq 응답이 비어 있습니다."
            )

        state["pm_api_diagnostic"] = diagnostic

        if diagnostic["finish_reason"] == "length":
            diagnostic["error"] = {
                "message": (
                    "출력 토큰 제한으로 응답이 중간에 종료됐습니다."
                )
            }

            state["pm_api_diagnostic"] = diagnostic

            raise RuntimeError(
                f"{stage}: 출력 토큰 제한으로 응답이 중간에 종료됐습니다. "
                "진단 화면에서 실제 출력 토큰 사용량을 확인해 주세요."
            )

        return result

    except requests.exceptions.Timeout:
        diagnostic["error"] = {
            "message": "API 요청 시간 초과",
        }

        state["pm_api_diagnostic"] = diagnostic

        raise RuntimeError(
            f"{stage}: Groq API 응답 시간이 초과되었습니다."
        )

    except requests.exceptions.RequestException as error:
        diagnostic["error"] = {
            "message": str(error)[:1000],
        }

        state["pm_api_diagnostic"] = diagnostic

        raise RuntimeError(
            f"{stage}: Groq 네트워크 요청 실패: {str(error)[:300]}"
        )

    except Exception:
        state["pm_api_diagnostic"] = diagnostic
        raise


# ===============================================================
# 14. 상세 리포트 / 압축본 생성
# ===============================================================

def build_compress_prompt(detail_text):
    return COMPRESS_PROMPT.replace(
        "__SOURCE__",
        detail_text,
    )


def run_premarket(use_source_only=False):
    state = st.session_state

    if state.get("pm_api_running", False):
        state["pm_note"] = (
            "이미 작업이 진행 중입니다. "
            "현재 작업이 끝난 후 다시 실행해 주세요."
        )
        return

    state["pm_api_running"] = True

    # 이전 실행의 진단 기록 초기화
    state["pm_api_diagnostics"] = []
    state["pm_api_diagnostic"] = None
    state["pm_request_preview"] = None
    state["pm_compress_preview"] = None

    try:
        now = datetime.datetime.now(KST)

        today = (
            f"{now.year}년 {now.month:02d}월 "
            f"{now.day:02d}일 ({WEEKDAYS[now.weekday()]})"
        )

        pasted = state.get("pm_pasted", "").strip()

        # -------------------------------------------------------
        # A. 직접 붙여넣은 원문: 압축만 실행
        # -------------------------------------------------------

        if use_source_only:
            if not pasted:
                state["pm_note"] = (
                    "압축할 원문을 먼저 붙여넣어 주세요."
                )
                return

            prompt = build_compress_prompt(pasted)

            safe_output = calculate_safe_output_tokens(
                prompt,
                GROQ_COMPRESS_MAX_TOKENS,
            )

            input_estimate = estimate_tokens(prompt)

            state["pm_compress_preview"] = {
                "stage": "원문 압축",
                "prompt_characters": len(prompt),
                "estimated_input_tokens": input_estimate,
                "max_output_tokens": safe_output,
                "estimated_total_tokens": (
                    input_estimate + safe_output
                ),
            }

            with st.spinner(
                "Groq API로 원문을 압축하는 중..."
            ):
                short = call_groq(
                    prompt,
                    max_tokens=GROQ_COMPRESS_MAX_TOKENS,
                    stage="원문 압축",
                )

            state["pm_short"] = short

            state["pm_note"] = (
                f"압축 완료 · "
                f"{datetime.datetime.now(KST).strftime('%H:%M:%S')} · "
                "Groq API 1회 호출"
            )

            return

        # -------------------------------------------------------
        # B. Toss 지수 데이터 확보
        # -------------------------------------------------------

        rows = state.get("pm_indices")

        if not rows:
            infos = get_toss_indicators()
            rows = build_toss_rows(infos)

            state["pm_indices"] = rows
            state["pm_main_indices"] = build_main_index_rows(rows)

        main_rows = state.get("pm_main_indices")

        if not main_rows:
            main_rows = build_main_index_rows(rows)

        index_table = indices_markdown(main_rows)

        # -------------------------------------------------------
        # C. 최근 24시간 뉴스 RSS 수집
        # -------------------------------------------------------

        with st.spinner(
            "최근 24시간 국내외 뉴스 RSS 수집 중..."
        ):
            news, news_errors = collect_news()

        base_prompt = (
            PREMARKET_PROMPT
            .replace("__TODAY__", today)
            .replace("__INDEX_TABLE__", index_table)
        )

        # -------------------------------------------------------
        # D. 1차 호출용 상세 리포트 프롬프트 구성
        # -------------------------------------------------------

        prompt, news_sent, estimated_input = (
            build_news_context_within_budget(
                news,
                base_prompt,
                GROQ_DETAIL_MAX_TOKENS,
            )
        )

        estimated_total = (
            estimated_input + GROQ_DETAIL_MAX_TOKENS
        )

        state["pm_request_preview"] = {
            "stage": "상세 리포트",
            "news_count": len(news),
            "news_sent": news_sent,
            "prompt_characters": len(prompt),
            "estimated_input_tokens": estimated_input,
            "max_output_tokens": GROQ_DETAIL_MAX_TOKENS,
            "estimated_total_tokens": estimated_total,
        }

        # 이전 결과가 남아 잘못 보이지 않도록 초기화
        state["pm_long"] = ""
        state["pm_short"] = ""

        # -------------------------------------------------------
        # E. 1차 API 호출: 상세 리포트만 생성
        # -------------------------------------------------------

        with st.spinner(
            "1/2 상세 리포트를 생성하는 중..."
        ):
            detail = call_groq(
                prompt,
                max_tokens=GROQ_DETAIL_MAX_TOKENS,
                stage="상세 리포트",
            )

        # 상세 리포트는 1차 생성 직후 저장
        state["pm_long"] = detail

        # -------------------------------------------------------
        # F. 2차 호출용 압축 프롬프트 구성
        # -------------------------------------------------------

        compress_prompt = build_compress_prompt(detail)

        compress_input_estimate = estimate_tokens(
            compress_prompt
        )

        compress_safe_output = calculate_safe_output_tokens(
            compress_prompt,
            GROQ_COMPRESS_MAX_TOKENS,
        )

        state["pm_compress_preview"] = {
            "stage": "상세 리포트 기반 압축",
            "prompt_characters": len(compress_prompt),
            "estimated_input_tokens": compress_input_estimate,
            "max_output_tokens": compress_safe_output,
            "estimated_total_tokens": (
                compress_input_estimate + compress_safe_output
            ),
        }

        # -------------------------------------------------------
        # G. TPM 잔여량 확인 후 필요한 경우 대기
        # -------------------------------------------------------

        first_diagnostic = state["pm_api_diagnostics"][-1]

        wait_seconds = wait_for_next_groq_call(
            first_diagnostic,
            compress_prompt,
            compress_safe_output,
        )

        if wait_seconds > 0:
            with st.spinner(
                f"Groq 토큰 제한 초기화를 기다리는 중... "
                f"약 {wait_seconds}초"
            ):
                time.sleep(wait_seconds)

        # -------------------------------------------------------
        # H. 2차 API 호출: 상세 리포트 압축
        # -------------------------------------------------------

        with st.spinner(
            "2/2 상세 리포트를 바탕으로 압축본을 생성하는 중..."
        ):
            short = call_groq(
                compress_prompt,
                max_tokens=GROQ_COMPRESS_MAX_TOKENS,
                stage="상세 리포트 압축",
            )

        state["pm_short"] = short

        error_note = ""

        if news_errors:
            error_note = (
                f" · RSS 일부 수집 실패 {len(news_errors)}건"
            )

        state["pm_note"] = (
            f"생성 완료 · "
            f"{datetime.datetime.now(KST).strftime('%H:%M:%S')} · "
            f"뉴스 {len(news)}건 수집 / {news_sent}건 전달 · "
            "Groq API 2회 호출"
            f"{error_note}"
        )

    except Exception as error:
        # 1차 상세 리포트가 이미 만들어졌다면 그대로 보존
        if state.get("pm_long"):
            state["pm_note"] = (
                f"일부 생성 완료: {error}\n\n"
                "상세 리포트는 저장되어 있습니다. "
                "압축본은 생성되지 않았거나 완성되지 않았을 수 있습니다."
            )
        else:
            state["pm_note"] = f"생성 실패: {error}"

    finally:
        state["pm_api_running"] = False


# ===============================================================
# 15. 마크다운 → HTML / 복사 버튼
# ===============================================================

def md_to_html(markdown):
    output = []
    in_table = False
    in_list = False

    def inline(text):
        safe = html.escape(text)

        return re.sub(
            r"\*\*(.*?)\*\*",
            r"<b>\1</b>",
            safe,
        )

    for line in markdown.splitlines():
        stripped = line.strip()

        if stripped.startswith("|"):
            cells = [
                cell.strip()
                for cell in stripped.split("|")[1:-1]
            ]

            if all(
                re.fullmatch(r":?-+:?", cell)
                for cell in cells
            ):
                continue

            if in_list:
                output.append("</ul>")
                in_list = False

            if not in_table:
                output.append(
                    "<table border='1' cellpadding='6' cellspacing='0' "
                    "style='border-collapse:collapse'>"
                )
                in_table = True

            output.append(
                "<tr>"
                + "".join(
                    f"<td>{inline(cell)}</td>"
                    for cell in cells
                )
                + "</tr>"
            )
            continue

        if in_table:
            output.append("</table>")
            in_table = False

        if stripped.startswith("- "):
            if not in_list:
                output.append("<ul>")
                in_list = True

            output.append(
                f"<li>{inline(stripped[2:])}</li>"
            )
            continue

        if in_list:
            output.append("</ul>")
            in_list = False

        if stripped.startswith("### "):
            output.append(
                f"<h3>{inline(stripped[4:])}</h3>"
            )

        elif stripped.startswith("## "):
            output.append(
                f"<h2>{inline(stripped[3:])}</h2>"
            )

        elif stripped.startswith("# "):
            output.append(
                f"<h1>{inline(stripped[2:])}</h1>"
            )

        elif stripped.startswith("> "):
            output.append(
                f"<p>{inline(stripped[2:])}</p>"
            )

        elif stripped:
            output.append(
                f"<p>{inline(stripped)}</p>"
            )

    if in_table:
        output.append("</table>")

    if in_list:
        output.append("</ul>")

    return "\n".join(output)


def copy_buttons(markdown):
    payload = json.dumps(
        {
            "md": markdown,
            "html": md_to_html(markdown),
        },
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
                    'text/html': new Blob(
                        [P.html],
                        {{type: 'text/html'}}
                    ),
                    'text/plain': new Blob(
                        [P.md],
                        {{type: 'text/plain'}}
                    )
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
# 16. API 진단 정보 표시
# ===============================================================

def show_api_diagnostics():
    state = st.session_state

    st.markdown("### 🔧 ④ API 사용량 및 진단")

    with st.expander(
        "API 요청 및 사용량 상세 정보",
        expanded=True,
    ):
        preview = state.get("pm_request_preview")
        compress_preview = state.get("pm_compress_preview")
        diagnostics = state.get("pm_api_diagnostics", [])

        # -------------------------------------------------------
        # 1차 요청 전 정보
        # -------------------------------------------------------

        if preview:
            st.markdown("#### 1차 요청 전 정보 · 상세 리포트")

            col1, col2, col3 = st.columns(3)

            col1.metric(
                "수집 뉴스",
                f"{preview.get('news_count', 0)}건",
            )

            col2.metric(
                "실제 전달 뉴스",
                f"{preview.get('news_sent', 0)}건",
            )

            col3.metric(
                "프롬프트 글자 수",
                f"{preview.get('prompt_characters', 0):,}",
            )

            st.caption(
                "예상 입력 토큰: "
                f"{preview.get('estimated_input_tokens', 0):,}개 "
                "(실제 토큰 수가 아닌 추정치)"
            )

            st.caption(
                "최대 출력 토큰: "
                f"{preview.get('max_output_tokens', 0):,}개"
            )

            st.caption(
                "입력+출력 예상 합계: "
                f"{preview.get('estimated_total_tokens', 0):,}개"
            )

        # -------------------------------------------------------
        # 2차 요청 전 정보
        # -------------------------------------------------------

        if compress_preview:
            st.divider()
            st.markdown("#### 2차 요청 전 정보 · 압축본")

            col1, col2, col3 = st.columns(3)

            col1.metric(
                "프롬프트 글자 수",
                f"{compress_preview.get('prompt_characters', 0):,}",
            )

            col2.metric(
                "예상 입력 토큰",
                f"{compress_preview.get('estimated_input_tokens', 0):,}",
            )

            col3.metric(
                "최대 출력 토큰",
                f"{compress_preview.get('max_output_tokens', 0):,}",
            )

            st.caption(
                "입력+출력 예상 합계: "
                f"{compress_preview.get('estimated_total_tokens', 0):,}개"
            )

        # -------------------------------------------------------
        # 각 API 호출 결과
        # -------------------------------------------------------

        if diagnostics:
            st.divider()
            st.markdown("#### API 호출별 응답 정보")

            for index, diagnostic in enumerate(diagnostics, start=1):
                stage = diagnostic.get(
                    "stage",
                    f"{index}차 호출",
                )

                with st.expander(
                    f"{index}차 호출 · {stage}",
                    expanded=True,
                ):
                    col1, col2, col3 = st.columns(3)

                    prompt_tokens = diagnostic.get(
                        "actual_prompt_tokens"
                    )

                    completion_tokens = diagnostic.get(
                        "actual_completion_tokens"
                    )

                    total_tokens = diagnostic.get(
                        "actual_total_tokens"
                    )

                    col1.metric(
                        "실제 입력 토큰",
                        (
                            f"{prompt_tokens:,}"
                            if prompt_tokens is not None
                            else "미확인"
                        ),
                    )

                    col2.metric(
                        "실제 출력 토큰",
                        (
                            f"{completion_tokens:,}"
                            if completion_tokens is not None
                            else "미확인"
                        ),
                    )

                    col3.metric(
                        "전체 사용 토큰",
                        (
                            f"{total_tokens:,}"
                            if total_tokens is not None
                            else "미확인"
                        ),
                    )

                    st.write(
                        "모델:",
                        diagnostic.get("model", "확인 불가"),
                    )

                    st.write(
                        "호출 시각:",
                        diagnostic.get("called_at", "확인 불가"),
                    )

                    st.write(
                        "입력 토큰 추정치:",
                        f"{diagnostic.get('estimated_input_tokens', 0):,}",
                    )

                    st.write(
                        "요청한 출력 토큰:",
                        f"{diagnostic.get('requested_output_tokens', 0):,}",
                    )

                    st.write(
                        "실제 요청 출력 한도:",
                        f"{diagnostic.get('actual_max_output_tokens', 0):,}",
                    )

                    st.write(
                        "예상 요청 토큰 합계:",
                        f"{diagnostic.get('estimated_total_tokens', 0):,}",
                    )

                    st.write(
                        "응답 종료 사유:",
                        diagnostic.get("finish_reason") or "확인 불가",
                    )

                    st.write(
                        "요청 ID:",
                        diagnostic.get("request_id") or "확인 불가",
                    )

                    st.markdown("##### API 제한 헤더")

                    st.json(
                        diagnostic.get("rate_limits", {})
                    )

                    if diagnostic.get("error"):
                        st.markdown("##### 오류 상세 정보")
                        st.error("해당 API 호출에 오류가 발생했습니다.")

                        st.json(
                            diagnostic["error"]
                        )

        if not preview and not compress_preview and not diagnostics:
            st.caption(
                "아직 API 실행 기록이 없습니다."
            )


# ===============================================================
# 17. 화면
# ===============================================================

def main():
    state = st.session_state

    st.title("MTNW 장전 시황")

    st.caption(
        "Toss 지수와 최근 24시간 뉴스 RSS를 수집한 뒤, "
        "Groq API 1차 호출로 상세 리포트를 생성하고 "
        "2차 호출로 압축본을 생성합니다. "
        "오후 시황은 왼쪽 메뉴의 'app'에서 작성합니다."
    )

    # -----------------------------------------------------------
    # ① 해외 지수
    # -----------------------------------------------------------

    st.markdown("### 🌎 ① 해외 지수·지표 확인")

    with st.container(border=True):
        col_btn, col_note = st.columns(
            [1.6, 5],
            vertical_alignment="center",
        )

        col_btn.button(
            "🔄 해외 지수 불러오기",
            on_click=fetch_indices,
            type="primary",
        )

        if state.get("pm_indices_note"):
            col_note.caption(state["pm_indices_note"])

        rows = state.get("pm_indices")

        if rows:
            st.markdown(indices_markdown(rows))

            st.caption(
                "※ Toss API에서 실제 반환된 값만 표시합니다. "
                "선물 지수는 제외하며, 응답에 없는 지수는 확인 불가로 표시합니다. "
                "기준 시각은 캔들 데이터에서 확인되는 시간만 표시합니다."
            )

        else:
            st.caption(
                "아직 조회 전입니다. "
                "생성 버튼을 누르면 지수도 자동으로 먼저 조회합니다."
            )

    # -----------------------------------------------------------
    # ② 생성
    # -----------------------------------------------------------

    st.markdown("### ✨ ② 장전 시황 생성")

    with st.container(border=True):
        st.text_area(
            "원문 직접 붙여넣기 (선택)",
            key="pm_pasted",
            height=140,
            help=(
                "이미 만들어 둔 긴 시황글이 있으면 붙여넣고 "
                "'압축만 실행'을 누르세요. 뉴스 수집 단계를 건너뜁니다."
            ),
        )

        col_a, col_b, col_c = st.columns(
            [1.8, 1.4, 4],
            vertical_alignment="center",
        )

        col_a.button(
            "✨ 장전 시황 생성 (뉴스 수집 + 압축)",
            type="primary",
            on_click=run_premarket,
            args=(False,),
            use_container_width=True,
            disabled=state.get("pm_api_running", False),
        )

        col_b.button(
            "압축만 실행",
            on_click=run_premarket,
            args=(True,),
            use_container_width=True,
            help="붙여넣은 원문만 압축합니다.",
            disabled=state.get("pm_api_running", False),
        )

        if state.get("pm_note"):
            col_c.caption(state["pm_note"])

    # -----------------------------------------------------------
    # ③ 결과
    # -----------------------------------------------------------

    st.markdown("### 📝 ③ 결과")

    tab_short, tab_long = st.tabs(
        [
            "한눈에 보이는 시황글",
            "원본 상세 리포트",
        ]
    )

    with tab_short:
        st.text_area(
            "압축 시황글 (수정 가능 · 복사 버튼은 수정한 내용으로 복사됩니다)",
            key="pm_short",
            height=480,
        )

        short = state.get("pm_short", "").strip()

        if short:
            copy_buttons(short)

            with st.container(border=True):
                st.markdown(short)

    with tab_long:
        long_text = state.get("pm_long", "").strip()

        if long_text:
            copy_buttons(long_text)

            with st.container(border=True):
                st.markdown(long_text)

        else:
            st.caption(
                "원본 상세 리포트가 아직 없습니다. "
                "직접 붙여넣은 원문만 압축한 경우에는 표시되지 않습니다."
            )

    # -----------------------------------------------------------
    # ④ API 진단
    # -----------------------------------------------------------

    show_api_diagnostics()


main()
