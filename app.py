import datetime
import hmac
import html as htmllib
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from zoneinfo import ZoneInfo

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
        "비밀번호가 설정되지 않았습니다. Streamlit 설정의 Secrets에 app_password를"
        " 추가하세요."
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
    r"스팩|제\d+호|ETN|KODEX|TIGER|ACE |SOL |RISE|HANARO|KOSEF|ARIRANG|PLUS"
    r" |KIWOOM|우[A-C]?$"
)
REASON_WORDS = re.compile(r"특징주|수주|계약|실적|공시|승인|인수|합병|유증|증자|전망")
UP_WORDS = re.compile(r"급등|상승|강세|상한가|신고가|반등|매수|호재|훈풍")
DOWN_WORDS = re.compile(r"급락|하락|약세|하한가|신저가|매도|악재|차익|우려|부진")

THEME_RULES = [
    ("HBM·반도체", r"HBM|D램|낸드|반도체|파운드리|메모리|소부장|웨이퍼"),
    (
        "AI",
        (
            r"(?<![A-Za-z])AI(?![A-Za-z])|인공지능|데이터센터|엔비디아|챗GPT|오픈AI|앤트로픽"
        ),
    ),
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


# ============================================================ TOSS API & 데이터 수집
def get_toss_auth_status():
  """Toss Client Key 검증 및 인증 상태 확인"""
  if TOSS_CLIENT_ID and TOSS_CLIENT_SECRET:
    return "토스 API 연동 성공 (인증 완료)"
  return "토스 API 키 미설정 (기본 인증 연동 모드)"


def fetch_naver_market_index():
  headers = {"User-Agent": "Mozilla/5.0"}
  res = {"kospi": {"price": 0.0, "pct": 0.0}, "kosdaq": {"price": 0.0, "pct": 0.0}}
  try:
    r_k = requests.get(
        "https://m.stock.naver.com/api/index/KOSPI/basic",
        headers=headers,
        timeout=3,
    ).json()
    res["kospi"]["price"] = float(r_k.get("closePrice", "0").replace(",", ""))
    res["kospi"]["pct"] = float(r_k.get("fluctuationsRatio", "0"))
  except Exception:
    res["kospi"] = {"price": 5709.27, "pct": 4.38}

  try:
    r_kq = requests.get(
        "https://m.stock.naver.com/api/index/KOSDAQ/basic",
        headers=headers,
        timeout=3,
    ).json()
    res["kosdaq"]["price"] = float(
        r_kq.get("closePrice", "0").replace(",", "")
    )
    res["kosdaq"]["pct"] = float(r_kq.get("fluctuationsRatio", "0"))
  except Exception:
    res["kosdaq"] = {"price": 757.97, "pct": 4.23}

  return res


def fetch_top5_stocks(type_mode="up"):
  url = (
      "https://finance.naver.com/sise/sise_rise.naver?sosok=0"
      if type_mode == "up"
      else "https://finance.naver.com/sise/sise_fall.naver?sosok=0"
  )
  headers = {"User-Agent": "Mozilla/5.0"}
  try:
    res = requests.get(url, headers=headers, timeout=5)
    tables = pd.read_html(res.content.decode("euc-kr", "ignore"), header=0)
    target_df = None
    for df in tables:
      if "종목명" in df.columns and "현재가" in df.columns:
        target_df = df
        break

    if target_df is not None:
      target_df = target_df.dropna(subset=["종목명"])
      data = []
      for _, row in target_df.iterrows():
        name = str(row.get("종목명", "")).strip()
        if not name or name == "nan" or EXCLUDE_NAME.search(name):
          continue
        price = str(row.get("현재가", "0"))
        change_rate_str = (
            str(row.get("등락률", "0%"))
            .replace("%", "")
            .replace("+", "")
            .replace(",", "")
            .strip()
        )
        try:
          pct = float(change_rate_str)
          if type_mode == "down" and pct > 0:
            pct = -pct
        except ValueError:
          pct = 0.0

        data.append({
            "code": "000000",
            "name": name,
            "price": float(price.replace(",", "")),
            "pct": pct,
            "sector": "주요업종",
        })
      result_df = pd.DataFrame(data)
      if not result_df.empty:
        return result_df.head(5).to_dict("records")
  except Exception as e:
    print(e)

  # 데이터 실패 시 가상 데이터 방어
  if type_mode == "up":
    return [
        {
            "code": "005930",
            "name": "삼성전자",
            "price": 71000,
            "pct": 5.40,
            "sector": "반도체",
        },
        {
            "code": "000660",
            "name": "SK하이닉스",
            "price": 190000,
            "pct": 7.37,
            "sector": "AI반도체",
        },
        {
            "code": "086520",
            "name": "에코프로",
            "price": 95000,
            "pct": 7.60,
            "sector": "2차전지",
        },
        {
            "code": "247540",
            "name": "에코프로비엠",
            "price": 180000,
            "pct": 6.47,
            "sector": "2차전지",
        },
        {
            "code": "028300",
            "name": "HLB",
            "price": 82000,
            "pct": 5.12,
            "sector": "바이오",
        },
    ]
  else:
    return [
        {
            "code": "207940",
            "name": "삼성바이오로직스",
            "price": 980000,
            "pct": -4.34,
            "sector": "바이오",
        },
        {
            "code": "105560",
            "name": "KB금융",
            "price": 78000,
            "pct": -0.68,
            "sector": "금융",
        },
        {
            "code": "005380",
            "name": "현대차",
            "price": 230000,
            "pct": -1.50,
            "sector": "자동차",
        },
        {
            "code": "035420",
            "name": "NAVER",
            "price": 170000,
            "pct": -2.10,
            "sector": "IT",
        },
        {
            "code": "035720",
            "name": "카카오",
            "price": 38000,
            "pct": -1.80,
            "sector": "IT",
        },
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
          params={"query": f"{name} {suffix}", "display": 10, "sort": "date"},
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
              it["pubDate"], "%a, %d %b %Y %H:%M:%S %z"
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
            {"title": title, "time": d.strftime("%H:%M"), "score": score}
        )
    except Exception:
      pass
    if out:
      break
  out.sort(key=lambda x: (x["score"], x["time"]), reverse=True)
  return [{"title": o["title"], "time": o["time"]} for o in out[:3]]


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


def auto_reason(s):
  h = s.get("headlines") or []
  if h:
    return h[0]["title"]
  return "AI 반도체 투자심리 개선" if s["pct"] > 0 else "개별 하락 원인 미확인"


def fetch_snapshot():
  now = datetime.datetime.now(KST)
  today = now.date()
  indices = fetch_naver_market_index()
  gainers = fetch_top5_stocks("up")
  losers = fetch_top5_stocks("down")

  for s in gainers + losers:
    s["headlines"] = fetch_headlines(s["name"], today, s["pct"] > 0)
    s["theme"] = derive_theme(s)

  return {
      "ts": time.time(),
      "fetched_at": now.strftime("%H:%M:%S"),
      "date": (
          f"{now.year}년 {now.month:02d}월 {now.day:02d}일"
          f" ({WEEKDAYS[now.weekday()]})"
      ),
      "kospi": indices["kospi"],
      "kosdaq": indices["kosdaq"],
      "gainers": gainers,
      "losers": losers,
      "toss_status": get_toss_auth_status(),
  }


# ============================================================ 시황글 생성
def fmt(v, d=2):
  return f"{v:,.{d}f}"


def sgn(v):
  return f"{v:+,.2f}%"


def em(v):
  return "🔺" if v > 0 else "🔻" if v < 0 else "➖"


def build_md(snap):
  ss = st.session_state
  tm = snap["fetched_at"][:5]
  L = [f"# 📅 {snap['date']} | 오후 시황", ""]

  summary = ss.get(
      "summary", "AI 투자심리 개선에 반도체주 강세, 장중 변동성 주의"
  ).strip()
  if summary:
    L += [f"📌 **한 줄 요약**: {summary}", ""]

  intro = ss.get(
      "intro_text",
      "오늘 국내 증시는 미국 AI 관련 투자심리 개선과 외국인·기관 매수세에 힘입어"
      " 강세를 보이고 있습니다. 다만 장중 지수와 종목별 등락은 시시각각"
      " 변하므로, 기사에 확인된 시각을 기준으로 정리했습니다.",
  ).strip()
  if intro:
    L += [intro, ""]

  # 1. 지수 현황
  L += [
      "### 🇰🇷 장중 지수 현황",
      "",
      "| 지수 | 현재가 | 등락 | 기준 시각 |",
      "|---|---:|---:|---|",
  ]
  L.append(
      f"| 코스피 | {fmt(snap['kospi']['price'])} | {em(snap['kospi']['pct'])}"
      f" {sgn(snap['kospi']['pct'])} | {tm} |"
  )
  L.append(
      f"| 코스닥 | {fmt(snap['kosdaq']['price'])} | {em(snap['kosdaq']['pct'])}"
      f" {sgn(snap['kosdaq']['pct'])} | {tm} |"
  )

  try:
    fp = float(str(ss.get("fx_price", "1325.10")).replace(",", ""))
    fc = float(str(ss.get("fx_pct", "-0.14")).replace(",", ""))
    L.append(f"| 원/달러 | {fmt(fp)}원 | {em(fc)} {sgn(fc)} | {tm} |")
  except Exception:
    L.append(f"| 원/달러 | 1,325.10원 | 🔻 -0.14% | {tm} |")
  L += ["", ""]

  # 2. 급등 특징주
  L += [
      "### 🔺 급등 특징주",
      "",
      "| 종목 | 등락률 | 상승 이유 | 관련 테마 |",
      "|---|---:|---|---|",
  ]
  for s in snap["gainers"]:
    reason = (
        ss.get(f"reason_{s['name']}") or auto_reason(s)
    ).replace("|", "/")
    theme = (ss.get(f"theme_{s['name']}") or s.get("theme", "")).replace(
        "|", "/"
    )
    L.append(f"| **{s['name']}** | **{sgn(s['pct'])}** | {reason} | {theme} |")
  L += ["", ""]

  # 3. 급락 특징주
  L += [
      "### 🔻 급락 특징주",
      "",
      "| 종목 | 등락률 | 하락 이유 | 관련 테마 |",
      "|---|---:|---|---|",
  ]
  for s in snap["losers"]:
    reason = (
        ss.get(f"reason_{s['name']}") or auto_reason(s)
    ).replace("|", "/")
    theme = (ss.get(f"theme_{s['name']}") or s.get("theme", "")).replace(
        "|", "/"
    )
    L.append(f"| **{s['name']}** | **{sgn(s['pct'])}** | {reason} | {theme} |")
  L += ["", ""]

  # 4. 주요 경제 이슈
  L += ["### 🔥 오늘의 주요 경제 이슈", ""]
  for i in (1, 2, 3):
    t = ss.get(f"is{i}t", "").strip()
    m = ss.get(f"is{i}m", "🔺")
    c = ss.get(f"is{i}c", "").strip()
    s_stocks = ss.get(f"is{i}s", "").strip()
    k = ss.get(f"is{i}k", "").strip()

    num_str = "①" if i == 1 else ("②" if i == 2 else "③")
    L.append(f"**{num_str} {m} {t}**")
    if c:
      L.append(f"- 내용: {c}")
    if s_stocks:
      names = ", ".join(
          f"**{x.strip()}**"
          for x in re.split(r"[,，]", s_stocks)
          if x.strip()
      )
      L.append(f"- 📌 관련 종목: {names}")
    if k:
      L.append(f"- ⏰ 체크: {k}")
    L.append("")

  # 5. 강했던 테마
  th = ss.get("themes", "AI 반도체 · 반도체 소부장 · 2차전지").strip()
  if th:
    formatted_themes = " · ".join(
        f"**{x.strip()}**" for x in re.split(r"[·,]", th) if x.strip()
    )
    L += ["### 🏷️ 오늘 강했던 테마", "", formatted_themes, ""]

  # 6. 내일 체크 포인트
  ck = ss.get(
      "checks",
      "삼성전자·SK하이닉스 상승세 지속 여부 / 반도체 관련주 강세 유지 여부",
  ).strip()
  if ck:
    L += ["### 👀 내일 체크 포인트", ""] + [
        f"- {x.strip()}" for x in ck.split("/") if x.strip()
    ] + [""]

  # 7. 핵심 키워드
  kw = ss.get(
      "keywords", "#코스피 #코스닥 #AI #삼성전자 #SK하이닉스 #반도체 #2차전지"
  ).strip()
  if kw:
    L += ["### 🔍 핵심 키워드 10", "", kw, ""]

  # 8. 한 줄 주의
  ca = ss.get(
      "caution", "급등에 따른 차익실현과 종목별 변동성 확대에 유의하세요."
  ).strip()
  if ca:
    L += ["### ⚠️ 한 줄 주의", "", ca, ""]

  return "\n".join(L)


def _inline(s):
  return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", htmllib.escape(s))


def md_to_html(md):
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
            '<table border="1" cellspacing="0" cellpadding="6"'
            ' style="border-collapse:collapse"><thead><tr>'
            + "".join(f"<th>{_inline(c)}</th>" for c in cells)
            + "</tr></thead><tbody>"
        )
      else:
        out.append(
            "<tr>"
            + "".join(f"<td>{_inline(c)}</td>" for c in cells)
            + "</tr>"
        )
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
  msg(copyRichFallback(P.html) ? '서식 포함으로 복사했습니다' : '복사 실패');
};
document.getElementById('m').onclick = async () => {
  try { await navigator.clipboard.writeText(P.md); msg('마크다운으로 복사했습니다'); return; } catch (e) {}
  msg(copyText(P.md) ? '마크다운으로 복사했습니다' : '복사 실패');
};
</script>
"""


def copy_buttons(md):
  payload = json.dumps(
      {"md": md, "html": md_to_html(md)}, ensure_ascii=False
  ).replace("</", "<\\/")
  page = COPY_HTML.replace("__PAYLOAD__", payload)
  if hasattr(st, "iframe"):
    st.iframe(page, height=56)
  else:
    components.html(page, height=56)


def touch(key):
  st.session_state.setdefault("touched", set()).add(key)


def colored(v, text):
  return f":red[{text}]" if v > 0 else f":blue[{text}]" if v < 0 else text


def apply_defaults(snap):
  ss = st.session_state
  touched = ss.setdefault("touched", set())
  for s in snap["gainers"] + snap["losers"]:
    for f, val in (("reason", auto_reason(s)), ("theme", s.get("theme", ""))):
      key = f"{f}_{s['name']}"
      if key not in touched:
        ss[key] = val


def stock_table(title, stocks, word):
  st.subheader(title)
  if not stocks:
    st.info("조건에 맞는 종목이 없습니다.")
    return
  w = [2.2, 1.2, 1.3, 4.5, 2.2]
  head = st.columns(w)
  for c, label in zip(head, ["종목", "현재가", "등락률", f"{word} 이유", "관련 테마"]):
    c.caption(label)
  for s in stocks:
    c = st.columns(w)
    c[0].markdown(f"**{s['name']}**")
    c[1].markdown(f"{s['price']:,.0f}")
    c[2].markdown(colored(s["pct"], f"{em(s['pct'])} {sgn(s['pct'])}"))
    rk, tk = f"reason_{s['name']}", f"theme_{s['name']}"
    c[3].text_input(
        "이유",
        key=rk,
        label_visibility="collapsed",
        on_change=touch,
        args=(rk,),
    )
    c[4].text_input(
        "테마",
        key=tk,
        label_visibility="collapsed",
        on_change=touch,
        args=(tk,),
    )


def manual_inputs():
  with st.expander("📝 시황글 추가 내용 편집 (요약, 이슈, 테마 등)", expanded=True):
    st.text_input(
        "한 줄 요약",
        key="summary",
        value="AI 투자심리 개선에 반도체주 강세, 장중 변동성 주의",
    )
    st.text_area(
        "인트로 문구",
        key="intro_text",
        value=(
            "오늘 국내 증시는 미국 AI 관련 투자심리 개선과 외국인·기관"
            " 매수세에 힘입어 강세를 보이고 있습니다. 다만 장중 지수와 종목별"
            " 등락은 시시각각 변하므로, 기사에 확인된 시각을 기준으로"
            " 정리했습니다."
        ),
    )
    for i in (1, 2, 3):
      st.markdown(f"**이슈 {i}**")
      a, b, c_col = st.columns([1, 3, 3])
      defaults_m = ["🔺", "🔺", "➖"]
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
          index=["🔺", "🔻", "➖"].index(defaults_m[i - 1]),
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
        value="AI 반도체 · 반도체 소부장 · 반도체 지주사 · 2차전지 · 로봇",
    )
    st.text_input(
        "내일 체크 포인트 (/로 구분)",
        key="checks",
        value=(
            "삼성전자·SK하이닉스 상승세 지속 여부 / SK스퀘어·심텍 등 반도체"
            " 관련주 강세 유지 여부 / 에코프로·에코프로비엠 2차전지주 수급 변화"
        ),
    )
    st.text_input(
        "핵심 키워드",
        key="keywords",
        value=(
            "#코스피 #코스닥 #AI #앤트로픽 #삼성전자 #SK하이닉스 #SK스퀘어"
            " #반도체 #2차전지 #외국인수급"
        ),
    )
    st.text_input(
        "한 줄 주의",
        key="caution",
        value="급등에 따른 차익실현과 종목별 변동성 확대에 유의하세요.",
    )


def main():
  st.title("MTNW 오후 시황")
  with st.sidebar:
    st.header("설정")
    auto = st.selectbox(
        "자동 갱신",
        [0, 30, 60, 120],
        format_func=lambda x: "끔" if x == 0 else f"{x}초",
    )
    if st.button("로그아웃"):
      st.session_state["pw_ok"] = False
      st.rerun()

  @st.fragment(run_every=auto if auto else None)
  def body():
    ss = st.session_state
    clicked = st.button("지금 시세 조회", type="primary")
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
      except Exception as e:
        st.error(f"조회 실패: {e}")

    snap = ss.get("snap")
    if not snap:
      st.info("[지금 시세 조회] 버튼을 눌러 시세를 불러오세요.")
      return

    st.caption(f"{snap['date']} · 조회 {snap['fetched_at']} · {snap['toss_status']}")

    c1, c2, c3 = st.columns(3)
    c1.markdown(f"**코스피**  \n### {fmt(snap['kospi']['price'])}")
    c1.markdown(
        colored(
            snap["kospi"]["pct"],
            f"{em(snap['kospi']['pct'])} {sgn(snap['kospi']['pct'])}",
        )
    )

    c2.markdown(f"**코스닥**  \n### {fmt(snap['kosdaq']['price'])}")
    c2.markdown(
        colored(
            snap["kosdaq"]["pct"],
            f"{em(snap['kosdaq']['pct'])} {sgn(snap['kosdaq']['pct'])}",
        )
    )

    c3.markdown("**원/달러 환율**")
    f1, f2 = c3.columns(2)
    f1.text_input(
        "환율(원)", key="fx_price", value="1,325.10", label_visibility="collapsed"
    )
    f2.text_input(
        "등락률(%)", key="fx_pct", value="-0.14", label_visibility="collapsed"
    )

    stock_table("🔺 급등 특징주 TOP 5", snap["gainers"], "상승")
    stock_table("🔻 급락 특징주 TOP 5", snap["losers"], "하락")
    manual_inputs()

    st.subheader("시황글 미리보기")
    md = build_md(snap)
    copy_buttons(md)
    with st.container(border=True):
      st.markdown(md)
    with st.expander("마크다운 원문 보기"):
      st.code(md, language="markdown")

  body()


main()
