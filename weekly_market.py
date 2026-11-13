"""주간 정리용 토스증권 시세 조회.

오후 시황 페이지와 같은 기준(시가총액 상위 100위, 우선주·스팩·ETF 제외)으로
한 주 동안의 등락률을 계산합니다. 토스증권 Open API(Secrets의 TOSS_CLIENT_ID /
TOSS_CLIENT_SECRET)를 사용합니다.

주간 등락률 = (그 주 마지막 거래일 종가 ÷ 직전 주 마지막 거래일 종가 - 1) × 100
"""

import datetime
import os
import re
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


def api_get(path, params=None):
    token = _token(_secret("TOSS_CLIENT_ID"), _secret("TOSS_CLIENT_SECRET"))

    response = None

    for attempt in range(4):
        try:
            response = requests.get(
                f"{BASE}{path}",
                params=params or {},
                headers={"Authorization": f"Bearer {token}"},
                timeout=(6, 15),
            )
        except requests.RequestException as error:
            if attempt == 3:
                raise TossError(f"{path} 연결 실패: {str(error)[-120:]}") from error

            time.sleep(1.5 * (attempt + 1))
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
def fetch_cap_top(day_text):
    """시가총액(현재가 × 발행주식수) 상위 100개 [(종목코드, 종목명)]."""

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


def candle_closes(symbol):
    """[(날짜, 종가)] 오름차순."""

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
            f"토스 일봉에서 주간 등락률을 계산한 종목이 {len(rows)}개뿐입니다. "
            "(최근 2주를 벗어난 주는 조회할 수 없습니다.)"
        )

    ups = sorted((row for row in rows if row["pct"] > 0), key=lambda r: -r["pct"])
    downs = sorted((row for row in rows if row["pct"] < 0), key=lambda r: r["pct"])

    return {"surge": ups[:3], "plunge": downs[:3], "count": len(rows)}
