import datetime
import hmac
import os
import sys
from pathlib import Path
import random
import time
from zoneinfo import ZoneInfo

import streamlit as st

# st.navigation()으로 실행된 페이지에서도 프로젝트 루트 모듈을 찾도록 설정
PROJECT_ROOT = str(Path(__file__).resolve().parent)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# 프로젝트 루트 내 파일을 Python 표준 import로 불러옵니다.
# 이전 진단본의 importlib 직접 실행 방식은 제거했습니다.
try:
    import arena_logic as logic
    import arena_store as store
except Exception as exc:
    import traceback
    st.error(f"주식 대결 모듈 로딩 오류: {type(exc).__name__}: {exc}")
    st.code("\n".join(f"{name}: {(Path(PROJECT_ROOT) / (name + '.py')).exists()}" for name in ("arena_logic", "arena_store")))
    st.code(traceback.format_exc(), language="text")
    st.stop()

from weekly_market import candle_closes, fetch_cap_top

st.set_page_config(page_title="MTNW 주식 대결 (베타)", layout="wide")

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

    tab_in, tab_new = st.tabs(["로그인", "처음이에요"])

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


def render_prize(data):
    current = data["prizes"].get("current")

    with st.container(border=True):
        if current and current.get("name"):
            how = "🎲 랜덤 추첨" if current.get("how") == "random" else "✋ 관리자 지정"
            st.markdown(f"### 🎁 이번 우승 상품: **{current['name']}**")
            st.caption(how)
        else:
            st.markdown("### 🎁 우승 상품")
            st.caption("상품은 준비 중입니다.")

        st.markdown("**🍀 꼴찌가 상품을 쏩니다!**")

        key, result = latest_standings(data)

        if result:
            day = datetime.date.fromisoformat(key)

            if result["tied_all"]:
                st.caption(f"{day_text(day)}: 참가자 전원이 같은 성적이라 꼴찌 없이 마무리됐어요.")
            else:
                st.caption(
                    f"{day_text(day)} 우승 {', '.join(result['first'])} · "
                    f"꼴찌 {', '.join(result['last'])} 🥄"
                )
        else:
            st.caption("매일 결과가 확정되면 그날의 우승자와 꼴찌가 여기에 표시됩니다.")


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

    st.markdown(f"#### {day_text(day)} 대결")
    st.caption(
        f"투표 시간 {day.month}/{day.day} 08:00 ~ 08:59 (09:00이 되면 바로 마감, 그 전에는 몇 번이든 바꿀 수 있어요) · "
        "승패 기준: 그날 장마감(15:40 이후 확정) 등락률이 더 높은 종목"
    )

    if phase == "before":
        st.info(
            f"⏰ {day_text(day)} 대결은 **오전 8시**에 열립니다. "
            "8시 정각에 오늘의 5대결이 공개되고, 8시 59분까지 투표할 수 있어요."
        )

        return

    if not rec:
        st.warning(error or "대진을 준비 중입니다.")

        if st.button("대진 다시 만들기", key="arena_retry_round"):
            st.rerun()

        return

    if not nick:
        st.info("닉네임으로 로그인하면 예측을 저장할 수 있습니다. 아래는 오늘의 대진입니다.")

        for index, match in enumerate(rec["matches"], start=1):
            st.markdown(f"**대결 {index}** · {match['a']['name']} vs {match['b']['name']}")

        return

    nkey = store.nick_key(nick)
    mine = (data["picks"].get(key) or {}).get(nkey) or {}
    prev_choices = mine.get("choices") or []
    prev_reasons = mine.get("reasons") or []

    if closed:
        st.warning("투표가 마감되었습니다(09:00). 결과는 장마감(15:40) 이후 확정됩니다.")

    values = []
    reasons = []

    with st.form(f"arena_form_{nkey}_{key}"):
        for index, match in enumerate(rec["matches"]):
            names = [match["a"]["name"], match["b"]["name"]]
            prev = prev_choices[index] if index < len(prev_choices) else None
            prev_index = {"a": 0, "b": 1}.get(prev)

            col_pick, col_reason = st.columns([3, 2])

            values.append(col_pick.radio(
                f"대결 {index + 1}",
                names,
                index=prev_index,
                horizontal=True,
                disabled=closed,
                key=f"arena_pick_{nkey}_{key}_{index}",
            ))

            prev_reason = prev_reasons[index] if index < len(prev_reasons) else logic.REASONS[0]
            reason_index = logic.REASONS.index(prev_reason) if prev_reason in logic.REASONS else 0

            reasons.append(col_reason.selectbox(
                "선택 이유 (선택)",
                logic.REASONS,
                index=reason_index,
                disabled=closed,
                key=f"arena_reason_{nkey}_{key}_{index}",
            ))

        submitted = st.form_submit_button("예측 저장", type="primary", disabled=closed, use_container_width=True)

    if submitted:
        choices = []

        for value, match in zip(values, rec["matches"]):
            if value == match["a"]["name"]:
                choices.append("a")
            elif value == match["b"]["name"]:
                choices.append("b")
            else:
                choices.append(None)

        def mutate(d):
            current_phase = logic.phase(day, now_kst())

            if current_phase == "closed":
                return "09:00이 지나 투표가 마감되어 저장하지 못했습니다."

            if current_phase == "before":
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
        except Exception as error:
            problem = f"저장 실패: {str(error)[:150]}"

        if problem:
            st.error(problem)
        else:
            st.success(f"저장했습니다 · 선택 {sum(1 for c in choices if c)}/{len(choices)}개")
            data = store.load()

    # 선택 현황은 본인이 선택을 저장했거나 마감된 뒤에만 보여 줍니다(쏠림 방지).
    has_pick = bool((data["picks"].get(key) or {}).get(nkey))

    if has_pick or closed:
        st.markdown("##### 참가자 선택 현황")

        for index, match in enumerate(rec["matches"]):
            share = logic.pick_share(data, key, index)
            total = share["a"] + share["b"]

            if total:
                st.markdown(
                    f"- 대결 {index + 1}: {match['a']['name']} **{share['a'] / total * 100:.0f}%** "
                    f"({share['a']}명) · {match['b']['name']} **{share['b'] / total * 100:.0f}%** ({share['b']}명)"
                )
            else:
                st.markdown(f"- 대결 {index + 1}: 아직 선택한 사람이 없습니다.")

        st.caption("선택 비율은 참가자들의 의견일 뿐 매수·매도 신호나 투자 지표가 아닙니다.")


