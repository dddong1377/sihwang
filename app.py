#!/usr/bin/env python3
"""
MTNW 오후 시황 대시보드
- 한국투자증권(KIS) Open API로 코스피·코스닥, 급등·급락 종목을 한 번에 조회 (같은 시각 기준)
- 네이버 뉴스 검색 API로 종목별 오늘자 헤드라인을 이유 후보로 자동 입력
- 화면에서 이유·테마를 고친 뒤 [복사]를 누르면 시황글이 클립보드에 저장됨

실행:  python mtnw_dashboard.py        → 브라우저에서 http://127.0.0.1:5055
점검:  python mtnw_dashboard.py --test → API 응답 원본 일부를 출력 (오류 확인용)
"""
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from flask import Flask, Response, jsonify, request

BASE_DIR = Path(__file__).resolve().parent
KST = ZoneInfo("Asia/Seoul")
WEEKDAYS = "월화수목금토일"


# ---------------------------------------------------------------- 설정
def load_env():
    """같은 폴더의 .env 파일을 읽어 환경변수로 등록 (외부 라이브러리 없이)."""
    f = BASE_DIR / ".env"
    if not f.exists():
        return
    for line in f.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


load_env()
KIS_APP_KEY = os.getenv("KIS_APP_KEY", "")
KIS_APP_SECRET = os.getenv("KIS_APP_SECRET", "")
KIS_BASE = os.getenv("KIS_BASE_URL", "https://openapi.koreainvestment.com:9443")
NAVER_ID = os.getenv("NAVER_CLIENT_ID", "")
NAVER_SECRET = os.getenv("NAVER_CLIENT_SECRET", "")
TOKEN_FILE = BASE_DIR / ".kis_token.json"

# 급등·급락 목록에서 제외할 이름 패턴 (스팩, ETF/ETN, 우선주)
EXCLUDE_NAME = re.compile(
    r"스팩|제\d+호|ETN|KODEX|TIGER|ACE |SOL |RISE|HANARO|KOSEF|ARIRANG|PLUS |KIWOOM|우[A-C]?$"
)
REASON_WORDS = re.compile(r"특징주|수주|계약|실적|공시|승인|인수|합병|유증|증자|전망")
UP_WORDS = re.compile(r"급등|상승|강세|상한가|신고가|반등|매수|호재|훈풍")
DOWN_WORDS = re.compile(r"급락|하락|약세|하한가|신저가|매도|악재|차익|우려|부진")

# 헤드라인·종목명·업종명에서 테마를 뽑는 규칙 (앞에서부터 최대 2개 사용). 필요하면 자유롭게 추가하세요.
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
    ("정치·테마", r"대선|정책|규제|법안|테마"),
]
SECTOR_CACHE = {}        # 종목코드 -> 업종명 (하루 동안 변하지 않음)
NEWS_CACHE = {}          # 종목명 -> (조회시각, 헤드라인) 자동 갱신 때 API 호출을 줄이기 위한 5분 캐시
NEWS_TTL = 300

app = Flask(__name__)


# ---------------------------------------------------------------- KIS
class ApiError(Exception):
    pass


def kis_token():
    """토큰은 24시간 유효하고 발급 횟수 제한이 있어 파일에 저장해 재사용한다."""
    if TOKEN_FILE.exists():
        try:
            t = json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
            if t.get("expired_at", 0) > time.time() + 300 and t.get("appkey") == KIS_APP_KEY:
                return t["token"]
        except Exception:
            pass
    if not KIS_APP_KEY or not KIS_APP_SECRET:
        raise ApiError("KIS 앱키가 없습니다. .env 파일에 KIS_APP_KEY / KIS_APP_SECRET을 입력하세요.")
    r = requests.post(
        f"{KIS_BASE}/oauth2/tokenP",
        json={"grant_type": "client_credentials", "appkey": KIS_APP_KEY, "appsecret": KIS_APP_SECRET},
        timeout=10,
    )
    body = r.json() if r.content else {}
    if r.status_code != 200 or "access_token" not in body:
        raise ApiError(f"KIS 토큰 발급 실패: {body.get('error_description') or body.get('msg1') or r.status_code}")
    exp = time.time() + 23 * 3600
    TOKEN_FILE.write_text(
        json.dumps({"token": body["access_token"], "expired_at": exp, "appkey": KIS_APP_KEY}), encoding="utf-8"
    )
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
    """KIS 부호코드(4·5=하락)를 반영해 부호 있는 숫자로 만든다."""
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
        name = o.get("hts_kor_isnm", "").strip()
        sign = o.get("prdy_vrss_sign", "")
        rows.append(
            {
                "code": o.get("stck_shrn_iscd", ""),
                "name": name,
                "price": num(o.get("stck_prpr")),
                "pct": signed(o.get("prdy_ctrt"), sign),
                "volume": num(o.get("acml_vol")),
            }
        )
    return rows


