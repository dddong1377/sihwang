import datetime
import hmac
import os
import random
import sys
import time
import traceback
from pathlib import Path
from zoneinfo import ZoneInfo

import streamlit as st

# set_page_config 는 이 페이지에서 가장 먼저 실행되는 화면 명령이어야 합니다.
# (아래의 어떤 st.* 호출보다 먼저 둡니다. 오류 안내도 이 줄 뒤에서만 표시합니다.)
st.set_page_config(page_title="MTNW 주식 대결 (베타)", layout="wide")

ROOT = Path(__file__).resolve().parent

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    import arena_logic as logic
    import arena_ui as ui
    import arena_store as store
    from weekly_market import candle_closes, fetch_cap_top
except Exception as exc:
    st.error(f"주식 대결 파일을 불러오지 못했습니다: {type(exc).__name__}: {exc}")
    st.code(
        f"이 파일 위치: {Path(__file__).resolve()}\n"
        f"같은 폴더의 .py 파일: {', '.join(sorted(p.name for p in ROOT.glob('*.py')))}\n"
        f"arena_logic.py 있음: {(ROOT / 'arena_logic.py').is_file()}\n"
        f"arena_store.py 있음: {(ROOT / 'arena_store.py').is_file()}\n"
        f"arena_ui.py 있음: {(ROOT / 'arena_ui.py').is_file()}",
        language="text",
    )
    st.code(traceback.format_exc(), language="text")
    st.stop()

KST = ZoneInfo("Asia/Seoul")
WEEKDAYS = "월화수목금토일"

DISCLAIMER = (
    "이 게임은 시황 도구 안에서만 진행되는 베타 테스트입니다. 종목 선택·선택 비율·랭킹은 "
    "투자 권유나 매수·매도 신호가 아니며, 종목 간 당일 등락률을 맞히는 게임 결과일 뿐 "
    "실제 투자 실력이나 수익을 뜻하지 않습니다."
)


# ===============================================================
# 공통: Secrets / 로그인(다른 시황 페이지와 같은 비밀번호)
# ===============================================================

def secret(key, default=""):
    try:
        value = st.secrets.get(key)
    except Exception:
        value = None

    return str(value) if value else os.getenv(key, default)


def login():
    if st.session_state.get("pw_ok"):
        return True

    expected = secret("app_password")

    if not expected:
        st.error("Streamlit Secrets에 app_password를 설정하세요.")
        return False

    def verify():
        entered = st.session_state.get("pw", "").encode()

        st.session_state["pw_ok"] = hmac.compare_digest(entered, expected.encode())
        st.session_state["pw"] = ""

    st.title("MTNW 시황")

    st.text_input("비밀번호", type="password", key="pw", on_change=verify)

    if st.session_state.get("pw_ok") is False:
        st.error("비밀번호가 올바르지 않습니다.")

    return False


if not login():
    st.stop()


def now_kst():
    return datetime.datetime.now(KST)


def day_text(day):
    return f"{day.month}/{day.day}({WEEKDAYS[day.weekday()]})"


# ===============================================================
# 대진 만들기 · 자동 판정
# ===============================================================

def ensure_round(day):
    """(data, 오류 문구). 대결일의 대진이 없으면 시가총액 상위 종목에서 자동으로 만듭니다."""

    key = day.isoformat()
    data = store.load()

    if key in data["rounds"]:
        return data, ""

    try:
        top = fetch_cap_top(now_kst().date().isoformat())
        matches = logic.make_matches(top, key)
    except Exception as error:
        return data, f"대진 만들기 실패: {str(error)[:200]}"

    def mutate(d):
        d["rounds"].setdefault(key, {
            "matches": matches,
            "created": now_kst().isoformat(),
            "state": "open",
            "results": [],
            "note": "",
        })

    try:
        store.update(mutate)
    except Exception as error:
        return data, f"대진 저장 실패: {str(error)[:200]}"

    return store.load(), ""


def judge_pending(force=False):
    """장마감(15:40) 이후 아직 판정되지 않은 라운드를 토스 일봉 등락률로 판정합니다. 오류 문구를 돌려줍니다."""

    state = st.session_state
    now = now_kst()

    if not force and time.time() - state.get("arena_judge_at", 0) < 120:
        return state.get("arena_judge_msg", "")

    state["arena_judge_at"] = time.time()

    data = store.load()
    messages = []

    for key in sorted(data["rounds"]):
        rec = data["rounds"][key]

        if rec.get("state", "open") != "open":
            continue

        day = datetime.date.fromisoformat(key)

        if day > now.date() or (day == now.date() and now.time() < logic.CLOSE_CONFIRM):
            continue

        closes = {}

        try:
            for match in rec["matches"]:
                for side in ("a", "b"):
                    symbol = match[side]["symbol"]

                    if symbol not in closes:
                        closes[symbol] = candle_closes(symbol)
        except Exception as error:
            messages.append(f"{day_text(day)} 판정 실패: {str(error)[:150]}")
            continue

        result = logic.judge_round(rec, closes, day, now)

        if result["state"] == "wait":
            continue

        def mutate(d, key=key, result=result):
            target = d["rounds"].get(key)

            if target and target.get("state", "open") == "open":
                target["state"] = result["state"]
                target["results"] = result["results"]
                target["note"] = result["note"]
                target["judged_at"] = now_kst().isoformat()

        try:
            store.update(mutate)
        except Exception as error:
            messages.append(f"{day_text(day)} 판정 저장 실패: {str(error)[:150]}")

    state["arena_judge_msg"] = " / ".join(messages)

    return state["arena_judge_msg"]


