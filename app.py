import datetime
import hmac
import html as htmllib
import json
import os
import re
import time
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup
import requests
import streamlit as st
import streamlit.components.v1 as components

# 반드시 첫 번째 Streamlit 명령
st.set_page_config(page_title="MTNW 오후 시황", layout="wide")

KST = ZoneInfo("Asia/Seoul")
WEEKDAYS = "월화수목금토일"


# ============================================================
# 설정 / 비밀번호
# ============================================================

def secret(name, default=""):
    """Streamlit Secrets → 환경변수 순으로 읽습니다."""
    try:
        value = st.secrets.get(name)
    except Exception:
        value = None

    return str(value) if value else os.getenv(name, default)


def check_password() -> bool:
    expected = secret("app_password")

    if not expected:
        st.error(
            "비밀번호가 설정되지 않았습니다. "
            "Streamlit 설정의 Secrets에 app_password를 추가하세요."
        )
        return False

    def entered():
        typed = st.session_state.get("pw", "").encode("utf-8")

        st.session_state["pw_ok"] = hmac.compare_digest(
            typed,
            expected.encode("utf-8"),
        )

        st.session_state["pw"] = ""

    if st.session_state.get("pw_ok"):
        return True

    st.title("MTNW 시황")
    st.text_input(
        "비밀번호",
        type="password",
        key="pw",
        on_change=entered,
    )

    if st.session_state.get("pw_ok") is False:
        st.error("비밀번호가 올바르지 않습니다.")

    return False


if not check_password():
    st.stop()


# ============================================================
# API 설정
# ============================================================

TOSS_CLIENT_ID = secret("TOSS_CLIENT_ID")
TOSS_CLIENT_SECRET = secret("TOSS_CLIENT_SECRET")

NAVER_ID = secret("NAVER_CLIENT_ID")
NAVER_SECRET = secret("NAVER_CLIENT_SECRET")


# ============================================================
# 종목 필터 / 뉴스 분석 설정
# ============================================================

EXCLUDE_NAME = re.compile(
    r"스팩|제\d+호|ETN|KODEX|TIGER|ACE |SOL |RISE|"
    r"HANARO|KOSEF|ARIRANG|PLUS |KIWOOM|우[A-C]?$"
)

REASON_WORDS = re.compile(
    r"특징주|수주|계약|실적|공시|승인|인수|합병|유증|증자|전망"
)

UP_WORDS = re.compile(
    r"급등|상승|강세|상한가|신고가|반등|매수|호재|훈풍"
)

DOWN_WORDS = re.compile(
    r"급락|하락|약세|하한가|신저가|매도|악재|차익|우려|부진"
)

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


# ============================================================
# 데이터 수집 로직
# ============================================================

def get_toss_auth_status():
    """
    현재 시세 수집은 네이버 금융을 사용합니다.
    토스 API를 실제로 호출하지 않으므로 연동 성공으로 표시하지 않습니다.
    """
    return (
        "시세 출처: 네이버 금융 · "
        "토스 API는 현재 시세 수집에 사용하지 않음"
    )


def _to_float(value):
    """숫자 문자열을 float로 변환합니다."""
    if value is None:
        raise ValueError("값이 없습니다.")

    text = (
        str(value)
        .strip()
        .replace(",", "")
        .replace("%", "")
        .replace("+", "")
    )

    if not text or text in {"-", "N/A", "None"}:
        raise ValueError(f"숫자로 변환할 수 없는 값: {value!r}")

    return float(text)


# ============================================================
# 코스피 / 코스닥 조회
# ============================================================

def fetch_naver_market_index():
    """
    네이버 모바일 지수 API에서 지수를 조회합니다.
    조회 실패 시 임의의 숫자를 넣지 않습니다.
    """

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 "
            "Chrome/131.0.0.0 Safari/537.36"
        )
    }

    result = {}

    for key, api_name in (
        ("kospi", "KOSPI"),
        ("kosdaq", "KOSDAQ"),
    ):
        url = (
            f"https://m.stock.naver.com/api/index/"
            f"{api_name}/basic"
        )

        try:
            response = requests.get(
                url,
                headers=headers,
                timeout=8,
            )

            response.raise_for_status()
            data = response.json()

            price = _to_float(data.get("closePrice"))
            pct = _to_float(data.get("fluctuationsRatio"))

            if price <= 0 or not (-100 <= pct <= 100):
                raise ValueError(
                    f"비정상 지수 응답: price={price}, pct={pct}"
                )

            result[key] = {
                "price": price,
                "pct": pct,
                "available": True,
            }

        except (
            requests.RequestException,
            ValueError,
            TypeError,
            KeyError,
        ) as exc:
            print(f"[지수 조회 실패] {api_name}: {exc}")

            result[key] = {
                "price": None,
                "pct": None,
                "available": False,
            }

        except Exception as exc:
            print(f"[지수 처리 오류] {api_name}: {exc}")

            result[key] = {
                "price": None,
                "pct": None,
                "available": False,
            }

    return result


