import streamlit as st

# 사이드바 메뉴: 이름·글자 크기를 여기서 정합니다.
pages = [
    st.Page(
        "afternoon.py",
        title="오후 시황",
        icon="📈",
        url_path="afternoon",
        default=True,
    ),
    st.Page(
        "premarket.py",
        title="장전 시황",
        icon="🌅",
        url_path="premarket",
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
    </style>
    """,
    unsafe_allow_html=True,
)

navigation = st.navigation(pages)
navigation.run()
