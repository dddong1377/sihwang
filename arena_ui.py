"""주식 대결 화면의 모양(다크 테마 · 브래킷 판 · 시상대 · Top Users 표)을 만드는 HTML 조각.

- 순수 파이썬(스트림릿 없이 동작)이라 단독으로 렌더해 볼 수 있습니다.
- Streamlit 마크다운 기본 스타일과 섞이지 않도록 h/p/ul/table 대신 div/span만 씁니다.
- 모든 글자는 이스케이프하고 $ 는 수식으로 읽히지 않게 바꿉니다. 한 줄(빈 줄 없음) HTML 입니다.
"""

import hashlib
import html
import math
import re
from urllib.parse import quote

AVATARS = ["🦊", "🐯", "🐻", "🐼", "🐨", "🦁", "🐸", "🐵", "🦄", "🐙", "🦉", "🐧", "🐶", "🐱", "🦖", "🐳"]


def esc(value):
    return html.escape(str(value if value is not None else ""), quote=True).replace("$", "&#36;")


def _hash(text):
    return int(hashlib.md5(str(text).encode("utf-8")).hexdigest(), 16)


def avatar(nick, size=36):
    h = _hash(nick)
    hue = h % 360
    face = AVATARS[(h >> 8) % len(AVATARS)]

    return (
        f'<span class="ar-av" style="width:{size}px;height:{size}px;font-size:{int(size * 0.56)}px;'
        f'background:linear-gradient(135deg,hsl({hue},62%,52%),hsl({(hue + 40) % 360},62%,30%))">{face}</span>'
    )


def _compact(text):
    return re.sub(r"\s*\n\s*", "", text).strip()


