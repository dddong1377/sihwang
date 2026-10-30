import datetime
import hmac
import html
import json
import math
import os
import re
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote
from zoneinfo import ZoneInfo

import requests
import streamlit as st
import streamlit.components.v1 as components

from realtime_feed import TossFeed

st.set_page_config(page_title="MTNW 오후 시황", layout="wide")

KST = ZoneInfo("Asia/Seoul")
BASE = "https://openapi.tossinvest.com"

# 토스증권 웹 화면의 공개 산업(TICS) API
# 공식 OAuth Open API와 별도이며, 공개 페이지에서 사용하는 읽기 전용 스냅샷입니다.
TOSS_WEB_BASE = "https://wts-info-api.tossinvest.com"
TOSS_WEB_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Referer": "https://www.tossinvest.com/",
    "Accept": "application/json, text/plain, */*",
    "Content-Type": "application/json",
}
WEEKDAYS = "월화수목금토일"
GLOBAL_ITEMS = (
    ("nasdaq", "나스닥", ""),
    ("sp500", "S&P500", ""),
    ("usdkrw", "원/달러 환율", "원"),
)
CAP_RANK_LIMIT = 100
CAP_SUFFIX = " (시총 100위 이내)"
NAVER_WEB_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Referer": "https://finance.naver.com/sise/",
}
NOW_TOKEN = "__NOW__"  # 복사 버튼을 누르는 순간의 시각으로 치환됨

DEFAULT_THEME = "주요업종"
POST_NOTE = " ※ 현재가·등락률 확인 필수"

# KRX 정규장 시간 (이 시간 안에서만 토스 실시간 체결가를 사용합니다)
KRX_OPEN = datetime.time(9, 0)
KRX_CLOSE = datetime.time(15, 30)

# 기준가 출처 표시용
BASE_SOURCE_LABELS = {
    "limit": "토스 상·하한가 역산 (KRX 전일 기준가)",
    "krx": "네이버 KRX 전일 기준가(전일비)",
    "krx_close": "KRX 전일 종가(네이버 일별 시세)",
    "krx_calc": "네이버 등락률 역산(오차 가능)",
    "toss": "토스 일봉(NXT 포함 가능, KRX와 다를 수 있음)",
}

# ---------------------------------------------------------------
# 테마별 키워드 (종목명·기사 제목에서 찾습니다)
# 순서가 앞쪽일수록 동점일 때 우선합니다.
# ---------------------------------------------------------------
THEME_KEYWORDS = [
    ("HBM·반도체",
     "HBM, D램, DRAM, 낸드, NAND, 반도체, 파운드리, 메모리, 소부장, 웨이퍼, 후공정, 패키징, "
     "유리기판, 글라스기판, CXL, 팹리스, 시스템반도체, 전력반도체, 반도체장비, 반도체소재, TSMC, "
     "마이크론, 포토레지스트, 식각, 증착, NPU, SiC, GaN, 테스트소켓, 노광"),
    ("AI",
     "AI, 인공지능, 데이터센터, 엔비디아, 챗GPT, 챗봇, 앤트로픽, 오픈AI, OpenAI, 생성형, LLM, "
     "온디바이스, 피지컬AI, 딥러닝, 머신러닝, 초거대, AI서버, GPU, 소버린, 딥시크, 제미나이, "
     "코파일럿, 슈퍼컴퓨터, 에이전트"),
    ("2차전지",
     "2차전지, 이차전지, 배터리, 양극재, 음극재, 전해질, 전해액, 분리막, 리튬, 전고체, 니켈, "
     "코발트, 흑연, 동박, 폐배터리, 배터리재활용, ESS, 에너지저장, 배터리셀, 파우치, 원통형, "
     "LFP, 나트륨이온, 건식전극"),
    ("바이오·제약",
     "바이오, 제약, 신약, 임상, FDA, 항암, 치료제, 백신, 항체, 세포치료, 유전자, 비만치료제, "
     "GLP-1, 바이오시밀러, 의약품, 기술수출, 기술이전, CDMO, 보톡스, 면역, mRNA, 신약개발, "
     "희귀질환, 임상시험, 줄기세포, 위탁생산, 감염병, 독감, 방역, ADC, 알츠하이머, 품목허가, "
     "신약후보, 약가, 제네릭, 엑소좀, HLB"),
    ("의료기기·헬스케어",
     "의료기기, 임플란트, 미용기기, 디지털헬스, 헬스케어, 체외진단, 진단키트, 원격의료, "
     "비대면진료, 초음파, 의료AI, 덴탈, 치과, 안과, 수술로봇, 건강검진, 진단기기, 의료장비, "
     "요양, 시니어, 고령화, 라식"),
    ("로봇",
     "로봇, 휴머노이드, 자동화, 로보틱스, 협동로봇, 물류로봇, 서비스로봇, 감속기, 액추에이터, "
     "스마트팩토리, 무인화, 옵티머스"),
    ("방산·우주",
     "방산, 방위산업, 미사일, 우주, 위성, K9, 천무, 항공우주, 발사체, 누리호, 레이더, 전차, K2, "
     "잠수함, 전투기, KF-21, 탄약, 국방, 지정학, 스페이스X, 무기수출, 방위, 폴란드"),
    ("드론·UAM",
     "드론, UAM, 도심항공, 에어택시, UAV, 무인기, eVTOL, 자율비행, 무인항공기, 군집비행"),
    ("조선·해운",
     "조선, 선박, LNG선, 해운, 컨테이너선, 조선업, 마스가, MASGA, 해양플랜트, 크루즈선, 벌크선, "
     "운임, 탱커, 선박엔진, 쇄빙선, 해운업, SCFI, BDI, 수주잔고, 선주, 해양, 조선소"),
    ("원전·전력",
     "원전, SMR, 전력, 변압기, 전선, 송전, 원자력, 소형모듈원전, 전력기기, 전력설비, 배전, "
     "초고압, 전력망, 한전, 우라늄, 핵융합, 변전, 케이블, 전력인프라, 가스터빈, 터빈, 전기요금, "
     "전력수요, 그리드, HVDC, 차단기, 개폐기, 한수원, 체코원전, 웨스팅하우스"),
    ("수소·신재생",
     "수소, 연료전지, 태양광, 풍력, 신재생, 탄소중립, 친환경, 그린에너지, 해상풍력, 재생에너지, "
     "탄소배출권, RE100, 수전해, 태양전지, 페로브스카이트, 지열, 바이오에너지, 바이오디젤, "
     "탄소포집, CCUS, 그린수소, 암모니아, 폴리실리콘, 잉곳"),
    ("자동차",
     "자동차, 전기차, 자율주행, 완성차, 타이어, 자동차부품, 전장, 하이브리드, 수소차, 전기차충전, "
     "충전인프라, 로보택시, 테슬라, 커넥티드카, 차량용, 차량용반도체, 전기버스, 트럭, 현대차그룹, "
     "모빌리티, 전동화, 라이다, 변속기"),
    ("디스플레이·IT부품",
     "디스플레이, OLED, 카메라모듈, 스마트폰, PCB, 기판, MLCC, 폴더블, 마이크로LED, 애플, 아이폰, "
     "갤럭시, 플렉시블, 광학, 커넥터, FPCB, 웨어러블, 스마트워치, 스마트글라스, 태블릿, 노트북, "
     "가전, TV, 전자부품, LCD, 반도체기판, 아이패드, 비전프로"),
    ("통신·네트워크",
     "통신, 5G, 6G, 광통신, 광케이블, 통신장비, 위성통신, 통신사, 이동통신, 네트워크, 와이파이, "
     "5G특화망, 광섬유, 저궤도위성, 스타링크, 안테나, 중계기, 이통사, 통신망, 통신3사, 주파수, "
     "로밍, 광모듈, 광트랜시버, 오픈랜"),
    ("소프트웨어·플랫폼",
     "소프트웨어, 소프트, 클라우드, SaaS, 플랫폼, ERP, 빅데이터, 포털, 메신저, 전자정부, 공공SW, "
     "오피스, 구독"),
    ("보안·양자",
     "보안, 사이버보안, 정보보안, 해킹, 랜섬웨어, 암호화, 개인정보, 양자, 양자컴퓨터, 양자컴퓨팅, "
     "양자암호, 양자내성, PQC, 퀀텀, 큐비트, IonQ, 제로트러스트, 사이버, 정보유출, 해커, 디도스, "
     "바이오인증, 생체인증, 전자인증, 지문인식"),
    ("가상자산·블록체인",
     "비트코인, 가상자산, 블록체인, 코인, 스테이블코인, 이더리움, STO, 토큰증권, 암호화폐, 디파이, "
     "NFT, 리플, 솔라나, 가상화폐, 업비트, 두나무, 빗썸, 크립토, 코인베이스, 비트코인ETF, 채굴, "
     "웹3, Web3, 디지털자산, 원화스테이블, 테더, 마이닝, 알트코인, 도지코인, 디지털자산기본법"),
    ("엔터",
     "엔터, K팝, 아이돌, 콘서트, 음반, 엔터테인먼트, 걸그룹, 보이그룹, 앨범, 팬덤, 컴백, 월드투어, "
     "하이브, 공연, 음원, 뮤직, 기획사, 아티스트, 팬콘, 데뷔, 한류, 방탄소년단, BTS, 블랙핑크, "
     "케이팝, 뮤지컬"),
    ("미디어·콘텐츠",
     "콘텐츠, 드라마, 영화, 웹툰, 웹소설, OTT, 넷플릭스, 방송, 광고, 스튜디오, 애니메이션, 캐릭터, "
     "IP, 숏폼, 미디어, 제작사, 티빙, 웨이브, 디즈니, 유튜브, 극장, 박스오피스, 영화관, 편성, "
     "시청률, K콘텐츠, 웹드라마, 만화"),
    ("게임",
     "게임, 신작, 퍼블리싱, 모바일게임, 온라인게임, MMORPG, 스팀, 게임사, e스포츠, 콘솔, 메타버스, "
     "VR, AR, XR, 게임주, 플레이스테이션, 닌텐도, 인디게임, P2E, 리니지, 아이온, 지스타, 신작게임, "
     "사전예약"),
    ("화장품·소비",
     "화장품, 뷰티, 면세, K푸드, 음식료, 코스메틱, 마스크팩, 화장품ODM, 화장품OEM, 중국인관광, "
     "인바운드, 소비, 리오프닝, 패션, 의류, 명품, 생활용품, 소비재, 소비쿠폰, 소비심리, 내수, "
     "내수주, 소비진작, 민생회복쿠폰, 스킨케어, 선크림, 섬유, 신발, 잡화, 아웃도어, 골프웨어, "
     "스포츠웨어, 슈즈, 주얼리"),
    ("식품·유통",
     "식품, 유통업, 유통사, 편의점, 라면, 제과, 주류, 음료, 백화점, 홈쇼핑, 이커머스, 온라인쇼핑, "
     "마트, 대형마트, 식자재, 외식, 프랜차이즈, 배달, 소주, 맥주, 우유, 육가공, 닭고기, 계란, 쿠팡, "
     "라이브커머스, 간편식, HMR, 커피, 제빵, 냉동식품, 밀키트, 과자, 아이스크림, 김치, 건강기능식품, "
     "건기식, 프로틴"),
    ("항공·여행",
     "항공, 여행, 카지노, 호텔, 크루즈, 레저, 항공사, 저비용항공, LCC, 공항, 관광, 리조트, 항공화물, "
     "유류할증료, 골프, 캠핑, 면세점, 해외여행, 여행객, 노선, 여객, 항공권, 관광객, 외국인관광객, "
     "한한령, 중국단체관광, 무비자"),
    ("금융",
     "은행, 증권, 보험, 금융지주, 배당, 저축은행, 카드사, 핀테크, 인터넷은행, 밸류업, 자사주, 증권사, "
     "캐피탈, 리츠, 자산운용, 배당성향, 주주환원, 금융주, 금융당국, 예대마진, 순이자마진, NIM, "
     "보험사, 손해보험, 생명보험, 지주사, 자사주소각, 배당락, 중간배당"),
    ("건설·부동산",
     "건설, 부동산, 분양, 재건축, 재개발, 건자재, 시멘트, 레미콘, 주택, 아파트, 신도시, 건설사, "
     "토목, 인프라, 주택공급, 도시정비, PF, 리모델링, 해외수주, 플랜트, 건축, 인테리어, 가구, 청약, "
     "공공택지, 철근, 골재, 대출규제"),
    ("정유·에너지",
     "정유, 유가, 에너지, 석유, 가스, LNG, 천연가스, 원유, 도시가스, 석유화학, 정제마진, OPEC, 셰일, "
     "휘발유, 주유소, 석탄, 정유사, 나프타, 산유국, 감산, WTI, 브렌트유, 국제유가, LPG, 가스공사, "
     "에틸렌, 정유주"),
    ("철강·소재",
     "철강, 화학, 비철, 구리, 알루미늄, 아연, 희토류, 강관, 철광석, 후판, 스테인리스, 합성수지, "
     "비철금속, 마그네슘, 텅스텐, 핵심광물, 탄소섬유, 철스크랩, 열연, 냉연, 도금강판, 특수강, 강재, "
     "몰리브덴, 안티모니, 갈륨, 게르마늄, 희소금속, 광물, 전기동"),
    ("농업·비료",
     "농업, 비료, 종자, 스마트팜, 곡물, 사료, 식량, 농기계, 축산, 양돈, 곡물가격, 옥수수, 대두, "
     "농산물, 식량안보, 작황, 가뭄, 채소, 과일, 농약, 살충제, 농작물, 수산, 양식, 요소수, 질소비료"),
    ("교육·출산·키즈",
     "교육, 에듀테크, 출산, 저출산, 육아, 유아, 영유아, 학습지, 교과서, 어린이, 출산율, 아동, 보육, "
     "키즈, 완구, 장난감, 인구절벽, 학원, 수능, 입시, 사교육, 출산장려, 출생아, 신생아, 임신, 난임, "
     "산후조리, 분유, 기저귀, 유모차, 어린이집"),
    ("환경·재활용",
     "폐기물, 재활용, 수처리, 미세먼지, 폐플라스틱, 소각, 하수처리, 폐수, 업사이클, 폐수처리, "
     "대기오염, 환경설비, 정수, 상하수도, 매립, 폐자원, 순환경제, 폐지, 고철"),
    ("재건·남북경협",
     "재건, 우크라이나, 남북, 북한, 경협, 철도, 휴전, 종전, 가자, 중동재건, 대북, DMZ, 접경지, 통일, "
     "남북경협, 대북송전, 철도부설, 시베리아, 금강산, 개성공단, 비핵화, 재건사업, 우크라, 평화협상, "
     "종전협상, 재건수혜"),
    ("트럼프·관세",
     "트럼프, 관세, 보호무역, 무역협상, 상호관세, 통상, 무역전쟁, 관세폭탄, 무역분쟁, 수출규제, "
     "무역합의, 자국우선, 관세유예, 관세협상, 철강관세, 반도체관세, 트럼프수혜, 미중무역, 미중갈등, "
     "대미수출, FTA, 상계관세, 반덤핑, 중국견제"),
    ("정치·정책",
     "대선, 총선, 지방선거, 정치인, 공약, 선거, 상법개정, 주주충실의무, 추경, 지역화폐, 감세, "
     "세제개편, 규제완화, 국책사업, 정책수혜"),
    ("반려동물",
     "반려동물, 펫, 동물병원, 반려견, 반려묘, 고양이, 강아지, 펫푸드, 동물의약품, 수의, 펫보험, "
     "애견, 동물용"),
]

