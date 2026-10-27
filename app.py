"""
MTNW 오후 시황 (Streamlit 배포용)
- 입장 시 비밀번호 입력
- 한국투자증권(KIS) Open API로 코스피·코스닥, 급등·급락 종목 TOP N 조회 (같은 시각 기준)
- 네이버 뉴스 API로 종목별 오늘자 헤드라인을 이유 후보로 입력, KIS 업종 + 키워드로 테마 자동 분류
- 이유·테마를 고친 뒤 [복사] 버튼으로 시황글을 클립보드에 저장

필요한 Secrets (Streamlit 설정 > Secrets):
  app_password, KIS_APP_KEY, KIS_APP_SECRET, NAVER_CLIENT_ID(선택), NAVER_CLIENT_SECRET(선택)
"""
import hmac
import html as htmllib
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from zoneinfo import ZoneInfo

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
    except Exception:  # secrets 파일이 아예 없는 경우
        v = None
    return str(v) if v else os.getenv(name, default)


def check_password() -> bool:
    expected = secret("app_password")
    if not expected:
        # 비밀번호가 설정되지 않았으면 열어 두지 않고 막는다 (실수로 공개되는 것을 방지)
        st.error("비밀번호가 설정되지 않았습니다. Streamlit 설정의 Secrets에 app_password를 추가하세요.")
        return False

    def entered():
        typed = st.session_state.get("pw", "").encode("utf-8")
        st.session_state["pw_ok"] = hmac.compare_digest(typed, expected.encode("utf-8"))
        st.session_state["pw"] = ""  # 입력값은 바로 지운다

    if st.session_state.get("pw_ok"):
        return True
    st.title("MTNW 시황")
    st.text_input("비밀번호", type="password", key="pw", on_change=entered)
    if st.session_state.get("pw_ok") is False:
        st.error("비밀번호가 올바르지 않습니다.")
    return False


if not check_password():
    st.stop()  # 통과하기 전에는 아래 코드(증권사·뉴스 API 호출 포함)가 실행되지 않는다

# ---- 비밀번호 통과 후 설정 값 읽기 (메인 스레드에서 한 번만) ----
KIS_APP_KEY = secret("KIS_APP_KEY")
KIS_APP_SECRET = secret("KIS_APP_SECRET")
KIS_BASE = secret("KIS_BASE_URL", "https://openapi.koreainvestment.com:9443")
NAVER_ID = secret("NAVER_CLIENT_ID")
NAVER_SECRET = secret("NAVER_CLIENT_SECRET")


@st.cache_resource
def _store():
    # 서버가 켜져 있는 동안 유지되는 저장소 (토큰, 업종, 뉴스 캐시)
    return {"token": {}, "sector": {}, "news": {}}


STORE = _store()
NEWS_TTL = 300

EXCLUDE_NAME = re.compile(
    r"스팩|제\d+호|ETN|KODEX|TIGER|ACE |SOL |RISE|HANARO|KOSEF|ARIRANG|PLUS |KIWOOM|우[A-C]?$"
)
REASON_WORDS = re.compile(r"특징주|수주|계약|실적|공시|승인|인수|합병|유증|증자|전망")
UP_WORDS = re.compile(r"급등|상승|강세|상한가|신고가|반등|매수|호재|훈풍")
DOWN_WORDS = re.compile(r"급락|하락|약세|하한가|신저가|매도|악재|차익|우려|부진")

# 헤드라인·종목명·업종명에서 테마를 뽑는 규칙 (앞에서부터 최대 2개). 필요하면 자유롭게 추가하세요.
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


# ============================================================ KIS API
class ApiError(Exception):
    pass


