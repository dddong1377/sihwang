"""게시판 줄 간격 시험용 페이지 (임시). 시험이 끝나면 app.py의 메뉴 항목과 이 파일을 지우면 됩니다.

같은 글을 4가지 HTML 구조로 복사해, 게시판 편집기에서 PC 줄 간격이 가장 촘촘한 방식을 고릅니다.
"""

import html
import json
import re
import unicodedata

import streamlit as st
import streamlit.components.v1 as components

st.set_page_config(page_title="복사 방식 시험", layout="wide")

if not st.session_state.get("pw_ok"):
    st.info("다른 페이지에서 로그인한 뒤 이 페이지를 열어 주세요.")
    st.stop()

SAMPLE = """### 🔻 급락 특징주 TOP 5 (시총 100위 이내)

| 종목 | 현재가 | 등락률 | 관련 테마 |
|---|---|---|---|
| **심텍** | 107,500원 | **-13.86%** | HBM·반도체 |
| **주성엔지니어링** | 152,950원 | **-11.33%** | HBM·반도체 |
| **SK스퀘어** | 683,000원 | **-10.36%** | HBM·반도체 |

### 🔥 오늘의 주요 경제 이슈

**1. 🔺 젠슨 황, 엔비디아 복귀 가능할까…월가 전망 엇갈려**
- 내용: 황 전 CEO가 측근들과 복귀 가능성을 논의한 것으로 알려진 가운데
  법적 문제를 해결한다면 복귀할 수 있다는 전망과 불가능하다는 회의론이 맞서고 있다.
- 📌 관련 종목: 엔비디아, SK하이닉스

**2. 🔻 파인엠텍, 3분기 영업익 215억 '깜짝 실적'…증권가 눈높이 넘었다**
- 내용: 폴더블 디스플레이 및 ESS 부품 전문기업 파인엠텍이 3·4분기 전망치를 웃도는 실적을 기록했다.
- 📌 관련 종목: 파인엠텍

※ 이 글은 시험용 예시입니다."""

MODES = {
    "A": "A. 현재 방식 (문단 + 줄바꿈)",
    "B": "B. 줄마다 <p> (여백 0)",
    "C": "C. 줄마다 <div> (여백 0)",
    "D": "D. 한 덩어리 + 줄바꿈만",
}

LINE = "margin:0;padding:0;line-height:1.6"


def inline(text):
    safe = html.escape(text)
    return re.sub(r"\*\*(.*?)\*\*", r"<b>\1</b>", safe)


def width(text):
    return sum(
        2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
        for ch in text.replace("*", "")
    )


def table_html(rows):
    columns = max(len(row) for row in rows)
    weights = [
        max(max(width(r[i]) if i < len(r) else 0 for r in rows), 6) + 3
        for i in range(columns)
    ]
    total = sum(weights)
    widths = [round(w * 100 / total) for w in weights]
    widths[-1] = 100 - sum(widths[:-1])

    out = [
        "<table border='1' cellpadding='4' cellspacing='0' "
        "style='border-collapse:collapse;margin:6px 0;width:100%'>"
    ]

    for n, row in enumerate(rows):
        cells = []

        for i in range(columns):
            cell = row[i] if i < len(row) else ""
            numeric = bool(re.fullmatch(r"[\s\d,.+\-%원🔺🔻➖▲▼]+", cell.replace("*", "")))
            wrap = "white-space:nowrap" if numeric else "word-break:break-all;overflow-wrap:anywhere"

            if n == 0:
                cells.append(
                    f"<th width='{widths[i]}%' style='width:{widths[i]}%;"
                    f"background-color:#F2F3F5;font-weight:normal;text-align:left;{wrap}'>"
                    f"{inline(cell)}</th>"
                )
            else:
                cells.append(f"<td style='{wrap}'>{inline(cell)}</td>")

        out.append("<tr>" + "".join(cells) + "</tr>")

    out.append("</table>")

    return "".join(out)


def convert(markdown, mode):
    out = []
    rows = []
    para = []  # 모드 A·D 에서 이어지는 글줄

    def line_html(text):
        if mode == "B":
            return f"<p style='{LINE}'>{text}</p>"

        return f"<div style='{LINE}'>{text}</div>"

    def flush_para():
        if not para:
            return

        if mode == "A":
            out.append("<p style='margin:0 0 10px'>" + "<br>".join(para) + "</p>")

        elif mode == "D":
            out.append(f"<div style='{LINE}'>" + "<br>".join(para) + "</div>")

        para.clear()

    def flush_table():
        if rows:
            out.append(table_html(rows))
            rows.clear()

    for raw in markdown.splitlines():
        stripped = raw.strip()

        if stripped.startswith("|"):
            flush_para()
            cells = [c.strip() for c in stripped.split("|")[1:-1]]

            if all(re.fullmatch(r":?-+:?", c) for c in cells):
                continue

            rows.append(cells)
            continue

        flush_table()

        if stripped.startswith("### "):
            flush_para()
            margin = "18px 0 6px" if mode == "A" else "14px 0 4px"
            out.append(f"<h3 style='margin:{margin}'>{inline(stripped[4:])}</h3>")
            continue

        if not stripped:
            flush_para()
            continue

        indent = len(raw) - len(raw.lstrip(" "))
        text = "&nbsp;" * indent + inline(stripped)

        if mode in ("A", "D"):
            para.append(text)
        else:
            out.append(line_html(text))

    flush_para()
    flush_table()

    return "\n".join(out)


st.title("복사 방식 시험 (임시)")

st.caption(
    "같은 글을 4가지 구조로 복사합니다. 각 버튼을 눌러 게시판 편집기(PC)에 붙여넣고, "
    "줄 간격이 가장 촘촘한 방식을 알려주세요. 시험이 끝나면 이 페이지는 지웁니다."
)

text = st.text_area("시험할 글 (다른 글을 붙여넣어 시험해도 됩니다)", value=SAMPLE, height=300)

payload = json.dumps(
    {
        "md": text,
        "labels": MODES,
        "html": {mode: convert(text, mode) for mode in MODES},
    },
    ensure_ascii=False,
).replace("</", "<\\/")

page = f"""
<div id="box" style="display:flex;gap:8px;flex-wrap:wrap;align-items:center"></div>
<div id="status" style="margin-top:8px;font-family:sans-serif"></div>
<script>
const P = {payload};
const box = document.getElementById('box');
const s = document.getElementById('status');

Object.keys(P.labels).forEach((mode) => {{
    const b = document.createElement('button');
    b.textContent = '복사 ' + P.labels[mode];
    b.style.padding = '8px 12px';
    b.onclick = async () => {{
        try {{
            await navigator.clipboard.write([
                new ClipboardItem({{
                    'text/html': new Blob([P.html[mode]], {{type: 'text/html'}}),
                    'text/plain': new Blob([P.md], {{type: 'text/plain'}})
                }})
            ]);
            s.textContent = mode + ' 방식으로 복사했습니다. 게시판에 붙여넣어 보세요.';
        }} catch (e) {{
            s.textContent = '복사 실패';
        }}
    }};
    box.appendChild(b);
}});
</script>
"""

components.html(page, height=110)

with st.expander("각 방식의 HTML 구조 보기", expanded=False):
    for mode, label in MODES.items():
        st.markdown(f"**{label}**")
        st.code(convert(text, mode)[:700], language="html")
