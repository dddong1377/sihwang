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

st.set_page_config(page_title="MTNW 오후 시황", layout="wide")

KST = ZoneInfo("Asia/Seoul")
BASE = "https://openapi.tossinvest.com"
WEEKDAYS = "월화수목금토일"
NOW_TOKEN = "__NOW__"  # 복사 버튼을 누르는 순간의 시각으로 치환됨

THEMES = [
    ("HBM·반도체", r"HBM|D램|낸드|반도체|파운드리|메모리|소부장"),
    ("AI", r"AI|인공지능|데이터센터|엔비디아|챗GPT|앤트로픽"),
    ("2차전지", r"2차전지|배터리|양극재|음극재|리튬|전고체"),
    ("바이오·제약", r"바이오|제약|신약|임상|FDA|항암|치료제|백신"),
    ("로봇", r"로봇|휴머노이드|자동화"),
    ("방산·우주", r"방산|미사일|우주|위성|K9|천무"),
    ("조선·해운", r"조선|선박|LNG선|해운"),
    ("원전·전력", r"원전|SMR|전력|변압기|전선|송전"),
    ("엔터", r"엔터|K팝|아이돌|콘서트|음반"),
    ("게임", r"게임|신작|퍼블리싱"),
    ("화장품·소비", r"화장품|뷰티|면세|K푸드|음식료"),
    ("자동차", r"자동차|전기차|자율주행"),
    ("금융", r"은행|증권|보험|금융지주|배당"),
    ("건설·부동산", r"건설|부동산|분양|재건축"),
    ("정유·에너지", r"정유|유가|에너지|석유|가스"),
    ("철강·소재", r"철강|화학|비철|구리"),
]

EXCLUDE = re.compile(
    r"스팩|제\d+호|ETN|KODEX|TIGER|ACE |SOL |RISE|HANARO|KOSEF|ARIRANG|PLUS |KIWOOM|우$"
)


def secret(key, default=""):
    """Streamlit Secrets 또는 환경변수에서 설정값을 가져옵니다."""
    try:
        value = st.secrets.get(key)
    except Exception:
        value = None

    return str(value) if value else os.getenv(key, default)


def login():
    """사이트 접속 비밀번호 확인."""
    expected = secret("app_password")

    if not expected:
        st.error("Streamlit Secrets에 app_password를 설정하세요.")
        return False

    def verify():
        entered = st.session_state.get("pw", "").encode()
        st.session_state["pw_ok"] = hmac.compare_digest(
            entered,
            expected.encode()
        )
        st.session_state["pw"] = ""

    if st.session_state.get("pw_ok"):
        return True

    st.title("MTNW 시황")

    st.text_input(
        "비밀번호",
        type="password",
        key="pw",
        on_change=verify
    )

    if st.session_state.get("pw_ok") is False:
        st.error("비밀번호가 올바르지 않습니다.")

    return False


if not login():
    st.stop()


CLIENT_ID = secret("TOSS_CLIENT_ID")
CLIENT_SECRET = secret("TOSS_CLIENT_SECRET")

NAVER_ID = secret("NAVER_CLIENT_ID")
NAVER_SECRET = secret("NAVER_CLIENT_SECRET")


class TossError(Exception):
    """토스증권 API 관련 오류."""
    pass


@st.cache_data(ttl=20 * 60 * 60, show_spinner=False)
def get_token(client_id, client_secret):
    """토스증권 API 액세스 토큰 발급."""

    if not client_id or not client_secret:
        raise TossError(
            "TOSS_CLIENT_ID / TOSS_CLIENT_SECRET이 "
            "Streamlit Secrets에 없습니다."
        )

    try:
        response = requests.post(
            f"{BASE}/oauth2/token",
            data={
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": client_secret,
            },
            headers={
                "Content-Type": "application/x-www-form-urlencoded"
            },
            timeout=12,
        )

    except requests.RequestException as error:
        raise TossError(
            f"토큰 서버 연결 실패: {error}"
        ) from error

    if response.status_code != 200:
        raise TossError(
            f"토큰 발급 실패 HTTP {response.status_code}: "
            f"{response.text[:500]}"
        )

    token_data = response.json()
    token = token_data.get("access_token")

    if not token:
        raise TossError(
            "토큰 응답에 access_token이 없습니다: "
            f"{response.text[:300]}"
        )

    return token