CSS = _compact("""
.stApp{background:radial-gradient(1100px 520px at 50% -8%,#1d1409 0%,#09090a 62%)!important}
[data-testid="stHeader"]{background:transparent!important}
.stApp :is([data-testid="stMain"],section.main,.stMain,[data-testid="stMainBlockContainer"],[data-testid="stAppViewBlockContainer"]){color:#ececee}
.stApp :is([data-testid="stMain"],section.main,.stMain,[data-testid="stMainBlockContainer"],[data-testid="stAppViewBlockContainer"]) h1,.stApp :is([data-testid="stMain"],section.main,.stMain,[data-testid="stMainBlockContainer"],[data-testid="stAppViewBlockContainer"]) h2,.stApp :is([data-testid="stMain"],section.main,.stMain,[data-testid="stMainBlockContainer"],[data-testid="stAppViewBlockContainer"]) h3,.stApp :is([data-testid="stMain"],section.main,.stMain,[data-testid="stMainBlockContainer"],[data-testid="stAppViewBlockContainer"]) h4,.stApp :is([data-testid="stMain"],section.main,.stMain,[data-testid="stMainBlockContainer"],[data-testid="stAppViewBlockContainer"]) h5{color:#fff!important}
.stApp :is([data-testid="stMain"],section.main,.stMain,[data-testid="stMainBlockContainer"],[data-testid="stAppViewBlockContainer"]) [data-testid="stCaptionContainer"],.stApp :is([data-testid="stMain"],section.main,.stMain,[data-testid="stMainBlockContainer"],[data-testid="stAppViewBlockContainer"]) [data-testid="stCaptionContainer"] *{color:#a3a3ab!important}
.stApp :is([data-testid="stMain"],section.main,.stMain,[data-testid="stMainBlockContainer"],[data-testid="stAppViewBlockContainer"]) [data-testid="stWidgetLabel"] p,.stApp :is([data-testid="stMain"],section.main,.stMain,[data-testid="stMainBlockContainer"],[data-testid="stAppViewBlockContainer"]) [data-testid="stWidgetLabel"] label{color:#d8d8dd!important}
.stApp :is([data-testid="stMain"],section.main,.stMain,[data-testid="stMainBlockContainer"],[data-testid="stAppViewBlockContainer"]) label[data-baseweb="radio"] *,.stApp :is([data-testid="stMain"],section.main,.stMain,[data-testid="stMainBlockContainer"],[data-testid="stAppViewBlockContainer"]) label[data-baseweb="checkbox"] *{color:#e8e8ea!important}
.stApp :is([data-testid="stMain"],section.main,.stMain,[data-testid="stMainBlockContainer"],[data-testid="stAppViewBlockContainer"]) [data-baseweb="tab-list"]{gap:8px;background:transparent!important}
.stApp :is([data-testid="stMain"],section.main,.stMain,[data-testid="stMainBlockContainer"],[data-testid="stAppViewBlockContainer"]) [data-baseweb="tab-highlight"],.stApp :is([data-testid="stMain"],section.main,.stMain,[data-testid="stMainBlockContainer"],[data-testid="stAppViewBlockContainer"]) [data-baseweb="tab-border"]{display:none!important}
.stApp :is([data-testid="stMain"],section.main,.stMain,[data-testid="stMainBlockContainer"],[data-testid="stAppViewBlockContainer"]) button[data-baseweb="tab"]{background:#17171b!important;border:1px solid #2d2d33!important;border-radius:999px!important;padding:6px 18px!important;height:auto!important}
.stApp :is([data-testid="stMain"],section.main,.stMain,[data-testid="stMainBlockContainer"],[data-testid="stAppViewBlockContainer"]) button[data-baseweb="tab"] p{color:#b9b9c0!important;font-weight:800!important}
.stApp :is([data-testid="stMain"],section.main,.stMain,[data-testid="stMainBlockContainer"],[data-testid="stAppViewBlockContainer"]) button[data-baseweb="tab"][aria-selected="true"]{background:#3a1a08!important;border-color:#ff6a1a!important}
.stApp :is([data-testid="stMain"],section.main,.stMain,[data-testid="stMainBlockContainer"],[data-testid="stAppViewBlockContainer"]) button[data-baseweb="tab"][aria-selected="true"] p{color:#fff!important}
.stApp :is([data-testid="stMain"],section.main,.stMain,[data-testid="stMainBlockContainer"],[data-testid="stAppViewBlockContainer"]) [data-testid="stForm"]{border:1px solid #2a2a2f!important;background:#101013;border-radius:16px}
.stApp :is([data-testid="stMain"],section.main,.stMain,[data-testid="stMainBlockContainer"],[data-testid="stAppViewBlockContainer"]) [data-testid="stExpander"]{background:#111114!important;border:1px solid #2a2a2f!important;border-radius:14px}
.stApp :is([data-testid="stMain"],section.main,.stMain,[data-testid="stMainBlockContainer"],[data-testid="stAppViewBlockContainer"]) [data-testid="stExpander"] summary p{color:#e8e8ea!important}
.stApp :is([data-testid="stMain"],section.main,.stMain,[data-testid="stMainBlockContainer"],[data-testid="stAppViewBlockContainer"]) [data-testid="stAlert"]{background:#17171b!important;border:1px solid #34343a!important;border-radius:14px}
.stApp :is([data-testid="stMain"],section.main,.stMain,[data-testid="stMainBlockContainer"],[data-testid="stAppViewBlockContainer"]) [data-testid="stAlert"] *{color:#ececee!important}
.stApp :is([data-testid="stMain"],section.main,.stMain,[data-testid="stMainBlockContainer"],[data-testid="stAppViewBlockContainer"]) [data-testid^="stBaseButton"]{border-radius:12px!important;font-weight:800!important}
.stApp :is([data-testid="stMain"],section.main,.stMain,[data-testid="stMainBlockContainer"],[data-testid="stAppViewBlockContainer"]) [data-testid="stBaseButton-secondary"],.stApp :is([data-testid="stMain"],section.main,.stMain,[data-testid="stMainBlockContainer"],[data-testid="stAppViewBlockContainer"]) button[kind="secondary"]{background:#1d1d22!important;border:1px solid #37373e!important;color:#f2f2f2!important}
.stApp :is([data-testid="stMain"],section.main,.stMain,[data-testid="stMainBlockContainer"],[data-testid="stAppViewBlockContainer"]) [data-testid="stBaseButton-secondary"] *,.stApp :is([data-testid="stMain"],section.main,.stMain,[data-testid="stMainBlockContainer"],[data-testid="stAppViewBlockContainer"]) button[kind="secondary"] *{color:#f2f2f2!important}
.stApp :is([data-testid="stMain"],section.main,.stMain,[data-testid="stMainBlockContainer"],[data-testid="stAppViewBlockContainer"]) [data-testid="stBaseButton-primary"],.stApp :is([data-testid="stMain"],section.main,.stMain,[data-testid="stMainBlockContainer"],[data-testid="stAppViewBlockContainer"]) button[kind="primary"],.stApp :is([data-testid="stMain"],section.main,.stMain,[data-testid="stMainBlockContainer"],[data-testid="stAppViewBlockContainer"]) [data-testid="stBaseButton-primaryFormSubmit"]{background:#4ade80!important;border:1px solid #4ade80!important;color:#05200f!important}
.stApp :is([data-testid="stMain"],section.main,.stMain,[data-testid="stMainBlockContainer"],[data-testid="stAppViewBlockContainer"]) [data-testid="stBaseButton-primary"] *,.stApp :is([data-testid="stMain"],section.main,.stMain,[data-testid="stMainBlockContainer"],[data-testid="stAppViewBlockContainer"]) button[kind="primary"] *,.stApp :is([data-testid="stMain"],section.main,.stMain,[data-testid="stMainBlockContainer"],[data-testid="stAppViewBlockContainer"]) [data-testid="stBaseButton-primaryFormSubmit"] *{color:#05200f!important}
.stApp :is([data-testid="stMain"],section.main,.stMain,[data-testid="stMainBlockContainer"],[data-testid="stAppViewBlockContainer"]) [data-testid="stBaseButton-secondary"]:hover{border-color:#4ade80!important}
.stApp :is([data-testid="stMain"],section.main,.stMain,[data-testid="stMainBlockContainer"],[data-testid="stAppViewBlockContainer"]) [class*="st-key-arena_card"]{background:#141417;border:1.5px solid #2c2c32;border-radius:16px;padding:12px 12px 10px}
.stApp :is([data-testid="stMain"],section.main,.stMain,[data-testid="stMainBlockContainer"],[data-testid="stAppViewBlockContainer"]) [class*="st-key-arena_card"] button{justify-content:flex-start!important;text-align:left!important;min-height:46px}
.stApp :is([data-testid="stMain"],section.main,.stMain,[data-testid="stMainBlockContainer"],[data-testid="stAppViewBlockContainer"]) [class*="st-key-arena_save"] button{min-height:54px;font-size:1.1rem!important;border-radius:999px!important;box-shadow:0 0 0 1px #4ade80,0 0 22px rgba(74,222,128,.28)}
.stApp :is([data-testid="stMain"],section.main,.stMain,[data-testid="stMainBlockContainer"],[data-testid="stAppViewBlockContainer"]) [data-testid="stTextInputRootElement"],.stApp :is([data-testid="stMain"],section.main,.stMain,[data-testid="stMainBlockContainer"],[data-testid="stAppViewBlockContainer"]) [data-baseweb="input"],.stApp :is([data-testid="stMain"],section.main,.stMain,[data-testid="stMainBlockContainer"],[data-testid="stAppViewBlockContainer"]) [data-baseweb="base-input"],.stApp :is([data-testid="stMain"],section.main,.stMain,[data-testid="stMainBlockContainer"],[data-testid="stAppViewBlockContainer"]) [data-baseweb="textarea"],.stApp :is([data-testid="stMain"],section.main,.stMain,[data-testid="stMainBlockContainer"],[data-testid="stAppViewBlockContainer"]) [data-testid="stTextArea"] textarea,.stApp :is([data-testid="stMain"],section.main,.stMain,[data-testid="stMainBlockContainer"],[data-testid="stAppViewBlockContainer"]) [data-testid="stNumberInputContainer"],.stApp :is([data-testid="stMain"],section.main,.stMain,[data-testid="stMainBlockContainer"],[data-testid="stAppViewBlockContainer"]) [data-baseweb="select"]>div{background-color:#17171b!important;border-color:#33333a!important;color:#f2f2f2!important}
.stApp :is([data-testid="stMain"],section.main,.stMain,[data-testid="stMainBlockContainer"],[data-testid="stAppViewBlockContainer"]) input,.stApp :is([data-testid="stMain"],section.main,.stMain,[data-testid="stMainBlockContainer"],[data-testid="stAppViewBlockContainer"]) textarea{background-color:#17171b!important;color:#f2f2f2!important;-webkit-text-fill-color:#f2f2f2!important;caret-color:#4ade80}
.stApp :is([data-testid="stMain"],section.main,.stMain,[data-testid="stMainBlockContainer"],[data-testid="stAppViewBlockContainer"]) input::placeholder,.stApp :is([data-testid="stMain"],section.main,.stMain,[data-testid="stMainBlockContainer"],[data-testid="stAppViewBlockContainer"]) textarea::placeholder{color:#7d7d86!important;-webkit-text-fill-color:#7d7d86!important}
.stApp :is([data-testid="stMain"],section.main,.stMain,[data-testid="stMainBlockContainer"],[data-testid="stAppViewBlockContainer"]) [data-testid="stTextInputRootElement"] button,.stApp :is([data-testid="stMain"],section.main,.stMain,[data-testid="stMainBlockContainer"],[data-testid="stAppViewBlockContainer"]) [data-testid="stTextInputRootElement"] svg{background:transparent!important;color:#c8c8ce!important;fill:#c8c8ce!important}
.stApp :is([data-testid="stMain"],section.main,.stMain,[data-testid="stMainBlockContainer"],[data-testid="stAppViewBlockContainer"]) [data-testid="stBaseButton-secondaryFormSubmit"]{background:#1d1d22!important;border:1px solid #37373e!important}
.stApp :is([data-testid="stMain"],section.main,.stMain,[data-testid="stMainBlockContainer"],[data-testid="stAppViewBlockContainer"]) [data-testid="stBaseButton-secondaryFormSubmit"] *{color:#f2f2f2!important}
.stApp :is([data-testid="stMain"],section.main,.stMain,[data-testid="stMainBlockContainer"],[data-testid="stAppViewBlockContainer"]) [data-testid="stBaseButton-secondaryFormSubmit"]:hover{border-color:#4ade80!important}
.stApp :is([data-testid="stMain"],section.main,.stMain,[data-testid="stMainBlockContainer"],[data-testid="stAppViewBlockContainer"]) [data-testid="InputInstructions"],.stApp :is([data-testid="stMain"],section.main,.stMain,[data-testid="stMainBlockContainer"],[data-testid="stAppViewBlockContainer"]) [data-testid="InputInstructions"] *{color:#7d7d86!important}
html body .stApp button[data-baseweb="tab"]{background:#17171b!important;border:1px solid #2d2d33!important;border-radius:999px!important;padding:6px 18px!important;height:auto!important}
html body .stApp button[data-baseweb="tab"],html body .stApp button[data-baseweb="tab"] *{color:#d6d6dc!important;font-weight:800!important}
html body .stApp button[data-baseweb="tab"][aria-selected="true"]{background:#3a1a08!important;border-color:#ff6a1a!important}
html body .stApp button[data-baseweb="tab"][aria-selected="true"],html body .stApp button[data-baseweb="tab"][aria-selected="true"] *{color:#ffffff!important}
html body .stApp [data-baseweb="tab-list"]{gap:8px!important;background:transparent!important}
html body .stApp [data-baseweb="tab-highlight"],html body .stApp [data-baseweb="tab-border"]{display:none!important}
html body .stApp [data-testid="stExpander"] summary,html body .stApp [data-testid="stExpander"] summary *{color:#e8e8ea!important}
html body .stApp [data-testid="stAlert"],html body .stApp [data-testid="stAlert"] [data-baseweb="notification"],html body .stApp [data-testid="stAlertContainer"],html body .stApp [data-testid="stNotification"]{background:#17171b!important;background-color:#17171b!important;border:1px solid #34343a!important;border-radius:14px!important}
html body .stApp [data-testid="stAlert"] *,html body .stApp [data-testid="stNotification"] *{color:#ececee!important}
html body .stApp [data-testid="stAlert"]:has([data-testid="stAlertContentSuccess"]){border-left:4px solid #4ade80!important}
html body .stApp [data-testid="stAlert"]:has([data-testid="stAlertContentInfo"]){border-left:4px solid #6ea8ff!important}
html body .stApp [data-testid="stAlert"]:has([data-testid="stAlertContentWarning"]){border-left:4px solid #ffd23f!important}
html body .stApp [data-testid="stAlert"]:has([data-testid="stAlertContentError"]){border-left:4px solid #ff6b6b!important}
html body .stApp [data-testid="stCode"],html body .stApp [data-testid="stCode"] pre,html body .stApp [data-testid="stCodeBlock"]{background:#111114!important}
html body .stApp [data-testid="stCode"] *,html body .stApp [data-testid="stCodeBlock"] *{color:#e6e6ea!important}
html body .stApp [data-testid="stToast"],html body .stApp [data-testid="stToast"] *{color:#111!important}
html body .stApp [data-testid="stExpander"],html body .stApp [data-testid="stExpander"] details,html body .stApp [data-testid="stExpander"] summary,html body .stApp [data-testid="stExpanderDetails"],html body .stApp [data-testid="stExpander"] [data-testid="stExpanderToggleIcon"]{background:#111114!important;background-color:#111114!important;color:#ececee!important}
html body .stApp [data-testid="stExpander"] details{border:1px solid #2a2a2f!important;border-radius:14px!important}
html body .stApp [data-testid="stExpander"] summary:hover,html body .stApp [data-testid="stExpander"] summary:focus,html body .stApp [data-testid="stExpander"] summary:active{background:#1a1a1f!important;color:#ffffff!important}
html body .stApp [data-testid="stExpander"] summary *,html body .stApp [data-testid="stExpander"] summary svg{color:#ececee!important;fill:#ececee!important}
html body .stApp [data-testid="stExpanderDetails"] *{color:#e6e6ea}
html body .stApp [data-testid="stVerticalBlockBorderWrapper"]{border-color:#2a2a2f!important}
html body .stApp [data-testid="stSelectbox"] [data-baseweb="select"] *,html body .stApp [data-testid="stMultiSelect"] [data-baseweb="select"] *{background-color:#17171b!important;color:#f2f2f2!important}
html body .stApp [data-baseweb="select"] svg{fill:#c8c8ce!important}
html body .stApp [data-testid="stToggle"] label,html body .stApp [data-testid="stToggle"] label *,html body .stApp [data-testid="stCheckbox"] label,html body .stApp [data-testid="stCheckbox"] label *{color:#e8e8ea!important}
html body .stApp [data-testid="stDateInput"] *,html body .stApp [data-testid="stNumberInput"] *{background-color:#17171b!important;color:#f2f2f2!important}
html body [data-baseweb="popover"],html body [data-baseweb="popover"] > div,html body [data-baseweb="menu"],html body [data-baseweb="popover"] ul{background:#1b1b1f!important;background-color:#1b1b1f!important;color:#f2f2f2!important}
html body [data-baseweb="popover"] li,html body [data-baseweb="popover"] li *,html body [data-baseweb="menu"] li,html body [data-baseweb="menu"] li *{background-color:#1b1b1f!important;color:#f2f2f2!important}
html body [data-baseweb="popover"] li:hover,html body [data-baseweb="popover"] li[aria-selected="true"],html body [data-baseweb="menu"] li:hover{background-color:#2c2c33!important}
.ar{--green:#4ade80;--yellow:#ffd23f;--orange:#ff6a1a;--line:#2c2c32;--panel:#141417;color:#ececee;font-family:inherit}
.ar *{box-sizing:border-box}
.ar-hero{text-align:center;padding:6px 0 4px}
.ar-brand{display:inline-block;background:var(--yellow);color:#111;font-weight:900;font-size:12px;letter-spacing:.12em;padding:3px 10px;border-radius:5px}
.ar-title{margin:10px 0 2px;font-size:clamp(26px,5vw,44px);font-weight:900;font-style:italic;letter-spacing:.01em;color:#fff;line-height:1.1}
.ar-title b{color:var(--green)}
.ar-sub{font-size:14px;color:#9e9ea6}
.ar-links{margin-top:10px;display:flex;gap:18px;justify-content:center;flex-wrap:wrap;font-style:italic;font-weight:900;color:var(--yellow);font-size:15px}
.ar-marq{margin:16px 0 6px;overflow:hidden;border-top:1px solid #1f3a28;border-bottom:1px solid #1f3a28;background:#0b120d;white-space:nowrap}
.ar-mtrack{display:inline-flex;animation:ar-scroll 26s linear infinite}
.ar-mtrack span{padding:9px 26px;font-style:italic;font-weight:900;font-size:clamp(14px,2.4vw,20px);color:var(--green);text-transform:uppercase}
.ar-mtrack span.y{color:var(--yellow)}
@keyframes ar-scroll{from{transform:translateX(0)}to{transform:translateX(-50%)}}
.ar-rounds{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:6px;margin:8px 0 14px;text-align:center}
.ar-rd{padding:6px 2px;border-radius:10px;border:1px solid transparent}
.ar-rd .n{font-size:14px;font-weight:800;color:#d9d9de}
.ar-rd .d{font-size:12px;color:#8d8d95}
.ar-rd .s{font-size:11px;font-weight:800;margin-top:2px;color:#6d6d75}
.ar-rd.done .s{color:var(--green)}.ar-rd.live{border-color:var(--yellow);background:#201a07}.ar-rd.live .s{color:var(--yellow)}.ar-rd.void .s{color:#ff6b6b}
.ar-prizes{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin:10px 0 14px}
.ar-pz{background:linear-gradient(160deg,#1c1408,#111114 70%);border:1px solid #3a2b12;border-radius:18px;padding:14px 16px}
.ar-pz .k{font-size:13px;font-weight:800;color:var(--yellow)}
.ar-pz .v{font-size:clamp(20px,3.4vw,28px);font-weight:900;color:#fff;margin:4px 0 2px}
.ar-pz .h{display:inline-block;font-size:11px;font-weight:800;color:#a3a3ab;border:1px solid #38383f;border-radius:999px;padding:1px 8px;margin-bottom:6px}
.ar-pz .t{font-size:14px;font-weight:800;color:var(--green)}
.ar-pz .r{font-size:12.5px;color:#a3a3ab;margin-top:3px}
.ar-cta{border:1.5px solid #2a6b43;border-radius:20px;background:radial-gradient(500px 220px at 50% 0,#14301f,#0d1511 70%);padding:18px 16px;text-align:center;box-shadow:0 0 32px rgba(74,222,128,.10)}
.ar-cta .a{font-size:clamp(18px,2.6vw,26px);font-weight:900;font-style:italic;color:var(--green)}
.ar-cta .b{margin-top:8px;font-size:14px;color:#e6e6ea;line-height:1.55}
.ar-cta .c{margin-top:10px;font-size:13px;font-weight:800;color:var(--yellow)}
.ar-cta .d{margin-top:6px;font-size:12px;color:#8d8d95}
.ar-tag{font-size:11px;font-weight:900;letter-spacing:.08em;color:#8d8d95;margin:0 0 6px 2px}
.ar-team{display:flex;align-items:center;gap:10px;padding:9px 10px;border-radius:12px;background:#1c1c21;border:1px solid #2f2f36;margin-bottom:6px}
.ar-team .nm{flex:1;min-width:0;font-weight:800;color:#f2f2f2;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.ar-team .pc{font-size:13px;font-weight:800;color:#c8c8ce;background:#26262c;border:1px solid #3a3a42;border-radius:8px;padding:2px 8px;min-width:46px;text-align:center}
.ar-team.pick{border-color:var(--green);background:#0f2218}.ar-team.pick .pc{color:#05200f;background:var(--green);border-color:var(--green)}
.ar-team.win{border-color:var(--yellow);background:#241d08}.ar-team.win .pc{color:#201700;background:var(--yellow);border-color:var(--yellow)}
.ar-team.lose{opacity:.55}
.ar-team .mk{font-size:15px}
.ar-team .ex{font-size:12.5px;font-weight:800;color:#c8c8ce}.ar-team .ex.up{color:#ff6b6b}.ar-team .ex.dn{color:#6ea8ff}
.ar-vs{text-align:center;font-size:11px;font-weight:900;color:#6d6d75;letter-spacing:.2em;margin:-2px 0 4px}
.ar-av{display:inline-flex;align-items:center;justify-content:center;border-radius:28%;flex:0 0 auto;box-shadow:inset 0 0 0 1px rgba(255,255,255,.16)}
.ar-tk{display:inline-flex;align-items:center;justify-content:center;width:28px;height:28px;border-radius:50%;background:#2a2a31;color:#e9e9ee;font-weight:900;font-size:13px;flex:0 0 auto}
.ar-pill{display:inline-block;background:#17171b;border:1px solid #34343a;border-radius:999px;padding:6px 14px;font-size:13px;color:#d8d8dd}
.ar-pill b{color:#fff}
.ar-me{text-align:center;margin:10px 0 2px}
.ar-pod{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px;align-items:end;max-width:880px;margin:10px auto 6px;padding:18px 0 0;border-radius:28px;background:radial-gradient(600px 300px at 50% 18%,rgba(255,106,26,.20),transparent 70%)}
.ar-pcol{text-align:center;min-width:0}
.ar-pname{margin:8px 0 8px;font-size:clamp(17px,2.6vw,26px);font-weight:800;color:#fff;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.ar-p1 .ar-pname{font-size:clamp(20px,3.2vw,32px)}
.ar-pbox{position:relative}
.ar-ptop{height:34px;background:linear-gradient(#3a2112,#2b180d);clip-path:polygon(10% 0,90% 0,100% 100%,0 100%);opacity:.95}
.ar-pbody{background:linear-gradient(#2a170c,#150d08);border:1px solid #3a2415;border-top:0;padding:12px 8px 16px;display:flex;flex-direction:column;align-items:center}
.ar-p1 .ar-pbody{min-height:236px}.ar-p2 .ar-pbody{min-height:196px}.ar-p3 .ar-pbody{min-height:176px}
.ar-trophy{display:inline-flex;width:40px;height:40px;border-radius:10px;align-items:center;justify-content:center;font-size:22px}
.ar-t1{background:#ecd08a}.ar-t2{background:#dcdce0}.ar-t3{background:#dcae60}
.ar-psm{margin-top:8px;font-size:13px;color:#d4c7bd}
.ar-pln{width:88%;border-top:1px solid #4a3020;margin:12px 0 10px}
.ar-pbig{font-size:clamp(22px,3.6vw,36px);font-weight:900;color:#fff;letter-spacing:.02em;font-variant-numeric:tabular-nums}
.ar-pbig i{font-style:normal;color:var(--orange)}
.ar-plb{font-size:12.5px;color:#a89a90;margin-top:2px}
.ar-panel{margin:16px 0 8px;border:1px solid #2d1c12;border-radius:26px;background:linear-gradient(#150e0a,#0b0908);padding:16px 14px 12px}
.ar-ph{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:2px 8px 12px}
.ar-ph b{font-size:22px;font-weight:800;color:#fff}.ar-ph span{font-size:12.5px;color:#a89a90}
.ar-th,.ar-tr{display:grid;align-items:center;gap:8px;padding:0 14px}
.ar-th{background:#2a150b;border-radius:999px;height:46px;font-size:13px;font-weight:800;color:#e6d9cf}
.ar-tr{min-height:58px;border-bottom:1px solid #261812;font-size:14.5px;color:#e6e0db;font-variant-numeric:tabular-nums}
.ar-tr:last-child{border-bottom:0}
.ar-tr.me{background:rgba(255,106,26,.10);border-radius:14px}
.ar-tr .rk{color:#a89a90;font-weight:700}
.ar-tr .who{display:flex;align-items:center;gap:10px;min-width:0;font-weight:700;color:#fff}
.ar-tr .who span.t{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.ar-tr .c{text-align:center}.ar-tr .e{text-align:right;color:#fff;font-weight:800}
.ar-th .c{text-align:center}.ar-th .e{text-align:right}
.ar-tr .g{color:var(--green);font-weight:800}.ar-tr .x{color:#ff7a7a;font-weight:800}
.ar-dt{display:grid;align-items:center;gap:6px;padding:9px 10px;border-bottom:1px solid #261812;font-size:14px;color:#e6e0db}.ar-dt.h{background:#2a150b;border-radius:999px;font-weight:800;border-bottom:0;color:#e6d9cf}.ar-dt .c{text-align:center}.ar-dt:last-child{border-bottom:0}
.ar-empty{padding:26px 10px;text-align:center;color:#a89a90;font-size:14px}
.ar-stats{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;margin:8px 0 12px}
.ar-st{border:1px solid #3a2415;border-radius:18px;background:linear-gradient(160deg,#2a150b,#140d09);padding:14px 14px}
.ar-st .k{font-size:12.5px;color:#b9a79b}.ar-st .v{font-size:clamp(22px,3.4vw,32px);font-weight:900;color:#fff;margin-top:2px}.ar-st .v i{font-style:normal;color:var(--orange)}
.ar-st .s{font-size:12px;color:#8f8076;margin-top:2px}
.ar-sec{margin:16px 0 8px;font-size:18px;font-weight:800;color:#fff}
.ar-sec i{font-style:normal;color:var(--orange);margin-right:6px}
.ar-bars{display:flex;align-items:flex-end;gap:4px;height:84px;margin:8px 0 2px;padding:0 2px}
.ar-bar{flex:1 1 0;max-width:56px;min-width:0;display:flex;flex-direction:column;justify-content:flex-end;align-items:center;height:100%}
.ar-bar i{display:block;width:100%;max-width:26px;border-radius:5px 5px 2px 2px;background:#2f2f36;min-height:3px}
.ar-bar i.g{background:var(--green)}.ar-bar i.y{background:var(--yellow)}.ar-bar i.o{background:var(--orange)}
.ar-bar span{margin-top:4px;font-size:10.5px;color:#8f8076;white-space:nowrap}
.ar-badges{display:flex;flex-wrap:wrap;gap:8px;margin:6px 0 10px}
.ar-badge{border:1px solid #4a3418;background:#1d1409;color:#f3dfb0;border-radius:999px;padding:5px 12px;font-size:12.5px;font-weight:800}
@media (max-width:760px){
.ar-prizes{grid-template-columns:1fr}
.ar-stats{grid-template-columns:repeat(2,minmax(0,1fr))}
.ar-pod{gap:8px}
.ar-p1 .ar-pbody{min-height:200px}.ar-p2 .ar-pbody{min-height:166px}.ar-p3 .ar-pbody{min-height:150px}
.ar-th,.ar-tr{padding:0 8px;gap:4px;font-size:12.5px}
.ar-rd .n{font-size:12.5px}
}
""")


