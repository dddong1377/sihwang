
import datetime
import hmac
import html
import json
import os
import re
import time
from zoneinfo import ZoneInfo

import requests
import streamlit as st
import streamlit.components.v1 as components
from google import genai

st.set_page_config(page_title="MTNW 장전 시황", layout="wide")

KST = ZoneInfo("Asia/Seoul")
WEEKDAYS = "월화수목금토일"

# ---------------------------------------------------------------
# Gemini API 설정
# ---------------------------------------------------------------

# Gemini 2.5 Flash 사용
GEMINI_MODEL = "models/gemini-2.5-flash"

# 일시적인 요청 제한일 때만 재시도
GEMINI_RETRY_WAIT = 20


def get_gemini_api_key():
    api_key = ""

    try:
        api_key = st.secrets.get("GEMINI_API_KEY", "")
    except Exception:
        pass

    if not api_key:
        api_key = os.environ.get("GEMINI_API_KEY", "")

    return str(api_key).strip()


def get_gemini_client():
    api_key = get_gemini_api_key()

    if not api_key:
        raise RuntimeError(
            "Gemini API 키가 설정되지 않았습니다. "
            "Streamlit Secrets에 GEMINI_API_KEY를 등록해 주세요."
        )

    return genai.Client(api_key=api_key)


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
1. 오늘 날짜 기준 최근 24시간 이내 기사만 사용한다.
2. 아래 출처 중 최소 10개 이상을 교차 검증한다.
   - 국내: 연합뉴스, 한국경제, 매일경제, 머니투데이, 서울경제, 뉴스1, 이데일리, 아시아경제
   - 해외: Reuters, Bloomberg, CNBC, WSJ, FT, AP
   - 공시·지표: DART, 한국거래소, FOMC, CPI, PPI, PCE,
     미국 국채금리, 달러인덱스, 국제유가, VIX
3. 같은 뉴스는 하나로 통합한다. 기사 문장을 그대로 복사하지 않는다.
4. 확인되지 않은 루머, 증시와 무관한 정치 뉴스는 제외한다.
5. 확인 불가한 수치는 추정하지 말고 "확인 불가"로 표기한다.
6. 한국 개인투자자 관점에서 우선순위를 정한다.

# 지수 데이터 규칙
- 아래 [확인된 Toss 지수 데이터]를 기준 수치로 사용한다.
- Toss 데이터에 없는 지수는 검색으로 별도 확인한다.
- 검색으로도 확인되지 않으면 "확인 불가"로 표기한다.
- 기준 시각을 확인하고 최신 종가인지 실시간 지표인지 구분한다.
- 비고에 미확인 또는 데이터 없음으로 표시된 항목은 임의로 채우지 않는다.
- 휴장/미갱신 가능 지수는 검색으로 휴장 여부를 확인한다.
- 표기 형식: 지수명 : 지수값 (전일 대비 ±포인트, ±%)
- 대상: 다우, S&P500 현물, 나스닥 현물, FTSE100, CAC40, DAX, 유로스톡스50
- 선물 지수는 사용하지 않는다. S&P500은 반드시 현물 지수만 사용한다.

[확인된 Toss 지수 데이터]
__INDEX_TABLE__

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
(7개 지수, 휴장 사유 표기)

### 2. 🔥 핵심 이슈 TOP5
각 이슈는 아래 4줄:
- **제목** (시장 영향도 ⭐1~5 / 콘텐츠 점수 00/100)
- 무슨 일: 1문장
- 왜 중요한가 + 관련 종목·테마: 1문장
- 향후 일정/리스크: 1문장 [출처번호]

### 3. 🔍 검색 키워드
**실시간 TOP20**
| 순위 | 키워드 | 검색 의도 | 광고/SEO 활용 |

**급상승 TOP10**
| 순위 | 키워드 | 급상승 이유 |

### 4. 📈 종목·테마·수급
- **언급 TOP10 종목**: 종목 | 이유 | 방송 적합도
- **강세 테마 TOP5**: 테마 | 관련 종목 | 지속 가능성
- **돈 몰리는 업종 TOP5**: 업종 | 이유
- **수급**: 외국인 / 기관 / 개인 순매수·순매도 요약

### 5. 🌐 글로벌 이슈
미국·중국·일본·유럽·원자재·환율 관련 내용을 각 2~3문장.
금리, 달러인덱스, 유가, VIX 등 확인 가능한 수치 포함.

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
새로 등장 / 사라진 키워드, 강해진·약해진 테마

### 12. 📎 출처
| 번호 | 언론사 | 기사 제목 | 링크 |
(최소 10개, 24시간 이내)

# 최종 점검
- 지수 수치와 휴장 여부를 재확인했는가?
- 24시간 초과 기사나 미확인 루머가 없는가?
- 지정 개수 이내로 간결한가?
- 키워드가 전부 단어형인가?

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
오늘 가장 큰 리스크 1문장