def api_get(path, params=None):
    """토스증권 API GET 요청."""

    token = get_token(CLIENT_ID, CLIENT_SECRET)

    try:
        response = requests.get(
            f"{BASE}{path}",
            params=params or {},
            headers={
                "Authorization": f"Bearer {token}"
            },
            timeout=15,
        )

    except requests.RequestException as error:
        raise TossError(
            f"{path} 연결 실패: {error}"
        ) from error

    if response.status_code != 200:
        try:
            detail = response.json()
        except ValueError:
            detail = response.text[:400]

        if response.status_code == 403:
            raise TossError(
                "HTTP 403: 토스증권 Open API 허용 IP에 "
                "배포 서버 IP를 등록했는지 확인하세요. "
                f"{detail}"
            )

        if response.status_code == 429:
            raise TossError(
                "HTTP 429: 요청 한도 초과. "
                f"Retry-After={response.headers.get('Retry-After', '미제공')}. "
                f"{detail}"
            )

        raise TossError(
            f"{path} HTTP {response.status_code}: {detail}"
        )

    data = response.json()

    if data.get("error"):
        error_data = data["error"]

        raise TossError(
            f"{error_data.get('code')}: "
            f"{error_data.get('message')}"
        )

    return data.get("result", {})


def num(value, default=None):
    """숫자 변환."""

    try:
        return float(str(value).replace(",", ""))

    except (TypeError, ValueError):
        return default


def get_indices():
    """
    코스피·코스닥 지수 조회.

    주의:
    아래 시장 지표 API 경로와 응답 필드는
    실제 토스 API 명세에 맞는지 별도 검증이 필요합니다.
    """

    result = api_get(
        "/api/v1/market-indicators/prices",
        {
            "symbols": "KOSPI,KOSDAQ"
        }
    )

    if isinstance(result, dict):
        rows = result.get("prices", [])
    elif isinstance(result, list):
        rows = result
    else:
        rows = []

    by_symbol = {
        item.get("symbol"): item
        for item in rows
        if isinstance(item, dict)
    }

    output = {}

    for symbol in ("KOSPI", "KOSDAQ"):
        item = by_symbol.get(symbol, {})

        price = num(item.get("lastPrice"))
        pct = None

        try:
            candle_result = api_get(
                f"/api/v1/market-indicators/{symbol}/candles",
                {
                    "interval": "1d",
                    "count": 3,
                }
            )

            candles = candle_result.get("candles", [])

            if price is not None and len(candles) >= 2:
                previous_close = num(
                    candles[1].get("closePrice")
                )

                if previous_close:
                    pct = (
                        price / previous_close - 1
                    ) * 100

        except Exception:
            pass

        output[symbol.lower()] = {
            "price": price,
            "pct": pct,
            "timestamp": item.get("timestamp"),
        }

    return output


def get_stock_names(symbols):
    """종목 코드에 해당하는 종목명 조회."""

    names = {}

    for start in range(0, len(symbols), 200):
        batch = symbols[start:start + 200]

        if not batch:
            continue

        try:
            rows = api_get(
                "/api/v1/stocks",
                {
                    "symbols": ",".join(batch)
                }
            )

            if isinstance(rows, dict):
                rows = rows.get("stocks", [])

            if isinstance(rows, list):
                for row in rows:
                    if not isinstance(row, dict):
                        continue

                    symbol = str(
                        row.get("symbol", "")
                    ).strip()

                    name = (
                        row.get("name")
                        or row.get("stockName")
                        or row.get("displayName")
                    )

                    if symbol and name:
                        names[symbol] = str(name).strip()

        except Exception:
            continue

    return names


