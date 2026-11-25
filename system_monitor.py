import hmac
import requests
import pandas as pd
import streamlit as st

from datetime import datetime
from zoneinfo import ZoneInfo

st.title("🖥️ 서버 관제 시스템")
st.caption("SIHWANG SYSTEM MONITOR | Oracle Cloud")


# 관리자 인증

try:
    admin_password = str(st.secrets["monitor_password"])
except Exception:
    st.error("Secrets에 monitor_password를 설정하세요.")
    st.stop()

if not admin_password:
    st.error("관리자 비밀번호를 설정하세요.")
    st.stop()

if not st.session_state.get("monitor_authenticated", False):

    with st.form("monitor_login"):
        password = st.text_input(
            "관리자 비밀번호",
            type="password"
        )
        submitted = st.form_submit_button("관리자 로그인")

    if submitted:
        if hmac.compare_digest(
            password.encode(),
            admin_password.encode()
        ):
            st.session_state["monitor_authenticated"] = True
            st.rerun()
        else:
            st.error("비밀번호가 일치하지 않습니다.")

    st.stop()

if st.button("관리자 로그아웃"):
    st.session_state["monitor_authenticated"] = False
    st.rerun()


# Oracle API 연결

def monitor_request(path, params=None):

    base = str(st.secrets["MTNW_API_URL"]).rstrip("/")
    key = str(st.secrets["MTNW_API_KEY"])

    if not base.startswith("https://"):
        raise ValueError("HTTPS is required")

    response = requests.get(
        base + path,
        headers={"X-API-Key": key},
        params=params,
        timeout=(5, 15),
        allow_redirects=False
    )

    response.raise_for_status()
    return response.json()


@st.cache_data(ttl=15, show_spinner=False)
def load_overview():
    return monitor_request("/v1/monitor/overview")


@st.cache_data(ttl=60, show_spinner=False)
def load_history(hours):
    return monitor_request(
        "/v1/monitor/history",
        {"hours": hours}
    )


if st.button("🔄 서버 상태 새로고침"):
    load_overview.clear()
    load_history.clear()
    st.rerun()


try:
    overview = load_overview()
except Exception:
    st.error("Oracle 서버 상태를 조회할 수 없습니다.")
    st.stop()

system = overview["system"]
cache = overview["cache"]
ws = overview["websocket"]

kst = ZoneInfo("Asia/Seoul")

updated = datetime.fromtimestamp(
    overview["timestamp"], kst
)

st.caption(
    "최종 조회: "
    + updated.strftime("%Y-%m-%d %H:%M:%S")
    + " KST"
)


# 서버 상태

if overview["status"] == "normal":
    st.success("서버 자원 사용 상태 정상")
else:
    st.warning("서버 자원 사용량 점검 필요")


# CPU RAM DISK SWAP

st.subheader("서버 자원 사용량")

cols = st.columns(2)

metrics = [
    ("CPU 사용률", "cpu_percent"),
    ("RAM 사용률", "memory_percent"),
    ("디스크 사용률", "disk_percent"),
    ("Swap 사용률", "swap_percent")
]

for i, (label, key) in enumerate(metrics):

    value = float(system[key])

    with cols[i % 2]:
        st.metric(label, f"{value:.1f}%")
        st.progress(max(0.0, min(value / 100, 1.0)))


# 상세 서버 정보

st.subheader("서버 상세 정보")

c1, c2, c3 = st.columns(3)

c1.metric(
    "가용 메모리",
    f'{system["memory_available_mb"]:.0f} MiB'
)

c2.metric(
    "남은 디스크",
    f'{system["disk_free_gb"]:.2f} GiB'
)

c3.metric(
    "1분 평균 부하",
    f'{system["load_1"]:.2f}'
)

uptime = int(system["uptime_seconds"])
days, rest = divmod(uptime, 86400)
hours, rest = divmod(rest, 3600)
minutes = rest // 60

