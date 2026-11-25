"""Read-only QUANT LAB Streamlit page; no collection or trading."""
import hmac
from urllib.parse import urlsplit
import requests
import streamlit as st
import pandas as pd


def fetch(endpoint, params=None):
    base = str(st.secrets['MTNW_API_URL']).rstrip('/')
    parsed = urlsplit(base)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('Invalid HTTPS configuration')
    response = requests.get(base + '/v1/quant/' + endpoint,
        params=params, headers={'X-API-Key': str(st.secrets['MTNW_API_KEY'])},
        timeout=(5, 15), allow_redirects=False)
    if response.status_code != 200:
        raise RuntimeError(f'HTTP {response.status_code}')
    data = response.json()
    if not isinstance(data, dict):
        raise ValueError('Invalid API response')
    return data


def render():
    st.title('QUANT LAB | 투자전략 연구소')
    st.caption('실제 저장된 데이터만 표시 · 연구용 · 주문 기능 없음')
    try:
        expected = str(st.secrets['monitor_password'])
        if not expected:
            raise ValueError()
    except Exception:
        st.error('관리자 인증 설정을 확인하세요.')
        return
    if not st.session_state.get('quant_authenticated', False):
        with st.form('quant_login'):
            password = st.text_input('관리자 비밀번호', type='password')
            submitted = st.form_submit_button('로그인')
        if submitted:
            if hmac.compare_digest(password.encode(), expected.encode()):
                st.session_state['quant_authenticated'] = True
                st.rerun()
            st.error('비밀번호가 일치하지 않습니다.')
        return
    if st.button('로그아웃', key='quant_logout'):
        st.session_state.pop('quant_authenticated', None)
        st.rerun()
    st.button('새로고침', key='quant_refresh')
    try:
        overview = fetch('overview')
        counts = overview['counts']
        cols = st.columns(4)
        for col, label, key in zip(cols, ['등록 종목','일봉 기록','포착 신호','성과 기록'], ['stocks','daily_prices','screening_signals','signal_returns']):
            col.metric(label, f'{int(counts[key]):,}')
        st.caption('최종 일봉 날짜: ' + str(overview.get('latest_price_date') or '없음'))
        if overview.get('status') == 'empty':
            st.info('아직 저장된 일봉 데이터가 없습니다. 실제 자동 수집은 별도 승인 및 설정 이후 진행됩니다.')
        with st.expander('데이터베이스 상세 현황'):
            st.dataframe(pd.DataFrame(list(counts.items()), columns=['테이블','기록 수']), hide_index=True, use_container_width=True)
        strategies = fetch('strategies', {'limit':200})['data']
        choices = {0:'전체 전략'}
        choices.update({int(r['rule_id']): f"{r['name']} · v{r['version']} (#{r['rule_id']})" for r in strategies})
        st.subheader('전략별 관찰 성과')
        rule = st.selectbox('전략', list(choices), format_func=choices.get)
        horizon = st.radio('평가 기간 (거래일)', [1,3,5,10,20], index=2, horizontal=True)
        common = {'rule_id':rule} if rule else {}
        page = int(st.number_input('성과 페이지', min_value=1, max_value=501, step=1, key='quant_perf_page'))
        perf = fetch('performance', {**common,'horizon':horizon,'limit':200,'offset':(page-1)*200})['data']
        st.caption('포착 가격 대비 관찰 수익률입니다. 실제 매매 수익률이나 백테스트 결과가 아닙니다. 평가 완료 건만 집계합니다.')
        if perf:
            df = pd.DataFrame(perf)
            st.dataframe(df.rename(columns={'name':'전략','version':'버전','direction':'방향','signal_price_source':'포착 가격 출처','price_source':'평가 가격 출처','method_version':'평가 방식','horizon':'거래일','evaluated_count':'평가 건수','positive_count':'상승 건수','average_return_pct':'평균 수익률(%)','positive_rate_pct':'상승 비율(%)'}), hide_index=True, use_container_width=True)
        else:
            st.info('해당 조건의 평가 완료 데이터가 없습니다.')
        st.subheader('포착 종목 이력')
        page = int(st.number_input('신호 페이지', min_value=1, max_value=2001, step=1, key='quant_signal_page'))
        signals = fetch('signals', {**common,'limit':50,'offset':(page-1)*50})['data']
        if signals:
            df = pd.DataFrame(signals)
            df['observed_at'] = pd.to_datetime(df['observed_at'], unit='s', utc=True).dt.tz_convert('Asia/Seoul').dt.strftime('%Y-%m-%d %H:%M:%S')
            st.dataframe(df.rename(columns={'strategy':'전략','stock_name':'종목명','symbol':'종목코드','observed_at':'포착 시각(KST)','trade_date':'거래일','observed_price':'포착 가격','pct':'등락률(%)','theme':'테마','price_source':'가격 출처'}), hide_index=True, use_container_width=True)
        else:
            st.info('표시할 포착 이력이 없습니다.')
        st.divider()
        st.caption('테마 지속성 및 백테스팅 화면은 해당 API와 실제 데이터 검증 이후 연결됩니다.')
    except Exception as exc:
        # Never expose connection URLs, headers or credentials.
        st.error('QUANT API 조회 실패. 서버 상태와 API 설치 여부를 확인하세요.' + (f' ({exc})' if isinstance(exc, RuntimeError) and str(exc).startswith('HTTP ') else ''))


render()