# ---------------------------------------------------------------
# 시즌 MVP 배너 (시상식 느낌: 와인색 바탕 · 금색 선 · 트로피 · 월계관)
# ---------------------------------------------------------------

def _svg_uri(svg):
    return "url(\"data:image/svg+xml," + quote(svg, safe="/:=,;()'") + "\")"


def _laurel():
    """월계관: 가운데가 비고 아래쪽이 모이는 두 가닥의 잎."""

    leaves = []

    for side in (-1, 1):
        for i in range(11):
            t = i / 10
            angle = math.radians(105 + t * 150)
            x = 60 + side * 44 * math.cos(angle) * -1
            y = 62 - 44 * math.sin(angle) * -1 * -1
            rot = side * (35 + t * 70) - 90 * side
            leaves.append(
                f'<ellipse cx="{x:.1f}" cy="{y:.1f}" rx="4" ry="10" fill="url(#g)" '
                f'transform="rotate({rot:.0f} {x:.1f} {y:.1f})"/>'
            )

    stars = "".join(
        f'<path d="M{cx} {cy - r} l{r * .3:.1f} {r * .65:.1f} {r * .7:.1f} .1 -{r * .55:.1f} {r * .45:.1f} {r * .2:.1f} {r * .72:.1f} '
        f'-{r * .65:.1f} -{r * .4:.1f} -{r * .65:.1f} {r * .4:.1f} {r * .2:.1f} -{r * .72:.1f} -{r * .55:.1f} -{r * .45:.1f} {r * .7:.1f} -{r * .1:.1f}z" fill="#f2c94c"/>'
        for cx, cy, r in ((60, 7, 6), (47, 11, 4), (73, 11, 4))
    )

    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 120">'
        '<defs><linearGradient id="g" x1="0" y1="0" x2="0" y2="1">'
        '<stop offset="0" stop-color="#f7dc7a"/><stop offset="1" stop-color="#b88a1f"/></linearGradient></defs>'
        + "".join(leaves) + stars + "</svg>"
    )