# ---------------------------------------------------------------
# 종목명 → 테마 (이름에 업종 단어가 없는 대표 종목용, 최우선 적용)
# ---------------------------------------------------------------
STOCK_THEMES = {
    "HBM·반도체":
        "삼성전자, SK하이닉스, SK스퀘어, 한미반도체, 리노공업, 이오테크닉스, 주성엔지니어링, 원익IPS, "
        "테스, 피에스케이, 유진테크, 솔브레인, 동진쎄미켐, 하나마이크론, 두산테스나, 이수페타시스, "
        "DB하이텍, 티씨케이, 에스티아이, HPSP, 케이씨텍, 프로텍, 와이씨, 에이피티씨, 네패스, "
        "제주반도체, 어보브반도체, 가온칩스, 오픈엣지테크놀로지, 심텍, 대덕전자, 해성디에스, "
        "ISC, 파크시스템스, 원익머트리얼즈, 램테크놀러지, 후성, 퀄리타스반도체, 인텍플러스, "
        "에스에프에이, 한솔케미칼, SK실트론, 윈팩, 엘오티베큠, 월덱스, 에이디테크놀로지, 라온텍",
    "AI":
        "NAVER, 네이버, 카카오, 삼성SDS, 솔트룩스, 코난테크놀로지, 마음AI, 라온피플, 플리토, 셀바스AI, "
        "바이브컴퍼니, 알체라, 제이엘케이, 크라우드웍스, 엑셈, 위세아이텍",
    "2차전지":
        "LG에너지솔루션, 삼성SDI, 에코프로, 에코프로비엠, 에코프로머티, 엘앤에프, 포스코퓨처엠, "
        "SK이노베이션, SK아이이테크놀로지, 더블유씨피, 솔루스첨단소재, 성일하이텍, 코스모신소재, "
        "대주전자재료, 천보, 나노신소재, 엔켐, 원준, 씨아이에스, 필에너지, 롯데에너지머티리얼즈, "
        "LG화학, 피엔티, 코윈테크, 하나기술, 이엔플러스, 에이프로",
    "바이오·제약":
        "셀트리온, 삼성바이오로직스, 삼성에피스홀딩스, 알테오젠, 유한양행, 한미약품, 한미사이언스, "
        "SK바이오팜, SK바이오사이언스, 리가켐바이오, 레고켐바이오, 에이비엘바이오, 삼천당제약, "
        "대웅제약, GC녹십자, 녹십자, 종근당, 보령, 휴젤, 파마리서치, 펩트론, 오스코텍, 코오롱티슈진, "
        "제넥신, 디앤디파마텍, 인벤티지랩, 큐리옥스바이오시스템즈, 동국제약, 일동제약, 대웅, "
        "한올바이오파마, 크리스탈지노믹스, 메지온, 유틸렉스, 씨젠, 에스디바이오센서, 지씨셀, "
        "차바이오텍, 네이처셀",
    "의료기기·헬스케어":
        "클래시스, 루닛, 뷰노, 인바디, 오스템임플란트, 덴티움, 디오, 레이, 원텍, 제이시스메디칼, "
        "아이센스, 휴메딕스, 바텍, 큐렉소, 딥노이드",
    "로봇":
        "두산로보틱스, 레인보우로보틱스, 로보티즈, 에스피지, 뉴로메카, 휴림로봇, 로보스타, 티로보틱스, "
        "하이젠알앤엠, 에스비비테크, 유일로보틱스, 아진엑스텍, 현대무벡스",
    "방산·우주":
        "한화에어로스페이스, LIG넥스원, 한국항공우주, 현대로템, 한화시스템, 풍산, 빅텍, 스페코, 퍼스텍, "
        "휴니드, 켄코아에어로스페이스, 이노스페이스, 쎄트렉아이, 인텔리안테크, AP위성, 컨텍, "
        "SNT다이내믹스, SNT모티브, 아이쓰리시스템, 한화, 한화비전, 에이스크래프트, 제노코, 아스트",
    "드론·UAM":
        "파블로항공, 오브젝트, 유콘시스템, 두산모빌리티이노베이션",
    "조선·해운":
        "HD한국조선해양, HD현대중공업, 삼성중공업, 한화오션, HD현대미포, HD현대마린솔루션, 한국카본, "
        "동성화인텍, 세진중공업, 삼영엠텍, HMM, 팬오션, 흥아해운, KSS해운, 대한해운, 현대미포조선, "
        "케이에스피, 성광벤드, 하이록코리아, 현대중공업",
    "원전·전력":
        "한국전력, 두산에너빌리티, 한전기술, 한전KPS, 우리기술, 보성파워텍, 비에이치아이, 일진전기, "
        "제룡전기, 산일전기, LS ELECTRIC, LS일렉트릭, HD현대일렉트릭, 효성중공업, 대한전선, LS, 가온전선, "
        "일진파워, 서전기전, 선도전기, 광명전기, 대원전선, 에너토크, 서호전기",
    "수소·신재생":
        "한화솔루션, OCI홀딩스, OCI, 두산퓨얼셀, 에스퓨얼셀, 일진하이솔루스, 씨에스윈드, 대명에너지, "
        "현대에너지솔루션, 신성이엔지, SK오션플랜트, 유니슨, 동국S&C, 삼강엠앤티, 에코바이오, "
        "HD현대에너지솔루션",
    "자동차":
        "현대차, 기아, 현대모비스, 현대위아, 현대오토에버, 만도, HL만도, 한온시스템, 한국타이어앤테크놀로지, "
        "금호타이어, 넥센타이어, 에스엘, 화신, 성우하이텍, 현대글로비스, 서연이화, KG모빌리티, 쌍용차, "
        "대동, 평화정공, 한국단자, 센트랄, 모트렉스, SNT홀딩스, 에스코넥",
    "디스플레이·IT부품":
        "삼성전기, LG이노텍, LG디스플레이, 덕산네오룩스, 아이티엠반도체, 비에이치, 파트론, 뉴프렉스, "
        "인터플렉스, LG전자, 이녹스첨단소재, 캠시스, 엠씨넥스, 아모텍, 세경하이테크, 켐트로닉스, "
        "서울반도체, 서울바이오시스, 와이솔, 알에프텍, 자화전자, 코리아써키트",
    "통신·네트워크":
        "SK텔레콤, KT, LG유플러스, SK브로드밴드, 케이엠더블유, 쏠리드, 우리넷, 에이스테크, 다산네트웍스, "
        "RFHIC, 이노와이어리스, 대한광통신, 오이솔루션, 빛과전자, 코위버, 옵티시스, 유비쿼스, "
        "기가레인, 에이치에프알, 머큐리",
    "소프트웨어·플랫폼":
        "한글과컴퓨터, 더존비즈온, 알서포트, 영림원소프트랩, 한컴, 티맥스소프트, 다우기술, 와이즈넛, "
        "이스트소프트",
    "보안·양자":
        "안랩, 파수, 라온시큐어, 지니언스, 윈스, 소프트캠프, 시큐센, 케이사인, 아이씨티케이, "
        "한국정보인증, 드림시큐리티, 에스넷시스템, 인포바인, SGA, 이글루, 지란지교시큐리티, 싸이버원, "
        "아톤, 이니텍, 한국전자인증",
    "가상자산·블록체인":
        "우리기술투자, 한화투자증권, 다날, 갤럭시아머니트리, 비덴트, 티사이언티픽, 한컴위드, "
        "컴투스홀딩스, 네오위즈홀딩스, 아이티센, 코나아이",
    "엔터":
        "하이브, 에스엠, SM, JYP Ent., JYP엔터테인먼트, 와이지엔터테인먼트, YG엔터테인먼트, 디어유, "
        "에프엔씨엔터, 큐브엔터, 팬엔터테인먼트, 지니뮤직, 키이스트, 판타지오",
    "미디어·콘텐츠":
        "스튜디오드래곤, CJ ENM, 콘텐트리중앙, 쇼박스, NEW, 위지윅스튜디오, 덱스터, 에이스토리, "
        "대원미디어, 초록뱀미디어, 스튜디오산타클로스, 제일기획, 이노션, HS애드, 디앤씨미디어, "
        "키다리스튜디오",
    "게임":
        "크래프톤, 엔씨소프트, 넷마블, 펄어비스, 카카오게임즈, 위메이드, 컴투스, 네오위즈, 웹젠, "
        "데브시스터즈, 시프트업, 선데이토즈, 넥슨게임즈, 위메이드플레이, 조이시티, 엠게임, 그라비티, "
        "액토즈소프트, 한빛소프트, 게임빌, 드래곤플라이, NHN",
    "화장품·소비":
        "아모레퍼시픽, 아모레G, LG생활건강, 코스맥스, 한국콜마, 실리콘투, 에이피알, 클리오, 토니모리, "
        "잇츠한불, 브이티, 달바글로벌, 마녀공장, 아이패밀리에스씨, 제이준코스메틱, 호텔신라, F&F, "
        "한세실업, 영원무역, 휠라홀딩스, 신성통상, 코웨이, 연우, 펌텍코리아, 씨앤씨인터내셔널, "
        "카버코리아, 락앤락, 베이직하우스, 태평양물산, LF",
    "식품·유통":
        "삼양식품, 농심, 오리온, CJ제일제당, 롯데웰푸드, 오뚜기, 하이트진로, 롯데칠성, 풀무원, 대상, "
        "동원F&B, 사조대림, 하림지주, 하림, 빙그레, 매일유업, 남양유업, 크라운해태홀딩스, 이마트, "
        "롯데쇼핑, BGF리테일, GS리테일, 신세계, 현대백화점, 신세계푸드, 동서, 샘표식품, 오리온홀딩스, "
        "지에스홈쇼핑, 현대홈쇼핑, 롯데하이마트, 농심홀딩스",
    "항공·여행":
        "대한항공, 제주항공, 진에어, 티웨이항공, 에어부산, 아시아나항공, 파라다이스, 강원랜드, GKL, "
        "하나투어, 모두투어, 롯데관광개발, 한진칼, 노랑풍선, 참좋은여행, 아난티, 그랜드코리아레저",
    "금융":
        "KB금융, 신한지주, 하나금융지주, 우리금융지주, 기업은행, BNK금융지주, JB금융지주, DGB금융지주, "
        "삼성증권, 미래에셋증권, 키움증권, NH투자증권, 한국금융지주, 대신증권, 메리츠금융지주, 삼성생명, "
        "삼성화재, DB손해보험, 현대해상, 한화생명, 카카오뱅크, 카카오페이, SK증권, 유안타증권, 교보증권, "
        "신영증권, 부국증권, 상상인증권, 한화손해보험, 롯데손해보험, 코리안리, 메리츠화재, 동양생명",
    "건설·부동산":
        "현대건설, 삼성물산, DL이앤씨, GS건설, 대우건설, HDC현대산업개발, 태영건설, 코오롱글로벌, "
        "금호건설, 쌍용C&E, 한일시멘트, 아세아시멘트, 삼표시멘트, KCC, 한샘, 현대엘리베이터, 삼성E&A, "
        "DL, 두산건설, 한신공영, 서희건설, 계룡건설, HL디앤아이한라, 신세계건설, 롯데건설, 동신건설",
    "정유·에너지":
        "S-Oil, 에쓰오일, GS, SK가스, E1, 한국가스공사, 롯데케미칼, 금호석유화학, 대한유화, "
        "포스코인터내셔널, 흥구석유, 중앙에너비스, 극동유화, 한국석유, 대성에너지, 삼천리, 서울가스, "
        "코오롱유화, 롯데정밀화학, 이수화학",
    "철강·소재":
        "POSCO홀딩스, 포스코홀딩스, 현대제철, 동국제강, 고려아연, 영풍, 세아베스틸지주, 세아제강지주, "
        "대한제강, KG스틸, 동국씨엠, 한국철강, 풍산홀딩스, 세아제강, 세아홀딩스, 화승알앤에이, 알루코, "
        "삼아알미늄, 대창단조, 이구산업, 포스코엠텍, 조일알미늄, 한일철강, 문배철강, 대호에이엘",
    "농업·비료":
        "남해화학, 효성오앤비, 조비, 대유, 사조동아원, 팜스코, 이지바이오, 대한제당, 한일사료, 현대사료, "
        "우성사료, 농우바이오, 아시아종묘, 경농, 동방아그로, 선진",
    "교육·출산·키즈":
        "대교, 웅진씽크빅, 메가스터디교육, 디지털대성, 아이스크림에듀, 비상교육, 아가방컴퍼니, 손오공, "
        "오로라, 메가스터디, 이투스, YBM넷, 크레버스, 웅진, NE능률, 지학사, 천재교육, 천재교과서",
}


def _split_words(text):
    return [word.strip() for word in text.split(",") if word.strip()]


def _keyword_regex(words):
    """영문·숫자만으로 된 키워드는 다른 단어 속에서 걸리지 않도록 경계를 둡니다."""

    parts = []

    for word in sorted(set(words), key=len, reverse=True):
        escaped = re.escape(word)

        if re.fullmatch(r"[A-Za-z0-9&+.\-]+", word):
            escaped = rf"(?<![A-Za-z0-9]){escaped}(?![A-Za-z0-9])"

        parts.append(escaped)

    return re.compile("|".join(parts), re.I)


def _norm_name(name):
    return re.sub(r"\s+", "", str(name)).upper()


THEMES = [
    (label, _keyword_regex(_split_words(words)))
    for label, words in THEME_KEYWORDS
]

STOCK_THEME_BY_NAME = {}

for _label, _names in STOCK_THEMES.items():
    for _name in _split_words(_names):
        STOCK_THEME_BY_NAME.setdefault(_norm_name(_name), _label)


def classify_theme(name, texts=()):
    """
    종목 테마 판별.
    1) 대표 종목명 목록에 있으면 그 테마
    2) 종목명 키워드(가중치 3) + 기사 제목 키워드(가중치 1) 점수가 가장 높은 테마
    3) 아무것도 없으면 '주요업종'
    """

    key = _norm_name(name)

    if key in STOCK_THEME_BY_NAME:
        return STOCK_THEME_BY_NAME[key]

    name_text = str(name)
    news_text = " ".join(str(text) for text in texts)

    best_label = DEFAULT_THEME
    best_score = 0

    for label, pattern in THEMES:
        name_hits = {hit.lower() for hit in pattern.findall(name_text)}
        news_hits = {hit.lower() for hit in pattern.findall(news_text)}

        score = 3 * len(name_hits) + len(news_hits)

        if score > best_score:
            best_label, best_score = label, score

    return best_label



EXCLUDE = re.compile(
    r"스팩|제\d+호|ETN|KODEX|TIGER|ACE |SOL |RISE|HANARO|KOSEF|ARIRANG|PLUS |KIWOOM|우$"
)


def secret(key, default=""):
    """Streamlit Secrets 또는 환경변수에서 설정값을 가져옵니다."""
    try:
        value = st.secrets.get(key)
    except Exception:
        value = None

    return str(value) if value else os.getenv(key, default)


def login():
    """사이트 접속 비밀번호 확인."""
    expected = secret("app_password")

    if not expected:
        st.error("Streamlit Secrets에 app_password를 설정하세요.")
        return False

    def verify():
        entered = st.session_state.get("pw", "").encode()
        st.session_state["pw_ok"] = hmac.compare_digest(
            entered,
            expected.encode()
        )
        st.session_state["pw"] = ""

    if st.session_state.get("pw_ok"):
        return True

    st.title("MTNW 시황")

    st.text_input(
        "비밀번호",
        type="password",
        key="pw",
        on_change=verify
    )

    if st.session_state.get("pw_ok") is False:
        st.error("비밀번호가 올바르지 않습니다.")

    return False


if not login():
    st.stop()


CLIENT_ID = secret("TOSS_CLIENT_ID")
CLIENT_SECRET = secret("TOSS_CLIENT_SECRET")

NAVER_ID = secret("NAVER_CLIENT_ID")
NAVER_SECRET = secret("NAVER_CLIENT_SECRET")


class TossError(Exception):
    """토스증권 API 관련 오류."""
    pass


@st.cache_data(ttl=20 * 60 * 60, show_spinner=False)
def get_token(client_id, client_secret):
    """토스증권 API 액세스 토큰 발급."""

    if not client_id or not client_secret:
        raise TossError(
            "TOSS_CLIENT_ID / TOSS_CLIENT_SECRET이 "
            "Streamlit Secrets에 없습니다."
        )

    try:
        response = requests.post(
            f"{BASE}/oauth2/token",
            data={
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": client_secret,
            },
            headers={
                "Content-Type": "application/x-www-form-urlencoded"
            },
            timeout=12,
        )

    except requests.RequestException as error:
        raise TossError(
            f"토큰 서버 연결 실패: {error}"
        ) from error

    if response.status_code != 200:
        raise TossError(
            f"토큰 발급 실패 HTTP {response.status_code}: "
            f"{response.text[:500]}"
        )

    token_data = response.json()
    token = token_data.get("access_token")

    if not token:
        raise TossError(
            "토큰 응답에 access_token이 없습니다: "
            f"{response.text[:300]}"
        )

    return token


@st.cache_resource(show_spinner=False)
def get_feed():
    """웹소켓 연결은 서버(프로세스)당 1개만 만듭니다. (계정당 동시 연결 2개 한도)"""

    return TossFeed(lambda: get_token(CLIENT_ID, CLIENT_SECRET))


def api_get(path, params=None):
    """토스증권 API GET 요청 (연결 오류·5xx는 최대 3회 재시도)."""

    token = get_token(CLIENT_ID, CLIENT_SECRET)

    response = None
    last_error = None

    for attempt in range(3):
        try:
            response = requests.get(
                f"{BASE}{path}",
                params=params or {},
                headers={
                    "Authorization": f"Bearer {token}"
                },
                timeout=(6, 15),
            )

            if response.status_code < 500:
                break

        except requests.RequestException as error:
            last_error = error

        time.sleep(1.5 * (attempt + 1))

    if response is None:
        raise TossError(
            f"{path} 연결 실패({type(last_error).__name__}): "
            f"{str(last_error)[-120:]}"
        )

    if response.status_code != 200:
        try:
            detail = response.json()
        except ValueError:
            detail = response.text[:400]

        if response.status_code == 403:
            raise TossError(
                "HTTP 403: 토스증권 Open API 허용 IP에 "
                "배포 서버 IP를 등록했는지 확인하세요. "
                f"{detail}"
            )

        if response.status_code == 429:
            raise TossError(
                "HTTP 429: 요청 한도 초과. "
                f"Retry-After={response.headers.get('Retry-After', '미제공')}. "
                f"{detail}"
            )

        raise TossError(
            f"{path} HTTP {response.status_code}: {detail}"
        )

    data = response.json()

    if data.get("error"):
        error_data = data["error"]

        raise TossError(
            f"{error_data.get('code')}: "
            f"{error_data.get('message')}"
        )

    return data.get("result", {})


def num(value, default=None):
    """숫자 변환."""

    try:
        return float(str(value).replace(",", ""))

    except (TypeError, ValueError):
        return default


def krx_regular_open(now=None):
    """KRX 정규장(평일 09:00~15:30) 시간인지 확인합니다.
    이 시간 밖에는 토스 체결가에 대체거래소(NXT) 체결이 섞일 수 있어
    KRX 기준 등락률을 흐트러뜨리므로, 네이버(KRX) 값을 그대로 씁니다."""

    now = now or datetime.datetime.now(KST)

    if now.weekday() >= 5:
        return False

    return KRX_OPEN <= now.time() < KRX_CLOSE


