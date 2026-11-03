import datetime
import email.utils
import hmac
import html
import json
import math
import os
import re
import tempfile
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

# 한 요청(입력 + 최대 출력)이 넘지 않도록 하는 안전 예산 (TPM 8,000 기준)
GROQ_TPM_SAFE_BUDGET = 7500

# 추론 토큰이 출력 한도를 먼저 소진하지 않도록 추론량을 낮춤
# (모델이 지원하지 않으면 자동으로 제외하고 재시도)
GROQ_REASONING_EFFORT = "low"

GROQ_COMPRESS_MAX_TOKENS = 1300

MAX_NEWS_ITEMS = 16

# 한국어 토큰 추정 오차를 고려한 보수적 추정
CHARS_PER_TOKEN_ESTIMATE = 1.65

GROQ_DEFAULT_WAIT_SECONDS = 60
GROQ_WAIT_SAFETY_SECONDS = 3


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
    input_estimate = estimate_tokens(prompt)
    available_output = budget - input_estimate

    if available_output < 500:
        raise RuntimeError(
            "프롬프트 입력량이 너무 많습니다. "
            f"입력 토큰 추정치: {input_estimate:,}개. "
            "뉴스 수집량이나 원문 분량을 줄여 주세요."
        )

    return min(requested_max_tokens, available_output)


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


def parse_retry_seconds(response, message):
    retry_after = response.headers.get("retry-after")

    if retry_after:
        try:
            return float(retry_after)
        except ValueError:
            pass

    found = re.search(
        r"try again in\s+([0-9hms.]+)",
        message or "",
        re.I,
    )

    if found:
        return parse_reset_seconds(found.group(1))

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


def wait_for_next_groq_call(
    previous_diagnostic,
    next_prompt,
    next_output_tokens,
):
    rate_limits = (
        previous_diagnostic.get("rate_limits") or {}
    )

    remaining_value = rate_limits.get(
        "x-ratelimit-remaining-tokens"
    )

    reset_value = rate_limits.get(
        "x-ratelimit-reset-tokens"
    )

    next_required = (
        estimate_tokens(next_prompt)
        + next_output_tokens
    )

    try:
        remaining_tokens = int(remaining_value)
    except (TypeError, ValueError):
        remaining_tokens = None

    if (
        remaining_tokens is not None
        and remaining_tokens >= next_required
    ):
        return 0

    finished_at = previous_diagnostic.get("finished_at")

    elapsed = (
        max(0.0, time.time() - finished_at)
        if finished_at
        else 0.0
    )

    reset_seconds = parse_reset_seconds(reset_value)

    if reset_seconds is None:
        reset_seconds = GROQ_DEFAULT_WAIT_SECONDS

    # 이전 응답 이후 이미 초기화 시간이 지났으면 대기하지 않음
    if elapsed >= reset_seconds:
        return 0

    wait_seconds = (
        math.ceil(reset_seconds - elapsed)
        + GROQ_WAIT_SAFETY_SECONDS
    )

    return min(max(2, wait_seconds), 180)


def wait_before_api_call(
    previous_diagnostic,
    next_prompt,
    next_output_tokens,
    stage,
):
    if not previous_diagnostic:
        return

    wait_seconds = wait_for_next_groq_call(
        previous_diagnostic,
        next_prompt,
        next_output_tokens,
    )

    if wait_seconds <= 0:
        return

    sleep_with_countdown(
        wait_seconds,
        f"{stage} 호출 전 Groq 토큰 제한 초기화 대기",
    )


# ===============================================================
# 1-2. 하루 토큰 사용량 기록 및 상단 표시 (이 앱 기준 추정)
# ===============================================================

# 무료 플랜 하루 토큰 한도 (Groq 콘솔 limits 페이지의 값에 맞게 수정)
GROQ_DAILY_TOKEN_LIMIT = 200_000

# 한도 부족 경고 기준 (1회 생성에 필요한 토큰 추정치)
LOW_TOKEN_WARNING = 60_000

USAGE_FILE = os.path.join(
    tempfile.gettempdir(),
    "mtnw_groq_usage.json",
)

# 화면 맨 위 표시 영역 (main()에서 지정)
TOKEN_BOX = {"box": None}


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


def format_token_value(value):
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return "확인 불가"


def render_token_status():
    box = TOKEN_BOX.get("box")

    if box is None:
        return

    try:
        usage = load_usage()

        used = int(usage.get("tokens", 0))
        calls = int(usage.get("calls", 0))
        remaining = max(0, GROQ_DAILY_TOKEN_LIMIT - used)

        # 가장 최근 호출의 분당 토큰 잔량(응답 헤더)
        minute_remaining = None

        diagnostics = (
            st.session_state.get("pm_api_diagnostics") or []
        )

        for diagnostic in reversed(diagnostics):
            limits = diagnostic.get("rate_limits") or {}
            value = limits.get("x-ratelimit-remaining-tokens")

            if value not in (None, "응답에 없음"):
                minute_remaining = value
                break

        with box.container():
            with st.container(border=True):
                col1, col2, col3, col4 = st.columns(4)

                col1.metric(
                    "오늘 남은 토큰 (추정)",
                    f"{remaining:,}",
                )

                col2.metric(
                    "오늘 사용 토큰",
                    f"{used:,} / {GROQ_DAILY_TOKEN_LIMIT:,}",
                )

                col3.metric(
                    "오늘 API 호출",
                    f"{calls}회",
                )

                col4.metric(
                    "분당 남은 토큰 (직전 호출)",
                    format_token_value(minute_remaining),
                )

                st.progress(
                    min(1.0, used / GROQ_DAILY_TOKEN_LIMIT)
                )

                if remaining < LOW_TOKEN_WARNING:
                    st.warning(
                        f"오늘 남은 토큰이 {LOW_TOKEN_WARNING:,} 미만입니다. "
                        "1회 생성에 수만 토큰이 들 수 있어 "
                        "도중에 하루 한도에 걸릴 수 있습니다."
                    )

                st.caption(
                    "이 페이지가 기록한 사용량 기준의 추정치입니다. "
                    "같은 Groq 조직의 다른 페이지·도구 사용분은 반영되지 않고, "
                    "초기화 시각은 한국 시간 자정으로 가정했습니다. "
                    "앱을 재시작하면 기록이 지워질 수 있으니 "
                    "정확한 잔량은 Groq 콘솔의 limits 페이지에서 확인하세요."
                )

    except Exception:
        pass


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