def _trophy():
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 160 200">'
        '<defs><linearGradient id="t" x1="0" y1="0" x2="1" y2="1">'
        '<stop offset="0" stop-color="#fff0a8"/><stop offset=".45" stop-color="#e9b93d"/><stop offset="1" stop-color="#9a6a12"/></linearGradient></defs>'
        '<path d="M38 22h84v38c0 30-18 52-42 56-24-4-42-26-42-56z" fill="url(#t)"/>'
        '<path d="M38 34H16c0 30 12 46 34 52M122 34h22c0 30-12 46-34 52" fill="none" stroke="url(#t)" stroke-width="9" stroke-linecap="round"/>'
        '<path d="M72 114h16v30H72z" fill="url(#t)"/>'
        '<path d="M52 144h56l8 16H44z" fill="url(#t)"/>'
        '<rect x="38" y="160" width="84" height="22" rx="4" fill="url(#t)"/>'
        '<path d="M80 40l6 13 14 2-10 10 3 14-13-7-13 7 3-14-10-10 14-2z" fill="#7a1010" opacity=".85"/>'
        '</svg>'
    )


def _lines():
    gold = 'stroke="#e6b94a" stroke-width="2" fill="none"'

    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 960 300" preserveAspectRatio="xMidYMid slice">'
        f'<path d="M0 110L210 0" {gold} opacity=".9"/><path d="M0 190L120 0" {gold} opacity=".55"/>'
        f'<path d="M0 40L90 0" {gold} opacity=".45"/>'
        f'<path d="M960 70L810 0" {gold} opacity=".9"/><path d="M960 150L700 0" {gold} opacity=".5"/>'
        f'<path d="M960 230L880 300" {gold} opacity=".8"/><path d="M0 250L70 300" {gold} opacity=".8"/>'
        f'<path d="M0 0L210 0L0 110z" fill="#3b0a0a" opacity=".55"/><path d="M960 0L810 0L960 70z" fill="#3b0a0a" opacity=".55"/>'
        '</svg>'
    )


