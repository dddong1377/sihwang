"""주간 정리용 토스증권 시세 조회.

오후 시황 페이지와 같은 기준(시가총액 상위 100위, 우선주·스팩·ETF 제외)으로
한 주 동안의 등락률을 계산합니다. 토스증권 Open API(Secrets의 TOSS_CLIENT_ID /
TOSS_CLIENT_SECRET)를 사용합니다.

주간 등락률 = (그 주 마지막 거래일 종가 ÷ 직전 주 마지막 거래일 종가 - 1) × 100

[공개 데이터 모드] Secrets의 OPENAPI_MODE
  - (비워 둠 / on) : 지금처럼 토스 Open API만 사용 (기본값, 동작 변화 없음)
  - auto           : 토스를 먼저 시도하고 막히면(허용 IP 문제 등) 10분간 네이버 금융 공개 데이터로 대체
  - off            : 토스 Open API를 쓰지 않고 항상 네이버 금융 공개 데이터 사용
공개 데이터: 시총 상위 100위 = 네이버 금융 시가총액 표, 일봉 종가 = 네이버 금융 일별 시세(수정주가 아님).
"""

import datetime
import html
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from zoneinfo import ZoneInfo

import requests
import streamlit as st

KST = ZoneInfo("Asia/Seoul")
BASE = "https://openapi.tossinvest.com"

CAP_RANK_LIMIT = 100
CANDLE_COUNT = 12
MARKET_CLOSE_CONFIRM = datetime.time(15, 40)

# 오후 시황 페이지와 같은 제외 규칙
EXCLUDE = re.compile(
    r"스팩|제\d+호|ETN|KODEX|TIGER|ACE |SOL |RISE|HANARO|KOSEF|ARIRANG|PLUS |KIWOOM|우$"
)


class TossError(Exception):
    pass


NAVER_WEB_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Referer": "https://finance.naver.com/sise/",
}
YAHOO_HEADERS = {"User-Agent": "Mozilla/5.0"}
NAVER_MOBILE_HEADERS = {
    "User-Agent": NAVER_WEB_HEADERS["User-Agent"],
    "Referer": "https://m.stock.naver.com/",
}


def _decode(content):
    """네이버 새 증권 페이지는 UTF-8, 예전 페이지는 CP949입니다."""

    try:
        return content.decode("utf-8")
    except UnicodeDecodeError:
        return content.decode("cp949", errors="ignore")

_BLOCK = {"until": 0.0}
_MARKET = {}  # 종목코드 → KOSPI/KOSDAQ (공개 데이터 모드에서 야후 대조에 사용)


def openapi_mode():
    return (_secret("OPENAPI_MODE").strip().lower() or "on")


def public_mode():
    """지금 이 순간 토스 Open API를 건너뛰고 공개 데이터를 써야 하는지."""

    mode = openapi_mode()

    if mode in ("off", "public"):
        return True

    if mode == "auto":
        return time.time() < _BLOCK["until"]

    return False


def _note_failure(error):
    """auto 모드에서 토스 접속이 막히면 10분간 공개 데이터로 전환합니다."""

    if openapi_mode() == "auto":
        _BLOCK["until"] = time.time() + 600


def _secret(key):
    try:
        value = st.secrets.get(key)
    except Exception:
        value = None

    return str(value) if value else os.getenv(key, "")


def num(value, default=None):
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return default


