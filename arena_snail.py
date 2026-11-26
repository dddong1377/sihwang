"""공동 꼴찌가 나왔을 때만 쓰는 '달팽이 레이스' 꼴찌 뽑기.

결과는 (구분 문자열 + 동점자 이름)으로 정해지는 고정된 값입니다.
새로고침·다시 보기를 해도, 누가 열어도 같은 결과가 나오므로 다시 뽑아서 바꿀 수 없습니다.
화면 연출(JS)은 여기서 정해 준 순서·속도를 그대로 재생할 뿐 결과를 다시 정하지 않습니다.
"""

import hashlib
import json
import random

import streamlit.components.v1 as components


def plan(names, seed):
    """{'lanes': [{'name','dur','way'}], 'loser': 이름}.
    dur(초)가 가장 긴 달팽이가 마지막에 도착해 꼴찌가 됩니다. way는 중간 통과 지점 [(시간비율, 위치비율)]."""

    names = sorted({str(n) for n in names})
    digest = hashlib.sha256((str(seed) + "|" + "|".join(names)).encode("utf-8")).hexdigest()
    rng = random.Random(int(digest, 16))

    lanes = []

    for name in names:
        dur = round(rng.uniform(7.0, 11.0), 3)
        spots = sorted(round(rng.uniform(0.08, 0.92), 3) for _ in range(5))
        way = [[round((i + 1) / 6, 4), spots[i]] for i in range(5)]
        lanes.append({"name": name, "dur": dur, "way": way})

    loser = max(lanes, key=lambda lane: (lane["dur"], lane["name"]))["name"]

    return {"lanes": lanes, "loser": loser}


def loser_of(names, seed):
    return plan(names, seed)["loser"]


