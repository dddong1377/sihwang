import datetime
import email.utils
import hmac
import html
import json
import os
import re
import time
import xml.etree.ElementTree as ET
from urllib.parse import quote_plus
from zoneinfo import ZoneInfo

import requests
import streamlit as st
import streamlit.components.v1 as components


st.set_page_config(page_title="MTNW 장전 시황", layout="wide")

KST = ZoneInfo("Asia/Seoul")
WEEKDAYS = "월화수목금토일"

# ---------------------------------------------------------------
# Groq 무료 API 설정
# ---------------------------------------------------------------

GROQ_MODEL = "openai/gpt-oss-120b"
GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"

GROQ_MAX_TOKENS = 6000
GROQ_TIMEOUT = 180


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


# ---------------------------------------------------------------
# 무료 뉴스 RSS 설정
# ---------------------------------------------------------------

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

        if not title or not link:
            continue

        if published is None:
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


def news_to_text(news):
    if not news:
        return (
            "최근 24시간 이내 수집된 뉴스가 없습니다. "
            "뉴스 관련 사실은 임의로 작성하지 말고 확인 불가로 표시하세요."
        )

    lines = []

    for index, item in enumerate(news[:60], start=1):
        lines.append(
            f"[{index}] [{item['category']}] "
            f"{item['title']}\n"
            f"언론사: {item['publisher']}\n"
            f"게시 시각: {item['published']}\n"
            f"링크: {item['link']}"
        )

    return "\n\n".join(lines)


# ---------------------------------------------------------------
# Toss 대시보드 지수 API 설정
# ---------------------------------------------------------------

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


# ---------------------------------------------------------------
# 표시할 지수 목록
# ---------------------------------------------------------------

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


# ---------------------------------------------------------------
# 공통: 설정값 / 로그인
# ---------------------------------------------------------------

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


# ---------------------------------------------------------------
# Toss API 요청 헤더
# ---------------------------------------------------------------

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


# ---------------------------------------------------------------
# Toss 지수 API 조회
# ---------------------------------------------------------------

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


# ---------------------------------------------------------------
# 데이터 변환
# ---------------------------------------------------------------

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


# ---------------------------------------------------------------
# 실제 캔들 시간 추출
# ---------------------------------------------------------------

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


# ---------------------------------------------------------------
# 선물 지수 필터
# ---------------------------------------------------------------

def is_futures_index(info):
    code = str(info.get("code") or "").strip()
    name = get_info_name(info).lower()

    if code in EXCLUDED_FUTURES_CODES:
        return True

    if code.upper().startswith("RFU."):
        return True

    futures_keywords = ("선물", "futures", "future")

    return any(keyword in name for keyword in futures_keywords)


# ---------------------------------------------------------------
# Toss 데이터 행 구성
# ---------------------------------------------------------------

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


# ---------------------------------------------------------------
# 해외 주요 지수 표 구성
# ---------------------------------------------------------------

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


# ---------------------------------------------------------------
# 프롬프트
# ---------------------------------------------------------------

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
- 전체 분량: 모바일 스크롤 기준 3~4화면 내외
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

=========================================================
# 추가 출력 지시: 상세 리포트와 압축본을 한 번에 작성
=========================================================

중요: API 호출을 한 번만 사용해야 한다.
위의 상세 리포트를 먼저 완성한 다음, 그 리포트 내용만 활용해 압축본도 작성한다.
상세 리포트와 압축본을 모두 하나의 응답에 출력한다.
두 결과 사이에는 반드시 아래 구분자를 정확히 넣는다.

<<<MTNW_DETAIL_START>>>

(위 출력 형식에 맞춘 상세 리포트 전체)

<<<MTNW_DETAIL_END>>>

<<<MTNW_SHORT_START>>>

(아래 압축 규칙에 맞춘 압축 시황글)

<<<MTNW_SHORT_END>>>

구분자 바깥에는 설명이나 인사말을 쓰지 않는다.
상세 리포트와 압축본의 내용이 서로 모순되지 않도록 한다.