def get_indices():
    """
    코스피·코스닥 지수 조회.

    주의:
    아래 시장 지표 API 경로와 응답 필드는
    실제 토스 API 명세에 맞는지 별도 검증이 필요합니다.
    """

    result = api_get(
        "/api/v1/market-indicators/prices",
        {
            "symbols": "KOSPI,KOSDAQ"
        }
    )

    if isinstance(result, dict):
        rows = result.get("prices", [])
    elif isinstance(result, list):
        rows = result
    else:
        rows = []

    by_symbol = {
        item.get("symbol"): item
        for item in rows
        if isinstance(item, dict)
    }

    output = {}

    for symbol in ("KOSPI", "KOSDAQ"):
        item = by_symbol.get(symbol, {})

        price = num(item.get("lastPrice"))
        pct = None

        try:
            candle_result = api_get(
                f"/api/v1/market-indicators/{symbol}/candles",
                {
                    "interval": "1d",
                    "count": 3,
                }
            )

            candles = candle_result.get("candles", [])

            if price is not None and len(candles) >= 2:
                previous_close = num(
                    candles[1].get("closePrice")
                )

                if previous_close:
                    pct = (
                        price / previous_close - 1
                    ) * 100

        except Exception:
            pass

        output[symbol.lower()] = {
            "price": price,
            "pct": pct,
            "timestamp": item.get("timestamp"),
        }

    return output


YAHOO_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; MTNW-Market/1.0)"}


@st.cache_data(ttl=60, show_spinner=False)
def yahoo_quote(symbol):
    """
    야후 파이낸스 차트 API(비공식)로 현재가와 전일 대비 등락률 조회.
    실패하면 None을 반환하므로 앱 전체가 멈추지 않습니다.
    """

    try:
        response = requests.get(
            "https://query1.finance.yahoo.com/v8/finance/chart/"
            f"{quote(symbol, safe='')}",
            params={"range": "1d", "interval": "1d"},
            headers=YAHOO_HEADERS,
            timeout=8,
        )

        if response.status_code != 200:
            return None

        meta = response.json()["chart"]["result"][0]["meta"]

        price = num(meta.get("regularMarketPrice"))

        previous = (
            num(meta.get("chartPreviousClose"))
            or num(meta.get("previousClose"))
        )

        pct = None

        if price is not None and previous:
            pct = (price / previous - 1) * 100

        return {"price": price, "pct": pct}

    except Exception:
        return None


def get_usdkrw():
    """
    원/달러 환율 조회.
    현재 환율은 토스증권 API, 등락률은 야후(KRW=X) 기준입니다.
    토스 조회가 실패하면 야후 값으로 대체합니다.
    """

    price = None

    try:
        result = api_get(
            "/api/v1/exchange-rate",
            {
                "baseCurrency": "USD",
                "quoteCurrency": "KRW",
            }
        )

        if isinstance(result, dict):
            price = num(result.get("rate"))

    except Exception:
        price = None

    fallback = yahoo_quote("KRW=X")

    pct = fallback["pct"] if fallback else None

    if price is None and fallback:
        price = fallback["price"]

    return {"price": price, "pct": pct}


def get_global_markets():
    """나스닥·S&P500 지수와 원/달러 환율 조회."""

    nasdaq = yahoo_quote("^IXIC") or {}
    sp500 = yahoo_quote("^GSPC") or {}

    return {
        "nasdaq": {
            "price": nasdaq.get("price"),
            "pct": nasdaq.get("pct"),
        },
        "sp500": {
            "price": sp500.get("price"),
            "pct": sp500.get("pct"),
        },
        "usdkrw": get_usdkrw(),
    }


def get_stock_names(symbols):
    """종목 코드에 해당하는 종목명 조회."""

    names = {}

    for start in range(0, len(symbols), 200):
        batch = symbols[start:start + 200]

        if not batch:
            continue

        try:
            rows = api_get(
                "/api/v1/stocks",
                {
                    "symbols": ",".join(batch)
                }
            )

            if isinstance(rows, dict):
                rows = rows.get("stocks", [])

            if isinstance(rows, list):
                for row in rows:
                    if not isinstance(row, dict):
                        continue

                    symbol = str(
                        row.get("symbol", "")
                    ).strip()

                    name = (
                        row.get("name")
                        or row.get("stockName")
                        or row.get("displayName")
                    )

                    if symbol and name:
                        names[symbol] = str(name).strip()

        except Exception:
            continue

    return names


def get_rankings(mode="up", duration="1d"):
    """
    상승·하락 종목 순위 조회 ("국내 전체 종목" 모드).

    주의:
    - /api/v1/rankings 및 전달 파라미터는 토스 API의 실제 명세와 일치하는지 확인해야 합니다.
    - 이 모드의 등락률은 토스가 계산한 값이라 KRX 기준가와 다를 수 있습니다.
      (KRX 기준은 '시총 100위 이내' 모드에서 적용됩니다.)
    """

    rank_type = (
        "TOP_GAINERS"
        if mode == "up"
        else "TOP_LOSERS"
    )

    result = api_get(
        "/api/v1/rankings",
        {
            "type": rank_type,
            "marketCountry": "KR",
            "duration": duration,
            "count": 100,
            "excludeInvestmentCaution": True,
        }
    )

    if isinstance(result, list):
        rows = result
        ranked_at = None

    elif isinstance(result, dict):
        rows = result.get("rankings", [])
        ranked_at = result.get("rankedAt")

    else:
        rows = []
        ranked_at = None

    symbols = [
        str(row.get("symbol", "")).strip()
        for row in rows
        if isinstance(row, dict) and row.get("symbol")
    ]

    names = get_stock_names(symbols)

    stocks = []

    for row in rows:
        if not isinstance(row, dict):
            continue

        symbol = str(
            row.get("symbol", "")
        ).strip()

        name = names.get(symbol)

        if not name or EXCLUDE.search(name):
            continue

        price_data = row.get("price") or {}

        change = num(price_data.get("changeRate"))
        price = num(price_data.get("lastPrice"))

        if change is None or price is None:
            continue

        # changeRate가 비율로 반환된다는 전제입니다.
        # 실제 응답 형식에 따라 계산식을 확인해야 합니다.
        pct = change * 100

        if mode == "up" and pct <= 0:
            continue

        if mode == "down" and pct >= 0:
            continue

        theme = classify_theme(name)

        stocks.append({
            "name": name,
            "symbol": symbol,
            "price": price,
            "pct": pct,
            "base_price": num(
                price_data.get("basePrice")
            ),
            "base_source": "toss",
            "rank": row.get("rank"),
            "theme": theme,
            "reason": "토스증권 당일 등락률 순위 기준",
            "timestamp": ranked_at,
        })

    stocks.sort(
        key=lambda stock: stock["pct"],
        reverse=(mode == "up")
    )

    return stocks[:5], ranked_at


def fetch_news(name, today):
    """네이버 뉴스 API를 이용한 당일 관련 기사 조회."""

    if not (NAVER_ID and NAVER_SECRET):
        return []

    try:
        response = requests.get(
            "https://openapi.naver.com/v1/search/news.json",
            params={
                "query": f"{name} 특징주",
                "display": 10,
                "sort": "date",
            },
            headers={
                "X-Naver-Client-Id": NAVER_ID,
                "X-Naver-Client-Secret": NAVER_SECRET,
            },
            timeout=5,
        )

        if response.status_code != 200:
            return []

        found = []

        for item in response.json().get("items", []):
            title = re.sub(
                r"<[^>]+>",
                "",
                item.get("title", "")
            )

            title = html.unescape(title)

            try:
                published = datetime.datetime.strptime(
                    item["pubDate"],
                    "%a, %d %b %Y %H:%M:%S %z"
                ).astimezone(KST)

            except Exception:
                continue

            if published.date() == today and name in title:
                found.append({
                    "title": title,
                    "time": published.strftime("%H:%M"),
                })

        return found[:3]

    except Exception:
        return []


@st.cache_data(ttl=600, show_spinner=False)
def cached_news(name, day_text):
    """실시간 갱신 때마다 뉴스 API를 두드리지 않도록 종목별 10분 캐시."""

    return fetch_news(name, datetime.date.fromisoformat(day_text))


def enrich_stock(stock, day):
    """종목별 당일 기사로 상승/하락 이유와 테마를 채웁니다."""

    headlines = cached_news(stock["name"], day.isoformat())

    if headlines:
        stock["reason"] = headlines[0]["title"]

        stock["theme"] = classify_theme(
            stock["name"],
            [headline["title"] for headline in headlines]
        )


def parse_naver_market_sum(page_html, market):
    """
    네이버 금융 시가총액 페이지(HTML) 한 장을 종목 목록으로 변환.
    열 위치는 표 머리글(현재가·전일비·등락률·시가총액)로 찾으므로 열 순서가 바뀌어도 동작합니다.

    전일비(금액)를 읽어 전일 기준가(base)를 함께 계산합니다.
    네이버는 상승/하락을 글자가 아닌 이미지로 표시하므로, 부호는 표의 등락률과
    일치하는 쪽(현재가 - 전일비 / 현재가 + 전일비)으로 결정합니다.
    어느 쪽도 등락률과 맞지 않으면 기준가는 버립니다(None).
    """

    body = None

    for found in re.finditer(r"<table[^>]*>(.*?)</table>", page_html, re.S):
        if "시가총액" in found.group(1) and "현재가" in found.group(1):
            body = found.group(1)
            break

    if body is None:
        title = re.search(r"<title[^>]*>(.*?)</title>", page_html, re.S)
        title_text = re.sub(r"\s+", " ", title.group(1)).strip() if title else "없음"
        snippet = re.sub(
            r"\s+", " ", re.sub(r"<[^>]+>", " ", page_html)
        ).strip()[:120]

        raise ValueError(
            f"시가총액 표를 찾지 못했습니다. (응답 {len(page_html)}자, "
            f"제목: {title_text[:60]}, 내용: {snippet})"
        )

    def clean(text):
        text = html.unescape(re.sub(r"<[^>]+>", "", text))
        return re.sub(r"\s+", " ", text).strip()

    headers = [
        clean(header)
        for header in re.findall(r"<th[^>]*>(.*?)</th>", body, re.S)
    ]

    try:
        i_price = headers.index("현재가")
        i_pct = headers.index("등락률")
        i_cap = headers.index("시가총액")
    except ValueError as error:
        raise ValueError(f"표 머리글 형식이 달라졌습니다: {headers}") from error

    # 전일비 열은 없어도 동작합니다(그 경우 기준가는 등락률로 역산).
    try:
        i_diff = headers.index("전일비")
    except ValueError:
        i_diff = None

    needed = max(i_price, i_pct, i_cap, i_diff if i_diff is not None else 0)

    rows = []

    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", body, re.S):
        link = re.search(
            r'code=(\d{6})"[^>]*>\s*([^<]+?)\s*</a>',
            tr
        )

        if not link:
            continue

        cells = [
            clean(cell)
            for cell in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)
        ]

        if len(cells) <= needed:
            continue

        price = num(cells[i_price])
        pct = num(cells[i_pct].replace("%", ""))
        cap = num(cells[i_cap])

        if price is None or pct is None or cap is None:
            continue

        # 전일 기준가: 전일비(금액)만 읽고, 부호는 등락률과 맞는 쪽을 골라 결정
        base = None

        if i_diff is not None:
            magnitude = num(re.sub(r"[^\d.,]", "", cells[i_diff]))

            if magnitude is not None:
                for candidate in (price - magnitude, price + magnitude):
                    if (
                        candidate > 0
                        and abs((price / candidate - 1) * 100 - pct) <= 0.05
                    ):
                        base = candidate
                        break

        rows.append({
            "symbol": link.group(1),
            "name": html.unescape(link.group(2)).strip(),
            "price": price,
            "pct": pct,
            "cap": cap,
            "base": base,
            "market": market,
        })

    return rows


@st.cache_data(ttl=600, show_spinner=False)
def fetch_cap_rows_naver(phase):
    """[1순위] 코스피 상위 150개 + 코스닥 상위 100개를 네이버 금융 시가총액 기준으로 수집 (10분 캐시).
    시총 '순위'와 현재가·전일비를 얻는 용도입니다.
    phase('open'/'closed')는 장 시작·마감 시점에 캐시가 바로 새로 만들어지도록 키로만 씁니다."""

    rows = {}

    for sosok, market, pages in ((0, "KOSPI", 3), (1, "KOSDAQ", 2)):
        for page in range(1, pages + 1):
            response = requests.get(
                "https://finance.naver.com/sise/sise_market_sum.naver",
                params={"sosok": sosok, "page": page},
                headers=NAVER_WEB_HEADERS,
                timeout=8,
            )

            if response.status_code != 200:
                raise RuntimeError(
                    f"네이버 금융 HTTP {response.status_code}"
                )

            page_html = response.content.decode("cp949", errors="ignore")

            parsed = parse_naver_market_sum(page_html, market)

            if len(parsed) < 10:
                raise RuntimeError(
                    "네이버 금융 시가총액 표에서 종목을 충분히 읽지 못했습니다."
                )

            for row in parsed:
                rows[row["symbol"]] = row

    return list(rows.values())


def parse_naver_daily_close(page_html, before_text):
    """네이버 금융 일별 시세(sise_day) HTML에서 before_text(YYYY-MM-DD) 이전
    가장 최근 거래일의 종가를 돌려줍니다. 못 찾으면 None."""

    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", page_html, re.S):
        found = re.search(r"(\d{4})\.(\d{2})\.(\d{2})", tr)

        if not found:
            continue

        day_iso = f"{found.group(1)}-{found.group(2)}-{found.group(3)}"

        # 오늘(장중이면 진행 중인 값) 이후는 건너뛰고, 직전 거래일 행을 찾습니다.
        if day_iso >= before_text:
            continue

        # 열 순서: 날짜, 종가, 전일비, 시가, 고가, 저가, 거래량 → 첫 숫자가 종가
        values = re.findall(
            r'class="tah p11[^"]*"[^>]*>\s*([\d,]+)\s*<',
            tr
        )

        if values:
            return num(values[0])

    return None


@st.cache_data(ttl=6 * 60 * 60, show_spinner=False)
def fetch_krx_prev_close(symbol, day_text):
    """종목 1개의 전일 KRX 정규장 종가(15:30). 6시간 캐시.
    실패하면 예외를 던집니다(예외는 캐시되지 않아 다음에 다시 시도합니다)."""

    response = requests.get(
        "https://finance.naver.com/item/sise_day.naver",
        params={"code": symbol, "page": 1},
        headers=NAVER_WEB_HEADERS,
        timeout=8,
    )

    if response.status_code != 200:
        raise RuntimeError(f"네이버 일별 시세 HTTP {response.status_code}")

    page_html = response.content.decode("cp949", errors="ignore")

    close = parse_naver_daily_close(page_html, day_text)

    if not close:
        raise RuntimeError(f"{symbol} 전일 종가를 찾지 못했습니다.")

    return close


def fetch_krx_prev_closes(symbols, day_text):
    """여러 종목의 전일 KRX 종가를 모읍니다. 실패한 종목은 결과에서 빠집니다."""

    def one(symbol):
        try:
            return symbol, fetch_krx_prev_close(symbol, day_text)
        except Exception:
            return symbol, None

    with ThreadPoolExecutor(max_workers=6) as pool:
        return {
            symbol: close
            for symbol, close in pool.map(one, symbols)
            if close
        }


@st.cache_data(ttl=12 * 60 * 60, show_spinner=False)
def fetch_share_universe():
    """KOSPI·KOSDAQ 보통주의 이름·발행주식수 (하루 2회 정도만 갱신)."""

    universe = {}

    for market in ("KOSPI", "KOSDAQ"):
        listed = api_get(
            "/api/v1/stocks/all",
            {
                "market": market,
                "status": "ACTIVE",
                "securityType": "STOCK",
                "commonShare": "true",
            },
        )

        symbols = [
            row["symbol"]
            for row in listed
            if isinstance(row, dict) and row.get("symbol")
        ]

        for start in range(0, len(symbols), 200):
            batch = symbols[start:start + 200]

            infos = api_get(
                "/api/v1/stocks",
                {"symbols": ",".join(batch)}
            )

            for info in infos:
                shares = num(info.get("sharesOutstanding"))

                if not shares:
                    continue

                universe[info["symbol"]] = {
                    "name": info.get("name", ""),
                    "shares": shares,
                    "market": market,
                }

    return universe


@st.cache_data(ttl=6 * 60 * 60, show_spinner=False)
def fetch_cap_candidates(day_text):
    """토스 API로 전체 종목 시총(현재가 × 발행주식수)을 계산해 상위 300개 후보를 고릅니다 (6시간 캐시)."""

    universe = fetch_share_universe()
    symbols = list(universe)

    caps = []

    for start in range(0, len(symbols), 200):
        batch = symbols[start:start + 200]

        prices = api_get(
            "/api/v1/prices",
            {"symbols": ",".join(batch)}
        )

        for item in prices:
            price = num(item.get("lastPrice"))
            info = universe.get(item.get("symbol"))

            if price and info and not EXCLUDE.search(info["name"]):
                caps.append((price * info["shares"], item["symbol"]))

    caps.sort(reverse=True)

    return [symbol for _, symbol in caps[:300]]


@st.cache_data(ttl=60, show_spinner=False)
def fetch_cap_rows_toss():
    """[2순위] 토스 API만으로 시총 상위 종목 목록 (평소에는 후보 300개 시세만 조회).
    기준가는 refresh_with_toss에서 토스 상·하한가로 채웁니다."""

    universe = fetch_share_universe()

    candidates = fetch_cap_candidates(
        datetime.datetime.now(KST).date().isoformat()
    )

    last = fetch_toss_prices(tuple(candidates))

    rows = []

    for symbol in candidates:
        price = last.get(symbol)
        info = universe.get(symbol)

        if price and info:
            rows.append({
                "symbol": symbol,
                "name": info["name"],
                "market": info["market"],
                "price": price,
                "pct": 0.0,
                "cap": price * info["shares"],
                "base": None,
            })

    return rows