# ============================================================
# 급등 / 급락 종목 TOP 5 조회
# ============================================================

def fetch_top5_stocks(type_mode="up"):
    """
    네이버 금융 상승 / 하락 순위에서 실제 수집한 데이터만 반환합니다.

    중요:
    - 고정 종목과 고정 가격을 대체 데이터로 사용하지 않습니다.
    - 종목명, 현재가, 등락률을 각각 확인합니다.
    - 상승 / 하락 방향과 실제 등락률이 일치하는지 검사합니다.
    """

    if type_mode not in ("up", "down"):
        raise ValueError(f"잘못된 조회 유형: {type_mode}")

    all_data = []

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 "
            "Chrome/131.0.0.0 Safari/537.36"
        ),
        "Referer": "https://finance.naver.com/",
    }

    page_name = (
        "sise_rise.naver"
        if type_mode == "up"
        else "sise_fall.naver"
    )

    # 0: 코스피, 1: 코스닥
    for sosok in ("0", "1"):

        url = (
            f"https://finance.naver.com/sise/"
            f"{page_name}?sosok={sosok}"
        )

        try:
            response = requests.get(
                url,
                headers=headers,
                timeout=10,
            )

            response.raise_for_status()

            soup = BeautifulSoup(
                response.content,
                "html.parser",
                from_encoding="euc-kr",
            )

            rows = soup.select("table.type_2 tr")

            if not rows:
                print(
                    f"[종목 조회 실패] "
                    f"유형={type_mode}, 시장={sosok}: "
                    "종목 테이블을 찾지 못했습니다."
                )
                continue

            for row in rows:
                cols = row.select("td")

                # 네이버 금융 표의 예상 열:
                # 종목명 = td[1]
                # 현재가 = td[2]
                # 등락률 = td[4]
                if len(cols) < 5:
                    continue

                name_tag = cols[1].select_one("a")

                if not name_tag:
                    continue

                name = name_tag.get_text(strip=True)

                if not name or EXCLUDE_NAME.search(name):
                    continue

                price_text = cols[2].get_text(strip=True)
                pct_text = cols[4].get_text(strip=True)

                try:
                    price = _to_float(price_text)
                    pct = _to_float(pct_text)

                except (ValueError, TypeError) as exc:
                    print(
                        f"[종목 파싱 실패] {name}: "
                        f"현재가={price_text!r}, "
                        f"등락률={pct_text!r}, "
                        f"오류={exc}"
                    )
                    continue

                # 비정상적인 데이터 제외
                if price <= 0 or not (-100 <= pct <= 1000):
                    print(
                        f"[비정상 종목 데이터 제외] {name}: "
                        f"price={price}, pct={pct}"
                    )
                    continue

                # 상승 종목은 실제 양수 등락률만 허용
                if type_mode == "up" and pct <= 0:
                    continue

                # 하락 종목은 실제 음수 등락률만 허용
                if type_mode == "down" and pct >= 0:
                    continue

                all_data.append(
                    {
                        "name": name,
                        "price": price,
                        "pct": pct,
                        "sector": "주요업종",
                    }
                )

        except requests.RequestException as exc:
            print(
                f"[네이버 금융 요청 실패] "
                f"유형={type_mode}, 시장={sosok}: {exc}"
            )

        except Exception as exc:
            print(
                f"[종목 데이터 처리 실패] "
                f"유형={type_mode}, 시장={sosok}: {exc}"
            )

    # 같은 종목이 중복 수집되면 등락률 기준으로 하나만 유지
    unique = {}

    for item in all_data:
        old = unique.get(item["name"])

        if old is None:
            unique[item["name"]] = item

        elif type_mode == "up" and item["pct"] > old["pct"]:
            unique[item["name"]] = item

        elif type_mode == "down" and item["pct"] < old["pct"]:
            unique[item["name"]] = item

    stocks = list(unique.values())

    # 등락률 기준 정렬
    stocks.sort(
        key=lambda item: item["pct"],
        reverse=(type_mode == "up"),
    )

    result = stocks[:5]

    print(
        f"[종목 조회 완료] 유형={type_mode}, "
        f"수집={len(stocks)}개, 표시={len(result)}개"
    )

    # 조회에 실패했을 때 임의 종목을 반환하지 않습니다.
    return result