def get_rankings(mode="up", duration="1d"):
    """
    상승·하락 종목 순위 조회.

    주의:
    /api/v1/rankings 및 전달 파라미터는
    토스 API의 실제 명세와 일치하는지 확인해야 합니다.
    """

    rank_type = (
        "TOP_GAINERS"
        if mode == "up"
        else "TOP_LOSERS"
    )

    result = api_get(
        "/api/v1/rankings",
        {
            "type": rank_type,
            "marketCountry": "KR",
            "duration": duration,
            "count": 100,
            "excludeInvestmentCaution": True,
        }
    )

    if isinstance(result, list):
        rows = result
        ranked_at = None

    elif isinstance(result, dict):
        rows = result.get("rankings", [])
        ranked_at = result.get("rankedAt")

    else:
        rows = []
        ranked_at = None

    symbols = [
        str(row.get("symbol", "")).strip()
        for row in rows
        if isinstance(row, dict) and row.get("symbol")
    ]

    names = get_stock_names(symbols)

    stocks = []

    for row in rows:
        if not isinstance(row, dict):
            continue

        symbol = str(
            row.get("symbol", "")
        ).strip()

        name = names.get(symbol)

        if not name or EXCLUDE.search(name):
            continue

        price_data = row.get("price") or {}

        change = num(price_data.get("changeRate"))
        price = num(price_data.get("lastPrice"))

        if change is None or price is None:
            continue

        # changeRate가 비율로 반환된다는 전제입니다.
        # 실제 응답 형식에 따라 계산식을 확인해야 합니다.
        pct = change * 100

        if mode == "up" and pct <= 0:
            continue

        if mode == "down" and pct >= 0:
            continue

        theme = next(
            (
                label
                for label, pattern in THEMES
                if re.search(pattern, name, re.I)
            ),
            "주요업종"
        )

        stocks.append({
            "name": name,
            "symbol": symbol,
            "price": price,
            "pct": pct,
            "base_price": num(
                price_data.get("basePrice")
            ),
            "rank": row.get("rank"),
            "theme": theme,
            "reason": "토스증권 당일 등락률 순위 기준",
            "timestamp": ranked_at,
        })

    stocks.sort(
        key=lambda stock: stock["pct"],
        reverse=(mode == "up")
    )

    return stocks[:5], ranked_at