# ===============================================================
# 탭 2: 결과 · 내 기록
# ===============================================================

def render_results(data, nick):
    import pandas as pd

    now = now_kst()

    if nick:
        stats = logic.user_stats(data).get(store.nick_key(nick))

        st.markdown("#### 내 기록")

        if stats and stats["total"]:
            col1, col2, col3, col4 = st.columns(4)
            col1.metric("적중률", f"{stats['hits'] / stats['total'] * 100:.0f}%")
            col2.metric("맞힌 대결", f"{stats['hits']} / {stats['total']}")
            col3.metric("참여 라운드", f"{stats['rounds']}회")
            col4.metric("보정 점수", f"{logic.wilson_lower(stats['hits'], stats['total']) * 100:.0f}")

            if stats["by_reason"]:
                rows = [
                    {"선택 이유": reason, "적중": f"{h} / {t}", "적중률": f"{h / t * 100:.0f}%"}
                    for reason, (h, t) in sorted(stats["by_reason"].items(), key=lambda kv: -kv[1][1])
                ]
                st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
        else:
            st.caption("아직 판정이 끝난 예측이 없습니다. 결과는 대결일 장마감(15:40) 이후에 확정됩니다.")

    st.markdown("#### 최근 대결 결과")

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

                rows.append({
                    "대결": index + 1,
                    "A": f"{match['a']['name']} {pct(result['a_pct'])}",
                    "B": f"{match['b']['name']} {pct(result['b_pct'])}",
                    "승자": win_name,
                    "내 선택": mark,
                })

            st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)


