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
  .box{background:#16120f;border:1px solid #3a2a1e;border-radius:16px;padding:14px 14px 12px;color:#f1e6dc}
  .top{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-bottom:10px}
  .ttl{font-weight:800;font-size:15px}
  button{cursor:pointer;border:0;border-radius:999px;padding:7px 14px;font-weight:800;font-size:13px;background:#ff7a2f;color:#1a0e06}
  button:disabled{opacity:.55;cursor:default}
  .lane{display:flex;align-items:center;gap:8px;margin:6px 0}
  .nm{width:84px;flex:none;font-size:13px;font-weight:700;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .trk{position:relative;flex:1;height:34px;border-radius:10px;background:repeating-linear-gradient(90deg,#221a14 0 12px,#1c1511 12px 24px);border:1px solid #33261b}
  .fin{position:absolute;right:4px;top:3px;font-size:18px}
  .sn{position:absolute;top:2px;left:0;font-size:24px;line-height:28px;transition:none}
  .rk{width:34px;flex:none;text-align:right;font-size:12px;font-weight:800;color:#b9a595}
  .lane.lose .trk{border-color:#ff5a5a}.lane.lose .nm{color:#ff8f8f}
  .res{margin-top:10px;min-height:22px;font-size:14px;font-weight:800;text-align:center;color:#ffd9a8}
  .hint{margin-top:6px;font-size:11px;color:#a8978a;text-align:center}
</style>
<div class="box">
  <div class="top"><div class="ttl" id="t"></div><button id="go">🐌 레이스 시작</button></div>
  <div id="lanes"></div>
  <div class="res" id="res"></div>
  <div class="hint">결과는 날짜와 참가자로 정해진 값이라 다시 해도 같습니다</div>
</div>
<script>
const P = __PLAN__;
const TITLE = __TITLE__;
document.getElementById('t').textContent = TITLE;
const lanesEl = document.getElementById('lanes');
const rows = P.lanes.map(l => {
  const row = document.createElement('div'); row.className = 'lane';
  const nm = document.createElement('div'); nm.className = 'nm'; nm.textContent = l.name; nm.title = l.name;
  const trk = document.createElement('div'); trk.className = 'trk';
  const fin = document.createElement('div'); fin.className = 'fin'; fin.textContent = '🏁';
  const sn = document.createElement('div'); sn.className = 'sn'; sn.textContent = '🐌';
  const rk = document.createElement('div'); rk.className = 'rk';
  trk.appendChild(fin); trk.appendChild(sn);
  row.appendChild(nm); row.appendChild(trk); row.appendChild(rk);
  lanesEl.appendChild(row);
  const pts = [[0, 0]].concat(l.way).concat([[1, 1]]);
  return {l, row, trk, sn, rk, pts, done: false};
});
function posAt(r, el) {
  const f = Math.min(1, el / r.l.dur), p = r.pts;
  for (let i = 1; i < p.length; i++) {
    if (f <= p[i][0]) { const a = p[i - 1], b = p[i], k = (f - a[0]) / (b[0] - a[0]); return a[1] + (b[1] - a[1]) * k; }
  }
  return 1;
}
function place() {
  rows.forEach(r => { const w = r.trk.clientWidth - 34; r.sn.style.left = (posAt(r, r.t || 0) * w) + 'px'; });
}
let timer = null;
function reset() {
  rows.forEach(r => { r.t = 0; r.done = false; r.rk.textContent = ''; r.row.classList.remove('lose'); });
  document.getElementById('res').textContent = '';
  place();
}
document.getElementById('go').onclick = function () {
  if (timer) return;
  reset();
  const btn = this; btn.disabled = true;
  const t0 = performance.now(); let order = 0;
  timer = setInterval(() => {
    const el = (performance.now() - t0) / 1000;
    rows.forEach(r => {
      r.t = el;
      if (!r.done && el >= r.l.dur) { r.done = true; order += 1; r.rk.textContent = order + '등'; }
    });
    place();
    if (rows.every(r => r.done)) {
      clearInterval(timer); timer = null; btn.disabled = false; btn.textContent = '🐌 다시 보기';
      rows.forEach(r => { if (r.l.name === P.loser) r.row.classList.add('lose'); });
      document.getElementById('res').textContent = '🥄 꼴찌는 ' + P.loser + '! 상품은 ' + P.loser + '님이 쏩니다';
    }
  }, 40);
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
    components.html(page, height=60 + 46 * len(names) + 78)

    return data["loser"]