# 압축본 작성 규칙
- 원문 상세 리포트에 있는 사실만 사용한다.
- 새로운 수치·종목·뉴스를 추가하지 않는다.
- "확인 불가", "추정", "후보"는 단정하지 않는다.
- 모바일 1화면 반 이내, 상세 리포트의 약 20~25% 분량
- 문장은 한 줄 40자 내외
- 핵심 포인트마다 관련 종목을 붙인다.
- 핵심 키워드·수치·종목명은 **굵게**
- 상승·호재는 🔺, 하락·악재는 🔻, 중립·관망은 ➖
- 가장 중요한 이슈 1개에는 🔥 표시
- 표는 지수표 1개만 허용한다.

## 압축본 출력 형식

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
외국인 / 기관 / 개인
원문에 없으면 "확인 불가"

### 📆 오늘·이번 주 일정
최대 5개

### 🔍 핵심 키워드 10
원문에 등장한 단어형 키워드

### ⚠️ 한 줄 주의
오늘 가장 큰 리스크 1문장
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


# ---------------------------------------------------------------
# Groq 오류 구분
# ---------------------------------------------------------------

def classify_groq_error(error):
    message = str(error).lower()

    if (
        "invalid_api_key" in message
        or "invalid api key" in message
        or "authentication" in message
        or "401" in message
    ):
        return "invalid_key"

    if (
        "429" in message
        or "rate_limit" in message
        or "rate limit" in message
        or "quota" in message
        or "tokens per minute" in message
    ):
        return "quota"

    if "model_decommissioned" in message:
        return "model"

    if "model_not_found" in message or "404" in message:
        return "model"

    if "403" in message or "permission" in message:
        return "permission"

    return "other"


def friendly_groq_error(error):
    error_type = classify_groq_error(error)

    if error_type == "invalid_key":
        return (
            "Groq API 키가 유효하지 않습니다. "
            "Groq Console에서 API 키를 확인해 주세요."
        )

    if error_type == "quota":
        return (
            "Groq 무료 API 사용량 한도에 도달했습니다. "
            "분당 또는 일일 사용량 초기화 후 다시 시도해 주세요. "
            "유료 API로 자동 전환하지 않습니다."
        )

    if error_type == "model":
        return (
            "Groq 모델을 찾을 수 없거나 사용이 중단되었습니다. "
            "Groq 공식 모델 목록에서 현재 사용 가능한 무료 모델을 확인해 주세요."
        )

    if error_type == "permission":
        return (
            "Groq API 권한 오류입니다. "
            "API 키와 모델 접근 권한을 확인해 주세요."
        )

    return f"Groq API 호출 실패: {str(error)[:500]}"


# ---------------------------------------------------------------
# Groq 호출
# ---------------------------------------------------------------

def call_groq(prompt, max_tokens=4000):
    headers = get_groq_headers()

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
        "max_tokens": max_tokens,
    }

    try:
        response = requests.post(
            GROQ_API_URL,
            headers=headers,
            json=payload,
            timeout=GROQ_TIMEOUT,
        )

        if response.status_code != 200:
            raise RuntimeError(
                f"HTTP {response.status_code}: "
                f"{response.text[:1000]}"
            )

        data = response.json()

        choices = data.get("choices") or []

        if not choices:
            raise RuntimeError(
                "Groq 응답에 choices 항목이 없습니다."
            )

        message = choices[0].get("message") or {}
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
                "Groq 응답이 비어 있습니다. "
                "사용량 또는 모델 응답을 확인해 주세요."
            )

        return result

    except requests.exceptions.Timeout:
        raise RuntimeError(
            "Groq API 응답 시간이 초과되었습니다. "
            "잠시 후 다시 시도해 주세요."
        )

    except requests.exceptions.RequestException as error:
        raise RuntimeError(
            f"Groq 네트워크 요청 실패: {str(error)[:300]}"
        )

    except Exception as error:
        raise RuntimeError(friendly_groq_error(error))


# ---------------------------------------------------------------
# 상세 리포트 / 압축본 구분
# ---------------------------------------------------------------

def extract_dual_output(raw_text):
    detail_start = "<<<MTNW_DETAIL_START>>>"
    detail_end = "<<<MTNW_DETAIL_END>>>"
    short_start = "<<<MTNW_SHORT_START>>>"
    short_end = "<<<MTNW_SHORT_END>>>"

    if not all(
        marker in raw_text
        for marker in (
            detail_start,
            detail_end,
            short_start,
            short_end,
        )
    ):
        return None, None

    detail = raw_text.split(detail_start, 1)[1]
    detail = detail.split(detail_end, 1)[0].strip()

    short = raw_text.split(short_start, 1)[1]
    short = short.split(short_end, 1)[0].strip()

    if not detail or not short:
        return None, None

    return detail, short