def select_news(news, limit=MAX_NEWS_ITEMS):
    """카테고리별로 돌아가며 최신 기사를 골라 한 주제에 쏠리지 않게 합니다."""

    buckets = {}
    order = []

    for item in news:
        category = item["category"]

        if category not in buckets:
            buckets[category] = []
            order.append(category)

        buckets[category].append(item)

    picked = []

    while len(picked) < limit and any(buckets.values()):
        for category in order:
            if buckets[category] and len(picked) < limit:
                picked.append(buckets[category].pop(0))

    return picked


def news_to_text(news, limit=None):
    """AI에는 제목·언론사·게시 시각만 전달합니다(링크는 토큰 절약을 위해 제외)."""

    items = news if limit is None else news[:limit]

    if not items:
        return (
            "최근 24시간 이내 수집된 뉴스가 없습니다. "
            "뉴스 관련 사실은 임의로 작성하지 말고 확인 불가로 표시하세요."
        )

    lines = []

    for index, item in enumerate(items, start=1):
        lines.append(
            f"[{index}] [{item['category']}] "
            f"{item['title']} "
            f"({item['publisher']}, {item['published']})"
        )

    return "\n".join(lines)


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

KNOWN_INDEX_CODES = {item[1] for item in TOSS_INDEX_ITEMS}

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


def report_index_rows(rows, main_rows):
    """리포트에 쓸 표: 해외 주요 7개 + Toss에서 확인된 보조 지표."""

    main_labels = {row["label"] for row in main_rows}

    extras = [
        row for row in rows
        if row.get("code") in KNOWN_INDEX_CODES
        and row["label"] not in main_labels
        and row["text"] != "확인 불가"
    ]

    return list(main_rows) + extras


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
# 11. 프롬프트 구성
# ===============================================================

PREMARKET_COMMON_PROMPT = """# 역할
너는 경제신문 증권부 기자, 증권사 애널리스트,
네이버 SEO 전문가, 검색광고 마케터, 유튜브 PD를 겸하는
"MTNW 시황 에디터"다.

국내외 최신 주식 뉴스를 조사해 MTNW 광고팀·콘텐츠팀·방송팀이
바로 쓸 수 있는 "한눈에 보이는 데일리 시황 리포트"를 작성한다.
이 리포트는 여러 파트로 나누어 작성되며, 지금은 그중 한 파트만 작성한다.

# 실행 모드: 장전
- 중심 내용: 미국·유럽 마감 결과, 간밤 주요 이슈, 오늘 국내 증시 전망, 오늘 일정
- 지수 표기: 미국·유럽 종가 기준 (휴장이면 사유 표기)
- 키워드: 개장 직후 검색 급증이 예상되는 키워드 우선
- 오늘 날짜: __TODAY__

# 조사 원칙
1. 제공된 뉴스 자료와 지수 자료를 최우선으로 사용한다.
2. 뉴스는 게시 시각이 오늘 기준 최근 24시간 이내인 자료만 사용한다.
3. 수집되지 않은 기사의 내용은 확인된 사실처럼 작성하지 않는다.
4. 기사 제목만 확인된 경우 세부 내용은 추정하지 않는다.
5. 같은 뉴스는 하나로 통합한다. 기사 문장을 그대로 복사하지 않는다.
6. 확인되지 않은 루머, 증시와 무관한 정치 뉴스는 제외한다.
7. 확인 불가한 수치는 추정하지 말고 "확인 불가"로 표기한다.
8. 한국 개인투자자 관점에서 우선순위를 정한다.
9. 출처는 뉴스 번호 [번호] 형식으로만 표기한다. 링크와 출처 표는 시스템이 자동으로 붙이므로 작성하지 않는다.
10. 수급 데이터가 제공되지 않으면 외국인·기관·개인 수급을 확인 불가로 표시한다.
11. 실시간 검색 순위 데이터가 없으면 검색량 순위인 것처럼 꾸며내지 않는다.
12. 키워드 순위는 실제 검색량 데이터가 아니라 뉴스 노출 기반 후보라고 명시한다.

__INDEX_BLOCK__
# 최근 24시간 뉴스 자료
아래는 Google News RSS에서 수집한 기사 목록이다. [번호] [분류] 제목 (언론사, 게시 시각) 형식이다.
제목·언론사·게시 시각을 근거로 작성하라.
기사 본문을 확인하지 못한 경우 세부 사실을 만들어내지 마라.

__NEWS_CONTEXT__

# 공통 작성 원칙
- 핵심 위주로 작성한다.
- 문장은 한 줄 요약형, 표·불릿 위주로 작성한다.
- 주요 주장에는 뉴스 번호를 [번호]로 붙인다.
- 검색 과정 설명 없이 최종 리포트 내용만 작성한다.
- 이번 파트에 지정된 항목만 작성하고 다른 파트의 항목은 작성하지 않는다.
- 확인할 수 없는 사실은 확인 불가로 처리한다.
"""


INDEX_BLOCK_TEMPLATE = """# 지수 데이터 규칙
- 아래 Toss 지수 데이터를 기준 수치로 사용한다.
- Toss 데이터에 없는 지수는 임의로 채우지 않는다.
- 기준 시각을 확인하고 최신 종가인지 실시간 지표인지 구분한다.
- 비고에 미확인 또는 데이터 없음으로 표시된 항목은 임의로 채우지 않는다.
- 선물 지수는 사용하지 않는다. S&P500은 반드시 현물 지수만 사용한다.

[확인된 Toss 지수 데이터]
__INDEX_TABLE__

"""


