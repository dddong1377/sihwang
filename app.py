import sys
from pathlib import Path
import streamlit as st

PROJECT_ROOT = str(Path(__file__).resolve().parent)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from autorun import render_auto_sidebar

# 사이드바 메뉴: 이름·글자 크기를 여기서 정합니다.
pages = [
    st.Page(
        "premarket.py",
        title="장전 시황",
        icon="🌅",
        url_path="premarket",
        default=True,
    ),
    st.Page(
        "afternoon.py",
        title="오후 시황",
        icon="📈",
        url_path="afternoon",
    ),
    st.Page(
        "weekly.py",
        title="주간 정리",
        icon="🗓️",
        url_path="weekly",
    ),
   st.Page("paper.py",
        title="오늘의 지면",
        icon="📰",
        url_path="paper"
    ),
    st.Page(
        "arena.py",
        title="주식 대결",
        icon="🥊",
        url_path="Arena",
    ),
    st.Page(
        "report_digest.py",
        title="리포트 압축(사용X)",
        icon="📑",
        url_path="report_digest",
    ),
]

st.markdown(
    """
    <style>
    [data-testid="stSidebarNav"] a,
    [data-testid="stSidebarNavLink"] {
        padding-top: .55rem !important;
        padding-bottom: .55rem !important;
    }
    [data-testid="stSidebarNav"] a span,
    [data-testid="stSidebarNavLink"] span,
    [data-testid="stSidebarNavLink"] p {
        font-size: 1.35rem !important;
        font-weight: 800 !important;
        line-height: 1.35 !important;
    }
    /* 옅은 글씨를 진하게 (설명 글·입력 칸 제목·탭·왼쪽 메뉴 설명) */
    [data-testid="stCaptionContainer"],
    [data-testid="stCaptionContainer"] *,
    [data-testid="stWidgetLabel"] p,
    [data-testid="stWidgetLabel"] label,
    [data-testid="stSidebar"] [data-testid="stCaptionContainer"] *,
    button[data-baseweb="tab"] p,
    [data-testid="stExpander"] summary p,
    [data-testid="stMetricLabel"] p {
        color: #1f2430 !important;
        opacity: 1 !important;
    }
    [data-testid="stCaptionContainer"] {
        font-size: 0.9rem !important;
    }
    textarea::placeholder, input::placeholder {
        color: #5b6270 !important;
        opacity: 1 !important;
    }
    @media (prefers-color-scheme: dark) {
        [data-testid="stCaptionContainer"],
        [data-testid="stCaptionContainer"] *,
        [data-testid="stWidgetLabel"] p,
        [data-testid="stWidgetLabel"] label,
        [data-testid="stSidebar"] [data-testid="stCaptionContainer"] *,
        button[data-baseweb="tab"] p,
        [data-testid="stExpander"] summary p,
        [data-testid="stMetricLabel"] p {
            color: #f1f3f7 !important;
        }
        textarea::placeholder, input::placeholder {
            color: #b8bdc9 !important;
        }
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# 왼쪽 메뉴의 '자동 실행' 스위치 (로그인 후에만 표시)
render_auto_sidebar()

navigation = st.navigation(pages)
navigation.run()