@st.cache_data(ttl=20 * 60 * 60, show_spinner=False)
def _token(client_id, client_secret):
    if not client_id or not client_secret:
        raise TossError("TOSS_CLIENT_ID / TOSS_CLIENT_SECRET이 Secrets에 없습니다.")

    response = requests.post(
        f"{BASE}/oauth2/token",
        data={
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=12,
    )

    if response.status_code != 200:
        raise TossError(
            f"토큰 발급 실패 HTTP {response.status_code}: {response.text[:300]}"
        )

    token = response.json().get("access_token")

    if not token:
        raise TossError("토큰 응답에 access_token이 없습니다.")

    return token


_TOKEN_LOCK = threading.Lock()


def _fresh_token(old_token, force=False):
    """다른 곳에서 새 토큰을 받아 이 토큰이 무효가 됐을 때 새로 받습니다.

    force=True 는 '다른 곳이 받아 둔 토큰'도 이미 무효일 수 있을 때, 무조건 새로 발급합니다."""

    with _TOKEN_LOCK:
        current = _token(_secret("TOSS_CLIENT_ID"), _secret("TOSS_CLIENT_SECRET"))

        if force or current == old_token:
            _token.clear()
            current = _token(_secret("TOSS_CLIENT_ID"), _secret("TOSS_CLIENT_SECRET"))

        return current


def api_get(path, params=None):
    token = _token(_secret("TOSS_CLIENT_ID"), _secret("TOSS_CLIENT_SECRET"))

    response = None
    refreshed = 0

    for attempt in range(5):
        try:
            response = requests.get(
                f"{BASE}{path}",
                params=params or {},
                headers={"Authorization": f"Bearer {token}"},
                timeout=(6, 15),
            )
        except requests.RequestException as error:
            if attempt == 4:
                raise TossError(f"{path} 연결 실패: {str(error)[-120:]}") from error

            time.sleep(1.5 * (attempt + 1))
            continue

        # 토큰이 무효화(401)되면 새 토큰으로 한 번 더 시도
        # (첫 번째는 저장된 새 토큰을 쓰고, 그래도 401이면 무조건 새로 발급)
        if response.status_code == 401 and refreshed < 2:
            refreshed += 1
            token = _fresh_token(token, force=(refreshed == 2))
            continue

        # 요청 한도(429)·서버 오류(5xx)는 잠깐 쉬고 다시 시도
        if response.status_code == 429 or response.status_code >= 500:
            time.sleep(1.5 * (attempt + 1))
            continue

        break

    if response is None or response.status_code != 200:
        code = response.status_code if response is not None else "없음"

        if code == 403:
            raise TossError(
                "HTTP 403: 토스증권 Open API 허용 IP에 배포 서버 IP가 등록돼 있는지 확인하세요."
            )

        raise TossError(f"{path} HTTP {code}")

    data = response.json()

    if data.get("error"):
        error = data["error"]
        raise TossError(f"{error.get('code')}: {error.get('message')}")

    return data.get("result", {})


@st.cache_data(ttl=12 * 60 * 60, show_spinner=False)
def fetch_share_universe():
    """KOSPI·KOSDAQ 보통주의 이름·발행주식수."""

    universe = {}

    for market in ("KOSPI", "KOSDAQ"):
        listed = api_get(
            "/api/v1/stocks/all",
            {
                "market": market,
                "status": "ACTIVE",
                "securityType": "STOCK",
                "commonShare": "true",
            },
        )

        symbols = [
            row["symbol"]
            for row in listed
            if isinstance(row, dict) and row.get("symbol")
        ]

        for start in range(0, len(symbols), 200):
            infos = api_get(
                "/api/v1/stocks",
                {"symbols": ",".join(symbols[start:start + 200])},
            )

            for info in infos:
                shares = num(info.get("sharesOutstanding"))

                if shares:
                    universe[info["symbol"]] = {
                        "name": info.get("name", ""),
                        "shares": shares,
                    }

    return universe


@st.cache_data(ttl=6 * 60 * 60, show_spinner=False)
def _fetch_cap_top_toss(day_text):
    """시가총액(현재가 × 발행주식수) 상위 100개 [(종목코드, 종목명)] (토스 Open API)."""

    universe = fetch_share_universe()
    symbols = list(universe)

    caps = []

    for start in range(0, len(symbols), 200):
        prices = api_get(
            "/api/v1/prices",
            {"symbols": ",".join(symbols[start:start + 200])},
        )

        for item in prices:
            if not isinstance(item, dict):
                continue

            price = num(item.get("lastPrice"))
            info = universe.get(item.get("symbol"))

            if price and info and not EXCLUDE.search(info["name"]):
                caps.append((price * info["shares"], item["symbol"], info["name"]))

    caps.sort(reverse=True)

    top = [(symbol, name) for _, symbol, name in caps[:CAP_RANK_LIMIT]]

    if len(top) < 50:
        raise TossError("시가총액 상위 종목 수가 부족합니다.")

    return top


def _candle_closes_toss(symbol):
    """[(날짜, 종가)] 오름차순 (토스 Open API)."""

    result = api_get(
        "/api/v1/candles",
        {
            "symbol": symbol,
            "interval": "1d",
            "count": CANDLE_COUNT,
            "adjusted": "true",
        },
    )

    closes = []

    for candle in (result.get("candles") or []) if isinstance(result, dict) else []:
        try:
            day = datetime.date.fromisoformat(str(candle.get("timestamp", ""))[:10])
        except ValueError:
            continue

        close = num(candle.get("closePrice"))

        if close:
            closes.append((day, close))

    closes.sort()

    return closes


# ---------------------------------------------------------------
# 공개 데이터(네이버 금융) 버전
# ---------------------------------------------------------------

def _parse_naver_market_sum(page_html, market):
    """네이버 금융 시가총액 표 한 장 → [{symbol, name, cap}]. 열 위치는 머리글로 찾습니다."""

    body = None

    for found in re.finditer(r"<table[^>]*>(.*?)</table>", page_html, re.S):
        if "시가총액" in found.group(1) and "현재가" in found.group(1):
            body = found.group(1)
            break

    if body is None:
        raise TossError("네이버 금융 시가총액 표를 찾지 못했습니다.")

    def clean(text):
        text = html.unescape(re.sub(r"<[^>]+>", "", text))
        return re.sub(r"\s+", " ", text).strip()

    headers = [clean(h) for h in re.findall(r"<th[^>]*>(.*?)</th>", body, re.S)]

    try:
        i_cap = headers.index("시가총액")
    except ValueError as error:
        raise TossError(f"네이버 표 머리글 형식이 달라졌습니다: {headers}") from error

    rows = []

    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", body, re.S):
        link = re.search(r'code=(\d{6})"[^>]*>\s*([^<]+?)\s*</a>', tr)

        if not link:
            continue

        cells = [clean(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]

        if len(cells) <= i_cap:
            continue

        cap = num(cells[i_cap])

        if cap is None:
            continue

        rows.append({
            "symbol": link.group(1),
            "name": html.unescape(link.group(2)).strip(),
            "cap": cap,
            "market": market,
        })

    return rows



def _cap_rows_html():
    rows = {}

    for sosok, market, pages in ((0, "KOSPI", 3), (1, "KOSDAQ", 2)):
        for page in range(1, pages + 1):
            response = requests.get(
                "https://finance.naver.com/sise/sise_market_sum.naver",
                params={"sosok": sosok, "page": page},
                headers=NAVER_WEB_HEADERS,
                timeout=8,
            )

            if response.status_code != 200:
                raise TossError(f"네이버 금융 HTTP {response.status_code}")

            parsed = _parse_naver_market_sum(
                _decode(response.content), market
            )

            if len(parsed) < 10:
                raise TossError("네이버 금융 시가총액 표에서 종목을 충분히 읽지 못했습니다.")

            for row in parsed:
                rows[row["symbol"]] = row

    return rows


def _cap_rows_mobile():
    """네이버 모바일 증권 API(JSON, 비공식)로 시총 순 목록."""

    rows = {}

    for market, pages in (("KOSPI", 3), ("KOSDAQ", 2)):
        for page in range(1, pages + 1):
            response = requests.get(
                f"https://m.stock.naver.com/api/stocks/marketValue/{market}",
                params={"page": page, "pageSize": 50},
                headers=NAVER_MOBILE_HEADERS,
                timeout=8,
            )

            if response.status_code != 200:
                raise TossError(f"HTTP {response.status_code}")

            for item in (response.json() or {}).get("stocks") or []:
                symbol = str(item.get("itemCode") or "")
                name = str(item.get("stockName") or "").strip()
                cap = num(item.get("marketValue"))

                if re.fullmatch(r"\d{6}", symbol) and name and cap is not None:
                    rows[symbol] = {"symbol": symbol, "name": name, "cap": cap, "market": market}

    if len(rows) < 50:
        raise TossError("종목 수가 부족합니다.")

    return rows


@st.cache_data(ttl=6 * 60 * 60, show_spinner=False)
def _fetch_cap_top_public(day_text):
    """시가총액 상위 100개 [(종목코드, 종목명)] (네이버 금융, 코스피 3쪽 + 코스닥 2쪽)."""

    try:
        rows = _cap_rows_html()

    except Exception as html_error:
        try:
            rows = _cap_rows_mobile()

        except Exception as mobile_error:
            raise TossError(f"{html_error} / 모바일 API: {mobile_error}") from mobile_error

    ranked = sorted(
        (row for row in rows.values() if not EXCLUDE.search(row["name"])),
        key=lambda row: -row["cap"],
    )[:CAP_RANK_LIMIT]

    if len(ranked) < 50:
        raise TossError("시가총액 상위 종목 수가 부족합니다.")

    for row in ranked:
        _MARKET[row["symbol"]] = row["market"]

    return [(row["symbol"], row["name"]) for row in ranked]


def _parse_naver_daily(page_html):
    """네이버 일별 시세 한 쪽 → [(날짜, 종가)]. 열 순서: 날짜, 종가, 전일비, 시가, 고가, 저가, 거래량."""

    out = []

    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", page_html, re.S):
        found = re.search(r"(\d{4})\.(\d{2})\.(\d{2})", tr)

        if not found:
            continue

        values = re.findall(r'class="tah p11[^"]*"[^>]*>\s*([\d,]+)\s*<', tr)
        close = num(values[0]) if values else None

        if close:
            out.append((
                datetime.date(int(found.group(1)), int(found.group(2)), int(found.group(3))),
                close,
            ))

    return out


def _candle_closes_mobile(symbol):
    """네이버 모바일 증권 일별 시세 API(JSON, 비공식) [(날짜, 종가)]."""

    response = requests.get(
        f"https://m.stock.naver.com/api/stock/{symbol}/price",
        params={"pageSize": 20, "page": 1},
        headers=NAVER_MOBILE_HEADERS,
        timeout=8,
    )

    if response.status_code != 200:
        raise TossError(f"{symbol} 모바일 시세 HTTP {response.status_code}")

    data = response.json()
    items = data if isinstance(data, list) else (data.get("priceInfos") or [])
    out = []

    for item in items:
        try:
            day = datetime.date.fromisoformat(str(item.get("localTradedAt", ""))[:10])
        except ValueError:
            continue

        close = num(item.get("closePrice"))

        if close:
            out.append((day, close))

    if not out:
        raise TossError(f"{symbol} 모바일 시세를 읽지 못했습니다.")

    return sorted(out)


def _candle_closes_public(symbol):
    """네이버 일별 시세(HTML)를 먼저, 안 되면 모바일 API로."""

    try:
        return _candle_closes_html(symbol)

    except Exception:
        return _candle_closes_mobile(symbol)


def _candle_closes_html(symbol):
    """[(날짜, 종가)] 오름차순 (네이버 일별 시세 1~2쪽 = 최근 10~20거래일)."""

    closes = {}

    for page in (1, 2):
        response = requests.get(
            "https://finance.naver.com/item/sise_day.naver",
            params={"code": symbol, "page": page},
            headers=NAVER_WEB_HEADERS,
            timeout=8,
        )

        if response.status_code != 200:
            raise TossError(f"{symbol} 네이버 일별 시세 HTTP {response.status_code}")

        rows = _parse_naver_daily(_decode(response.content))

        if not rows:
            raise TossError(f"{symbol} 네이버 일별 시세를 읽지 못했습니다.")

        closes.update(dict(rows))

        # 12거래일 정도면 지난주 기준가까지 충분합니다.
        if len(closes) >= CANDLE_COUNT:
            break

    return sorted(closes.items())


def _yahoo_closes(symbol):
    """야후 일봉 종가 [(날짜, 종가)] — 대조용. 실패하면 None."""

    suffix = ".KS" if _MARKET.get(symbol) == "KOSPI" else ".KQ"

    try:
        response = requests.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}{suffix}",
            params={"range": "1mo", "interval": "1d"},
            headers=YAHOO_HEADERS,
            timeout=8,
        )

        if response.status_code != 200:
            return None

        result = response.json()["chart"]["result"][0]
        stamps = result.get("timestamp") or []
        closes = result["indicators"]["quote"][0].get("close") or []

        return sorted(
            (datetime.datetime.fromtimestamp(ts, KST).date(), close)
            for ts, close in zip(stamps, closes)
            if close
        )

    except Exception:
        return None


