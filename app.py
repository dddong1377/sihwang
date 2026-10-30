                a, b, c = st.columns([1, 3, 3])

                a.selectbox(
                    "방향",
                    ["🔺", "🔻", "➖"],
                    key=f"is{i}m",
                )

                b.text_area(
                    "제목",
                    key=f"is{i}t",
                    height=68,
                )

                c.text_area(
                    "관련 종목",
                    key=f"is{i}s",
                    height=68,
                )

                l1, l2 = st.columns([5, 1.4], vertical_alignment="bottom")

                l1.text_input(
                    "기사 링크",
                    key=f"is{i}link",
                    placeholder="https://...",
                )

                l2.button(
                    "기사 불러오기",
                    key=f"is{i}fetch",
                    on_click=load_article,
                    args=(i,),
                )

                st.text_area(
                    "기사 내용 (링크로 불러오거나 직접 붙여넣기)",
                    key=f"is{i}src",
                    height=110,
                )

                s1, s2 = st.columns([1.4, 5], vertical_alignment="center")

                s1.button(
                    "✨ 핵심 정리",
                    key=f"is{i}sum",
                    on_click=summarize_issue,
                    args=(i,),
                )

                if st.session_state.get(f"is{i}note"):
                    s2.caption(st.session_state[f"is{i}note"])

                st.text_area(
                    "내용",
                    key=f"is{i}c",
                    height=110,
                )

                st.text_area(
                    "체크",
                    key=f"is{i}k",
                    height=68,
                )

            st.session_state.setdefault("themes", "AI 반도체 · 2차전지")

            t1, t2 = st.columns([1.6, 5], vertical_alignment="center")

            t1.button(
                "🔄 강한 테마 자동 불러오기",
                key="themes_btn",
                on_click=autofill_themes,
            )

            if st.session_state.get("themes_note"):
                t2.caption(st.session_state["themes_note"])

            st.text_area(
                "강한 테마 (#태그를 띄어쓰기로 구분)",
                key="themes",
                height=68,
            )

            st.session_state.setdefault(
                "checks",
                "급등 종목 상승 지속 여부 / "
                "급락 종목 추가 하락 여부 / "
                "시장 거래대금 변화",
            )

            c1, c2 = st.columns([1.6, 5], vertical_alignment="center")

            c1.button(
                "🔄 다음 영업일 정보 넣기",
                key="checks_btn",
                on_click=autofill_checks,
            )

            if st.session_state.get("checks_note"):
                c2.caption(st.session_state["checks_note"])

            st.text_area(
                "내일 체크 포인트 (줄바꿈 또는 /로 구분)",
                height=110,
                key="checks",
            )

            st.text_area(
                "핵심 키워드",
                key="keywords",
                value="#코스피 #코스닥 #급등주 #급락주 #주식시황",
                height=68,
            )

        st.subheader("시황글 미리보기")

        now_text = datetime.datetime.now(KST).strftime("%H:%M")

        markdown = build_markdown(snapshot, now_text)

        copy_buttons(build_markdown(snapshot, NOW_TOKEN))

        with st.container(border=True):
            st.markdown(markdown)

        with st.expander("마크다운 원문 보기"):
