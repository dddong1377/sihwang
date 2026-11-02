import datetime
import hmac
import html
import json
import os
import re
from zoneinfo import ZoneInfo

import requests
import streamlit as st
import streamlit.components.v1 as components

st.set_page_config(page_title="MTNW 장전 시황", layout="wide")

KST = ZoneInfo("Asia/Seoul")
WEEKDAYS = "월화수목금토일"

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
# 선물 지수는 전부 제외
# S&P500은 현물 코드 SPX.CBI만 사용
# 러셀 2000 선물도 제외
# ---------------------------------------------------------------

TOSS_INDEX_ITEMS = (
    # 국내 증시 - 상단 배치
    ("코스피", "KGG01P", 2, "extra"),
    ("코스닥", "QGG01P", 2, "extra"),

    # 미국 주요 지수
    ("다우존스", "DJI.DJI", 2, "index"),
    ("S&P500", "SPX.CBI", 2, "index"),
    ("나스닥", "COMP.NAI", 2, "index"),

    # 기타 주요 지표
    ("필라델피아 반도체", "SOX.NAI", 2, "extra"),
    ("VIX", "RGI..VIX", 2, "extra"),
)

# 선물 지수 코드 차단 목록
EXCLUDED_FUTURES_CODES = {
    "RFU.NQc1",
    "RFU.ESc1",
    "RFU.RTYc1",
}

# 해외 주요 지수 표에 표시할 항목
MAIN_INDEX_LABELS = (
    "다우존스",
    "S&P500",
    "나스닥",
    "영국 FTSE100",
    "프랑스 CAC40",
    "독일 DAX",
    "유로스톡스50",
)