def kis_token():
    """토큰은 24시간 유효하고 발급 횟수 제한이 있어 서버 메모리에 저장해 재사용한다."""
    t = STORE["token"]
    if t.get("token") and t.get("expires", 0) > time.time() + 300 and t.get("key") == KIS_APP_KEY:
        return t["token"]
    if not KIS_APP_KEY or not KIS_APP_SECRET:
        raise ApiError("KIS 앱키가 없습니다. Secrets에 KIS_APP_KEY / KIS_APP_SECRET을 추가하세요.")
    r = requests.post(
        f"{KIS_BASE}/oauth2/tokenP",
        json={"grant_type": "client_credentials", "appkey": KIS_APP_KEY, "appsecret": KIS_APP_SECRET},
        timeout=10,
    )
    body = r.json() if r.content else {}
    if r.status_code != 200 or "access_token" not in body:
        raise ApiError(f"KIS 토큰 발급 실패: {body.get('error_description') or body.get('msg1') or r.status_code}")
    STORE["token"] = {"token": body["access_token"], "expires": time.time() + 23 * 3600, "key": KIS_APP_KEY}
    return body["access_token"]


def kis_get(path, tr_id, params):
    headers = {
        "content-type": "application/json; charset=utf-8",
        "authorization": f"Bearer {kis_token()}",
        "appkey": KIS_APP_KEY,
        "appsecret": KIS_APP_SECRET,
        "tr_id": tr_id,
        "custtype": "P",
    }
    r = requests.get(f"{KIS_BASE}{path}", headers=headers, params=params, timeout=10)
    body = r.json() if r.content else {}
    if r.status_code != 200 or body.get("rt_cd") not in (None, "0"):
        raise ApiError(f"KIS 오류 {tr_id}: {body.get('msg1') or r.status_code}")
    return body


def num(v, default=0.0):
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return default


def signed(value, sign_code):
    v = num(value)
    if str(sign_code) in ("4", "5") and v > 0:
        return -v
    return v


def fetch_index(code, name):
    body = kis_get(
        "/uapi/domestic-stock/v1/quotations/inquire-index-price",
        "FHPUP02100000",
        {"FID_COND_MRKT_DIV_CODE": "U", "FID_INPUT_ISCD": code},
    )
    o = body.get("output") or {}
    if isinstance(o, list):
        o = o[0] if o else {}
    sign = o.get("prdy_vrss_sign", "")
    return {
        "name": name,
        "price": num(o.get("bstp_nmix_prpr")),
        "change": signed(o.get("bstp_nmix_prdy_vrss"), sign),
        "pct": signed(o.get("bstp_nmix_prdy_ctrt"), sign),
    }


def fetch_rank(sort_code, min_vol):
    params = {
        "fid_rsfl_rate2": "",
        "fid_cond_mrkt_div_code": "J",
        "fid_cond_scr_div_code": "20170",
        "fid_input_iscd": "0000",
        "fid_rank_sort_cls_code": sort_code,
        "fid_input_cnt_1": "0",
        "fid_prc_cls_code": "0",
        "fid_input_price_1": "1000",  # 1,000원 미만 동전주 제외
        "fid_input_price_2": "",
        "fid_vol_cnt": str(min_vol),
        "fid_trgt_cls_code": "0",
        "fid_trgt_exls_cls_code": "0",
        "fid_div_cls_code": "0",
        "fid_rsfl_rate1": "",
    }
    body = kis_get("/uapi/domestic-stock/v1/ranking/fluctuation", "FHPST01700000", params)
    rows = []
    for o in body.get("output") or []:
        sign = o.get("prdy_vrss_sign", "")
        rows.append(
            {
                "code": o.get("stck_shrn_iscd", ""),
                "name": o.get("hts_kor_isnm", "").strip(),
                "price": num(o.get("stck_prpr")),
                "pct": signed(o.get("prdy_ctrt"), sign),
                "volume": num(o.get("acml_vol")),
            }
        )
    return rows


def select_movers(min_vol, n, min_pct):
    """상승률순·하락률순을 각각 받아 합친 뒤 응답의 실제 등락률 부호로 직접 분류한다."""
    warnings = []
    up_rows = fetch_rank("0", min_vol)
    down_rows = fetch_rank("1", min_vol)
    pool = {r["code"]: r for r in up_rows + down_rows if r["name"] and not EXCLUDE_NAME.search(r["name"])}
    rows = list(pool.values())
    gainers = sorted([r for r in rows if r["pct"] >= min_pct], key=lambda r: -r["pct"])[:n]
    losers = sorted([r for r in rows if r["pct"] <= -min_pct], key=lambda r: r["pct"])[:n]
    if not any(r["pct"] < 0 for r in down_rows):
        warnings.append("하락률 순위 응답에서 하락 종목이 확인되지 않았습니다. 사이드바의 [연결 점검]을 확인하세요.")
    if len(gainers) < n:
        warnings.append(f"급등 기준(+{min_pct}%) 충족 종목 {len(gainers)}개")
    if len(losers) < n:
        warnings.append(f"급락 기준(-{min_pct}%) 충족 종목 {len(losers)}개")
    return gainers, losers, warnings