def fetch_cap_top(day_text):
    """시총 상위 100개 [(종목코드, 종목명)]. 모드에 따라 토스 또는 네이버."""

    if not public_mode():
        try:
            return _fetch_cap_top_toss(day_text)

        except Exception as error:
            if openapi_mode() != "auto":
                raise

            _note_failure(error)

    return _fetch_cap_top_public(day_text)


def candle_closes(symbol):
    """[(날짜, 종가)] 오름차순. 모드에 따라 토스 일봉 또는 네이버 일별 시세."""

    if not public_mode():
        try:
            return _candle_closes_toss(symbol)

        except Exception as error:
            if openapi_mode() != "auto":
                raise

            _note_failure(error)

    return _candle_closes_public(symbol)


def weekly_return(closes, monday, friday, skip_today):
    """(주간 등락률, 마지막 종가일) 또는 None. 기준은 월요일 이전 마지막 종가."""

    base = None
    last = None

    for day, close in closes:
        if day < monday:
            base = close
        elif day <= friday and day != skip_today:
            last = (day, close)

    if not base or not last:
        return None

    return (last[1] - base) / base * 100, last[0]


@st.cache_data(ttl=10 * 60, show_spinner=False)
def fetch_weekly_movers(monday_text, closed_text):
    """시총 상위 100위의 주간 등락률. {'surge': 상위3, 'plunge': 하위3, 'count': 계산된 종목 수}

    closed_text: 오늘 장마감 봉을 쓸 수 있는지 구분하기 위한 캐시 키('open'/'closed')."""

    monday = datetime.date.fromisoformat(monday_text)
    friday = monday + datetime.timedelta(days=4)

    now = datetime.datetime.now(KST)
    today = now.date()

    # 장마감 전의 오늘 봉은 진행 중인 값이므로 제외
    skip_today = today if now.time() < MARKET_CLOSE_CONFIRM else None

    top = fetch_cap_top(today.isoformat())

    def one(item):
        symbol, name = item

        try:
            closes = candle_closes(symbol)
        except Exception:
            return None

        result = weekly_return(closes, monday, friday, skip_today)

        if result is None:
            return None

        pct, last_day = result

        return {"name": name, "symbol": symbol, "pct": pct, "last_day": last_day}

    with ThreadPoolExecutor(max_workers=3) as pool:
        rows = [row for row in pool.map(one, top) if row]

    if len(rows) < 30:
        raise TossError(
            f"일봉에서 주간 등락률을 계산한 종목이 {len(rows)}개뿐입니다. "
            "(최근 2주를 벗어난 주는 조회할 수 없습니다.)"
        )

    ups = sorted((row for row in rows if row["pct"] > 0), key=lambda r: -r["pct"])
    downs = sorted((row for row in rows if row["pct"] < 0), key=lambda r: r["pct"])

    used_public = public_mode() or openapi_mode() == "off"
    picked = ups[:3] + downs[:3]

    # 공개 데이터일 때는 야후 일봉으로 주간 등락률을 한 번 더 계산해 대조합니다.
    if used_public:
        for row in picked:
            other_closes = _yahoo_closes(row["symbol"])
            other = (
                weekly_return(other_closes, monday, friday, skip_today)
                if other_closes else None
            )

            if other is None:
                row["check"] = None
            else:
                diff = abs(row["pct"] - other[0])
                row["check"] = {"ok": diff <= 0.7, "diff": diff, "other": other[0], "with": "야후"}

    return {
        "surge": ups[:3],
        "plunge": downs[:3],
        "count": len(rows),
        "source": "네이버 금융(공개 데이터)" if used_public else "토스 Open API",
        "fetched": time.time(),
    }