def fetch_cap_rows():
    """네이버 우선, 실패하면 토스 계산 방식으로 전환. (rows, 출처)를 돌려줍니다."""

    phase = "open" if krx_regular_open() else "closed"

    try:
        rows = fetch_cap_rows_naver(phase)
        return rows, "naver"

    except Exception as naver_error:
        try:
            rows = fetch_cap_rows_toss()
            return rows, "toss"

        except Exception as toss_error:
            raise RuntimeError(
                f"네이버 실패({naver_error}) / 토스 실패({toss_error})"
            )


@st.cache_data(ttl=6 * 60 * 60, show_spinner=False)
def fetch_prev_closes(symbols, day_text):
    """[마지막 수단] 토스 일봉의 종목별 전일 종가 (하루 단위로 캐시).
    KRX 기준가를 얻지 못한 종목에만 씁니다. 일봉에는 대체거래소(NXT) 체결이 섞여
    있을 수 있어 KRX 등락률과 다를 수 있습니다."""

    def one(symbol):
        try:
            result = api_get(
                "/api/v1/candles",
                {
                    "symbol": symbol,
                    "interval": "1d",
                    "count": 3,
                    "adjusted": "false",
                }
            )

            for candle in result.get("candles", []):
                day = str(candle.get("timestamp", ""))[:10]

                if day and day < day_text:
                    return symbol, num(candle.get("closePrice"))

        except Exception:
            pass

        return symbol, None

    with ThreadPoolExecutor(max_workers=4) as pool:
        return {
            symbol: prev
            for symbol, prev in pool.map(one, symbols)
            if prev
        }


@st.cache_data(ttl=30, show_spinner=False)
def fetch_toss_prices(symbols):
    """토스 현재가 일괄 조회 (30초 캐시)."""

    last = {}

    for start in range(0, len(symbols), 200):
        batch = symbols[start:start + 200]

        prices = api_get(
            "/api/v1/prices",
            {"symbols": ",".join(batch)}
        )

        for item in prices:
            if isinstance(item, dict):
                price = num(item.get("lastPrice"))

                if price:
                    last[item.get("symbol")] = price

    return last


_LIMIT_FAIL_AT = {}  # 상·하한가 조회에 실패한 종목은 60초 동안 재시도하지 않음


def kr_tick(price):
    """KRX 주식(ETF 제외) 호가 단위."""

    for bound, tick in (
        (2000, 1), (5000, 5), (20000, 10),
        (50000, 50), (200000, 100), (500000, 500),
    ):
        if price < bound:
            return tick

    return 1000


def limit_prices(base, mode="base"):
    """전일 기준가 → (상한가, 하한가).
    상한가 = 기준가 × 130% 를 호가 단위 미만 절사,
    하한가 = 기준가 × 70% 를 호가 단위 미만 절상.
    mode='base' : 기준가 가격대의 호가 단위로 맞춤 (HMM·에이피알처럼 가격대가 갈리는 종목에서 맞는 방식)
    mode='own'  : 계산된 상·하한가 각자의 가격대 호가 단위로 맞춤"""

    base = int(round(base))

    if mode == "base":
        up_tick = low_tick = kr_tick(base)
    else:
        up_tick = kr_tick(base * 13 / 10)
        low_tick = kr_tick(base * 7 / 10)

    upper = (base * 13 // (10 * up_tick)) * up_tick
    lower = -(-(base * 7) // (10 * low_tick)) * low_tick

    return upper, lower


def derive_base_from_limits(upper, lower, hint=None):
    """당일 상·하한가에 정확히 들어맞는 전일 기준가(호가 단위 위의 값)를 찾습니다.
    두 가지 반올림 규칙을 모두 시도하고, 그래도 없으면 상·하한가 범위의 중간값을 가장 가까운 호가에 맞춰 씁니다.
    반환: (기준가, 후보 수). 후보 수가 2 이상이면 ±1호가 오차 가능.
    hint(네이버 기준가 등)가 있으면 그에 가까운 후보를 고릅니다."""

    upper = int(round(upper))
    lower = int(round(lower))

    center = upper / 1.3
    start = int(center * 0.97)
    price = max(1, start - start % kr_tick(start))
    end = int(center * 1.03) + 1

    found = set()

    while price <= end:
        for mode in ("base", "own"):
            if limit_prices(price, mode) == (upper, lower):
                found.add(price)

        price += kr_tick(price)

    found = sorted(found)

    if found:
        if hint:
            return min(found, key=lambda value: abs(value - hint)), len(found)

        return found[len(found) // 2], len(found)

    # 정확히 맞는 값이 없을 때: 상한가·하한가가 각각 가리키는 범위의 중간값
    estimate = (upper / 1.3 + lower / 0.7) / 2

    if hint and abs(hint - estimate) <= estimate * 0.01:
        estimate = hint

    tick = kr_tick(estimate)
    snapped = int(round(estimate / tick) * tick)

    if snapped <= 0:
        return None, 0

    return snapped, 2


@st.cache_data(ttl=6 * 60 * 60, show_spinner=False)
def fetch_toss_limit(symbol, day_text):
    """토스 당일 상·하한가 (종목별 하루 캐시). 실패하면 예외(캐시되지 않음)."""

    result = api_get("/api/v1/price-limits", {"symbol": symbol})

    upper = num(result.get("upperLimitPrice")) if isinstance(result, dict) else None
    lower = num(result.get("lowerLimitPrice")) if isinstance(result, dict) else None

    if not upper or not lower:
        raise RuntimeError(f"{symbol} 상·하한가 없음")

    return upper, lower


def fetch_toss_limit_bases(symbols, hints, day_text):
    """상·하한가에서 KRX 전일 기준가를 역산합니다.
    반환: ({종목: (기준가, 후보 수)}, {종목: (상한가, 하한가)})"""

    def one(symbol):
        if _LIMIT_FAIL_AT.get(symbol, 0) > time.time() - 60:
            return symbol, None

        for attempt in range(3):
            try:
                return symbol, fetch_toss_limit(symbol, day_text)

            except Exception as error:
                if "429" in str(error):
                    time.sleep(1.0 + attempt)  # 요청 한도: 잠깐 쉬고 재시도
                    continue

                break

        _LIMIT_FAIL_AT[symbol] = time.time()

        return symbol, None

    with ThreadPoolExecutor(max_workers=3) as pool:
        limits = {
            symbol: value
            for symbol, value in pool.map(one, symbols)
            if value
        }

    bases = {}

    for symbol, (upper, lower) in limits.items():
        base, count = derive_base_from_limits(upper, lower, hints.get(symbol))

        if base:
            bases[symbol] = (base, count)

    return bases, limits


def refresh_with_toss(stocks, source="naver"):
    """
    등락률 = (현재가 ÷ 전일 KRX 기준가 - 1) × 100

    기준가(base_price) 우선순위 — 모두 '전일 KRX 정규장 종가(15:30)' 기준입니다
      1) 토스 당일 상·하한가에서 역산 (거래소가 기준가 × 130%/70%로 정하는 값) → 'limit'
      2) 네이버 시총 표의 전일비로 구한 기준가                               → 'krx'
      3) 전일 KRX 정규장 종가 (네이버 일별 시세)                              → 'krx_close'
      4) 네이버 현재가·등락률로 역산한 기준가 (반올림 오차 가능)              → 'krx_calc'
      5) 토스 일봉 전일 종가 (NXT 체결이 섞일 수 있음)                        → 'toss'

    현재가
      - KRX 정규장 중: 토스 현재가(REST)
      - 정규장 밖: 네이버(KRX) 현재가 그대로 (대체거래소 체결이 섞이지 않도록)
    """

    if not stocks:
        return stocks

    today_text = datetime.datetime.now(KST).date().isoformat()

    # 순서만 바뀌어도 캐시가 깨지지 않게 정렬해서 키로 씁니다
    symbols = tuple(sorted(stock["symbol"] for stock in stocks))

    # 진단용: 네이버가 알려준 기준가를 따로 보관
    for stock in stocks:
        stock["naver_base"] = stock.get("base_price")

        if not stock["naver_base"] and source == "naver" and stock["pct"] > -100 and stock["price"]:
            stock["naver_base_calc"] = stock["price"] / (1 + stock["pct"] / 100)

    # 1) 토스 상·하한가로 KRX 전일 기준가 역산
    hints = {
        stock["symbol"]: stock.get("naver_base") or stock.get("naver_base_calc")
        for stock in stocks
    }

    try:
        limit_bases, limits = fetch_toss_limit_bases(symbols, hints, today_text)
    except Exception:
        limit_bases, limits = {}, {}

    for stock in stocks:
        if stock["symbol"] in limits:
            stock["limits"] = limits[stock["symbol"]]

        found = limit_bases.get(stock["symbol"])

        if found:
            stock["base_price"] = found[0]
            stock["base_ambiguous"] = found[1] > 1
            stock["base_source"] = "limit"

    # 2) 네이버 전일비 기준가가 있는 종목은 그대로 두고,
    # 3) 없는 종목만 일별 시세의 전일 종가를 조회합니다.
    missing = tuple(sorted(
        stock["symbol"] for stock in stocks if not stock.get("base_price")
    ))

    try:
        krx_close = fetch_krx_prev_closes(missing, today_text) if missing else {}
    except Exception:
        krx_close = {}

    for stock in stocks:
        if stock.get("base_source") == "limit":
            continue

        if stock.get("base_price"):
            stock["base_source"] = "krx"
            continue

        close = krx_close.get(stock["symbol"])

        if close:
            stock["base_price"] = close
            stock["base_source"] = "krx_close"
            continue

        # 4) 네이버 현재가·등락률로 역산
        if source == "naver" and stock["pct"] > -100 and stock["price"]:
            stock["base_price"] = stock["price"] / (1 + stock["pct"] / 100)
            stock["base_source"] = "krx_calc"

    # 5) 그래도 없는 종목만 토스 일봉 전일 종가
    need_prev = tuple(sorted(
        stock["symbol"] for stock in stocks if not stock.get("base_price")
    ))

    if need_prev:
        try:
            prev = fetch_prev_closes(need_prev, today_text)
        except Exception:
            prev = {}

        for stock in stocks:
            if not stock.get("base_price"):
                fallback = prev.get(stock["symbol"])

                if fallback:
                    stock["base_price"] = fallback

                stock["base_source"] = "toss"

    # 현재가: 정규장 중에는 토스 현재가, 장외에는 네이버(KRX) 현재가 유지
    # (네이버를 못 써서 토스 목록으로 대체된 경우에는 토스 현재가가 이미 들어 있습니다)
    last = {}

    if krx_regular_open():
        try:
            last = fetch_toss_prices(symbols)
        except Exception:
            last = {}

    for stock in stocks:
        price = last.get(stock["symbol"])

        if price:
            stock["price"] = price

        base = stock.get("base_price")

        if base and stock.get("price"):
            stock["pct"] = (stock["price"] / base - 1) * 100

    return stocks


def apply_realtime(stocks):
    """웹소켓 체결가가 정상 수신 중이고 KRX 정규장 시간이면 현재가·등락률을 실시간 값으로 덮어씁니다.
    (정규장 밖에는 대체거래소 체결이 섞일 수 있어 사용하지 않습니다.)"""

    feed = get_feed()

    symbols = tuple(stock["symbol"] for stock in stocks)
    feed.set_symbols(symbols)

    if not krx_regular_open():
        return stocks  # KRX 값 유지

    status = feed.status()

    live_ok = (
        status["connected"]
        and status["age"] is not None
        and status["age"] < 30
    )

    if not live_ok:
        return stocks  # REST 값 유지

    prices = feed.get_prices(symbols)

    for stock in stocks:
        price = prices.get(stock["symbol"])
        base = stock.get("base_price")

        if price and base:
            stock["price"] = price
            stock["pct"] = (price / base - 1) * 100
            stock["live"] = True

    return stocks


def render_feed_status():
    """웹소켓 연결 상태 한 줄 표시."""

    s = get_feed().status()

    if s["connected"] and s["age"] is not None and s["age"] < 30:
        st.caption(
            f"🟢 실시간 체결 수신 중 · 시세 수신 {s['priced']}/{s['wanted']}종목 "
            f"· 마지막 수신 {s['age']:.1f}초 전"
        )

    elif s["connected"]:
        st.caption(
            "🟠 웹소켓 연결됨 · 최근 30초간 체결 없음(장외·휴장이면 정상) → 토스 REST 시세로 표시"
        )

    else:
        st.caption(
            "🔴 웹소켓 끊김 → 토스 REST 시세(최대 30초 지연)로 표시 · "
            f"{s['error'] or '재연결 중'}"
        )

    if s["rejected"]:
        st.warning(f"구독 거부된 종목: {s['rejected'][:3]}")


def render_basis_status(snapshot):
    """등락률 기준가 사용 현황을 알려주고, 종목별 기준가를 직접 검증할 수 있게 합니다."""

    shown = snapshot.get("gainers", []) + snapshot.get("losers", [])

    if not shown:
        return

    weak = [
        stock["name"]
        for stock in shown
        if stock.get("base_source") not in ("limit", "krx", "krx_close")
    ]

    # 토스 상·하한가 기준가와 네이버 기준가가 다른 종목 (둘 중 하나가 KRX 기준이 아니라는 신호)
    mismatch = [
        stock["name"]
        for stock in shown
        if stock.get("base_source") == "limit"
        and stock.get("naver_base")
        and abs(stock["base_price"] - stock["naver_base"]) > 1.5 * kr_tick(stock["base_price"])
    ]

    if not weak:
        st.caption(
            "✅ 등락률 기준: 전일 KRX 기준가 "
            "(정규장 중에는 토스 체결가 ÷ 전일 기준가, 장외에는 KRX 마감 값)"
        )

    else:
        st.caption(
            "⚠️ 일부 종목은 KRX 전일 기준가를 확인하지 못해 보조 기준가로 계산되었습니다. "
            f"KRX 등락률과 다를 수 있습니다: {', '.join(weak[:5])}"
        )

    if mismatch:
        st.warning(
            "토스 기준가(상·하한가 역산)와 네이버 기준가가 다른 종목이 있습니다: "
            f"{', '.join(mismatch[:5])} — 아래 '기준가 확인'에서 값을 비교해 주세요."
        )

    with st.expander("📋 등락률 기준가 확인 (종목별 전일 기준가)"):
        lines = [
            "| 종목 | 현재가 | 기준가(전일) | 등락률 | 기준가 출처 | 네이버 기준가 | 토스 상한가 / 하한가 |",
            "|---|---:|---:|---:|---|---:|---|",
        ]

        for stock in shown:
            base = stock.get("base_price")
            naver_base = stock.get("naver_base") or stock.get("naver_base_calc")
            limits = stock.get("limits")

            lines.append(
                f"| {cell_text(stock['name'])} "
                f"| {fmt(stock.get('price'), 0)}원 "
                f"| {fmt(base, 0) + '원' + (' (±1호가)' if stock.get('base_ambiguous') else '') if base else '확인 불가'} "
                f"| {pct_text_trunc(stock.get('pct'))} "
                f"| {BASE_SOURCE_LABELS.get(stock.get('base_source'), '확인 불가')} "
                f"| {fmt(naver_base, 0) + '원' if naver_base else '-'} "
                f"| {fmt(limits[0], 0) + ' / ' + fmt(limits[1], 0) if limits else '-'} |"
            )

        st.markdown("\n".join(lines))

        st.caption(
            "증권사 앱의 '전일 종가'와 기준가가 다르면 종목명과 값을 알려주세요. "
            "상한가 ≈ 기준가 × 1.3, 하한가 ≈ 기준가 × 0.7 입니다."
        )


def get_cap_rankings(mode="up"):
    """시가총액 상위 100위(우선주·스팩 등 제외)를 전일 KRX 기준가로 재계산한 뒤,
    등락률 순으로 급등/급락 TOP 5를 뽑습니다."""

    cap_rows, source = fetch_cap_rows()

    candidates = [
        row
        for row in cap_rows
        if not EXCLUDE.search(row["name"])
    ]

    candidates.sort(key=lambda row: row["cap"], reverse=True)

    universe = candidates[:CAP_RANK_LIMIT]

    if len(universe) < 50:
        raise RuntimeError("시가총액 상위 종목 수가 부족합니다.")

    ranked_at = datetime.datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")

    stocks = []

    for cap_rank, row in enumerate(universe, start=1):
        stocks.append({
            "name": row["name"],
            "symbol": row["symbol"],
            "price": row["price"],
            "pct": row["pct"],
            "base_price": row.get("base"),
            "base_source": None,
            "rank": cap_rank,
            "theme": classify_theme(row["name"]),
            "reason": "시가총액 상위 100위 내 당일 등락률 기준 (전일 KRX 기준가 대비)",
            "timestamp": ranked_at,
        })

    # 1) 전일 KRX 기준가 확보 + 정규장 중 토스 현재가로 등락률 계산
    # 2) 웹소켓 체결가가 정상 수신 중이고 정규장이면 실시간 값으로 덮어쓰기
    stocks = refresh_with_toss(stocks, source)
    stocks = apply_realtime(stocks)

    if mode == "up":
        picked = [stock for stock in stocks if stock["pct"] > 0]
    else:
        picked = [stock for stock in stocks if stock["pct"] < 0]

    picked.sort(
        key=lambda stock: stock["pct"],
        reverse=(mode == "up")
    )

    return picked[:5], ranked_at


def refresh_live(snapshot):
    """시총 100위 모드에서 TOP5를 실시간 체결가로 다시 계산해 snapshot에 반영."""

    gainers, up_at = get_cap_rankings("up")
    losers, down_at = get_cap_rankings("down")

    today = datetime.datetime.now(KST).date()

    for stock in gainers + losers:
        enrich_stock(stock, today)

    snapshot["gainers"] = gainers
    snapshot["losers"] = losers
    snapshot["ranking_at"] = up_at or down_at
    snapshot["fetched_at"] = datetime.datetime.now(KST).strftime("%H:%M:%S")


def fetch_snapshot(duration, universe="all"):
    """지수와 급등·급락 종목 데이터를 한 번에 조회."""

    now = datetime.datetime.now(KST)

    indices = get_indices()

    global_markets = get_global_markets()

    actual_universe = universe
    note = None

    gainers, losers, up_at, down_at = [], [], None, None

    if universe == "cap100":
        try:
            gainers, up_at = get_cap_rankings("up")
            losers, down_at = get_cap_rankings("down")

            if duration != "1d":
                note = (
                    "시총 100위 이내 모드는 당일(전일 대비) 등락률 기준으로만 "
                    "조회됩니다. 선택한 기간은 적용되지 않았습니다."
                )

        except Exception as error:
            actual_universe = "all"
            note = (
                "시총 100위 목록을 가져오지 못해 국내 전체 종목 기준으로 "
                f"표시합니다. 이 모드의 등락률은 토스 기준이라 KRX와 다를 수 있습니다. ({error})"
            )

    if actual_universe == "all":
        gainers, up_at = get_rankings(
            "up",
            duration
        )

        losers, down_at = get_rankings(
            "down",
            duration
        )

    for stock in gainers + losers:
        enrich_stock(stock, now.date())

    return {
        "date": (
            f"{now.year}년 "
            f"{now.month:02d}월 "
            f"{now.day:02d}일 "
            f"({WEEKDAYS[now.weekday()]})"
        ),
        "fetched_at": now.strftime("%H:%M:%S"),
        "ranking_at": up_at or down_at,
        "duration": duration,
        "universe": actual_universe,
        "note": note,
        "indices": indices,
        "global": global_markets,
        "gainers": gainers,
        "losers": losers,
    }


ISSUE_STOPWORDS = set(
    "코스피 코스닥 증시 마감 오늘 전망 상승 하락 시장 개장 장중 지수 주가 투자 경제 한국 미국 "
    "국내 올해 이번 관련 발표 대한 위한 통해 강세 약세 출발 전일 대비 억원 조원 속보 종합 "
    "기자 뉴스 외국인 기관 개인 순매수 순매도 매수 매도 거래 거래일 종목 주식 시황 하루 이날 "
    "현재 오후 오전 정부 기업 업계 가운데 이어 다시 가장 최대 최고 최저 예상 확대 감소 증가 기록".split()
)

DEFAULT_ISSUE_TITLES = {
    "당일 상승률 상위 종목 흐름",
    "당일 하락률 상위 종목 흐름",
    "시장 변동성 확인",
}

ARTICLE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9",
}

RELATED_NAMES = sorted(
    {
        name
        for names in STOCK_THEMES.values()
        for name in _split_words(names)
        if len(name) >= 3 or name == "기아"
    },
    key=len,
    reverse=True,
)

RELATED_REGEX = _keyword_regex(RELATED_NAMES)


def clean_news_text(text):
    text = html.unescape(re.sub(r"<[^>]+>", "", str(text)))
    return re.sub(r"\s+", " ", text).strip()


def clean_title(title):
    title = re.sub(r"^\s*(\[[^\]]{1,10}\]\s*)+", "", title)
    return clean_news_text(title)[:70].rstrip()


def _meta_content(page, prop):
    for pattern in (
        rf'<meta[^>]+(?:property|name)=["\']{prop}["\'][^>]+content=["\']([^"\']*)',
        rf'<meta[^>]+content=["\']([^"\']*)["\'][^>]+(?:property|name)=["\']{prop}["\']',
    ):
        found = re.search(pattern, page, re.I)

        if found:
            return html.unescape(found.group(1)).strip()

    return ""


def _html_to_lines(fragment, min_len):
    fragment = re.sub(r"<br\s*/?>|</p>|</div>|</li>|</h\d>", "\n", fragment, flags=re.I)
    fragment = html.unescape(re.sub(r"<[^>]+>", " ", fragment))

    lines = []

    for line in fragment.splitlines():
        line = re.sub(r"\s+", " ", line).strip()

        if len(line) >= min_len:
            lines.append(line)

    return lines


def parse_article_html(page):
    """기사 페이지 HTML에서 (제목, 본문)을 뽑습니다. 외부 라이브러리 없이 정규식으로 처리."""

    title = _meta_content(page, "og:title")

    if not title:
        found = re.search(r"<title[^>]*>(.*?)</title>", page, re.S | re.I)
        title = clean_news_text(found.group(1)) if found else ""

    page = re.sub(r"<!--.*?-->", " ", page, flags=re.S)

    page = re.sub(
        r"<(script|style|noscript|iframe|header|footer|nav|aside|form)\b.*?</\1>",
        " ",
        page,
        flags=re.S | re.I,
    )

    articles = re.findall(r"<article\b[^>]*>(.*?)</article>", page, re.S | re.I)

    lines = []

    if articles:
        lines = _html_to_lines(max(articles, key=len), 20)

    if sum(len(line) for line in lines) < 200:
        lines = _html_to_lines(page, 40)

    body = "\n".join(lines)

    return title, body[:6000]


def fetch_article(url):
    """기사 링크에서 제목과 본문을 가져옵니다."""

    response = requests.get(url, headers=ARTICLE_HEADERS, timeout=10)

    if response.status_code != 200:
        raise RuntimeError(f"HTTP {response.status_code}")

    encoding = None

    if "charset" in response.headers.get("content-type", "").lower():
        encoding = response.encoding

    if not encoding:
        head = response.content[:3000].decode("ascii", errors="ignore")
        found = re.search(r'charset=["\']?([\w-]+)', head, re.I)
        encoding = found.group(1) if found else (response.apparent_encoding or "utf-8")

    try:
        page = response.content.decode(encoding, errors="ignore")
    except LookupError:
        page = response.content.decode("utf-8", errors="ignore")

    title, body = parse_article_html(page)

    if len(body) < 100:
        raise RuntimeError(
            "본문을 읽지 못했습니다(로그인·구독 필요 또는 동적 페이지). "
            "기사 내용을 아래 칸에 직접 붙여넣어 주세요."
        )

    return title, body


def extractive_summary(text, max_sentences=3, max_chars=260):
    """AI 키가 없을 때: 핵심 단어가 많이 들어간 문장을 골라 원문 순서대로 잇습니다."""

    text = re.sub(r"\s+", " ", text)

    sentences = [
        s.strip()
        for s in re.split(r"(?<=[.!?])\s+", text)
        if 20 <= len(s.strip()) <= 220
    ]

    if not sentences:
        return text[:max_chars]

    df = Counter()

    for sentence in sentences:
        df.update(issue_tokens(sentence))

    scored = []

    for index, sentence in enumerate(sentences):
        tokens = issue_tokens(sentence)

        if not tokens:
            continue

        score = sum(df[t] for t in tokens) / len(tokens) + (2 if index < 2 else 0)
        scored.append((score, index, sentence))

    picked = sorted(sorted(scored, reverse=True)[:max_sentences], key=lambda x: x[1])

    result = " ".join(sentence for _, _, sentence in picked)

    return result[:max_chars + 40]


def ai_summary(text):
    """ANTHROPIC_API_KEY가 있으면 Claude로 핵심 요약. 실패하면 예외."""

    response = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": secret("ANTHROPIC_API_KEY"),
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": secret("ANTHROPIC_MODEL", "claude-haiku-5-5"),
            "max_tokens": 500,
            "messages": [{
                "role": "user",
                "content": (
                    "다음 경제 기사에서 투자자가 알아야 할 핵심만 한국어로 2~3문장, "
                    "180자 안팎으로 정리해 주세요. 기사 문장을 그대로 옮기지 말고 "
                    "자신의 말로 요약하고, 제목·머리말·따옴표 없이 본문만 쓰세요.\n\n"
                    + text[:6000]
                ),
            }],
        },
        timeout=40,
    )

    if response.status_code != 200:
        raise RuntimeError(f"HTTP {response.status_code}: {response.text[:200]}")

    parts = response.json().get("content", [])

    result = "".join(p.get("text", "") for p in parts if p.get("type") == "text").strip()

    if not result:
        raise RuntimeError("빈 응답")

    return result


