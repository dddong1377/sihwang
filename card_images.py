"""시황글을 720×960 카드 이미지(PNG)로 만들어 ZIP으로 내려받는 버튼.

- 글의 '### 제목' 단위로 카드를 만들고, 짧은 항목은 한 장에 함께 담습니다.
- 외부 라이브러리 없이 브라우저 캔버스로 직접 그립니다 (인터넷 연결 불필요).
- 표는 글자 크기를 자동으로 줄여 항상 한 줄로 맞춥니다.
"""

import json
import re

import streamlit.components.v1 as components

from kakao_text import KEYWORD_COUNT, _pick_keywords


# ---------------------------------------------------------------
# 1. 마크다운 → 구조 데이터
# ---------------------------------------------------------------

def _plain(text):
    return re.sub(r"[*`]", "", str(text or "")).strip()


def _runs(text):
    """'**굵게**' 표시를 [[글, 굵게여부], …] 로 나눕니다."""

    text = str(text or "").replace("`", "")
    parts = re.split(r"\*\*(.+?)\*\*", text)
    runs = []

    for index, part in enumerate(parts):
        if part:
            runs.append([part, index % 2 == 1])

    return runs or [["", False]]


def _split_row(line):
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _is_sep(cells):
    return bool(cells) and all(
        re.fullmatch(r":?-+:?", c.replace(" ", "")) for c in cells if c
    ) and any(cells)


LABEL = re.compile(r"^((?:📌|⏰|🔥|⚠️|📍)\s*)?([^:：\n]{1,12})[:：]\s*(.*)$")
DATE = re.compile(r"^(\d{1,2}/\d{1,2}\([월화수목금토일]\))\s*(.*)$")


def _item(text, level):
    text = text.strip()
    plain = _plain(text)

    found = DATE.match(plain)

    if found:
        return {"level": level, "date": found.group(1), "runs": _runs(found.group(2))}

    found = LABEL.match(text.replace("**", ""))

    if found and len(found.group(2)) <= 10:
        icon = (found.group(1) or "").strip()
        label = found.group(2)
        red = any(k in label for k in ("관련 종목", "체크", "주의"))

        return {
            "level": level,
            "label": (icon + " " if icon else "") + label + ":",
            "red": red,
            "runs": _runs(found.group(3)),
        }

    return {"level": level, "runs": _runs(text)}


def parse_markdown(markdown):
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
        elif re.match(r"^#{1,6}\s", stripped):
            if not cover_title:
                cover_title = _plain(stripped.lstrip("#"))
        elif current is None:
            cover_lines.append(raw)
        else:
            current["lines"].append(raw)

    summary = " ".join(
        raw.strip().lstrip(">").strip() for raw in cover_lines if raw.strip()
    )
    summary = re.sub(r"^\s*📌?\s*\**한 줄 요약\**\s*[:：]?\s*", "", summary)

    return cover_title, _plain(summary), sections


DIRECTIONS = ("🔺", "🔻", "➖")
GROUPS = [
    ("summary", ("지수 현황", "해외 지수", "지수 한눈에", "성적표")),
    ("watch", ("오늘 주목 종목", "강세·관심 테마")),
    ("end", ("체크 포인트", "일정", "핵심 키워드")),
]


def group_of(title):
    for name, keys in GROUPS:
        if any(k in title for k in keys):
            return name

    return ""


def _align(cells):
    out = []

    for c in cells:
        c = c.replace(" ", "")
        if c.startswith(":") and c.endswith(":"):
            out.append("center")
        elif c.endswith(":"):
            out.append("right")
        else:
            out.append("left")

    return out