def fetch_sector(code):
    if code in SECTOR_CACHE:
        return SECTOR_CACHE[code]
    body = kis_get(
        "/uapi/domestic-stock/v1/quotations/inquire-price",
        "FHKST01010100",
        {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code},
    )
    o = body.get("output") or {}
    sector = (o.get("bstp_kor_isnm") or "").strip()
    market = (o.get("rprs_mrkt_kor_name") or "").strip()
    SECTOR_CACHE[code] = (sector, market)
    return SECTOR_CACHE[code]


def derive_theme(stock):
    """헤드라인 → 종목명 → 업종명 순서로 키워드를 찾아 테마를 만든다. 못 찾으면 업종명만 쓴다."""
    texts = [" ".join(h["title"] for h in stock.get("headlines", [])), stock["name"], stock.get("sector", "")]
    found = []
    for text in texts:
        for label, pattern in THEME_RULES:
            if label not in found and re.search(pattern, text):
                found.append(label)
        if found:
            break
    sector = stock.get("sector", "")
    if found:
        return " · ".join(found[:2])
    return sector


def fill_fallback_reasons(stocks, up):
    """오늘자 기사가 없는 종목은 데이터로 확인되는 사실만 이유로 쓴다 (추측 금지).
    1) 같은 업종 종목이 같은 방향으로 여러 개 움직였으면 업종 동반 흐름 (🏷️)
    2) 아니면 거래량 사실만 표시하고 기사 미확인을 명시 (📊)"""
    word = "강세" if up else "약세"
    by_sector = {}
    for st in stocks:
        by_sector.setdefault(st.get("sector", ""), []).append(st["name"])
    for st in stocks:
        if st.get("headlines"):
            continue
        sec = st.get("sector", "")
        peers = by_sector.get(sec, [])
        if sec and len(peers) >= 2:
            st["auto_reason"] = f"🏷️ {sec} 업종 동반 {word} ({len(peers)}개 종목 같은 방향)"
        else:
            st["auto_reason"] = f"📊 거래량 {st['volume']/10000:,.0f}만주 동반, 오늘자 개별 기사 미확인"


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


def select_movers(min_vol, n, min_pct):
    """상승률순·하락률순을 각각 받아 합친 뒤, 부호를 기준으로 직접 분류한다.
    정렬 코드 해석이 문서마다 달라서, 응답의 실제 등락률로 다시 검증한다."""
    warnings = []
    up_rows = fetch_rank("0", min_vol)
    down_rows = fetch_rank("1", min_vol)
    pool = {r["code"]: r for r in up_rows + down_rows if r["name"] and not EXCLUDE_NAME.search(r["name"])}
    rows = list(pool.values())
    gainers = sorted([r for r in rows if r["pct"] >= min_pct], key=lambda r: -r["pct"])[:n]
    losers = sorted([r for r in rows if r["pct"] <= -min_pct], key=lambda r: r["pct"])[:n]
    if not any(r["pct"] < 0 for r in down_rows):
        warnings.append("하락률 순위 응답에서 하락 종목이 확인되지 않았습니다. `--test`로 정렬 코드를 점검하세요.")
    if len(gainers) < n:
        warnings.append(f"급등 기준(+{min_pct}%) 충족 종목 {len(gainers)}개")
    if len(losers) < n:
        warnings.append(f"급락 기준(-{min_pct}%) 충족 종목 {len(losers)}개")
    return gainers, losers, warnings


# ---------------------------------------------------------------- 환율 (비공식)
def fetch_fx():
    """Yahoo Finance 비공식 차트 주소. 막히면 '확인 불가'로 두고 화면에서 직접 입력한다."""
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
    return {
        "name": "원/달러",
        "price": price,
        "change": price - prev,
        "pct": (price - prev) / prev * 100 if prev else 0.0,
        "time": t.strftime("%H:%M"),
    }