def summarize_text(text):
    """(요약, 방식) 반환. AI 키가 있으면 AI, 없거나 실패하면 핵심 문장 추출."""

    if secret("ANTHROPIC_API_KEY"):
        try:
            return ai_summary(text), "AI 요약"

        except Exception as error:
            return extractive_summary(text), f"핵심 문장 추출 (AI 실패: {error})"

    return extractive_summary(text), "핵심 문장 추출"


def _fill_related(i, text):
    state = st.session_state

    if state.get(f"is{i}s", "").strip():
        return

    names = [
        name
        for name, _ in Counter(RELATED_REGEX.findall(text)).most_common(4)
    ]

    if names:
        state[f"is{i}s"] = ", ".join(names)


def load_article(i):
    """[기사 불러오기] 링크의 기사 본문을 기사 내용 칸에 넣습니다."""

    state = st.session_state
    url = state.get(f"is{i}link", "").strip()

    if not url:
        state[f"is{i}note"] = "기사 링크를 먼저 입력해 주세요."
        return

    if not url.lower().startswith("http"):
        url = "https://" + url
        state[f"is{i}link"] = url

    try:
        title, body = fetch_article(url)

    except Exception as error:
        state[f"is{i}note"] = f"기사 불러오기 실패: {error}"
        return

    state[f"is{i}src"] = body

    current = state.get(f"is{i}t", "").strip()

    if title and (not current or current in DEFAULT_ISSUE_TITLES):
        state[f"is{i}t"] = clean_title(title)

    _fill_related(i, f"{title} {body}")

    state[f"is{i}note"] = f"기사 {len(body):,}자를 불러왔습니다. [핵심 정리]를 눌러 보세요."


def summarize_issue(i):
    """[핵심 정리] 기사 내용 칸을 핵심만 남겨 '내용' 칸에 넣습니다."""

    state = st.session_state
    source = state.get(f"is{i}src", "").strip()

    # 기사 내용 칸에 링크만 붙여넣은 경우: 링크로 보고 먼저 불러옴
    if re.fullmatch(r"https?://\S+", source):
        state[f"is{i}link"] = source
        state[f"is{i}src"] = ""
        source = ""

    if not source:
        if state.get(f"is{i}link", "").strip():
            load_article(i)
            source = state.get(f"is{i}src", "").strip()

        if not source:
            state.setdefault(f"is{i}note", "")

            if not state[f"is{i}note"]:
                state[f"is{i}note"] = "링크를 넣거나 기사 내용을 붙여넣어 주세요."

            return

    summary, how = summarize_text(source)

    state[f"is{i}c"] = summary

    _fill_related(i, source)

    state[f"is{i}note"] = f"핵심 정리 완료 ({how}). 내용 칸에서 자유롭게 고치세요."


def issue_tokens(title):
    """기사 제목에서 주제 판별용 핵심 단어를 뽑습니다."""

    tokens = set()

    for word in re.findall(r"[가-힣A-Za-z0-9]{2,}", title):
        if (
            re.fullmatch(r"[가-힣]+", word)
            and len(word) >= 3
            and word[-1] in "은는이가을를의에도로과와"
        ):
            word = word[:-1]

        if word in ISSUE_STOPWORDS or word.isdigit():
            continue

        tokens.add(word.upper() if word.isascii() else word)

    return tokens




@st.cache_data(ttl=12 * 60 * 60, show_spinner=False)
def fetch_sector_depths():
    """섹터 ID → 계층 깊이(0 대분류, 1 중분류, 2 소분류). 하루 2회만 갱신."""

    result = api_get("/api/v1/sectors")

    rows = result.get("sectors", []) if isinstance(result, dict) else result

    return {
        str(row.get("sectorId")): row.get("depth")
        for row in rows
        if isinstance(row, dict)
    }


def _fetch_strong_themes_openapi(limit=5):
    """
    오늘(조회 시점까지) 강했던 섹터.
    중분류 섹터 중 '거래대금 상위'이면서 '상승률이 높은' 순서로 고릅니다.
    반환: [{"name", "pct", "amount"}, ...]
    """

    try:
        depths = fetch_sector_depths()
    except Exception:
        depths = {}

    def ranking(rank_type):
        result = api_get(
            "/api/v1/sectors/rankings",
            {"type": rank_type, "marketCountry": "KR", "duration": "1d"}
        )

        rows = result.get("rankings", []) if isinstance(result, dict) else result

        return [row for row in rows if isinstance(row, dict)]

    gain_rows = ranking("TOP_GAINERS")
    amount_rows = ranking("MARKET_TRADING_AMOUNT")

    def at_depth(rows, depth):
        if not depths:
            return rows

        return [
            row for row in rows
            if depths.get(str(row.get("sectorId"))) == depth
        ]

    def pick(depth, use_activity):
        rows = [
            row for row in at_depth(gain_rows, depth)
            if (num(row.get("changeRate")) or 0) > 0
        ]

        if use_activity:
            active = {
                str(row.get("sectorId"))
                for row in at_depth(amount_rows, depth)[:25]
            }

            rows = [row for row in rows if str(row.get("sectorId")) in active]

        rows.sort(key=lambda row: num(row.get("changeRate")) or 0, reverse=True)

        return rows

    # 1) 중분류 + 거래대금 상위 → 2) 중분류 전체 → 3) 대분류까지 확대
    rows = pick(1, True)

    if len(rows) < 3:
        rows += [row for row in pick(1, False) if row not in rows]

    if len(rows) < 3:
        rows += [row for row in pick(0, False) if row not in rows]

    themes = []

    for row in rows[:limit]:
        themes.append({
            "name": str(row.get("name", "")).strip(),
            "pct": (num(row.get("changeRate")) or 0) * 100,
            "amount": num(row.get("tradingAmount")),
        })

    return themes




def _toss_web_request(path, method="GET", params=None, body=None):
    """토스증권 공개 웹 API 요청. 인증·계좌·주문 API는 사용하지 않습니다."""
    url = f"{TOSS_WEB_BASE}{path}"
    try:
        if method.upper() == "POST":
            response = requests.post(
                url,
                params=params or {},
                json=body or {},
                headers=TOSS_WEB_HEADERS,
                timeout=(5, 12),
            )
        else:
            response = requests.get(
                url,
                params=params or {},
                headers=TOSS_WEB_HEADERS,
                timeout=(5, 12),
            )
    except requests.RequestException as error:
        raise TossError(f"토스증권 산업 데이터 연결 실패: {error}") from error

    if response.status_code in (403, 429):
        raise TossError(
            f"토스증권 공개 웹 API 접근 제한(HTTP {response.status_code}). "
            "잠시 후 다시 시도해 주세요."
        )
    if not response.ok:
        raise TossError(
            f"토스증권 산업 데이터 조회 실패 HTTP {response.status_code}: "
            f"{response.text[:250]}"
        )

    try:
        payload = response.json()
    except ValueError as error:
        raise TossError("토스증권 산업 데이터 응답이 JSON 형식이 아닙니다.") from error

    # 대부분의 공개 API는 result에 실제 데이터가 들어오지만,
    # 응답 형태가 달라지는 경우를 고려해 result가 있을 때만 벗깁니다.
    if isinstance(payload, dict) and "result" in payload:
        return payload["result"]
    return payload


@st.cache_data(ttl=180, show_spinner=False)
def fetch_toss_dashboard_news(news_type="HOT", index_code=None, limit=12):
    """토스증권 공개 대시보드 뉴스. 개인화 피드는 호출하지 않습니다."""
    allowed_types = {"ALL_HIGHLIGHT", "HOT", "SOARING_STOCK", "INDEX"}
    if news_type not in allowed_types:
        news_type = "HOT"

    body = {"type": news_type}
    if news_type == "INDEX":
        body["indexCode"] = index_code or "KGG01P"

    try:
        payload = _toss_web_request(
            "/api/v1/dashboard/wts/news",
            method="POST",
            body=body,
        )
    except Exception:
        return []

    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        rows = payload.get("news") or payload.get("items") or payload.get("results") or []
        if isinstance(rows, dict):
            rows = rows.get("news") or rows.get("items") or []
    else:
        rows = []

    normalized = []
    seen = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        title = (
            row.get("title") or row.get("headline") or row.get("newsTitle")
            or row.get("displayTitle") or row.get("name") or ""
        )
        title = re.sub(r"\\s+", " ", str(title)).strip()
        if not title or title in seen:
            continue
        seen.add(title)
        url = (
            row.get("url") or row.get("link") or row.get("newsUrl")
            or row.get("originalUrl") or row.get("articleUrl") or ""
        )
        summary = (
            row.get("summary") or row.get("description") or row.get("content")
            or row.get("subTitle") or ""
        )
        source = (
            row.get("source") or row.get("pressName") or row.get("providerName")
            or row.get("mediaName") or ""
        )
        published = (
            row.get("publishedAt") or row.get("createdAt") or row.get("date")
            or row.get("datetime") or ""
        )
        normalized.append({
            "title": title,
            "url": str(url).strip() if url else "",
            "summary": re.sub(r"\\s+", " ", str(summary)).strip(),
            "source": str(source).strip() if source else "",
            "published": str(published).strip() if published else "",
        })
        if len(normalized) >= max(1, min(int(limit), 20)):
            break

    return normalized


def use_toss_news_for_issue(issue_number, item):
    """선택한 토스증권 뉴스 제목/링크를 시황 이슈 입력칸으로 보냅니다."""
    state = st.session_state
    title = item.get("title", "").strip()
    if title:
        state[f"is{issue_number}t"] = clean_title(title)
    if item.get("url"):
        state[f"is{issue_number}link"] = item["url"]
        state[f"is{issue_number}note"] = "토스증권 뉴스에서 제목과 링크를 가져왔습니다. 기사 불러오기를 눌러 본문을 가져오세요."
    elif item.get("summary"):
        state[f"is{issue_number}src"] = f"{title}\\n\\n{item['summary']}"
        state[f"is{issue_number}note"] = "토스증권 뉴스의 요약을 기사 내용 칸에 넣었습니다. 확인 후 핵심 정리를 눌러 주세요."
    else:
        state[f"is{issue_number}note"] = "뉴스 제목을 가져왔습니다. 기사 링크가 제공되지 않아 본문은 직접 입력해 주세요."


