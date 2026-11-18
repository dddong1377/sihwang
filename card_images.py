"""시황글을 720×960 카드 이미지(PNG)로 만들어 내려받는 버튼.

- 글의 '### 제목' 단위로 카드를 만들고, 짧은 항목은 한 장에 함께 담습니다.
- 이미지는 사용자의 브라우저에서 그려지므로 서버 부담이 없습니다.
- 여러 장은 ZIP 한 파일로 내려받습니다.
"""

import html
import json
import re

import streamlit.components.v1 as components

from kakao_text import KEYWORD_COUNT, _pick_keywords

HTML2CANVAS = "https://cdnjs.cloudflare.com/ajax/libs/html2canvas/1.4.1/html2canvas.min.js"
JSZIP = "https://cdnjs.cloudflare.com/ajax/libs/jszip/3.10.1/jszip.min.js"


def _inline(text):
    safe = html.escape(str(text or ""))
    return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", safe)


def _plain(text):
    return re.sub(r"[*`]", "", str(text or "")).strip()


def _split_row(line):
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _is_sep(cells):
    return bool(cells) and all(
        re.fullmatch(r":?-+:?", c.replace(" ", "")) for c in cells if c
    ) and any(cells)


NUMERIC = re.compile(r"^[\s\d,.+\-%원🔺🔻➖▲▼]+$")


def _table_html(rows):
    header = [_plain(c) for c in rows[0]]
    bold_first = bool(header) and header[0] in ("지수", "구분", "종목", "순위")
    bold_name = header[:2] == ["순위", "종목"]

    out = ["<table>"]
    out.append("<tr>" + "".join(f"<th>{_inline(c)}</th>" for c in rows[0]) + "</tr>")

    for row in rows[1:]:
        cells = []

        for i, cell in enumerate(row):
            text = str(cell)
            numeric = bool(NUMERIC.match(text.replace("*", ""))) and text.strip() != ""
            cls = []

            if numeric:
                cls.append("num")

            if (bold_first and i == 0 and not bold_name) or (bold_name and i == 1):
                cls.append("name")

            c = f" class='{' '.join(cls)}'" if cls else ""
            cells.append(f"<td{c}>{_inline(text)}</td>")

        out.append("<tr>" + "".join(cells) + "</tr>")

    out.append("</table>")

    return "".join(out)


LABEL = re.compile(r"^((?:📌|⏰|🔥|⚠️|📍)\s*)?([^:：\n]{1,12})[:：]\s*(.*)$")
DATE = re.compile(r"^(\d{1,2}/\d{1,2}\([월화수목금토일]\))\s*(.*)$")


def _bullet_html(text):
    text = text.strip()

    found = DATE.match(_plain(text))

    if found:
        return (
            f"<li><span class='date'>{html.escape(found.group(1))}</span> "
            f"{_inline(found.group(2))}</li>"
        )

    found = LABEL.match(text.replace("**", ""))

    if found and len(found.group(2)) <= 10:
        icon = found.group(1) or ""
        label = found.group(2)
        rest = found.group(3)
        red = "관련 종목" in label or "체크" in label or "주의" in label
        cls = "lab red" if red else "lab"

        return (
            f"<li><span class='{cls}'>{html.escape(icon + label)}:</span> "
            f"{_inline(rest)}</li>"
        )

    return f"<li>{_inline(text)}</li>"


def parse_sections(markdown):
    """글을 표지(제목·한 줄 요약)와 '###' 단위 섹션으로 나눕니다."""

    lines = str(markdown or "").splitlines()

    cover_title = ""
    cover_lines = []
    sections = []
    current = None

    for raw in lines:
        stripped = raw.strip()

        if stripped.startswith("###"):
            current = {"title": _plain(stripped.lstrip("#")), "lines": []}
            sections.append(current)
            continue

        if stripped.startswith("#"):
            if not cover_title:
                cover_title = _plain(stripped.lstrip("#"))

            continue

        if current is None:
            cover_lines.append(raw)
        else:
            current["lines"].append(raw)

    summary = ""

    for raw in cover_lines:
        text = raw.strip().lstrip(">").strip()

        if text:
            summary += (" " if summary else "") + text

    return cover_title, summary, sections


