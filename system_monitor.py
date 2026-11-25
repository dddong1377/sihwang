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


# ENHANCED MONITOR UI
st.divider()
st.subheader("고급 서버 관제")

refresh = st.selectbox("자동 갱신 간격 (고급 관제 영역)", ["수동", "15초", "30초", "60초"], index=0)
interval = {"수동": None, "15초": "15s", "30초": "30s", "60초": "60s"}[refresh]


def render_enhanced():
    if not st.session_state.get("monitor_authenticated", False):
        return
    if st.button("고급 관제 새로고침", key="enhanced_refresh"):
        pass
    try:
        data = monitor_request("/v1/monitor/enhanced")
    except Exception:
        st.error("고급 관제 API 조회 실패. Oracle API 설치 및 연결 상태를 확인하세요.")
        return
    st.caption("조회 시각: " + datetime.fromtimestamp(data["timestamp"], kst).strftime("%Y-%m-%d %H:%M:%S KST"))
    tls = data["tls"]
    st.markdown("**SSL 인증서**")
    if tls.get("verified"):
        expiry = datetime.fromtimestamp(tls["expires_at"], kst)
        a, b = st.columns(2)
        a.metric("인증서 만료일 (KST)", expiry.strftime("%m/%d %H:%M"))
        b.metric("남은 기간", f'{tls["days_left"]:.2f}일')
        if tls["days_left"] <= 2:
            st.warning("인증서 만료 임박: 자동 갱신 상태 확인 필요")
        else:
            st.success("서버 인증서 검증 성공")
    else:
        st.warning("서버 인증서 검증 실패")
    st.markdown("**HTTP 요청 및 응답 성능**")
    http = data["http"]
    a, b, c = st.columns(3)
    a.metric("집계 요청", http["total"])
    b.metric("HTTP 4xx/5xx", http["errors"])
    c.metric("측정 표본", http["sample_count"])
    a, b, c = st.columns(3)
    for col, label, key in [(a, "평균", "mean_ms"), (b, "최대", "max_ms"), (c, "P95", "p95_ms")]:
        value = http.get(key)
        col.metric(label, "—" if value is None else f"{value:.2f} ms")
    st.caption("백엔드 재시작 이후 지정 API 집계 · 응답시간은 최근 최대 2,048건, 서버 내부 처리시간 기준")
    counts = http.get("status_counts", {})
    if counts:
        st.dataframe(pd.DataFrame([{"HTTP 상태": k, "요청 수": v} for k, v in sorted(counts.items())]), hide_index=True, use_container_width=True)
    st.markdown("**WebSocket 수신 최신성**")
    ws = data["websocket"]
    state = ws.get("freshness_state")
    if state == "recent_message":
        st.success("최근 메시지 수신 확인")
    elif state == "stale_or_no_trade":
        st.warning("연결 중이나 최근 메시지 없음: 거래 공백 또는 수신 지연 가능")
    elif state == "disconnected":
        st.warning("WebSocket 연결 끊김")
    else:
        st.info("실시간 수집 대기 또는 구독 없음")
    age = ws.get("age")
    st.metric("마지막 메시지 이후", "—" if age is None else f"{age:.1f}초")
    st.caption("전체 피드 기준이며 개별 종목의 최신 체결을 보장하지 않습니다. 장 운영 여부는 자동 판별하지 않습니다.")


if hasattr(st, "fragment"):
    st.fragment(run_every=interval)(render_enhanced)()
else:
    st.caption("현재 Streamlit 버전에서는 자동 갱신을 지원하지 않습니다.")
    render_enhanced()


# NETWORK MONITOR UI
st.divider()
st.subheader("네트워크 트래픽 분석")
st.caption("서버 전체 인터페이스 누적 카운터의 차분 기준 · OCI 청구 트래픽 또는 토스 API 전용 트래픽과는 다릅니다.")


def render_network_monitor():
    if not st.session_state.get("monitor_authenticated", False):
        return
    try:
        rows = load_history(168)["data"]
        net = pd.DataFrame(rows)
        required = ["timestamp", "uptime_seconds", "network_sent_bytes", "network_recv_bytes"]
        if len(net) < 2 or not all(c in net.columns for c in required):
            st.info("네트워크 분석에는 최소 두 건의 측정 기록이 필요합니다.")
            return
        net = net[required].apply(pd.to_numeric, errors="coerce").dropna()
        net = net.sort_values("timestamp").drop_duplicates("timestamp")
        elapsed = net["timestamp"].diff()
        uptime_delta = net["uptime_seconds"].diff()
        sent = net["network_sent_bytes"].diff()
        recv = net["network_recv_bytes"].diff()
        valid = (elapsed > 0) & (elapsed <= 180) & ((uptime_delta - elapsed).abs() <= 2) & (sent >= 0) & (recv >= 0)
        skipped = int((~valid).sum()) - 1
        net["송신 MiB"] = sent.where(valid) / 1048576
        net["수신 MiB"] = recv.where(valid) / 1048576
        net["합계 MiB/분"] = ((sent + recv) / 1048576 * 60 / elapsed).where(valid)
        net["시간"] = pd.to_datetime(net["timestamp"], unit="s", utc=True).dt.tz_convert("Asia/Seoul").dt.tz_localize(None)
        net = net.set_index("시간")
        traffic = net[["송신 MiB", "수신 MiB"]].dropna()
        if traffic.empty:
            st.info("유효한 연속 측정 구간이 아직 없습니다.")
            return
        a, b, c = st.columns(3)
        a.metric("최근 7일 이내 송신", f'{traffic["송신 MiB"].sum():,.2f} MiB')
        b.metric("최근 7일 이내 수신", f'{traffic["수신 MiB"].sum():,.2f} MiB')
        c.metric("합계", f'{traffic.sum().sum():,.2f} MiB')
        st.caption(f"유효 구간 {len(traffic):,}건 · 제외 구간 {max(0, skipped):,}건. 재부팅, 카운터 감소, 3분 초과 기록 공백은 제외합니다. 구간 사용량은 종료 시각에 귀속됩니다.")
        period_net = st.radio("트래픽 집계 단위", ["시간별", "일별"], horizontal=True, key="network_period")
        grouped = traffic.resample("h" if period_net == "시간별" else "D").sum(min_count=1)
        st.bar_chart(grouped)
        st.caption("미측정 구간은 사용량 0으로 간주하지 않습니다. 최초 기록 이전의 트래픽은 포함되지 않습니다.")
        rates = net["합계 MiB/분"].dropna()
        if len(rates) >= 31:
            latest = float(rates.iloc[-1])
            baseline = float(rates.iloc[-31:-1].median())
            st.metric("최근 유효 구간 트래픽", f"{latest:.2f} MiB/분")
            if baseline > 0 and latest >= max(5 * baseline, 10):
                st.warning("트래픽 증가 감지: 직전 30개 유효 구간 중앙값의 5배 이상이며 10 MiB/분 이상입니다.")
            else:
                st.info("설정한 트래픽 증가 기준에 해당하지 않습니다.")
            st.caption("초기 참고 기준이며 보안 이상이나 API 과다 호출을 확정하지 않습니다.")
        else:
            st.info("증가 감지 기준을 계산하려면 유효 구간 31건 이상이 필요합니다.")
    except Exception:
        st.error("네트워크 기록 조회 또는 분석에 실패했습니다.")


render_network_monitor()
