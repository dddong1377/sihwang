"""시황글을 사이트에서 보는 신문 '지면' HTML 로 만듭니다 (paper.py 페이지가 사용).

paper_model.build_model() 이 나눈 재료를 그대로 씁니다. 이미지(paper_images.py)와 같은 재료·같은 색 규칙입니다.
- 상승은 빨강, 하락은 파랑 (한국 관례)
- 모든 글자는 HTML 이스케이프하고, $ 는 수식으로 읽히지 않도록 바꿉니다.
- st.markdown(unsafe_allow_html=True) 로 넣어도 깨지지 않게 '빈 줄 없는 한 줄'로 만듭니다.
"""

import html
import re

SCOPE = "mtnwp"

CSS = """
.mtnwp{--paper:#F5F0E6;--ink:#16130E;--muted:#6B6254;--rule:#CFC6B4;--soft:#EDE6D6;--acc:#E8590C;--up:#D6282B;--down:#1D5FD0;
max-width:1080px;margin:0 auto;padding:28px 36px 22px;background:var(--paper);color:var(--ink);border:1px solid var(--rule);border-radius:6px;
font-family:'Noto Sans KR','Pretendard','Apple SD Gothic Neo','Malgun Gothic',sans-serif;line-height:1.55;box-shadow:0 2px 14px rgba(40,30,10,.10)}
.mtnwp *{box-sizing:border-box}
.mtnwp .mast{display:flex;align-items:flex-end;justify-content:space-between;gap:12px;flex-wrap:wrap;padding-bottom:10px;border-bottom:4px solid var(--ink);position:relative}
.mtnwp .mast:after{content:"";position:absolute;left:0;right:0;bottom:-9px;border-bottom:1.5px solid var(--ink)}
.mtnwp .logo{font-size:clamp(34px,6vw,56px);font-weight:900;letter-spacing:-.02em;line-height:1.05}
.mtnwp .logo i{display:inline-block;width:.16em;height:.16em;border-radius:50%;background:var(--acc);margin-left:.08em}
.mtnwp .mright{text-align:right}
.mtnwp .badge{display:inline-block;background:var(--ink);color:var(--paper);font-weight:800;font-size:15px;padding:5px 12px;border-radius:4px}
.mtnwp .date{margin-top:6px;font-size:15px;color:var(--muted)}
.mtnwp .meta{margin-top:18px;font-size:13px;color:var(--muted)}
.mtnwp .kicker{margin-top:14px;font-size:14px;font-weight:800;color:var(--acc)}
.mtnwp h1{margin:6px 0 14px;font-size:clamp(26px,4.6vw,46px);line-height:1.2;font-weight:900;letter-spacing:-.02em;word-break:keep-all}
.mtnwp .rule{border:0;border-top:1.5px solid var(--ink);margin:0 0 18px}
.mtnwp .cols{display:grid;grid-template-columns:minmax(0,1fr) 290px;gap:0 26px;align-items:start}
.mtnwp .cols.one{grid-template-columns:minmax(0,1fr)}
.mtnwp .main{min-width:0}
.mtnwp .rail{border-left:1.5px solid #D8CFBC;padding-left:22px;min-width:0}
.mtnwp .rail h3{margin:0;font-size:18px;font-weight:800}
.mtnwp .rail .sub{font-size:12px;color:var(--muted);margin:2px 0 8px}
.mtnwp .idx{display:flex;justify-content:space-between;align-items:baseline;gap:8px;padding:9px 0 0}
.mtnwp .idx b{font-size:16px;font-weight:800}
.mtnwp .idx em{font-style:normal;font-weight:800;font-size:16px;font-variant-numeric:tabular-nums}
.mtnwp .idxv{font-size:13px;color:var(--muted);padding-bottom:8px;border-bottom:1px solid var(--rule);font-variant-numeric:tabular-nums}
.mtnwp .grp{border-top:2px solid var(--ink);margin-top:6px}
.mtnwp .up{color:var(--up)}.mtnwp .down{color:var(--down)}
.mtnwp .tag{font-size:13px;font-weight:800;color:var(--acc)}
.mtnwp .lead h2{margin:4px 0 8px;font-size:clamp(22px,3vw,30px);line-height:1.3;font-weight:800;word-break:keep-all}
.mtnwp .lead p{margin:0 0 8px;font-size:16.5px;color:#2B2620}
.mtnwp .rel{font-size:15px}.mtnwp .rel b{color:var(--acc);margin-right:8px;font-size:13px}
.mtnwp .sec{display:flex;align-items:center;gap:10px;margin:26px 0 10px;padding-bottom:5px;border-bottom:1px solid var(--rule)}
.mtnwp .sec:before{content:"";width:6px;height:22px;background:var(--acc);display:block}
.mtnwp .sec h3{margin:0;font-size:19px;font-weight:800}
.mtnwp .issue{display:flex;gap:14px;padding:14px 0;border-bottom:1px solid var(--rule)}
.mtnwp .issue:last-child{border-bottom:0}
.mtnwp .num{flex:0 0 36px;height:36px;border-radius:6px;background:var(--ink);color:var(--paper);display:flex;align-items:center;justify-content:center;font-weight:800;font-size:19px}
.mtnwp .ibody{min-width:0}
.mtnwp .ibody h4{margin:0 0 4px;font-size:19px;line-height:1.35;font-weight:800;word-break:keep-all}
.mtnwp .ibody p{margin:0 0 6px;font-size:15.5px;color:#2B2620}
.mtnwp .chip{display:inline-block;background:var(--soft);color:var(--acc);font-weight:800;font-size:12.5px;padding:1px 8px;border-radius:4px;margin-right:8px}
.mtnwp .ln{font-size:15px;margin:3px 0}
.mtnwp .grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:0 30px}
.mtnwp .grid .full{grid-column:1/-1}
.mtnwp table{width:100%;border-collapse:collapse;font-size:15px;font-variant-numeric:tabular-nums}
.mtnwp thead th{background:var(--soft);border-top:2px solid var(--ink);border-bottom:1.5px solid var(--ink);padding:7px 8px;font-weight:800;text-align:center;white-space:nowrap}
.mtnwp td{padding:7px 8px;border-bottom:1px solid var(--rule)}
.mtnwp tbody tr:last-child td{border-bottom:2px solid var(--ink)}
.mtnwp td.r{text-align:right}.mtnwp td.c{text-align:center}.mtnwp td.n{font-weight:800}
.mtnwp .tw{overflow-x:auto}
.mtnwp .note{font-size:13px;color:var(--muted);margin:6px 0 0}
.mtnwp ul.rows{list-style:none;margin:0;padding:0}
.mtnwp ul.rows li{position:relative;padding:4px 0 4px 18px;font-size:15.5px}
.mtnwp ul.rows li:before{content:"";position:absolute;left:2px;top:.85em;width:7px;height:7px;border-radius:50%;background:var(--acc)}
.mtnwp ul.rows li b{font-weight:800}.mtnwp ul.rows li .dt{color:var(--acc);font-weight:800;margin-right:8px}
.mtnwp ul.rows li span.d{color:var(--muted)}
.mtnwp .pills{display:flex;flex-wrap:wrap;gap:8px}
.mtnwp .pill{border:1.6px solid var(--ink);border-radius:999px;padding:4px 14px;font-weight:800;font-size:15px}
.mtnwp .caution{background:var(--soft);border-left:5px solid var(--acc);border-radius:6px;padding:12px 16px;font-size:15.5px;margin-top:22px}
.mtnwp .flow{font-size:16.5px;margin:0}
.mtnwp .foot{margin-top:26px;padding-top:8px;border-top:1px solid var(--ink);display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap;font-size:12.5px;color:var(--muted)}
@media (max-width:820px){
.mtnwp{padding:20px 16px 16px}
.mtnwp .cols{grid-template-columns:1fr}
.mtnwp .rail{border-left:0;padding-left:0;margin-top:22px;border-top:1.5px solid var(--ink);padding-top:12px}
.mtnwp .grid{grid-template-columns:1fr}
.mtnwp .mright{text-align:left}
}
"""