_HTML = """
<style>
  html,body{margin:0;background:transparent;font-family:'Pretendard','Malgun Gothic','Apple SD Gothic Neo',sans-serif}
  .box{background:#120e0b;border:1px solid #3a2a1e;border-radius:18px;padding:12px 12px 10px;color:#f1e6dc;overflow:hidden}
  .top{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-bottom:8px}
  .ttl{font-weight:800;font-size:15px}
  button{cursor:pointer;border:0;border-radius:999px;padding:7px 14px;font-weight:800;font-size:13px;background:#ff7a2f;color:#1a0e06}
  button:disabled{opacity:.55;cursor:default}
  /* 관중석 */
  .stand{position:relative;height:34px;border-radius:12px 12px 0 0;background:linear-gradient(#2a1c15,#1a120e);display:flex;align-items:flex-end;justify-content:space-around;padding:0 6px;overflow:hidden;font-size:17px;line-height:1}
  .stand span{display:inline-block;transform-origin:bottom center}
  .cheer .stand span{animation:hop .5s ease-in-out infinite alternate}
  .cheer .stand span:nth-child(3n){animation-delay:.12s}.cheer .stand span:nth-child(3n+1){animation-delay:.25s}
  @keyframes hop{from{transform:translateY(2px)}to{transform:translateY(-6px)}}
  /* 경기장 */
  .arena{position:relative;border-radius:0 0 12px 12px;overflow:hidden;background:#2f6b3a;border:1px solid #1d4a27}
  .lane{position:relative;height:44px;background:linear-gradient(90deg,#8c3d22,#a94a28 50%,#8c3d22);border-bottom:2px dashed rgba(255,255,255,.55)}
  .lane:last-child{border-bottom:0}
  .gate{position:absolute;left:0;top:0;bottom:0;width:86px;background:linear-gradient(90deg,#1f1612,#2b1e17);display:flex;align-items:center;gap:6px;padding-left:6px;border-right:3px solid #f5efe8;box-sizing:border-box}
  .bib{flex:none;width:20px;height:20px;border-radius:50%;background:#f5efe8;color:#1a0e06;font-size:12px;font-weight:900;display:flex;align-items:center;justify-content:center}
  .nm{font-size:13px;font-weight:800;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:54px}
  .over{position:absolute;right:0;top:0;bottom:0;width:44px;background:repeating-linear-gradient(0deg,#2f6b3a 0 8px,#2a6234 8px 16px);display:flex;align-items:center;justify-content:center;font-size:15px;font-weight:900;color:#fff}
  .fl{position:absolute;top:0;bottom:0;width:16px;background:repeating-conic-gradient(#fff 0% 25%,#15110e 0% 50%) 0 0/16px 16px;box-shadow:0 0 0 1px #000 inset}
  .sn{position:absolute;top:5px;left:0;font-size:26px;line-height:32px;width:30px;text-align:center}
  .go .sn{animation:bob .32s ease-in-out infinite alternate}
  @keyframes bob{from{transform:translateY(0)}to{transform:translateY(-3px)}}
  .lane.win{box-shadow:inset 0 0 0 2px #ffd34d}
  .lane.lose{box-shadow:inset 0 0 0 2px #ff5a5a}.lane.lose .nm{color:#ff9d9d}
  .banner{position:absolute;right:0;bottom:0;width:60px;text-align:center;background:#15110e;color:#fff;font-size:10px;font-weight:900;letter-spacing:.5px;padding:2px 0;border-radius:8px 8px 0 0;z-index:3}
  .cd{position:absolute;inset:0;display:none;align-items:center;justify-content:center;font-size:54px;font-weight:900;color:#fff;text-shadow:0 3px 12px rgba(0,0,0,.7);background:rgba(0,0,0,.35);z-index:4}
  .res{margin-top:10px;min-height:22px;font-size:14px;font-weight:800;text-align:center;color:#ffd9a8}
  .hint{margin-top:4px;font-size:11px;color:#a8978a;text-align:center}
</style>
<div class="box" id="box">
  <div class="top"><div class="ttl" id="t"></div><button id="go">🐌 레이스 시작</button></div>
  <div class="stand" id="stand"><div class="banner">FINISH</div></div>
  <div class="arena" id="arena">
    <div id="lanes"></div>
    <div class="cd" id="cd"></div>
  </div>
  <div class="res" id="res"></div>
  <div class="hint">결과는 날짜와 참가자로 정해진 값이라 다시 해도 같습니다</div>
</div>
<script>
const P = __PLAN__;
const TITLE = __TITLE__;
const GATE = 86, OVER = 44, FLW = 16, SNW = 30;
document.getElementById('t').textContent = TITLE;
const stand = document.getElementById('stand');
['👏','🎉','🙌','📣','👏','🎊','🙌','📣','👏','🎉','🙌','🎊','👏','📣'].forEach(e => { const s = document.createElement('span'); s.textContent = e; stand.appendChild(s); });
const lanesEl = document.getElementById('lanes');
const rows = P.lanes.map((l, i) => {
  const row = document.createElement('div'); row.className = 'lane';
  const gate = document.createElement('div'); gate.className = 'gate';
  const bib = document.createElement('div'); bib.className = 'bib'; bib.textContent = String(i + 1);
  const nm = document.createElement('div'); nm.className = 'nm'; nm.textContent = l.name; nm.title = l.name;
  gate.appendChild(bib); gate.appendChild(nm);
  const fl = document.createElement('div'); fl.className = 'fl';
  const over = document.createElement('div'); over.className = 'over';
  const sn = document.createElement('div'); sn.className = 'sn'; sn.textContent = '🐌';
  row.appendChild(gate); row.appendChild(fl); row.appendChild(over); row.appendChild(sn);
  lanesEl.appendChild(row);
  const pts = [[0, 0]].concat(l.way).concat([[1, 1]]);
  return {l, row, sn, fl, over, pts, done: false, t: 0};
});
function posAt(r, el) {
  const f = Math.min(1, el / r.l.dur), p = r.pts;
  for (let i = 1; i < p.length; i++) {
    if (f <= p[i][0]) { const a = p[i - 1], b = p[i], k = (f - a[0]) / (b[0] - a[0]); return a[1] + (b[1] - a[1]) * k; }
  }
  return 1;
}
function place() {
  rows.forEach(r => {
    const w = r.row.clientWidth;
    const finishX = w - OVER - FLW;
    r.fl.style.left = finishX + 'px';
    const run = finishX - GATE - SNW - 4;
    r.sn.style.left = (GATE + 4 + posAt(r, r.t) * run) + 'px';
  });
}
const box = document.getElementById('box'), cd = document.getElementById('cd');
let timer = null;
function reset() {
  rows.forEach(r => { r.t = 0; r.done = false; r.over.textContent = ''; r.row.classList.remove('lose', 'win'); });
  document.getElementById('res').textContent = '';
  box.classList.remove('go', 'cheer'); place();
}
function countdown(done) {
  const seq = ['3', '2', '1', '출발!']; let i = 0;
  cd.style.display = 'flex'; box.classList.add('cheer');
  (function step() {
    cd.textContent = seq[i];
    if (++i <= seq.length) setTimeout(step, i === seq.length ? 600 : 700); else { cd.style.display = 'none'; done(); }
  })();
}
document.getElementById('go').onclick = function () {
  if (timer) return;
  reset();
  const btn = this; btn.disabled = true;
  countdown(() => {
    box.classList.add('go');
    const t0 = performance.now(); let order = 0;
    timer = setInterval(() => {
      const el = (performance.now() - t0) / 1000;
      rows.forEach(r => {
        r.t = el;
        if (!r.done && el >= r.l.dur) {
          r.done = true; order += 1;
          r.over.textContent = order === 1 ? '🥇' : order === 2 ? '🥈' : order === 3 ? '🥉' : order + '등';
          r.sn.style.animation = 'none';
        }
      });
      place();
      if (rows.every(r => r.done)) {
        clearInterval(timer); timer = null; btn.disabled = false; btn.textContent = '🐌 다시 보기';
        box.classList.remove('go');
        rows.forEach(r => {
          if (r.l.name === P.loser) { r.row.classList.add('lose'); r.over.textContent = '🥄'; }
        });
        const first = rows.reduce((a, b) => (a.l.dur <= b.l.dur ? a : b));
        if (first.l.name !== P.loser) first.row.classList.add('win');
        document.getElementById('res').textContent = '🥄 꼴찌는 ' + P.loser + '! 상품은 ' + P.loser + '님이 쏩니다';
      }
    }, 40);
  });
};
window.addEventListener('resize', place);
place();
</script>
"""


def render(names, seed, title="🐌 꼴찌 뽑기 달팽이 레이스"):
    """공동 꼴찌(2명 이상)일 때만 호출하세요. 달팽이 레이스 화면을 표시하고 정해진 꼴찌 이름을 돌려줍니다."""

    names = list(dict.fromkeys(str(n) for n in names))

    if len(names) < 2:
        return names[0] if names else None

    data = plan(names, seed)

    def js(value):
        return json.dumps(value, ensure_ascii=False).replace("<", "\\u003c").replace("</", "<\\/")

    page = _HTML.replace("__PLAN__", js(data)).replace("__TITLE__", js(title))
    components.html(page, height=176 + 46 * len(names))

    return data["loser"]
