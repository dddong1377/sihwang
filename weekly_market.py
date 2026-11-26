"""주간 정리용 토스증권 시세 조회.

오후 시황 페이지와 같은 기준(시가총액 상위 100위, 우선주·스팩·ETF 제외)으로
한 주 동안의 등락률을 계산합니다. 토스증권 Open API(Secrets의 TOSS_CLIENT_ID /
TOSS_CLIENT_SECRET)를 사용합니다.

주간 등락률 = (그 주 마지막 거래일 종가 ÷ 직전 주 마지막 거래일 종가 - 1) × 100
"""

import datetime
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








def api_get(path, params=None):
    from toss_client import api_get as oracle_get
    from toss_client import TossClientError

    try:
        return oracle_get(path, params)
    except TossClientError as error:
        raise TossError(str(error)) from error



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

    if len(top) != CAP_RANK_LIMIT:
        raise TossError(
            f"시가총액 분석 대상 부족: "
            f"{len(top)}/{CAP_RANK_LIMIT}종목"
        )

    if len({symbol for symbol, _ in top}) != len(top):
        raise TossError("시총 상위 종목 코드 중복")

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
    """월요일 이전 마지막 유효 종가 대비 주간 누적 수익률."""

    import math

    valid = {}

    for day, close in closes:

        if not isinstance(day, datetime.date):
            continue

        if isinstance(day, datetime.datetime):
            day = day.date()

        try:
            close = float(close)
        except (TypeError, ValueError):
            continue

        if not math.isfinite(close) or close <= 0:
            continue

        if day in valid and valid[day] != close:
            return None

        valid[day] = close

    base = None
    last = None

    for day, close in sorted(valid.items()):

        if day < monday:
            base = (day, close)

        elif monday <= day <= friday and day != skip_today:
            last = (day, close)

    if base is None or last is None:
        return None

    # 지나치게 오래된 기준 종가는 사용하지 않음
    if (monday - base[0]).days > 14:
        return None

    return (
        (last[1] / base[1] - 1) * 100,
        last[0],
    )


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

    if len(rows) != len(top):
        raise TossError(
            f"주간 종목 데이터 미확보: "
            f"{len(rows)}/{len(top)}종목. "
            "전체 순위를 확정할 수 없습니다."
        )

    last_days = {row["last_day"] for row in rows}

    if len(last_days) != 1:
        raise TossError(
            "종목별 최종 종가일 불일치: "
            "주간 순위를 확정할 수 없습니다."
        )

    ups = sorted((row for row in rows if row["pct"] > 0), key=lambda r: -r["pct"])
    downs = sorted((row for row in rows if row["pct"] < 0), key=lambda r: r["pct"])

    return {"surge": ups[:3], "plunge": downs[:3], "count": len(rows)}