def build_blocks(lines):
    blocks = []
    table = []
    aligns = []
    items = []
    issue = None

    def flush_table():
        if not table:
            return

        header = [_plain(c) for c in table[0]]
        name = -1

        if header[:2] == ["순위", "종목"]:
            name = 1
        elif header and header[0] in ("지수", "구분", "종목"):
            name = 0

        blocks.append({
            "type": "table",
            "header": header,
            "rows": [[_runs(c) for c in row] for row in table[1:]],
            "name": name,
            "align": list(aligns),
        })
        table.clear()
        aligns.clear()

    def flush_items():
        nonlocal items

        if not items:
            return

        if issue is not None:
            issue["items"].extend(items)
        else:
            blocks.append({"type": "list", "items": items})

        items = []

    def flush_issue():
        nonlocal issue

        flush_items()

        if issue is not None:
            blocks.append(issue)
            issue = None

    for raw in lines:
        stripped = raw.strip()
        indent = len(raw) - len(raw.lstrip(" "))

        if stripped.startswith("|"):
            flush_items()
            cells = _split_row(stripped)

            if _is_sep(cells):
                aligns[:] = _align(cells)
            else:
                table.append(cells)

            continue

        flush_table()

        if not stripped:
            continue

        if stripped.startswith("**") and not stripped.startswith("**한"):
            flush_issue()
            plain = _plain(stripped)
            circled = "①②③④⑤⑥⑦⑧⑨"
            found = re.match(r"^(\d+)\s*[.．)]\s*(.*)$", plain)
            num, title = "", plain

            if found:
                num, title = found.group(1), found.group(2)
            elif plain[:1] in circled:
                num, title = str(circled.index(plain[:1]) + 1), plain[1:].strip()

            direction = ""

            for d in DIRECTIONS:
                if title.startswith(d):
                    direction, title = d, title[len(d):].strip()
                    break

            title = title.replace("...", "…")
            issue = {"type": "issue", "num": num, "dir": direction, "title": title, "items": []}
            continue

        if stripped.startswith(("- ", "* ")):
            items.append(_item(stripped[2:], 1 if indent >= 2 else 0))
            continue

        # 들여쓴 이어지는 줄(줄바꿈된 문장)은 앞 항목에 붙입니다
        if items and indent >= 2:
            last = items[-1]
            last["runs"][-1][0] += " " + stripped
            continue

        flush_items()

        if issue is not None:
            flush_issue()

        kind = "note" if stripped.startswith("※") else "p"
        blocks.append({"type": kind, "runs": _runs(stripped)})

    flush_table()
    flush_issue()

    return blocks


# ---------------------------------------------------------------
# 2. 브라우저에서 그리는 코드
# ---------------------------------------------------------------