_ARROWS = {"🔺": "▲", "🔻": "▼", "➖": "–"}


def esc(value):
    """HTML 이스케이프 + $ 보호 (Streamlit 마크다운이 수식으로 읽지 않게)"""

    return html.escape(str(value if value is not None else ""), quote=True).replace("$", "&#36;")


def arrows(text):
    text = str(text or "")

    for emoji, arrow in _ARROWS.items():
        text = text.replace(emoji, arrow)

    return re.sub(r"\s+(?=[▲▼–])", " ", text)


def dir_class(text):
    text = str(text or "").strip()

    if re.match(r"^(▲|\+)", text):
        return "up"

    if re.match(r"^(▼|-|−)", text):
        return "down"

    return ""


def _sec(title):
    return f'<div class="sec"><h3>{esc(title)}</h3></div>'


def _dir_tag(direction):
    if direction == "up":
        return ' <span class="up">▲</span>'

    if direction == "down":
        return ' <span class="down">▼</span>'

    return ""


def _table(piece, full_title=None):
    header = piece["header"]
    align = piece.get("align") or []
    name_col = piece.get("name", 0)
    head = "".join(f"<th>{esc(arrows(h))}</th>" for h in header)
    body = []

    for row in piece["rows"]:
        cells = list(row[: len(header)]) + [""] * max(0, len(header) - len(row))
        tds = []

        for i, cell in enumerate(cells):
            text = arrows(cell)
            klass = []
            al = align[i] if i < len(align) else "left"

            if header[i].strip() in ("순위", "No", "#"):
                klass.append("c")
            elif al == "right":
                klass.append("r")
            elif al == "center":
                klass.append("c")

            if i == name_col:
                klass.append("n")

            tone = ""

            if re.search(r"등락|%", header[i]) or re.match(r"^[▲▼+\-−]\s*[+\-−]?\d", text):
                tone = dir_class(text)

            inner = f'<span class="{tone}"><b>{esc(text)}</b></span>' if tone else esc(text)
            tds.append(f'<td class="{" ".join(klass)}">{inner}</td>')

        body.append("<tr>" + "".join(tds) + "</tr>")

    extra = "".join(f'<p class="note">{esc(t)}</p>' for t in piece.get("extra", []))

    return (
        f'{_sec(full_title or piece["title"])}'
        f'<div class="tw"><table><thead><tr>{head}</tr></thead><tbody>{"".join(body)}</tbody></table></div>{extra}'
    )