BAN_CSS = _compact(
    """
.ar-ban{position:relative;margin:10px 0 14px;border:1.5px solid #8a6a12;border-radius:22px;overflow:hidden;
display:flex;align-items:center;gap:clamp(10px,3vw,34px);padding:clamp(14px,3vw,26px) clamp(16px,4vw,40px);
background:%LINES% center/cover no-repeat,radial-gradient(700px 260px at 62% 40%,#2a0707 0%,#110303 72%);
box-shadow:0 0 0 1px #3a2308 inset,0 10px 34px rgba(0,0,0,.45)}
.ar-ban:before{content:"";position:absolute;left:0;top:0;bottom:0;width:34%;opacity:.5;pointer-events:none;
background-image:radial-gradient(#c79a35 1px,transparent 1.4px);background-size:10px 10px;
-webkit-mask-image:linear-gradient(90deg,#000,transparent);mask-image:linear-gradient(90deg,#000,transparent)}
.ar-ban .tr{position:relative;flex:0 0 auto;width:clamp(74px,16vw,150px);aspect-ratio:4/5;
background:%TROPHY% center/contain no-repeat;filter:drop-shadow(0 8px 14px rgba(0,0,0,.55))}
.ar-ban .bd{position:relative;flex:1;min-width:0;text-align:center}
.ar-ban .top{display:flex;align-items:center;justify-content:center;gap:clamp(8px,2vw,16px);flex-wrap:wrap}
.ar-ban .wr{position:relative;display:inline-flex;flex-direction:column;align-items:center;justify-content:center;
width:clamp(84px,13vw,118px);aspect-ratio:1;background:%LAUREL% center/contain no-repeat;line-height:1}
.ar-ban .wr b{font-size:clamp(12px,2.1vw,17px);font-weight:900;color:#ee3d4b;letter-spacing:0;margin-top:8px}
.ar-ban .wr u{text-decoration:none;font-size:clamp(9px,1.5vw,12.5px);font-weight:900;color:#e9bf55;margin-top:2px}
.ar-ban .yr{font-size:clamp(20px,3.6vw,38px);font-weight:900;color:#ee3d4b}
.ar-ban .mv{font-size:clamp(24px,4.4vw,48px);font-weight:900;color:#ffd23f;text-shadow:0 2px 0 #5a1a0a}
.ar-ban .nm{margin:6px 0 4px;font-size:clamp(34px,8vw,84px);font-weight:900;line-height:1.08;letter-spacing:.01em;
background:linear-gradient(180deg,#fff7c9 0%,#f1cd5e 48%,#c28d22 100%);-webkit-background-clip:text;background-clip:text;
-webkit-text-fill-color:transparent;color:transparent;
filter:drop-shadow(0 3px 0 #5a0f12) drop-shadow(0 0 2px #3a0508);overflow-wrap:anywhere}
.ar-ban .nm.many{font-size:clamp(24px,5vw,52px)}
.ar-ban .mt{font-size:clamp(14px,2.2vw,22px);font-weight:600;color:#f6efe2;font-family:"Nanum Myeongjo","Noto Serif KR",serif}
.ar-ban .nt{margin-top:8px;font-size:12px;color:#b99a62}
@media (max-width:560px){.ar-ban{flex-direction:column;text-align:center;padding:16px 14px}.ar-ban .tr{width:78px}}
"""
).replace("%LINES%", _svg_uri(_lines())).replace("%TROPHY%", _svg_uri(_trophy())).replace("%LAUREL%", _svg_uri(_laurel()))