def fetch_sector(code):
    if code in STORE["sector"]:
        return STORE["sector"][code]
    body = kis_get(
        "/uapi/domestic-stock/v1/quotations/inquire-price",
        "FHKST01010100",
        {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code},
    )
    o = body.get("output") or {}
    STORE["sector"][code] = ((o.get("bstp_kor_isnm") or "").strip(), (o.get("rprs_mrkt_kor_name") or "").strip())
    return STORE["sector"][code]


def attach_sector(stocks, status):
    def work(s):
        try:
            return fetch_sector(s["code"]), None
        except Exception as e:  # noqa: BLE001
            return ("", ""), str(e)

    with ThreadPoolExecutor(max_workers=4) as ex:
        results = list(ex.map(work, stocks))
    fails = 0
    for s, ((sector, market), err) in zip(stocks, results):
        s["sector"], s["market"] = sector, market
        fails += 1 if err else 0
    status["sector"] = "정상" if not fails else f"일부 실패 {fails}건"


# ============================================================ 환율 (비공식)
def fetch_fx():
    r = requests.get(
        "https://query1.finance.yahoo.com/v8/finance/chart/KRW=X",
        params={"interval": "1m", "range": "1d"},
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=8,
    )
    meta = r.json()["chart"]["result"][0]["meta"]
    price = float(meta["regularMarketPrice"])
    prev = float(meta.get("chartPreviousClose") or meta.get("previousClose") or price)
    t = datetime.fromtimestamp(meta.get("regularMarketTime", time.time()), KST)
    return {"price": price, "pct": (price - prev) / prev * 100 if prev else 0.0, "time": t.strftime("%H:%M")}


# ============================================================ 네이버 뉴스 (이유 후보)
def clean_title(s):
    s = re.sub(r"<[^>]+>", "", s)
    return (
        s.replace("&quot;", '"').replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").replace("&apos;", "'")
    ).strip()


def fetch_headlines(name, today, up=True):
    out, seen = [], set()
    for suffix in ("특징주", "주가"):  # 특징주 기사가 없으면 일반 주가 기사로 한 번 더 찾는다
        r = requests.get(
            "https://openapi.naver.com/v1/search/news.json",
            params={"query": f"{name} {suffix}", "display": 20, "sort": "date"},
            headers={"X-Naver-Client-Id": NAVER_ID, "X-Naver-Client-Secret": NAVER_SECRET},
            timeout=8,
        )
        if r.status_code != 200:
            raise ApiError(f"네이버 API 오류 {r.status_code}")
        for it in r.json().get("items", []):
            title = clean_title(it.get("title", ""))
            try:
                d = datetime.strptime(it["pubDate"], "%a, %d %b %Y %H:%M:%S %z").astimezone(KST)
            except Exception:
                continue
            if d.date() != today or name not in title or title in seen:
                continue
            seen.add(title)
            same, opposite = (UP_WORDS, DOWN_WORDS) if up else (DOWN_WORDS, UP_WORDS)
            score = (2 if same.search(title) else 0) + (1 if REASON_WORDS.search(title) else 0)
            if opposite.search(title) and not same.search(title):
                score -= 2  # 종목 방향과 반대되는 기사는 뒤로
            out.append({"title": title, "time": d.strftime("%H:%M"), "score": score})
        if out:
            break
    out.sort(key=lambda x: (x["score"], x["time"]), reverse=True)
    return [{"title": o["title"], "time": o["time"]} for o in out[:4]]