# API 응답에 추가 지표가 있을 때 이름을 연결하기 위한 별칭
INDEX_NAME_ALIASES = {
    "다우존스": (
        "다우존스",
        "다우 산업",
        "Dow Jones",
        "Dow Jones Industrial Average",
    ),
    "S&P500": (
        "S&P 500",
        "S&P500",
        "S&P 500 지수",
        "S&P 500 Index",
    ),
    "나스닥": (
        "나스닥",
        "나스닥 종합",
        "NASDAQ",
        "NASDAQ Composite",
    ),
    "영국 FTSE100": (
        "영국 FTSE100",
        "FTSE 100",
        "FTSE100",
    ),
    "프랑스 CAC40": (
        "프랑스 CAC40",
        "CAC 40",
        "CAC40",
    ),
    "독일 DAX": (
        "독일 DAX",
        "DAX",
        "DAX 40",
    ),
    "유로스톡스50": (
        "유로스톡스50",
        "Euro Stoxx 50",
        "EURO STOXX 50",
    ),
    "원/달러 환율": (
        "원/달러",
        "USD/KRW",
        "달러/원",
        "미국 달러",
    ),
    "WTI 유가": (
        "WTI",
        "WTI 유",
        "서부 텍사스산 원유",
    ),
    "미 10년물 금리(%)": (
        "미국 10년물",
        "미 10년물",
        "US 10Y",
        "미국 국채 10년",
    ),
    "VIX": (
        "VIX",
        "변동성지수",
    ),
    "달러인덱스": (
        "달러인덱스",
        "달러 지수",
        "Dollar Index",
        "DXY",
    ),
    "비트코인": (
        "비트코인",
        "Bitcoin",
        "BTC",
    ),
    "금": (
        "금",
        "Gold",
        "금 선물",
    ),
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
    """
    Toss 웹 세션이 필요한 경우 Streamlit Secrets의 쿠키 등을 사용한다.

    Secrets 설정 예시:
    TOSS_INDICATOR_COOKIE = "브라우저에서 확인한 Cookie 값"
    TOSS_INDICATOR_XSRF_TOKEN = "필요한 경우 XSRF 토큰"
    TOSS_INDICATOR_APP_VERSION = "필요한 경우 앱 버전"
    TOSS_INDICATOR_BROWSER_TAB_ID = "필요한 경우 브라우저 탭 ID"

    쿠키나 인증값은 코드에 직접 입력하지 않는 것을 권장한다.
    """

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
    """
    Toss 대시보드 지수 API 조회.

    확인된 응답 구조:
    result.majorIndicatorInfos[]

    주요 필드:
    code
    name
    displayName
    price.latestPrice
    price.basePrice
    miniChart.candles[]
    """

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
    """
    다양한 형식의 날짜·시간 데이터를 KST로 변환한다.

    지원 형식:
    - Unix timestamp (초)
    - Unix timestamp (밀리초)
    - ISO 날짜 문자열
    - YYYYMMDD
    - YYYYMMDDHHMMSS

    미래 시각은 실제 업데이트 시각으로 인정하지 않는다.
    """

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
        "timestamp",
        "time",
        "datetime",
        "date",
        "candleTime",
        "candleDate",
        "tradingTime",
        "tradingDate",
        "startTime",
        "endTime",
        "startDate",
        "endDate",
        "baseDateTime",
        "localDateTime",
        "tradeDate",
        "chartTime",
        "x",
        "t",
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
    """
    실제 캔들 데이터의 마지막 유효 시각을 반환한다.

    miniChart.tradingEnd는 거래 종료 예정 시각일 수 있으므로
    실제 업데이트 시각으로 사용하지 않는다.
    """

    mini_chart = info.get("miniChart") or {}

    if not isinstance(mini_chart, dict):
        return "확인 불가"

    candles = mini_chart.get("candles")

    if not isinstance(candles, list) or not candles:
        return "확인 불가"

    timestamps = find_candle_timestamps(candles)

    if not timestamps:
        return "확인 불가"

    latest_timestamp = max(timestamps)

    return latest_timestamp.strftime("%Y-%m-%d %H:%M")


# ---------------------------------------------------------------
# 선물 지수 필터
# ---------------------------------------------------------------

def is_futures_index(info):
    """
    API가 고정 목록 외의 지수를 추가로 반환하더라도
    선물 지수는 화면에 표시하지 않는다.

    RFU 계열 코드 및 선물 명칭을 가진 지수 제외.
    """

    code = str(info.get("code") or "").strip()
    name = get_info_name(info).lower()

    if code in EXCLUDED_FUTURES_CODES:
        return True

    if code.upper().startswith("RFU."):
        return True

    futures_keywords = (
        "선물",
        "futures",
        "future",
    )

    if any(keyword in name for keyword in futures_keywords):
        return True

    return False


# ---------------------------------------------------------------
# Toss 데이터 행 구성
# ---------------------------------------------------------------

def build_toss_rows(infos):
    """
    API에서 실제로 반환된 지수만 데이터로 사용한다.

    선물 지수는 고정 목록과 추가 응답 모두에서 제외한다.
    """

    by_code = {}

    for info in infos:

        if not isinstance(info, dict):
            continue

        code = str(info.get("code") or "").strip()

        if not code:
            continue

        if is_futures_index(info):
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

    # 고정 목록에 없는 추가 지표 표시
    # 단, 선물 지수는 제외
    known_codes = {
        item[1]
        for item in TOSS_INDEX_ITEMS
    }

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

            row_name = str(
                row.get("label", "")
            ).lower()

            if any(
                alias.lower() == row_name
                for alias in aliases
            ):
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

        state["pm_indices_note"] = (
            f"Toss 지수 조회 실패: {error}"
        )


# ---------------------------------------------------------------
# 프롬프트
# ---------------------------------------------------------------

PREMARKET_PROMPT = """# 역할
너는 경제신문 증권부 기자, 증권사 애널리스트, 네이버 SEO 전문가, 검색광고 마케터, 유튜브 PD를 겸하는 "MTNW 시황 에디터"다.
국내외 최신 주식 뉴스를 조사해 MTNW 광고팀·콘텐츠팀·방송팀이 바로 쓸 수 있는 "한눈에 보이는 데일리 시황 리포트"를 작성한다.

# 실행 모드: [장전] (개장 전 브리핑)
- 중심 내용: 미국·유럽 마감 결과, 간밤 주요 이슈, 오늘 국내 증시 전망, 오늘 일정
- 지수 표기: 미국·유럽 "종가" 기준 (휴장이면 사유 표기)
- 키워드: 개장 직후 검색 급증이 예상되는 키워드 우선
- 섹션 강조: 해외 증시 → 핵심 이슈 → 글로벌 이슈 → 오늘 일정
- 오늘 날짜: __TODAY__

# 조사 원칙 (반드시 웹검색 사용)
1. 오늘 날짜 기준 최근 24시간 이내 기사만 사용한다.
2. 아래 출처 중 최소 10개 이상을 교차 검증한다.
   - 국내: 연합뉴스, 한국경제, 매일경제, 머니투데이, 서울경제, 뉴스1, 이데일리, 아시아경제
   - 해외: Reuters, Bloomberg, CNBC, WSJ, FT, AP
   - 공시·지표: DART, 한국거래소, FOMC, CPI, PPI, PCE, 미국 국채금리, 달러인덱스, 국제유가, VIX
3. 같은 뉴스는 하나로 통합한다. 기사 문장을 그대로 복사하지 않는다.
4. 확인되지 않은 루머, 증시와 무관한 정치 뉴스는 제외한다.
5. 확인 불가한 수치는 추정하지 말고 "확인 불가"로 표기한다. 허위 정보는 절대 작성하지 않는다.
6. 한국 개인투자자 관점에서 우선순위를 정한다.

# 지수 데이터 규칙 (중요)
- 아래 [확인된 Toss 지수 데이터]를 기준 수치로 사용한다.
- Toss 데이터에 없는 지수는 검색으로 별도 확인한다.
- 검색으로도 확인되지 않으면 "확인 불가"로 표기한다.
- Toss 데이터에 표시된 기준 시각을 확인하고, 최신 종가인지 실시간 지표인지 구분한다.
- 비고에 미확인 또는 데이터 없음으로 표시된 항목은 임의로 수치를 채우지 않는다.
- 비고에 "휴장/미갱신 가능"이 있는 지수는 검색으로 휴장 여부를 확인해 사유를 표기한다.
- 표기 형식: 지수명 : 지수값 (전일 대비 ±포인트, ±%) — 소수점 둘째 자리까지
- 대상: 다우, S&P500 현물, 나스닥 현물, 영국 FTSE100, 프랑스 CAC40, 독일 DAX, 유로스톡스50
- 선물 지수는 사용하지 않는다. S&P500은 반드시 현물 지수만 사용한다.

[확인된 Toss 지수 데이터]
__INDEX_TABLE__

# 작성 원칙 (간결성 최우선)
- 전체 분량: 모바일 스크롤 기준 3~4화면 내외로 압축한다.
- 문장은 한 줄 요약형, 표·불릿 위주. 줄글은 "글로벌 이슈"에서만 2~3문장 허용.
- 각 항목은 지정된 개수를 넘기지 않는다. 이모지는 섹션 제목과 핵심 포인트에만 사용한다.
- 키워드는 네이버/구글 검색광고에 바로 넣을 수 있는 단어형만 쓴다. (O: 삼성전자, HBM, 환율 / X: 미국 금리 인상 가능성)
- 주요 주장에는 [번호]로 출처를 붙이고, 하단 출처 목록과 연결한다.
- 검색 과정이나 설명 문장은 쓰지 말고, 아래 형식의 최종 리포트만 출력한다.

# 출력 형식

## 📅 YYYY년 MM월 DD일 (요일) | MTNW Market Intelligence [장전]
> 📌 **오늘 한 줄 요약**: (20~30자)

### 1. 🌎 해외 증시
| 지수 | 종가/현재가 | 전일 대비 |
|---|---|---|
(7개 지수, 휴장 사유 표기)

### 2. 🔥 핵심 이슈 TOP5
각 이슈는 아래 4줄 고정:
- **제목** (시장 영향도 ⭐1~5 / 콘텐츠 점수 00/100)
- 무슨 일: 1문장
- 왜 중요한가 + 관련 종목·테마: 1문장
- 향후 일정/리스크: 1문장 [출처번호]

### 3. 🔍 검색 키워드
**실시간 TOP20** (단어형만)
| 순위 | 키워드 | 검색 의도 | 광고/SEO 활용 |
**급상승 TOP10**
| 순위 | 키워드 | 급상승 이유(10자 내외) |

### 4. 📈 종목·테마·수급
- **언급 TOP10 종목**: 종목 | 이유(한 줄) | 방송 적합도(상/중/하)
- **강세 테마 TOP5**: 테마 | 관련 종목 | 지속 가능성
- **돈 몰리는 업종 TOP5**: 업종 | 이유
- **수급**: 외국인 / 기관 / 개인 순매수·순매도 요약, 매수 업종, 매도 업종 (각 1줄)

### 5. 🌐 글로벌 이슈 (미국·중국·일본·유럽·원자재·환율)
각 2~3문장. 지표(금리, 달러인덱스, 유가, VIX 등) 수치 포함.

### 6. 📆 오늘 체크 일정
| 시간 | 일정 | 시장 영향 |

### 7. 🎬 오늘 반드시 만들 콘텐츠 TOP3
| 순위 | 뉴스 | 추천 형식 | 핵심 키워드 | 썸네일 문구(15자 이하) |

### 8. 💰 광고 키워드 (각 5개 이내)
| 구분(브랜드/일반/정보탐색/급상승/롱테일) | 키워드 | 전환 가능성 |

### 9. ✍️ 콘텐츠 아이디어
- 유튜브 제목 5개
- 블로그 제목 10개 (SEO형)
- 숏폼 아이디어 10개 (한 줄씩)
- 카드뉴스 주제 5개
- 썸네일 문구 20개 (15자 이하, 클릭 유도형)

### 10. 🎙️ 방송 추천 종목 TOP5 + 오늘 꼭 봐야 할 종목 TOP5
| 종목 | 이유 | 주의점 |

### 11. 🔄 전일 대비 변화
새로 등장 / 사라진 키워드, 강해진·약해진 테마 (각 1줄)

### 12. 📎 출처
| 번호 | 언론사 | 기사 제목 | 링크 |
(최소 10개, 24시간 이내)

# 최종 점검 (출력 전)
- 지수 수치와 휴장 여부를 재확인했는가?
- 24시간 초과 기사나 미확인 루머가 없는가?
- 모든 항목이 지정 개수 이내로 간결한가?
- 키워드가 전부 단어형인가?
"""

COMPRESS_PROMPT = """# 역할
너는 바쁜 투자자가 5초 만에 읽는 "MTNW 시황 압축 에디터"다.
아래 [원문 시황글]을 읽고, 핵심만 남긴 "한눈에 보이는 시황글"로 압축한다.

# 압축 원칙
1. 원문에 있는 사실만 사용한다. 새로운 수치·종목·뉴스를 추가하지 않는다.
2. 원문에서 "확인 불가", "추정", "후보"로 표시된 내용은 단정하지 말고 그 표시를 유지한다.
3. 전체 분량은 모바일 1화면 반(원문의 약 20~25%) 이내로 한다.
4. 문장은 모두 한 줄(40자 내외). 설명·배경·반복 문장은 삭제한다.
5. 핵심 포인트마다 반드시 관련 종목을 붙인다.
6. 강조 규칙을 반드시 지킨다.
   - 핵심 키워드·수치·종목명은 **굵게**
   - 상승·호재는 🔺, 하락·악재는 🔻, 중립·관망은 ➖
   - 가장 중요한 이슈 1개에는 🔥 표시
7. 표는 지수표 1개만 허용한다. 나머지는 불릿으로 쓴다.
8. 출처는 번호 없이 "출처: 언론사명 나열"로 한 줄만 쓴다. (링크 생략, 필요하면 요청 시 제공)
9. 설명 문장 없이 아래 형식의 최종 결과만 출력한다.

# 출력 형식

## 📅 YYYY년 MM월 DD일 (요일) | [장전/오후] 시황

> 📌 **한 줄 요약**: (20~30자, 가장 강한 메시지)

### 🌎 지수 한눈에
| 지수 | 등락 |
|---|---|
(원문에서 미국 3대 지수 + 유럽 4개 + 필요 시 코스피·코스닥만. 형식: 수치 (±%) + 🔺/🔻. 기준일 표기. 수치 없으면 "확인 불가")

### 🔥 오늘의 핵심 포인트 TOP3
각 포인트는 아래 3줄 고정:
**① 핵심 문장 (15자 내외 제목)** 🔺/🔻/➖
- 내용: 무슨 일인지 한 줄
- 📌 관련 종목: **종목A**, **종목B**, **종목C**
- ⏰ 체크: 일정 또는 리스크 한 줄

### 📈 오늘 주목 종목 TOP5
| 종목 | 한 줄 이유 | 주의 |
|---|---|---|
(종목명 굵게. 원문에 근거 있는 종목만)

### 🏷️ 강세·관심 테마
**테마1** · **테마2** · **테마3** (최대 5개, 한 줄로 나열)

### 💰 수급
외국인 **○○** / 기관 **○○** / 개인 **○○** (원문에 없으면 "확인 불가")

### 📆 오늘·이번 주 일정
- 시간 | 일정 (**핵심 일정은 굵게**, 최대 5개)

### 🔍 핵심 키워드 10
#키워드1 #키워드2 ... (단어형만, 원문에 등장한 키워드 위주)

### ⚠️ 한 줄 주의
(오늘 가장 큰 리스크 1문장)

# 압축 우선순위 (원문에서 이것만 살린다)
1순위: 지수 등락, 시장을 움직인 핵심 이슈, 관련 종목
2순위: 수급, 오늘 일정, 강세 테마
3순위: 키워드
삭제 대상: 콘텐츠 점수, 광고 키워드, 콘텐츠 아이디어, 썸네일 문구, 글로벌 이슈 상세, 전일 대비 변화, 출처 링크, 최종 점검, 각종 참고 문구

# 최종 점검 (출력 전)
- 원문에 없는 수치·종목을 넣지 않았는가?
- 핵심 포인트마다 관련 종목이 붙어 있는가?
- 굵게·이모지 강조가 핵심에만 쓰였는가?
- 모든 문장이 한 줄인가?

# [원문 시황글]
__SOURCE__
"""


# ---------------------------------------------------------------
# Claude 호출 (웹검색 도구 선택 사용)
# ---------------------------------------------------------------

def call_claude(prompt, search=False, max_tokens=8000):

    key = secret("ANTHROPIC_API_KEY")

    if not key:
        raise RuntimeError(
            "Streamlit Secrets에 ANTHROPIC_API_KEY를 설정하세요."
        )

    model = (
        secret("ANTHROPIC_MODEL_PREMARKET")
        or "claude-sonnet-5-5"
    )

    body = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [
            {
                "role": "user",
                "content": prompt,
            }
        ],
    }

    if search:

        body["tools"] = [
            {
                "type": "web_search_20250305",
                "name": "web_search",
                "max_uses": 10,
            }
        ]

    texts = []

    for _ in range(4):

        response = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json=body,
            timeout=240,
        )

        if response.status_code != 200:

            raise RuntimeError(
                f"HTTP {response.status_code}: "
                f"{response.text[:400]}"
            )

        data = response.json()
        content = data.get("content", [])

        texts.extend(
            block.get("text", "")
            for block in content
            if block.get("type") == "text"
        )

        if data.get("stop_reason") == "pause_turn":

            body["messages"] = body["messages"] + [
                {
                    "role": "assistant",
                    "content": content,
                }
            ]

            continue

        break

    result = "".join(texts).strip()

    start = result.find("## 📅")

    if start > 0:
        result = result[start:]

    if not result:
        raise RuntimeError("응답이 비어 있습니다.")

    return result


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

    try:

        pasted = state.get("pm_pasted", "").strip()

        if use_source_only and pasted:

            source = pasted

        else:

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
                "① 웹검색으로 장전 시황 원본 작성 중... (1~3분)"
            ):

                source = call_claude(
                    prompt,
                    search=True,
                    max_tokens=8000,
                )

            state["pm_long"] = source

        with st.spinner("② 한눈에 보이는 시황글로 압축 중..."):

            short = call_claude(
                COMPRESS_PROMPT.replace("__SOURCE__", source),
                search=False,
                max_tokens=3000,
            )

        state["pm_short"] = short

        state["pm_note"] = (
            f"생성 완료 · "
            f"{datetime.datetime.now(KST).strftime('%H:%M:%S')} · "
            "수치·종목·출처를 발행 전에 확인하세요."
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
        "해외 지수를 먼저 확인한 뒤, 웹검색으로 장전 브리핑 원본을 만들고 "
        "한눈에 보이는 시황글로 압축합니다. "
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

            col_note.caption(
                state["pm_indices_note"]
            )

        rows = state.get("pm_indices")

        if rows:

            st.markdown(
                indices_markdown(rows)
            )

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

            col_c.caption(
                state["pm_note"]
            )

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