@st.cache_data(ttl=60, show_spinner=False)
def fetch_public_tics_ranking(sort_by="FLUCTUATION_RATE"):
    """토스증권 웹 화면의 현재 국내 산업(TICS) 순위 스냅샷."""
    if sort_by not in {"FLUCTUATION_RATE", "TRADING_AMOUNT"}:
        raise ValueError("지원하지 않는 산업 순위 정렬 기준입니다.")

    result = _toss_web_request(
        "/api/v2/dashboard/wts/overview/tics/ranking",
        method="POST",
        body={
            "nation": "KR",
            "duration": "1d",
            "sortBy": sort_by,
        },
    )
    rows = result.get("tics", []) if isinstance(result, dict) else result
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _normalize_public_rate(value):
    """토스 웹 응답의 비율 값을 화면 표시용 퍼센트로 정규화합니다."""
    value = num(value)
    if value is None:
        return None
    # 공개 TICS API의 fluctuationRate/changeRate는 비율형으로 내려오는 경우가 있어
    # 절댓값 1 이하 값은 소수 비율로 간주합니다.
    return value * 100 if abs(value) <= 1 else value


def _public_stock_name(row):
    for key in ("name", "stockName", "productName", "companyName", "displayName", "title"):
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


@st.cache_data(ttl=120, show_spinner=False)
def fetch_public_sector_stocks(tics_id, limit=5):
    """특정 TICS 산업의 대표 종목을 시가총액 내림차순으로 최대 5개 조회합니다."""
    tics_id = str(tics_id or "").strip()
    if not tics_id.isdigit():
        return []

    result = _toss_web_request(
        f"/api/v2/dashboard/wts/overview/tics/{tics_id}/stocks",
        method="POST",
        body={
            "nation": "KR",
            "sortBy": "MARKET_CAP",
            "sortOrder": "DESC",
            "page": 1,
        },
    )
    rows = result.get("stocks", []) if isinstance(result, dict) else []
    stocks = []

    for row in rows:
        if not isinstance(row, dict):
            continue
        name = _public_stock_name(row)
        if not name:
            continue

        rate_value = None
        for key in ("changeRate", "fluctuationRate", "changeRateRatio"):
            if row.get(key) is not None:
                rate_value = _normalize_public_rate(row.get(key))
                break

        stocks.append({
            "name": name,
            "code": str(
                row.get("productCode")
                or row.get("code")
                or row.get("stockCode")
                or ""
            ),
            "pct": rate_value,
        })
        if len(stocks) >= max(1, min(int(limit), 5)):
            break

    return stocks


@st.cache_data(ttl=60, show_spinner=False)
def fetch_strong_themes(limit=5):
    """
    토스증권 웹의 현재 산업 순위를 우선 사용합니다.
    상승률 상위 산업 중 거래대금 순위에도 포함된 산업을 먼저 고르고,
    각 산업의 대표 종목은 시가총액 내림차순으로 최대 5개 조회합니다.
    공개 웹 API가 실패하면 기존 공식 Open API 기반 섹터 조회로 대체합니다.
    """
    try:
        gain_rows = fetch_public_tics_ranking("FLUCTUATION_RATE")
        amount_rows = fetch_public_tics_ranking("TRADING_AMOUNT")

        active_ids = {
            str(row.get("ticsId"))
            for row in amount_rows[:25]
            if row.get("ticsId") is not None
        }

        candidates = []
        for row in gain_rows:
            tics_id = row.get("ticsId")
            name = str(row.get("name") or row.get("title") or "").strip()
            raw_rate = row.get("fluctuationRate")
            pct = _normalize_public_rate(raw_rate)

            if tics_id is None or not name or pct is None or pct <= 0:
                continue

            candidates.append({
                "id": str(tics_id),
                "name": name,
                "pct": pct,
                "amount": num(
                    row.get("tradingAmountKrw")
                    or row.get("tradingAmount")
                    or row.get("totalTradingAmountKrw")
                ),
                "leading_stock": (
                    _public_stock_name(row.get("leadingStock"))
                    if isinstance(row.get("leadingStock"), dict)
                    else str(row.get("leadingStock") or "").strip()
                ),
                "_active": str(tics_id) in active_ids,
            })

        # 거래대금 상위 25개 산업과 겹치는 상승 산업을 우선합니다.
        active = [row for row in candidates if row["_active"]]
        active.sort(key=lambda row: row["pct"], reverse=True)
        candidates.sort(key=lambda row: row["pct"], reverse=True)
        selected = active[:limit]

        if len(selected) < min(3, limit):
            seen_ids = {row["id"] for row in selected}
            selected.extend(
                row for row in candidates
                if row["id"] not in seen_ids
            )
            selected = selected[:limit]

        # 대표 종목 조회는 표시할 상위 산업에 대해서만 제한적으로 실행합니다.
        for item in selected:
            try:
                item["stocks"] = fetch_public_sector_stocks(item["id"], limit=5)
            except Exception:
                # 한 산업의 종목 목록 조회가 실패해도 나머지 산업 순위는 유지합니다.
                item["stocks"] = []
            item["stock_names"] = [stock["name"] for stock in item["stocks"]]
            item.pop("_active", None)

        if selected:
            return selected

    except Exception:
        # 공개 웹 API 응답 변경/일시 장애 시 기존 기능이 완전히 멈추지 않도록
        # 공식 Open API 기반 섹터 랭킹을 예비 경로로 유지합니다.
        pass

    fallback = _fetch_strong_themes_openapi(limit=limit)
    for item in fallback:
        item.setdefault("id", "")
        item.setdefault("stocks", [])
        item.setdefault("stock_names", [])
        item.setdefault("leading_stock", "")
    return fallback


def theme_hashtag(name):
    """섹터명을 해시태그로. 공백·슬래시는 제거합니다."""

    return "#" + re.sub(r"[\s/#]+", "", name)


def autofill_themes():
    """'오늘 강했던 테마' 칸을 토스 섹터 랭킹으로 채웁니다 (덮어쓰기)."""

    state = st.session_state

    try:
        themes = fetch_strong_themes()

    except Exception as error:
        state["themes_note"] = f"테마 자동 불러오기 실패: {error}"
        return

    themes = [theme for theme in themes if theme["name"]]
    state["sector_items"] = themes

    if not themes:
        state["themes_note"] = (
            "상승한 섹터를 찾지 못했습니다(장 시작 전이거나 데이터 없음). 직접 입력해 주세요."
        )
        return

    state["themes"] = " ".join(theme_hashtag(theme["name"]) for theme in themes)

    stock_sector_count = sum(1 for theme in themes if theme.get("stock_names"))
    state["themes_note"] = (
        "토스증권 산업 순위 기준(거래대금 상위 산업 우선, 상승률순) · "
        + " · ".join(f"{t['name']} {t['pct']:+.2f}%" for t in themes)
        + f" · 주요 종목 {stock_sector_count}개 산업 조회"
        + f" · 갱신 {datetime.datetime.now(KST).strftime('%H:%M:%S')}"
    )


# ---------------------------------------------------------------
# 한 줄 요약 자동 생성
# 기사 내용이 없으면 현재 시장 데이터로 생성하고,
# 기사 내용이 있으면 기사 + 산업/종목 흐름을 함께 반영합니다.
# ---------------------------------------------------------------
def collect_summary_inputs(snapshot=None):
    state = st.session_state
    snapshot = snapshot or state.get("snapshot", {}) or {}

    # 강세 산업/섹터: 토스 섹터 랭킹을 우선 사용
    sector_items = [
        item for item in (state.get("sector_items") or [])
        if isinstance(item, dict) and item.get("name")
    ]
    if not sector_items:
        try:
            sector_items = [
                item for item in fetch_strong_themes(limit=5)
                if isinstance(item, dict) and item.get("name")
            ]
            state["sector_items"] = sector_items
        except Exception as error:
            state["summary_note"] = f"섹터 조회 실패: {error}"

    sector_names = [item["name"] for item in sector_items[:3]]

    # 수동 입력한 테마가 있으면 자동 조회 결과와 함께 보조 자료로 활용
    typed_themes = str(state.get("themes", "")).strip()
    if not sector_names and typed_themes:
        sector_names = [
            re.sub(r"^#", "", x.strip())
            for x in re.split(r"[\s,·]+", typed_themes)
            if x.strip()
        ][:3]

    gainers = snapshot.get("gainers", []) or []
    losers = snapshot.get("losers", []) or []
    rising_names = [
        str(row.get("name", "")).strip()
        for row in gainers[:3]
        if isinstance(row, dict) and row.get("name")
    ]
    falling_names = [
        str(row.get("name", "")).strip()
        for row in losers[:3]
        if isinstance(row, dict) and row.get("name")
    ]

    indices = snapshot.get("indices", {}) or {}
    market_lines = []
    for key, label in (("kospi", "코스피"), ("kosdaq", "코스닥")):
        item = indices.get(key, {}) or {}
        pct = num(item.get("pct"))
        if pct is None:
            market_lines.append(f"{label}: 등락률 확인 불가")
        else:
            direction = "상승" if pct > 0 else "하락" if pct < 0 else "보합"
            market_lines.append(f"{label}: {direction}({pct:+.2f}%)")

    # 각 이슈의 제목, 정리된 내용, 원문을 모두 확인하되
    # 동일 기사 내용이 중복으로 들어가지 않도록 정리합니다.
    issue_titles = []
    article_blocks = []
    seen_text = set()
    default_titles = {
        "당일 상승률 상위 종목 흐름",
        "당일 하락률 상위 종목 흐름",
        "시장 변동성 확인",
    }

    for i in range(1, 4):
        title = str(state.get(f"is{i}t", "")).strip()
        content = str(state.get(f"is{i}c", "")).strip()
        source = str(state.get(f"is{i}src", "")).strip()

        if title and title not in default_titles and title not in issue_titles:
            issue_titles.append(title)

        # 정리된 내용이 있으면 우선 사용하고, 없으면 기사 원문 사용
        body = content or source
        if body:
            normalized = re.sub(r"\s+", " ", body).strip()
            if normalized and normalized not in seen_text:
                seen_text.add(normalized)
                article_blocks.append((title or f"이슈 {i}", normalized[:5000]))

    return {
        "sectors": sector_names,
        "sector_items": sector_items[:5],
        "gainers": rising_names,
        "losers": falling_names,
        "market": market_lines,
        "issue_titles": issue_titles,
        "articles": article_blocks,
        "date": str(snapshot.get("date", datetime.datetime.now(KST).strftime("%Y-%m-%d"))),
    }


def ai_market_summary(data):
    """AI 키가 설정된 경우 기사와 시장 데이터를 종합해 한 문장으로 생성합니다."""
    article_text = "\n\n".join(
        f"[기사 {i}: {title}]\n{body}"
        for i, (title, body) in enumerate(data["articles"], start=1)
    )
    if not article_text:
        article_text = "입력된 기사가 없습니다. 시장 데이터만 사용하세요."

    market_context = "\n".join([
        f"기준일: {data['date']}",
        f"강세 산업/섹터: {', '.join(data['sectors']) or '확인 불가'}",
        "산업별 대표 종목: " + " / ".join(
            f"{item.get('name')}: {', '.join(item.get('stock_names', [])[:5])}"
            for item in data.get("sector_items", [])
            if item.get("stock_names")
        ),
        f"급등 종목: {', '.join(data['gainers']) or '확인 불가'}",
        f"급락 종목: {', '.join(data['losers']) or '확인 불가'}",
        f"시장 지수: {' / '.join(data['market']) or '확인 불가'}",
        f"이슈 제목: {' / '.join(data['issue_titles']) or '없음'}",
    ])

    response = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": secret("ANTHROPIC_API_KEY"),
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": secret("ANTHROPIC_MODEL", "claude-haiku-5-5"),
            "max_tokens": 220,
            "messages": [{
                "role": "user",
                "content": (
                    "당신은 경제·금융 분야에서 독자의 시선을 붙잡는 스타 기자이자 경제부 데스크입니다. "
                    "결과물은 보고서식 요약이 아니라, 실제 주요 경제 매체의 메인 기사에 실릴 법한 "
                    "완성도 높은 헤드라인 단 한 줄입니다. 보통 25~45자 안팎으로 작성하세요.\n\n"
                    "[헤드라인 작성 방식]\n"
                    "1. 먼저 자료에서 가장 중요한 뉴스 가치 하나를 고르세요: 시장의 대비, 예상 밖의 변화, "
                    "돈이 몰린 곳, 실적·정책 변수, 업종 간 온도차, 투자자들이 주목할 만한 쟁점 등입니다.\n"
                    "2. 첫 단어부터 핵심을 보여주세요. 기업·산업·지수·정책·실적 등 구체적인 주체를 앞세우고, "
                    "뒤에는 변화나 긴장감을 짧고 힘 있게 붙이세요.\n"
                    "3. 단순 나열 대신 대비와 리듬을 살리세요. 예: '반도체는 웃고 바이오는 숨 고르기', "
                    "'엇갈린 코스피·코스닥…온도차 키운 종목 장세', '실적이 가른 희비…○○에 쏠린 시선'. "
                    "예시는 문체 참고용이며, 실제 자료가 뒷받침할 때만 사용하세요.\n"
                    "4. '시장을 확인할 필요가 있다', '관심이 필요하다', '흐름을 살펴보자'처럼 밋밋한 설명형 마무리는 금지합니다. "
                    "기사 제목다운 밀도와 리듬을 갖추되, 의미가 불분명한 말장난은 피하세요.\n"
                    "5. 여러 기사가 입력되면 가장 중요한 공통 쟁점이나 서로 맞물리는 흐름을 찾아 하나로 압축하세요. "
                    "기사 제목을 단순히 이어 붙이지 마세요. 기사 사이의 연관성이 확인되지 않으면 억지로 엮지 마세요.\n"
                    "6. 기사가 없으면 산업·종목·지수 데이터에서 실제로 확인되는 가장 뚜렷한 대비나 집중 현상을 선택하세요. "
                    "데이터가 부족하면 무리하게 드라마를 만들지 말고, 확인 가능한 사실을 선명하게 표현하세요.\n\n"
                    "[정확성·법적 리스크 방지 원칙 — 반드시 준수]\n"
                    "7. 자료에 없는 사실·원인·인과관계·전망·수치·투자자 심리를 절대 만들어내지 마세요. "
                    "단순 동시 발생을 원인과 결과로 단정하지 마세요.\n"
                    "8. '폭등', '대폭락', '패닉', '대란', '충격', '역대급', '상한가 임박', '급등 예고' 등 "
                    "강한 표현은 데이터와 사실관계가 명확히 뒷받침되지 않으면 사용하지 마세요. '급등·급락'도 실제 등락 자료가 있을 때만 쓰세요.\n"
                    "9. 수익 보장·매수 유도·매도 유도·특정 종목 추천처럼 읽힐 표현을 쓰지 마세요. "
                    "확인되지 않은 의혹이나 루머를 사실처럼 표현하지 말고, 기업·개인에 대한 단정적 비난도 금지합니다.\n"
                    "10. 독자의 시선을 끄는 것은 과장이 아니라 정확한 핵심과 선명한 대비로 만드세요. "
                    "자료에 근거한 범위 안에서 가장 힘 있는 제목을 선택하세요.\n"
                    "11. 헤드라인만 출력하세요. 설명, 번호, 따옴표, '한 줄 요약:' 같은 접두어는 쓰지 마세요.\n\n"
                    "[시장 데이터]\n" + market_context +
                    "\n\n[기사 내용]\n" + article_text[:12000]
                ),
            }],
        },
        timeout=40,
    )

    if response.status_code != 200:
        raise RuntimeError(f"AI 요약 HTTP {response.status_code}: {response.text[:200]}")

    parts = response.json().get("content", [])
    result = "".join(
        part.get("text", "") for part in parts if part.get("type") == "text"
    ).strip()
    result = re.sub(r"\s+", " ", result).strip(" \"'“”‘’")
    if not result:
        raise RuntimeError("AI 요약 결과가 비어 있습니다.")
    return result