def attach_news(stocks, status):
    if not (NAVER_ID and NAVER_SECRET):
        status["naver"] = "키 없음 (이유는 자동 분류 또는 직접 입력)"
        for s in stocks:
            s["headlines"] = []
        return
    today = datetime.now(KST).date()

    def work(s):
        k = (s["name"], s["pct"] > 0)
        hit = STORE["news"].get(k)
        if hit and time.time() - hit[0] < NEWS_TTL:
            return hit[1], None
        try:
            h = fetch_headlines(s["name"], today, s["pct"] > 0)
            STORE["news"][k] = (time.time(), h)
            return h, None
        except Exception as e:  # noqa: BLE001
            return [], str(e)

    with ThreadPoolExecutor(max_workers=5) as ex:
        results = list(ex.map(work, stocks))
    fails = 0
    for s, (h, err) in zip(stocks, results):
        s["headlines"] = h
        fails += 1 if err else 0
    status["naver"] = "정상" if not fails else f"일부 실패 {fails}건"


def derive_theme(stock):
    """헤드라인 → 종목명 → 업종명 순서로 키워드를 찾아 테마를 만든다. 못 찾으면 업종명."""
    texts = [" ".join(h["title"] for h in stock.get("headlines", [])), stock["name"], stock.get("sector", "")]
    found = []
    for text in texts:
        for label, pattern in THEME_RULES:
            if label not in found and re.search(pattern, text):
                found.append(label)
        if found:
            break
    return " · ".join(found[:2]) if found else stock.get("sector", "")


def fill_fallback_reasons(stocks, up):
    """오늘자 기사가 없는 종목은 데이터로 확인되는 사실만 이유로 쓴다 (추측 금지)."""
    word = "강세" if up else "약세"
    by_sector = {}
    for st_ in stocks:
        by_sector.setdefault(st_.get("sector", ""), []).append(st_["name"])
    for st_ in stocks:
        if st_.get("headlines"):
            continue
        sec = st_.get("sector", "")
        peers = by_sector.get(sec, [])
        if sec and len(peers) >= 2:
            st_["auto_reason"] = f"🏷️ {sec} 업종 동반 {word} ({len(peers)}개 종목 같은 방향)"
        else:
            st_["auto_reason"] = f"📊 거래량 {st_['volume'] / 10000:,.0f}만주 동반, 오늘자 개별 기사 미확인"


def fetch_snapshot(n, min_pct, min_vol):
    status = {}
    now = datetime.now(KST)
    snap = {
        "ts": time.time(),
        "fetched_at": now.strftime("%H:%M:%S"),
        "date": f"{now.year}년 {now.month:02d}월 {now.day:02d}일 ({WEEKDAYS[now.weekday()]})",
        "kospi": fetch_index("0001", "코스피"),
        "kosdaq": fetch_index("1001", "코스닥"),
    }
    gainers, losers, warnings = select_movers(min_vol, n, min_pct)
    snap["gainers"], snap["losers"], snap["warnings"] = gainers, losers, warnings
    status["kis"] = "정상"
    try:
        snap["fx"] = fetch_fx()
        status["fx"] = "정상 (Yahoo 비공식)"
    except Exception:  # noqa: BLE001
        snap["fx"] = None
        status["fx"] = "실패 (직접 입력)"
    movers = gainers + losers
    try:
        attach_sector(movers, status)
    except Exception:  # noqa: BLE001
        status["sector"] = "실패"
        for s in movers:
            s.setdefault("sector", "")
            s.setdefault("market", "")
    attach_news(movers, status)
    for s in movers:
        s["theme"] = derive_theme(s)
    fill_fallback_reasons(gainers, True)
    fill_fallback_reasons(losers, False)
    snap["status"] = status
    return snap


# ============================================================ 시황글 생성
def fmt(v, d=2):
    return f"{v:,.{d}f}"


def sgn(v):
    return f"{v:+,.2f}%"


def em(v):
    return "🔺" if v > 0 else "🔻" if v < 0 else "➖"


def auto_reason(s):
    h = s.get("headlines") or []
    return "📰 " + h[0]["title"] if h else s.get("auto_reason", "")


