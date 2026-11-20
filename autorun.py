"""자동 실행 모드 공통 부분 (페이지 코드와 왼쪽 메뉴에서 같이 씁니다).

흐름: GitHub 예약 작업(10분마다)이 설정을 확인 → 실행할 때가 되면 가상 브라우저로
앱 주소 뒤에 ?auto=pre 를 붙여 열어 로그인 → 이 앱이 버튼을 누른 것처럼 생성·저장 →
결과를 실행 기록에 남깁니다. 생성은 앱 서버에서 하므로 토스 허용 IP는 그대로 씁니다.
"""
import requests
from toss_logo import TOSS_LOGO_B64
TOSS_CONSOLE_URL = "https://www.tossinvest.com/"

import datetime
from zoneinfo import ZoneInfo

import streamlit as st

from archive import (
    DEFAULT_TIMES,
    load_config,
    load_run,
    record_run,
    save_config,
    valid_hhmm,
)

KST = ZoneInfo("Asia/Seoul")

LABELS = {"pre": "장전 시황", "aft": "오후 시황", "week": "주간 정리"}

# 이 시간 안에 시작 기록이 있으면 같은 작업을 다시 시작하지 않습니다.
RUNNING_GUARD_MINUTES = 30


def _minutes_since(iso_text):
    try:
        then = datetime.datetime.fromisoformat(iso_text)
    except (TypeError, ValueError):
        return None

    return (datetime.datetime.now(KST) - then).total_seconds() / 60


def auto_requested(kind):
    """주소에 ?auto=<kind> 가 있고, 자동 실행이 켜져 있고, 오늘 성공 기록이 없을 때만 True."""

    if st.query_params.get("auto") != kind:
        return False

    try:
        if not load_config()["enabled"]:
            return False

        run = load_run(kind)

    except Exception:
        return False

    if not run:
        return True

    if run.get("ok") is True:
        return False

    # 다른 곳에서 실행 중인 것으로 보이면 건너뜀
    if run.get("ok") is None:
        minutes = _minutes_since(run.get("at"))

        if minutes is not None and minutes < RUNNING_GUARD_MINUTES:
            return False

    return True


def mark_started(kind):
    try:
        record_run(kind, None, "자동 실행 시작")
    except Exception:
        pass


def finish_auto(kind, ok, message):
    """실행 결과를 남깁니다. (기록 실패는 화면에만 알림)"""

    try:
        record_run(kind, ok, message)
    except Exception as error:
        st.warning(f"자동 실행 결과 기록 실패: {str(error)[:150]}")


# ---------------------------------------------------------------
# 왼쪽 메뉴: ON/OFF 와 오늘의 실행 현황
# ---------------------------------------------------------------

def _on_toggle():
    try:
        config = load_config()
        config["enabled"] = bool(st.session_state.get("auto_enabled"))
        save_config(config)
        st.session_state["auto_msg"] = ""
    except Exception as error:
        st.session_state["auto_msg"] = f"설정 저장 실패: {str(error)[:150]}"


def _status_line(kind, config):
    run = load_run(kind)
    scheduled = config["times"][kind]

    head = f"{LABELS[kind]} · 예정 {scheduled}"

    if run is None:
        return f"{head} · 오늘 대기 중"

    at = str(run.get("at", ""))[11:16]

    if run.get("ok") is True:
        return f"{head} · ✅ {at}에 생성·저장 완료"

    if run.get("ok") is False:
        return f"{head} · ❌ {at}에 실패"

    return f"{head} · ⏳ {at}부터 실행 중"


def _save_sidebar_times():
    state = st.session_state

    try:
        config = load_config()

        for kind in LABELS:
            value = state.get(f"sb_time_{kind}")

            if isinstance(value, datetime.time):
                text = value.strftime("%H:%M")

                if valid_hhmm(text):
                    config["times"][kind] = text

        save_config(config)
        state["auto_msg"] = "시각을 저장했습니다."

    except Exception as error:
        state["auto_msg"] = f"시각 저장 실패: {str(error)[:150]}"