JS = r"""
const D = __DATA__;
const W = 720, H = 960, MX = 22, MY = 30, CPX = 32, CPT = 30, CPB = 30;
const MAXH = H - 2 * MY;              // 카드가 가질 수 있는 최대 높이
const CW = W - 2 * MX - 2 * CPX;      // 글이 들어가는 폭
const SEP = 40;                       // 같은 카드 안 항목 사이 간격
const FONT = "'Pretendard','Malgun Gothic','Apple SD Gothic Neo','Noto Sans KR','Noto Sans CJK KR','Segoe UI Emoji','Apple Color Emoji','Noto Color Emoji',sans-serif";
const mcv = document.createElement('canvas');
const mctx = mcv.getContext('2d');

function nowKST() {
  return new Date().toLocaleTimeString('en-GB', {timeZone: 'Asia/Seoul', hour: '2-digit', minute: '2-digit', hourCycle: 'h23'});
}
function stamp(t) { return D.token ? String(t).split(D.token).join(nowKST()) : String(t); }
function fnt(px, bold) { return (bold ? 'bold ' : '') + px + 'px ' + FONT; }
function tw(t, px, bold) { mctx.font = fnt(px, bold); return mctx.measureText(t).width; }

// ----- 글 배치: 조각(굵기·색이 다른 글)을 폭에 맞춰 줄로 나눔 -----
function layoutText(segs, width, px, firstIndent) {
  const lines = [[]];
  let x = firstIndent || 0;
  function newLine() { lines.push([]); x = 0; }
  for (const seg of segs) {
    const parts = stamp(seg.t).split(/( +)/);
    for (const p of parts) {
      if (!p) continue;
      const w = tw(p, px, seg.bold);
      const line = lines[lines.length - 1];
      if (p.trim() === '') { if (x > 0) { line.push({t: p, w, x, bold: seg.bold, color: seg.color}); x += w; } continue; }
      if (x + w > width && x > 0) { newLine(); }
      if (w > width) {   // 한 단어가 너무 길면 글자 단위로 나눔
        for (const ch of Array.from(p)) {
          const cw = tw(ch, px, seg.bold);
          if (x + cw > width && x > 0) newLine();
          lines[lines.length - 1].push({t: ch, w: cw, x, bold: seg.bold, color: seg.color});
          x += cw;
        }
        continue;
      }
      lines[lines.length - 1].push({t: p, w, x, bold: seg.bold, color: seg.color});
      x += w;
    }
  }
  return lines;
}
function drawLines(ctx, lines, x0, y0, px, lineH, defColor) {
  ctx.textBaseline = 'middle';
  lines.forEach((line, i) => {
    const cy = y0 + i * lineH + lineH / 2 + px * 0.04;
    for (const tok of line) {
      if (tok.t.trim() === '') continue;
      ctx.font = fnt(px, tok.bold);
      ctx.fillStyle = tok.color || defColor || '#111';
      ctx.fillText(tok.t, x0 + tok.x, cy);
    }
  });
}
function lineWidth(line) { let m = 0; for (const t of line) m = Math.max(m, t.x + t.w); return m; }
function segsOf(runs, color) { return runs.map(r => ({t: r[0], bold: r[1], color})); }
function rrect(ctx, x, y, w, h, r) {
  ctx.beginPath();
  ctx.moveTo(x + r, y); ctx.arcTo(x + w, y, x + w, y + h, r); ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r); ctx.arcTo(x, y, x + w, y, r); ctx.closePath();
}

// ----- 블록들 -----
const INK = '#111827';
function blockHeading(title, underline) {
  const hasEmoji = /^[\p{Extended_Pictographic}]/u.test(title.trim());
  const bar = hasEmoji ? 0 : 20;
  let px = 31;
  while (px > 24 && tw(stamp(title), px, true) + bar > CW) px -= 1;
  const lh = Math.round(px * 1.3);
  const lines = layoutText([{t: title, bold: true}], CW - bar, px);
  const h = lines.length * lh + (underline ? 14 + 2 + 20 : 18);
  return {h, draw(ctx, x, y) {
    if (bar) { ctx.fillStyle = '#2563EB'; rrect(ctx, x, y + 3, 8, lh - 6, 4); ctx.fill(); }
    drawLines(ctx, lines, x + bar, y, px, lh, INK);
    if (underline) { ctx.fillStyle = '#E3E7EE'; ctx.fillRect(x, y + lines.length * lh + 14, CW, 2); }
  }};
}
function blockParagraph(runs, px, color, lhRatio) {
  const lh = Math.round(px * lhRatio);
  const lines = layoutText(segsOf(runs, color), CW, px);
  return {h: lines.length * lh, draw(ctx, x, y) { drawLines(ctx, lines, x, y, px, lh, color); }};
}
function itemLayout(it, px, lhRatio, gap, indent0) {
  const lh = Math.round(px * lhRatio);
  const level = it.level || 0;
  const ind = (indent0 || 26) + (level ? 28 : 0);
  const ipx = level ? Math.round(px * 0.92) : px;
  const segs = [];
  if (it.date) segs.push({t: it.date + ' ', bold: true, color: '#EA6A00'});
  if (it.label) segs.push({t: it.label + ' ', bold: false});
  it.runs.forEach(r => segs.push({t: r[0], bold: r[1]}));
  const lines = layoutText(segs, CW - ind, ipx);
  const h = lines.length * lh + gap;
  return {h, draw(ctx, x, y) {
    const cy = y + lh / 2 + 1;
    if (level) {
      const cx = x + 36; ctx.strokeStyle = INK; ctx.lineWidth = 1.8;
      ctx.beginPath(); ctx.moveTo(cx, cy - 5); ctx.lineTo(cx + 5, cy); ctx.lineTo(cx, cy + 5); ctx.lineTo(cx - 5, cy); ctx.closePath(); ctx.stroke();
    } else { ctx.fillStyle = INK; ctx.beginPath(); ctx.arc(x + 8, cy, 4.5, 0, Math.PI * 2); ctx.fill(); }
    drawLines(ctx, lines, x + ind, y, ipx, lh, INK);
  }};
}
function blockList(items, plain) {
  const px = plain ? 24 : 20, gap = plain ? 14 : 10;
  const ls = items.map(it => itemLayout(it, px, 1.45, gap));
  const h = ls.reduce((a, l) => a + l.h, 0) - gap;
  return {h, draw(ctx, x, y) { let yy = y; ls.forEach(l => { l.draw(ctx, x, yy); yy += l.h; }); }};
}
function blockIssue(b, isLast) {
  const BW = b.num ? 52 : 0, GAPB = 8, PADL = 14, PADR = 16;
  const dirW = b.dir ? 34 : 0;
  const avail = CW - BW - GAPB - PADL - PADR - dirW;
  let px = 22, lines;
  while (px > 19 && tw(stamp(b.title), px, true) > avail) px -= 1;
  lines = layoutText([{t: b.title, bold: true}], avail, px);
  const lh = Math.round(px * 1.35);
  const barH = Math.max(52, lines.length * lh + 18);
  const ls = b.items.map(it => itemLayout(it, 20, 1.45, 10));
  const body = ls.reduce((a, l) => a + l.h, 0) - (ls.length ? 10 : 0);
  const h = barH + (ls.length ? 16 + body : 0) + (isLast ? 0 : 30);
  return {h, draw(ctx, x, y) {
    const px0 = x + BW + GAPB;
    ctx.fillStyle = '#FBE9ED'; rrect(ctx, px0, y, CW - BW - GAPB, barH, 12); ctx.fill();
    if (b.num) {
      ctx.fillStyle = '#1F2A44'; rrect(ctx, x, y, BW, barH, 12); ctx.fill();
      ctx.fillStyle = '#fff'; ctx.font = fnt(26, true); ctx.textBaseline = 'middle'; ctx.textAlign = 'center';
      ctx.fillText(b.num, x + BW / 2, y + barH / 2 + 1); ctx.textAlign = 'left';
    }
    let tx = px0 + PADL;
    if (b.dir) { ctx.font = fnt(20, false); ctx.textBaseline = 'middle'; ctx.fillStyle = INK; ctx.fillText(b.dir, tx, y + barH / 2 + 1); tx += dirW; }
    drawLines(ctx, lines, tx, y + (barH - lines.length * lh) / 2, px, lh, INK);
    let yy = y + barH + 16;
    ls.forEach(l => { l.draw(ctx, x, yy); yy += l.h; });
  }};
}
function cellText(runs) { return runs.map(r => r[0]).join(''); }
const TABLE_FS = {};   // 같은 머리글을 가진 표는 같은 글자 크기로 맞춤
function tableKey(b) { return b.header.join('|'); }
function tableRows(b) {
  const rows = [b.header.map(h => [[h, true]])].concat(b.rows.map(r => r.map((c, i) => c.map(x => [x[0], x[1] || i === b.name]))));
  const ncol = b.header.length;
  rows.forEach(r => { while (r.length < ncol) r.push([['', false]]); });
  return rows;
}
const TPX = 0.5, TPY = 0.58;
function naturalFs(b) {
  const ncol = b.header.length;
  const rows = tableRows(b);
  const texts = rows.map(r => r.map(c => cellText(c)));
  const bolds = rows.map((r, ri) => r.map((c, i) => ri === 0 || i === b.name || c.some(x => x[1])));
  let fs = 24;
  for (; fs > 14; fs -= 0.5) {
    const nat = new Array(ncol).fill(0);
    texts.forEach((r, ri) => r.forEach((t, i) => { nat[i] = Math.max(nat[i], tw(stamp(t), fs, bolds[ri][i]) + 2 * TPX * fs); }));
    if (nat.reduce((a, v) => a + v, 0) <= CW) break;
  }
  return fs;
}
function prepareTables() {
  D.sections.forEach(sec => sec.blocks.forEach(b => {
    if (b.type !== 'table') return;
    const k = tableKey(b), f = naturalFs(b);
    TABLE_FS[k] = Math.min(TABLE_FS[k] || 99, f);
  }));
}
function blockTable(b) {
  const PX = TPX, PY = TPY;
  const rows = tableRows(b);
  const ncol = b.header.length;
  const texts = rows.map(r => r.map(c => cellText(c)));
  const bolds = rows.map((r, ri) => r.map((c, i) => ri === 0 || i === b.name || c.some(x => x[1])));
  const al = [];
  for (let i = 0; i < ncol; i++) al.push((b.align && b.align[i]) || 'left');
  let fs = TABLE_FS[tableKey(b)] || 24, nat;
  function measure() {
    nat = new Array(ncol).fill(0);
    texts.forEach((r, ri) => r.forEach((t, i) => { nat[i] = Math.max(nat[i], tw(stamp(t), fs, bolds[ri][i]) + 2 * PX * fs); }));
    return nat.reduce((a, v) => a + v, 0);
  }
  let guard = 0;
  while (measure() > CW && guard++ < 300) {   // 그래도 넘치면 가장 긴 칸을 …로 줄임
    let br = -1, bc = -1, bw = 0;
    texts.forEach((r, ri) => r.forEach((t, i) => { const w = tw(stamp(t), fs, bolds[ri][i]); if (w > bw && t.length > 3) { bw = w; br = ri; bc = i; } }));
    if (br < 0) break;
    texts[br][bc] = texts[br][bc].slice(0, -2).trimEnd() + '…';
  }
  const total = nat.reduce((a, v) => a + v, 0);
  const colW = nat.map(v => v + (CW - total) * v / total);
  const rowH = Math.round(fs * 1.3 + 2 * PY * fs);
  const h = rowH * rows.length;
  return {h, draw(ctx, x, y) {
    ctx.save(); rrect(ctx, x, y, CW, h, 14); ctx.clip();
    ctx.fillStyle = '#F3F4F7'; ctx.fillRect(x, y, CW, rowH);
    ctx.restore();
    ctx.strokeStyle = '#D5DAE3'; ctx.lineWidth = 1.5;
    for (let r = 1; r < rows.length; r++) { ctx.beginPath(); ctx.moveTo(x, y + r * rowH); ctx.lineTo(x + CW, y + r * rowH); ctx.stroke(); }
    let xx = x;
    for (let i = 0; i < ncol - 1; i++) { xx += colW[i]; ctx.beginPath(); ctx.moveTo(xx, y); ctx.lineTo(xx, y + h); ctx.stroke(); }
    rrect(ctx, x, y, CW, h, 14); ctx.stroke();
    ctx.textBaseline = 'middle';
    rows.forEach((r, ri) => {
      let cx = x;
      r.forEach((c, i) => {
        ctx.font = fnt(fs, bolds[ri][i]); ctx.fillStyle = INK;
        const t = stamp(texts[ri][i]);
        const a = ri === 0 ? 'center' : al[i];
        const cy = y + ri * rowH + rowH / 2 + fs * 0.04;
        if (a === 'right') { ctx.textAlign = 'right'; ctx.fillText(t, cx + colW[i] - PX * fs, cy); }
        else if (a === 'center') { ctx.textAlign = 'center'; ctx.fillText(t, cx + colW[i] / 2, cy); }
        else { ctx.textAlign = 'left'; ctx.fillText(t, cx + PX * fs, cy); }
        cx += colW[i];
      });
    });
    ctx.textAlign = 'left';
  }};
}

function layoutBlocks(sec) {
  const plain = !sec.blocks.some(b => b.type === 'issue');
  const out = [];
  sec.blocks.forEach((b, i) => {
    const last = i === sec.blocks.length - 1 || sec.blocks.slice(i + 1).every(x => x.type !== 'issue');
    let l;
    if (b.type === 'table') l = blockTable(b);
    else if (b.type === 'list') l = blockList(b.items, plain);
    else if (b.type === 'issue') l = blockIssue(b, last);
    else if (b.type === 'note') l = blockParagraph(b.runs, 17, '#6b7280', 1.5);
    else l = blockParagraph(b.runs, 22, undefined, 1.55);
    out.push(l);
  });
  return out;
}

const GAP = 16;
function sectionPiece(title, line, laid, cont) {
  const head = blockHeading(title + (cont ? ' (이어서)' : ''), line);
  let h = head.h;
  laid.forEach((l, i) => { h += l.h + (i < laid.length - 1 ? GAP : 0); });
  return {h, draw(ctx, x, y) {
    head.draw(ctx, x, y); let yy = y + head.h;
    laid.forEach(l => { l.draw(ctx, x, yy); yy += l.h + GAP; });
  }};
}
function coverPiece(sumPx) {
  const t = blockParagraph([[D.cover.title, false]], 20, '#64748B', 1.35);
  const hd = blockHeading('📌 한 줄 요약', false);
  const sm = blockParagraph([[D.cover.summary, true]], sumPx, INK, 1.45);
  return {h: t.h + 12 + hd.h + sm.h, draw(ctx, x, y) {
    t.draw(ctx, x, y); hd.draw(ctx, x, y + t.h + 12); sm.draw(ctx, x, y + t.h + 12 + hd.h);
  }};
}
function pageH(items) { let h = CPT + CPB; items.forEach((p, i) => { h += (i ? SEP : 0) + p.h; }); return h; }

function paginate() {
  prepareTables();
  const pages = [];
  const push = (items, name) => pages.push({items, h: pageH(items), name});
  const secPiece = (sec) => sectionPiece(sec.title, !sec.hasTable, layoutBlocks(sec), false);
  const hasCover = !!(D.cover.title || D.cover.summary);
  const secs = D.sections;
  let i = 0;

  // 한 장을 넘는 섹션은 블록 단위로 나눔
  function splitSection(sec) {
    const laid = layoutBlocks(sec);
    let group = [], cont = false;
    const flush = () => {
      if (!group.length) return;
      push([sectionPiece(sec.title, !sec.hasTable, group, cont)], sec.title + (cont ? '_이어서' : ''));
      group = []; cont = true;
    };
    laid.forEach(l => {
      const trial = sectionPiece(sec.title, !sec.hasTable, group.concat([l]), cont);
      if (group.length && pageH([trial]) > MAXH) flush();
      group.push(l);
    });
    flush();
  }
  function emit(sectionList, withCover, name) {
    const pieces = sectionList.map(secPiece);
    if (withCover) {
      for (let px = 30; px >= 18; px--) {
        const items = [coverPiece(px)].concat(pieces);
        if (pageH(items) <= MAXH || px === 18) {
          if (pageH(items) <= MAXH) { push(items, name); return true; }
        }
      }
      return false;
    }
    const items = pieces;
    if (pageH(items) <= MAXH) { push(items, name); return true; }
    return false;
  }

  // 표지 + 같은 묶음(지수 현황·해외 지수)을 한 장에
  if (hasCover) {
    let n = 0;
    if (D.cover.group === 'summary') { while (n < secs.length && secs[n].group === 'summary') n++; }
    if (!(n && emit(secs.slice(0, n), true, '한줄요약'))) {
      n = 0;
      if (!emit([], true, '한줄요약')) push([coverPiece(20)], '한줄요약');
    }
    i = n;
  }
  while (i < secs.length) {
    const g = secs[i].group;
    let n = 1;
    if (g && g !== 'summary') { while (i + n < secs.length && secs[i + n].group === g) n++; }
    if (n > 1 && emit(secs.slice(i, i + n), false, secs[i].title)) { i += n; continue; }
    const sec = secs[i];
    if (!emit([sec], false, sec.title)) splitSection(sec);
    i++;
  }
  return pages;
}

function drawPage(page, scale) {
  const cv = document.createElement('canvas');
  cv.width = W * scale; cv.height = H * scale;
  const ctx = cv.getContext('2d'); ctx.scale(scale, scale);
  ctx.fillStyle = '#F3F5FA'; ctx.fillRect(0, 0, W, H);
  ctx.save(); ctx.shadowColor = 'rgba(30,40,70,0.10)'; ctx.shadowBlur = 24; ctx.shadowOffsetY = 6;
  ctx.fillStyle = '#fff'; rrect(ctx, MX, MY, W - 2 * MX, page.h, 26); ctx.fill(); ctx.restore();
  let y = MY + CPT;
  page.items.forEach((p, i) => {
    if (i > 0) y += SEP;
    p.draw(ctx, MX + CPX, y); y += p.h;
  });
  return cv;
}
window.__MTNW = {paginate, drawPage};

// ----- ZIP (압축 없이 묶기) -----
const CRC = (() => { const t = new Uint32Array(256); for (let n = 0; n < 256; n++) { let c = n; for (let k = 0; k < 8; k++) c = c & 1 ? 0xEDB88320 ^ (c >>> 1) : c >>> 1; t[n] = c >>> 0; } return t; })();
function crc32(b) { let c = 0xFFFFFFFF; for (let i = 0; i < b.length; i++) c = CRC[(c ^ b[i]) & 255] ^ (c >>> 8); return (c ^ 0xFFFFFFFF) >>> 0; }
function makeZip(files) {
  const enc = new TextEncoder(), chunks = [], central = []; let off = 0;
  files.forEach(f => {
    const nb = enc.encode(f.name), crc = crc32(f.data), sz = f.data.length;
    const lh = new DataView(new ArrayBuffer(30));
    lh.setUint32(0, 0x04034b50, true); lh.setUint16(4, 20, true); lh.setUint16(6, 0x0800, true); lh.setUint16(8, 0, true);
    lh.setUint16(10, 0, true); lh.setUint16(12, 33, true); lh.setUint32(14, crc, true); lh.setUint32(18, sz, true); lh.setUint32(22, sz, true);
    lh.setUint16(26, nb.length, true); lh.setUint16(28, 0, true);
    chunks.push(new Uint8Array(lh.buffer), nb, f.data);
    const ch = new DataView(new ArrayBuffer(46));
    ch.setUint32(0, 0x02014b50, true); ch.setUint16(4, 20, true); ch.setUint16(6, 20, true); ch.setUint16(8, 0x0800, true);
    ch.setUint16(10, 0, true); ch.setUint16(12, 0, true); ch.setUint16(14, 33, true); ch.setUint32(16, crc, true);
    ch.setUint32(20, sz, true); ch.setUint32(24, sz, true); ch.setUint16(28, nb.length, true); ch.setUint32(42, off, true);
    central.push(new Uint8Array(ch.buffer), nb);
    off += 30 + nb.length + sz;
  });
  const csize = central.reduce((a, c) => a + c.length, 0);
  const end = new DataView(new ArrayBuffer(22));
  end.setUint32(0, 0x06054b50, true); end.setUint16(8, files.length, true); end.setUint16(10, files.length, true);
  end.setUint32(12, csize, true); end.setUint32(16, off, true);
  return new Blob(chunks.concat(central, [new Uint8Array(end.buffer)]), {type: 'application/zip'});
}
function safeName(t) { return String(t).replace(/[\\/:*?"<>|#]/g, '').replace(/[\s·.,()]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 24) || 'card'; }
function download(blob, name) {
  const a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = name;
  document.body.appendChild(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(a.href), 4000);
}

async function make() {
  const btn = document.getElementById('mk'), st = document.getElementById('status'), prev = document.getElementById('prev');
  try {
    btn.disabled = true; st.textContent = ' 이미지를 만드는 중...'; prev.innerHTML = '';
    if (document.fonts && document.fonts.ready) await document.fonts.ready;
    const pages = paginate(); const files = [];
    for (let i = 0; i < pages.length; i++) {
      const cv = drawPage(pages[i], D.scale);
      const blob = await new Promise(r => cv.toBlob(r, 'image/png'));
      const name = String(i + 1).padStart(2, '0') + '_' + safeName(pages[i].name) + '.png';
      files.push({name, data: new Uint8Array(await blob.arrayBuffer())});
      const img = document.createElement('img'); img.src = URL.createObjectURL(blob); img.title = name; prev.appendChild(img);
    }
    download(makeZip(files), D.prefix + '_카드이미지.zip');
    st.textContent = ' 완료 · ' + pages.length + '장 (ZIP으로 내려받았습니다)';
  } catch (e) { st.textContent = ' 실패: ' + (e && e.message ? e.message : e); }
  finally { btn.disabled = false; }
}
document.getElementById('mk').onclick = make;
"""