def build_md(snap):
    ss = st.session_state
    tm = snap["fetched_at"][:5]
    L = [f"# 📅 {snap['date']} | 오후 시황", f"**조회 기준: {tm}**", ""]
    if ss.get("summary", "").strip():
        L += [f"> 📌 **한 줄 요약**: {ss['summary'].strip()}", ""]

    L += ["### 🇰🇷 장중 지수 현황", "", "| 지수 | 현재가 | 등락 | 기준 시각 |", "|---|---:|---:|---|"]
    for x, name in ((snap["kospi"], "코스피"), (snap["kosdaq"], "코스닥")):
        L.append(f"| {name} | {fmt(x['price'])} | {em(x['pct'])} {sgn(x['pct'])} | {tm} |")
    try:
        fp = float(str(ss.get("fx_price", "")).replace(",", ""))
        try:
            fc = float(str(ss.get("fx_pct", "")).replace(",", ""))
            fcs = f"{em(fc)} {sgn(fc)}"
        except ValueError:
            fcs = "-"
        L.append(f"| 원/달러 | {fmt(fp)}원 | {fcs} | {ss.get('fx_time') or tm} |")
    except ValueError:
        L.append("| 원/달러 | 확인 불가 | 확인 불가 | - |")
    L.append("")

    for title, lst, word in (
        (f"🔺 급등 특징주 TOP{len(snap['gainers'])}", snap["gainers"], "상승"),
        (f"🔻 급락 특징주 TOP{len(snap['losers'])}", snap["losers"], "하락"),
    ):
        L += [f"### {title}", ""]
        if not lst:
            L += ["기준을 충족하는 종목이 없습니다.", ""]
            continue
        L += [f"| 종목 | 등락률 | 기준 시각 | {word} 이유 | 관련 테마 |", "|---|---:|---|---|---|"]
        for s in lst:
            reason = (ss.get(f"reason_{s['code']}") or "-").replace("|", "/")
            theme = (ss.get(f"theme_{s['code']}") or "-").replace("|", "/")
            L.append(f"| **{s['name']}** | **{sgn(s['pct'])}** | {tm} | {reason} | {theme} |")
        L.append("")

    issues = []
    for i, no in enumerate("①②③", start=1):
        t = ss.get(f"is{i}t", "").strip()
        if not t:
            continue
        issues.append(f"**{no} {t}** {ss.get(f'is{i}m', '🔺')}")
        if ss.get(f"is{i}c", "").strip():
            issues.append(f"- 내용: {ss[f'is{i}c'].strip()}")
        if ss.get(f"is{i}s", "").strip():
            names = ", ".join(f"**{x.strip()}**" for x in re.split(r"[,，]", ss[f"is{i}s"]) if x.strip())
            issues.append(f"- 📌 관련 종목: {names}")
        if ss.get(f"is{i}k", "").strip():
            issues.append(f"- ⏰ 체크: {ss[f'is{i}k'].strip()}")
        issues.append("")
    if issues:
        L += ["### 🔥 오늘의 주요 경제 이슈", ""] + issues

    th = ss.get("themes", "").strip()
    if th:
        L += ["### 🏷️ 오늘 강했던 테마", "", " · ".join(f"**{x.strip()}**" for x in re.split(r"[·,]", th) if x.strip()), ""]
    ck = ss.get("checks", "").strip()
    if ck:
        L += ["### 👀 내일 체크 포인트", ""] + [f"- {x.strip()}" for x in ck.split("/") if x.strip()] + [""]
    kw = ss.get("keywords", "").strip()
    if kw:
        L += ["### 🔍 핵심 키워드", "", kw, ""]
    ca = ss.get("caution", "").strip()
    if ca:
        L += ["### ⚠️ 한 줄 주의", "", ca, ""]

    stt = snap["status"]
    L += [
        "### 🔎 조회 현황",
        "",
        f"- 지수·종목 시세: 한국투자증권 Open API, {tm} 조회 (전 종목 같은 시각)",
        f"- 업종·테마: KIS 업종 + 헤드라인 키워드 자동 분류 ({stt.get('sector', '-')}), 게시 전 확인 필요",
        f"- 환율: {stt.get('fx', '-')}",
        f"- 이유: 네이버 뉴스 오늘자 헤드라인 기반 ({stt.get('naver', '-')}), 게시 전 확인 필요",
        "- 안내: 수치는 조회 시각 기준이며 이후 달라질 수 있습니다.",
    ]
    return "\n".join(L)