CSS = CSS + BAN_CSS


def css_only():
    return f'<style>{CSS}</style>'


def frag(inner):
    return f'<div class="ar">{inner}</div>'


def wrap(inner):
    return f'<style>{CSS}</style><div class="ar">{inner}</div>'


# ---------------------------------------------------------------
# 머리말 · 배너 · 일정 · 상품
# ---------------------------------------------------------------

def hero(date_text, links):
    chips = "".join(f"<span>{esc(t)}</span>" for t in links)
    unit = "".join(
        f'<span class="{"y" if i % 2 else ""}">{esc(t)}</span>'
        for i, t in enumerate(["오늘의 5대결 · 투표 08:00~08:59", "무료 참가", "꼴찌가 쏩니다!", "15:40 이후 결과 확정"])
    )

    return (
        '<div class="ar-hero"><span class="ar-brand">MTNW · 베타</span>'
        '<div class="ar-title">MTNW <b>주식 대결</b> 브래킷</div>'
        f'<div class="ar-sub">{esc(date_text)}</div>'
        f'<div class="ar-links">{chips}</div></div>'
        f'<div class="ar-marq"><div class="ar-mtrack">{unit * 3}{unit * 3}</div></div>'
    )


def round_strip(items):
    """items: [{'name','date','state'(done|live|wait|void),'label'}]"""

    cells = "".join(
        f'<div class="ar-rd {esc(i["state"])}"><div class="n">{esc(i["name"])}</div>'
        f'<div class="d">{esc(i["date"])}</div><div class="s">{esc(i["label"])}</div></div>'
        for i in items
    )

    return f'<div class="ar-rounds">{cells}</div>'