def blocks_html(lines):
    """섹션 본문을 블록(표·이슈·목록·문단) HTML 목록으로 만듭니다."""

    blocks = []
    table = []
    bullets = []
    issue = None  # [제목 html, [li…]]

    def flush_table():
        if table:
            blocks.append(f"<div class='blk'>{_table_html(list(table))}</div>")
            table.clear()

    def flush_bullets():
        nonlocal bullets

        if not bullets:
            return

        ul = "<ul>" + "".join(bullets) + "</ul>"

        if issue is not None:
            issue[1].extend(bullets)
        else:
            blocks.append(f"<div class='blk'>{ul}</div>")

        bullets = []

    def flush_issue():
        nonlocal issue

        flush_bullets()

        if issue is not None:
            body = "<ul>" + "".join(issue[1]) + "</ul>" if issue[1] else ""
            blocks.append(
                f"<div class='blk issue'><div class='pill'>{issue[0]}</div>{body}</div>"
            )
            issue = None

    for raw in lines:
        stripped = raw.strip()

        if stripped.startswith("|"):
            flush_bullets()
            cells = _split_row(stripped)

            if not _is_sep(cells):
                table.append(cells)

            continue

        flush_table()

        if not stripped:
            continue

        if stripped.startswith("**") and not stripped.startswith("**한"):
            flush_issue()
            issue = [_inline(stripped), []]
            continue

        if stripped.startswith(("- ", "* ")):
            bullets.append(_bullet_html(stripped[2:]))
            continue

        flush_bullets()

        cls = "note" if stripped.startswith("※") else "p"

        if issue is not None and cls == "p":
            flush_issue()

        blocks.append(f"<div class='blk {cls}'>{_inline(stripped)}</div>")

    flush_table()
    flush_issue()

    return blocks


CSS = """
*{box-sizing:border-box;margin:0;padding:0}
body{background:transparent;font-family:'Pretendard','Malgun Gothic','Apple SD Gothic Neo','Noto Sans KR','Noto Sans CJK KR',sans-serif;color:#111}
.bar{display:flex;gap:8px;align-items:center;flex-wrap:wrap;font-size:14px}
.bar button{padding:6px 12px;cursor:pointer}
#stage{position:absolute;left:-10000px;top:0}
.page{width:720px;height:960px;background:#F3F5FA;padding:36px 44px;position:relative;overflow:hidden}
.card{background:#fff;border-radius:26px;padding:28px 30px 26px;box-shadow:0 6px 22px rgba(30,40,70,.10)}
.sec+.sec{margin-top:26px;padding-top:26px;border-top:2px solid #EEF0F5}
.sec h2{font-size:36px;font-weight:800;line-height:1.25;margin-bottom:14px;letter-spacing:-.5px}
.sec.hasissue h2{padding-bottom:10px;border-bottom:3px solid #E5242B;margin-bottom:16px}
.cover .ttl{font-size:25px;font-weight:600;line-height:1.35;color:#6b7280;margin-bottom:22px}
.cover h2{font-size:40px;margin-bottom:22px}
.cover .sum{font-size:34px;font-weight:800;line-height:1.5;color:#111;word-break:keep-all}
.sec.plain li{font-size:25px;line-height:1.45;margin-bottom:10px}
.blk{margin-bottom:14px}
.blk:last-child{margin-bottom:0}
.p{font-size:23px;line-height:1.55;word-break:keep-all}
.note{font-size:17px;line-height:1.5;color:#6b7280}
table{width:100%;border-collapse:collapse;font-size:26px}
th,td{border:1.5px solid #D5D9E2;padding:.42em .55em;text-align:left;line-height:1.3;vertical-align:middle;white-space:nowrap}
th{background:#F7F8FB;font-weight:700}
td.name{font-weight:800}
ul{list-style:none}
li{position:relative;padding-left:24px;font-size:22px;line-height:1.42;margin-bottom:6px;word-break:keep-all}
li:before{content:'';position:absolute;left:5px;top:.62em;width:9px;height:9px;border-radius:50%;background:#111}
.lab{font-weight:800}
.red{color:#C81E1E}
.date{font-weight:800;color:#EA6A00}
.issue{padding-bottom:10px;margin-bottom:12px;border-bottom:1.5px solid #E6E8EE}
.issue:last-child{border-bottom:0;padding-bottom:0;margin-bottom:0}
.pill{display:inline-block;background:#FDE9EA;border-radius:14px;padding:6px 14px;font-size:27px;font-weight:800;line-height:1.3;margin-bottom:8px;word-break:keep-all}
.pill b{font-weight:800}
.cont{font-size:20px;font-weight:600;color:#6b7280}
#prev{display:flex;gap:10px;overflow-x:auto;margin-top:10px;padding-bottom:6px}
#prev img{width:180px;height:240px;border:1px solid #ccc;border-radius:6px;flex:0 0 auto}
"""