# ============================================================
# 네이버 뉴스
# ============================================================

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
    """
    네이버 뉴스 검색 API를 이용해 당일 관련 기사를 조회합니다.
    API 키가 없거나 기사를 찾지 못하면 빈 목록을 반환합니다.
    """

    out = []
    seen = set()

    if not (NAVER_ID and NAVER_SECRET):
        return []

    for suffix in ("특징주", "주가"):

        try:
            response = requests.get(
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

            if response.status_code != 200:
                print(
                    f"[뉴스 조회 실패] "
                    f"{name}: HTTP {response.status_code}"
                )
                continue

            items = response.json().get("items", [])

            for item in items:
                title = clean_title(item.get("title", ""))

                try:
                    published = datetime.datetime.strptime(
                        item["pubDate"],
                        "%a, %d %b %Y %H:%M:%S %z",
                    ).astimezone(KST)

                except Exception:
                    continue

                # 오늘 게시된 기사이며 종목명이 제목에 포함된 경우만 사용
                if published.date() != today:
                    continue

                if name not in title or title in seen:
                    continue

                seen.add(title)

                direction_words = UP_WORDS if up else DOWN_WORDS

                score = (
                    (2 if direction_words.search(title) else 0)
                    + (1 if REASON_WORDS.search(title) else 0)
                )

                out.append(
                    {
                        "title": title,
                        "time": published.strftime("%H:%M"),
                        "score": score,
                    }
                )

        except Exception as exc:
            print(f"[뉴스 처리 오류] {name}: {exc}")

        if out:
            break

    out.sort(
        key=lambda item: (item["score"], item["time"]),
        reverse=True,
    )

    return [
        {
            "title": item["title"],
            "time": item["time"],
        }
        for item in out[:3]
    ]


# ============================================================
# 테마 분석
# ============================================================

def derive_theme(stock):
    texts = [
        " ".join(
            headline["title"]
            for headline in stock.get("headlines", [])
        ),
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

    if found:
        return " · ".join(found[:2])

    return stock.get("sector", "주요테마")


def auto_reason(stock):
    headlines = stock.get("headlines") or []

    if headlines:
        return headlines[0]["title"]

    # 뉴스가 없으면 상승 / 하락 이유를 임의로 단정하지 않습니다.
    if stock["pct"] > 0:
        return "관련 기사 확인 필요"

    return "하락 원인 확인 필요"


# ============================================================
# 전체 시황 데이터 수집
# ============================================================

def fetch_snapshot():
    now = datetime.datetime.now(KST)
    today = now.date()

    indices = fetch_naver_market_index()

    gainers = fetch_top5_stocks("up")
    losers = fetch_top5_stocks("down")

    for stock in gainers + losers:
        stock["headlines"] = fetch_headlines(
            stock["name"],
            today,
            stock["pct"] > 0,
        )

        stock["theme"] = derive_theme(stock)

    return {
        "ts": time.time(),
        "fetched_at": now.strftime("%H:%M:%S"),
        "date": (
            f"{now.year}년 {now.month:02d}월 "
            f"{now.day:02d}일 ({WEEKDAYS[now.weekday()]})"
        ),
        "kospi": indices["kospi"],
        "kosdaq": indices["kosdaq"],
        "gainers": gainers,
        "losers": losers,
        "toss_status": get_toss_auth_status(),
    }


# ============================================================
# 시황글 생성 도우미
# ============================================================

def fmt(value, decimals=2):
    return f"{value:,.{decimals}f}"


def sgn(value):
    return f"{value:+,.2f}%"


def em(value):
    if value > 0:
        return "🔺"

    if value < 0:
        return "🔻"

    return "➖"


# ============================================================
# 시황글 마크다운 생성
# ============================================================

def build_md(snap):
    ss = st.session_state
    tm = snap["fetched_at"][:5]

    lines = [
        f"# 📅 {snap['date']} | 오후 시황",
        "",
    ]

    summary = ss.get(
        "summary",
        "AI 투자심리 개선에 반도체주 강세, 장중 변동성 주의",
    ).strip()

    if summary:
        lines += [
            f"📌 **한 줄 요약**: {summary}",
            "",
        ]

    intro = ss.get(
        "intro_text",
        (
            "오늘 국내 증시는 미국 AI 관련 투자심리 개선과 "
            "외국인·기관 매수세에 힘입어 강세를 보이고 있습니다. "
            "다만 장중 지수와 종목별 등락은 시시각각 변하므로, "
            "조회 시각을 기준으로 정리했습니다."
        ),
    ).strip()

    if intro:
        lines += [intro, ""]

    # --------------------------------------------------------
    # 국내 지수
    # --------------------------------------------------------

    lines += [
        "### 🇰🇷 장중 지수 현황",
        "",
        "| 지수 | 현재가 | 등락 | 기준 시각 |",
        "|---|---:|---:|---|",
    ]

    for index_key, index_name in (
        ("kospi", "코스피"),
        ("kosdaq", "코스닥"),
    ):
        index_data = snap[index_key]

        if (
            index_data.get("available")
            and index_data.get("price") is not None
            and index_data.get("pct") is not None
        ):
            price_text = fmt(index_data["price"])
            change_text = (
                f"{em(index_data['pct'])} "
                f"{sgn(index_data['pct'])}"
            )

        else:
            price_text = "조회 실패"
            change_text = "-"

        lines.append(
            f"| {index_name} | {price_text} | "
            f"{change_text} | {tm} |"
        )

    # --------------------------------------------------------
    # 원/달러 환율
    # --------------------------------------------------------

    try:
        fx_price_text = (
            str(ss.get("fx_price", ""))
            .strip()
            .replace(",", "")
        )

        fx_pct_text = (
            str(ss.get("fx_pct", ""))
            .strip()
            .replace(",", "")
        )

        if fx_price_text and fx_pct_text:
            fx_price = float(fx_price_text)
            fx_pct = float(fx_pct_text)

            if fx_price > 0 and -100 <= fx_pct <= 100:
                lines.append(
                    f"| 원/달러 | {fmt(fx_price)}원 | "
                    f"{em(fx_pct)} {sgn(fx_pct)} | {tm} |"
                )
            else:
                lines.append(
                    f"| 원/달러 | 입력값 확인 필요 | - | {tm} |"
                )

        else:
            lines.append(
                f"| 원/달러 | 직접 입력 필요 | - | {tm} |"
            )

    except (ValueError, TypeError):
        lines.append(
            f"| 원/달러 | 입력값 확인 필요 | - | {tm} |"
        )

    lines += ["", ""]

    # --------------------------------------------------------
    # 급등 특징주
    # --------------------------------------------------------

    lines += [
        "### 🔺 급등 특징주",
        "",
        "| 종목 | 등락률 | 상승 이유 | 관련 테마 |",
        "|---|---:|---|---|",
    ]

    if snap["gainers"]:
        for stock in snap["gainers"]:
            reason = (
                ss.get(f"reason_{stock['name']}")
                or auto_reason(stock)
            ).replace("|", "/")

            theme = (
                ss.get(f"theme_{stock['name']}")
                or stock.get("theme", "")
            ).replace("|", "/")

            lines.append(
                f"| **{stock['name']}** | "
                f"**{sgn(stock['pct'])}** | "
                f"{reason} | {theme} |"
            )

    else:
        lines.append(
            "| 조회 결과 없음 | - | 실제 시세 데이터를 "
            "가져오지 못했습니다. | - |"
        )

    lines += ["", ""]

    # --------------------------------------------------------
    # 급락 특징주
    # --------------------------------------------------------

    lines += [
        "### 🔻 급락 특징주",
        "",
        "| 종목 | 등락률 | 하락 이유 | 관련 테마 |",
        "|---|---:|---|---|",
    ]

    if snap["losers"]:
        for stock in snap["losers"]:
            reason = (
                ss.get(f"reason_{stock['name']}")
                or auto_reason(stock)
            ).replace("|", "/")

            theme = (
                ss.get(f"theme_{stock['name']}")
                or stock.get("theme", "")
            ).replace("|", "/")

            lines.append(
                f"| **{stock['name']}** | "
                f"**{sgn(stock['pct'])}** | "
                f"{reason} | {theme} |"
            )

    else:
        lines.append(
            "| 조회 결과 없음 | - | 실제 시세 데이터를 "
            "가져오지 못했습니다. | - |"
        )

    lines += ["", ""]

    # --------------------------------------------------------
    # 주요 경제 이슈
    # --------------------------------------------------------

    lines += [
        "### 🔥 오늘의 주요 경제 이슈",
        "",
    ]

    for i in (1, 2, 3):
        title = ss.get(f"is{i}t", "").strip()
        direction = ss.get(f"is{i}m", "🔺")
        content = ss.get(f"is{i}c", "").strip()
        related_stocks = ss.get(f"is{i}s", "").strip()
        check_point = ss.get(f"is{i}k", "").strip()

        number = (
            "①" if i == 1
            else "②" if i == 2
            else "③"
        )

        lines.append(f"**{number} {direction} {title}**")

        if content:
            lines.append(f"- 내용: {content}")

        if related_stocks:
            names = ", ".join(
                f"**{name.strip()}**"
                for name in re.split(r"[,，]", related_stocks)
                if name.strip()
            )

            lines.append(f"- 📌 관련 종목: {names}")

        if check_point:
            lines.append(f"- ⏰ 체크: {check_point}")

        lines.append("")

    # --------------------------------------------------------
    # 주요 테마
    # --------------------------------------------------------

    themes = ss.get(
        "themes",
        "AI 반도체 · 반도체 소부장 · 2차전지",
    ).strip()

    if themes:
        formatted_themes = " · ".join(
            f"**{theme.strip()}**"
            for theme in re.split(r"[·,]", themes)
            if theme.strip()
        )

        lines += [
            "### 🏷️ 오늘 강했던 테마",
            "",
            formatted_themes,
            "",
        ]

    # --------------------------------------------------------
    # 체크 포인트
    # --------------------------------------------------------

    checks = ss.get(
        "checks",
        "삼성전자·SK하이닉스 상승세 지속 여부 / "
        "반도체 관련주 강세 유지 여부",
    ).strip()

    if checks:
        lines += [
            "### 👀 내일 체크 포인트",
            "",
        ]

        lines += [
            f"- {item.strip()}"
            for item in checks.split("/")
            if item.strip()
        ]

        lines.append("")

    # --------------------------------------------------------
    # 핵심 키워드
    # --------------------------------------------------------

    keywords = ss.get(
        "keywords",
        "#코스피 #코스닥 #AI #삼성전자 "
        "#SK하이닉스 #반도체 #2차전지",
    ).strip()

    if keywords:
        lines += [
            "### 🔍 핵심 키워드 10",
            "",
            keywords,
            "",
        ]

    # --------------------------------------------------------
    # 주의 문구
    # --------------------------------------------------------

    caution = ss.get(
        "caution",
        "급등에 따른 차익실현과 종목별 변동성 확대에 유의하세요.",
    ).strip()

    if caution:
        lines += [
            "### ⚠️ 한 줄 주의",
            "",
            caution,
            "",
        ]

    return "\n".join(lines)


# ============================================================
# 마크다운 → HTML 변환
# ============================================================

def _inline(text):
    escaped = htmllib.escape(text)
    return re.sub(
        r"\*\*(.+?)\*\*",
        r"<b>\1</b>",
        escaped,
    )


def md_to_html(md):
    output = []
    table_open = False
    list_open = False

    for line in md.split("\n"):

        if line.startswith("|"):

            if list_open:
                output.append("</ul>")
                list_open = False

            cells = [
                cell.strip()
                for cell in line.split("|")[1:-1]
            ]

            # 마크다운 표 구분선
            if all(
                re.fullmatch(r":?-+:?", cell)
                for cell in cells
            ):
                continue

            if not table_open:
                table_open = True

                output.append(
                    '<table border="1" cellspacing="0" cellpadding="6" '
                    'style="border-collapse:collapse">'
                    "<thead><tr>"
                    + "".join(
                        f"<th>{_inline(cell)}</th>"
                        for cell in cells
                    )
                    + "</tr></thead><tbody>"
                )

            else:
                output.append(
                    "<tr>"
                    + "".join(
                        f"<td>{_inline(cell)}</td>"
                        for cell in cells
                    )
                    + "</tr>"
                )

            continue

        if table_open:
            output.append("</tbody></table>")
            table_open = False

        if line.startswith("- "):

            if not list_open:
                output.append("<ul>")
                list_open = True

            output.append(
                f"<li>{_inline(line[2:])}</li>"
            )

            continue

        if list_open:
            output.append("</ul>")
            list_open = False

        if line.startswith("# "):
            output.append(
                f"<h1>{_inline(line[2:])}</h1>"
            )

        elif line.startswith("### "):
            output.append(
                f"<h3>{_inline(line[4:])}</h3>"
            )

        elif line.startswith("> "):
            output.append(
                f"<blockquote>{_inline(line[2:])}</blockquote>"
            )

        elif line.strip():
            output.append(
                f"<p>{_inline(line)}</p>"
            )

    if list_open:
        output.append("</ul>")

    if table_open:
        output.append("</tbody></table>")

    return "\n".join(output)


# ============================================================
# 복사 버튼
# ============================================================

COPY_HTML = """
<div style="display:flex;gap:8px;align-items:center;font-family:sans-serif;font-size:13px">
  <button id="r" style="padding:8px 14px;border-radius:6px;border:0;background:#0f5c5c;color:#fff;font-weight:600;cursor:pointer">복사 (서식 포함)</button>
  <button id="m" style="padding:8px 14px;border-radius:6px;border:1px solid #bbb;background:#fff;cursor:pointer">복사 (마크다운)</button>
  <span id="t" style="color:#555"></span>
</div>
<script>
const P = __PAYLOAD__;

const msg = s => {
    document.getElementById('t').textContent = s;
};

function copyText(text) {
    const ta = document.createElement('textarea');
    ta.value = text;
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand('copy');
    ta.remove();
    return ok;
}

function copyRichFallback(html) {
    const d = document.createElement('div');
    d.innerHTML = html;
    d.style.position = 'fixed';
    d.style.left = '-9999px';
    document.body.appendChild(d);

    const r = document.createRange();
    r.selectNodeContents(d);

    const s = window.getSelection();
    s.removeAllRanges();
    s.addRange(r);

    const ok = document.execCommand('copy');

    s.removeAllRanges();
    d.remove();

    return ok;
}

document.getElementById('r').onclick = async () => {
    try {
        if (
            window.ClipboardItem &&
            navigator.clipboard &&
            navigator.clipboard.write
        ) {
            await navigator.clipboard.write([
                new ClipboardItem({
                    'text/html': new Blob(
                        [P.html],
                        {type: 'text/html'}
                    ),
                    'text/plain': new Blob(
                        [P.md],
                        {type: 'text/plain'}
                    )
                })
            ]);

            msg('서식 포함으로 복사했습니다');
            return;
        }
    } catch (e) {}

    msg(
        copyRichFallback(P.html)
            ? '서식 포함으로 복사했습니다'
            : '복사 실패'
    );
};

document.getElementById('m').onclick = async () => {
    try {
        await navigator.clipboard.writeText(P.md);
        msg('마크다운으로 복사했습니다');
        return;
    } catch (e) {}

    msg(
        copyText(P.md)
            ? '마크다운으로 복사했습니다'
            : '복사 실패'
    );
};
</script>
"""


def copy_buttons(md):
    payload = json.dumps(
        {
            "md": md,
            "html": md_to_html(md),
        },
        ensure_ascii=False,
    ).replace("</", "<\\/")

    page = COPY_HTML.replace(
        "__PAYLOAD__",
        payload,
    )

    if hasattr(st, "iframe"):
        st.iframe(page, height=56)
    else:
        components.html(page, height=56)


# ============================================================
# 입력 상태 관리
# ============================================================

def touch(key):
    st.session_state.setdefault(
        "touched",
        set(),
    ).add(key)


def colored(value, text):
    if value > 0:
        return f":red[{text}]"

    if value < 0:
        return f":blue[{text}]"

    return text


def apply_defaults(snap):
    """
    새로 조회한 종목의 이유 / 테마 기본값을 설정합니다.
    사용자가 직접 수정한 값은 덮어쓰지 않습니다.
    """

    ss = st.session_state
    touched = ss.setdefault("touched", set())

    for stock in snap["gainers"] + snap["losers"]:

        defaults = (
            ("reason", auto_reason(stock)),
            ("theme", stock.get("theme", "")),
        )

        for field, value in defaults:
            key = f"{field}_{stock['name']}"

            if key not in touched:
                ss[key] = value


# ============================================================
# 종목 표
# ============================================================

def stock_table(title, stocks, word):
    st.subheader(title)

    if not stocks:
        st.warning(
            "실제 시세 데이터를 가져오지 못했거나 "
            "조건에 맞는 종목이 없습니다. "
            "배포 환경 로그를 확인해 주세요."
        )
        return

    widths = [2.2, 1.2, 1.3, 4.5, 2.2]
    headers = st.columns(widths)

    labels = [
        "종목",
        "현재가",
        "등락률",
        f"{word} 이유",
        "관련 테마",
    ]

    for column, label in zip(headers, labels):
        column.caption(label)

    for stock in stocks:
        columns = st.columns(widths)

        columns[0].markdown(
            f"**{stock['name']}**"
        )

        columns[1].markdown(
            f"{stock['price']:,.0f}"
        )

        columns[2].markdown(
            colored(
                stock["pct"],
                f"{em(stock['pct'])} {sgn(stock['pct'])}",
            )
        )

        reason_key = f"reason_{stock['name']}"
        theme_key = f"theme_{stock['name']}"

        columns[3].text_input(
            "이유",
            key=reason_key,
            label_visibility="collapsed",
            on_change=touch,
            args=(reason_key,),
        )

        columns[4].text_input(
            "테마",
            key=theme_key,
            label_visibility="collapsed",
            on_change=touch,
            args=(theme_key,),
        )


# ============================================================
# 시황글 추가 입력
# ============================================================

def manual_inputs():

    with st.expander(
        "📝 시황글 추가 내용 편집 (요약, 이슈, 테마 등)",
        expanded=True,
    ):

        st.text_input(
            "한 줄 요약",
            key="summary",
            value=(
                "AI 투자심리 개선에 반도체주 강세, "
                "장중 변동성 주의"
            ),
        )

        st.text_area(
            "인트로 문구",
            key="intro_text",
            value=(
                "오늘 국내 증시는 미국 AI 관련 투자심리 개선과 "
                "외국인·기관 매수세에 힘입어 강세를 보이고 있습니다. "
                "다만 장중 지수와 종목별 등락은 시시각각 변하므로, "
                "조회 시각을 기준으로 정리했습니다."
            ),
        )

        for i in (1, 2, 3):

            st.markdown(f"**이슈 {i}**")

            a, b, c_col = st.columns([1, 3, 3])

            defaults_m = [
                "🔺",
                "🔺",
                "➖",
            ]

            defaults_t = [
                "AI 수익성 우려 완화",
                "코스피 매수 사이드카 발동",
                "반도체·2차전지 동반 강세",
            ]

            defaults_s = [
                "삼성전자, SK하이닉스, SK스퀘어",
                "삼성전자, SK하이닉스, 삼성전기",
                "심텍, 원익IPS, 에코프로비엠",
            ]

            a.selectbox(
                "방향",
                ["🔺", "🔻", "➖"],
                index=["🔺", "🔻", "➖"].index(
                    defaults_m[i - 1]
                ),
                key=f"is{i}m",
                label_visibility="collapsed",
            )

            b.text_input(
                "제목",
                key=f"is{i}t",
                value=defaults_t[i - 1],
                label_visibility="collapsed",
            )

            c_col.text_input(
                "관련 종목",
                key=f"is{i}s",
                value=defaults_s[i - 1],
                label_visibility="collapsed",
            )

            defaults_c = [
                "앤트로픽 IPO 추진과 매출 성장 소식이 투자심리 개선에 영향",
                "외국인·기관 매수세에 코스피가 장중 4%대 상승",
                "반도체 소부장과 2차전지 관련주로 매수세 확산",
            ]

            defaults_k = [
                "미국 빅테크 실적과 AI 투자 지속성",
                "급등 이후 차익실현 및 변동성 확대 가능성",
                "업종별 상승세 지속 여부",
            ]

            st.text_input(
                "내용",
                key=f"is{i}c",
                value=defaults_c[i - 1],
                label_visibility="collapsed",
            )

            st.text_input(
                "체크",
                key=f"is{i}k",
                value=defaults_k[i - 1],
                label_visibility="collapsed",
            )

        st.text_input(
            "강했던 테마",
            key="themes",
            value=(
                "AI 반도체 · 반도체 소부장 · "
                "반도체 지주사 · 2차전지 · 로봇"
            ),
        )

        st.text_input(
            "내일 체크 포인트 (/로 구분)",
            key="checks",
            value=(
                "삼성전자·SK하이닉스 상승세 지속 여부 / "
                "SK스퀘어·심텍 등 반도체 관련주 강세 유지 여부 / "
                "에코프로·에코프로비엠 2차전지주 수급 변화"
            ),
        )

        st.text_input(
            "핵심 키워드",
            key="keywords",
            value=(
                "#코스피 #코스닥 #AI #앤트로픽 "
                "#삼성전자 #SK하이닉스 #SK스퀘어 "
                "#반도체 #2차전지 #외국인수급"
            ),
        )

        st.text_input(
            "한 줄 주의",
            key="caution",
            value=(
                "급등에 따른 차익실현과 "
                "종목별 변동성 확대에 유의하세요."
            ),
        )


# ============================================================
# 메인 화면
# ============================================================

def main():

    st.title("MTNW 오후 시황")

    with st.sidebar:

        st.header("설정")

        auto = st.selectbox(
            "자동 갱신",
            [0, 30, 60, 120],
            format_func=lambda value: (
                "끔" if value == 0 else f"{value}초"
            ),
        )

        if st.button("로그아웃"):
            st.session_state["pw_ok"] = False
            st.rerun()

    @st.fragment(
        run_every=auto if auto else None
    )
    def body():

        ss = st.session_state

        clicked = st.button(
            "지금 시세 조회",
            type="primary",
        )

        snap = ss.get("snap")

        due = (
            bool(auto)
            and snap is not None
            and time.time() - snap["ts"] >= auto - 1
        )

        if clicked or due or not snap:

            try:
                with st.spinner("시세 조회 중..."):
                    snap = fetch_snapshot()

                ss["snap"] = snap
                apply_defaults(snap)

            except Exception as exc:
                st.error(f"조회 실패: {exc}")

        snap = ss.get("snap")

        if not snap:
            st.info(
                "[지금 시세 조회] 버튼을 눌러 "
                "시세를 불러오세요."
            )
            return

        st.caption(
            f"{snap['date']} · "
            f"조회 {snap['fetched_at']} · "
            f"{snap['toss_status']}"
        )

        # ----------------------------------------------------
        # 지수 표시
        # ----------------------------------------------------

        c1, c2, c3 = st.columns(3)

        if snap["kospi"].get("available"):

            c1.markdown(
                f"**코스피**  \n"
                f"### {fmt(snap['kospi']['price'])}"
            )

            c1.markdown(
                colored(
                    snap["kospi"]["pct"],
                    f"{em(snap['kospi']['pct'])} "
                    f"{sgn(snap['kospi']['pct'])}",
                )
            )

        else:
            c1.markdown(
                "**코스피**  \n### 조회 실패"
            )

        if snap["kosdaq"].get("available"):

            c2.markdown(
                f"**코스닥**  \n"
                f"### {fmt(snap['kosdaq']['price'])}"
            )

            c2.markdown(
                colored(
                    snap["kosdaq"]["pct"],
                    f"{em(snap['kosdaq']['pct'])} "
                    f"{sgn(snap['kosdaq']['pct'])}",
                )
            )

        else:
            c2.markdown(
                "**코스닥**  \n### 조회 실패"
            )

        # ----------------------------------------------------
        # 환율 입력
        # ----------------------------------------------------

        c3.markdown("**원/달러 환율**")

        f1, f2 = c3.columns(2)

        f1.text_input(
            "환율(원)",
            key="fx_price",
            placeholder="직접 입력",
            label_visibility="collapsed",
        )

        f2.text_input(
            "등락률(%)",
            key="fx_pct",
            placeholder="직접 입력",
            label_visibility="collapsed",
        )

        # ----------------------------------------------------
        # 종목 표
        # ----------------------------------------------------

        stock_table(
            "🔺 급등 특징주 TOP 5",
            snap["gainers"],
            "상승",
        )

        stock_table(
            "🔻 급락 특징주 TOP 5",
            snap["losers"],
            "하락",
        )

        # ----------------------------------------------------
        # 시황글 작성 및 미리보기
        # ----------------------------------------------------

        manual_inputs()

        st.subheader("시황글 미리보기")

        md = build_md(snap)

        copy_buttons(md)

        with st.container(border=True):
            st.markdown(md)

        with st.expander("마크다운 원문 보기"):
            st.code(
                md,
                language="markdown",
            )

    body()


# ============================================================
# 실행
# ============================================================

main()
