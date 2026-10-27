
def touch(key):
    st.session_state.setdefault("touched", set()).add(key)


def colored(v, text):
    return f":red[{text}]" if v > 0 else f":blue[{text}]" if v < 0 else text


def apply_defaults(snap):
    ss = st.session_state
    touched = ss.setdefault("touched", set())
    for s in snap["gainers"] + snap["losers"]:
        for f, val in (("reason", auto_reason(s)), ("theme", s.get("theme", ""))):
            key = f"{f}_{s['name']}"
            if key not in touched:
                ss[key] = val


def stock_table(title, stocks, word):
    st.subheader(title)
    if not stocks:
        st.warning("실제 시세 데이터를 가져오지 못했거나 조건에 맞는 종목이 없습니다. 배포 환경 로그를 확인해 주세요.")
        return
    w = [2.2, 1.2, 1.3, 4.5, 2.2]
    head = st.columns(w)
    for c, label in zip(head, ["종목", "현재가", "등락률", f"{word} 이유", "관련 테마"]):
        c.caption(label)
    for s in stocks:
        c = st.columns(w)
        c[0].markdown(f"**{s['name']}**")
        c[1].markdown(f"{s['price']:,.0f}")
        c[2].markdown(colored(s["pct"], f"{em(s['pct'])} {sgn(s['pct'])}"))
        rk, tk = f"reason_{s['name']}", f"theme_{s['name']}"
        c[3].text_input("이유", key=rk, label_visibility="collapsed", on_change=touch, args=(rk,))
        c[4].text_input("테마", key=tk, label_visibility="collapsed", on_change=touch, args=(tk,))


def manual_inputs():
    with st.expander("📝 시황글 추가 내용 편집 (요약, 이슈, 테마 등)", expanded=True):
        st.text_input("한 줄 요약", key="summary", value="AI 투자심리 개선에 반도체주 강세, 장중 변동성 주의")
        st.text_area(
            "인트로 문구",
            key="intro_text",
            value="오늘 국내 증시는 미국 AI 관련 투자심리 개선과 외국인·기관 매수세에 힘입어 강세를 보이고 있습니다. 다만 장중 지수와 종목별 등락은 시시각각 변하므로, 기사에 확인된 시각을 기준으로 정리했습니다.",
        )
        for i in (1, 2, 3):
            st.markdown(f"**이슈 {i}**")
            a, b, c_col = st.columns([1, 3, 3])
            defaults_m = ["🔺", "🔺", "➖"]
            defaults_t = ["AI 수익성 우려 완화", "코스피 매수 사이드카 발동", "반도체·2차전지 동반 강세"]
            defaults_s = ["삼성전자, SK하이닉스, SK스퀘어", "삼성전자, SK하이닉스, 삼성전기", "심텍, 원익IPS, 에코프로비엠"]
            a.selectbox("방향", ["🔺", "🔻", "➖"], index=["🔺", "🔻", "➖"].index(defaults_m[i-1]), key=f"is{i}m", label_visibility="collapsed")
            b.text_input("제목", key=f"is{i}t", value=defaults_t[i-1], label_visibility="collapsed")
            c_col.text_input("관련 종목", key=f"is{i}s", value=defaults_s[i-1], label_visibility="collapsed")
            defaults_c = [
                "앤트로픽 IPO 추진과 매출 성장 소식이 투자심리 개선에 영향",
                "외국인·기관 매수세에 코스피가 장중 4%대 상승",
                "반도체 소부장과 2차전지 관련주로 매수세 확산",
            ]
            defaults_k = [
                "미국 빅테크 실적과 AI 투자 지속성",
                "급등 이후 차익실현 및 변동성 확대 가능성",
                "업종별 상승세 지속 여부",
            ]
            st.text_input("내용", key=f"is{i}c", value=defaults_c[i-1], label_visibility="collapsed")
            st.text_input("체크", key=f"is{i}k", value=defaults_k[i-1], label_visibility="collapsed")
        st.text_input("강했던 테마", key="themes", value="AI 반도체 · 반도체 소부장 · 반도체 지주사 · 2차전지 · 로봇")
        st.text_input("내일 체크 포인트 (/로 구분)", key="checks", value="삼성전자·SK하이닉스 상승세 지속 여부 / SK스퀘어·심텍 등 반도체 관련주 강세 유지 여부 / 에코프로·에코프로비엠 2차전지주 수급 변화")
        st.text_input("핵심 키워드", key="keywords", value="#코스피 #코스닥 #AI #앤트로픽 #삼성전자 #SK하이닉스 #SK스퀘어 #반도체 #2차전지 #외국인수급")
        st.text_input("한 줄 주의", key="caution", value="급등에 따른 차익실현과 종목별 변동성 확대에 유의하세요.")


def main():
    st.title("MTNW 오후 시황")
    with st.sidebar:
        st.header("설정")
        auto = st.selectbox("자동 갱신", [0, 30, 60, 120], format_func=lambda x: "끔" if x == 0 else f"{x}초")
        if st.button("로그아웃"):
            st.session_state["pw_ok"] = False
            st.rerun()

    @st.fragment(run_every=auto if auto else None)
    def body():
        ss = st.session_state
        clicked = st.button("지금 시세 조회", type="primary")
        snap = ss.get("snap")
        due = bool(auto) and snap is not None and time.time() - snap["ts"] >= auto - 1
        if clicked or due or not snap:
            try:
                with st.spinner("시세 조회 중..."):
                    snap = fetch_snapshot()
                ss["snap"] = snap
                apply_defaults(snap)
            except Exception as e:
                st.error(f"조회 실패: {e}")

        snap = ss.get("snap")
        if not snap:
            st.info("[지금 시세 조회] 버튼을 눌러 시세를 불러오세요.")
            return

        st.caption(f"{snap['date']} · 조회 {snap['fetched_at']} · {snap['toss_status']}")

        c1, c2, c3 = st.columns(3)
        if snap["kospi"].get("available"):
            c1.markdown(f"**코스피**  \n### {fmt(snap['kospi']['price'])}")
            c1.markdown(colored(snap["kospi"]["pct"], f"{em(snap['kospi']['pct'])} {sgn(snap['kospi']['pct'])}"))
        else:
            c1.markdown("**코스피**  \n### 조회 실패")

        if snap["kosdaq"].get("available"):
            c2.markdown(f"**코스닥**  \n### {fmt(snap['kosdaq']['price'])}")
            c2.markdown(colored(snap["kosdaq"]["pct"], f"{em(snap['kosdaq']['pct'])} {sgn(snap['kosdaq']['pct'])}"))
        else:
            c2.markdown("**코스닥**  \n### 조회 실패")

        c3.markdown("**원/달러 환율**")
        f1, f2 = c3.columns(2)
        f1.text_input("환율(원)", key="fx_price", placeholder="직접 입력", label_visibility="collapsed")
        f2.text_input("등락률(%)", key="fx_pct", placeholder="직접 입력", label_visibility="collapsed")

        stock_table("🔺 급등 특징주 TOP 5", snap["gainers"], "상승")
        stock_table("🔻 급락 특징주 TOP 5", snap["losers"], "하락")
        manual_inputs()

        st.subheader("시황글 미리보기")
        md = build_md(snap)