def prize_cards(cards):
    """cards: [{'title','name','how','line','sub'}]"""

    out = []

    for c in cards:
        how = f'<div class="h">{esc(c["how"])}</div>' if c.get("how") else ""
        sub = f'<div class="r">{esc(c["sub"])}</div>' if c.get("sub") else ""
        out.append(
            f'<div class="ar-pz"><div class="k">{esc(c["title"])}</div><div class="v">{esc(c["name"])}</div>{how}'
            f'<div class="t">🍀 꼴찌가 상품을 쏩니다!</div><div class="r">{esc(c["line"])}</div>{sub}</div>'
        )

    one = ' style="grid-template-columns:1fr"' if len(out) == 1 else ""

    return f'<div class="ar-prizes"{one}>{"".join(out)}</div>'


def cta_panel(title, lines, accent="", note=""):
    body = "<br>".join(esc(t) for t in lines)
    acc = f'<div class="c">{esc(accent)}</div>' if accent else ""
    nt = f'<div class="d">{esc(note)}</div>' if note else ""

    return f'<div class="ar-cta"><div class="a">{esc(title)}</div><div class="b">{body}</div>{acc}{nt}</div>'


# ---------------------------------------------------------------
# 대결 카드 (읽기 전용 행 · 버튼은 arena.py 에서 직접 그림)
# ---------------------------------------------------------------

def tag(text):
    return f'<div class="ar-tag">{esc(text)}</div>'


def vs_chip():
    return '<div class="ar-vs">VS</div>'


def team_row(name, state="", pct=None, mark="", extra=""):
    """state: '' | pick | win | lose"""

    initial = esc((str(name).strip() or "?")[0])
    badge = f'<span class="pc">{esc(pct)}</span>' if pct not in (None, "") else ""
    mk = f'<span class="mk">{esc(mark)}</span>' if mark else ""
    ex = ""

    if extra:
        tone = "up" if str(extra).startswith("+") else "dn" if str(extra).startswith("-") else ""
        ex = f'<span class="ex {tone}">{esc(extra)}</span>'

    return f'<div class="ar-team {state}"><span class="ar-tk">{initial}</span><span class="nm">{esc(name)}</span>{ex}{mk}{badge}</div>'


# ---------------------------------------------------------------
# 시상대 · Top Users 표 · 통계 카드
# ---------------------------------------------------------------