st.caption(
    f"서버 가동 시간: {days}일 {hours}시간 {minutes}분"
)


# API 캐시 통계

st.divider()
st.subheader("REST API 및 캐시 현황")

c1, c2 = st.columns(2)

total = cache["hits"] + cache["misses"]

c1.metric("캐시 조회 요청", f"{total:,}회")

c2.metric(
    "상위 API 호출 함수 실행",
    f'{cache["upstream_calls"]:,}회'
)

c1.metric(
    "캐시 적중률",
    f'{cache["hit_rate"]:.1f}%'
)

c2.metric(
    "캐시 저장 항목",
    f'{cache["cache_items"]:,}개'
)

st.caption(
    f'캐시 데이터 크기: {cache["cache_bytes"]/1024:.2f} KiB'
)

st.caption("통계는 백엔드 재시작 이후 누적 기준")


# WebSocket

st.divider()
st.subheader("WebSocket 실시간 수집 상태")

if not ws.get("initialized"):
    st.info("실시간 시세 연결이 아직 시작되지 않았습니다.")
elif ws.get("connected"):
    st.success("WebSocket 연결 정상")
else:
    st.warning("WebSocket 미연결 상태")

c1, c2, c3 = st.columns(3)

c1.metric("요청 종목", ws.get("wanted", 0))
c2.metric("구독 종목", ws.get("subscribed", 0))
c3.metric("저장된 체결가", ws.get("priced", 0))

age = ws.get("age")

if age is not None:
    st.caption(f"마지막 수신 이후: {age:.1f}초")

if ws.get("error"):
    st.warning("WebSocket 연결 오류 기록이 있습니다.")


# SQLite 자원 사용 이력

st.divider()
st.subheader("서버 자원 사용 추이")

period = st.radio(
    "조회 기간",
    ["1시간", "24시간", "3일", "7일"],
    horizontal=True,
    index=1
)

hours = {
    "1시간": 1,
    "24시간": 24,
    "3일": 72,
    "7일": 168
}[period]

try:
    history = load_history(hours)
    df = pd.DataFrame(history["data"])

    if df.empty:
        st.info("아직 저장된 데이터가 없습니다.")
    else:
        df["시간"] = (
            pd.to_datetime(
                df["timestamp"],
                unit="s",
                utc=True
            )
            .dt.tz_convert("Asia/Seoul")
            .dt.tz_localize(None)
        )

        df = df.set_index("시간").sort_index()

        chart = df[[
            "cpu_percent",
            "memory_percent",
            "disk_percent",
            "swap_percent"
        ]].rename(columns={
            "cpu_percent": "CPU",
            "memory_percent": "RAM",
            "disk_percent": "DISK",
            "swap_percent": "SWAP"
        })

        st.line_chart(chart)

        st.caption(
            f"실제 저장 기록: {len(df):,}건"
        )

        c1, c2 = st.columns(2)

        c1.metric(
            "평균 CPU 사용률",
            f'{df["cpu_percent"].mean():.1f}%'
        )

        c2.metric(
            "최대 RAM 사용률",
            f'{df["memory_percent"].max():.1f}%'
        )

        with st.expander("상세 측정 기록"):
            st.dataframe(
                chart.sort_index(ascending=False)
            )

except Exception:
    st.error("서버 사용 기록을 조회할 수 없습니다.")


# 자원 경고

st.divider()
st.subheader("서버 이상 감지")

warning_names = {
    "HIGH_CPU": "CPU 사용률 80% 이상",
    "HIGH_MEMORY": "RAM 사용률 80% 이상",
    "HIGH_DISK": "디스크 사용률 80% 이상",
    "HIGH_SWAP": "Swap 사용률 25% 이상"
}

warnings = overview.get("warnings", [])

if not warnings:
    st.success("현재 자원 사용량 경고 없음")
else:
    for warning in warnings:
        st.warning(
            warning_names.get(warning, warning)
        )
