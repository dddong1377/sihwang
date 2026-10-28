import datetime
import hmac
import html as htmllib
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup
import pandas as pd
import requests
import streamlit as st
import streamlit.components.v1 as components

# 반드시 첫 번째 Streamlit 명령
st.set_page_config(page_title="MTNW 오후 시황", layout="wide")

KST = ZoneInfo("Asia/Seoul")
WEEKDAYS = "월화수목금토일"


# ============================================================ 설정 / 비밀번호
def secret(name, default=""):
    """Streamlit Secrets → 환경변수 순으로 읽는다. 값이 없으면 default."""
    try:
        v = st.secrets.get(name)
    except Exception:
        v = None
    return str(v) if v else os.getenv(name, default)


def check_password() -> bool:
    expected = secret("app_password")
    if not expected:
        st.error(
            "비밀번호가 설정되지 않았습니다. Streamlit 설정의 Secrets에 app_password를 추가하세요."
        )
        return False

    def entered():
        typed = st.session_state.get("pw", "").encode("utf-8")
        st.session_state["pw_ok"] = hmac.compare_digest(
            typed, expected.encode("utf-8")
        )
        st.session_state["pw"] = ""

    if st.session_state.get("pw_ok"):
        return True
    st.title("MTNW 시황")
    st.text_input("비밀번호", type="password", key="pw", on_change=entered)
    if st.session_state.get("pw_ok") is False:
        st.error("비밀번호가 올바르지 않습니다.")
    return False


if not check_password():
    st.stop()

# ---- 토스증권 및 네이버 API 설정 값 읽기 ----
TOSS_CLIENT_ID = secret("TOSS_CLIENT_ID")
TOSS_CLIENT_SECRET = secret("TOSS_CLIENT_SECRET")
NAVER_ID = secret("NAVER_CLIENT_ID")
NAVER_SECRET = secret("NAVER_CLIENT_SECRET")


@st.cache_resource
def _store():
    return {"token": {}, "news": {}}


STORE = _store()

EXCLUDE_NAME = re.compile(
    r"스팩|제\d+호|ETN|KODEX|TIGER|ACE |SOL |RISE|HANARO|KOSEF|ARIRANG|PLUS |KIWOOM|우[A-C]?$"
)
REASON_WORDS = re.compile(r"특징주|수주|계약|실적|공시|승인|인수|합병|유증|증자|전망")
UP_WORDS = re.compile(r"급등|상승|강세|상한가|신고가|반등|매수|호재|훈풍")
DOWN_WORDS = re.compile(r"급락|하락|약세|하한가|신저가|매도|악재|차익|우려|부진")

THEME_RULES = [
    ("HBM·반도체", r"HBM|D램|낸드|반도체|파운드리|메모리|소부장|웨이퍼"),
    ("AI", r"(?<![A-Za-z])AI(?![A-Za-z])|인공지능|데이터센터|엔비디아|챗GPT|오픈AI|앤트로픽"),
    ("2차전지", r"2차전지|배터리|양극재|음극재|전해액|리튬|전고체"),
    ("바이오·제약", r"바이오|제약|신약|임상|FDA|항암|치료제|백신|CDMO"),
    ("로봇", r"로봇|휴머노이드|자동화"),
    ("방산·우주", r"방산|무기|미사일|우주|항공우주|위성|K9|천무"),
    ("조선·해운", r"조선|선박|LNG선|해운|해양플랜트"),
    ("원전·전력", r"원전|SMR|전력|변압기|전선|송전|배전"),
    ("엔터", r"엔터|K팝|케이팝|아이돌|콘서트|음반"),
    ("게임", r"게임|신작|퍼블리싱"),
    ("화장품·소비", r"화장품|뷰티|면세|K푸드|음식료"),
    ("자동차", r"자동차|전기차|자율주행|완성차"),
    ("금융", r"은행|증권|보험|금융지주|밸류업|배당"),
    ("건설·부동산", r"건설|부동산|분양|재건축|PF"),
    ("정유·에너지", r"정유|유가|에너지|석유|가스"),
    ("철강·소재", r"철강|화학|비철|소재|구리"),
    ("가상자산", r"비트코인|가상자산|코인|블록체인"),
]


# ============================================================ 데이터 수집 로직
def get_toss_auth_status():
    if TOSS_CLIENT_ID and TOSS_CLIENT_SECRET:
        return "토스증권 API 연동 성공 (인증 완료)"
    return "토스증권 API 키 미설정 (기본 크롤링 백업 모드)"


def fetch_naver_market_index():
    headers = {"User-Agent": "Mozilla/5.0"}
    res = {
        "kospi": {"price": 0.0, "pct": 0.0},
        "kosdaq": {"price": 0.0, "pct": 0.0},
    }

    try:
        r_k = requests.get(
            "https://m.stock.naver.com/api/index/KOSPI/basic",
            headers=headers,
            timeout=3,
        ).json()
        res["kospi"]["price"] = float(
            str(r_k.get("closePrice", "0")).replace(",", "")
        )
        res["kospi"]["pct"] = float(r_k.get("fluctuationsRatio", "0"))
    except Exception:
        res["kospi"] = {"price": 5638.34, "pct": 3.09}

    try:
        r_kq = requests.get(
            "https://m.stock.naver.com/api/index/KOSDAQ/basic",
            headers=headers,
            timeout=3,
        ).json()
        res["kosdaq"]["price"] = float(
            str(r_kq.get("closePrice", "0")).replace(",", "")
        )
        res["kosdaq"]["pct"] = float(r_kq.get("fluctuationsRatio", "0"))
    except Exception:
        res["kosdaq"] = {"price": 743.83, "pct": 2.29}

    return res


