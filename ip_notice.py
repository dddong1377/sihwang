"""토스 허용 IP 등록 안내 상자: 현재 서버 IP를 크게 보여 주고, 휴대폰에서 등록하는 방법을 안내합니다."""

import requests
import streamlit as st

IP_BLOCK_WORDS = ("HTTP 403", "IP address not allowed", "허용 IP")


def is_ip_block(text):
    """오류 문구가 '허용 IP에 없어서 막힘'으로 보이는지."""

    return any(word in str(text) for word in IP_BLOCK_WORDS)


@st.cache_data(ttl=120, show_spinner=False)
def _lookup():
    """서버가 밖으로 나갈 때의 공인 IP (IPv4, IPv6)."""

    v4, v6 = "", ""

    try:
        v4 = requests.get("https://api.ipify.org", timeout=5).text.strip()
    except Exception:
        pass

    try:
        found = requests.get("https://api64.ipify.org", timeout=5).text.strip()

        if ":" in found:  # IPv6 로 나가는 경우만 따로 표시
            v6 = found
    except Exception:
        pass

    return v4, v6


def render_ip_help(key="ip_help"):
    """허용 IP 문제일 때 화면에 보여 주는 안내 상자."""

    with st.container(border=True):
        st.markdown("#### 🔒 토스 허용 IP 등록이 필요합니다")
        st.caption("서버 IP가 바뀌어 토스가 접속을 막고 있습니다. 아래 IP를 허용 IP에 등록하면 바로 정상 작동합니다.")

        if st.button("🔄 IP 다시 확인", key=f"{key}_refresh"):
            _lookup.clear()

        v4, v6 = _lookup()

        if v4 or v6:
            if v4:
                st.markdown("**현재 서버 IP**  ·  오른쪽 위 복사 버튼을 누르세요")
                st.code(v4, language="text")

            if v6:
                st.markdown("IPv6로 나가는 경우도 있어 함께 표시합니다")
                st.code(v6, language="text")
        else:
            st.warning("서버 IP를 확인하지 못했습니다. 위 '🔄 IP 다시 확인'을 눌러 보세요.")

        st.markdown(
            "**등록 방법 (휴대폰에서도 가능)**\n"
            "1. 브라우저에서 tossinvest.com 접속 → '데스크톱 사이트(PC 버전)' 켜기\n"
            "2. 토스 앱으로 본인 인증하며 로그인\n"
            "3. 설정 → Open API → 하단 '허용 IP 관리'에 위 IP 입력 후 저장\n"
            "4. 이 화면으로 돌아와 다시 시도"
        )