# ===============================================================
# 로그인(닉네임 + PIN)
# ===============================================================

def render_account(data):
    state = st.session_state
    nick = state.get("arena_nick")

    if nick:
        col_name, col_out = st.columns([5, 1], vertical_alignment="center")
        col_name.markdown(f"👤 **{nick}** 님으로 참여 중")

        if col_out.button("로그아웃", key="arena_logout", use_container_width=True):
            state["arena_nick"] = None
            state["arena_admin"] = False
            st.rerun()

        return nick

    st.markdown("##### 닉네임으로 참여하기")

    tab_in, tab_new = st.tabs(tab_names("로그인", "처음이에요"))

    with tab_in:
        with st.form("arena_login_form"):
            nick_in = st.text_input("닉네임", key="arena_login_nick")
            pin_in = st.text_input("PIN (숫자 4자리)", type="password", max_chars=4, key="arena_login_pin")
            go = st.form_submit_button("로그인", use_container_width=True)

        if go:
            try:
                ok, message = store.update(lambda d: store.verify(d, nick_in, pin_in))
            except Exception as error:
                ok, message = False, f"저장소 오류: {str(error)[:150]}"

            if ok:
                saved = store.load()["users"].get(store.nick_key(nick_in), {})
                state["arena_nick"] = saved.get("nick", nick_in.strip())
                st.rerun()

            st.error(message)

    with tab_new:
        with st.form("arena_register_form"):
            nick_new = st.text_input("사용할 닉네임 (한글·영문·숫자 2~12자)", key="arena_new_nick")
            pin_new = st.text_input("PIN (숫자 4자리, 잊지 마세요)", type="password", max_chars=4, key="arena_new_pin")
            go = st.form_submit_button("가입하고 시작", use_container_width=True)

        if go:
            try:
                ok, message = store.update(lambda d: store.register(d, nick_new, pin_new))
            except Exception as error:
                ok, message = False, f"저장소 오류: {str(error)[:150]}"

            if ok:
                state["arena_nick"] = nick_new.strip()
                st.rerun()

            st.error(message)

        st.caption("닉네임은 랭킹에 공개됩니다. 실명이나 연락처는 쓰지 마세요. PIN을 잊으면 관리자에게 초기화를 요청하세요.")

    return None


# ===============================================================
# 우승 상품 표시
# ===============================================================

def latest_standings(data):
    """판정이 끝난 가장 최근 라운드의 (날짜, 1등·꼴찌). 없으면 (None, None)"""

    for key in sorted(data["rounds"], reverse=True):
        result = logic.day_standings(data, key)

        if result:
            return key, result

    return None, None


def completed_weeks(data, now):
    """주간 우승·꼴찌가 확정된 주의 [(월요일, 순위)] 최근 순."""

    mondays = {
        logic.week_start(datetime.date.fromisoformat(key)) for key in data["rounds"]
    }

    found = []

    for monday in sorted(mondays, reverse=True):
        if logic.week_complete(data, monday, now):
            standing = logic.week_standings(data, monday)

            if standing:
                found.append((monday, standing))

    return found


def week_label(monday):
    friday = monday + datetime.timedelta(days=4)

    return f"{monday.month}/{monday.day}~{friday.month}/{friday.day}"


def prize_info(current):
    if current and current.get("name"):
        return current["name"], ("🎲 랜덤 추첨" if current.get("how") == "random" else "✋ 관리자 지정")

    return "준비 중", ""


def tab_names(*names):
    """탭 이름에 색을 직접 지정해, 선택 여부와 상관없이 어두운 배경에서도 항상 보이게 합니다."""

    return [f":orange[**{name}**]" for name in names]


def md(html_text):
    st.markdown(html_text, unsafe_allow_html=True)