def fallback_market_summary(data):
    """AI API가 없을 때도 확인된 정보만으로 기사형 헤드라인을 구성합니다."""
    sectors = [str(x).strip() for x in data.get("sectors", []) if str(x).strip()]
    gainers = [str(x).strip() for x in data.get("gainers", []) if str(x).strip()]
    losers = [str(x).strip() for x in data.get("losers", []) if str(x).strip()]
    markets = [str(x).strip() for x in data.get("market", []) if str(x).strip() and "확인 불가" not in str(x)]
    titles = [str(x).strip() for x in data.get("issue_titles", []) if str(x).strip()]
    articles = data.get("articles", []) or []

    def polish_headline(value, limit=72):
        value = re.sub(r"\s+", " ", str(value)).strip(" .;:—-…")
        value = re.sub(r"^(한편|또한|이와 함께|이에 따라|이날|최근|한편으로는)\s*", "", value)
        # 보고서식 종결 표현을 제목형으로 정리
        value = re.sub(r"(확인할 필요가 있다|관심이 필요하다|살펴볼 필요가 있다|주목할 필요가 있다)[.!]?", "", value).strip()
        if len(value) > limit:
            value = value[:limit - 1].rsplit(" ", 1)[0] + "…"
        return value or "오늘 증시, 주요 산업과 종목 흐름에 주목"

    # 기사 제목을 우선하되, 관련성이 불명확한 서로 다른 제목을 억지로 연결하지 않습니다.
    if titles:
        headline = polish_headline(titles[0], 72)
        if len(headline) < 18 and articles:
            body = articles[0][1]
            key_sentence = extractive_summary(body, max_sentences=1, max_chars=90).strip(" .;。")
            if key_sentence and key_sentence.lower() not in headline.lower():
                headline = f"{headline}…{polish_headline(key_sentence, 52)}"
        return polish_headline(headline, 72)

    # 제목은 없지만 기사 본문이 있다면 첫 핵심 문장을 헤드라인 길이로 다듬습니다.
    if articles:
        joined = " ".join(body for _, body in articles)
        extracted = extractive_summary(joined, max_sentences=1, max_chars=100).strip(" .;。")
        if extracted:
            extracted = polish_headline(extracted, 58)
            # 단순 설명문에 제목형 리듬을 부여하되, 원문에 없는 사실은 추가하지 않습니다.
            if len(extracted) >= 24 and not re.search(r"[.!?…]$", extracted):
                extracted = extracted.rstrip(" ,") + "…"
            return extracted

    # 기사 없이 시장 데이터만 있는 경우, 확인 가능한 상승·하락 대비를 중심으로 구성합니다.
    sector_text = "·".join(sectors[:2])
    rising_text = "·".join(gainers[:2])
    falling_text = "·".join(losers[:2])
    up_markets = [x.split(":", 1)[0] for x in markets if "상승" in x]
    down_markets = [x.split(":", 1)[0] for x in markets if "하락" in x]

    if sector_text and rising_text and falling_text:
        headline = f"{sector_text} 강세 속 종목별 희비…{rising_text} 상승·{falling_text} 하락"
    elif sector_text and rising_text:
        headline = f"{sector_text} 강세에 시선…상승 흐름 보인 {rising_text}"
    elif rising_text and falling_text:
        headline = f"엇갈린 종목 흐름…{rising_text} 상승·{falling_text} 하락"
    elif sector_text and down_markets:
        headline = f"{sector_text} 강세와 엇갈린 지수…{', '.join(down_markets)} 하락"
    elif sector_text and up_markets:
        headline = f"{sector_text} 강세…{', '.join(up_markets)} 상승 흐름"
    elif rising_text:
        headline = f"상승률 상위에 포착된 {rising_text}…종목별 강세"
    elif falling_text:
        headline = f"하락률 상위에 오른 {falling_text}…약세 종목 부각"
    elif up_markets and down_markets:
        headline = f"코스피·코스닥 엇갈린 흐름…{', '.join(up_markets)} 상승·{', '.join(down_markets)} 하락"
    elif up_markets:
        headline = f"{', '.join(up_markets)} 상승…지수 흐름은 강세"
    elif down_markets:
        headline = f"{', '.join(down_markets)} 하락…약세 흐름 나타나"
    elif sector_text:
        headline = f"오늘 강세 확인된 산업은 {sector_text}…섹터별 온도차"
    else:
        headline = "엇갈린 종목 흐름 속 오늘 증시의 주요 변수는"

    return polish_headline(headline, 72)


def generate_market_summary(snapshot=None):
    """기사 입력 여부와 관계없이 현재 데이터로 한 줄 요약을 생성합니다."""
    data = collect_summary_inputs(snapshot)

    if secret("ANTHROPIC_API_KEY"):
        try:
            result = ai_market_summary(data)
            st.session_state["summary_note"] = (
                "AI 요약 완료 · "
                + ("입력 기사와 시장 데이터를 종합했습니다." if data["articles"]
                   else "기사 없이 현재 시장 데이터를 바탕으로 생성했습니다.")
            )
            return result
        except Exception as error:
            result = fallback_market_summary(data)
            st.session_state["summary_note"] = f"AI 요약 실패로 자동 조합 방식을 사용했습니다: {error}"
            return result

    result = fallback_market_summary(data)
    st.session_state["summary_note"] = (
        "규칙 기반 요약 · 기사 내용이 있으면 핵심 문장과 시장 데이터를 조합합니다. "
        "기사 전체를 의미적으로 종합하는 AI 요약은 ANTHROPIC_API_KEY 설정 시 사용할 수 있습니다."
    )
    return result


def autofill_summary():
    """버튼 클릭 시 최신 입력 기사와 시장 데이터를 반영해 요약을 다시 생성합니다."""
    state = st.session_state
    snapshot = state.get("snapshot", {})
    try:
        with st.spinner("시장 데이터와 입력 기사를 종합하는 중..."):
            state["summary"] = generate_market_summary(snapshot)
    except Exception as error:
        state["summary_note"] = f"한 줄 요약 생성 실패: {error}"


NEXT_DAY_LINE = re.compile(
    r"^(다음 영업일은 |\d{1,2}월 \d{1,2}일\([월화수목금토일]\) 정규장)"
)


def kr_date_text(day):
    """'10월 30일(금)' 형태. 슬래시(/)는 체크 포인트 구분자와 겹쳐서 쓰지 않습니다."""
    return f"{day.month}월 {day.day}일({WEEKDAYS[day.weekday()]})"


def next_business_info():
    """(다음 영업일, 개장 시각, 캘린더 조회 성공 여부)"""

    try:
        result = api_get("/api/v1/market-calendar/KR")

        nxt = result.get("nextBusinessDay") or {}
        day = nxt.get("date")

        regular = (
            (nxt.get("integrated") or {}).get("regularMarket") or {}
        ).get("startTime")

        open_time = str(regular)[11:16] if regular else "09:00"

        if day:
            return datetime.date.fromisoformat(day), open_time, True

    except Exception:
        pass

    day = datetime.datetime.now(KST).date() + datetime.timedelta(days=1)

    while day.weekday() >= 5:
        day += datetime.timedelta(days=1)

    return day, "09:00", False


def build_next_day_line():
    """내일 체크 포인트의 첫 줄: 다음 영업일 안내(휴장 포함)."""

    today = datetime.datetime.now(KST).date()

    nxt, open_time, ok = next_business_info()

    # 오늘과 다음 영업일 사이의 평일 = 휴장일
    closed = [
        today + datetime.timedelta(days=d)
        for d in range(1, max((nxt - today).days, 1))
        if (today + datetime.timedelta(days=d)).weekday() < 5
    ]

    if closed:
        names = ", ".join(kr_date_text(day) for day in closed)
        return f"다음 영업일은 {kr_date_text(nxt)}, {names} 휴장", ok

    return f"{kr_date_text(nxt)} 정규장 {open_time} 개장", ok


def autofill_checks():
    """'내일 체크 포인트' 맨 위에 다음 영업일 안내 한 줄을 넣습니다 (기존 내용은 유지)."""

    state = st.session_state

    line, ok = build_next_day_line()

    rest = [
        row
        for row in state.get("checks", "").splitlines()
        if row.strip() and not NEXT_DAY_LINE.match(row.strip())
    ]

    state["checks"] = "\n".join([line] + rest)

    state["checks_note"] = (
        "토스 장 캘린더 기준으로 첫 줄에 넣었습니다. 나머지 일정은 직접 이어서 적어 주세요."
        if ok
        else "캘린더 조회에 실패해 평일 기준으로 계산했습니다. 공휴일 여부는 직접 확인해 주세요."
    )


def fmt(value, digits=2):
    """숫자 표시 형식."""

    if value is None:
        return "확인 불가"

    return f"{value:,.{digits}f}"


def pct_text(value):
    """등락률 표시 형식."""

    if value is None:
        return "확인 불가"

    return f"{value:+.2f}%"


def pct_text_trunc(value):
    """등락률을 소수 둘째 자리에서 버림 처리 (토스증권 표시 방식)."""

    if value is None:
        return "확인 불가"

    truncated = math.trunc(round(value * 100, 6)) / 100

    return f"{truncated:+.2f}%"


def arrow(value):
    """등락률에 따른 아이콘."""

    if value is not None and value > 0:
        return "🔺"

    if value is not None and value < 0:
        return "🔻"

    return "➖"


def md_lines(text, indent=""):
    """입력창(textarea)의 줄바꿈을 마크다운 줄바꿈으로 변환."""

    rows = [row.rstrip() for row in str(text).strip().splitlines()]

    if indent:
        rows = [row.strip() for row in rows if row.strip()]

    return f"  \n{indent}".join(rows)


def one_line(text):
    """굵게·목록 제목처럼 한 줄이어야 하는 값의 줄바꿈 제거."""

    return " ".join(str(text).split())


def cell_text(text):
    """표 셀 안에서 깨지지 않도록 줄바꿈과 세로선(|)을 정리."""

    return one_line(text).replace("|", "/")


def stock_table_lines(stocks, reason_header, show_reason=True):
    """급등·급락 종목 마크다운 표. 이유 열은 선택적으로 뺄 수 있습니다."""

    if show_reason:
        lines = [
            f"| 종목 | 현재가 | 등락률 | {reason_header} | 관련 테마 |",
            "|---|---:|---:|---|---|",
        ]
    else:
        lines = [
            "| 종목 | 현재가 | 등락률 | 관련 테마 |",
            "|---|---:|---:|---|",
        ]

    for stock in stocks:
        cells = [
            f"**{cell_text(stock['name'])}**",
            f"{fmt(stock['price'], 0)}원",
            f"**{pct_text_trunc(stock['pct'])}**",
        ]

        if show_reason:
            cells.append(cell_text(stock["reason"]))

        cells.append(cell_text(stock["theme"]))

        lines.append("| " + " | ".join(cells) + " |")

    if not stocks:
        cells = ["데이터 없음", "확인 불가", "확인 불가"]

        if show_reason:
            cells.append("API 응답 확인 필요")

        cells.append("-")

        lines.append("| " + " | ".join(cells) + " |")

    return lines


def build_markdown(snapshot, time_text="확인 불가"):
    """시황글 마크다운 생성."""

    session = st.session_state

    summary_text = md_lines(
        session.get(
            "summary",
            "당일 등락률 상위 종목과 시장 흐름을 확인하세요."
        )
    )

    lines = [
        f"# 📅 {snapshot['date']} | 오후 시황",
        "",
        f"📌 **한 줄 요약**: {summary_text}",
        "",
        "### 장중 지수 현황",
        "",
        "| 지수 | 현재가 | 등락률 | 기준 시각 |",
        "|---|---:|---:|---|",
    ]

    for key, label in (
        ("kospi", "코스피"),
        ("kosdaq", "코스닥"),
    ):
        data = snapshot["indices"].get(key, {})

        lines.append(
            f"| {label} "
            f"| {fmt(data.get('price'))} "
            f"| {arrow(data.get('pct'))} {pct_text(data.get('pct'))} "
            f"| {time_text} |"
        )

    lines += [
        "",
        "",
        "### 🌎 해외 지수·환율",
        "",
        "| 구분 | 현재가 | 등락률 | 기준 시각 |",
        "|---|---:|---:|---|",
    ]

    for key, label, unit in GLOBAL_ITEMS:
        data = snapshot.get("global", {}).get(key, {})
        price = data.get("price")

        price_text = (
            "확인 불가"
            if price is None
            else f"{fmt(price)}{unit}"
        )

        lines.append(
            f"| {label} "
            f"| {price_text} "
            f"| {arrow(data.get('pct'))} {pct_text(data.get('pct'))} "
            f"| {time_text} |"
        )

    suffix = (
        CAP_SUFFIX
        if snapshot.get("universe") == "cap100"
        else ""
    )

    up_note = POST_NOTE if session.get("show_up_reason_note") else ""
    down_note = POST_NOTE if session.get("show_down_reason_note") else ""

    lines += ["", "", f"### 🔺 급등 특징주 TOP 5{suffix}{up_note}", ""]

    lines += stock_table_lines(
        snapshot["gainers"],
        "상승 이유",
        session.get("show_up_reason", True),
    )

    lines += ["", "", f"### 🔻 급락 특징주 TOP 5{suffix}{down_note}", ""]

    lines += stock_table_lines(
        snapshot["losers"],
        "하락 이유",
        session.get("show_down_reason", True),
    )

    lines += [
        "",
        "",
        "### 🔥 오늘의 주요 경제 이슈",
        "",
    ]

    for i in (1, 2, 3):
        title = one_line(session.get(f"is{i}t", ""))

        if not title:
            continue

        lines.append(
            f"**{i}. {session.get(f'is{i}m', '➖')} {title}**"
        )

        content = session.get(f"is{i}c", "").strip()

        if content:
            lines.append(f"- 내용: {md_lines(content, '  ')}")

        related = one_line(session.get(f"is{i}s", ""))

        if related:
            lines.append(f"- 📌 관련 종목: {related}")

        check = session.get(f"is{i}k", "").strip()

        if check:
            lines.append(f"- ⏰ 체크: {md_lines(check, '  ')}")

        lines.append("")

    sections = [
        (
            "🏷️ 오늘 뜨는 테마",
            "themes",
            "AI 반도체 · 2차전지",
        ),
        (
            "👀 내일 체크 포인트",
            "checks",
            "급등 종목 상승 지속 여부 / 급락 종목 추가 하락 여부",
        ),
        (
            "🔍 핵심 키워드",
            "keywords",
            "#코스피 #코스닥 #급등주 #급락주",
        ),
    ]

    for title, key, default in sections:
        value = session.get(key, default).strip()

        if value:
            if key == "checks":
                items = [
                    re.sub(r"^[-•·]\s*", "", part.strip())
                    for part in re.split(r"[/\n]", value)
                ]

                body_text = "\n".join(
                    f"- {item}" for item in items if item
                )

            else:
                body_text = md_lines(value.replace("/", "\n- "))

            lines += [
                f"### {title}",
                "",
                body_text,
                "",
            ]

    return "\n".join(lines)


def md_to_html(markdown):
    """마크다운을 간단한 HTML로 변환."""

    output = []
    in_table = False

    for line in markdown.splitlines():
        if line.startswith("|"):
            cells = [
                cell.strip()
                for cell in line.split("|")[1:-1]
            ]

            if all(
                re.fullmatch(r":?-+:?", cell)
                for cell in cells
            ):
                continue

            if not in_table:
                output.append(
                    "<table border='1' cellpadding='6' "
                    "cellspacing='0' "
                    "style='border-collapse:collapse'>"
                )
                in_table = True

            rendered_cells = []

            for cell in cells:
                safe = html.escape(cell)
                safe = re.sub(
                    r"\*\*(.*?)\*\*",
                    r"<b>\1</b>",
                    safe
                )
                rendered_cells.append(f"<td>{safe}</td>")

            output.append(
                "<tr>" + "".join(rendered_cells) + "</tr>"
            )

            continue

        if in_table:
            output.append("</table>")
            in_table = False

        if line.startswith("# "):
            output.append(
                "<h1>" + html.escape(line[2:]) + "</h1>"
            )

        elif line.startswith("### "):
            output.append(
                "<h3>" + html.escape(line[4:]) + "</h3>"
            )

        elif line.strip():
            safe = html.escape(line)

            safe = re.sub(
                r"\*\*(.*?)\*\*",
                r"<b>\1</b>",
                safe
            )

            output.append("<p>" + safe + "</p>")

    if in_table:
        output.append("</table>")

    return "\n".join(output)


def copy_buttons(markdown):
    """서식 포함 복사 및 마크다운 복사 버튼."""

    payload = json.dumps(
        {
            "md": markdown,
            "html": md_to_html(markdown),
        },
        ensure_ascii=False
    ).replace("</", "<\\/")

    page = f"""
    <button id="rich">복사 (서식 포함)</button>
    <button id="plain">복사 (마크다운)</button>
    <span id="status"></span>

    <script>
    const P = {payload};
    const s = document.getElementById('status');

    function nowKST() {{
        return new Date().toLocaleTimeString('en-GB', {{
            timeZone: 'Asia/Seoul',
            hour: '2-digit',
            minute: '2-digit',
            hourCycle: 'h23'
        }});
    }}

    function stamp(text) {{
        return text.split('{NOW_TOKEN}').join(nowKST());
    }}

    document.getElementById('rich').onclick = async () => {{
        try {{
            await navigator.clipboard.write([
                new ClipboardItem({{
                    'text/html': new Blob(
                        [stamp(P.html)],
                        {{type: 'text/html'}}
                    ),
                    'text/plain': new Blob(
                        [stamp(P.md)],
                        {{type: 'text/plain'}}
                    )
                }})
            ]);

            s.textContent = ' 복사 완료';

        }} catch (e) {{
            s.textContent = ' 복사 실패';
        }}
    }};

    document.getElementById('plain').onclick = async () => {{
        try {{
            await navigator.clipboard.writeText(stamp(P.md));
            s.textContent = ' 복사 완료';

        }} catch (e) {{
            s.textContent = ' 복사 실패';
        }}
    }};
    </script>
    """

    components.html(page, height=45)


def live_input(container, label, key, auto_text):
    """
    실시간 값을 입력칸에 자동으로 넣습니다.
    사용자가 직접 고친 칸은 그대로 두고(수기 값 우선), '수기 입력 초기화'를 누르면 다시 자동값으로 돌아갑니다.
    """

    state = st.session_state
    auto_key = f"{key}__auto"

    if key not in state or state[key] == state.get(auto_key):
        state[key] = auto_text

    state[auto_key] = auto_text

    return container.text_input(label, key=key, label_visibility="collapsed")