# ===============================================================
# 탭 3: 랭킹
# ===============================================================

def render_ranking(data):
    import pandas as pd

    today = now_kst().date()

    views = (
        ("주간 (최근 7일)", "week", (today - datetime.timedelta(days=7)).isoformat()),
        ("월간 (최근 30일)", "month", (today - datetime.timedelta(days=30)).isoformat()),
        ("누적", "all", None),
    )

    tabs = st.tabs([view[0] for view in views])

    for tab, (_, mode, since) in zip(tabs, views):
        with tab:
            minimum = logic.MIN_RANKED[mode]
            ranked, waiting = logic.ranking(logic.user_stats(data, since), minimum)

            if ranked:
                rows = [
                    {
                        "순위": rank,
                        "닉네임": row["nick"],
                        "적중률": f"{row['rate'] * 100:.0f}%",
                        "적중": f"{row['hits']} / {row['total']}",
                        "보정 점수": f"{row['score'] * 100:.0f}",
                    }
                    for rank, row in enumerate(ranked, start=1)
                ]
                st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
            else:
                st.caption(f"판정된 대결이 {minimum}개 이상인 참가자부터 순위에 들어갑니다.")

            if waiting:
                with st.expander(f"집계 중 ({minimum}개 미만 · {len(waiting)}명)"):
                    st.dataframe(
                        pd.DataFrame([
                            {"닉네임": r["nick"], "적중": f"{r['hits']} / {r['total']}"}
                            for r in waiting
                        ]),
                        hide_index=True,
                        use_container_width=True,
                    )

    st.caption(
        "순위는 참여 횟수가 적은 사람이 우연히 100%가 되어도 1위가 되지 않도록 보정한 점수 순입니다. "
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

    if not items:
        st.caption("목록을 저장하면 랜덤 뽑기와 직접 선택을 쓸 수 있습니다.")
    else:
        col_rand, col_pick = st.columns(2)

        with col_rand:
            st.markdown("**🎲 랜덤으로 뽑기**")

            if st.button("랜덤 뽑기", key="arena_prize_random", type="primary", use_container_width=True):
                chosen = random.SystemRandom().choice(items)

                def mutate_random(d):
                    d["prizes"]["current"] = {"name": chosen, "how": "random", "at": now_kst().isoformat()}

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
                    d["prizes"]["current"] = {"name": selected, "how": "manual", "at": now_kst().isoformat()}

                try:
                    store.update(mutate_set)
                    st.rerun()
                except Exception as error:
                    st.error(f"저장 실패: {str(error)[:150]}")

    if prizes.get("current") and st.button("상품 표시 지우기", key="arena_prize_clear"):
        try:
            store.update(lambda d: d["prizes"].__setitem__("current", None))
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
    st.title("MTNW 주식 대결 🥊 (베타)")
    st.caption(DISCLAIMER)

    now = now_kst()
    day = logic.active_round_date(now)

    # 8시 전에는 대진을 만들지도 공개하지도 않습니다.
    if logic.phase(day, now) == "before":
        data, round_error = store.load(), ""
    else:
        data, round_error = ensure_round(day)

    judge_error = judge_pending()

    # 판정이 끝났을 수 있으니 다시 읽음
    data = store.load()

    render_prize(data)

    nick = render_account(data)

    if judge_error:
        st.warning(judge_error)

    tab_play, tab_result, tab_rank, tab_admin = st.tabs(
        ["🎯 오늘의 대결", "📋 결과·내 기록", "🏆 랭킹", "🔧 관리자"]
    )

    with tab_play:
        render_play(data, nick, day, round_error)

    with tab_result:
        render_results(data, nick)

    with tab_rank:
        render_ranking(data)

    with tab_admin:
        render_admin(data)


main()