CSS = """
*{box-sizing:border-box;margin:0;padding:0}
body{background:transparent;font-family:'Malgun Gothic','Apple SD Gothic Neo',sans-serif}
.bar{display:flex;gap:8px;align-items:center;flex-wrap:wrap;font-size:14px}
.bar button{padding:6px 12px;cursor:pointer}
#prev{display:flex;gap:10px;overflow-x:auto;margin-top:10px;padding-bottom:6px}
#prev img{width:180px;height:240px;border:1px solid #ccc;border-radius:6px;flex:0 0 auto}
"""


def build_data(markdown, prefix, now_token="", scale=1):
    title, summary, sections = parse_markdown(markdown)

    data = {
        "prefix": prefix,
        "token": now_token or "",
        "scale": scale,
        "cover": {"title": title, "summary": summary},
        "sections": [],
    }

    if sections and group_of(sections[0]["title"]) == "summary":
        data["cover"]["group"] = "summary"

    for section in sections:
        sec_title = section["title"]
        lines = section["lines"]

        # 핵심 키워드는 카톡용과 같이 5개만 (본문에 많이 나온 순)
        if "핵심 키워드" in sec_title:
            sec_title = re.sub(r"핵심 키워드\s*\d+", f"핵심 키워드 {KEYWORD_COUNT}", sec_title)
            kept, done = [], False

            for line in lines:
                if not done and line.strip():
                    line = ", ".join(
                        _pick_keywords(line.strip(), str(markdown).replace(line.strip(), ""))
                    )
                    done = True

                kept.append(line)

            lines = kept

        blocks = build_blocks(lines)

        if blocks:
            data["sections"].append({
                "title": sec_title,
                "blocks": blocks,
                "hasIssue": any(b["type"] == "issue" for b in blocks),
                "hasTable": any(b["type"] == "table" for b in blocks),
                "group": group_of(sec_title),
            })

    return data


def render_card_images(markdown, prefix, now_token="", scale=1):
    """'🖼️ 카드 이미지 만들기' 버튼과 미리보기를 표시합니다.

    prefix: 파일 이름 머리말(예: '장전시황'). now_token: 오후 시황의 시각 자리표시자."""

    payload = json.dumps(
        build_data(markdown, prefix, now_token, scale), ensure_ascii=False
    ).replace("</", "<\\/")

    page = f"""
    <style>{CSS}</style>
    <div class="bar">
      <button id="mk">🖼️ 카드 이미지 만들기 (720×960)</button>
      <span id="status"></span>
    </div>
    <div id="prev"></div>
    <script>{JS.replace('__DATA__', payload)}</script>
    """

    import streamlit.components.v1 as _components

    _components.html(page, height=330, scrolling=True)
