import datetime
import hmac
import html as htmllib
import json
import os
import re
import time
from zoneinfo import ZoneInfo

import requests
import streamlit as st
import streamlit.components.v1 as components


# ============================================================
# 기본 설정
# ============================================================

st.set_page_config(
    page_title="MTNW 오후 시황",
    layout="wide",
)

KST = ZoneInfo("Asia/Seoul")
WEEKDAYS = "월화수목금토일"

NAVER_API = "https://m.stock.naver.com/front-api"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Referer": "https://m.stock.naver.com/",
    "Accept": "application/json, text/plain, */*",
}

EXCLUDE_NAME = re.compile(
    r"스팩|제\d+호|ETN|KODEX|TIGER|ACE|SOL|RISE|HANARO|"
    r"KOSEF|ARIRANG|PLUS|KIWOOM|우[A-C]?$"
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
# 설정 / 비밀번호
# ============================================================

def secret(name, default=""):
    try:
        value = st.secrets.get(name)
    except Exception:
        value = None

    return str(value) if value else os.getenv(name, default)


def check_password():
    expected = secret("app_password")

    if not expected:
        st.error(
            "비밀번호가 설정되지 않았습니다. "
            "Streamlit Secrets에 app_password를 추가하세요."
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

NAVER_ID = secret("NAVER_CLIENT_ID")
NAVER_SECRET = secret("NAVER_CLIENT_SECRET")

# requests.Session을 재사용합니다.
SESSION = requests.Session()
SESSION.headers.update(HEADERS)


# ============================================================
# 공통 데이터 처리
# ============================================================

def to_number(value):
    """쉼표, 퍼센트 기호 등을 제거하고 숫자로 변환합니다."""

    if value is None:
        return None

    if isinstance(value, (int, float)):
        return float(value)

    value = str(value).strip()

    if not value or value in ("-", "--", "N/A", "null"):
        return None

    value = (
        value.replace(",", "")
        .replace("%", "")
        .replace("+", "")
        .strip()
    )

    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def request_json(url, params=None, timeout=10):
    """JSON 응답을 요청하고 HTTP 및 데이터 오류를 구분합니다."""

    response = SESSION.get(
        url,
        params=params,
        timeout=timeout,
    )

    response.raise_for_status()

    try:
        return response.json()
    except ValueError as exc:
        raise ValueError(
            f"JSON 응답이 아닙니다: {response.url}"
        ) from exc


# ============================================================
# 코스피 / 코스닥 지수
# ============================================================

def fetch_one_index(code):
    """
    네이버 증권의 지수 기본 정보를 조회합니다.

    조회 실패 시 임의의 기본값을 반환하지 않습니다.
    """

    url = f"{NAVER_API}/stock/domestic/basic"

    payload = request_json(
        url,
        params={
            "code": code,
            "endType": "index",
        },
    )

    if not isinstance(payload, dict):
        raise ValueError("지수 응답 형식이 올바르지 않습니다.")

    # 일부 응답은 result 또는 data 아래에 데이터가 들어갑니다.
    data = payload

    for key in ("result", "data"):
        if isinstance(data.get(key), dict):
            data = data[key]
            break

    price = None
    pct = None

    # 응답 버전에 따라 필드명이 달라질 수 있습니다.
    for key in (
        "closePrice",
        "currentPrice",
        "nowVal",
        "price",
    ):
        if data.get(key) is not None:
            price = to_number(data[key])
            if price is not None:
                break

    for key in (
        "fluctuationsRatio",
        "changeRate",
        "compareToPreviousPriceRatio",
        "changeRateValue",
    ):
        if data.get(key) is not None:
            pct = to_number(data[key])
            if pct is not None:
                break

    if price is None or price <= 0:
        raise ValueError(
            f"{code} 지수의 현재가를 확인할 수 없습니다."
        )

    if pct is None:
        raise ValueError(
            f"{code} 지수의 등락률을 확인할 수 없습니다."
        )

    return {
        "price": price,
        "pct": pct,
    }


def fetch_naver_market_index():
    result = {}

    for key, code in (
        ("kospi", "KOSPI"),
        ("kosdaq", "KOSDAQ"),
    ):
        try:
            result[key] = fetch_one_index(code)
            result[key]["error"] = None

        except Exception as exc:
            result[key] = {
                "price": None,
                "pct": None,
                "error": str(exc),
            }

    return result


# ============================================================
# 급등 / 급락 종목 조회
# ============================================================

def extract_stock_records(payload):
    """
    JSON 응답에서 종목 데이터를 재귀적으로 찾습니다.

    현재가와 등락률 필드가 모두 확인된 데이터만 반환합니다.
    """

    records = []

    def walk(obj):
        if isinstance(obj, dict):

            name = (
                obj.get("stockName")
                or obj.get("name")
                or obj.get("itemName")
            )

            price_value = None

            for key in (
                "closePrice",
                "currentPrice",
                "nowPrice",
                "price",
            ):
                if obj.get(key) is not None:
                    price_value = obj[key]
                    break

            pct_value = None

            for key in (
                "fluctuationsRatio",
                "changeRate",
                "compareToPreviousPriceRatio",
                "fluctuationRate",
            ):
                if obj.get(key) is not None:
                    pct_value = obj[key]
                    break

            if (
                isinstance(name, str)
                and price_value is not None
                and pct_value is not None
            ):
                price = to_number(price_value)
                pct = to_number(pct_value)

                if (
                    price is not None
                    and pct is not None
                    and price > 0
                    and -100 <= pct <= 100
                ):
                    records.append({
                        "name": name.strip(),
                        "price": price,
                        "pct": pct,
                    })

            for value in obj.values():
                if isinstance(value, (dict, list)):
                    walk(value)

        elif isinstance(obj, list):
            for value in obj:
                walk(value)

    walk(payload)

    return records


def fetch_top5_stocks(type_mode="up"):
    """
    실제 네이버 증권 종목 목록을 조회합니다.

    up   : 상승률 상위
    down : 하락률 상위

    데이터 수집 실패 시 임의의 종목이나 가격을 만들지 않습니다.
    """

    if type_mode not in ("up", "down"):
        raise ValueError("type_mode는 up 또는 down이어야 합니다.")

    url = f"{NAVER_API}/stock/domestic/stockList"

    all_data = []
    errors = []

    # 코스피와 코스닥을 각각 조회합니다.
    for market in ("KOSPI", "KOSDAQ"):

        params = {
            "sortType": type_mode,
            "category": market,
            "page": 1,
            "pageSize": 100,
        }

        try:
            payload = request_json(
                url,
                params=params,
                timeout=12,
            )

            records = extract_stock_records(payload)

            if not records:
                errors.append(
                    f"{market}: 종목 데이터를 찾지 못했습니다. "
                    "API 응답 구조를 확인해야 합니다."
                )
                continue

            for stock in records:
                stock["sector"] = market
                all_data.append(stock)

        except requests.HTTPError as exc:
            status = (
                exc.response.status_code
                if exc.response is not None
                else "확인 불가"
            )

            errors.append(
                f"{market}: HTTP 오류 {status}"
            )

        except Exception as exc:
            errors.append(
                f"{market}: {type(exc).__name__}: {exc}"
            )

    # 동일 종목 제거
    unique = {}

    for stock in all_data:
        unique[stock["name"]] = stock

    stocks = list(unique.values())

    # ETF, ETN, 스팩 등 제외
    filtered = []

    for stock in stocks:
        name = stock["name"]
        pct = stock["pct"]

        if EXCLUDE_NAME.search(name):
            continue

        # 상승 / 하락 방향 검증
        if type_mode == "up" and pct <= 0:
            continue

        if type_mode == "down" and pct >= 0:
            continue

        filtered.append(stock)

    # 상승률 내림차순 / 하락률 오름차순
    if type_mode == "up":
        filtered.sort(
            key=lambda item: item["pct"],
            reverse=True,
        )
    else:
        filtered.sort(
            key=lambda item: item["pct"],
        )

    # 5개가 확보되지 않았더라도 실제 수집된 종목만 반환
    result = filtered[:5]

    # 오류 정보도 앱에서 확인할 수 있도록 저장
    st.session_state[f"stock_errors_{type_mode}"] = errors

    return result


# ============================================================
# 네이버 뉴스
# ============================================================

def clean_title(value):
    value = re.sub(r"<[^>]+>", "", value)

    return (
        value.replace("&quot;", '"')
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&apos;", "'")
        .strip()
    )


def fetch_headlines(name, today, up=True):
    """
    네이버 뉴스 검색 API가 설정되어 있을 때만 뉴스를 조회합니다.
    """

    if not (NAVER_ID and NAVER_SECRET):
        return []

    output = []
    seen = set()

    for suffix in ("특징주", "주가"):

        try:
            response = SESSION.get(
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
                timeout=8,
            )

            if response.status_code != 200:
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

                if published.date() != today:
                    continue

                if name not in title or title in seen:
                    continue

                seen.add(title)

                direction_words = UP_WORDS if up else DOWN_WORDS

                score = (
                    2 if direction_words.search(title) else 0
                ) + (
                    1 if REASON_WORDS.search(title) else 0
                )

                output.append({
                    "title": title,
                    "time": published.strftime("%H:%M"),
                    "score": score,
                })

        except Exception:
            continue

        if output:
            break

    output.sort(
        key=lambda item: (
            item["score"],
            item["time"],
        ),
        reverse=True,
    )

    return [
        {
            "title": item["title"],
            "time": item["time"],
        }
        for item in output[:3]
    ]


# ============================================================
# 테마 / 상승·하락 이유
# ============================================================

def derive_theme(stock):
    texts = [
        " ".join(
            headline["title"]
            for headline in stock.get("headlines", [])
        ),
        stock["name"],
    ]

    found = []

    for text in texts:
        for label, pattern in THEME_RULES:
            if (
                label not in found
                and re.search(pattern, text)
            ):
                found.append(label)

    if found:
        return " · ".join(found[:2])

    return "테마 확인 필요"


def auto_reason(stock):
    headlines = stock.get("headlines") or []

    if headlines:
        return headlines[0]["title"]

    return "관련 뉴스 확인 필요"


def fetch_snapshot():
    now = datetime.datetime.now(KST)

    indices = fetch_naver_market_index()

    gainers = fetch_top5_stocks("up")
    losers = fetch_top5_stocks("down")

    for stock in gainers:
        stock["headlines"] = fetch_headlines(
            stock["name"],
            now.date(),
            up=True,
        )
        stock["theme"] = derive_theme(stock)

    for stock in losers:
        stock["headlines"] = fetch_headlines(
            stock["name"],
            now.date(),
            up=False,
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
    }


# ============================================================
# 시황글 생성
# ============================================================

def fmt(value, digits=2):
    if value is None:
        return "확인 불가"

    return f"{value:,.{digits}f}"


def sgn(value):
    if value is None:
        return "확인 불가"

    return f"{value:+,.2f}%"


def em(value):
    if value is None:
        return "➖"

    return "🔺" if value > 0 else "🔻" if value < 0 else "➖"


def build_md(snap):
    ss = st.session_state

    tm = snap["fetched_at"][:5]

    lines = [
        f"# 📅 {snap['date']} | 오후 시황",
        "",
    ]

    summary = ss.get(
        "summary",
        "실시간 지수와 종목별 등락을 확인하세요.",
    ).strip()

    if summary:
        lines += [
            f"📌 **한 줄 요약**: {summary}",
            "",
        ]

    intro = ss.get("intro_text", "").strip()

    if intro:
        lines += [intro, ""]

    # 지수
    lines += [
        "### 🇰🇷 장중 지수 현황",
        "",
        "| 지수 | 현재가 | 등락률 | 기준 시각 |",
        "|---|---:|---:|---|",
    ]

    for key, label in (
        ("kospi", "코스피"),
        ("kosdaq", "코스닥"),
    ):
        data = snap[key]

        lines.append(
            f"| {label} "
            f"| {fmt(data['price'])} "
            f"| {em(data['pct'])} {sgn(data['pct'])} "
            f"| {tm} |"
        )

    lines += ["", ""]

    # 급등주
    lines += [
        "### 🔺 급등 특징주",
        "",
        "| 종목 | 현재가 | 등락률 | 상승 이유 | 관련 테마 |",
        "|---|---:|---:|---|---|",
    ]

    for stock in snap["gainers"]:
        name = stock["name"]

        reason = (
            ss.get(f"reason_{name}")
            or auto_reason(stock)
        ).replace("|", "/")

        theme = (
            ss.get(f"theme_{name}")
            or stock.get("theme", "")
        ).replace("|", "/")

        lines.append(
            f"| **{name}** "
            f"| {fmt(stock['price'], 0)}원 "
            f"| **{sgn(stock['pct'])}** "
            f"| {reason} "
            f"| {theme} |"
        )

    if not snap["gainers"]:
        lines.append(
            "| 확인된 종목 없음 | - | - | "
            "시세 데이터 조회 필요 | - |"
        )

    lines += ["", ""]

    # 급락주
    lines += [
        "### 🔻 급락 특징주",
        "",
        "| 종목 | 현재가 | 등락률 | 하락 이유 | 관련 테마 |",
        "|---|---:|---:|---|---|",
    ]

    for stock in snap["losers"]:
        name = stock["name"]

        reason = (
            ss.get(f"reason_{name}")
            or auto_reason(stock)
        ).replace("|", "/")

        theme = (
            ss.get(f"theme_{name}")
            or stock.get("theme", "")
        ).replace("|", "/")

        lines.append(
            f"| **{name}** "
            f"| {fmt(stock['price'], 0)}원 "
            f"| **{sgn(stock['pct'])}** "
            f"| {reason} "
            f"| {theme} |"
        )

    if not snap["losers"]:
        lines.append(
            "| 확인된 종목 없음 | - | - | "
            "시세 데이터 조회 필요 | - |"
        )

    lines += ["", ""]

    # 경제 이슈
    lines += [
        "### 🔥 오늘의 주요 경제 이슈",
        "",
    ]

    for i in (1, 2, 3):
        title = ss.get(f"is{i}t", "").strip()
        direction = ss.get(f"is{i}m", "➖")
        content = ss.get(f"is{i}c", "").strip()
        stocks = ss.get(f"is{i}s", "").strip()
        check = ss.get(f"is{i}k", "").strip()

        number = ("①", "②", "③")[i - 1]

        if title:
            lines.append(f"**{number} {direction} {title}**")

        if content:
            lines.append(f"- 내용: {content}")

        if stocks:
            names = ", ".join(
                f"**{item.strip()}**"
                for item in re.split(r"[,，]", stocks)
                if item.strip()
            )
            lines.append(f"- 📌 관련 종목: {names}")

        if check:
            lines.append(f"- ⏰ 체크: {check}")

        lines.append("")

    # 테마
    themes = ss.get("themes", "").strip()

    if themes:
        formatted = " · ".join(
            f"**{item.strip()}**"
            for item in re.split(r"[·,]", themes)
            if item.strip()
        )

        lines += [
            "### 🏷️ 오늘 강했던 테마",
            "",
            formatted,
            "",
        ]

    # 체크 포인트
    checks = ss.get("checks", "").strip()

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

    keywords = ss.get("keywords", "").strip()

    if keywords:
        lines += [
            "### 🔍 핵심 키워드",
            "",
            keywords,
            "",
        ]

    caution = ss.get("caution", "").strip()

    if caution:
        lines += [
            "### ⚠️ 한 줄 주의",
            "",
            caution,
            "",
        ]

    return "\n".join(lines)


# ============================================================
# 마크다운 → HTML
# ============================================================

def _inline(value):
    escaped = htmllib.escape(value)

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

            if all(
                re.fullmatch(r":?-+:?", cell)
                for cell in cells
            ):
                continue

            if not table_open:
                table_open = True

                output.append(
                    '<table border="1" cellspacing="0" '
                    'cellpadding="6" '
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
<div style="display:flex;gap:8px;align-items:center;
font-family:sans-serif;font-size:13px">

<button id="r"
style="padding:8px 14px;border-radius:6px;border:0;
background:#0f5c5c;color:#fff;font-weight:600;cursor:pointer">
복사 (서식 포함)
</button>

<button id="m"
style="padding:8px 14px;border-radius:6px;
border:1px solid #bbb;background:#fff;cursor:pointer">
복사 (마크다운)
</button>

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

    components.html(
        page,
        height=60,
    )


# ============================================================
# 화면 구성
# ============================================================

def colored(value, text):
    if value is None:
        return text

    if value > 0:
        return f":red[{text}]"

    if value < 0:
        return f":blue[{text}]"

    return text


def stock_table(title, stocks, word, mode):
    st.subheader(title)

    errors = st.session_state.get(
        f"stock_errors_{mode}",
        [],
    )

    if not stocks:

        st.warning(
            "실제 시세 데이터를 가져오지 못했거나 "
            "표시할 종목이 없습니다."
        )

        for error in errors:
            st.caption(error)

        st.caption(
            "네이버 증권 API 응답 및 Streamlit 배포 로그를 "
            "확인해 주세요."
        )

        return

    columns = st.columns(
        [2.2, 1.2, 1.3, 4.5, 2.2]
    )

    for column, label in zip(
        columns,
        [
            "종목",
            "현재가",
            "등락률",
            f"{word} 이유",
            "관련 테마",
        ],
    ):
        column.caption(label)

    for stock in stocks:
        columns = st.columns(
            [2.2, 1.2, 1.3, 4.5, 2.2]
        )

        name = stock["name"]

        columns[0].markdown(
            f"**{name}**"
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

        reason_key = f"reason_{name}"
        theme_key = f"theme_{name}"

        if reason_key not in st.session_state:
            st.session_state[reason_key] = auto_reason(stock)

        if theme_key not in st.session_state:
            st.session_state[theme_key] = stock.get(
                "theme",
                "테마 확인 필요",
            )

        columns[3].text_input(
            "이유",
            key=reason_key,
            label_visibility="collapsed",
        )

        columns[4].text_input(
            "테마",
            key=theme_key,
            label_visibility="collapsed",
        )


def manual_inputs():
    with st.expander(
        "📝 시황글 추가 내용 편집 (요약, 이슈, 테마 등)",
        expanded=True,
    ):

        if "summary" not in st.session_state:
            st.session_state["summary"] = (
                "실시간 지수와 종목별 등락을 확인하세요."
            )

        if "intro_text" not in st.session_state:
            st.session_state["intro_text"] = (
                "국내 증시의 주요 지수와 종목별 등락 현황을 "
                "조회 시각 기준으로 정리했습니다. "
                "종목별 상승·하락 이유는 관련 뉴스 확인 후 "
                "작성해 주세요."
            )

        st.text_input(
            "한 줄 요약",
            key="summary",
        )

        st.text_area(
            "인트로 문구",
            key="intro_text",
        )

        for i in (1, 2, 3):

            st.markdown(f"**이슈 {i}**")

            a, b, c = st.columns([1, 3, 3])

            if f"is{i}m" not in st.session_state:
                st.session_state[f"is{i}m"] = "➖"

            if f"is{i}t" not in st.session_state:
                st.session_state[f"is{i}t"] = ""

            if f"is{i}s" not in st.session_state:
                st.session_state[f"is{i}s"] = ""

            if f"is{i}c" not in st.session_state:
                st.session_state[f"is{i}c"] = ""

            if f"is{i}k" not in st.session_state:
                st.session_state[f"is{i}k"] = ""

            a.selectbox(
                "방향",
                ["🔺", "🔻", "➖"],
                key=f"is{i}m",
                label_visibility="collapsed",
            )

            b.text_input(
                "제목",
                key=f"is{i}t",
                label_visibility="collapsed",
            )

            c.text_input(
                "관련 종목",
                key=f"is{i}s",
                label_visibility="collapsed",
            )

            st.text_input(
                "내용",
                key=f"is{i}c",
            )

            st.text_input(
                "체크",
                key=f"is{i}k",
            )

        defaults = {
            "themes": "",
            "checks": "",
            "keywords": "",
            "caution": (
                "급등에 따른 차익실현과 종목별 변동성 확대에 "
                "유의하세요."
            ),
        }

        for key, value in defaults.items():
            if key not in st.session_state:
                st.session_state[key] = value

        st.text_input(
            "강했던 테마",
            key="themes",
        )

        st.text_input(
            "내일 체크 포인트 (/로 구분)",
            key="checks",
        )

        st.text_input(
            "핵심 키워드",
            key="keywords",
        )

        st.text_input(
            "한 줄 주의",
            key="caution",
        )


# ============================================================
# 메인 앱
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

        if clicked or "snap" not in ss:

            try:
                with st.spinner(
                    "실시간 시세 조회 중..."
                ):
                    snap = fetch_snapshot()

                ss["snap"] = snap

            except Exception as exc:
                st.error(
                    f"시세 조회 중 오류가 발생했습니다: {exc}"
                )

                return

        snap = ss.get("snap")

        if not snap:
            st.info(
                "'지금 시세 조회' 버튼을 눌러 "
                "시세를 불러오세요."
            )
            return

        st.caption(
            f"{snap['date']} · "
            f"조회 {snap['fetched_at']}"
        )

        # 지수 표시
        c1, c2, c3 = st.columns(3)

        for column, key, label in (
            (c1, "kospi", "코스피"),
            (c2, "kosdaq", "코스닥"),
        ):

            data = snap[key]

            column.markdown(f"**{label}**")

            if data["price"] is None:
                column.error("지수 조회 실패")
                column.caption(data.get("error") or "")
            else:
                column.markdown(
                    f"### {fmt(data['price'])}"
                )

                column.markdown(
                    colored(
                        data["pct"],
                        f"{em(data['pct'])} {sgn(data['pct'])}",
                    )
                )

        c3.markdown("**원/달러 환율**")

        c3.info(
            "환율 데이터는 별도 연동이 필요합니다. "
            "실시간 값으로 확인된 수치가 아닙니다."
        )

        # 급등 / 급락
        stock_table(
            "🔺 급등 특징주 TOP 5",
            snap["gainers"],
            "상승",
            "up",
        )

        stock_table(
            "🔻 급락 특징주 TOP 5",
            snap["losers"],
            "하락",
            "down",
        )

        # 추가 입력
        manual_inputs()

        # 미리보기
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


main()