def fetch_news(name, today):
    """네이버 뉴스 API를 이용한 당일 관련 기사 조회."""

    if not (NAVER_ID and NAVER_SECRET):
        return []

    try:
        response = requests.get(
            "https://openapi.naver.com/v1/search/news.json",
            params={
                "query": f"{name} 특징주",
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
            return []

        found = []

        for item in response.json().get("items", []):
            title = re.sub(
                r"<[^>]+>",
                "",
                item.get("title", "")
            )

            title = html.unescape(title)

            try:
                published = datetime.datetime.strptime(
                    item["pubDate"],
                    "%a, %d %b %Y %H:%M:%S %z"
                ).astimezone(KST)

            except Exception:
                continue

            if published.date() == today and name in title:
                found.append({
                    "title": title,
                    "time": published.strftime("%H:%M"),
                })

        return found[:3]

    except Exception:
        return []


def fetch_snapshot(duration):
    """지수와 급등·급락 종목 데이터를 한 번에 조회."""

    now = datetime.datetime.now(KST)

    indices = get_indices()

    gainers, up_at = get_rankings(
        "up",
        duration
    )

    losers, down_at = get_rankings(
        "down",
        duration
    )

    for stock in gainers + losers:
        headlines = fetch_news(
            stock["name"],
            now.date()
        )

        if headlines:
            stock["reason"] = headlines[0]["title"]

    return {
        "date": (
            f"{now.year}년 "
            f"{now.month:02d}월 "
            f"{now.day:02d}일 "
            f"({WEEKDAYS[now.weekday()]})"
        ),
        "fetched_at": now.strftime("%H:%M:%S"),
        "ranking_at": up_at or down_at,
        "duration": duration,
        "indices": indices,
        "gainers": gainers,
        "losers": losers,
    }


def fmt(value, digits=2):
    """숫자 표시 형식."""

    if value is None:
        return "확인 불가"

    return f"{value:,.{digits}f}"


def pct_text(value):
    """등락률 표시 형식."""

    if value is None:
        return "확인 불가"

    return f"{value:+.2f}%"


def arrow(value):
    """등락률에 따른 아이콘."""

    if value is not None and value > 0:
        return "🔺"

    if value is not None and value < 0:
        return "🔻"

    return "➖"


def build_markdown(snapshot, time_text="확인 불가"):
    """시황글 마크다운 생성."""

    session = st.session_state

    lines = [
        f"# 📅 {snapshot['date']} | 오후 시황",
        "",
        (
            f"📌 **한 줄 요약**: "
            f"{session.get('summary', '당일 등락률 상위 종목과 시장 흐름을 확인하세요.')}"
        ),
        "",
        session.get(
            "intro_text",
            "종목 순위는 토스증권 Open API의 기간별 등락률 기준입니다. "
            "실시간 순위와 다를 수 있으므로 기준 시각을 확인해 주세요."
        ),
        "",
        "### 🇰🇷 장중 지수 현황",
        "",
        "| 지수 | 현재가 | 등락률 | 기준 시각 |",
        "|---|---:|---:|---|",
    ]

    for key, label in (
        ("kospi", "코스피"),
        ("kosdaq", "코스닥"),
    ):
        data = snapshot["indices"].get(key, {})

        lines.append(
            f"| {label} "
            f"| {fmt(data.get('price'))} "
            f"| {arrow(data.get('pct'))} {pct_text(data.get('pct'))} "
            f"| {time_text} |"
        )

    lines += [
        "",
        "",
        "### 🔺 급등 특징주 TOP 5",
        "",
        "| 종목 | 현재가 | 등락률 | 상승 이유 | 관련 테마 |",
        "|---|---:|---:|---|---|",
    ]

    for stock in snapshot["gainers"]:
        lines.append(
            f"| **{stock['name']}** "
            f"| {fmt(stock['price'], 0)}원 "
            f"| **{pct_text(stock['pct'])}** "
            f"| {stock['reason']} "
            f"| {stock['theme']} |"
        )

    if not snapshot["gainers"]:
        lines.append(
            "| 데이터 없음 | 확인 불가 | 확인 불가 | API 응답 확인 필요 | - |"
        )

    lines += [
        "",
        "",
        "### 🔻 급락 특징주 TOP 5",
        "",
        "| 종목 | 현재가 | 등락률 | 하락 이유 | 관련 테마 |",
        "|---|---:|---:|---|---|",
    ]

    for stock in snapshot["losers"]:
        lines.append(
            f"| **{stock['name']}** "
            f"| {fmt(stock['price'], 0)}원 "
            f"| **{pct_text(stock['pct'])}** "
            f"| {stock['reason']} "
            f"| {stock['theme']} |"
        )

    if not snapshot["losers"]:
        lines.append(
            "| 데이터 없음 | 확인 불가 | 확인 불가 | API 응답 확인 필요 | - |"
        )

    lines += [
        "",
        "",
        "### 🔥 오늘의 주요 경제 이슈",
        "",
    ]

    for i in (1, 2, 3):
        title = session.get(f"is{i}t", "").strip()

        if not title:
            continue

        lines.append(
            f"**{i}. {session.get(f'is{i}m', '➖')} {title}**"
        )

        content = session.get(f"is{i}c", "").strip()

        if content:
            lines.append(f"- 내용: {content}")

        related = session.get(f"is{i}s", "").strip()

        if related:
            lines.append(f"- 📌 관련 종목: {related}")

        check = session.get(f"is{i}k", "").strip()

        if check:
            lines.append(f"- ⏰ 체크: {check}")

        lines.append("")

    sections = [
        (
            "🏷️ 오늘 강했던 테마",
            "themes",
            "AI 반도체 · 2차전지",
        ),
        (
            "👀 내일 체크 포인트",
            "checks",
            "급등 종목 상승 지속 여부 / 급락 종목 추가 하락 여부",
        ),
        (
            "🔍 핵심 키워드",
            "keywords",
            "#코스피 #코스닥 #급등주 #급락주",
        ),
        (
            "⚠️ 한 줄 주의",
            "caution",
            "등락률 순위는 선택한 기간 기준이며 실시간 순위와 다를 수 있습니다.",
        ),
    ]

    for title, key, default in sections:
        value = session.get(key, default).strip()

        if value:
            lines += [
                f"### {title}",
                "",
                value.replace("/", "\n- "),
                "",
            ]

    return "\n".join(lines)


def md_to_html(markdown):
    """마크다운을 간단한 HTML로 변환."""

    output = []
    in_table = False

    for line in markdown.splitlines():
        if line.startswith("|"):
            cells = [
                cell.strip()
                for cell in line.split("|")[1:-1]
            ]

            if all(
                re.fullmatch(r":?-+:?", cell)
                for cell in cells
            ):
                continue

            if not in_table:
                output.append(
                    "<table border='1' cellpadding='6' "
                    "cellspacing='0' "
                    "style='border-collapse:collapse'>"
                )
                in_table = True

            rendered_cells = []

            for cell in cells:
                safe = html.escape(cell)
                safe = re.sub(
                    r"\*\*(.*?)\*\*",
                    r"<b>\1</b>",
                    safe
                )
                rendered_cells.append(f"<td>{safe}</td>")

            output.append(
                "<tr>" + "".join(rendered_cells) + "</tr>"
            )

            continue

        if in_table:
            output.append("</table>")
            in_table = False

        if line.startswith("# "):
            output.append(
                "<h1>" + html.escape(line[2:]) + "</h1>"
            )

        elif line.startswith("### "):
            output.append(
                "<h3>" + html.escape(line[4:]) + "</h3>"
            )

        elif line.strip():
            safe = html.escape(line)

            safe = re.sub(
                r"\*\*(.*?)\*\*",
                r"<b>\1</b>",
                safe
            )

            output.append("<p>" + safe + "</p>")

    if in_table:
        output.append("</table>")

    return "\n".join(output)


def copy_buttons(markdown):
    """서식 포함 복사 및 마크다운 복사 버튼."""

    payload = json.dumps(
        {
            "md": markdown,
            "html": md_to_html(markdown),
        },
        ensure_ascii=False
    ).replace("</", "<\\/")

    page = f"""
    <button id="rich">복사 (서식 포함)</button>
    <button id="plain">복사 (마크다운)</button>
    <span id="status"></span>

    <script>
    const P = {payload};
    const s = document.getElementById('status');

    function nowKST() {{
        return new Date().toLocaleTimeString('en-GB', {{
            timeZone: 'Asia/Seoul',
            hour: '2-digit',
            minute: '2-digit',
            hourCycle: 'h23'
        }});
    }}

    function stamp(text) {{
        return text.split('{NOW_TOKEN}').join(nowKST());
    }}

    document.getElementById('rich').onclick = async () => {{
        try {{
            await navigator.clipboard.write([
                new ClipboardItem({{
                    'text/html': new Blob(
                        [stamp(P.html)],
                        {{type: 'text/html'}}
                    ),
                    'text/plain': new Blob(
                        [stamp(P.md)],
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
            await navigator.clipboard.writeText(stamp(P.md));
            s.textContent = ' 복사 완료';

        }} catch (e) {{
            s.textContent = ' 복사 실패';
        }}
    }};
    </script>
    """

    components.html(page, height=45)


def main():
    st.title("MTNW 오후 시황")

    with st.sidebar:
        st.header("설정")

        auto = st.selectbox(
            "자동 갱신",
            [0, 60, 120, 300],
            format_func=lambda value: (
                "끔" if value == 0 else f"{value}초"
            )
        )

        duration = st.selectbox(
            "급등·급락 순위 기준",
            ["1d", "1w", "1mo", "3mo", "6mo", "1y"],
            format_func=lambda value: {
                "1d": "당일",
                "1w": "1주",
                "1mo": "1개월",
                "3mo": "3개월",
                "6mo": "6개월",
                "1y": "1년",
            }[value]
        )

        st.caption(
            "토스증권 TOP_GAINERS/TOP_LOSERS는 "
            "realtime 기간을 지원하지 않습니다."
        )

        if st.button("서버 IP 확인"):
            try:
                v4 = requests.get(
                    "https://api.ipify.org", timeout=5
                ).text
            except Exception as e:
                v4 = f"실패: {e}"

            try:
                v6 = requests.get(
                    "https://api64.ipify.org", timeout=5
                ).text
            except Exception as e:
                v6 = f"실패: {e}"

            st.write("IPv4:", v4)
            st.write("api64 (IPv6 우선):", v6)

        if st.button("로그아웃"):
            st.session_state["pw_ok"] = False
            st.rerun()

    @st.fragment(run_every=auto if auto else None)
    def body():

        should_fetch = (
            st.button("지금 시세 조회", type="primary")
            or "snapshot" not in st.session_state
            or st.session_state.get("duration") != duration
        )

        if should_fetch:
            try:
                with st.spinner("토스증권 시세 조회 중..."):
                    st.session_state["snapshot"] = fetch_snapshot(
                        duration
                    )

                    st.session_state["duration"] = duration
                    st.session_state.pop("api_error", None)

            except Exception as error:
                st.session_state["api_error"] = str(error)
                st.error(f"시세 조회 실패: {error}")

        snapshot = st.session_state.get("snapshot")

        if not snapshot:
            st.info(
                "토스증권 API 인증·허용 IP·랭킹 응답을 확인해 주세요."
            )
            return

        if st.session_state.get("api_error"):
            st.warning(
                f"최근 조회 오류: {st.session_state['api_error']}"
            )

        st.caption(
            f"{snapshot['date']} · "
            f"화면 조회 {snapshot['fetched_at']} · "
            f"랭킹 기준 {snapshot['ranking_at'] or '확인 불가'} · "
            "토스증권 Open API"
        )

        c1, c2 = st.columns(2)

        for col, key, label in (
            (c1, "kospi", "코스피"),
            (c2, "kosdaq", "코스닥"),
        ):
            data = snapshot["indices"].get(key, {})

            col.markdown(
                f"**{label}**  \n### {fmt(data.get('price'))}"
            )

            col.markdown(
                f"{arrow(data.get('pct'))} "
                f"{pct_text(data.get('pct'))}"
            )

            if data.get("pct") is None:
                col.caption(
                    "전일 종가를 확인할 수 없어 "
                    "등락률은 표시하지 않았습니다."
                )

        st.subheader("🔺 급등 특징주 TOP 5")

        if not snapshot["gainers"]:
            st.warning(
                "급등 랭킹이 비어 있습니다. "
                "선택한 기간의 랭킹 데이터가 있는지 확인하세요."
            )

        else:
            for stock in snapshot["gainers"]:
                a, b, c, d, e = st.columns(
                    [2, 1.2, 1.2, 4, 2]
                )

                a.markdown(f"**{stock['name']}**")
                b.write(fmt(stock["price"], 0))

                c.markdown(
                    f"🔺 {pct_text(stock['pct'])}"
                )

                stock["reason"] = d.text_input(
                    "상승 이유",
                    value=stock["reason"],
                    key=f"up_reason_{stock['symbol']}",
                    label_visibility="collapsed",
                )

                stock["theme"] = e.text_input(
                    "테마",
                    value=stock["theme"],
                    key=f"up_theme_{stock['symbol']}",
                    label_visibility="collapsed",
                )

        st.subheader("🔻 급락 특징주 TOP 5")

        if not snapshot["losers"]:
            st.warning(
                "급락 랭킹이 비어 있습니다. "
                "선택한 기간의 랭킹 데이터가 있는지 확인하세요."
            )

        else:
            for stock in snapshot["losers"]:
                a, b, c, d, e = st.columns(
                    [2, 1.2, 1.2, 4, 2]
                )

                a.markdown(f"**{stock['name']}**")
                b.write(fmt(stock["price"], 0))

                c.markdown(
                    f"🔻 {pct_text(stock['pct'])}"
                )

                stock["reason"] = d.text_input(
                    "하락 이유",
                    value=stock["reason"],
                    key=f"down_reason_{stock['symbol']}",
                    label_visibility="collapsed",
                )

                stock["theme"] = e.text_input(
                    "테마",
                    value=stock["theme"],
                    key=f"down_theme_{stock['symbol']}",
                    label_visibility="collapsed",
                )

        with st.expander(
            "📝 시황글 추가 내용 편집",
            expanded=True
        ):
            st.text_input(
                "한 줄 요약",
                key="summary",
                value="당일 등락률 상위 종목과 시장 흐름을 확인하세요."
            )

            st.text_area(
                "인트로 문구",
                key="intro_text",
                value=(
                    "종목 순위는 토스증권 Open API의 기간별 등락률 기준입니다. "
                    "실시간 순위와 다를 수 있으므로 기준 시각을 확인해 주세요."
                )
            )

            issues = [
                (1, "🔺", "당일 상승률 상위 종목 흐름"),
                (2, "🔻", "당일 하락률 상위 종목 흐름"),
                (3, "➖", "시장 변동성 확인"),
            ]

            for i, direction, title in issues:
                st.markdown(f"**이슈 {i}**")

                a, b, c = st.columns([1, 3, 3])

                a.selectbox(
                    "방향",
                    ["🔺", "🔻", "➖"],
                    index=["🔺", "🔻", "➖"].index(direction),
                    key=f"is{i}m",
                )

                b.text_input(
                    "제목",
                    value=title,
                    key=f"is{i}t",
                )

                c.text_input(
                    "관련 종목",
                    key=f"is{i}s",
                )

                st.text_input(
                    "내용",
                    key=f"is{i}c",
                )

                st.text_input(
                    "체크",
                    key=f"is{i}k",
                )

            st.text_input(
                "강했던 테마",
                key="themes",
                value="AI 반도체 · 2차전지",
            )

            st.text_input(
                "내일 체크 포인트 (/로 구분)",
                key="checks",
                value=(
                    "급등 종목 상승 지속 여부 / "
                    "급락 종목 추가 하락 여부 / "
                    "시장 거래대금 변화"
                ),
            )

            st.text_input(
                "핵심 키워드",
                key="keywords",
                value="#코스피 #코스닥 #급등주 #급락주 #주식시황",
            )

            st.text_input(
                "한 줄 주의",
                key="caution",
                value=(
                    "등락률 순위는 선택한 기간 기준이며 "
                    "실시간 순위와 다를 수 있습니다."
                ),
            )

        st.subheader("시황글 미리보기")

        now_text = datetime.datetime.now(KST).strftime("%H:%M")

        markdown = build_markdown(snapshot, now_text)

        copy_buttons(build_markdown(snapshot, NOW_TOKEN))

        with st.container(border=True):
            st.markdown(markdown)

        with st.expander("마크다운 원문 보기"):
            st.code(markdown, language="markdown")

    body()


main()
