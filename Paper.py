import datetime
import hmac
import os
import sys
import traceback
from pathlib import Path
from zoneinfo import ZoneInfo

import streamlit as st
import streamlit.components.v1 as components

# set_page_config 는 이 페이지에서 가장 먼저 실행되는 화면 명령이어야 합니다.
st.set_page_config(page_title="MTNW 오늘의 지면", layout="wide")

ROOT = Path(__file__).resolve().parent

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from archive import list_posts
    from paper_html import build_paper_document
    from paper_images import render_paper_images
    from paper_model import build_model, issue_number
except Exception as exc:
    st.error(f"지면 파일을 불러오지 못했습니다: {type(exc).__name__}: {exc}")
    st.code(
        f"이 파일 위치: {Path(__file__).resolve()}\n"
        f"같은 폴더의 .py 파일: {', '.join(sorted(p.name for p in ROOT.glob('*.py')))}",
        language="text",
    )
    st.code(traceback.format_exc(), language="text")
    st.stop()

KST = ZoneInfo("Asia/Seoul")
WEEKDAYS = "월화수목금토일"

EDITIONS = [("pre", "🌅 장전판"), ("aft", "📈 오후판"), ("week", "🗓️ 주간 특집판")]
IMAGE_PREFIX = {"pre": "장전지면", "aft": "오후지면", "week": "주간지면"}
EMPTY_HINT = {
    "pre": "장전 시황 페이지에서 시황글을 저장하면 여기에 나타납니다 (자동 실행도 저장됩니다).",
    "aft": "오후 시황 페이지의 '오늘 오후 시황글 저장' 버튼으로 저장하면 여기에 나타납니다.",
    "week": "주간 정리 페이지의 '주간 정리 저장 (지면용)' 버튼으로 저장하면 여기에 나타납니다.",
}


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


@st.cache_data(ttl=120, show_spinner=False)
def posts_of(kind):
    try:
        return list_posts(kind, limit=60)
    except Exception:
        return []


def day_label(iso):
    try:
        day = datetime.date.fromisoformat(iso)
    except ValueError:
        return iso

    return f"{day.year}.{day.month}.{day.day}({WEEKDAYS[day.weekday()]})"


st.title("📰 MTNW 오늘의 지면")
st.caption("저장된 시황글을 신문 지면으로 보여 주고, 같은 모양의 이미지(PNG)로 내려받을 수 있습니다. 시황글 복사·미리보기·카드 이미지는 각 시황 페이지에서 그대로 쓰시면 됩니다.")

top_left, top_right = st.columns([4, 1])

with top_left:
    labels = [label for _, label in EDITIONS]
    chosen = st.radio("판", labels, horizontal=True, label_visibility="collapsed", key="paper_edition")

with top_right:
    if st.button("🔄 목록 새로고침", use_container_width=True, key="paper_refresh"):
        posts_of.clear()
        st.rerun()

kind = next(k for k, label in EDITIONS if label == chosen)
posts = posts_of(kind)

markdown, picked_day = "", ""

if posts:
    days = [day for day, _ in posts]
    picked_day = st.selectbox("날짜", days, format_func=day_label, key=f"paper_day_{kind}")
    markdown = dict(posts).get(picked_day, "")
else:
    st.info(EMPTY_HINT[kind])

with st.expander("직접 붙여넣어 지면으로 보기"):
    pasted = st.text_area(
        "시황글(마크다운) 붙여넣기",
        key=f"paper_paste_{kind}",
        height=160,
        placeholder="시황 페이지의 '마크다운 원문'을 붙여넣으면 위 선택 대신 이 글로 지면을 만듭니다.",
    )

if pasted.strip():
    markdown, picked_day = pasted.strip(), ""

if not markdown.strip():
    st.stop()

try:
    model = build_model(markdown)

    all_pre_days = [day for day, _ in posts_of("pre")]
    number = issue_number(model, all_pre_days)

    document = build_paper_document(model, number)
except Exception as exc:
    st.error(f"지면을 만들지 못했습니다: {type(exc).__name__}: {exc}")
    st.code(traceback.format_exc(), language="text")
    st.stop()

components.html(document, height=1700, scrolling=True)

st.divider()
st.subheader("지면 이미지")
st.caption("1면에는 제호·한 줄 요약·톱 이슈·숫자로 본 지수표가 들어가고, 내용이 많으면 2면·3면이 이어집니다. 이미지는 ZIP으로 내려받습니다.")

suffix = (picked_day or datetime.datetime.now(KST).date().isoformat()).replace("-", "")
render_paper_images(markdown, f"{IMAGE_PREFIX[kind]}_{suffix}", issue_no=number)
