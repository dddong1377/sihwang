"""주식 대결(베타) 규칙. 화면·저장소와 분리해 두어 값만으로 검증할 수 있습니다.

규칙
- 하루 5대결(종목 2개씩). 시가총액 상위 종목에서 자동으로 짝을 짓습니다.
- 투표는 대결일 08:00~08:59에만 할 수 있습니다. 09:00이 되는 순간 투표할 수 없습니다.
  (그 시간 안에서는 몇 번이든 바꿀 수 있습니다.)
- 대결일 장마감(15:40 이후, 종가 확정) 뒤 두 종목의 '당일 등락률'을 비교해 높은 쪽이 승리합니다.
- 휴장·데이터 없음은 무효 처리하고 적중률에서 뺍니다. 등락률이 같으면 무승부(적중률에서 제외).
"""

import datetime
import math
import random

N_MATCHES = 5
OPEN_AT = datetime.time(8, 0)
DEADLINE = datetime.time(9, 0)
CLOSE_CONFIRM = datetime.time(15, 40)
GIVE_UP_AFTER = datetime.time(18, 0)   # 이 시각이 지나도 오늘 봉이 없으면 휴장으로 봄
DRAW_EPS = 0.005                        # 등락률 차이가 이보다 작으면 무승부(%p)
MIN_RANKED = {"week": 5, "month": 10, "all": 10}

REASONS = (
    "선택 안 함",
    "실적 개선 가능성",
    "산업·업황 전망",
    "기관·외국인 수급",
    "낮은 밸류에이션",
    "차트·기술적 추세",
    "기타",
)


# ---------------------------------------------------------------
# 날짜
# ---------------------------------------------------------------

def next_weekday(day):
    day = day + datetime.timedelta(days=1)

    while day.weekday() >= 5:
        day += datetime.timedelta(days=1)

    return day


def active_round_date(now):
    """지금 참여할 수 있는(또는 가장 최근) 대결일.

    평일 장마감 전이면 오늘, 장마감 후나 주말이면 다음 평일입니다."""

    today = now.date()

    if today.weekday() < 5 and now.time() < CLOSE_CONFIRM:
        return today

    return next_weekday(today)


def deadline_of(day, tz=None):
    return datetime.datetime.combine(day, DEADLINE, tzinfo=tz)


def open_of(day, tz=None):
    return datetime.datetime.combine(day, OPEN_AT, tzinfo=tz)


def deadline_passed(day, now):
    return now >= deadline_of(day, now.tzinfo)


def phase(day, now):
    """'before'(투표 열리기 전) | 'open'(08:00~08:59) | 'closed'(09:00 이후)"""

    if now < open_of(day, now.tzinfo):
        return "before"

    if now >= deadline_of(day, now.tzinfo):
        return "closed"

    return "open"


# ---------------------------------------------------------------
# 대진
# ---------------------------------------------------------------

def make_matches(top, day_text, n=N_MATCHES):
    """top: [(종목코드, 종목명)] 시가총액 순. 시가총액이 비슷한 종목끼리 짝을 지어 n개 대결을 만듭니다."""

    if len(top) < n * 2:
        raise ValueError("대진을 만들 종목이 부족합니다.")

    rng = random.Random(f"mtnw-arena:{day_text}")
    picked = sorted(rng.sample(range(len(top)), n * 2))

    matches = []

    for i in range(0, n * 2, 2):
        a = top[picked[i]]
        b = top[picked[i + 1]]

        if rng.random() < 0.5:
            a, b = b, a

        matches.append({
            "a": {"symbol": a[0], "name": a[1]},
            "b": {"symbol": b[0], "name": b[1]},
        })

    return matches


# ---------------------------------------------------------------
# 판정
# ---------------------------------------------------------------

def day_return(closes, day):
    """closes: [(date, 종가)] 오름차순. 그날 등락률(%) 또는 None."""

    previous = None

    for d, close in closes:
        if d == day:
            if not previous:
                return None

            return (close - previous) / previous * 100

        if d < day:
            previous = close

    return None


def judge_round(round_rec, closes_by_symbol, day, now):
    """판정 결과를 돌려줍니다.

    {"state": "wait" | "judged" | "void", "results": [...], "note": 안내}
    results 항목: {"a_pct", "b_pct", "winner": "a" | "b" | "draw" | "void"}
    """

    today = now.date()

    if day > today or (day == today and now.time() < CLOSE_CONFIRM):
        return {"state": "wait", "results": [], "note": "장마감 후 확정됩니다."}

    pcts = []
    market_open = False

    for match in round_rec["matches"]:
        pair = []

        for side in ("a", "b"):
            closes = closes_by_symbol.get(match[side]["symbol"]) or []
            value = day_return(closes, day)

            if value is not None:
                market_open = True

            pair.append(value)

        pcts.append(pair)

    if not market_open:
        if day < today or now.time() >= GIVE_UP_AFTER:
            return {"state": "void", "results": [], "note": "휴장이거나 시세 데이터가 없어 무효 처리했습니다."}

        return {"state": "wait", "results": [], "note": "종가 데이터를 기다리는 중입니다."}

    results = []

    for pa, pb in pcts:
        if pa is None or pb is None:
            results.append({"a_pct": pa, "b_pct": pb, "winner": "void"})
        elif abs(pa - pb) < DRAW_EPS:
            results.append({"a_pct": pa, "b_pct": pb, "winner": "draw"})
        else:
            results.append({"a_pct": pa, "b_pct": pb, "winner": "a" if pa > pb else "b"})

    return {"state": "judged", "results": results, "note": ""}