# 원문 시황글
__SOURCE__
"""


# ---------------------------------------------------------------
# Gemini 응답 텍스트 추출
# ---------------------------------------------------------------

def extract_interaction_text(interaction):
    output_text = getattr(interaction, "output_text", None)

    if output_text:
        return str(output_text).strip()

    steps = getattr(interaction, "steps", None)

    if steps:
        texts = []

        for step in steps:
            step_text = getattr(step, "text", None)

            if step_text:
                texts.append(str(step_text))

            elif isinstance(step, dict):
                value = step.get("text")

                if value:
                    texts.append(str(value))

        if texts:
            return "\n".join(texts).strip()

    return ""


# ---------------------------------------------------------------
# Gemini 오류 구분
# ---------------------------------------------------------------

def classify_gemini_error(error):
    message = str(error)
    lower = message.lower()

    if (
        "API_KEY_INVALID" in message
        or "invalid api key" in lower
        or "api key not valid" in lower
    ):
        return "invalid_key"

    if (
        "quota" in lower
        or "limit: 0" in lower
        or "daily" in lower
        or "per day" in lower
        or "resource_exhausted" in lower
    ):
        return "quota"

    if (
        "429" in message
        or "rate limit" in lower
        or "too many requests" in lower
    ):
        return "rate_limit"

    if "403" in message or "permission_denied" in lower:
        return "permission"

    return "other"


def friendly_gemini_error(error):
    error_type = classify_gemini_error(error)

    if error_type == "invalid_key":
        return (
            "Gemini API 키가 유효하지 않습니다. "
            "Google AI Studio에서 API 키를 확인해 주세요."
        )

    if error_type == "quota":
        return (
            "Gemini 무료 API의 사용량 한도에 도달했습니다. "
            "일일 또는 분당 할당량이 초기화된 후 다시 시도해 주세요. "
            "이 오류는 자동 재시도하지 않습니다."
        )

    if error_type == "rate_limit":
        return (
            "Gemini 요청이 일시적으로 집중되어 제한되었습니다. "
            "잠시 기다린 후 다시 시도해 주세요."
        )

    if error_type == "permission":
        return (
            "Gemini API 권한 오류입니다. "
            "API 키와 모델 사용 가능 여부를 확인해 주세요."
        )

    return f"Gemini API 호출 실패: {str(error)[:500]}"


# ---------------------------------------------------------------
# Gemini 호출
# ---------------------------------------------------------------

def call_gemini(prompt, search=False, max_tokens=8000):
    client = get_gemini_client()

    generation_config = {
        "max_output_tokens": max_tokens,
        "thinking_level": "medium",
    }

    tools = []

    if search:
        tools = [{"type": "google_search"}]

    last_error = None

    for attempt in range(2):
        try:
            interaction = client.interactions.create(
                model=GEMINI_MODEL,
                input=prompt,
                tools=tools,
                generation_config=generation_config,
            )

            result = extract_interaction_text(interaction)

            if not result:
                raise RuntimeError(
                    "Gemini 응답이 비어 있습니다. "
                    "모델 응답 또는 API 사용량을 확인해 주세요."
                )

            return result

        except Exception as error:
            last_error = error
            error_type = classify_gemini_error(error)

            # 일일 할당량 초과는 재시도해도 해결되지 않으므로 중단
            if error_type == "quota":
                raise RuntimeError(friendly_gemini_error(error))

            # 일시적인 분당 요청 제한에 한해서만 한 번 재시도
            if error_type == "rate_limit" and attempt == 0:
                time.sleep(GEMINI_RETRY_WAIT)
                continue

            raise RuntimeError(friendly_gemini_error(error))

    raise RuntimeError(friendly_gemini_error(last_error))


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
        # 직접 붙여넣은 원문: 압축만 실행 (Gemini 1회)
        # -------------------------------------------------------

        if use_source_only:
            if not pasted:
                state["pm_note"] = (
                    "압축할 원문을 먼저 붙여넣어 주세요."
                )
                return

            with st.spinner("Gemini로 원문을 압축하는 중..."):
                short = call_gemini(
                    COMPRESS_PROMPT.replace(
                        "__SOURCE__",
                        pasted,
                    ),
                    search=False,
                    max_tokens=3000,
                )

            state["pm_short"] = short

            state["pm_note"] = (
                f"압축 완료 · "
                f"{datetime.datetime.now(KST).strftime('%H:%M:%S')} · "
                "Gemini API 1회 호출"
            )

            return

        # -------------------------------------------------------
        # 장전 시황: 상세 리포트 + 압축본을 한 번에 생성
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

        prompt = (
            PREMARKET_PROMPT
            .replace("__TODAY__", today)
            .replace("__INDEX_TABLE__", index_table)
        )

        with st.spinner(
            "Gemini가 웹검색으로 상세 리포트와 압축본을 함께 작성 중입니다... "
            "(약 1~3분)"
        ):
            raw_result = call_gemini(
                prompt,
                search=True,
                max_tokens=12000,
            )

        detail, short = extract_dual_output(raw_result)

        # -------------------------------------------------------
        # 구분자 누락 등으로 응답 파싱에 실패한 경우
        # 상세 결과를 보존하고 오류 안내
        # -------------------------------------------------------

        if detail is None or short is None:
            state["pm_long"] = raw_result

            state["pm_note"] = (
                "상세 리포트는 생성됐지만 압축본 구분에 실패했습니다. "
                "원본 상세 리포트 탭에서 결과를 확인해 주세요. "
                "불필요한 추가 API 호출은 실행하지 않았습니다."
            )

            return

        state["pm_long"] = detail
        state["pm_short"] = short

        state["pm_note"] = (
            f"생성 완료 · "
            f"{datetime.datetime.now(KST).strftime('%H:%M:%S')} · "
            "Gemini API 1회 호출 · 상세 리포트 + 압축본"
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
        "해외 지수를 먼저 확인한 뒤, 웹검색으로 장전 브리핑 원본과 "
        "압축본을 한 번에 생성합니다. "
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
                "'압축만 실행'을 누르세요. 웹검색 단계를 건너뜁니다."
            ),
        )

        col_a, col_b, col_c = st.columns(
            [1.8, 1.4, 4],
            vertical_alignment="center",
        )

        col_a.button(
            "✨ 장전 시황 생성 (웹검색 + 압축)",
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