def _rows(piece):
    items = []

    for row in piece["rows"]:
        date = row.get("date", "")
        head = row["head"].replace(date, "", 1).strip() if date else row["head"]
        detail = f' <span class="d">{esc(row["detail"])}</span>' if row.get("detail") else ""
        stamp = f'<span class="dt">{esc(date)}</span>' if date else ""
        items.append(f"<li>{stamp}<b>{esc(head)}</b>{detail}</li>")

    return f'{_sec(piece["title"])}<ul class="rows">{"".join(items)}</ul>'


def _pills(title, words, hash_=False):
    pills = "".join(f'<span class="pill">{"#" if hash_ else ""}{esc(w)}</span>' for w in words)

    return f'{_sec(title)}<div class="pills">{pills}</div>'


def _issue_item_html(item):
    label, text = item["label"], item["text"]

    if label == "내용":
        return f"<p>{esc(text)}</p>"

    chip = f'<span class="chip">{esc(label)}</span>' if label else ""

    return f'<div class="ln">{chip}{esc(text)}</div>'


def _issue_html(issue, lead=False):
    body = "".join(_issue_item_html(i) for i in issue["items"])

    return (
        '<div class="issue">'
        f'<div class="num">{esc(issue.get("num") or "•")}</div>'
        f'<div class="ibody"><h4>{esc(issue["title"])}{_dir_tag(issue["dir"])}</h4>{body}</div></div>'
    )


def _lead_html(issue):
    direction = {"up": '<span class="tag up">▲ 상승 이슈</span>', "down": '<span class="tag down">▼ 하락 이슈</span>'}.get(issue["dir"], "")
    parts = [f'<div class="tag">톱 이슈 {direction}</div>', f'<h2>{esc(issue["title"])}</h2>']

    for item in issue["items"]:
        if item["label"] == "내용":
            parts.append(f'<p>{esc(item["text"])}</p>')
        elif "종목" in item["label"]:
            parts.append(f'<div class="rel"><b>관련 종목</b>{esc(item["text"])}</div>')
        else:
            parts.append(_issue_item_html(item))

    return f'<div class="lead">{"".join(parts)}</div>'


def _rail_html(model):
    rows = model["indices"][:12]

    if not rows:
        return ""

    note = ("장중 기준 · " if model["kind"] == "aft" else "") + "등락률은 전일 대비"
    out = [f'<h3>{esc(model["rail_title"])}</h3><div class="sub">{esc(note)}</div>']
    last = ""

    for row in rows:
        tone = {"up": "up", "down": "down"}.get(row["dir"], "")
        grp = " grp" if last and row["group"] != last else ""
        last = row["group"]
        out.append(
            f'<div class="idx{grp}"><b>{esc(row["name"])}</b><em class="{tone}">{esc(arrows(row["change"]))}</em></div>'
            f'<div class="idxv">{esc(row["value"])}</div>'
        )

    return f'<aside class="rail">{"".join(out)}</aside>'