# key: 결과 저장 키 / use_index: 지수 자료 포함 여부 / max_tokens: 출력 한도
DETAIL_PARTS = [
    {
        "key": "overseas",
        "name": "한 줄 요약 · 해외 증시",
        "max_tokens": 1000,
        "use_index": True,
        "instruction": """
# 작성할 파트

> 📌 **오늘 한 줄 요약**: 20~30자

### 1. 🌎 해외 증시
(지수 표는 시스템이 이 제목 바로 아래에 자동 삽입하므로 표는 작성하지 않는다.)
이 제목 아래에 간밤 해외 증시·지표 흐름을 불릿 4~6개로 정리한다.
- 미국·유럽 마감 결과와 방향성
- 제공된 지표(금리·유가·환율·VIX 등)가 있으면 수치와 함께 정리
- 확인 불가 항목은 사유(휴장 등)와 함께 표기
제공된 지수 자료에 없는 수치는 만들지 않는다.

이 파트에는 위 두 항목만 작성한다. 문서 제목(## 📅 ...)은 쓰지 않는다.
""",
    },
    {
        "key": "issues",
        "name": "핵심 이슈 TOP5",
        "max_tokens": 1200,
        "use_index": False,
        "instruction": """
# 작성할 파트

### 2. 🔥 핵심 이슈 TOP5
각 이슈는 아래 4줄 형식으로 작성한다.
- **제목** (시장 영향도 ⭐1~5 / 콘텐츠 점수 00/100)
- 무슨 일: 1문장
- 왜 중요한가 + 관련 종목·테마: 1문장
- 향후 일정/리스크: 1문장 [출처번호]

뉴스 자료에서 확인되는 이슈만 선정한다. 5개를 채울 수 없으면 확인된 개수만 작성한다.
이 파트에는 위 항목만 작성한다.
""",
    },
    {
        "key": "keywords",
        "name": "검색 키워드",
        "max_tokens": 1400,
        "use_index": False,
        "instruction": """
# 작성할 파트

### 3. 🔍 검색 키워드
표 위에 한 줄로 "실제 검색량 순위가 아닌 뉴스 노출 기반 후보"임을 명시한다.

**뉴스 기반 관심 키워드 TOP20**
| 순위 | 키워드 | 검색 의도 | 광고/SEO 활용 |
|---|---|---|---|
(키워드는 단어형, 검색 의도·활용은 각 10자 내외)

**주요 급상승 후보 TOP10**
| 순위 | 키워드 | 뉴스 노출 이유 |
|---|---|---|
(이유는 10자 내외)

이 파트에는 위 항목만 작성한다.
""",
    },
    {
        "key": "stocks",
        "name": "종목·테마·수급",
        "max_tokens": 1400,
        "use_index": False,
        "instruction": """
# 작성할 파트

### 4. 📈 종목·테마·수급
**언급 TOP10 종목**
| 종목 | 이유(한 줄) | 방송 적합도(상/중/하) |
|---|---|---|

**강세 테마 TOP5**
| 테마 | 관련 종목 | 지속 가능성 |
|---|---|---|

**관심 업종 TOP5**
| 업종 | 이유 |
|---|---|

**수급**
- 외국인 / 기관 / 개인 순매수·순매도 요약 (제공된 자료에 없으면 "확인 불가")

뉴스에서 실제로 확인된 종목·테마만 사용한다. 이 파트에는 위 항목만 작성한다.
""",
    },
    {
        "key": "schedule",
        "name": "글로벌 이슈 · 일정",
        "max_tokens": 1300,
        "use_index": True,
        "instruction": """
# 작성할 파트

### 5. 🌐 글로벌 이슈 (미국·중국·일본·유럽·원자재·환율)
각 분야를 2~3문장으로 작성한다.
제공된 지수·뉴스 자료에 없는 수치(금리, 달러인덱스, 유가, VIX 등)는 확인 불가로 표기한다.

### 6. 📆 오늘 체크 일정
| 시간 | 일정 | 시장 영향 |
|---|---|---|
뉴스 자료로 확인할 수 있는 일정만 작성한다. 시간·일정이 확인되지 않으면 확인 불가로 표시한다.

이 파트에는 위 두 항목만 작성한다.
""",
    },
    {
        "key": "content",
        "name": "콘텐츠 TOP3 · 광고 키워드",
        "max_tokens": 1200,
        "use_index": False,
        "instruction": """
# 작성할 파트

### 7. 🎬 오늘 반드시 만들 콘텐츠 TOP3
| 순위 | 뉴스 | 추천 형식 | 핵심 키워드 | 썸네일 문구(15자 이하) |
|---|---|---|---|---|

### 8. 💰 광고 키워드 (각 5개 이내)
| 구분(브랜드/일반/정보탐색/급상승/롱테일) | 키워드 | 전환 가능성 |
|---|---|---|

뉴스 자료로 확인된 이슈만 근거로 삼는다. 이 파트에는 위 두 항목만 작성한다.
""",
    },
    {
        "key": "ideas",
        "name": "콘텐츠 아이디어",
        "max_tokens": 1500,
        "use_index": False,
        "instruction": """
# 작성할 파트

### 9. ✍️ 콘텐츠 아이디어
- 유튜브 제목 5개
- 블로그 제목 10개 (SEO형)
- 숏폼 아이디어 10개 (한 줄씩)
- 카드뉴스 주제 5개
- 썸네일 문구 20개 (15자 이하, 클릭 유도형)

제공된 뉴스와 확인된 이슈를 기반으로 작성한다.
확인되지 않은 사실을 제목에 단정적으로 넣지 않는다.
이 파트에는 위 항목만 작성한다.
""",
    },
    {
        "key": "broadcast",
        "name": "방송 추천 종목 · 전일 대비 변화",
        "max_tokens": 1300,
        "use_index": False,
        "instruction": """
# 작성할 파트

### 10. 🎙️ 방송 추천 종목 TOP5 + 오늘 꼭 봐야 할 종목 TOP5
| 종목 | 이유 | 주의점 |
|---|---|---|
뉴스에서 실제로 확인된 종목만 사용한다.
추천은 콘텐츠 제작 관점의 관심 후보이며 투자 권유가 아니다.

### 11. 🔄 전일 대비 변화
전일 자료가 없으면 "비교 불가"라고 표시한다.
새로 등장 / 사라진 키워드, 강해진·약해진 테마를 각 1줄로 정리한다.

이 파트에는 위 두 항목만 작성한다.
""",
    },
]

DETAIL_PART_KEYS = [part["key"] for part in DETAIL_PARTS]

# 압축에 쓸 파트: 필수는 항상 포함, 선택은 토큰 예산 안에서만 포함
COMPRESS_REQUIRED = ["overseas", "issues", "stocks"]
COMPRESS_OPTIONAL = ["schedule", "keywords"]  # 뒤에서부터 먼저 제외


COMPRESS_PROMPT = """# 역할
너는 바쁜 투자자가 5초 만에 읽는 "MTNW 시황 압축 에디터"다.
아래 상세 리포트를 읽고 핵심만 남긴 시황글로 압축한다.

# 압축 원칙
1. 원문에 있는 사실만 사용한다. 새로운 수치·종목·뉴스를 추가하지 않는다.
2. "확인 불가", "추정", "후보"는 단정하지 않는다.
3. 모바일 1화면 반 이내로 압축한다.
4. 문장은 한 줄 40자 내외로 작성한다.
5. 핵심 포인트마다 관련 종목을 붙인다.
6. 핵심 키워드·수치·종목명은 **굵게** 표시한다.
7. 상승은 🔺, 하락은 🔻, 중립은 ➖로 표시한다.
8. 가장 중요한 이슈 1개에는 🔥 표시한다.
9. 표는 지수표 1개만 허용한다. 지수표에는 반드시 현재값(종가)과 등락을 모두 표시한다.
10. 출처는 "출처: 언론사명 나열" 한 줄로 작성한다.
11. 설명 문장 없이 아래 형식의 최종 결과만 출력한다.

# 출력 형식

## 📅 YYYY년 MM월 DD일 (요일) | 장전 시황

> 📌 **한 줄 요약**: 20~30자

__INDEX_SECTION__

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
최대 5개 (원문에 일정이 없으면 확인 불가)

### 🔍 핵심 키워드 10
원문에 등장한 단어형 키워드

### ⚠️ 한 줄 주의

# 원문 시황글
__SOURCE__
"""