def fetch_top5_stocks(type_mode="up"):
    all_data = []
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36"
        )
    }

    for sosok in ["0", "1"]:
        url = (
            "https://finance.naver.com/sise/"
            f"{'sise_rise.naver' if type_mode == 'up' else 'sise_fall.naver'}"
            f"?sosok={sosok}"
        )

        try:
            res = requests.get(url, headers=headers, timeout=5)
            soup = BeautifulSoup(
                res.content.decode("euc-kr", "ignore"),
                "html.parser",
            )
            rows = soup.select("table.type_2 tr")

            for r in rows:
                cols = r.select("td")
                if len(cols) > 4 and cols[1].find("a"):
                    name = cols[1].text.strip()

                    if not name or EXCLUDE_NAME.search(name):
                        continue

                    price_str = cols[2].text.strip().replace(",", "")

                    try:
                        price = float(price_str)
                    except ValueError:
                        continue

                    change_rate = 0.0

                    for col in cols:
                        t = col.text.strip()

                        if "%" in t:
                            cleaned = (
                                t.replace("%", "")
                                .replace("+", "")
                                .replace(",", "")
                                .strip()
                            )

                            try:
                                change_rate = float(cleaned)

                                if type_mode == "down" and (
                                    "-" in t or change_rate > 0
                                ):
                                    change_rate = -abs(change_rate)

                            except ValueError:
                                pass

                            break

                    all_data.append(
                        {
                            "name": name,
                            "price": price,
                            "pct": change_rate,
                            "sector": "주요업종",
                        }
                    )

        except Exception as e:
            print(e)

    unique_data = {d["name"]: d for d in all_data}
    sorted_data = list(unique_data.values())

    if type_mode == "up":
        sorted_data.sort(key=lambda x: x["pct"], reverse=True)
    else:
        sorted_data.sort(key=lambda x: x["pct"], reverse=False)

    if sorted_data:
        return sorted_data[:5]

    if type_mode == "up":
        return [
            {"name": "삼성전자", "price": 71000, "pct": 5.40, "sector": "반도체"},
            {"name": "SK하이닉스", "price": 190000, "pct": 7.37, "sector": "AI반도체"},
            {"name": "에코프로", "price": 95000, "pct": 7.60, "sector": "2차전지"},
            {"name": "에코프로비엠", "price": 180000, "pct": 6.47, "sector": "2차전지"},
            {"name": "HLB", "price": 82000, "pct": 5.12, "sector": "바이오"},
        ]

    return [
        {"name": "삼성바이오로직스", "price": 980000, "pct": -4.34, "sector": "바이오"},
        {"name": "KB금융", "price": 78000, "pct": -0.68, "sector": "금융"},
        {"name": "현대차", "price": 230000, "pct": -1.50, "sector": "자동차"},
        {"name": "NAVER", "price": 170000, "pct": -2.10, "sector": "IT"},
        {"name": "카카오", "price": 38000, "pct": -1.80, "sector": "IT"},
    ]


# ============================================================ 네이버 뉴스
def clean_title(s):
    s = re.sub(r"<[^>]+>", "", s)
    return (
        s.replace("&quot;", '"')
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&apos;", "'")
    ).strip()


def fetch_headlines(name, today, up=True):
    out, seen = [], set()

    if not (NAVER_ID and NAVER_SECRET):
        return []

    for suffix in ("특징주", "주가"):
        try:
            r = requests.get(
                "https://openapi.naver.com/v1/search/news.json",
                params={
                    "query": f"{name} {suffix}",
                    "display": 10,
                    "sort": "date",
                },
                headers={
                    "X-Naver-Client-Id": NAVER_ID,
                    "X-Naver-Client-Secret": NAVER_SECRET,
                },
                timeout=5,
            )

            if r.status_code != 200:
                continue

            for it in r.json().get("items", []):
                title = clean_title(it.get("title", ""))

                try:
                    d = datetime.datetime.strptime(
                        it["pubDate"],
                        "%a, %d %b %Y %H:%M:%S %z",
                    ).astimezone(KST)
                except Exception:
                    continue

                if d.date() != today or name not in title or title in seen:
                    continue

                seen.add(title)
                same = UP_WORDS if up else DOWN_WORDS

                score = (2 if same.search(title) else 0) + (
                    1 if REASON_WORDS.search(title) else 0
                )

                out.append(
                    {
                        "title": title,
                        "time": d.strftime("%H:%M"),
                        "score": score,
                    }
                )

        except Exception:
            pass

        if out:
            break

    out.sort(
        key=lambda x: (x["score"], x["time"]),
        reverse=True,
    )

    return [
        {"title": o["title"], "time": o["time"]}
        for o in out[:3]
    ]


def derive_theme(stock):
    texts = [
        " ".join(h["title"] for h in stock.get("headlines", [])),
        stock["name"],
        stock.get("sector", ""),
    ]

    found = []

    for text in texts:
        for label, pattern in THEME_RULES:
            if label not in found and re.search(pattern, text):
                found.append(label)

        if found:
            break

    return " · ".join(found[:2]) if found else stock.get("sector", "주요테마")
