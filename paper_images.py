"""시황글을 신문 '지면' 모양의 이미지(PNG)로 만들어 ZIP으로 내려받는 버튼.

- 1면: 제호 · 한 줄 요약(큰 헤드라인) · 톱 이슈 · '숫자로 본 …' 지수표
- 2면~: 핵심 이슈, 종목 표, 테마, 일정, 키워드 (내용이 많으면 면이 늘어납니다)
- 카드 이미지(card_images.py)와 별개로 동작하며, 같은 글꼴·같은 마크다운 해석을 씁니다.
"""

import json

from card_images import _font_data
from paper_model import build_model, issue_number

W, H = 720, 960

JS = r"""
const D = __DATA__;
const W = 720, H = 960, M = 40, CW = W - 2 * M;
const PAPER = '#F5F0E6', INK = '#16130E', MUTED = '#6B6254', RULE = '#CFC6B4', ACC = '#E8590C', UP = '#D6282B', DOWN = '#1D5FD0', SOFT = '#EDE6D6';
const FONT = "'NanumSquareNeo','Pretendard','Malgun Gothic','Apple SD Gothic Neo','Noto Sans KR','Noto Sans CJK KR','Segoe UI Emoji','Apple Color Emoji','Noto Color Emoji',sans-serif";
const FACES = [];
if (D.fonts && D.fonts.rg && D.fonts.bd && D.fonts.xb) {
  [['400', D.fonts.rg], ['700', D.fonts.bd], ['800', D.fonts.xb]].forEach(([w, u]) => {
    const f = new FontFace('NanumSquareNeo', 'url(' + u + ')', {weight: w});
    document.fonts.add(f); FACES.push(f.load());
  });
}
const mcv = document.createElement('canvas'), mctx = mcv.getContext('2d');
function nowKST() { return new Date().toLocaleTimeString('en-GB', {timeZone: 'Asia/Seoul', hour: '2-digit', minute: '2-digit', hourCycle: 'h23'}); }
function stamp(t) { return D.token ? String(t).split(D.token).join(nowKST()) : String(t); }
function fnt(px, b) { return (b === 2 ? '800 ' : b ? 'bold ' : '') + px + 'px ' + FONT; }
function tw(t, px, b) { mctx.font = fnt(px, b); return mctx.measureText(t).width; }
function arrows(t) { return String(t).replace(/🔺/g, '▲').replace(/🔻/g, '▼').replace(/➖/g, '–').replace(/\s+(?=[▲▼–])/g, ' '); }
function dirColor(t) { const s = String(t).trim(); if (/^(▲|\+)/.test(s)) return UP; if (/^(▼|-|−)/.test(s)) return DOWN; return INK; }

function wrap(text, width, px, b) {            // 글자·단어 단위 줄바꿈 → 줄 글자 배열
  const out = []; let line = '';
  for (const word of stamp(text).split(/( +)/)) {
    if (!word) continue;
    if (tw(line + word, px, b) <= width || !line.trim()) {
      if (tw(word, px, b) > width) {                // 한 단어가 너무 길면 글자 단위
        for (const ch of Array.from(word)) {
          if (tw(line + ch, px, b) > width && line) { out.push(line.trimEnd()); line = ''; }
          line += ch;
        }
      } else line += word;
    } else { out.push(line.trimEnd()); line = word.trimStart(); }
  }
  if (line.trim()) out.push(line.trimEnd());
  return out.length ? out : [''];
}
function clamp(lines, max, width, px, b) {       // max줄을 넘으면 마지막 줄을 …로
  if (lines.length <= max) return lines;
  const keep = lines.slice(0, max); let last = keep[max - 1];
  while (last.length > 1 && tw(last + '…', px, b) > width) last = last.slice(0, -1);
  keep[max - 1] = last.trimEnd() + '…'; return keep;
}
function text(ctx, t, x, y, px, b, color, align) {
  ctx.font = fnt(px, b); ctx.fillStyle = color || INK; ctx.textAlign = align || 'left'; ctx.textBaseline = 'middle';
  ctx.fillText(t, x, y); ctx.textAlign = 'left';
}
function para(ctx, lines, x, y, px, lh, b, color) { lines.forEach((l, i) => text(ctx, l, x, y + i * lh + lh / 2, px, b, color)); }
function rrect(ctx, x, y, w, h, r) {
  ctx.beginPath(); ctx.moveTo(x + r, y); ctx.arcTo(x + w, y, x + w, y + h, r); ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r); ctx.arcTo(x, y, x + w, y, r); ctx.closePath();
}
function hline(ctx, x, y, w, th, color) { ctx.fillStyle = color || INK; ctx.fillRect(x, y, w, th); }

// ---------- 머리글(제호) ----------
function mastheadBig(ctx) {
  let y = 34;
  ctx.font = fnt(60, 2); const nm = 'MTNW 시황';
  text(ctx, nm, M, y + 34, 60, 2, INK);
  const nw = tw(nm, 60, 2);
  ctx.fillStyle = ACC; ctx.beginPath(); ctx.arc(M + nw + 9, y + 55, 6, 0, Math.PI * 2); ctx.fill();
  const eb = D.model.edition, ew = tw(eb, 19, 2) + 28;
  ctx.fillStyle = INK; rrect(ctx, W - M - ew, y + 8, ew, 36, 4); ctx.fill();
  text(ctx, eb, W - M - ew / 2, y + 27, 19, 2, PAPER, 'center');
  text(ctx, D.model.date_text, W - M, y + 62, 17, 1, MUTED, 'right');
  hline(ctx, M, y + 84, CW, 4); hline(ctx, M, y + 92, CW, 1.5);
  const meta = (D.issueNo ? '제' + D.issueNo + '호 · ' : '') + D.model.edition + ' 발행';
  text(ctx, meta, M, y + 112, 15, 1, MUTED);
  return y + 132;
}
function mastheadSmall(ctx, pageNo) {
  const y = 30;
  text(ctx, 'MTNW 시황', M, y + 16, 28, 2, INK);
  const nw = tw('MTNW 시황', 28, 2);
  ctx.fillStyle = ACC; ctx.beginPath(); ctx.arc(M + nw + 6, y + 26, 3.6, 0, Math.PI * 2); ctx.fill();
  text(ctx, D.model.edition + ' · ' + D.model.date_text + ' · ' + pageNo + '면', W - M, y + 16, 15, 1, MUTED, 'right');
  hline(ctx, M, y + 40, CW, 3); hline(ctx, M, y + 46, CW, 1);
  return y + 66;
}
function footer(ctx, no, total, nextText) {
  const y = H - 52;
  hline(ctx, M, y, CW, 1, INK);
  text(ctx, nextText || '※ 참고 자료이며 투자 권유가 아닙니다', M, y + 20, 14, 1, MUTED);
  text(ctx, no + ' / ' + total, W - M, y + 20, 14, 2, MUTED, 'right');
  if (nextText) text(ctx, '※ 참고 자료이며 투자 권유가 아닙니다', M, y + 38, 12, 1, MUTED);
}

// ---------- 조각(높이를 재고 그리는 블록) ----------
function sectionHead(title, color) {
  const px = 21, h = 36;
  return {h, draw(ctx, x, y, w) {
    ctx.fillStyle = color || ACC; ctx.fillRect(x, y + 5, 6, 24);
    text(ctx, stamp(title), x + 16, y + 18, px, 2, INK);
    hline(ctx, x, y + h - 3, w, 1, RULE);
  }};
}
function issuePiece(it, w, big) {
  const px = big ? 28 : 23, lh = Math.round(px * 1.28), bw = 40;
  const tl = clamp(wrap(it.title, w - bw - 14, px, 2), 4, w - bw - 14, px, 2);
  const items = it.items.map(i => ({label: i.label, lines: clamp(wrap(i.text, w - bw - 14, 17, 0), i.label === '내용' ? (big ? 6 : 5) : 2, w - bw - 14, 17, 0)}));
  let h = tl.length * lh + 8;
  items.forEach(i => { h += i.lines.length * 26 + 8; });
  h += 18;
  return {h, draw(ctx, x, y) {
    ctx.fillStyle = INK; rrect(ctx, x, y + 2, bw, bw, 6); ctx.fill();
    text(ctx, it.num || '•', x + bw / 2, y + 2 + bw / 2 + 1, 24, 2, PAPER, 'center');
    const tx = x + bw + 14; let yy = y;
    if (it.dir !== 'flat') {
      const c = it.dir === 'up' ? UP : DOWN, ax = tx;   // 방향 표시는 제목 위 작은 칩
    }
    para(ctx, tl, tx, yy, px, lh, 2, INK); yy += tl.length * lh + 8;
    items.forEach(i => {
      if (i.label === '내용') { para(ctx, i.lines, tx, yy, 17, 26, 0, '#2B2620'); }
      else {
        const lab = i.label || '•'; const lw = tw(lab, 15, 2) + 16;
        ctx.fillStyle = SOFT; rrect(ctx, tx, yy + 3, lw, 22, 4); ctx.fill();
        text(ctx, lab, tx + lw / 2, yy + 14, 15, 2, ACC, 'center');
        const rest = clamp(wrap(i.lines.join(' '), w - bw - 14 - lw - 10, 17, 1), 2, w - bw - 14 - lw - 10, 17, 1);
        rest.forEach((l, k) => text(ctx, l, tx + lw + 10, yy + k * 26 + 14, 17, 1, INK));
      }
      yy += i.lines.length * 26 + 8;
    });
    hline(ctx, x, y + h - 6, w, 1, RULE);
    if (it.dir !== 'flat') { text(ctx, it.dir === 'up' ? '▲' : '▼', x + bw + 14 + Math.max(...tl.map(l => tw(l, px, 2))) + 8, y + lh / 2 + 1, px * 0.7, 2, it.dir === 'up' ? UP : DOWN); }
  }};
}
function tablePiece(p, w) {
  const ncol = p.header.length;
  const rows = [p.header].concat(p.rows.map(r => { const c = r.slice(0, ncol); while (c.length < ncol) c.push(''); return c; }));
  const isName = i => i === p.name;
  let fs = 20, nat;
  const PADX = 0.5;
  function measure() {
    nat = new Array(ncol).fill(0);
    rows.forEach((r, ri) => r.forEach((c, i) => { nat[i] = Math.max(nat[i], tw(arrows(stamp(c)), fs, ri === 0 || isName(i) ? 2 : 0) + 2 * PADX * fs); }));
    return nat.reduce((a, v) => a + v, 0);
  }
  while (measure() > w && fs > 13) fs -= 0.5;
  const texts = rows.map(r => r.slice());
  let guard = 0;
  while (measure() > w && guard++ < 200) {             // 그래도 넘치면 긴 칸을 …로
    let br = -1, bc = -1, bw = 0;
    texts.forEach((r, ri) => { if (ri) r.forEach((c, i) => { const ww = tw(arrows(c), fs, 0); if (ww > bw && c.length > 3) { bw = ww; br = ri; bc = i; } }); });
    if (br < 0) break;
    texts[br][bc] = texts[br][bc].slice(0, -2).trimEnd() + '…'; rows[br][bc] = texts[br][bc];
  }
  const total = nat.reduce((a, v) => a + v, 0), colW = nat.map(v => v + (w - total) * v / total);
  const rowH = Math.round(fs * 1.2 + 0.7 * fs);
  const head = sectionHead(p.title);
  const extra = (p.extra || []).map(t => clamp(wrap(t, w, 14, 0), 2, w, 14, 0));
  let h = head.h + 6 + rowH * rows.length + 6;
  extra.forEach(e => { h += e.length * 20 + 4; });
  return {h, draw(ctx, x, y) {
    head.draw(ctx, x, y, w); let yy = y + head.h + 6;
    ctx.fillStyle = SOFT; ctx.fillRect(x, yy, w, rowH);
    hline(ctx, x, yy, w, 2, INK); hline(ctx, x, yy + rowH - 2, w, 1.5, INK);
    rows.forEach((r, ri) => {
      let cx = x;
      r.forEach((c, i) => {
        const t = arrows(stamp(texts[ri][i])); const al = ri === 0 ? 'center' : ((p.align && p.align[i]) || 'left');
        const col = ri === 0 ? INK : (/등락|%/.test(p.header[i]) || /^[▲▼+\-−]\s*[+\-−]?\d/.test(t) ? dirColor(t) : INK);
        const b = ri === 0 || isName(i) || col !== INK ? 2 : 0;
        const cy = yy + rowH / 2 + 1;
        if (al === 'right') text(ctx, t, cx + colW[i] - PADX * fs, cy, fs, b, col, 'right');
        else if (al === 'center') text(ctx, t, cx + colW[i] / 2, cy, fs, b, col, 'center');
        else text(ctx, t, cx + PADX * fs, cy, fs, b, col);
        cx += colW[i];
      });
      if (ri > 0 && ri < rows.length - 1) hline(ctx, x, yy + rowH - 1, w, 1, RULE);
      yy += rowH;
    });
    hline(ctx, x, yy, w, 2, INK); yy += 6;
    extra.forEach(e => { para(ctx, e, x, yy, 14, 20, 0, MUTED); yy += e.length * 20 + 4; });
  }};
}
function rowsPiece(p, w) {
  const head = sectionHead(p.title), px = 19, lh = 26;
  const items = p.rows.map(r => {
    const dateW = r.date ? tw(r.date, 17, 2) + 10 : 0;
    const body = r.date ? r.head.replace(r.date, '').trim() : r.head;
    const hl = clamp(wrap(body + (r.detail ? '  ' + r.detail : ''), w - 22 - dateW, px, 0), 3, w - 22 - dateW, px, 0);
    return {r, hl, dateW, body};
  });
  let h = head.h + 8;
  items.forEach(i => { h += i.hl.length * lh + 6; });
  return {h, draw(ctx, x, y) {
    head.draw(ctx, x, y, w); let yy = y + head.h + 8;
    items.forEach(i => {
      ctx.fillStyle = ACC; ctx.beginPath(); ctx.arc(x + 6, yy + lh / 2, 3.5, 0, Math.PI * 2); ctx.fill();
      if (i.r.date) text(ctx, i.r.date, x + 22, yy + lh / 2, 17, 2, ACC);
      i.hl.forEach((l, k) => {
        const tx = x + 22 + i.dateW;
        if (k === 0 && i.r.detail && l.indexOf(i.r.detail) > 0) {
          const cut = l.indexOf('  ' + i.r.detail); const a = cut > 0 ? l.slice(0, cut) : l, bb = cut > 0 ? l.slice(cut) : '';
          text(ctx, a, tx, yy + lh / 2, px, 2, INK); if (bb) text(ctx, bb, tx + tw(a, px, 2), yy + lh / 2, px, 0, MUTED);
        } else text(ctx, l, tx, yy + k * lh + lh / 2, px, (k === 0 && i.r.detail) ? 2 : 0, INK);
      });
      yy += i.hl.length * lh + 6;
    });
  }};
}
function pillsPiece(p, w, items, title, hash) {
  const head = sectionHead(title), px = 17, ph = 34;
  const pos = []; let x = 0, y = 0;
  items.forEach(t => {
    const label = (hash ? '#' : '') + t, pw = tw(label, px, 2) + 28;
    if (x + pw > w && x > 0) { x = 0; y += ph + 8; }
    pos.push({label, x, y, pw}); x += pw + 10;
  });
  const h = head.h + 8 + (y + ph);
  return {h, draw(ctx, X, Y) {
    head.draw(ctx, X, Y, w);
    pos.forEach(q => {
      const px0 = X + q.x, py0 = Y + head.h + 8 + q.y;
      ctx.strokeStyle = INK; ctx.lineWidth = 1.6; rrect(ctx, px0, py0, q.pw, ph, ph / 2); ctx.stroke();
      text(ctx, q.label, px0 + q.pw / 2, py0 + ph / 2 + 1, px, 2, INK, 'center');
    });
  }};
}
function cautionPiece(p, w) {
  const lines = clamp(wrap(p.text, w - 36, 17, 0), 3, w - 36, 17, 0);
  const h = lines.length * 26 + 28;
  return {h, draw(ctx, x, y) {
    ctx.fillStyle = SOFT; rrect(ctx, x, y, w, h, 8); ctx.fill();
    ctx.fillStyle = ACC; ctx.fillRect(x, y + 10, 5, h - 20);
    para(ctx, lines, x + 20, y + 14, 17, 26, 0, INK);
  }};
}
function bandPiece(b, w) {   // 주간 성적표처럼 열이 많은 표 (전체 폭)
  return tablePiece({title: b.title, header: b.header, rows: b.rows, align: b.align, name: 0, extra: []}, w);
}
function flowPiece(text_, w) {
  const lines = clamp(wrap(text_, w - 4, 18, 0), 4, w - 4, 18, 0);
  const head = sectionHead('주간 흐름');
  return {h: head.h + 8 + lines.length * 28, draw(ctx, x, y) { head.draw(ctx, x, y, w); para(ctx, lines, x, y + head.h + 8, 18, 28, 0, INK); }};
}

// ---------- 1면 ----------
function page1(ctx, total, nextText, tail) {
  const m = D.model;
  let y = mastheadBig(ctx) + 6;
  text(ctx, (m.kind === 'week' ? '주간 종합 · 이번 주의 한 줄' : '1면 종합 · 오늘의 한 줄'), M, y + 10, 16, 2, ACC);
  y += 30;
  let hp = 52, hl;
  for (; hp >= 32; hp -= 2) { hl = wrap(m.headline || m.title, CW, hp, 2); if (hl.length <= 4) break; }
  hl = clamp(hl, 4, CW, hp, 2);
  const hlh = Math.round(hp * 1.22);
  para(ctx, hl, M, y, hp, hlh, 2, INK); y += hl.length * hlh + 14;
  hline(ctx, M, y, CW, 1.5, INK); y += 18;

  const rail = m.indices.length ? m.indices.slice(0, 10) : null;
  const RW = 236, GAP = 22, LW = CW - RW - GAP;
  const leftW = rail ? LW : CW;
  const lead = m.issues[0];
  let ly = y;

  // 왼쪽: 톱 이슈 + 이어지는 이슈
  if (lead) {
    const px = 26, lh = 34;
    const tl = clamp(wrap(lead.title, leftW - 4, px, 2), 3, leftW - 4, px, 2);
    text(ctx, '톱 이슈', M, ly + 9, 15, 2, ACC);
    if (lead.dir !== 'flat') text(ctx, lead.dir === 'up' ? '▲ 상승 이슈' : '▼ 하락 이슈', M + tw('톱 이슈', 15, 2) + 10, ly + 9, 15, 2, lead.dir === 'up' ? UP : DOWN);
    ly += 26;
    para(ctx, tl, M, ly, px, lh, 2, INK); ly += tl.length * lh + 6;
    const body = lead.items.find(i => i.label === '내용') || lead.items[0];
    if (body) { const bl = clamp(wrap(body.text, leftW - 4, 18, 0), 6, leftW - 4, 18, 0); para(ctx, bl, M, ly, 18, 29, 0, '#2B2620'); ly += bl.length * 29 + 6; }
    const rel = lead.items.find(i => /종목/.test(i.label));
    if (rel) { const rl = clamp(wrap(rel.text, leftW - 90, 17, 1), 2, leftW - 90, 17, 1); text(ctx, '관련 종목', M, ly + 13, 15, 2, ACC); para(ctx, rl, M + 82, ly, 17, 26, 1, INK); ly += rl.length * 26 + 6; }
  } else if (m.flow) {
    const fl = clamp(wrap(m.flow, leftW - 4, 19, 0), 9, leftW - 4, 19, 0); para(ctx, fl, M, ly, 19, 30, 0, INK); ly += fl.length * 30;
  }
  // 이어지는 이슈 제목
  const rest = m.issues.slice(1);
  if (rest.length) {
    ly += 10; hline(ctx, M, ly, leftW, 1, RULE); ly += 14;
    text(ctx, '이어지는 이슈', M, ly + 9, 15, 2, ACC); ly += 26;
    rest.forEach(it => {
      const lines = clamp(wrap(it.title, leftW - 36, 19, 2), 3, leftW - 36, 19, 2);
      ctx.fillStyle = INK; rrect(ctx, M, ly + 2, 26, 26, 5); ctx.fill();
      text(ctx, it.num || '•', M + 13, ly + 16, 16, 2, PAPER, 'center');
      para(ctx, lines, M + 36, ly, 19, 27, 2, INK);
      if (it.dir !== 'flat') text(ctx, it.dir === 'up' ? '▲' : '▼', M + 36 + tw(lines[lines.length - 1], 19, 2) + 6, ly + (lines.length - 1) * 27 + 14, 15, 2, it.dir === 'up' ? UP : DOWN);
      ly += lines.length * 27 + 12;
    });
  }

  // 오른쪽: 숫자로 본 …
  let ry = y;
  if (rail) {
    const rx = M + LW + GAP;
    text(ctx, m.rail_title, rx, ry + 11, 20, 2, INK);
    text(ctx, (m.kind === 'aft' ? '장중 기준 · ' : '') + '등락률은 전일 대비', rx, ry + 34, 13, 1, MUTED);
    ry += 52; hline(ctx, rx, ry, RW, 2, INK); ry += 4;
    let lastGroup = '';
    rail.forEach(r => {
      if (r.group !== lastGroup && lastGroup) { hline(ctx, rx, ry + 3, RW, 1, INK); ry += 9; }
      lastGroup = r.group;
      const c = r.dir === 'up' ? UP : r.dir === 'down' ? DOWN : INK;
      let nm = r.name; let np = 18; while (tw(nm, np, 2) > RW - 100 && np > 13) np--; 
      text(ctx, nm, rx, ry + 17, np, 2, INK);
      text(ctx, arrows(r.change).replace(/^[▲▼–]\s*/, (m0) => m0.trim() + ' '), rx + RW, ry + 17, 17, 2, c, 'right');
      text(ctx, r.value, rx, ry + 40, 15, 1, MUTED);
      hline(ctx, rx, ry + 52, RW, 1, RULE);
      ry += 56;
    });
    ctx.fillStyle = '#D8CFBC'; ctx.fillRect(rx - GAP / 2 - 0.5, y, 1.5, Math.max(ly, ry) - y);
  }
  y = Math.max(ly, ry) + 12;

  // 아래: 주간 성적표 등 넓은 표, 주간 흐름
  const extras = [];
  m.bands.forEach(b => extras.push(bandPiece(b, CW)));
  if (m.flow && lead) extras.push(flowPiece(m.flow, CW));
  extras.forEach(p => { if (y + p.h < H - 70) { p.draw(ctx, M, y); y += p.h + 14; } });
  (tail || []).forEach(p => { p.draw(ctx, M, y); y += p.h + 14; });
  footer(ctx, 1, total, nextText);
  return y;
}

// ---------- 2면~ ----------
function buildFlow() {
  const m = D.model, pieces = [];
  m.issues.slice(m.issues.length > 1 ? 1 : 0).forEach(it => { if (m.issues.length > 1 || true) pieces.push({label: '핵심 이슈', make: w => issuePiece(it, w, false)}); });
  if (m.issues.length === 1) pieces.length = 0;
  m.pieces.forEach(p => {
    if (p.type === 'table') pieces.push({label: p.title.replace(/\(.*?\)/g, '').replace(/ TOP ?\d+/, '').trim(), make: w => tablePiece(p, w)});
    else if (p.type === 'rows') pieces.push({label: p.title.replace(/\(.*?\)/g, '').trim(), make: w => rowsPiece(p, w)});
    else if (p.type === 'tags') pieces.push({label: p.title, make: w => pillsPiece(p, w, p.tags, p.title, false)});
    else if (p.type === 'keywords') pieces.push({tail: true, label: '키워드', make: w => pillsPiece(p, w, p.words, p.title, true)});
    else if (p.type === 'caution') pieces.push({tail: true, label: '주의', make: w => cautionPiece(p, w)});
  });
  return pieces;
}
function dryCtx() { const c = document.createElement('canvas'); c.width = 8; c.height = 8; return c.getContext('2d'); }
function paginate() {
  const flow = buildFlow().map(f => ({label: f.label, tail: f.tail, p: f.make(CW)}));
  // 1면 아래 빈 자리에 키워드·주의를 먼저 넣어 보고, 안 들어가면 다음 면으로 보냅니다
  const used = page1(dryCtx(), 1, '', []);
  let room = H - 70 - used - 8;
  const tail = [];
  flow.filter(f => f.tail).forEach(f => {
    if (f.p.h + 14 <= room) { tail.push(f.p); f.moved = true; room -= f.p.h + 14; }
  });
  const pages = []; let cur = null;
  const MAXY = H - 64, START = 96;
  const place = (f, pg) => { pg.items.push(f.p); pg.y += f.p.h + 16; if (!pg.labels.includes(f.label)) pg.labels.push(f.label); };
  flow.filter(f => !f.moved && !f.tail).forEach(f => {
    if (!cur || cur.y + f.p.h > MAXY) { cur = {items: [], y: START, labels: []}; pages.push(cur); }
    place(f, cur);
  });
  flow.filter(f => !f.moved && f.tail).forEach(f => {       // 키워드·주의는 빈 자리가 있는 면부터 채움
    const pg = pages.find(q => q.y + f.p.h <= MAXY);
    if (pg) place(f, pg);
    else { cur = {items: [], y: START, labels: []}; pages.push(cur); place(f, cur); }
  });
  pages.tail = tail;
  return pages;
}
function nextTextOf(pages) {
  if (!pages.length) return '';
  return pages.slice(0, 3).map((pg, i) => (i + 2) + '면 ' + pg.labels.slice(0, 2).join('·')).join('   ');
}
function drawPage(i, pages, scale) {
  const cv = document.createElement('canvas'); cv.width = W * scale; cv.height = H * scale;
  const ctx = cv.getContext('2d'); ctx.scale(scale, scale);
  ctx.fillStyle = PAPER; ctx.fillRect(0, 0, W, H);
  const total = pages.length + 1;
  if (i === 0) page1(ctx, total, nextTextOf(pages), pages.tail);
  else {
    let y = mastheadSmall(ctx, i + 1) + 6; const pg = pages[i - 1];
    pg.items.forEach(p => { p.draw(ctx, M, y); y += p.h + 16; });
    footer(ctx, i + 1, total, '');
  }
  return cv;
}

// ---------- ZIP ----------
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
function download(blob, name) {
  const a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = name;
  document.body.appendChild(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(a.href), 4000);
}
window.__PAPER = {paginate, drawPage, ready: Promise.all(FACES)};

async function make() {
  const btn = document.getElementById('mkp'), st = document.getElementById('statusp'), prev = document.getElementById('prevp');
  try {
    btn.disabled = true; st.textContent = ' 지면을 만드는 중...'; prev.innerHTML = '';
    try { await Promise.all(FACES); } catch (e) {}
    if (document.fonts && document.fonts.ready) await document.fonts.ready;
    const pages = paginate(), files = [], total = pages.length + 1;
    for (let i = 0; i < total; i++) {
      const cv = drawPage(i, pages, D.scale);
      const blob = await new Promise(r => cv.toBlob(r, 'image/png'));
      const name = String(i + 1).padStart(2, '0') + '_' + (i + 1) + '면.png';
      files.push({name, data: new Uint8Array(await blob.arrayBuffer())});
      const img = document.createElement('img'); img.src = URL.createObjectURL(blob); img.title = name; prev.appendChild(img);
    }
    download(makeZip(files), D.prefix + '_지면이미지.zip');
    st.textContent = ' 완료 · ' + total + '면 (ZIP으로 내려받았습니다)';
  } catch (e) { st.textContent = ' 실패: ' + (e && e.message ? e.message : e); }
  finally { btn.disabled = false; }
}
document.getElementById('mkp').onclick = make;
"""