def build_part_prompt(part, today, index_table, news_text):
    if part["use_index"]:
        index_block = INDEX_BLOCK_TEMPLATE.replace(
            "__INDEX_TABLE__",
            index_table,
        )
    else:
        index_block = ""

    common = (
        PREMARKET_COMMON_PROMPT
        .replace("__TODAY__", today)
        .replace("__INDEX_BLOCK__", index_block)
        .replace("__NEWS_CONTEXT__", news_text)
    )

    limit_chars = int(part["max_tokens"] * 1.1)

    return (
        common
        + "\n\n"
        + part["instruction"]
        + "\n\n중요: 위에 지정된 항목만 작성하세요. "
        + f"공백 포함 약 {limit_chars:,}자 이내로 간결하게 작성하고, "
        + "서론·설명·맺음말은 쓰지 마세요."
    )


INDEX_MARKER = "[[INDEX_TABLE]]"

# 통합 생성: 지수표는 코드가 직접 삽입 (AI는 표시 줄만 출력)
COMPRESS_INDEX_SYSTEM = f"""### 🌎 지수 한눈에
{INDEX_MARKER}
위 줄은 그대로 출력하고 지수표는 직접 쓰지 않는다. 지수 이름·수치도 따로 쓰지 않는다."""

# 붙여넣은 원문 압축: AI가 원문 지수표를 옮겨 적음
COMPRESS_INDEX_FREE = """### 🌎 지수 한눈에
| 지수 | 현재값 | 등락 |
|---|---|---|
원문 지수표에 있는 지수만, 원문 순서 그대로 쓴다. 원문에 없는 지수는 추가하지 않는다.
- 현재값 열: 원문의 종가/현재가 숫자를 소수점까지 그대로 적는다. 반올림하거나 바꾸지 않는다.
- 등락 열: 원문의 등락률(%)과 🔺/🔻/➖를 적는다.
- 원문에 값이 "확인 불가"인 지수는 표에서 뺀다."""


def build_compress_prompt(detail_text, system_index=False):
    section = (
        COMPRESS_INDEX_SYSTEM
        if system_index
        else COMPRESS_INDEX_FREE
    )

    return (
        COMPRESS_PROMPT
        .replace("__INDEX_SECTION__", section)
        .replace("__SOURCE__", detail_text)
    )


def split_index_text(text):
    """'5,635.93 (-223.15, -3.81%) 🔻' → ('5,635.93', '-3.81% 🔻')"""

    text = str(text or "").strip()

    match = re.match(
        r"^([\d,]+(?:\.\d+)?)\s*\((.*)\)\s*(\S*)$",
        text,
    )

    if not match:
        return "확인 불가", "확인 불가"

    value, inner, arrow = match.groups()

    pct = re.search(r"[+-]?\d+(?:\.\d+)?%", inner)

    if pct:
        change = f"{pct.group(0)} {arrow}".strip()
    else:
        change = inner.strip() or "확인 불가"

    return value, change


def compact_index_table(rows):
    """화면 ①에 표시되는 지수를 같은 순서로 '지수/현재값/등락' 표로 만듭니다."""

    lines = [
        "| 지수 | 현재값 | 등락 |",
        "|---|---|---|",
    ]

    for row in rows:
        value, change = split_index_text(row.get("text"))

        lines.append(
            f"| {row.get('label', '')} | {value} | {change} |"
        )

    return "\n".join(lines)


def insert_compact_index_table(short, table_md):
    """AI가 쓴 압축본의 지수 자리에 코드가 만든 표를 넣습니다."""

    # 표시 줄 아래에 AI가 덧붙인 안내 문장 제거
    short = re.sub(
        r"^\s*위 줄은 그대로 출력[^\n]*\n?",
        "",
        short,
        flags=re.M,
    )

    if INDEX_MARKER in short:
        return short.replace(INDEX_MARKER, table_md)

    # 표시 줄이 없으면 지수 제목 아래의 AI 표를 지우고 삽입
    match = re.search(r"^###\s*🌎[^\n]*\n", short, re.M)

    if match:
        rest = short[match.end():]

        rest = re.sub(
            r"\A\s*(?:\|[^\n]*(?:\n|$))+",
            "",
            rest,
        )

        return (
            short[:match.end()]
            + "\n"
            + table_md
            + "\n\n"
            + rest.lstrip("\n")
        )

    return (
        short
        + "\n\n### 🌎 지수 한눈에\n\n"
        + table_md
    )


def choose_news_count(selected, today, index_table):
    """모든 파트가 토큰 예산 안에 들어가는 가장 큰 뉴스 개수를 찾습니다."""

    for count in range(len(selected), -1, -1):
        news_text = news_to_text(selected[:count])

        fits = True

        for part in DETAIL_PARTS:
            prompt = build_part_prompt(
                part,
                today,
                index_table,
                news_text,
            )

            if (
                estimate_tokens(prompt) + part["max_tokens"]
                > GROQ_TPM_SAFE_BUDGET
            ):
                fits = False
                break

        if fits:
            return count

    raise RuntimeError(
        "뉴스를 모두 줄여도 API 요청량이 안전 범위를 초과합니다. "
        "프롬프트 자체의 분량을 줄여야 합니다."
    )


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
        "invalid_key": "Groq API 인증 오류입니다. API 키를 확인해 주세요.",
        "permission": "Groq API 권한 오류입니다. 모델 접근 권한을 확인해 주세요.",
        "model": "요청한 Groq 모델을 찾을 수 없습니다. 모델명을 확인해 주세요.",
        "tpm": "분당 토큰 제한(TPM)을 초과했습니다. 잠시 후 다시 시도해 주세요.",
        "request_too_large": "요청 크기를 초과했습니다. 프롬프트 분량을 줄여 주세요.",
        "rpm": "분당 요청 횟수 제한(RPM)에 도달했습니다.",
        "daily": "일일 API 사용량 제한에 도달했을 가능성이 있습니다.",
        "quota": "API 사용량 또는 할당량 제한을 확인해 주세요.",
        "rate_limit_unknown": "HTTP 429 제한 오류입니다. 제한 헤더를 확인해 주세요.",
        "server": "Groq 서버 오류입니다. 잠시 후 다시 시도해 주세요.",
        "other": "Groq API 요청 중 오류가 발생했습니다.",
    }

    return messages.get(error_type, messages["other"])