def render_auto_sidebar():
    """app.py 에서 st.navigation 을 실행하기 전에 한 번 호출합니다. 로그인 후에만 보입니다."""

    if not st.session_state.get("pw_ok"):
        return
        
    with st.sidebar:
        st.markdown(
            f'<a href="{TOSS_CONSOLE_URL}" target="_blank" rel="noopener" '
            'style="display:flex;flex-direction:column;align-items:center;gap:6px;'
            'padding:10px 0 8px;background:#F7F7F7;border:2px solid #0064FF;'
            'border-radius:10px;text-decoration:none;">'
            f'<img src="data:image/png;base64,{TOSS_LOGO_B64}" '
            'style="width:124px;height:auto;display:block" alt="토스증권">'
            '<span style="color:#0F1B3D;font-weight:800;font-size:13px;">'
            '허용 IP 등록 바로가기</span></a>',
            unsafe_allow_html=True,
        )
        st.caption("로그인 → 설정 → Open API → 허용 IP 관리")

        if st.button("현재 서버 IP 확인", key="sb_show_ip", use_container_width=True):
            try:
                st.code(requests.get("https://api.ipify.org", timeout=5).text)
            except Exception as error:
                st.caption(f"IP 확인 실패: {str(error)[:80]}")
    
    try:
        config = load_config()
    except Exception as error:
        with st.sidebar:
            st.caption(f"자동 실행 설정을 불러오지 못했습니다: {str(error)[:120]}")
        return

    if "auto_enabled" not in st.session_state:
        st.session_state["auto_enabled"] = config["enabled"]

    with st.sidebar:
        st.markdown("**⏰ 자동 실행 모드**")

        st.toggle(
            "자동 실행",
            key="auto_enabled",
            on_change=_on_toggle,
            help=(
                "켜면 정해진 시각 이후에 시황글을 자동으로 만들어 저장합니다. "
                "끄면 예약된 실행이 건너뜁니다. 시각은 각 시황 페이지에서 바꿉니다."
            ),
        )

        if st.session_state.get("auto_msg"):
            st.caption(st.session_state["auto_msg"])

        if st.session_state.get("auto_enabled"):
            for kind in ("pre", "aft", "week"):
                st.caption(_status_line(kind, config))
        else:
            st.caption("꺼져 있음 · 자동으로 만들지 않습니다")

        with st.expander("실행 시각 바꾸기", expanded=False):
            for kind in ("pre", "aft", "week"):
                key = f"sb_time_{kind}"

                if key not in st.session_state:
                    st.session_state[key] = _parse_time(config["times"][kind])

                st.time_input(LABELS[kind], key=key, step=300)

            st.button(
                "저장",
                key="sb_time_save",
                on_click=_save_sidebar_times,
                use_container_width=True,
            )

            st.caption(
                "시각 이후 10~20분 안에 시작합니다. 주간 정리는 금요일만 실행됩니다."
            )


# ---------------------------------------------------------------
# 각 시황 페이지의 '자동 실행 시각' 설정 칸
# ---------------------------------------------------------------

def _parse_time(text):
    hour, minute = str(text).split(":")

    return datetime.time(int(hour), int(minute))


def _save_time(kind):
    state = st.session_state
    value = state.get(f"auto_time_{kind}")

    if not isinstance(value, datetime.time):
        return

    text = value.strftime("%H:%M")

    if not valid_hhmm(text):
        return

    try:
        config = load_config()
        config["times"][kind] = text
        save_config(config)
        state[f"auto_time_msg_{kind}"] = f"저장했습니다 · 이제 {text} 이후에 자동 실행됩니다."
    except Exception as error:
        state[f"auto_time_msg_{kind}"] = f"저장 실패: {str(error)[:150]}"


def render_time_setting(kind, note=""):
    """'자동 실행 시작 시각' 칸 + 저장 버튼. 기본값은 14:30(오후), 08:00(장전), 금 15:40(주간)."""

    try:
        saved = load_config()["times"][kind]
    except Exception:
        saved = DEFAULT_TIMES[kind]

    key = f"auto_time_{kind}"

    if key not in st.session_state:
        st.session_state[key] = _parse_time(saved)

    with st.expander(f"⏰ {LABELS[kind]} 자동 실행 시각", expanded=False):
        col_time, col_btn = st.columns([2, 1], vertical_alignment="bottom")

        col_time.time_input(
            "시작 시각 (이 시각 이후 첫 확인 때 실행)",
            key=key,
            step=300,
        )

        col_btn.button(
            "저장",
            key=f"auto_time_save_{kind}",
            on_click=_save_time,
            args=(kind,),
            use_container_width=True,
        )

        if st.session_state.get(f"auto_time_msg_{kind}"):
            st.caption(st.session_state[f"auto_time_msg_{kind}"])

        st.caption(
            f"기본값 {DEFAULT_TIMES[kind]} · 예약 작업이 10분마다 확인하므로 "
            "실제 시작은 최대 10~20분 늦을 수 있습니다. "
            "왼쪽 메뉴의 '자동 실행' 스위치가 켜져 있어야 실행됩니다."
            + (f" {note}" if note else "")
        )