def participation_html(data, key):
    """그날 투표한 참가자와 투표하지 않은 참가자를 함께 보여 줍니다."""

    users = data["users"]
    picks = data["picks"].get(key) or {}
    voted = sorted(u["nick"] for k, u in users.items() if k in picks)
    absent = sorted(u["nick"] for k, u in users.items() if k not in picks)

    def chips(names, fg, bg, border):
        if not names:
            return '<span style="color:#8d8d95;font-size:13px">없음</span>'

        return "".join(
            f'<span style="display:inline-block;margin:3px 6px 3px 0;padding:4px 12px;border-radius:999px;'
            f'font-size:13px;font-weight:600;color:{fg};background:{bg};border:1px solid {border}">{ui.esc(n)}</span>'
            for n in names
        )

    label = 'style="font-size:12px;color:#9a9aa2;margin:8px 0 2px"'

    return ui.frag(
        ui.section(f"{day_text(datetime.date.fromisoformat(key))} 참여 현황", "👥")
        + '<div style="background:#111114;border:1px solid #2a2a2f;border-radius:14px;padding:10px 16px 12px">'
        + f'<div {label}>✅ 투표함 · {len(voted)}명</div>'
        + chips(voted, "#7ee0a3", "#12301f", "#1f6b3f")
        + f'<div {label}>⬜ 투표 안 함 · {len(absent)}명</div>'
        + chips(absent, "#b4b4bc", "#1a1a1f", "#2e2e35")
        + "</div>"
    )


def render_prize(data):
    now = now_kst()

    name_day, how_day = prize_info(data["prizes"].get("current"))
    key, result = latest_standings(data)

    if result:
        day = datetime.date.fromisoformat(key)

        if result["tied_all"]:
            line_day = f"{day_text(day)}: 전원 같은 성적이라 꼴찌 없이 마무리"
        else:
            line_day = f"{day_text(day)} 우승 {', '.join(result['first'])} · 꼴찌 {', '.join(result['last'])} 🥄"
    else:
        line_day = "매일 결과가 확정되면 그날의 우승자와 꼴찌가 표시됩니다."

    name_week, how_week = prize_info(data["prizes"].get("weekly_current"))
    weeks = completed_weeks(data, now)

    if weeks:
        monday, standing = weeks[0]

        if standing["tied_all"]:
            line_week = f"{week_label(monday)} 주간: 전원 같은 성적이라 꼴찌 없이 마무리"
        else:
            line_week = f"{week_label(monday)} 주간 우승 {', '.join(standing['first'])} · 꼴찌 {', '.join(standing['last'])} 🥄"
    else:
        line_week = "금요일 장마감(15:40) 이후 그 주의 우승자와 꼴찌가 확정됩니다."

    sub_week = ""
    this_monday = logic.week_start(now.date())
    current = logic.week_standings(data, this_monday)

    if current and not logic.week_complete(data, this_monday, now):
        sub_week = f"이번 주 진행 중 · 현재 1위 {', '.join(current['first'])} ({current['rows'][0]['hits']}개 적중)"

    md(ui.frag(ui.prize_cards([
        {"title": "📅 일간 토너먼트 우승 상품", "name": name_day, "how": how_day, "line": line_day},
        {"title": "🏅 주간 토너먼트 우승 상품", "name": name_week, "how": how_week, "line": line_week, "sub": sub_week},
    ])))


def render_round_strip(data, now, active_day):
    monday = logic.week_start(active_day)
    items = []

    for offset in range(5):
        day = monday + datetime.timedelta(days=offset)
        rec = data["rounds"].get(day.isoformat())
        record_state = (rec or {}).get("state", "")

        if record_state == "judged":
            state, label = "done", "확정"
        elif record_state == "void":
            state, label = "void", "무효"
        elif day == active_day:
            phase = logic.phase(day, now)
            state = "live"
            label = {"before": "08:00 오픈", "open": "투표 중", "closed": "결과 대기"}[phase]
        elif record_state == "open" and day < now.date():
            state, label = "wait", "판정 대기"
        else:
            state, label = "wait", "예정"

        items.append({"name": "월화수목금"[offset], "date": f"{day.month}/{day.day}", "state": state, "label": label})

    md(ui.frag(ui.round_strip(items)))
    return monday


# ===============================================================
# 탭 1: 오늘의 대결
# ===============================================================

