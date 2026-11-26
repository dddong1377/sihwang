"""Independent offline market testing page."""

import streamlit as st
from market_fixture import build_fixture, SCENARIOS

st.set_page_config(
    page_title="SIHWANG TEST LAB",
    layout="wide",
)

LABELS = {
    "normal": "일반 시장",
    "bull": "강한 상승장",
    "bear": "급락장",
    "mixed": "혼조세",
    "missing": "종목 데이터 누락",
    "ambiguous": "기준가 불확실",
}

st.title("SIHWANG TEST LAB")

st.warning(
    "가상 데이터 테스트 전용입니다. "
    "실제 금융정보가 아니며 외부 게시용으로 사용할 수 없습니다."
)

st.caption("외부 금융 API 및 데이터베이스를 사용하지 않습니다.")

state = st.session_state

state.setdefault("fixture_revision", 0)

scenario = st.selectbox(
    "시장 테스트 시나리오",
    SCENARIOS,
    format_func=lambda value: LABELS[value],
)

if st.button("가상 시장 데이터 갱신", type="primary"):
    state["fixture_revision"] += 1

revision = state["fixture_revision"]

fixture_key = (scenario, revision)

if state.get("fixture_key") != fixture_key:
    state["fixture_data"] = build_fixture(scenario, revision)
    state["fixture_key"] = fixture_key

snapshot = state["fixture_data"]
quality = snapshot["quality"]

st.caption(
    f"테스트 조회 시각: {snapshot['fetched_at']} "
    f"| 갱신 횟수: {revision}"
)

st.subheader("시장 지표")

columns = st.columns(2)

for col, key, label in zip(
    columns,
    ("kospi", "kosdaq"),
    ("코스피", "코스닥"),
):
    row = snapshot["indices"][key]
    col.metric(
        label,
        f"{row['price']:,.2f}",
        f"{row['pct']:+.2f}%",
    )

with st.expander("시장 데이터 품질 진단", expanded=True):
    c1, c2, c3, c4 = st.columns(4)

    c1.metric("분석 대상", quality["expected"])
    c2.metric("확보 종목", quality["available"])
    c3.metric(
        "데이터 확보율",
        f"{quality['available'] / quality['expected'] * 100:.1f}%",
    )
    c4.metric("기준가 추가 확인", len(quality["weak"]))

    if quality["missing"]:
        st.warning(
            "데이터 누락: " + ", ".join(quality["missing"])
        )

    if quality["weak"]:
        st.warning(
            "기준가 추가 확인: " + ", ".join(quality["weak"])
        )

    if quality["complete"]:
        st.success("가상 시장 데이터 기본 검사 통과")

    st.info("가상 데이터이므로 실제 게시 기능은 제공하지 않습니다.")

def display_stocks(rows):
    return [
        {
            "종목명": row["name"],
            "시총 순위": row["rank"],
            "현재가": row["price"],
            "등락률": f"{row['pct']:+.2f}%",
            "산업": row["theme"],
        }
        for row in rows
    ]

up_col, down_col = st.columns(2)

with up_col:
    st.subheader("가상 급등 TOP 5")
    st.dataframe(
        display_stocks(snapshot["gainers"]),
        hide_index=True,
    )

with down_col:
    st.subheader("가상 급락 TOP 5")
    st.dataframe(
        display_stocks(snapshot["losers"]),
        hide_index=True,
    )

st.subheader("가상 강세 산업")

for sector in snapshot["sector_items"]:
    with st.expander(
        f"{sector['name']} ({sector['pct']:+.2f}%)"
    ):
        st.dataframe(
            display_stocks(sector["stocks"]),
            hide_index=True,
        )

st.subheader("수동 작성 내용 보호")

st.text_area(
    "주요 경제 이슈",
    key="fixture_manual_issue",
    help="가상 시장 데이터를 갱신해도 입력값이 유지되어야 합니다.",
)

st.text_area(
    "내일 체크 포인트",
    key="fixture_manual_checks",
)


from content_refresh import refresh_content
import datetime
from zoneinfo import ZoneInfo


from offline_analysis import (
    generate_themes as fixture_themes,
    generate_summary as fixture_summary,
    generate_keywords as fixture_keywords,
    record_generation as fixture_recorder,
    freshness as fixture_freshness,
    load_sectors as fixture_sector_loader,
)


def fixture_refresh_callback():

    snapshot = st.session_state["fixture_data"]

    working = dict(
        st.session_state.get("fixture_content", {})
    )

    for key in ("themes", "summary", "keywords"):
        widget_key = "fixture_content_" + key

        if widget_key in st.session_state:
            working[key] = st.session_state[widget_key]

    report = refresh_content(
        working,
        snapshot,
        {
            "themes": fixture_themes,
            "summary": fixture_summary,
            "keywords": fixture_keywords,
        },
        fixture_recorder,
        sector_loader=fixture_sector_loader,
    )

    st.session_state["fixture_content"] = working
    st.session_state["fixture_refresh_report"] = report

    for key in ("themes", "summary", "keywords"):
        st.session_state["fixture_content_" + key] = (
            working.get(key, "")
        )


st.divider()
st.subheader("시황 콘텐츠 일괄 갱신")

st.caption(
    "가상 시장 데이터를 기반으로 자동 생성하며 "
    "직접 수정한 내용은 유지합니다."
)

if "fixture_content" not in st.session_state:
    fixture_refresh_callback()

st.button(
    "시황 콘텐츠 일괄 갱신",
    key="fixture_refresh_content_button",
    on_click=fixture_refresh_callback,
    type="primary",
)

report = st.session_state.get(
    "fixture_refresh_report", {}
)

c1, c2, c3 = st.columns(3)

c1.metric("자동 갱신", len(report.get("updated", [])))
c2.metric("수동 작성 보존", len(report.get("preserved", [])))
c3.metric("생성 실패", len(report.get("failed", [])))

for failure in report.get("failed", []):
    st.warning(
        f"{failure['key']}: {failure['error']}"
    )

for key, label in (
    ("themes", "강세 산업 및 테마"),
    ("summary", "한 줄 시황 요약"),
    ("keywords", "핵심 키워드"),
):
    st.text_area(
        label,
        key="fixture_content_" + key,
    )

    content = st.session_state["fixture_content"]

    current = st.session_state["fixture_content_" + key]

    generated = content.get(f"{key}__generated")

    current_state = dict(content)

    for field in ("themes", "summary", "keywords"):
        current_state[field] = st.session_state.get(
            "fixture_content_" + field,
            content.get(field, ""),
        )

    status = fixture_freshness(
        key, snapshot, current_state
    )

    if current != generated:
        st.info("수동 수정값 · 자동 갱신 시 보존")

    elif status == "stale":
        st.warning("시장 데이터 변경 · 다시 생성 권장")

    elif status == "matched":
        st.success("현재 가상 시장 데이터와 일치")

    else:
        st.info("콘텐츠 생성 이력 또는 입력값 확인 필요")


st.caption("TEST LAB · 실제 저장 및 게시 기능 미연결")