# ---------------------------------------------------------------- 네이버 뉴스 (이유 후보)
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
                score -= 2  # 종목 방향과 반대되는 기사는 뒤로 보낸다
            out.append({"title": title, "time": d.strftime("%H:%M"), "score": score})
        if out:
            break
    out.sort(key=lambda x: (x["score"], x["time"]), reverse=True)  # 점수 높은 순, 같으면 최신 순
    return [{"title": o["title"], "time": o["time"]} for o in out[:4]]


def attach_news(stocks, status):
    if not (NAVER_ID and NAVER_SECRET):
        status["naver"] = "키 없음 (이유는 직접 입력)"
        for s in stocks:
            s["headlines"] = []
        return
    today = datetime.now(KST).date()

    def work(s):
        hit = NEWS_CACHE.get((s["name"], s["pct"] > 0))
        if hit and time.time() - hit[0] < NEWS_TTL:
            return hit[1], None
        try:
            h = fetch_headlines(s["name"], today, s["pct"] > 0)
            NEWS_CACHE[(s["name"], s["pct"] > 0)] = (time.time(), h)
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


# ---------------------------------------------------------------- 라우트
@app.get("/")
def index():
    return Response(HTML, mimetype="text/html")


@app.get("/api/snapshot")
def snapshot():
    n = int(request.args.get("n", 10))
    min_pct = float(request.args.get("min_pct", 3))
    min_vol = int(request.args.get("min_vol", 100000))
    status, warnings = {}, []
    now = datetime.now(KST)
    data = {
        "fetched_at": now.strftime("%H:%M:%S"),
        "date": f"{now.year}년 {now.month}월 {now.day}일 ({WEEKDAYS[now.weekday()]})",
        "kospi": None, "kosdaq": None, "fx": None, "gainers": [], "losers": [],
    }
    try:
        data["kospi"] = fetch_index("0001", "코스피")
        data["kosdaq"] = fetch_index("1001", "코스닥")
        g, l, w = select_movers(min_vol, n, min_pct)
        data["gainers"], data["losers"] = g, l
        warnings += w
        status["kis"] = "정상"
    except ApiError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": f"KIS 조회 실패: {e}"}), 500
    try:
        data["fx"] = fetch_fx()
        status["fx"] = "정상 (Yahoo 비공식)"
    except Exception:  # noqa: BLE001
        status["fx"] = "실패 (직접 입력)"
    movers = data["gainers"] + data["losers"]
    try:
        attach_sector(movers, status)
    except Exception:  # noqa: BLE001
        status["sector"] = "실패"
    attach_news(movers, status)
    for st in movers:
        st["theme"] = derive_theme(st)
    fill_fallback_reasons(data["gainers"], True)
    fill_fallback_reasons(data["losers"], False)
    data["status"], data["warnings"] = status, warnings
    return jsonify(data)