def podium(entries):
    """entries: 순위순 [{'nick','big','unit','label','small'}] (최대 3). 왼쪽 2위 · 가운데 1위 · 오른쪽 3위."""

    slots = {}

    for index, entry in enumerate(entries[:3], start=1):
        unit = f"<i>{esc(entry['unit'])}</i>" if entry.get("unit") else ""
        slots[index] = (
            f'<div class="ar-pcol ar-p{index}">{avatar(entry["nick"], 76 if index == 1 else 62)}'
            f'<div class="ar-pname">{esc(entry["nick"])}</div>'
            f'<div class="ar-pbox"><div class="ar-ptop"></div><div class="ar-pbody">'
            f'<span class="ar-trophy ar-t{index}">🏆</span><div class="ar-psm">{esc(entry.get("small", ""))}</div>'
            f'<div class="ar-pln"></div><div class="ar-pbig">{esc(entry["big"])}{unit}</div>'
            f'<div class="ar-plb">{esc(entry.get("label", ""))}</div></div></div></div>'
        )

    def empty(index):
        return (
            f'<div class="ar-pcol ar-p{index}"><span class="ar-av" style="width:56px;height:56px;background:#241913;font-size:26px">·</span>'
            f'<div class="ar-pname" style="color:#6d5d52">—</div><div class="ar-pbox"><div class="ar-ptop"></div>'
            f'<div class="ar-pbody"><span class="ar-trophy ar-t{index}" style="opacity:.35">🏆</span>'
            f'<div class="ar-psm">자리 비어 있음</div></div></div></div>'
        )

    ordered = [slots.get(2) or empty(2), slots.get(1) or empty(1), slots.get(3) or empty(3)]

    return f'<div class="ar-pod">{"".join(ordered)}</div>'


def me_pill(text):
    return f'<div class="ar-me"><span class="ar-pill">{text}</span></div>'


def top_table(title, sub, headers, rows, mine=None, start=1):
    """headers: ['순위','닉네임','월',...,'합계']  rows: [{'nick','cells':[...], 'tone':[None|'g'|'x', ...]}]
    첫 열(순위)·둘째 열(닉네임)은 자동. 마지막 열은 오른쪽 정렬."""

    if not rows:
        body = '<div class="ar-empty">아직 표시할 순위가 없습니다.</div>'
        return f'<div class="ar-panel"><div class="ar-ph"><b>{esc(title)}</b><span>{esc(sub)}</span></div>{body}</div>'

    extra = len(headers) - 2
    tpl = f"grid-template-columns:56px minmax(0,1.6fr) repeat({extra},minmax(44px,1fr))"

    head_cells = ['<span>' + esc(headers[0]) + "</span>", "<span>" + esc(headers[1]) + "</span>"]

    for index in range(extra):
        klass = "e" if index == extra - 1 else "c"
        head_cells.append(f'<span class="{klass}">{esc(headers[2 + index])}</span>')

    out = [f'<div class="ar-th" style="{tpl}">{"".join(head_cells)}</div>']

    for offset, row in enumerate(rows):
        rank = start + offset
        cells = [f'<span class="rk">#{rank:03d}</span>', f'<span class="who">{avatar(row["nick"], 30)}<span class="t">{esc(row["nick"])}</span></span>']
        tones = row.get("tone") or [None] * extra

        for index in range(extra):
            klass = "e" if index == extra - 1 else "c"
            tone = tones[index] if index < len(tones) else None
            klass += f" {tone}" if tone else ""
            cells.append(f'<span class="{klass}">{esc(row["cells"][index])}</span>')

        me = " me" if mine and row["nick"] == mine else ""
        out.append(f'<div class="ar-tr{me}" style="{tpl}">{"".join(cells)}</div>')

    return f'<div class="ar-panel"><div class="ar-ph"><b>{esc(title)}</b><span>{esc(sub)}</span></div>{"".join(out)}</div>'


def stat_cards(cards):
    """cards: [{'k','v','unit','s'}]"""

    out = []

    for c in cards:
        unit = f"<i>{esc(c['unit'])}</i>" if c.get("unit") else ""
        sub = f'<div class="s">{esc(c["s"])}</div>' if c.get("s") else ""
        out.append(f'<div class="ar-st"><div class="k">{esc(c["k"])}</div><div class="v">{esc(c["v"])}{unit}</div>{sub}</div>')

    return f'<div class="ar-stats">{"".join(out)}</div>'


def section(text, icon="●"):
    return f'<div class="ar-sec"><i>{esc(icon)}</i>{esc(text)}</div>'


def data_table(headers, rows, widths=""):
    """일반 표: 첫 칸은 왼쪽, 나머지는 가운데 정렬."""

    n = len(headers)
    tpl = f"grid-template-columns:{widths or ' '.join(['minmax(0,1fr)'] * n)}"

    def line(cells, klass=""):
        inner = "".join(
            f'<span class="{"" if i == 0 else "c"}">{esc(c)}</span>' for i, c in enumerate(cells)
        )

        return f'<div class="ar-dt {klass}" style="{tpl}">{inner}</div>'

    body = "".join(line(r) for r in rows)

    return f'<div class="ar-panel" style="padding:10px 10px 6px;margin:8px 0">{line(headers, "h")}{body}</div>'


def season_banner(season, names, meta, note, joint=False):
    """시즌 MVP 배너. season: '11월 시즌', names: 닉네임 목록(동점이면 여러 명)."""

    who = " · ".join(esc(n) for n in names)
    many = " many" if len(names) > 1 or len(" ".join(names)) > 8 else ""
    mvp = "MVP (공동)" if joint else "MVP"

    return (
        '<div class="ar-ban"><div class="tr"></div><div class="bd">'
        '<div class="top"><span class="wr"><b>MTNW</b><u>AWARD</u></span>'
        f'<span class="yr">{esc(season)}</span><span class="mv">{esc(mvp)}</span></div>'
        f'<div class="nm{many}">{who}</div>'
        + (f'<div class="mt">{esc(meta)}</div>' if meta else "")
        + f'<div class="nt">{esc(note)}</div></div></div>'
    )


def bars(items):
    """items: [{'label','value'(0~1),'tone'('g'|'y'|'o'|''),'tip'}] 막대 그래프."""

    out = []

    for it in items:
        height = max(0, min(1, it["value"])) * 100
        tip = esc(it.get("tip", ""))
        out.append(
            f'<div class="ar-bar" title="{tip}"><i class="{it.get("tone", "")}" style="height:{height:.0f}%"></i>'
            f'<span>{esc(it["label"])}</span></div>'
        )

    return f'<div class="ar-bars">{"".join(out)}</div>'


def badges(items):
    return f'<div class="ar-badges">{"".join(f"<span class=ar-badge>{esc(t)}</span>" for t in items)}</div>'