# ---------------------------------------------------------------
# 장전 시황 생성 및 압축
# ---------------------------------------------------------------

def run_premarket(use_source_only=False):
    state = st.session_state
    now = datetime.datetime.now(KST)

    today = (
        f"{now.year}년 {now.month:02d}월 "
        f"{now.day:02d}일 ({WEEKDAYS[now.weekday()]})"
    )

    pasted = state.get("pm_pasted", "").strip()

    try:
        # -------------------------------------------------------
        # 직접 붙여넣은 원문: 압축만 실행
        # -------------------------------------------------------

        if use_source_only:
            if not pasted:
                state["pm_note"] = (
                    "압축할 원문을 먼저 붙여넣어 주세요."
                )
                return

            with st.spinner("Groq 무료 API로 원문을 압축하는 중..."):
                short = call_groq(
                    COMPRESS_PROMPT.replace(
                        "__SOURCE__",
                        pasted,
                    ),
                    max_tokens=2500,
                )

            state["pm_short"] = short

            state["pm_note"] = (
                f"압축 완료 · "
                f"{datetime.datetime.now(KST).strftime('%H:%M:%S')} · "
                "Groq API 1회 호출"
            )

            return

        # -------------------------------------------------------
        # Toss 지수 데이터 확보
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
        # 최근 24시간 뉴스 RSS 수집
        # -------------------------------------------------------

        with st.spinner("최근 24시간 국내외 뉴스 RSS 수집 중..."):
            news, news_errors = collect_news()

        news_context = news_to_text(news)

        prompt = (
            PREMARKET_PROMPT
            .replace("__TODAY__", today)
            .replace("__INDEX_TABLE__", index_table)
            .replace("__NEWS_CONTEXT__", news_context)
        )

        with st.spinner(
            "Groq 무료 API가 상세 리포트와 압축본을 작성 중입니다..."
        ):
            raw_result = call_groq(
                prompt,
                max_tokens=GROQ_MAX_TOKENS,
            )

        detail, short = extract_dual_output(raw_result)

        # -------------------------------------------------------
        # 구분자 누락 등으로 응답 파싱에 실패한 경우
        # -------------------------------------------------------

        if detail is None or short is None:
            state["pm_long"] = raw_result

            state["pm_note"] = (
                "상세 리포트는 생성됐지만 압축본 구분에 실패했습니다. "
                "원본 상세 리포트 탭에서 결과를 확인해 주세요. "
                "추가 API 호출은 실행하지 않았습니다."
            )

            return

        state["pm_long"] = detail
        state["pm_short"] = short

        error_note = ""

        if news_errors:
            error_note = (
                f" · RSS 일부 수집 실패 {len(news_errors)}건"
            )

        state["pm_note"] = (
            f"생성 완료 · "
            f"{datetime.datetime.now(KST).strftime('%H:%M:%S')} · "
            f"뉴스 {len(news)}건 수집 · "
            "Groq API 1회 호출"
            f"{error_note}"
        )

    except Exception as error:
        state["pm_note"] = f"생성 실패: {error}"


# ---------------------------------------------------------------
# 마크다운 → HTML / 복사 버튼
# ---------------------------------------------------------------

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


# ---------------------------------------------------------------
# 화면
# ---------------------------------------------------------------

def main():
    state = st.session_state

    st.title("MTNW 장전 시황")

    st.caption(
        "Toss 지수와 최근 24시간 뉴스 RSS를 수집한 뒤, "
        "Groq 무료 API로 장전 브리핑 원본과 압축본을 생성합니다. "
        "오후 시황은 왼쪽 메뉴의 'app'에서 작성합니다."
    )

    # 1) 해외 지수
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

    # 2) 생성
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
        )

        col_b.button(
            "압축만 실행",
            on_click=run_premarket,
            args=(True,),
            use_container_width=True,
            help="붙여넣은 원문만 압축합니다.",
        )

        if state.get("pm_note"):
            col_c.caption(state["pm_note"])

    # 3) 결과
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


main()