# ---------------------------------------------------------------- 화면
HTML = r"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>MTNW 오후 시황</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans+KR:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
:root{
  --bg:#f3f4f6; --panel:#ffffff; --ink:#16202e; --muted:#667085; --line:#dfe3ea;
  --up:#d1293d; --down:#2161c4; --accent:#0f5c5c; --accent-ink:#ffffff; --field:#f8f9fb; --warn:#fff4d6; --warn-ink:#6a4b00;
}
@media (prefers-color-scheme: dark){
  :root{ --bg:#10151c; --panel:#18202b; --ink:#e8ecf2; --muted:#93a0b3; --line:#2a3544;
    --up:#ff6b7a; --down:#6ea8ff; --accent:#58c4b8; --accent-ink:#0b1a19; --field:#121a24; --warn:#3a2f10; --warn-ink:#f1d589; }
}
*{box-sizing:border-box}
html,body{margin:0}
body{background:var(--bg);color:var(--ink);font:14px/1.5 "IBM Plex Sans KR","Malgun Gothic",system-ui,sans-serif}
.num{font-variant-numeric:tabular-nums}
.up{color:var(--up)} .down{color:var(--down)}
header{display:flex;flex-wrap:wrap;gap:12px 20px;align-items:center;justify-content:space-between;padding:14px 24px;background:var(--panel);border-bottom:1px solid var(--line)}
header h1{margin:0;font-size:17px;font-weight:700}
header .meta{color:var(--muted);font-size:12px}
.controls{display:flex;flex-wrap:wrap;gap:10px;align-items:center}
.controls label{font-size:12px;color:var(--muted);display:flex;gap:6px;align-items:center}
input,select,textarea{font:inherit;color:var(--ink);background:var(--field);border:1px solid var(--line);border-radius:6px;padding:6px 8px}
input:focus,select:focus,textarea:focus,button:focus-visible{outline:2px solid var(--accent);outline-offset:1px}
.controls input{width:64px}
button{font:inherit;cursor:pointer;border-radius:6px;border:1px solid var(--line);background:var(--panel);color:var(--ink);padding:7px 14px}
button.primary{background:var(--accent);color:var(--accent-ink);border-color:var(--accent);font-weight:600}
button:disabled{opacity:.5;cursor:wait}
.wrap{display:grid;grid-template-columns:minmax(0,1.55fr) minmax(0,1fr);gap:20px;padding:20px 24px;max-width:1500px;margin:0 auto}
@media (max-width:1000px){.wrap{grid-template-columns:1fr}}
section.card{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:16px 18px;margin-bottom:16px}
section.card h2{margin:0 0 12px;font-size:14px;font-weight:700}
.banner{padding:10px 14px;border-radius:6px;background:var(--warn);color:var(--warn-ink);margin-bottom:14px;font-size:13px;display:none}
.banner.err{background:#fde7ea;color:#7a1624;display:block}
.banner.show{display:block}
.idx{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}
.idx .t{border:1px solid var(--line);border-radius:6px;padding:10px 12px}
.idx .t .n{font-size:12px;color:var(--muted)}
.idx .t .p{font-size:20px;font-weight:600}
.idx .t .c{font-size:12px}
.idx input{width:100%;margin-top:4px}
table{width:100%;border-collapse:collapse;font-size:13px}
th{text-align:left;font-weight:600;color:var(--muted);font-size:12px;padding:6px 6px;border-bottom:1px solid var(--line)}
td{padding:6px 6px;border-bottom:1px solid var(--line);vertical-align:middle}
td input{width:100%;padding:4px 6px}
td.r,th.r{text-align:right}
.stk{font-weight:600}
.sub{font-weight:400;font-size:11px;color:var(--muted)}
.empty{color:var(--muted);padding:10px 0}
.issue{display:grid;grid-template-columns:90px 1fr 1fr;gap:6px;margin-bottom:8px}
.issue input,.issue select{width:100%}
.issue .full{grid-column:1/-1}
.row{display:grid;grid-template-columns:110px 1fr;gap:8px;align-items:center;margin-bottom:8px}
.row label{color:var(--muted);font-size:12px}
.preview{position:sticky;top:14px}
.preview .box{border:1px solid var(--line);border-radius:6px;padding:14px 16px;max-height:70vh;overflow:auto;background:var(--field);font-size:13px}
.preview .box h1,.preview .box h3{margin:12px 0 6px}
.preview .box h1{font-size:16px;margin-top:0} .preview .box h3{font-size:14px}
.preview .box blockquote{margin:8px 0;padding:4px 10px;border-left:3px solid var(--accent)}
.preview .box table{margin:6px 0}
.preview .box td,.preview .box th{border:1px solid var(--line);padding:4px 6px}
.preview .box ul{margin:4px 0;padding-left:18px}
.actions{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:10px}
.toast{position:fixed;left:50%;bottom:24px;transform:translateX(-50%);background:var(--ink);color:var(--bg);padding:9px 16px;border-radius:6px;opacity:0;pointer-events:none;transition:opacity .15s}
.toast.on{opacity:1}
@media (prefers-reduced-motion: reduce){.toast{transition:none}}
</style>
</head>
<body>
<header>
  <div>
    <h1>MTNW 오후 시황</h1>
    <div class="meta" id="meta">조회 전</div>
  </div>
  <div class="controls">
    <label>종목 수 <input id="n" type="number" value="10" min="1" max="20"></label>
    <label>기준 등락률 ±<input id="minPct" type="number" value="3" step="0.5" min="0"> %</label>
    <label>최소 거래량 <input id="minVol" type="number" value="100000" step="50000" min="0"></label>
    <label>자동 갱신 <select id="auto"><option value="0">끔</option><option value="30">30초</option><option value="60">60초</option><option value="120">2분</option></select></label>
    <button class="primary" id="load">지금 시세 조회</button>
  </div>
</header>

<div class="wrap">
  <div>
    <div class="banner" id="banner"></div>

    <section class="card">
      <h2>장중 지수 현황</h2>
      <div class="idx" id="idx"><div class="empty">[지금 시세 조회]를 누르세요.</div></div>
    </section>

    <section class="card">
      <h2>급등 특징주</h2>
      <div id="up"><div class="empty">-</div></div>
    </section>

    <section class="card">
      <h2>급락 특징주</h2>
      <div id="down"><div class="empty">-</div></div>
    </section>

    <section class="card">
      <h2>직접 쓰는 부분 (비워 두면 시황글에서 빠집니다)</h2>
      <div class="row"><label>한 줄 요약</label><input id="summary" placeholder="20~30자"></div>
      <div id="issues"></div>
      <div class="row"><label>강했던 테마</label><input id="themes" placeholder="반도체 · HBM · 2차전지 (구분은 · 로)"></div>
      <div class="row"><label>내일 체크</label><input id="checks" placeholder="항목은 / 로 구분"></div>
      <div class="row"><label>핵심 키워드</label><input id="keywords" placeholder="조회 후 자동 입력됩니다"></div>
      <div class="row"><label>한 줄 주의</label><input id="caution" placeholder="급등 이후 변동성 유의"></div>
    </section>
  </div>

  <aside class="preview">
    <section class="card">
      <h2>시황글 미리보기</h2>
      <div class="actions">
        <button class="primary" id="copyRich">복사 (서식 포함)</button>
        <button id="copyMd">복사 (마크다운)</button>
      </div>
      <div class="box" id="preview">조회하면 여기에 시황글이 만들어집니다.</div>
    </section>
  </aside>
</div>
<div class="toast" id="toast"></div>

<script>
const $ = s => document.querySelector(s);
let snap = null;
const edits = {};          // 종목코드 -> {reason, theme}  (재조회해도 유지)
const fx = {price:'', pct:'', time:''};
const ISSUE_N = 3;

// ---------- 유틸
const fmt = (v, d=2) => Number(v).toLocaleString('ko-KR', {minimumFractionDigits:d, maximumFractionDigits:d});
const sgn = v => (v > 0 ? '+' : '') + fmt(v) + '%';
const em = v => v > 0 ? '🔺' : v < 0 ? '🔻' : '➖';
const cls = v => v > 0 ? 'up' : v < 0 ? 'down' : '';
const esc = s => String(s).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const hhmm = t => (t || '').slice(0, 5);
function toast(msg){ const t = $('#toast'); t.textContent = msg; t.classList.add('on'); setTimeout(() => t.classList.remove('on'), 1800); }

// ---------- 조회
let timer = null;
async function load(auto=false){
  const btn = $('#load'); btn.disabled = true; btn.textContent = '조회 중…';
  if (!auto) $('#banner').className = 'banner';
  try {
    const q = new URLSearchParams({n: $('#n').value, min_pct: $('#minPct').value, min_vol: $('#minVol').value});
    const r = await fetch('/api/snapshot?' + q);
    const d = await r.json();
    if (!r.ok) throw new Error(d.error || '조회 실패');
    snap = d;
    if (d.fx && !fx.touched) { fx.price = fmt(d.fx.price); fx.pct = fmt(d.fx.pct); fx.time = d.fx.time; }
    [...d.gainers, ...d.losers].forEach(s => {
      const h = s.headlines && s.headlines[0];
      const auto = {reason: h ? '📰 ' + h.title : (s.auto_reason || ''), theme: s.theme || ''};
      const e = edits[s.code] || (edits[s.code] = {reason: '', theme: '', touched: {}});
      // 사용자가 직접 고친 칸은 유지하고, 나머지는 최신 자동값으로 갱신
      if (!e.touched.reason) e.reason = auto.reason;
      if (!e.touched.theme) e.theme = auto.theme;
    });
    if (!$('#keywords').value || $('#keywords').dataset.auto === '1') {
      const names = [...d.gainers, ...d.losers].slice(0, 8).map(s => s.name);
      $('#keywords').value = ['코스피', '코스닥', ...names].map(w => '#' + w.replace(/\s/g, '')).join(' ');
      $('#keywords').dataset.auto = '1';
    }
    const msgs = (d.warnings || []).slice();
    if (msgs.length) { const b = $('#banner'); b.textContent = msgs.join(' / '); b.className = 'banner show'; }
    else if (auto) $('#banner').className = 'banner';
    render();
  } catch (e) {
    const b = $('#banner'); b.textContent = e.message; b.className = 'banner err';
  } finally { btn.disabled = false; btn.textContent = '지금 시세 조회'; }
}
$('#load').addEventListener('click', () => load(false));
$('#auto').addEventListener('change', e => {
  clearInterval(timer); timer = null;
  const sec = parseInt(e.target.value, 10);
  if (sec > 0) { timer = setInterval(() => load(true), sec * 1000); toast(`${sec}초마다 자동 갱신합니다`); }
});

// ---------- 화면 렌더
function idxTile(x, label){
  if (!x) return `<div class="t"><div class="n">${label}</div><div class="p">확인 불가</div></div>`;
  return `<div class="t"><div class="n">${label}</div><div class="p num">${fmt(x.price)}</div>
    <div class="c num ${cls(x.pct)}">${em(x.pct)} ${sgn(x.pct)}</div></div>`;
}
function render(){
  if (!snap) return;
  const ae = document.activeElement;
  if (ae && ae.dataset && ae.dataset.code) { preview(); updatePrices(); return; }  // 입력 중에는 표를 다시 그리지 않는다
  $('#meta').textContent = `${snap.date} · 조회 ${snap.fetched_at} · KIS ${snap.status.kis} · 환율 ${snap.status.fx} · 뉴스 ${snap.status.naver} · 업종 ${snap.status.sector || '-'}`;
  $('#idx').innerHTML = idxTile(snap.kospi, '코스피') + idxTile(snap.kosdaq, '코스닥') +
    `<div class="t"><div class="n">원/달러 (직접 수정 가능)</div>
      <input id="fxPrice" class="num" placeholder="환율(원)" value="${esc(fx.price)}">
      <input id="fxPct" class="num" placeholder="등락률(%)" value="${esc(fx.pct)}"></div>`;
  $('#fxPrice').addEventListener('input', e => { fx.price = e.target.value; fx.touched = true; preview(); });
  $('#fxPct').addEventListener('input', e => { fx.pct = e.target.value; fx.touched = true; preview(); });
  $('#up').innerHTML = table(snap.gainers, '상승');
  $('#down').innerHTML = table(snap.losers, '하락');
  document.querySelectorAll('[data-code]').forEach(el => el.addEventListener('input', e => {
    const ed = edits[el.dataset.code]; ed[el.dataset.f] = e.target.value; ed.touched[el.dataset.f] = true; preview();
  }));
  preview();
}
function updatePrices(){
  // 입력 중일 때는 현재가·등락률 칸만 갱신
  [...snap.gainers, ...snap.losers].forEach(s => {
    const a = document.querySelector(`[data-p="${s.code}"]`), b = document.querySelector(`[data-c="${s.code}"]`);
    if (a) a.textContent = fmt(s.price, 0);
    if (b) { b.textContent = `${em(s.pct)} ${sgn(s.pct)}`; b.className = `r num ${cls(s.pct)}`; }
  });
}
function table(list, word){
  if (!list.length) return '<div class="empty">기준을 충족하는 종목이 없습니다. 기준 등락률을 낮춰 다시 조회해 보세요.</div>';
  const rows = list.map(s => {
    const e = edits[s.code];
    const dl = `dl-${s.code}`;
    const opts = (s.headlines || []).map(h => `<option value="${esc('📰 ' + h.title)}">${esc(h.time)}</option>`).join('');
    return `<tr>
      <td class="stk">${esc(s.name)}<div class="sub">${esc(s.market || '')} ${esc(s.sector || '')}</div></td>
      <td class="r num" data-p="${s.code}">${fmt(s.price, 0)}</td>
      <td class="r num ${cls(s.pct)}" data-c="${s.code}">${em(s.pct)} ${sgn(s.pct)}</td>
      <td><input data-code="${s.code}" data-f="reason" list="${dl}" value="${esc(e.reason)}" placeholder="${word} 이유 (기사 후보는 ▼에서 선택)">
          <datalist id="${dl}">${opts}</datalist></td>
      <td><input data-code="${s.code}" data-f="theme" value="${esc(e.theme)}" placeholder="테마" style="min-width:120px"></td>
    </tr>`;
  }).join('');
  return `<table><thead><tr><th>종목</th><th class="r">현재가</th><th class="r">등락률</th><th>${word} 이유</th><th>관련 테마</th></tr></thead><tbody>${rows}</tbody></table>`;
}

// 직접 쓰는 이슈 입력칸
(function(){
  let h = '';
  for (let i = 1; i <= ISSUE_N; i++) h += `<div class="issue">
    <select id="is${i}m"><option>🔺</option><option>🔻</option><option>➖</option></select>
    <input id="is${i}t" placeholder="이슈 ${i} 제목 (15자 내외)">
    <input id="is${i}s" placeholder="관련 종목 (쉼표로 구분)">
    <input class="full" id="is${i}c" placeholder="내용 한 줄">
    <input class="full" id="is${i}k" placeholder="체크: 향후 일정 또는 리스크 한 줄"></div>`;
  $('#issues').innerHTML = h;
})();
document.querySelectorAll('.card input, .card select').forEach(el => {
  el.addEventListener('input', () => { if (el.id === 'keywords') el.dataset.auto = '0'; preview(); });
});

// ---------- 시황글 생성 (마크다운)
function buildMd(){
  if (!snap) return '';
  const L = [];
  const tm = hhmm(snap.fetched_at);
  L.push(`# 📅 ${snap.date.replace(/^(\d+)년 (\d+)월 (\d+)일/, (m,y,mo,d)=>`${y}년 ${String(mo).padStart(2,'0')}월 ${String(d).padStart(2,'0')}일`)} | 오후 시황`);
  L.push(`**조회 기준: ${tm}**`, '');
  const sm = $('#summary').value.trim();
  if (sm) L.push(`> 📌 **한 줄 요약**: ${sm}`, '');

  L.push('### 🇰🇷 장중 지수 현황', '', '| 지수 | 현재가 | 등락 | 기준 시각 |', '|---|---:|---:|---|');
  const ir = (x, name) => x ? `| ${name} | ${fmt(x.price)} | ${em(x.pct)} ${sgn(x.pct)} | ${tm} |` : `| ${name} | 확인 불가 | 확인 불가 | - |`;
  L.push(ir(snap.kospi, '코스피'), ir(snap.kosdaq, '코스닥'));
  const fp = parseFloat(String(fx.price).replace(/,/g, '')), fc = parseFloat(String(fx.pct).replace(/,/g, ''));
  L.push(isNaN(fp) ? '| 원/달러 | 확인 불가 | 확인 불가 | - |'
    : `| 원/달러 | ${fmt(fp)}원 | ${isNaN(fc) ? '-' : em(fc) + ' ' + sgn(fc)} | ${fx.time || tm} |`, '');

  const stockTable = (title, list, word) => {
    L.push(`### ${title}`, '');
    if (!list.length) { L.push('기준을 충족하는 종목이 없습니다.', ''); return; }
    L.push(`| 종목 | 등락률 | 기준 시각 | ${word} 이유 | 관련 테마 |`, '|---|---:|---|---|---|');
    list.forEach(s => {
      const e = edits[s.code] || {};
      L.push(`| **${s.name}** | **${sgn(s.pct)}** | ${tm} | ${(e.reason || '-').replace(/\|/g, '/')} | ${(e.theme || '-').replace(/\|/g, '/')} |`);
    });
    L.push('');
  };
  stockTable(`🔺 급등 특징주 TOP${snap.gainers.length}`, snap.gainers, '상승');
  stockTable(`🔻 급락 특징주 TOP${snap.losers.length}`, snap.losers, '하락');

  const issues = [];
  for (let i = 1; i <= ISSUE_N; i++) {
    const t = $(`#is${i}t`).value.trim(); if (!t) continue;
    const c = $(`#is${i}c`).value.trim(), s = $(`#is${i}s`).value.trim(), k = $(`#is${i}k`).value.trim();
    const m = $(`#is${i}m`).value, no = ['①','②','③'][i-1];
    issues.push(`**${no} ${t}** ${m}`);
    if (c) issues.push(`- 내용: ${c}`);
    if (s) issues.push(`- 📌 관련 종목: ${s.split(/[,，]/).map(x => `**${x.trim()}**`).join(', ')}`);
    if (k) issues.push(`- ⏰ 체크: ${k}`);
    issues.push('');
  }
  if (issues.length) L.push('### 🔥 오늘의 주요 경제 이슈', '', ...issues);

  const th = $('#themes').value.trim();
  if (th) L.push('### 🏷️ 오늘 강했던 테마', '', th.split(/[·,]/).map(x => `**${x.trim()}**`).join(' · '), '');
  const ck = $('#checks').value.trim();
  if (ck) L.push('### 👀 내일 체크 포인트', '', ...ck.split('/').map(x => `- ${x.trim()}`), '');
  const kw = $('#keywords').value.trim();
  if (kw) L.push('### 🔍 핵심 키워드', '', kw, '');
  const ca = $('#caution').value.trim();
  if (ca) L.push('### ⚠️ 한 줄 주의', '', ca, '');

  L.push('### 🔎 조회 현황', '',
    `- 지수·종목 시세: 한국투자증권 Open API, ${tm} 조회 (전 종목 같은 시각)`,
    `- 업종·테마: KIS 업종 + 헤드라인 키워드 자동 분류 (${snap.status.sector || '-'}), 게시 전 확인 필요`,
    `- 환율: ${snap.status.fx}`,
    `- 이유: 네이버 뉴스 오늘자 헤드라인 기반 (${snap.status.naver}), 게시 전 확인 필요`,
    '- 안내: 수치는 조회 시각 기준이며 이후 달라질 수 있습니다.');
  return L.join('\n');
}

// ---------- 마크다운 → HTML (이 글에 쓰는 문법만 지원)
function inline(s){ return esc(s).replace(/\*\*(.+?)\*\*/g, '<b>$1</b>'); }
function mdToHtml(md){
  const out = []; let tbl = null, ul = false;
  const closeUl = () => { if (ul) { out.push('</ul>'); ul = false; } };
  const closeTbl = () => { if (tbl) { out.push('</tbody></table>'); tbl = null; } };
  md.split('\n').forEach(line => {
    if (line.startsWith('|')) {
      closeUl();
      const cells = line.split('|').slice(1, -1).map(c => c.trim());
      if (cells.every(c => /^:?-+:?$/.test(c))) return;
      if (!tbl) { tbl = true; out.push('<table border="1" cellspacing="0" cellpadding="6" style="border-collapse:collapse"><thead><tr>' + cells.map(c => `<th>${inline(c)}</th>`).join('') + '</tr></thead><tbody>'); return; }
      out.push('<tr>' + cells.map(c => `<td>${inline(c)}</td>`).join('') + '</tr>'); return;
    }
    closeTbl();
    if (line.startsWith('- ')) { if (!ul) { out.push('<ul>'); ul = true; } out.push(`<li>${inline(line.slice(2))}</li>`); return; }
    closeUl();
    if (line.startsWith('# ')) out.push(`<h1>${inline(line.slice(2))}</h1>`);
    else if (line.startsWith('### ')) out.push(`<h3>${inline(line.slice(4))}</h3>`);
    else if (line.startsWith('> ')) out.push(`<blockquote>${inline(line.slice(2))}</blockquote>`);
    else if (line.trim()) out.push(`<p>${inline(line)}</p>`);
  });
  closeUl(); closeTbl();
  return out.join('\n');
}
function preview(){
  const md = buildMd();
  $('#preview').innerHTML = md ? mdToHtml(md) : '조회하면 여기에 시황글이 만들어집니다.';
}

// ---------- 클립보드
async function copy(rich){
  const md = buildMd();
  if (!md) { toast('먼저 시세를 조회하세요'); return; }
  try {
    if (rich && window.ClipboardItem) {
      const html = mdToHtml(md);
      await navigator.clipboard.write([new ClipboardItem({
        'text/html': new Blob([html], {type: 'text/html'}),
        'text/plain': new Blob([md], {type: 'text/plain'})
      })]);
    } else {
      await navigator.clipboard.writeText(md);
    }
    toast(rich ? '서식 포함으로 복사했습니다' : '마크다운으로 복사했습니다');
  } catch (e) {
    const ta = document.createElement('textarea'); ta.value = md; document.body.appendChild(ta);
    ta.select(); document.execCommand('copy'); ta.remove(); toast('복사했습니다 (마크다운)');
  }
}
$('#copyRich').addEventListener('click', () => copy(true));
$('#copyMd').addEventListener('click', () => copy(false));
</script>
</body>
</html>
"""


# ---------------------------------------------------------------- 점검 모드
def run_test():
    print("== KIS 지수 ==")
    for code, name in (("0001", "코스피"), ("1001", "코스닥")):
        print(name, fetch_index(code, name))
    for sort_code in ("0", "1"):
        rows = fetch_rank(sort_code, 100000)
        print(f"\n== 등락률 순위 sort={sort_code} ({len(rows)}건) 상위 5 ==")
        for r in rows[:5]:
            print(r)
    print("\n== 환율 ==")
    try:
        print(fetch_fx())
    except Exception as e:  # noqa: BLE001
        print("실패:", e)
    if NAVER_ID and NAVER_SECRET:
        print("\n== 네이버 뉴스 (삼성전자) ==")
        print(fetch_headlines("삼성전자", datetime.now(KST).date()))


if __name__ == "__main__":
    if "--test" in sys.argv:
        run_test()
    else:
        port = int(os.getenv("PORT", "5055"))
        print(f"\n  MTNW 오후 시황 대시보드: http://127.0.0.1:{port}\n")
        app.run(host="127.0.0.1", port=port, debug=False)