CSS = """
*{box-sizing:border-box;margin:0;padding:0}
body{background:transparent;font-family:'Malgun Gothic','Apple SD Gothic Neo',sans-serif}
.bar{display:flex;gap:8px;align-items:center;flex-wrap:wrap;font-size:14px}
.bar button{padding:6px 12px;cursor:pointer}
#prevp{display:flex;gap:10px;overflow-x:auto;margin-top:10px;padding-bottom:6px}
#prevp img{width:180px;height:240px;border:1px solid #ccc;border-radius:6px;flex:0 0 auto}
"""


def saved_days():
    """저장된 장전 시황 날짜(호수 계산용). 실패하면 빈 목록."""

    try:
        from archive import list_posts

        return [day for day, _ in list_posts("pre", limit=2000)]
    except Exception:
        return []


def build_paper_data(markdown, prefix, now_token="", scale=1.5, issue_no=None):
    model = build_model(markdown)

    if issue_no is None:
        issue_no = issue_number(model, saved_days())

    return {
        "prefix": prefix,
        "token": now_token or "",
        "scale": scale,
        "fonts": _font_data(),
        "model": model,
        "issueNo": issue_no,
    }


def render_paper_images(markdown, prefix, now_token="", scale=1.5, issue_no=None):
    """'📰 지면 이미지 만들기' 버튼과 미리보기를 표시합니다 (카드 이미지 버튼 아래)."""

    import streamlit.components.v1 as components

    payload = json.dumps(
        build_paper_data(markdown, prefix, now_token, scale, issue_no), ensure_ascii=False
    ).replace("</", "<\\/")

    page = f"""
    <style>{CSS}</style>
    <div class="bar">
      <button id="mkp">📰 지면 이미지 만들기 (신문 스타일)</button>
      <span id="statusp"></span>
    </div>
    <div id="prevp"></div>
    <script>{JS.replace('__DATA__', payload)}</script>
    """

    components.html(page, height=330, scrolling=True)