def groq_error_message(error_info):
    return (
        friendly_groq_error(error_info)
        + "\n\n"
        + f"HTTP 상태: {error_info['http_status']}"
        + "\n"
        + f"오류 코드: {error_info['error_code'] or '없음'}"
        + "\n"
        + f"오류 유형: {error_info['error_type'] or '없음'}"
        + "\n"
        + "오류 원문: "
        + (
            error_info["error_message"]
            or error_info["raw_response"]
        )
    )


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
        "reasoning_tokens": None,
        "finish_reason": None,
        "request_id": None,
        "rate_limits": {},
        "retries": 0,
        "note": "",
        "finished_at": None,
        "error": None,
        "called_at": datetime.datetime.now(KST).strftime(
            "%Y-%m-%d %H:%M:%S"
        ),
    }

    state = st.session_state

    state.setdefault("pm_api_diagnostics", []).append(diagnostic)
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

    if GROQ_REASONING_EFFORT:
        payload["reasoning_effort"] = GROQ_REASONING_EFFORT
        payload["include_reasoning"] = False

    try:
        response = None

        for attempt in range(3):
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

            diagnostic["finished_at"] = time.time()

            if response.status_code == 200:
                break

            error_info = parse_groq_error(response)

            # 추론 옵션을 지원하지 않는 모델이면 옵션을 빼고 재시도
            if (
                response.status_code == 400
                and "reasoning_effort" in payload
                and "reasoning" in (
                    error_info["error_message"]
                    + error_info["raw_response"]
                ).lower()
            ):
                payload.pop("reasoning_effort", None)
                payload.pop("include_reasoning", None)

                diagnostic["retries"] += 1
                diagnostic["note"] = (
                    "추론 옵션이 지원되지 않아 제외하고 재시도"
                )
                continue

            # 분당 토큰 제한: 안내된 시간만큼 기다린 뒤 재시도
            if (
                response.status_code == 429
                and classify_groq_error(
                    response.status_code,
                    error_info["error_code"],
                    error_info["error_message"],
                ) == "tpm"
                and attempt < 2
            ):
                wait = parse_retry_seconds(
                    response,
                    error_info["error_message"],
                )

                wait = min(
                    (wait if wait is not None else 20) + 2,
                    90,
                )

                diagnostic["retries"] += 1

                sleep_with_countdown(
                    wait,
                    f"{stage} 토큰 제한 대기 후 재시도",
                )
                continue

            diagnostic["error"] = error_info
            raise RuntimeError(groq_error_message(error_info))

        if response is None or response.status_code != 200:
            error_info = parse_groq_error(response)
            diagnostic["error"] = error_info
            raise RuntimeError(groq_error_message(error_info))

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

        # 잘린 응답이라도 처리된 토큰은 하루 한도에서 차감되므로 기록
        add_usage(usage.get("total_tokens"))
        render_token_status()

        details = usage.get("completion_tokens_details") or {}

        if isinstance(details, dict):
            diagnostic["reasoning_tokens"] = details.get(
                "reasoning_tokens"
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

        # 잘린 응답은 완성된 결과로 취급하지 않음 (내용 유무와 무관)
        if diagnostic["finish_reason"] == "length":
            reasoning = diagnostic["reasoning_tokens"]

            diagnostic["error"] = {
                "message": (
                    "출력 한도에 도달해 응답이 잘렸습니다. "
                    f"추론 토큰: {reasoning if reasoning is not None else '미확인'}"
                )
            }

            raise RuntimeError(
                f"{stage}: 출력 한도({safe_max_tokens:,})에 도달해 "
                "응답이 중간에 종료됐습니다. "
                f"추론 토큰: {reasoning if reasoning is not None else '미확인'}. "
                "이 파트는 저장하지 않았습니다."
            )

        if not result:
            raise RuntimeError("Groq 응답이 비어 있습니다.")

        return result

    except requests.exceptions.Timeout:
        diagnostic["error"] = {
            "message": "API 요청 시간 초과",
        }

        raise RuntimeError(
            f"{stage}: Groq API 응답 시간이 초과되었습니다."
        )

    except requests.exceptions.RequestException as error:
        diagnostic["error"] = {
            "message": str(error)[:1000],
        }

        raise RuntimeError(
            f"{stage}: Groq 네트워크 요청 실패: {str(error)[:300]}"
        )

    except Exception as error:
        if diagnostic.get("error") is None:
            diagnostic["error"] = {
                "message": str(error)[:2000],
            }

        raise

    finally:
        state["pm_api_diagnostic"] = diagnostic


# ===============================================================
# 14. 상세 리포트 파트별 생성, 저장, 조립, 압축
# ===============================================================

def make_request_preview(
    stage,
    prompt,
    max_tokens,
    extra=None,
):
    result = {
        "stage": stage,
        "prompt_characters": len(prompt),
        "estimated_input_tokens": estimate_tokens(prompt),
        "max_output_tokens": calculate_safe_output_tokens(
            prompt,
            max_tokens,
        ),
    }

    result["estimated_total_tokens"] = (
        result["estimated_input_tokens"]
        + result["max_output_tokens"]
    )

    if extra:
        result.update(extra)

    return result


def news_table_markdown(items, sent_links=None):
    """뉴스 목록을 표로 만듭니다. sent_links가 있으면 AI 전달 번호 열을 추가합니다."""

    if not items:
        return "표시할 뉴스가 없습니다."

    sent_numbers = {}

    if sent_links is not None:
        sent_numbers = {
            link: number
            for number, link in enumerate(sent_links, start=1)
        }

    if sent_links is None:
        lines = [
            "| 번호 | 분류 | 언론사 | 기사 제목 | 게시 시각 | 링크 |",
            "|---|---|---|---|---|---|",
        ]
    else:
        lines = [
            "| AI 전달 | 분류 | 언론사 | 기사 제목 | 게시 시각 | 링크 |",
            "|---|---|---|---|---|---|",
        ]

    for index, item in enumerate(items, start=1):
        title = item["title"].replace("|", "/")
        publisher = item["publisher"].replace("|", "/")

        if sent_links is None:
            first = str(index)
        else:
            number = sent_numbers.get(item["link"])
            first = f"[{number}]" if number else "-"

        lines.append(
            f"| {first} | {item['category']} | {publisher} | "
            f"{title} | {item['published']} | "
            f"[링크]({item['link']}) |"
        )

    return "\n".join(lines)


def cited_numbers(text, limit):
    return sorted({
        int(number)
        for number in re.findall(r"\[(\d{1,3})\]", text)
        if 1 <= int(number) <= limit
    })


def insert_overseas_table(text, table_md):
    """AI가 쓴 해외 증시 파트의 제목 바로 아래에 지수 표를 삽입합니다."""

    match = re.search(r"^###\s*1\.\s*🌎[^\n]*\n", text, re.M)

    if match:
        position = match.end()

        return (
            text[:position]
            + "\n"
            + table_md
            + "\n"
            + text[position:]
        )

    return (
        "### 1. 🌎 해외 증시\n\n"
        + table_md
        + "\n\n"
        + text
    )


def build_sources_markdown(news, body_text):
    heading = "### 12. 📎 출처"

    if not news:
        return (
            f"{heading}\n\n"
            "수집된 뉴스가 없어 출처를 표시할 수 없습니다."
        )

    cited = cited_numbers(body_text, len(news))

    numbers = cited or list(range(1, len(news) + 1))

    lines = [
        heading,
        "",
        (
            "본문에서 인용된 기사만 표시합니다."
            if cited
            else "본문에 인용 번호가 없어 전달된 기사 전체를 표시합니다."
        ),
        "",
        "| 번호 | 언론사 | 기사 제목 | 링크 |",
        "|---|---|---|---|",
    ]

    for number in numbers:
        item = news[number - 1]

        title = item["title"].replace("|", "/")
        publisher = item["publisher"].replace("|", "/")

        lines.append(
            f"| {number} | {publisher} | {title} | "
            f"[링크]({item['link']}) |"
        )

    return "\n".join(lines)


def compose_report(
    keys,
    ctx,
    parts,
    include_sources=True,
    include_table=True,
):
    keys = set(keys)

    blocks = [
        f"## 📅 {ctx['today']} | MTNW Market Intelligence [장전]"
    ]

    for part in DETAIL_PARTS:
        key = part["key"]

        if key not in keys or key not in parts:
            continue

        text = re.sub(
            r"^## 📅.*\n?",
            "",
            parts[key],
            flags=re.M,
        ).strip()

        if key == "overseas" and include_table:
            text = insert_overseas_table(text, ctx["table_md"])

        blocks.append(text)

    body = "\n\n".join(blocks)

    if include_sources:
        body += "\n\n" + build_sources_markdown(ctx["news"], body)

    return body


def rebuild_long_report():
    """완성된 파트만 모아 상세 리포트를 갱신합니다."""

    state = st.session_state

    ctx = state.get("pm_ctx")
    parts = state.get("pm_detail_parts") or {}

    if not ctx or not parts:
        return

    state["pm_long"] = compose_report(
        DETAIL_PART_KEYS,
        ctx,
        parts,
    )


def build_compress_source(ctx, parts):
    """토큰 예산 안에서 압축 입력을 구성합니다. 반환: (프롬프트, 포함 파트)"""

    keys = [
        key
        for key in COMPRESS_REQUIRED + COMPRESS_OPTIONAL
        if key in parts
    ]

    while True:
        source = compose_report(
            keys,
            ctx,
            parts,
            include_sources=False,
            include_table=False,
        )

        numbers = cited_numbers(source, len(ctx["news"]))

        publishers = sorted({
            ctx["news"][number - 1]["publisher"]
            for number in numbers
        })

        source += (
            "\n\n출처 언론사: "
            + (", ".join(publishers) if publishers else "확인 불가")
        )

        if "keywords" not in keys:
            source += (
                "\n\n※ 키워드 파트는 입력 한도로 제외됨. "
                "핵심 키워드 10은 위 이슈·종목에 나온 단어형 키워드만 사용하고, "
                "부족하면 확인 불가로 표시할 것."
            )

        if "schedule" not in keys:
            source += (
                "\n\n※ 일정 파트는 입력 한도로 제외됨. "
                "일정은 확인 불가로 표시할 것."
            )

        prompt = build_compress_prompt(source, system_index=True)

        if (
            estimate_tokens(prompt) + GROQ_COMPRESS_MAX_TOKENS
            <= GROQ_TPM_SAFE_BUDGET
        ):
            return prompt, keys

        optional_in = [
            key for key in keys if key in COMPRESS_OPTIONAL
        ]

        if not optional_in:
            raise RuntimeError(
                "필수 파트만으로도 압축 입력이 토큰 예산을 초과합니다. "
                "상세 파트의 출력 한도를 낮춰 주세요."
            )

        keys.remove(optional_in[-1])


def run_one_part(part, number, total, ctx, status):
    state = st.session_state

    news_text = news_to_text(ctx["news"])

    prompt = build_part_prompt(
        part,
        ctx["today"],
        ctx["index_table"],
        news_text,
    )

    stage = f"상세 {number}/{total} · {part['name']}"

    preview = make_request_preview(
        stage,
        prompt,
        part["max_tokens"],
    )

    state.setdefault("pm_part_previews", []).append(preview)

    diagnostics = state.get("pm_api_diagnostics") or []
    previous = diagnostics[-1] if diagnostics else None

    wait_before_api_call(
        previous,
        prompt,
        preview["max_output_tokens"],
        stage,
    )

    status.write(f"⏳ {stage} 생성 중...")

    result = call_groq(
        prompt,
        max_tokens=part["max_tokens"],
        stage=stage,
    )

    # 정상 완료된 파트만 즉시 저장
    state["pm_detail_parts"][part["key"]] = result

    rebuild_long_report()

    status.write(f"✅ {stage} 완료 · 저장됨")


def run_pipeline(mode, status):
    state = st.session_state
    now = datetime.datetime.now(KST)

    # -----------------------------------------------------------
    # A. 붙여넣은 원문 압축
    # -----------------------------------------------------------

    if mode == "paste":
        pasted = state.get("pm_pasted", "").strip()

        if not pasted:
            raise RuntimeError("압축할 원문을 먼저 붙여넣어 주세요.")

        state["pm_api_diagnostics"] = []
        state["pm_api_diagnostic"] = None
        state["pm_compress_preview"] = None

        prompt = build_compress_prompt(pasted)

        state["pm_compress_preview"] = make_request_preview(
            "원문 압축",
            prompt,
            GROQ_COMPRESS_MAX_TOKENS,
        )

        status.write("⏳ 붙여넣은 원문을 압축하는 중...")

        state["pm_short"] = call_groq(
            prompt,
            max_tokens=GROQ_COMPRESS_MAX_TOKENS,
            stage="원문 압축",
        )

        state["pm_note"] = (
            f"압축 완료 · {datetime.datetime.now(KST).strftime('%H:%M:%S')} · "
            "Groq API 1회 호출"
        )
        state["pm_note_level"] = "ok"
        return

    # -----------------------------------------------------------
    # B. 전체 생성 또는 이어서 생성
    # -----------------------------------------------------------

    if mode == "full":
        state["pm_api_diagnostics"] = []
        state["pm_api_diagnostic"] = None
        state["pm_request_preview"] = None
        state["pm_compress_preview"] = None
        state["pm_part_previews"] = []
        state["pm_detail_parts"] = {}
        state["pm_long"] = ""
        state["pm_short"] = ""
        state["pm_ctx"] = None
        state["pm_news_all"] = []

        today = (
            f"{now.year}년 {now.month:02d}월 "
            f"{now.day:02d}일 ({WEEKDAYS[now.weekday()]})"
        )

        # 지수 데이터 확보
        status.write("📊 Toss 지수 데이터 확인 중...")

        rows = state.get("pm_indices")

        if not rows:
            infos = get_toss_indicators()
            rows = build_toss_rows(infos)

            state["pm_indices"] = rows
            state["pm_main_indices"] = build_main_index_rows(rows)

        # 화면 ①에 표시되는 지수만, 같은 순서로 사용
        index_table = indices_markdown(rows)

        # 뉴스 수집
        status.write("📰 최근 24시간 뉴스 RSS 수집 중...")

        news, news_errors = collect_news()
        state["pm_news_all"] = news

        selected = select_news(news, MAX_NEWS_ITEMS)

        count = choose_news_count(selected, today, index_table)

        used_news = selected[:count]

        state["pm_ctx"] = {
            "today": today,
            "index_table": index_table,
            "table_md": index_table,
            "index_rows": rows,
            "news": used_news,
            "news_total": len(news),
            "news_errors": len(news_errors),
        }

        sample_prompt = build_part_prompt(
            DETAIL_PARTS[0],
            today,
            index_table,
            news_to_text(used_news),
        )

        state["pm_request_preview"] = {
            "news_count": len(news),
            "news_sent": len(used_news),
            "prompt_characters": len(sample_prompt),
            "estimated_input_tokens": estimate_tokens(sample_prompt),
            "max_output_tokens": DETAIL_PARTS[0]["max_tokens"],
            "estimated_total_tokens": (
                estimate_tokens(sample_prompt)
                + DETAIL_PARTS[0]["max_tokens"]
            ),
        }

        status.write(
            f"✅ 뉴스 {len(news)}건 수집 · {len(used_news)}건 사용"
        )

    else:  # resume
        if not state.get("pm_ctx"):
            raise RuntimeError(
                "이어서 생성할 이전 작업이 없습니다. "
                "'장전 시황 생성'으로 처음부터 실행해 주세요."
            )

        state.setdefault("pm_detail_parts", {})
        state.setdefault("pm_part_previews", [])

        status.write(
            f"▶ 이어서 생성: 완료된 파트 "
            f"{len(state['pm_detail_parts'])}/{len(DETAIL_PARTS)}"
        )

    ctx = state["pm_ctx"]

    # -----------------------------------------------------------
    # C. 상세 리포트 파트별 생성 (완료된 파트는 건너뜀)
    # -----------------------------------------------------------

    total = len(DETAIL_PARTS)

    for number, part in enumerate(DETAIL_PARTS, start=1):
        if part["key"] in state["pm_detail_parts"]:
            continue

        run_one_part(part, number, total, ctx, status)

    parts = state["pm_detail_parts"]

    if len(parts) < total:
        raise RuntimeError(
            "상세 리포트 파트가 모두 생성되지 않아 압축을 실행하지 않았습니다."
        )

    rebuild_long_report()

    # -----------------------------------------------------------
    # D. 압축본 생성
    # -----------------------------------------------------------

    compress_prompt, included = build_compress_source(ctx, parts)

    compress_preview = make_request_preview(
        "통합 상세 리포트 압축",
        compress_prompt,
        GROQ_COMPRESS_MAX_TOKENS,
        extra={"included_parts": ", ".join(included)},
    )

    state["pm_compress_preview"] = compress_preview

    diagnostics = state.get("pm_api_diagnostics") or []
    previous = diagnostics[-1] if diagnostics else None

    wait_before_api_call(
        previous,
        compress_prompt,
        compress_preview["max_output_tokens"],
        "통합 상세 리포트 압축",
    )

    status.write("⏳ 통합 상세 리포트를 압축하는 중...")

    short = call_groq(
        compress_prompt,
        max_tokens=GROQ_COMPRESS_MAX_TOKENS,
        stage="통합 상세 리포트 압축",
    )

    index_rows = (
        ctx.get("index_rows")
        or state.get("pm_indices")
        or []
    )

    state["pm_short"] = insert_compact_index_table(
        short,
        compact_index_table(index_rows),
    )

    dropped = [
        key for key in COMPRESS_OPTIONAL if key not in included
    ]

    dropped_note = (
        f" · 입력 한도로 압축에서 제외: {', '.join(dropped)}"
        if dropped
        else ""
    )

    error_note = (
        f" · RSS 일부 수집 실패 {ctx['news_errors']}건"
        if ctx.get("news_errors")
        else ""
    )

    state["pm_note"] = (
        f"생성 완료 · {datetime.datetime.now(KST).strftime('%H:%M:%S')} · "
        f"뉴스 {ctx['news_total']}건 수집 / {len(ctx['news'])}건 사용 · "
        f"Groq API {len(state.get('pm_api_diagnostics') or [])}회 호출"
        f"{dropped_note}{error_note}"
    )
    state["pm_note_level"] = "ok"


def run_with_status(mode):
    state = st.session_state

    labels = {
        "full": "장전 시황 생성 중... (대기 포함 약 4~7분)",
        "resume": "이어서 생성 중...",
        "paste": "원문 압축 중...",
    }

    with st.status(
        labels.get(mode, "생성 중..."),
        expanded=True,
    ) as status:
        try:
            run_pipeline(mode, status)

            status.update(
                label="완료",
                state="complete",
                expanded=False,
            )

        except Exception as error:
            rebuild_long_report()

            done = len(state.get("pm_detail_parts") or {})

            if done:
                state["pm_note"] = (
                    f"중단됨: {error}\n\n"
                    f"정상 완료된 상세 파트 {done}/{len(DETAIL_PARTS)}개는 "
                    "저장되어 있습니다. '이어서 생성'으로 "
                    "실패한 단계부터 계속할 수 있습니다."
                )
            else:
                state["pm_note"] = f"생성 실패: {error}"

            state["pm_note_level"] = "error"

            status.update(
                label="오류로 중단됨 (완료된 파트는 보존)",
                state="error",
                expanded=True,
            )


def request_run(mode):
    st.session_state["pm_run_mode"] = mode


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
        part_previews = state.get("pm_part_previews", [])
        diagnostics = state.get("pm_api_diagnostics", [])

        if preview:
            st.markdown("#### 뉴스 수집 및 첫 파트 프롬프트")

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
                "첫 파트 프롬프트 글자 수",
                f"{preview.get('prompt_characters', 0):,}",
            )

            st.caption(
                "첫 파트 예상 입력 토큰: "
                f"{preview.get('estimated_input_tokens', 0):,}개"
            )

            all_news = state.get("pm_news_all") or []
            ctx = state.get("pm_ctx") or {}
            sent_news = ctx.get("news") or []

            with st.expander(
                f"📰 수집된 뉴스 전체 {len(all_news)}건 보기 (출처·링크)",
                expanded=False,
            ):
                st.caption(
                    "Google News RSS에서 수집한 최근 24시간 기사입니다. "
                    "'AI 전달' 열의 [번호]는 상세 리포트 본문의 인용 번호와 같습니다."
                )

                st.markdown(
                    news_table_markdown(
                        all_news,
                        sent_links=[item["link"] for item in sent_news],
                    )
                )

            with st.expander(
                f"🤖 AI에 실제 전달된 뉴스 {len(sent_news)}건 보기",
                expanded=False,
            ):
                st.markdown(news_table_markdown(sent_news))

        if part_previews:
            st.divider()
            st.markdown("#### 상세 리포트 파트별 요청 정보")

            for index, item in enumerate(part_previews, start=1):
                with st.expander(
                    f"{index}차 요청 · {item.get('stage', '')}",
                    expanded=False,
                ):
                    col1, col2, col3 = st.columns(3)

                    col1.metric(
                        "프롬프트 글자 수",
                        f"{item.get('prompt_characters', 0):,}",
                    )

                    col2.metric(
                        "예상 입력 토큰",
                        f"{item.get('estimated_input_tokens', 0):,}",
                    )

                    col3.metric(
                        "최대 출력 토큰",
                        f"{item.get('max_output_tokens', 0):,}",
                    )

                    st.caption(
                        "예상 요청 토큰 합계: "
                        f"{item.get('estimated_total_tokens', 0):,}개"
                    )

        if compress_preview:
            st.divider()
            st.markdown("#### 압축본 요청 정보")

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
                "예상 요청 토큰 합계: "
                f"{compress_preview.get('estimated_total_tokens', 0):,}개"
            )

            if compress_preview.get("included_parts"):
                st.caption(
                    "압축 입력에 포함된 파트: "
                    f"{compress_preview['included_parts']}"
                )

        if diagnostics:
            st.divider()
            st.markdown("#### API 호출별 응답 정보")

            for index, diagnostic in enumerate(
                diagnostics,
                start=1,
            ):
                stage = diagnostic.get(
                    "stage",
                    f"{index}차 호출",
                )

                with st.expander(
                    f"{index}차 호출 · {stage}",
                    expanded=(index == len(diagnostics)),
                ):
                    col1, col2, col3, col4 = st.columns(4)

                    prompt_tokens = diagnostic.get(
                        "actual_prompt_tokens"
                    )

                    completion_tokens = diagnostic.get(
                        "actual_completion_tokens"
                    )

                    reasoning_tokens = diagnostic.get(
                        "reasoning_tokens"
                    )

                    total_tokens = diagnostic.get(
                        "actual_total_tokens"
                    )

                    col1.metric(
                        "실제 입력 토큰",
                        (
                            f"{prompt_tokens:,}"
                            if isinstance(prompt_tokens, (int, float))
                            else "미확인"
                        ),
                    )

                    col2.metric(
                        "실제 출력 토큰",
                        (
                            f"{completion_tokens:,}"
                            if isinstance(completion_tokens, (int, float))
                            else "미확인"
                        ),
                    )

                    col3.metric(
                        "추론 토큰(출력에 포함)",
                        (
                            f"{reasoning_tokens:,}"
                            if isinstance(reasoning_tokens, (int, float))
                            else "미확인"
                        ),
                    )

                    col4.metric(
                        "전체 사용 토큰",
                        (
                            f"{total_tokens:,}"
                            if isinstance(total_tokens, (int, float))
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
                        "재시도 횟수:",
                        diagnostic.get("retries", 0),
                    )

                    if diagnostic.get("note"):
                        st.write("비고:", diagnostic["note"])

                    st.write(
                        "프롬프트 글자 수:",
                        f"{diagnostic.get('input_characters', 0):,}",
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

                        st.error(
                            "해당 API 호출에 오류가 발생했습니다."
                        )

                        st.json(
                            diagnostic["error"]
                        )

        if not preview and not compress_preview and not diagnostics:
            st.caption("아직 API 실행 기록이 없습니다.")


# ===============================================================
# 17. 화면
# ===============================================================

def main():
    state = st.session_state

    st.title("MTNW 장전 시황")

    # 맨 위 토큰 현황 (생성 중·생성 후에도 같은 자리에서 갱신됨)
    TOKEN_BOX["box"] = st.empty()
    render_token_status()

    st.caption(
        "Toss 지수와 최근 24시간 뉴스 RSS를 수집한 뒤, Groq API로 "
        "상세 리포트를 8개 파트로 나누어 생성하고, 마지막에 통합 압축본을 만듭니다. "
        "무료 TPM 한도 때문에 중간에 대기가 있어 4~7분 걸릴 수 있습니다. "
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

        done_before = len(state.get("pm_detail_parts") or {})

        can_resume = bool(state.get("pm_ctx")) and (
            done_before < len(DETAIL_PARTS)
            or not str(state.get("pm_short", "")).strip()
        )

        col_a, col_b, col_c = st.columns(
            [1.8, 1.2, 1.6],
            vertical_alignment="center",
        )

        col_a.button(
            "✨ 장전 시황 생성 (뉴스 수집 + 압축)",
            type="primary",
            on_click=request_run,
            args=("full",),
            use_container_width=True,
        )

        col_b.button(
            "압축만 실행",
            on_click=request_run,
            args=("paste",),
            use_container_width=True,
            help="붙여넣은 원문만 압축합니다.",
        )

        col_c.button(
            "▶ 실패한 단계부터 이어서",
            on_click=request_run,
            args=("resume",),
            use_container_width=True,
            disabled=not can_resume,
            help="이미 정상 생성된 파트는 건너뛰고 나머지만 생성합니다.",
        )

        mode = state.pop("pm_run_mode", None)

        if mode:
            run_with_status(mode)

        done = len(state.get("pm_detail_parts") or {})

        if state.get("pm_ctx"):
            st.caption(
                f"상세 리포트 파트 {done}/{len(DETAIL_PARTS)} 완료 "
                "(완료된 파트는 실패해도 보존됩니다)"
            )

        if state.get("pm_note"):
            if state.get("pm_note_level") == "error":
                st.error(state["pm_note"])
            else:
                st.success(state["pm_note"])

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

    render_token_status()


main()