def _inline(s):
    return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", htmllib.escape(s))


def md_to_html(md):
    """이 글에서 쓰는 마크다운 문법만 HTML로 바꾼다 (서식 포함 복사용)."""
    out, tbl, ul = [], False, False
    for line in md.split("\n"):
        if line.startswith("|"):
            if ul:
                out.append("</ul>")
                ul = False
            cells = [c.strip() for c in line.split("|")[1:-1]]
            if all(re.fullmatch(r":?-+:?", c) for c in cells):
                continue
            if not tbl:
                tbl = True
                out.append(
                    '<table border="1" cellspacing="0" cellpadding="6" style="border-collapse:collapse"><thead><tr>'
                    + "".join(f"<th>{_inline(c)}</th>" for c in cells)
                    + "</tr></thead><tbody>"
                )
            else:
                out.append("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in cells) + "</tr>")
            continue
        if tbl:
            out.append("</tbody></table>")
            tbl = False
        if line.startswith("- "):
            if not ul:
                out.append("<ul>")
                ul = True
            out.append(f"<li>{_inline(line[2:])}</li>")
            continue
        if ul:
            out.append("</ul>")
            ul = False
        if line.startswith("# "):
            out.append(f"<h1>{_inline(line[2:])}</h1>")
        elif line.startswith("### "):
            out.append(f"<h3>{_inline(line[4:])}</h3>")
        elif line.startswith("> "):
            out.append(f"<blockquote>{_inline(line[2:])}</blockquote>")
        elif line.strip():
            out.append(f"<p>{_inline(line)}</p>")
    if ul:
        out.append("</ul>")
    if tbl:
        out.append("</tbody></table>")
    return "\n".join(out)


COPY_HTML = """
<div style="display:flex;gap:8px;align-items:center;font-family:sans-serif;font-size:13px">
  <button id="r" style="padding:8px 14px;border-radius:6px;border:0;background:#0f5c5c;color:#fff;font-weight:600;cursor:pointer">복사 (서식 포함)</button>
  <button id="m" style="padding:8px 14px;border-radius:6px;border:1px solid #bbb;background:#fff;cursor:pointer">복사 (마크다운)</button>
  <span id="t" style="color:#555"></span>
</div>
<script>
const P = __PAYLOAD__;
const msg = s => { document.getElementById('t').textContent = s; };
function copyText(text){
  const ta = document.createElement('textarea'); ta.value = text; document.body.appendChild(ta);
  ta.select(); const ok = document.execCommand('copy'); ta.remove(); return ok;
}
function copyRichFallback(html){
  const d = document.createElement('div'); d.innerHTML = html; d.style.position = 'fixed'; d.style.left = '-9999px';
  document.body.appendChild(d);
  const r = document.createRange(); r.selectNodeContents(d);
  const s = window.getSelection(); s.removeAllRanges(); s.addRange(r);
  const ok = document.execCommand('copy'); s.removeAllRanges(); d.remove(); return ok;
}
document.getElementById('r').onclick = async () => {
  try {
    if (window.ClipboardItem && navigator.clipboard && navigator.clipboard.write) {
      await navigator.clipboard.write([new ClipboardItem({
        'text/html': new Blob([P.html], {type: 'text/html'}),
        'text/plain': new Blob([P.md], {type: 'text/plain'})})]);
      msg('서식 포함으로 복사했습니다'); return;
    }
  } catch (e) {}
  msg(copyRichFallback(P.html) ? '서식 포함으로 복사했습니다' : '복사 실패: 아래 코드 상자의 복사 아이콘을 사용하세요');
};
document.getElementById('m').onclick = async () => {
  try { await navigator.clipboard.writeText(P.md); msg('마크다운으로 복사했습니다'); return; } catch (e) {}
  msg(copyText(P.md) ? '마크다운으로 복사했습니다' : '복사 실패: 아래 코드 상자의 복사 아이콘을 사용하세요');
};
</script>
"""


def copy_buttons(md):
    payload = json.dumps({"md": md, "html": md_to_html(md)}, ensure_ascii=False).replace("</", "<\\/")
    page = COPY_HTML.replace("__PAYLOAD__", payload)
    if hasattr(st, "iframe"):          # 최신 Streamlit
        st.iframe(page, height=56)
    else:                              # 구버전 호환
        components.html(page, height=56)


# ============================================================ 화면
def touch(key):
    st.session_state.setdefault("touched", set()).add(key)


def colored(v, text):
    return f":red[{text}]" if v > 0 else f":blue[{text}]" if v < 0 else text


def apply_defaults(snap):
    """자동값을 입력칸에 채운다. 사용자가 직접 고친 칸은 건드리지 않는다."""
    ss = st.session_state
    touched = ss.setdefault("touched", set())
    for s in snap["gainers"] + snap["losers"]:
        for f, val in (("reason", auto_reason(s)), ("theme", s.get("theme", ""))):
            key = f"{f}_{s['code']}"
            if key not in touched:
                ss[key] = val
    if snap.get("fx"):
        for key, val in (("fx_price", fmt(snap["fx"]["price"])), ("fx_pct", fmt(snap["fx"]["pct"])), ("fx_time", snap["fx"]["time"])):
            if key not in touched:
                ss[key] = val
    else:
        for key in ("fx_price", "fx_pct", "fx_time"):
            ss.setdefault(key, "")
    if "keywords" not in touched:
        names = [s["name"] for s in (snap["gainers"] + snap["losers"])[:8]]
        ss["keywords"] = " ".join("#" + w.replace(" ", "") for w in ["코스피", "코스닥"] + names)


def stock_table(title, stocks, word):
    st.subheader(title)
    if not stocks:
        st.info("기준을 충족하는 종목이 없습니다. 사이드바에서 기준 등락률을 낮춰 다시 조회해 보세요.")
        return
    w = [2.2, 1.2, 1.3, 4.5, 2.2]
    head = st.columns(w)
    for c, label in zip(head, ["종목", "현재가", "등락률", f"{word} 이유", "관련 테마"]):
        c.caption(label)
    for s in stocks:
        c = st.columns(w)
        c[0].markdown(f"**{s['name']}**  \n:gray[{s.get('market', '')} {s.get('sector', '')}]")
        c[1].markdown(f"{s['price']:,.0f}")
        c[2].markdown(colored(s["pct"], f"{em(s['pct'])} {sgn(s['pct'])}"))
        rk, tk = f"reason_{s['code']}", f"theme_{s['code']}"
        c[3].text_input("이유", key=rk, label_visibility="collapsed", on_change=touch, args=(rk,))
        alts = [h["title"] for h in (s.get("headlines") or [])[1:3]]
        if alts:
            c[3].caption("다른 기사: " + " / ".join(alts))
        c[4].text_input("테마", key=tk, label_visibility="collapsed", on_change=touch, args=(tk,))


def manual_inputs():
    with st.expander("직접 쓰는 부분 (비워 두면 시황글에서 빠집니다)", expanded=False):
        st.text_input("한 줄 요약", key="summary", placeholder="20~30자")
        for i in (1, 2, 3):
            st.markdown(f"**이슈 {i}**")
            a, b, c = st.columns([1, 3, 3])
            a.selectbox("방향", ["🔺", "🔻", "➖"], key=f"is{i}m", label_visibility="collapsed")
            b.text_input("제목", key=f"is{i}t", placeholder="제목 (15자 내외)", label_visibility="collapsed")
            c.text_input("관련 종목", key=f"is{i}s", placeholder="관련 종목 (쉼표로 구분)", label_visibility="collapsed")
            st.text_input("내용", key=f"is{i}c", placeholder="내용 한 줄", label_visibility="collapsed")
            st.text_input("체크", key=f"is{i}k", placeholder="체크: 향후 일정 또는 리스크 한 줄", label_visibility="collapsed")
        st.text_input("강했던 테마", key="themes", placeholder="반도체 · HBM · 2차전지")
        st.text_input("내일 체크 (/ 로 구분)", key="checks")
        st.text_input("핵심 키워드", key="keywords", on_change=touch, args=("keywords",))
        st.text_input("한 줄 주의", key="caution", placeholder="급등 이후 변동성 유의")


def diagnostics():
    """배포 환경에서 증권사 연결이 되는지 확인한다."""
    try:
        st.write("코스피:", fetch_index("0001", "코스피"))
        for code in ("0", "1"):
            rows = fetch_rank(code, 100000)
            st.write(f"등락률 순위 sort={code} ({len(rows)}건) 상위 3:", rows[:3])
    except Exception as e:  # noqa: BLE001
        st.error(f"연결 실패: {e}")
        st.caption("해외 서버(Streamlit Cloud)에서 증권사 API가 막혀 있을 수 있습니다. 내 컴퓨터에서 실행하면 되는지 확인해 보세요.")


def main():
    st.title("MTNW 오후 시황")
    with st.sidebar:
        st.header("설정")
        n = st.number_input("종목 수 (급등·급락 각각)", 1, 20, 10)
        min_pct = st.number_input("기준 등락률 ± (%)", 0.0, 30.0, 3.0, 0.5)
        min_vol = st.number_input("최소 거래량 (주)", 0, 10_000_000, 100_000, 50_000)
        auto = st.selectbox("자동 갱신", [0, 30, 60, 120], format_func=lambda x: "끔" if x == 0 else f"{x}초")
        if st.button("연결 점검"):
            diagnostics()
        if st.button("로그아웃"):
            st.session_state["pw_ok"] = False
            st.rerun()

    @st.fragment(run_every=auto if auto else None)
    def body():
        ss = st.session_state
        clicked = st.button("지금 시세 조회", type="primary")
        snap = ss.get("snap")
        due = bool(auto) and snap is not None and time.time() - snap["ts"] >= auto - 1
        if clicked or due:
            try:
                with st.spinner("시세 조회 중..."):
                    snap = fetch_snapshot(int(n), float(min_pct), int(min_vol))
                ss["snap"] = snap
                apply_defaults(snap)
            except ApiError as e:
                st.error(str(e))
            except Exception as e:  # noqa: BLE001
                st.error(f"조회 실패: {e}")
            snap = ss.get("snap")
        if not snap:
            st.info("[지금 시세 조회]를 누르세요.")
            return

        stt = snap["status"]
        st.caption(
            f"{snap['date']} · 조회 {snap['fetched_at']} · KIS {stt.get('kis')} · 환율 {stt.get('fx')} · "
            f"뉴스 {stt.get('naver')} · 업종 {stt.get('sector', '-')}"
        )
        for w in snap.get("warnings", []):
            st.warning(w)

        c1, c2, c3 = st.columns(3)
        for c, x, name in ((c1, snap["kospi"], "코스피"), (c2, snap["kosdaq"], "코스닥")):
            c.markdown(f"**{name}**  \n### {fmt(x['price'])}")
            c.markdown(colored(x["pct"], f"{em(x['pct'])} {sgn(x['pct'])}"))
        c3.markdown("**원/달러** (직접 수정 가능)")
        f1, f2 = c3.columns(2)
        f1.text_input("환율(원)", key="fx_price", on_change=touch, args=("fx_price",))
        f2.text_input("등락률(%)", key="fx_pct", on_change=touch, args=("fx_pct",))

        stock_table(f"🔺 급등 특징주 TOP{len(snap['gainers'])}", snap["gainers"], "상승")
        stock_table(f"🔻 급락 특징주 TOP{len(snap['losers'])}", snap["losers"], "하락")
        manual_inputs()

        st.subheader("시황글 미리보기")
        md = build_md(snap)
        copy_buttons(md)
        with st.container(border=True):
            st.markdown(md)
        with st.expander("마크다운 원문 (복사 버튼이 안 될 때 오른쪽 위 아이콘으로 복사)"):
            st.code(md, language="markdown")

    body()


main()
