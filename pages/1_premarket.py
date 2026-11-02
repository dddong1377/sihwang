import datetime
import hmac
import html
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote
from zoneinfo import ZoneInfo

import requests
import streamlit as st
import streamlit.components.v1 as components

st.set_page_config(page_title="MTNW 장전 시황", layout="wide")

KST = ZoneInfo("Asia/Seoul")
WEEKDAYS = "월화수목금토일"
NOW_TOKEN = "__NOW__"

YAHOO_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; MTNW-Market/1.0)"}

# (표시명, 야후 심볼, 소수 자리, 구분)
INDEX_ITEMS = (
    ("다우존스", "^DJI", 2, "index"),
    ("S&P500", "^GSPC", 2, "index"),
    ("나스닥", "^IXIC", 2, "index"),
    ("영국 FTSE100", "^FTSE", 2, "index"),
    ("프랑스 CAC40", "^FCHI", 2, "index"),
    ("독일 DAX", "^GDAXI", 2, "index"),
    ("유로스톡스50", "^STOXX50E", 2, "index"),
    ("원/달러 환율", "KRW=X", 2, "extra"),
    ("WTI 유가", "CL=F", 2, "extra"),
    ("미 10년물 금리(%)", "^TNX", 3, "extra"),
    ("VIX", "^VIX", 2, "extra"),
    ("달러인덱스", "DX-Y.NYB", 2, "extra"),
)


# ---------------------------------------------------------------
# 공통: 설정값 / 로그인 (오후 페이지와 같은 세션 비밀번호 사용)
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
        st.session_state["pw_ok"] = hmac.compare_digest(entered, expected.encode())
        st.session_state["pw"] = ""

    st.title("MTNW 시황")
    st.text_input("비밀번호", type="password", key="pw", on_change=verify)

    if st.session_state.get("pw_ok") is False:
        st.error("비밀번호가 올바르지 않습니다.")

    return False


if not login():
    st.stop()


# ---------------------------------------------------------------
# 해외 지수 (야후 비공식 차트 API, 최근 일봉 2개로 종가·전일 대비 계산)
# ---------------------------------------------------------------
def prev_weekday(day):
    day = day - datetime.timedelta(days=1)

    while day.weekday() >= 5:
        day -= datetime.timedelta(days=1)

    return day


@st.cache_data(ttl=120, show_spinner=False)
def yahoo_daily(symbol):
    """(기준일 YYYY-MM-DD, 종가, 전 거래일 종가). 실패하면 None."""

    try:
        response = requests.get(
            "https://query1.finance.yahoo.com/v8/finance/chart/"
            f"{quote(symbol, safe='')}",
            params={"range": "5d", "interval": "1d"},
            headers=YAHOO_HEADERS,
            timeout=8,
        )

        if response.status_code != 200:
            return None

        result = response.json()["chart"]["result"][0]
        stamps = result.get("timestamp") or []
        closes = result["indicators"]["quote"][0].get("close") or []
        zone = ZoneInfo(result["meta"].get("exchangeTimezoneName") or "UTC")

        rows = [
            (datetime.datetime.fromtimestamp(ts, zone).date().isoformat(), close)
            for ts, close in zip(stamps, closes)
            if close is not None
        ]

        if len(rows) < 2:
            return None

        return rows[-1][0], float(rows[-1][1]), float(rows[-2][1])

    except Exception:
        return None


def load_indices():
    expected = prev_weekday(datetime.datetime.now(KST).date()).isoformat()

    with ThreadPoolExecutor(max_workers=6) as pool:
        fetched = list(pool.map(lambda item: yahoo_daily(item[1]), INDEX_ITEMS))

    rows = []

    for (label, symbol, digits, kind), data in zip(INDEX_ITEMS, fetched):
        if not data:
            rows.append({
                "label": label, "kind": kind, "text": "확인 불가",
                "date": "-", "note": "",
            })
            continue

        day, close, prev = data
        change = close - prev
        pct = (close / prev - 1) * 100 if prev else 0.0
        arrow = "🔺" if change > 0 else "🔻" if change < 0 else "➖"
        note = "휴장/미갱신 가능" if day < expected else ""

        rows.append({
            "label": label,
            "kind": kind,
            "text": f"{close:,.{digits}f} ({change:+,.{digits}f}, {pct:+.2f}%) {arrow}",
            "date": day,
            "note": note,
        })

    return rows


def indices_markdown(rows):
    lines = ["| 구분 | 종가/현재가 (전일 대비) | 기준일 | 비고 |", "|---|---|---|---|"]

    for row in rows:
        lines.append(f"| {row['label']} | {row['text']} | {row['date']} | {row['note']} |")

    return "\n".join(lines)


def fetch_indices():
    state = st.session_state

    try:
        rows = load_indices()
        state["pm_indices"] = rows
        state["pm_indices_note"] = (
            f"조회 완료 · {datetime.datetime.now(KST).strftime('%H:%M:%S')} · "
            "야후 파이낸스(비공식) 기준, 휴장/미갱신 표시를 확인하세요."
        )
    except Exception as error:
        state["pm_indices_note"] = f"지수 조회 실패: {error}"


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
- 아래 [확인된 지수 데이터]를 해외 증시 표의 기준 수치로 사용한다. 이 표에 없는 수치는 검색으로 교차 확인하고, 확인되지 않으면 "확인 불가"로 쓴다.
- 비고에 "휴장/미갱신 가능"이 있는 지수는 검색으로 휴장 여부를 확인해 사유를 표기한다.
- 표기 형식: 지수명 : 지수값 (전일 대비 ±포인트, ±%) — 소수점 둘째 자리까지
- 대상: 다우, S&P500, 나스닥, 영국 FTSE100, 프랑스 CAC40, 독일 DAX, 유로스톡스50