# ---------------------------------------------------------------
# 집계
# ---------------------------------------------------------------

def wilson_lower(hits, total, z=1.64):
    """참여 횟수가 적은 사람이 우연히 100%가 되어 1위가 되지 않도록 하는 보정 점수(0~1)."""

    if total <= 0:
        return 0.0

    p = hits / total
    denominator = 1 + z * z / total
    centre = p + z * z / (2 * total)
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * total)) / total)

    return max(0.0, (centre - margin) / denominator)


def user_stats(data, since=None):
    """{닉네임키: {"nick", "hits", "total", "rounds", "by_reason": {이유: [맞힘, 전체]}}}

    판정이 끝난 라운드의 선택만 셉니다. 무승부·무효·선택 안 한 대결은 제외합니다."""

    stats = {}

    for day, rec in data["rounds"].items():
        if rec.get("state") != "judged":
            continue

        if since and day < since:
            continue

        for key, pick in (data["picks"].get(day) or {}).items():
            user = data["users"].get(key)

            if not user:
                continue

            entry = stats.setdefault(key, {
                "nick": user["nick"], "hits": 0, "total": 0, "rounds": 0, "by_reason": {},
            })

            counted = False
            reasons = pick.get("reasons") or []

            for index, choice in enumerate(pick.get("choices") or []):
                if choice not in ("a", "b") or index >= len(rec["results"]):
                    continue

                winner = rec["results"][index]["winner"]

                if winner not in ("a", "b"):
                    continue

                hit = choice == winner
                counted = True
                entry["total"] += 1
                entry["hits"] += int(hit)

                reason = reasons[index] if index < len(reasons) else ""

                if reason and reason != REASONS[0]:
                    cell = entry["by_reason"].setdefault(reason, [0, 0])
                    cell[0] += int(hit)
                    cell[1] += 1

            if counted:
                entry["rounds"] += 1

    return stats


def ranking(stats, minimum):
    """(순위표, 집계 중) 목록. 순위표는 보정 점수 순입니다."""

    ranked, waiting = [], []

    for entry in stats.values():
        row = dict(entry)
        row["rate"] = entry["hits"] / entry["total"] if entry["total"] else 0.0
        row["score"] = wilson_lower(entry["hits"], entry["total"])

        (ranked if entry["total"] >= minimum else waiting).append(row)

    ranked.sort(key=lambda r: (-r["score"], -r["total"], r["nick"]))
    waiting.sort(key=lambda r: (-r["total"], r["nick"]))

    return ranked, waiting


def day_standings(data, day):
    """그날 라운드의 1등·꼴찌. 판정이 끝나지 않았거나 참가자가 없으면 None.

    기준: 맞힌 개수가 많은 순, 같으면 틀린 개수가 적은 순. 완전히 같으면 모두 공동입니다.
    무승부·무효·선택하지 않은 대결은 세지 않으며, 셀 수 있는 선택이 하나도 없는 사람은 제외합니다."""

    rec = data["rounds"].get(day)

    if not rec or rec.get("state") != "judged":
        return None

    rows = []

    for key, pick in (data["picks"].get(day) or {}).items():
        user = data["users"].get(key)

        if not user:
            continue

        hits = misses = 0

        for index, choice in enumerate(pick.get("choices") or []):
            if choice not in ("a", "b") or index >= len(rec["results"]):
                continue

            winner = rec["results"][index]["winner"]

            if winner not in ("a", "b"):
                continue

            if choice == winner:
                hits += 1
            else:
                misses += 1

        if hits + misses:
            rows.append({"nick": user["nick"], "hits": hits, "misses": misses})

    if not rows:
        return None

    rows.sort(key=lambda r: (-r["hits"], r["misses"], r["nick"]))

    best = (rows[0]["hits"], rows[0]["misses"])
    worst = (rows[-1]["hits"], rows[-1]["misses"])

    return {
        "rows": rows,
        "first": [r["nick"] for r in rows if (r["hits"], r["misses"]) == best],
        "last": [r["nick"] for r in rows if (r["hits"], r["misses"]) == worst],
        "tied_all": best == worst,
    }


def pick_share(data, day, index):
    """해당 대결의 선택 비율 {'a': 명, 'b': 명}"""

    count = {"a": 0, "b": 0}

    for pick in (data["picks"].get(day) or {}).values():
        choices = pick.get("choices") or []

        if index < len(choices) and choices[index] in count:
            count[choices[index]] += 1

    return count