JS = r"""
const D = __DATA__;
const stage = document.getElementById('stage');
const statusEl = document.getElementById('status');
const PAGE_H = 960, MAXCARD = 960 - 36 - 36;

function nowKST() {
  return new Date().toLocaleTimeString('en-GB', {timeZone:'Asia/Seoul', hour:'2-digit', minute:'2-digit', hourCycle:'h23'});
}
function stamp(t) { return D.token ? String(t).split(D.token).join(nowKST()) : t; }

function newPage() {
  const page = document.createElement('div'); page.className = 'page';
  const card = document.createElement('div'); card.className = 'card';
  page.appendChild(card); stage.appendChild(page);
  return {page, card};
}
function secNode(title, blocks, extraClass, cont) {
  const s = document.createElement('div');
  s.className = 'sec' + (extraClass || '');
  if (title) {
    const h = document.createElement('h2');
    h.innerHTML = stamp(title) + (cont ? ' <span class="cont">(이어서)</span>' : '');
    s.appendChild(h);
  }
  blocks.forEach(b => { s.insertAdjacentHTML('beforeend', stamp(b)); });
  return s;
}
function fitTables(root) {
  root.querySelectorAll('table').forEach(t => {
    const box = t.parentElement;
    const maxW = box.clientWidth;
    let fs = 26;
    t.style.fontSize = fs + 'px';
    while (t.offsetWidth > maxW + 0.5 && fs > 15) { fs -= 0.5; t.style.fontSize = fs + 'px'; }
    // 그래도 넘치면 가장 긴 칸의 글을 …로 줄여 한 줄을 지킵니다
    let guard = 0;
    while (t.offsetWidth > maxW + 0.5 && guard++ < 200) {
      let best = null;
      t.querySelectorAll('td').forEach(td => {
        if (!best || td.textContent.length > best.textContent.length) best = td;
      });
      if (!best || best.textContent.length < 4) break;
      best.textContent = best.textContent.slice(0, -2).trimEnd() + '…';
    }
  });
}
function overflows(cur) { return cur.card.getBoundingClientRect().height > MAXCARD; }

function buildPages() {
  stage.innerHTML = '';
  const pages = [];
  let cur = newPage(); let firstTitle = '';
  const names = [];

  function closePage() {
    pages.push(cur.page);
    names.push(cur.name || ('p' + pages.length));
    cur = newPage();
  }
  function place(node, name) {
    cur.card.appendChild(node);
    if (!cur.name) cur.name = name;
    return true;
  }

  const items = [];
  if (D.cover.title || D.cover.summary) {
    const cv = document.createElement('div');
    cv.className = 'sec cover';
    cv.innerHTML = (D.cover.title ? '<div class="ttl">' + stamp(D.cover.title) + '</div>' : '') +
                   (D.cover.summary ? '<h2>📌 한 줄 요약</h2><div class="sum">' + stamp(D.cover.summary) + '</div>' : '');
    items.push({node: cv, name: '한줄요약', blocks: null, cover: true});
  }
  D.sections.forEach(sec => {
    items.push({node: secNode(sec.title, sec.blocks, sec.hasIssue ? ' hasissue' : ' plain'), name: sec.title, sec});
  });

  items.forEach(item => {
    if (cur.card.children.length && (cur.cover || item.cover)) closePage();
    cur.card.appendChild(item.node);
    fitTables(item.node);
    if (item.cover) { cur.cover = true; cur.name = item.name; return; }
    if (!overflows(cur)) { if (!cur.name) cur.name = item.name; return; }

    cur.card.removeChild(item.node);
    if (cur.card.children.length) closePage();

    cur.card.appendChild(item.node);
    fitTables(item.node);
    if (!overflows(cur) || !item.sec) { if (!cur.name) cur.name = item.name; return; }

    // 한 장보다 긴 섹션: 블록 단위로 나눠 여러 장에 담기
    cur.card.removeChild(item.node);
    let first = true;
    let part = secNode(item.sec.title, [], item.sec.hasIssue ? ' hasissue' : ' plain', false);
    cur.card.appendChild(part);
    cur.name = cur.name || item.name;

    item.sec.blocks.forEach(b => {
      part.insertAdjacentHTML('beforeend', stamp(b));
      fitTables(part);
      if (overflows(cur) && part.querySelectorAll('.blk').length > 1) {
        part.removeChild(part.lastElementChild);
        closePage();
        first = false;
        part = secNode(item.sec.title, [], item.sec.hasIssue ? ' hasissue' : ' plain', true);
        cur.card.appendChild(part);
        cur.name = item.name + '_이어서';
        part.insertAdjacentHTML('beforeend', stamp(b));
        fitTables(part);
      }
    });
  });

  if (cur.card.children.length) { pages.push(cur.page); names.push(cur.name || ('p' + pages.length)); }
  else stage.removeChild(cur.page);

  return {pages, names};
}
window.__MTNW = {buildPages};

function safeName(t) {
  return String(t).replace(/[\\/:*?"<>|#]/g, '').replace(/[\s·.,()]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 24) || 'card';
}
function download(blob, name) {
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob); a.download = name;
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 4000);
}

async function make() {
  const btn = document.getElementById('mk');
  try {
    if (!window.html2canvas || !window.JSZip) throw new Error('이미지 라이브러리를 불러오지 못했습니다 (인터넷 연결 확인)');
    btn.disabled = true; statusEl.textContent = ' 이미지를 만드는 중...';
    if (document.fonts && document.fonts.ready) await document.fonts.ready;

    const {pages, names} = buildPages();
    const zip = new JSZip();
    const prev = document.getElementById('prev'); prev.innerHTML = '';

    for (let i = 0; i < pages.length; i++) {
      statusEl.textContent = ` 이미지 ${i + 1}/${pages.length} 만드는 중...`;
      const canvas = await html2canvas(pages[i], {width: 720, height: 960, scale: D.scale, backgroundColor: '#F3F5FA', useCORS: true, logging: false});
      const blob = await new Promise(r => canvas.toBlob(r, 'image/png'));
      const fname = String(i + 1).padStart(2, '0') + '_' + safeName(names[i]) + '.png';
      zip.file(fname, blob);
      const img = document.createElement('img'); img.src = URL.createObjectURL(blob); img.title = fname;
      prev.appendChild(img);
    }

    stage.innerHTML = '';
    const out = await zip.generateAsync({type: 'blob'});
    download(out, D.prefix + '_카드이미지.zip');
    statusEl.textContent = ` 완료 · ${pages.length}장 (ZIP으로 내려받았습니다)`;
  } catch (e) {
    statusEl.textContent = ' 실패: ' + (e && e.message ? e.message : e);
  } finally {
    btn.disabled = false;
  }
}
document.getElementById('mk').onclick = make;
"""