[확인된 지수 데이터]
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
        raise RuntimeError("Streamlit Secrets에 ANTHROPIC_API_KEY를 설정하세요.")

    model = secret("ANTHROPIC_MODEL_PREMARKET") or "claude-sonnet-5-5"

    body = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }

    if search:
        body["tools"] = [{
            "type": "web_search_20250305",
            "name": "web_search",
            "max_uses": 10,
        }]

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
            raise RuntimeError(f"HTTP {response.status_code}: {response.text[:400]}")

        data = response.json()
        content = data.get("content", [])

        texts.extend(
            block.get("text", "")
            for block in content
            if block.get("type") == "text"
        )

        if data.get("stop_reason") == "pause_turn":
            body["messages"] = body["messages"] + [
                {"role": "assistant", "content": content}
            ]
            continue

        break

    result = "".join(texts).strip()

    # 검색 과정 설명이 앞에 붙은 경우 리포트 제목부터 사용
    start = result.find("## 📅")

    if start > 0:
        result = result[start:]

    if not result:
        raise RuntimeError("응답이 비어 있습니다.")

    return result


def run_premarket(use_source_only=False):
    """콜백: (원문 생성) → 압축. 결과를 session_state에 저장."""

    state = st.session_state
    now = datetime.datetime.now(KST)
    today = f"{now.year}년 {now.month:02d}월 {now.day:02d}일 ({WEEKDAYS[now.weekday()]})"

    try:
        pasted = state.get("pm_pasted", "").strip()

        if use_source_only and pasted:
            source = pasted
        else:
            rows = state.get("pm_indices")

            if not rows:
                rows = load_indices()
                state["pm_indices"] = rows

            prompt = (
                PREMARKET_PROMPT
                .replace("__TODAY__", today)
                .replace("__INDEX_TABLE__", indices_markdown(rows))
            )

            with st.spinner("① 웹검색으로 장전 시황 원본 작성 중... (1~3분)"):
                source = call_claude(prompt, search=True, max_tokens=8000)

            state["pm_long"] = source

        with st.spinner("② 한눈에 보이는 시황글로 압축 중..."):
            short = call_claude(
                COMPRESS_PROMPT.replace("__SOURCE__", source),
                search=False,
                max_tokens=3000,
            )

        state["pm_short"] = short
        state["pm_note"] = (
            f"생성 완료 · {datetime.datetime.now(KST).strftime('%H:%M:%S')} · "
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
        return re.sub(r"\*\*(.*?)\*\*", r"<b>\1</b>", safe)

    for line in markdown.splitlines():
        stripped = line.strip()

        if stripped.startswith("|"):
            cells = [cell.strip() for cell in stripped.split("|")[1:-1]]

            if all(re.fullmatch(r":?-+:?", cell) for cell in cells):
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
                "<tr>" + "".join(f"<td>{inline(cell)}</td>" for cell in cells) + "</tr>"
            )
            continue

        if in_table:
            output.append("</table>")
            in_table = False

        if stripped.startswith("- "):
            if not in_list:
                output.append("<ul>")
                in_list = True

            output.append(f"<li>{inline(stripped[2:])}</li>")
            continue

        if in_list:
            output.append("</ul>")
            in_list = False

        if stripped.startswith("### "):
            output.append(f"<h3>{inline(stripped[4:])}</h3>")
        elif stripped.startswith("## "):
            output.append(f"<h2>{inline(stripped[3:])}</h2>")
        elif stripped.startswith("# "):
            output.append(f"<h1>{inline(stripped[2:])}</h1>")
        elif stripped.startswith("> "):
            output.append(f"<p>{inline(stripped[2:])}</p>")
        elif stripped:
            output.append(f"<p>{inline(stripped)}</p>")

    if in_table:
        output.append("</table>")

    if in_list:
        output.append("</ul>")

    return "\n".join(output)


def copy_buttons(markdown):
    payload = json.dumps(
        {"md": markdown, "html": md_to_html(markdown)},
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
                    'text/html': new Blob([P.html], {{type: 'text/html'}}),
                    'text/plain': new Blob([P.md], {{type: 'text/plain'}})
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
        "한눈에 보이는 시황글로 압축합니다. 오후 시황은 왼쪽 메뉴의 'app'에서 작성합니다."
    )

    # 1) 해외 지수
    st.markdown("### 🌎 ① 해외 지수·지표 확인")

    with st.container(border=True):
        col_btn, col_note = st.columns([1.6, 5], vertical_alignment="center")

        col_btn.button("🔄 해외 지수 불러오기", on_click=fetch_indices, type="primary")

        if state.get("pm_indices_note"):
            col_note.caption(state["pm_indices_note"])

        rows = state.get("pm_indices")

        if rows:
            st.markdown(indices_markdown(rows))
        else:
            st.caption("아직 조회 전입니다. 생성 버튼을 눌러도 지수는 자동으로 먼저 조회됩니다.")

    # 2) 생성
    st.markdown("### ✨ ② 장전 시황 생성")

    with st.container(border=True):
        st.text_area(
            "원문 직접 붙여넣기 (선택)",
            key="pm_pasted",
            height=140,
            help="이미 만들어 둔 긴 시황글이 있으면 붙여넣고 '압축만 실행'을 누르세요. 웹검색 단계를 건너뜁니다.",
        )

        col_a, col_b, col_c = st.columns([1.8, 1.4, 4], vertical_alignment="center")

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

    tab_short, tab_long = st.tabs(["한눈에 보이는 시황글", "원본 상세 리포트"])

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
            st.caption("원본 상세 리포트가 아직 없습니다. 직접 붙여넣은 원문만 압축한 경우에는 표시되지 않습니다.")


main()