def build_paper_html(model, issue_no=None):
    """한 줄(빈 줄 없는) HTML 문자열. <style> 포함."""

    kicker = "주간 종합 · 이번 주의 한 줄" if model["kind"] == "week" else "1면 종합 · 오늘의 한 줄"
    meta = (f"제{issue_no}호 · " if issue_no else "") + f'{model["edition"]} 발행'
    if model.get("market_at"):
        meta += " · 국내 시장 조회 " + model["market_at"]

    issues = model["issues"]
    lead = issues[0] if issues else None
    rest = issues[1:]

    main = []

    if lead:
        main.append(_lead_html(lead))
    elif model["flow"]:
        main.append(f'<p class="flow">{esc(model["flow"])}</p>')

    if rest:
        main.append(_sec("이어지는 이슈") + "".join(_issue_html(i) for i in rest))

    rail = _rail_html(model)
    cols_class = "cols" if rail else "cols one"
    cols = f'<div class="{cols_class}"><div class="main">{"".join(main)}</div>{rail}</div>' if (main or rail) else ""

    wide = []

    for band in model["bands"]:
        wide.append(_table({"title": band["title"], "header": band["header"], "rows": band["rows"], "align": band["align"], "name": 0, "extra": []}))

    if model["flow"] and lead:
        wide.append(_sec("주간 흐름") + f'<p class="flow">{esc(model["flow"])}</p>')

    cells, tail = [], []

    for piece in model["pieces"]:
        kind = piece["type"]

        if kind == "table":
            cells.append(f'<div class="full">{_table(piece)}</div>')
        elif kind == "rows":
            cells.append(f"<div>{_rows(piece)}</div>")
        elif kind == "tags":
            cells.append(f'<div>{_pills(piece["title"], piece["tags"])}</div>')
        elif kind == "keywords":
            tail.append(f'<div class="full">{_pills(piece["title"], piece["words"], True)}</div>')
        elif kind == "caution":
            tail.append(f'<div class="full"><div class="caution"><b>{esc(piece["title"])}</b> · {esc(piece["text"])}</div></div>')

    grid = f'<div class="grid">{"".join(cells + tail)}</div>' if (cells or tail) else ""

    text = (
        f'<div class="mast"><div class="logo">MTNW 시황<i></i></div>'
        f'<div class="mright"><span class="badge">{esc(model["edition"])}</span><div class="date">{esc(model["date_text"])}</div></div></div>'
        f'<div class="meta">{esc(meta)}</div>'
        f'<div class="kicker">{esc(kicker)}</div>'
        f'<h1>{esc(model["headline"] or model["title"])}</h1><hr class="rule">'
        f'{cols}{"".join(wide)}{grid}'
        f'<div class="foot"><span>※ 참고 자료이며 투자 권유가 아닙니다</span><span>MTNW 시황 · {esc(model["edition"])}</span></div>'
    )

    # 줄바꿈·들여쓰기가 없어야 st.markdown 이 HTML 블록을 그대로 둡니다.
    css = re.sub(r"\s*\n\s*", "", CSS).strip()

    return f'<style>{css}</style><div class="{SCOPE}">{text}</div>'


FONT_LINK = (
    '<link rel="preconnect" href="https://fonts.googleapis.com">'
    '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@400;500;700;900&display=swap">'
)

RESIZE_JS = """
(function () {
  function fit() {
    try {
      var h = Math.ceil(document.documentElement.getBoundingClientRect().height);
      var f = window.frameElement;
      if (f && h > 200) { f.style.height = (h + 4) + 'px'; f.setAttribute('height', h + 4); }
    } catch (e) {}
  }
  window.addEventListener('load', fit);
  window.addEventListener('resize', fit);
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(fit);
  if (window.ResizeObserver) new ResizeObserver(fit).observe(document.body);
  setTimeout(fit, 300); setTimeout(fit, 1200);
})();
"""


def build_paper_document(model, issue_no=None):
    """components.html 에 넣는 완성형 문서 (iframe 안이라 Streamlit 스타일과 섞이지 않음)."""

    return (
        "<!doctype html><html lang='ko'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"{FONT_LINK}<style>html,body{{margin:0;padding:0;background:transparent}}body{{padding:2px}}</style></head>"
        f"<body>{build_paper_html(model, issue_no)}<script>{RESIZE_JS}</script></body></html>"
    )