def render_play(data, nick, day, error):
    state = st.session_state
    now = now_kst()
    key = day.isoformat()
    rec = data["rounds"].get(key)
    phase = logic.phase(day, now)
    closed = phase == "closed"
    judged = bool(rec) and rec.get("state") == "judged"
    admin_test = bool(state.get("arena_admin"))

    flash = state.pop("arena_flash", "")

    if flash:
        st.success(flash)

    if phase == "before" and not admin_test:
        md(ui.frag(ui.cta_panel(
            f"{day_text(day)} 대결은 오전 8시에 열려요",
            ["8시 정각에 오늘의 5대결이 공개되고", "8시 59분까지 투표할 수 있어요"],
            "08:00 오픈",
            "승패 기준: 그날 장마감 등락률 · 꼴찌가 상품을 쏩니다",
        )))
        return

    if not rec:
        st.warning(error or "대진을 준비 중입니다.")

        if st.button("대진 다시 만들기", key="arena_retry_round"):
            st.rerun()

        return

    waiting_close = day == now.date() and now.time() < logic.CLOSE_CONFIRM

    if closed and not judged and waiting_close:
        md(ui.frag(ui.cta_panel(
            "오늘 투표는 마감되었습니다!",
            ["투표는 08:00~08:59에만 할 수 있어요", "결과는 장마감(15:40) 이후 확정돼요"],
            "09:00 마감",
        )))

    matches = rec["matches"]
    count = len(matches)
    nkey = store.nick_key(nick) if nick else ""
    mine = ((data["picks"].get(key) or {}).get(nkey) or {}) if nick else {}
    saved = list(mine.get("choices") or [])
    saved = (saved + [None] * count)[:count]
    saved_reasons = mine.get("reasons") or []
    has_pick = bool(mine)
    can_vote = bool(nick) and (phase == "open" or (admin_test and not judged))
    show_share = has_pick or closed
    results = list(rec.get("results") or []) if judged else []

    draft_key = f"arena_draft_{nkey or 'guest'}_{key}"

    if draft_key not in state:
        state[draft_key] = list(saved)

    draft = state[draft_key]

    def share_text(index, side):
        if not show_share:
            return None

        share = logic.pick_share(data, key, index)
        total = share["a"] + share["b"]

        return f"{share[side] / total * 100:.0f}%" if total else "0%"

    def draw_card(index):
        match = matches[index]
        result = results[index] if index < len(results) else None
        winner = result["winner"] if result else None

        with st.container(key=f"arena_card_{index}"):
            md(ui.frag(ui.tag(f"대결 {index + 1}")))

            for side in ("a", "b"):
                name = match[side]["name"]
                pct = share_text(index, side)

                if can_vote:
                    chosen = draft[index] == side
                    label = ("✓ " if chosen else "") + name + (f"   ·   {pct}" if pct else "")

                    if st.button(
                        label,
                        key=f"arena_btn_{key}_{index}_{side}",
                        type="primary" if chosen else "secondary",
                        use_container_width=True,
                    ):
                        draft[index] = None if chosen else side
                        state[draft_key] = draft
                        st.rerun()
                else:
                    row_state, mark, extra = "", "", ""
                    my = saved[index]

                    if winner in ("a", "b"):
                        row_state = "win" if side == winner else "lose"
                        value = result.get(f"{side}_pct")
                        extra = "" if value is None else f"{value:+.2f}%"

                        if my == side:
                            mark = "✅" if winner == side else "❌"
                    elif my == side:
                        row_state = "pick"
                        mark = "👈"

                    md(ui.frag(ui.team_row(name, row_state, pct, mark, extra)))

                if side == "a":
                    md(ui.frag(ui.vs_chip()))

    # 가운데 안내 패널
    picked = sum(1 for c in draft if c)

    if judged:
        total_hit = total_all = 0

        for index, res in enumerate(results):
            if saved[index] in ("a", "b") and res["winner"] in ("a", "b"):
                total_all += 1
                total_hit += int(saved[index] == res["winner"])

        lines = ["장마감 등락률로 확정된 결과예요"]

        if nick and total_all:
            lines.append(f"내 적중 {total_hit} / {total_all}")

        cta = ui.cta_panel("오늘의 결과 확정", lines, "노란 테두리가 승리한 종목", rec.get("note") or "")
    elif closed and not can_vote:
        if waiting_close or day != now.date():
            cta = ui.cta_panel("투표가 마감됐어요", ["09:00에 마감되어 더 이상 바꿀 수 없어요", "결과는 장마감(15:40) 이후 확정"], "09:00 마감")
        else:
            cta = ui.cta_panel("장마감 결과를 집계하고 있어요", ["투표는 마감됐고 결과를 확정하는 중이에요", "잠시 뒤 새로고침하면 결과가 보여요"], "결과 집계 중")
    elif not nick:
        cta = ui.cta_panel("오늘의 대결에 참여하세요", ["5개 대결 · 08:00~08:59 투표", "위에서 로그인하면 종목을 고를 수 있어요"], "", "투표 전에는 선택 비율이 보이지 않아요")
    elif phase != "open":
        cta = ui.cta_panel(
            "종목을 눌러 승자를 고르세요",
            [f"내 선택 {picked} / {count}", "관리자 테스트 모드라 투표 시간과 상관없이 고를 수 있어요"],
            "🔧 관리자 테스트 모드",
            "고른 뒤 아래 '예측 저장'을 꼭 눌러 주세요 (결과 확정 전까지)",
        )
    else:
        remain = logic.deadline_of(day, now.tzinfo) - now
        minutes = max(int(remain.total_seconds() // 60), 0)
        cta = ui.cta_panel(
            "종목을 눌러 승자를 고르세요",
            [f"내 선택 {picked} / {count}", "마음이 바뀌면 09:00 전까지 몇 번이든 바꿀 수 있어요"],
            f"마감까지 약 {minutes}분",
            "고른 뒤 아래 '예측 저장'을 꼭 눌러 주세요",
        )

    half = (count + 1) // 2
    col_left, col_mid, col_right = st.columns([5, 4, 5], gap="medium", vertical_alignment="center")

    with col_left:
        for index in range(half):
            draw_card(index)

    with col_mid:
        md(ui.frag(cta))

    with col_right:
        for index in range(half, count):
            draw_card(index)

    if admin_test and nick and phase != "open":
        st.caption("🔧 관리자 테스트 모드: 투표 시간 제한 없이 투표할 수 있어요 (결과 확정 전까지). 일반 참가자는 08:00~08:59만 가능합니다.")

    if can_vote:
        reasons = []

        with st.expander("선택 이유 (선택 사항 · 나중에 이유별 적중률이 쌓여요)"):
            for index, match in enumerate(matches):
                prev_reason = saved_reasons[index] if index < len(saved_reasons) else logic.REASONS[0]
                reason_index = logic.REASONS.index(prev_reason) if prev_reason in logic.REASONS else 0

                reasons.append(st.selectbox(
                    f"대결 {index + 1} · {match['a']['name']} vs {match['b']['name']}",
                    logic.REASONS,
                    index=reason_index,
                    key=f"arena_reason_{nkey}_{key}_{index}",
                ))

        changed = list(draft[:count]) != saved

        if changed:
            st.caption("저장하지 않은 변경이 있어요. 아래 버튼을 눌러 저장하세요.")
        elif has_pick:
            st.caption("저장되어 있어요 ✓")

        with st.container(key="arena_save"):
            submitted = st.button("예측 저장", type="primary", use_container_width=True, key="arena_save_btn")

        if submitted:
            choices = [c if c in ("a", "b") else None for c in draft[:count]]

            def mutate(d):
                current_phase = logic.phase(day, now_kst())

                if admin_test:
                    if (d["rounds"].get(key) or {}).get("state", "open") != "open":
                        return "결과가 이미 확정된 대결이라 저장하지 못했습니다."
                elif current_phase == "closed":
                    return "09:00이 지나 투표가 마감되어 저장하지 못했습니다."
                elif current_phase == "before":
                    return "아직 투표 시간(08:00~08:59)이 아닙니다."

                if nkey not in d["users"]:
                    return "참가자 정보를 찾지 못했습니다. 다시 로그인해 주세요."

                d["picks"].setdefault(key, {})[nkey] = {
                    "choices": choices,
                    "reasons": reasons,
                    "at": now_kst().isoformat(),
                }

                return ""

            try:
                problem = store.update(mutate)
            except Exception as error_:
                problem = f"저장 실패: {str(error_)[:150]}"

            if problem:
                st.error(problem)
            else:
                state["arena_flash"] = f"저장했습니다 · 선택 {sum(1 for c in choices if c)}/{len(choices)}개"
                st.rerun()

    if closed or judged:
        md(participation_html(data, key))

    st.caption(
        f"투표 {day.month}/{day.day} 08:00~08:59 · 승패: 그날 장마감(15:40 이후 확정) 등락률이 더 높은 종목 · "
        "선택 비율은 내가 저장했거나 마감된 뒤에만 보여요. 참가자들의 의견일 뿐 매수·매도 신호가 아닙니다."
    )


# ===============================================================
# 탭 2: 결과 · 내 기록
# ===============================================================

def render_results(data, nick):
    now = now_kst()

    if nick:
        stats = logic.user_stats(data).get(store.nick_key(nick))

        md(ui.frag(ui.section("내 기록", "👤")))

        if stats and stats["total"]:
            md(ui.frag(ui.stat_cards([
                {"k": "적중률", "v": f"{stats['hits'] / stats['total'] * 100:.0f}", "unit": "%", "s": "판정된 선택 기준"},
                {"k": "맞힌 대결", "v": str(stats["hits"]), "unit": f" / {stats['total']}"},
                {"k": "참여 라운드", "v": str(stats["rounds"]), "unit": "회"},
                {"k": "보정 점수", "v": f"{logic.wilson_lower(stats['hits'], stats['total']) * 100:.0f}", "s": "참여가 적어도 공정하게"},
            ])))

            if stats["by_reason"]:
                rows = [
                    [reason, f"{h} / {t}", f"{h / t * 100:.0f}%"]
                    for reason, (h, t) in sorted(stats["by_reason"].items(), key=lambda kv: -kv[1][1])
                ]
                md(ui.frag(ui.data_table(["선택 이유", "적중", "적중률"], rows, "minmax(0,2fr) minmax(0,1fr) minmax(0,1fr)")))
        else:
            st.caption("아직 판정이 끝난 예측이 없습니다. 결과는 대결일 장마감(15:40) 이후에 확정됩니다.")

    weeks = completed_weeks(data, now)

    if weeks:
        md(ui.frag(ui.section("주간 토너먼트 결과", "🏅")))

        for monday, standing in weeks[:4]:
            if standing["tied_all"]:
                headline = "전원 같은 성적이라 꼴찌 없이 마무리"
            else:
                headline = (
                    f"🥇 우승 {', '.join(standing['first'])} · "
                    f"🥄 꼴찌 {', '.join(standing['last'])} (꼴찌가 상품을 쏩니다)"
                )

            with st.expander(f"{week_label(monday)} 주간 · {headline}", expanded=(monday == weeks[0][0])):
                md(ui.frag(week_table_html(standing, mine=nick)))

    md(ui.frag(ui.section("최근 대결 결과", "📋")))

    days = sorted(data["rounds"], reverse=True)[:7]

    if not days:
        st.caption("아직 진행된 대결이 없습니다.")
        return

    for key in days:
        rec = data["rounds"][key]
        day = datetime.date.fromisoformat(key)
        state_name = rec.get("state", "open")
        label = {"judged": "결과 확정", "void": "무효", "open": "판정 대기"}.get(state_name, state_name)

        with st.expander(f"{day_text(day)} · {label}", expanded=(key == days[0])):
            md(participation_html(data, key))

            if state_name != "judged":
                st.caption(rec.get("note") or "장마감(15:40) 이후 결과가 확정됩니다.")
                continue

            standing = logic.day_standings(data, key)

            if standing and standing["tied_all"]:
                st.markdown("🤝 참가자 전원이 같은 성적이라 꼴찌 없이 마무리됐어요.")
            elif standing:
                st.markdown(
                    f"🥇 우승 **{', '.join(standing['first'])}** · "
                    f"🥄 꼴찌 **{', '.join(standing['last'])}** (꼴찌가 상품을 쏩니다)"
                )
            else:
                st.caption("이날은 판정된 선택이 있는 참가자가 없습니다.")

            mine = ((data["picks"].get(key) or {}).get(store.nick_key(nick)) or {}) if nick else {}
            choices = mine.get("choices") or []
            rows = []

            for index, (match, result) in enumerate(zip(rec["matches"], rec["results"])):
                def pct(value):
                    return "-" if value is None else f"{value:+.2f}%"

                winner = result["winner"]
                win_name = {
                    "a": match["a"]["name"],
                    "b": match["b"]["name"],
                    "draw": "무승부",
                    "void": "무효",
                }[winner]

                pick = choices[index] if index < len(choices) else None
                mark = "-"

                if pick in ("a", "b") and winner in ("a", "b"):
                    mark = "✅ 적중" if pick == winner else "❌ 미적중"
                elif pick in ("a", "b"):
                    mark = "제외"

                rows.append([
                    f"대결 {index + 1}",
                    f"{match['a']['name']} {pct(result['a_pct'])}",
                    f"{match['b']['name']} {pct(result['b_pct'])}",
                    win_name,
                    mark,
                ])

            md(ui.frag(ui.data_table(
                ["대결", "A", "B", "승자", "내 선택"],
                rows,
                "minmax(54px,.6fr) minmax(0,1.3fr) minmax(0,1.3fr) minmax(0,1fr) minmax(0,1fr)",
            )))


# ===============================================================
# 탭 3: 랭킹
# ===============================================================

def week_table_html(standing, mine=None, title="Top Users", sub="월~금 합산 · 많이 맞힌 순"):
    rows = []

    for row in standing["rows"]:
        cells = []

        for offset in range(5):
            cell = row["days"].get(offset)
            cells.append(f"{cell[0]}/{cell[0] + cell[1]}" if cell else "-")

        cells.append(str(row["hits"]))
        rows.append({"nick": row["nick"], "cells": cells})

    return ui.top_table(title, sub, ["순위", "닉네임", "월", "화", "수", "목", "금", "합계"], rows, mine=mine)


def render_board(key, entries, rows, headers, title, sub, nick, note=""):
    md(ui.frag(ui.podium(entries)))

    if nick:
        names = [row["nick"] for row in rows]

        if nick in names:
            md(ui.frag(ui.me_pill(f"내 순위 <b>{names.index(nick) + 1}위</b> · 전체 {len(rows)}명")))
        else:
            md(ui.frag(ui.me_pill(note or "아직 순위에 들지 않았어요")))

    show_all = len(rows) > 10 and st.toggle("전체 보기", key=f"arena_all_{key}")

    md(ui.frag(ui.top_table(title, sub, headers, rows if show_all else rows[:10], mine=nick)))


def render_week_tournament(data, nick):
    now = now_kst()
    monday = logic.week_start(now.date())
    standing = logic.week_standings(data, monday)
    complete = logic.week_complete(data, monday, now)

    st.caption(f"이번 주 ({week_label(monday)}) {'· 확정' if complete else '· 진행 중'}")

    if not standing:
        md(ui.frag(ui.podium([])))
        st.caption("아직 이번 주에 판정이 끝난 대결이 없습니다. 하루 결과가 확정되면 합산이 시작됩니다.")
    else:
        if complete and not standing["tied_all"]:
            md(ui.frag(ui.me_pill(
                f"🥇 주간 우승 <b>{ui.esc(', '.join(standing['first']))}</b> · "
                f"🥄 꼴찌 <b>{ui.esc(', '.join(standing['last']))}</b> (꼴찌가 상품을 쏩니다)"
            )))

        entries = [
            {"nick": r["nick"], "big": str(r["hits"]), "unit": "개", "label": "적중", "small": f"오답 {r['misses']}"}
            for r in standing["rows"][:3]
        ]
        md(ui.frag(ui.podium(entries)))

        names = [r["nick"] for r in standing["rows"]]

        if nick and nick in names:
            md(ui.frag(ui.me_pill(f"내 순위 <b>{names.index(nick) + 1}위</b> · 전체 {len(names)}명")))

        show_all = len(names) > 10 and st.toggle("전체 보기", key="arena_all_week")
        view = dict(standing)
        view["rows"] = standing["rows"] if show_all else standing["rows"][:10]
        md(ui.frag(week_table_html(view, mine=nick)))

    st.caption(
        "월~금 일간 대결의 맞힌 개수를 합산해 순위를 정합니다(많은 순, 같으면 오답이 적은 순). "
        "표의 월~금 칸은 '맞힌 개수/판정된 개수'입니다. 금요일 장마감(15:40) 이후 우승자와 꼴찌가 확정됩니다."
    )


def render_ranking(data, nick=None):
    today = now_kst().date()

    tab_week, tab_month, tab_all = st.tabs(tab_names("🏅 주간 토너먼트", "월간 (최근 30일)", "누적"))

    with tab_week:
        render_week_tournament(data, nick)

    views = (
        (tab_month, "month", (today - datetime.timedelta(days=30)).isoformat(), "최근 30일 · 보정 점수 순"),
        (tab_all, "all", None, "전체 기간 · 보정 점수 순"),
    )

    for tab, mode, since, sub in views:
        with tab:
            minimum = logic.MIN_RANKED[mode]
            ranked, waiting = logic.ranking(logic.user_stats(data, since), minimum)

            entries = [
                {
                    "nick": r["nick"],
                    "big": f"{r['rate'] * 100:.0f}",
                    "unit": "%",
                    "label": "적중률",
                    "small": f"{r['hits']} / {r['total']} 적중",
                }
                for r in ranked[:3]
            ]
            rows = [
                {
                    "nick": r["nick"],
                    "cells": [f"{r['hits']} / {r['total']}", f"{r['score'] * 100:.0f}", f"{r['rate'] * 100:.0f}%"],
                }
                for r in ranked
            ]

            if ranked:
                render_board(
                    mode, entries, rows, ["순위", "닉네임", "적중", "보정 점수", "적중률"],
                    "Top Users", sub, nick, f"판정된 대결 {minimum}개 이상부터 순위에 들어가요",
                )
            else:
                md(ui.frag(ui.podium([])))
                st.caption(f"판정된 대결이 {minimum}개 이상인 참가자부터 순위에 들어갑니다.")

            if waiting:
                with st.expander(f"집계 중 ({minimum}개 미만 · {len(waiting)}명)"):
                    md(ui.frag(ui.data_table(
                        ["닉네임", "적중"],
                        [[r["nick"], f"{r['hits']} / {r['total']}"] for r in waiting],
                    )))

    st.caption(
        "월간·누적 순위는 참여 횟수가 적은 사람이 우연히 100%가 되어도 1위가 되지 않도록 보정한 점수 순입니다. "
        "무승부·무효·선택하지 않은 대결은 집계하지 않습니다."
    )


# ===============================================================
# 탭 4: 관리자 (우승 상품 · PIN 초기화)
# ===============================================================

def render_admin(data):
    state = st.session_state
    expected = secret("admin_password")

    if not expected:
        st.info("Streamlit Secrets에 admin_password를 추가하면 관리자 기능을 쓸 수 있습니다.")
        return

    if not state.get("arena_admin"):
        with st.form("arena_admin_form"):
            entered = st.text_input("관리자 비밀번호", type="password")
            go = st.form_submit_button("확인")

        if go:
            if hmac.compare_digest(entered.encode(), expected.encode()):
                state["arena_admin"] = True
                st.rerun()

            st.error("관리자 비밀번호가 올바르지 않습니다.")

        return

    prizes = data["prizes"]
    items = [str(item) for item in prizes.get("list") or []] or list(store.DEFAULT_PRIZES)

    st.markdown("#### 🎁 우승 상품 목록")

    text = st.text_area(
        "상품 목록 (한 줄에 하나)",
        value="\n".join(items),
        height=160,
        key=f"arena_prize_text_{len(items)}",
    )

    if st.button("목록 저장", key="arena_prize_save"):
        new_items = [line.strip() for line in text.splitlines() if line.strip()]

        def mutate(d):
            d["prizes"]["list"] = new_items

        try:
            store.update(mutate)
            st.success(f"{len(new_items)}개 저장했습니다.")
            st.rerun()
        except Exception as error:
            st.error(f"저장 실패: {str(error)[:150]}")

    target = st.radio(
        "어느 토너먼트의 상품을 바꿀까요?",
        ["일간 토너먼트", "주간 토너먼트"],
        horizontal=True,
        key="arena_prize_target",
    )
    slot = "current" if target == "일간 토너먼트" else "weekly_current"

    if not items:
        st.caption("목록을 저장하면 랜덤 뽑기와 직접 선택을 쓸 수 있습니다.")
    else:
        col_rand, col_pick = st.columns(2)

        with col_rand:
            st.markdown("**🎲 랜덤으로 뽑기**")

            if st.button("랜덤 뽑기", key="arena_prize_random", type="primary", use_container_width=True):
                chosen = random.SystemRandom().choice(items)

                def mutate_random(d):
                    d["prizes"][slot] = {"name": chosen, "how": "random", "at": now_kst().isoformat()}

                try:
                    store.update(mutate_random)
                    st.balloons()
                    st.rerun()
                except Exception as error:
                    st.error(f"저장 실패: {str(error)[:150]}")

        with col_pick:
            st.markdown("**✋ 목록에서 직접 선택**")

            selected = st.selectbox("상품", items, key="arena_prize_select", label_visibility="collapsed")

            if st.button("이 상품으로 지정", key="arena_prize_set", use_container_width=True):
                def mutate_set(d):
                    d["prizes"][slot] = {"name": selected, "how": "manual", "at": now_kst().isoformat()}

                try:
                    store.update(mutate_set)
                    st.rerun()
                except Exception as error:
                    st.error(f"저장 실패: {str(error)[:150]}")

    if prizes.get(slot) and st.button(f"{target} 상품 표시 지우기", key="arena_prize_clear"):
        try:
            store.update(lambda d: d["prizes"].__setitem__(slot, None))
            st.rerun()
        except Exception as error:
            st.error(f"저장 실패: {str(error)[:150]}")

    st.divider()
    st.markdown("#### 🔑 PIN 초기화")

    users = sorted(user["nick"] for user in data["users"].values())

    if users:
        target = st.selectbox("닉네임", users, key="arena_reset_nick")
        new_pin = st.text_input("새 PIN (숫자 4자리)", type="password", max_chars=4, key="arena_reset_pin")

        if st.button("PIN 바꾸기", key="arena_reset_go"):
            problem = store.check_pin(new_pin)

            if problem:
                st.error(problem)
            else:
                def mutate_pin(d):
                    user = d["users"][store.nick_key(target)]
                    user["pin_hash"] = store.hash_pin(new_pin, user["salt"])
                    user["fails"] = 0
                    user["locked_until"] = ""

                try:
                    store.update(mutate_pin)
                    st.success("바꿨습니다. 새 PIN을 참가자에게 알려 주세요.")
                except Exception as error:
                    st.error(f"저장 실패: {str(error)[:150]}")
    else:
        st.caption("아직 가입한 참가자가 없습니다.")

    st.divider()

    if st.button("판정 다시 시도", key="arena_judge_force"):
        message = judge_pending(force=True)

        if message:
            st.error(message)
        else:
            st.success("판정을 확인했습니다. 다른 탭을 누르면 결과가 반영됩니다.")


# ===============================================================
# 화면
# ===============================================================

def main():
    now = now_kst()
    day = logic.active_round_date(now)

    md(ui.css_only())

    # 8시 전에는 대진을 만들지도 공개하지도 않습니다. (관리자 테스트 모드는 예외)
    if logic.phase(day, now) == "before" and not st.session_state.get("arena_admin"):
        data, round_error = store.load(), ""
    else:
        data, round_error = ensure_round(day)

    judge_error = judge_pending()

    # 판정이 끝났을 수 있으니 다시 읽음
    data = store.load()

    # 투표가 열리기 전(예: 장마감 후 저녁)에는 '내일 열려요' 안내 아래에 가장 최근 대결 결과를 계속 보여 줍니다.
    before_open = logic.phase(day, now) == "before" and not st.session_state.get("arena_admin")
    past_keys = [k for k in data["rounds"] if k < day.isoformat()] if before_open else []
    prev_day = datetime.date.fromisoformat(max(past_keys)) if past_keys else None
    view_day = prev_day or day

    monday = logic.week_start(view_day)
    friday = monday + datetime.timedelta(days=4)

    md(ui.frag(ui.hero(
        f"{day_text(monday)} – {day_text(friday)}",
        ["투표 08:00~08:59", "15:40 이후 확정", "꼴찌가 쏩니다"],
    )))

    render_round_strip(data, now, view_day)
    render_prize(data)

    nick = render_account(data)

    if judge_error:
        st.warning(judge_error)

    tab_play, tab_result, tab_rank, tab_admin = st.tabs(
        tab_names("🎯 오늘의 대결", "📋 결과·내 기록", "🏆 랭킹", "🔧 관리자")
    )

    with tab_play:
        render_play(data, nick, day, round_error)

        if prev_day:
            md(ui.frag(ui.section(f"{day_text(prev_day)} 대결 결과", "📋")))
            render_play(data, nick, prev_day, "")

    with tab_result:
        render_results(data, nick)

    with tab_rank:
        render_ranking(data, nick)

    with tab_admin:
        render_admin(data)

    st.caption(DISCLAIMER)


main()