def render_stock_section(
    title,
    stocks,
    prefix,
    icon,
    reason_label,
    toggle_key,
    empty_message,
    key_tag="all",
):
    """급등/급락 종목 섹션: 제목 옆에 '이유 열 포함' 체크박스를 둡니다."""

    head, toggle = st.columns([5, 3], vertical_alignment="center")

    head.subheader(title)

    head.markdown(
        "<span style='color:#D93025;font-weight:700;font-size:1.05rem'>"
        "⚠️ 종목은 정확하지만 현재가·등락률은 실시간과 다를 수 있어 "
        "확인 필수 (칸을 눌러 직접 수정 가능)</span>",
        unsafe_allow_html=True,
    )

    show_reason = toggle.checkbox(
        f"시황글에 {reason_label} 열 포함",
        value=True,
        key=toggle_key,
        help=f"끄면 시황글 표에서 {reason_label} 열이 빠집니다.",
    )

    toggle.checkbox(
        "시황글 제목 옆에 '확인 필수' 문구 포함",
        value=False,
        key=f"{toggle_key}_note",
        help="켜면 시황글의 급등·급락 제목 옆에 '※ 현재가·등락률 확인 필수'가 붙습니다.",
    )

    if not stocks:
        st.warning(empty_message)
        return

    widths = [2, 1.2, 1.2, 4, 2]

    labels = st.columns(widths)

    for col, text in zip(
        labels,
        [
            "종목",
            "현재가",
            "등락률",
            reason_label if show_reason else f"{reason_label} (시황글 제외)",
            "관련 테마",
        ]
    ):
        col.caption(text)

    for stock in stocks:
        a, b, c, d, e = st.columns(
            widths,
            vertical_alignment="center",
        )

        a.markdown(f"**{stock['name']}**")

        price_text = live_input(
            b,
            "현재가",
            f"{prefix}_price_{key_tag}_{stock['symbol']}",
            fmt(stock["price"], 0),
        )

        pct_input = live_input(
            c,
            "등락률",
            f"{prefix}_pct_{key_tag}_{stock['symbol']}",
            pct_text_trunc(stock["pct"]),
        )

        # 수기로 고친 값을 시황글에도 반영 (숫자로 읽히지 않으면 기존 값 유지)
        edited_price = num(
            price_text.replace("원", "").replace(",", "").strip()
        )

        if edited_price is not None:
            stock["price"] = edited_price

        edited_pct = num(
            pct_input.replace("%", "").replace("+", "").replace(",", "").strip()
        )

        if edited_pct is not None:
            stock["pct"] = edited_pct

        stock["reason"] = d.text_input(
            reason_label,
            value=stock["reason"],
            key=f"{prefix}_reason_{key_tag}_{stock['symbol']}",
            label_visibility="collapsed",
            disabled=not show_reason,
        )

        stock["theme"] = e.text_input(
            "테마",
            value=stock["theme"],
            key=f"{prefix}_theme_{key_tag}_{stock['symbol']}",
            label_visibility="collapsed",
        )


def pick_universe(changed):
    """두 체크박스 중 항상 하나만 켜지도록 맞춥니다."""

    other = "uni_all" if changed == "uni_cap100" else "uni_cap100"

    st.session_state[other] = not st.session_state[changed]


def apply_label_style():
    """입력칸 이름(라벨)과 표 머리글(캡션)을 더 크고 진하게 보이게 합니다."""

    theme = "light"

    try:
        theme = st.context.theme.type or "light"
    except Exception:
        try:
            theme = st.get_option("theme.base") or "light"
        except Exception:
            pass

    strong, soft = (
        ("#FFFFFF", "#E6E6E6") if theme == "dark" else ("#000000", "#1A1A1A")
    )

    st.markdown(
        f"""
        <style>
        [data-testid="stWidgetLabel"] p,
        [data-testid="stWidgetLabel"] span,
        [data-testid="stCheckbox"] label p {{
            font-size: 1.1rem !important;
            font-weight: 700 !important;
            color: {strong} !important;
            opacity: 1 !important;
        }}
        [data-testid="stCaptionContainer"],
        [data-testid="stCaptionContainer"] p {{
            font-size: 1rem !important;
            font-weight: 600 !important;
            color: {soft} !important;
            opacity: 1 !important;
        }}
        [data-testid="stExpander"] summary p {{
            font-size: 1.15rem !important;
            font-weight: 700 !important;
            color: {strong} !important;
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )


def main():
    st.title("MTNW 오후 시황")

    apply_label_style()

    # 시총 100위 모드를 기본값으로 시작합니다.
    st.session_state.setdefault("uni_all", False)
    st.session_state.setdefault("uni_cap100", True)

    with st.sidebar:
        st.header("설정")

        auto = st.selectbox(
            "자동 갱신 (지수·환율 등 전체)",
            [0, 60, 120, 300],
            format_func=lambda value: (
                "끔" if value == 0 else f"{value}초"
            )
        )

        live_every = st.selectbox(
            "실시간 갱신 (시총 100위 TOP5)",
            [0, 10, 30, 60, 300],
            index=1,
            format_func=lambda value: (
                "끔" if value == 0
                else f"{value}초" if value < 60
                else f"{value // 60}분"
            ),
            help="급등·급락 TOP5를 이 주기로 다시 계산합니다. "
                 "긴 글을 입력할 때 간섭이 느껴지면 '끔'으로 두세요.",
        )

        duration = st.selectbox(
            "급등·급락 순위 기준",
            ["1d", "1w", "1mo", "3mo", "6mo", "1y"],
            format_func=lambda value: {
                "1d": "당일",
                "1w": "1주",
                "1mo": "1개월",
                "3mo": "3개월",
                "6mo": "6개월",
                "1y": "1년",
            }[value]
        )

        st.caption(
            "토스증권 TOP_GAINERS/TOP_LOSERS는 "
            "realtime 기간을 지원하지 않습니다."
        )

        st.markdown("**급등·급락 종목 범위**")

        st.checkbox(
            "국내 시총 100위 이내 종목만",
            key="uni_cap100",
            on_change=pick_universe,
            args=("uni_cap100",),
            help="시가총액 순위 기준 상위 100개(우선주·스팩 제외) 안에서 "
                 "당일 등락률 TOP 5를 뽑습니다. 시총 순위는 네이버 금융(실패 시 토스 계산)으로, "
                 "현재가·등락률은 토스 실시간 체결가로 계산합니다.",
        )

        st.checkbox(
            "국내 전체 종목",
            key="uni_all",
            on_change=pick_universe,
            args=("uni_all",),
            help="토스증권 등락률 순위 기준 국내 전체 종목에서 TOP 5를 뽑습니다.",
        )

        universe = (
            "cap100"
            if st.session_state.get("uni_cap100")
            else "all"
        )

        if st.button("서버 IP 확인"):
            try:
                v4 = requests.get(
                    "https://api.ipify.org", timeout=5
                ).text
            except Exception as e:
                v4 = f"실패: {e}"

            try:
                v6 = requests.get(
                    "https://api64.ipify.org", timeout=5
                ).text
            except Exception as e:
                v6 = f"실패: {e}"

            st.write("IPv4:", v4)
            st.write("api64 (IPv6 우선):", v6)

        if st.button("로그아웃"):
            st.session_state["pw_ok"] = False
            st.rerun()

    # 시총 100위 + 실시간 갱신이 켜져 있으면 그 주기로, 아니면 기존 자동 갱신 주기로 다시 그립니다.
    refresh_every = (
        live_every
        if (universe == "cap100" and live_every)
        else auto
    )

    @st.fragment(run_every=refresh_every if refresh_every else None)
    def body():

        if st.button("수기 입력 초기화 (현재가·등락률)"):
            for key in list(st.session_state.keys()):
                if "_price_" in key or "_pct_" in key:
                    del st.session_state[key]

        stale = (
            bool(auto)
            and time.time() - st.session_state.get("fetched_ts", 0) >= auto
        )

        should_fetch = (
            st.button("지금 시세 조회", type="primary")
            or "snapshot" not in st.session_state
            or st.session_state.get("duration") != duration
            or st.session_state.get("universe") != universe
            or stale
        )

        if should_fetch:
            try:
                with st.spinner("토스증권 시세 조회 중..."):
                    st.session_state["snapshot"] = fetch_snapshot(
                        duration,
                        universe,
                    )

                    st.session_state["duration"] = duration
                    st.session_state["universe"] = universe
                    st.session_state["fetched_ts"] = time.time()
                    st.session_state.pop("api_error", None)

            except Exception as error:
                st.session_state["api_error"] = str(error)
                st.error(f"시세 조회 실패: {error}")

        snapshot = st.session_state.get("snapshot")

        if not snapshot:
            st.info(
                "토스증권 API 인증·허용 IP·랭킹 응답을 확인해 주세요."
            )
            return

        if st.session_state.get("api_error"):
            st.warning(
                f"최근 조회 오류: {st.session_state['api_error']}"
            )

        is_cap = snapshot.get("universe") == "cap100"

        # 실시간 모드: 방금 전체 조회를 했다면 이미 최신이므로 건너뜁니다.
        if is_cap and live_every and not should_fetch:
            try:
                refresh_live(snapshot)

            except Exception as error:
                st.warning(f"실시간 갱신 실패: {error}")

        if is_cap:
            render_feed_status()

        render_basis_status(snapshot)

        if is_cap and not krx_regular_open():
            st.info(
                "지금은 KRX 정규장(평일 09:00~15:30) 시간이 아닙니다. "
                "장 시작 전에는 당일 등락률이 0%라 급등·급락 목록이 비어 있을 수 있습니다."
            )

        st.caption(
            f"{snapshot['date']} · "
            f"화면 조회 {snapshot['fetched_at']} · "
            f"랭킹 기준 {snapshot['ranking_at'] or '확인 불가'} · "
            + (
                "지수·환율: 토스증권 Open API / 시총 100위 종목: 시총 순위 네이버 금융(실패 시 토스 계산) · 전일 KRX 기준가 토스 상·하한가 역산 + 정규장 중 토스 실시간 체결가"
                if is_cap
                else "토스증권 Open API"
            )
        )

        if snapshot.get("note"):
            st.warning(snapshot["note"])

        c1, c2 = st.columns(2)

        for col, key, label in (
            (c1, "kospi", "코스피"),
            (c2, "kosdaq", "코스닥"),
        ):
            data = snapshot["indices"].get(key, {})

            col.markdown(
                f"**{label}**  \n### {fmt(data.get('price'))}"
            )

            col.markdown(
                f"{arrow(data.get('pct'))} "
                f"{pct_text(data.get('pct'))}"
            )

            if data.get("pct") is None:
                col.caption(
                    "전일 종가를 확인할 수 없어 "
                    "등락률은 표시하지 않았습니다."
                )

        g_cols = st.columns(3)

        for col, (key, label, unit) in zip(g_cols, GLOBAL_ITEMS):
            data = snapshot.get("global", {}).get(key, {})
            price = data.get("price")

            col.markdown(
                f"**{label}**  \n### "
                f"{fmt(price)}{unit if price is not None else ''}"
            )

            col.markdown(
                f"{arrow(data.get('pct'))} "
                f"{pct_text(data.get('pct'))}"
            )

        render_stock_section(
            f"🔺 급등 특징주 TOP 5{CAP_SUFFIX if is_cap else ''}",
            snapshot["gainers"],
            "up",
            "🔺",
            "상승 이유",
            "show_up_reason",
            "급등 랭킹이 비어 있습니다. "
            "선택한 기간의 랭킹 데이터가 있는지 확인하세요.",
            snapshot.get("universe", "all"),
        )

        render_stock_section(
            f"🔻 급락 특징주 TOP 5{CAP_SUFFIX if is_cap else ''}",
            snapshot["losers"],
            "down",
            "🔻",
            "하락 이유",
            "show_down_reason",
            "급락 랭킹이 비어 있습니다. "
            "선택한 기간의 랭킹 데이터가 있는지 확인하세요.",
            snapshot.get("universe", "all"),
        )

        # 처음 한 번만 오늘 강했던 테마를 자동으로 채움 (이후 수기 수정 유지)
        if "themes_filled" not in st.session_state:
            st.session_state["themes_filled"] = True
            autofill_themes()

        with st.expander(
            "📝 시황글 추가 내용 편집",
            expanded=True
        ):
            st.caption(
                "이슈별로 기사 링크를 넣고 [기사 불러오기] → [핵심 정리]를 누르면 "
                "'내용' 칸이 채워집니다. 기사 내용을 직접 붙여넣어도 됩니다. "
                + (
                    "요약 방식: AI"
                    if secret("ANTHROPIC_API_KEY")
                    else "요약 방식: 핵심 문장 자동 추출 (AI 요약은 ANTHROPIC_API_KEY 설정 시)"
                )
            )

            # 첫 화면에서만 자동 생성합니다. 이후에는 사용자의 수정 내용을 유지합니다.
            if "summary" not in st.session_state:
                st.session_state["summary"] = generate_market_summary(snapshot)

            summary_btn_col, summary_note_col = st.columns([1.7, 5], vertical_alignment="center")
            summary_btn_col.button(
                "✨ 한 줄 요약 자동 생성 / 다시 생성",
                key="summary_generate_btn",
                on_click=autofill_summary,
                use_container_width=True,
            )
            if st.session_state.get("summary_note"):
                summary_note_col.caption(st.session_state["summary_note"])

            st.text_area(
                "한 줄 요약",
                key="summary",
                height=90,
                help=(
                    "기사가 없어도 현재 산업·종목·지수 데이터로 생성됩니다. "
                    "기사 내용을 입력한 뒤 다시 생성하면 입력 기사까지 반영합니다. "
                    "필요하면 직접 수정할 수 있습니다."
                ),
            )

            with st.expander("📰 토스증권 주요 뉴스 불러오기", expanded=False):
                st.caption(
                    "토스증권 공개 대시보드 뉴스입니다. 개인화 추천 피드나 커뮤니티 댓글은 불러오지 않습니다. "
                    "뉴스 제목/링크를 이슈 입력칸으로 옮긴 뒤 기사 본문을 확인할 수 있습니다."
                )
                news_type_labels = {
                    "주요 뉴스": "ALL_HIGHLIGHT",
                    "최신 뉴스": "HOT",
                    "급등주 뉴스": "SOARING_STOCK",
                }
                news_type_label = st.selectbox(
                    "뉴스 분류",
                    list(news_type_labels.keys()),
                    key="toss_news_type_label",
                )
                news_btn_col, news_note_col = st.columns([1.6, 5], vertical_alignment="center")
                news_btn_col.button(
                    "🔄 토스 뉴스 불러오기",
                    key="toss_news_fetch_btn",
                    on_click=lambda: st.session_state.__setitem__(
                        "toss_dashboard_news",
                        fetch_toss_dashboard_news(news_type_labels[st.session_state.get("toss_news_type_label", "최신 뉴스")]),
                    ),
                )
                toss_news_items = st.session_state.get("toss_dashboard_news", []) or []
                if toss_news_items:
                    news_note_col.caption(f"{len(toss_news_items)}건 표시 · 조회 결과는 최대 3분간 캐시됩니다.")
                    for news_idx, news_item in enumerate(toss_news_items):
                        news_title = news_item.get("title", "")
                        metadata = " · ".join(
                            part for part in [news_item.get("source", ""), news_item.get("published", "")]
                            if part
                        )
                        st.markdown(f"**{news_idx + 1}. {news_title}**")
                        if metadata:
                            st.caption(metadata)
                        if news_item.get("summary"):
                            st.write(news_item["summary"][:280])
                        if news_item.get("url"):
                            st.markdown(f"[기사 링크 열기]({news_item['url']})")
                        pick_cols = st.columns([1, 1, 1, 4])
                        for issue_num, pick_col in zip((1, 2, 3), pick_cols[:3]):
                            pick_col.button(
                                f"이슈 {issue_num}에 적용",
                                key=f"toss_news_{news_idx}_issue_{issue_num}",
                                on_click=use_toss_news_for_issue,
                                args=(issue_num, news_item),
                            )
                        st.divider()
                else:
                    st.caption("아직 조회된 뉴스가 없습니다. ‘토스 뉴스 불러오기’를 눌러 주세요.")

            issues = [
                (1, "🔺", "당일 상승률 상위 종목 흐름"),
                (2, "🔻", "당일 하락률 상위 종목 흐름"),
                (3, "➖", "시장 변동성 확인"),
            ]

            for i, direction, title in issues:
                st.session_state.setdefault(f"is{i}m", direction)
                st.session_state.setdefault(f"is{i}t", title)

                st.markdown(f"**이슈 {i}**")

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

            sector_items_for_ui = st.session_state.get("sector_items", []) or []
            if sector_items_for_ui:
                with st.expander("🏭 토스증권 산업별 주요 종목", expanded=False):
                    st.caption(
                        "토스증권 공개 산업 데이터 기준 · 대표 종목은 시가총액 내림차순 상위 최대 5개입니다. "
                        "데이터는 조회 시점의 스냅샷이며, 실시간 체결 오버레이와 다를 수 있습니다."
                    )
                    for sector_item in sector_items_for_ui[:5]:
                        stock_rows = sector_item.get("stocks", []) or []
                        stock_labels = []
                        for stock in stock_rows[:5]:
                            label = stock.get("name", "")
                            pct = stock.get("pct")
                            if pct is not None:
                                label += f" ({pct:+.2f}%)"
                            stock_labels.append(label)
                        if not stock_labels and sector_item.get("leading_stock"):
                            stock_labels = [sector_item["leading_stock"]]
                        if stock_labels:
                            st.markdown(
                                f"**{sector_item['name']} ({sector_item.get('pct', 0):+.2f}%)**  \n"
                                + " · ".join(stock_labels)
                            )
                        else:
                            st.markdown(
                                f"**{sector_item['name']} ({sector_item.get('pct', 0):+.2f}%)**  \n"
                                "주요 종목 데이터는 현재 확인되지 않았습니다."
                            )
            else:
                st.caption(
                    "산업별 주요 종목은 ‘강한 테마 자동 불러오기’를 실행하면 함께 조회됩니다."
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
            st.code(markdown, language="markdown")

    body()


main()