def render_card_images(markdown, prefix, now_token="", scale=1):
    """'🖼️ 카드 이미지 만들기' 버튼과 미리보기를 표시합니다.

    prefix: 파일 이름 머리말(예: '장전시황'). now_token: 오후 시황의 시각 자리표시자."""

    title, summary, sections = parse_sections(markdown)

    data = {
        "prefix": prefix,
        "token": now_token or "",
        "scale": scale,
        "cover": {
            "title": html.escape(title),
            "summary": _inline(
                re.sub(r"^\s*📌?\s*\**한 줄 요약\**\s*[:：]?\s*", "", summary)
            ),
        },
        "sections": [],
    }

    for section in sections:
        # 핵심 키워드는 카톡용과 같이 5개만 (본문에 많이 나온 순으로 고름)
        if "핵심 키워드" in section["title"]:
            section["title"] = re.sub(
                r"핵심 키워드\s*\d+", f"핵심 키워드 {KEYWORD_COUNT}", section["title"]
            )
            kept = []
            done = False

            for line in section["lines"]:
                if not done and line.strip():
                    line = ", ".join(_pick_keywords(line.strip(), str(markdown).replace(line.strip(), "")))
                    done = True

                kept.append(line)

            section["lines"] = kept

        blocks = blocks_html(section["lines"])

        if not blocks:
            continue

        data["sections"].append({
            "title": html.escape(section["title"]),
            "blocks": blocks,
            "hasIssue": any("class='blk issue'" in b for b in blocks),
        })

    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")

    page = f"""
    <style>{CSS}</style>
    <div class="bar">
      <button id="mk">🖼️ 카드 이미지 만들기 (720×960)</button>
      <span id="status"></span>
    </div>
    <div id="prev"></div>
    <div id="stage"></div>
    <script src="{HTML2CANVAS}"></script>
    <script src="{JSZIP}"></script>
    <script>{JS.replace('__DATA__', payload)}</script>
    """

    components.html(page, height=330, scrolling=True)
