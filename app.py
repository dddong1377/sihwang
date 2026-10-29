import datetime
import hmac
import html
import json
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

st.set_page_config(page_title="MTNW 오후 시황", layout="wide")

KST = ZoneInfo("Asia/Seoul")
BASE = "https://openapi.tossinvest.com"
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
    상승·하락 종목 순위 조회.

    주의:
    /api/v1/rankings 및 전달 파라미터는
    토스 API의 실제 명세와 일치하는지 확인해야 합니다.
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


def parse_naver_market_sum(page_html, market):
    """
    네이버 금융 시가총액 페이지(HTML) 한 장을 종목 목록으로 변환.
    열 위치는 표 머리글(현재가·등락률·시가총액)로 찾으므로 열 순서가 바뀌어도 동작합니다.
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

    needed = max(i_price, i_pct, i_cap)

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

        rows.append({
            "symbol": link.group(1),
            "name": html.unescape(link.group(2)).strip(),
            "price": price,
            "pct": pct,
            "cap": cap,
            "market": market,
        })

    return rows


@st.cache_data(ttl=30, show_spinner=False)
def fetch_cap_rows_naver():
    """[1순위] 코스피 상위 150개 + 코스닥 상위 100개를 네이버 금융 시가총액 기준으로 수집 (30초 캐시)."""

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
    등락률은 get_cap_rankings의 refresh_with_toss에서 다시 계산합니다."""

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
            })

    return rows


def fetch_cap_rows():
    """네이버 우선, 실패하면 토스 계산 방식으로 전환. (rows, 출처)를 돌려줍니다."""

    try:
        rows = fetch_cap_rows_naver()
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
    """종목별 전일 종가 (하루 단위로 캐시). day_text가 바뀌면 새로 조회합니다."""

    def one(symbol):
        try:
            result = api_get(
                "/api/v1/candles",
                {"symbol": symbol, "interval": "1d", "count": 3}
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


def refresh_with_toss(stocks):
    """
    시총 100위 전체를 토스 현재가 + 전일 종가로 등락률 재계산합니다.
    전일 종가는 하루 단위로 캐시하므로 평소에는 /prices 1회만 호출됩니다.
    실패한 종목은 기존(네이버) 값을 그대로 둡니다.
    """

    if not stocks:
        return stocks

    symbols = tuple(stock["symbol"] for stock in stocks)

    try:
        last = fetch_toss_prices(symbols)

        prev = fetch_prev_closes(
            symbols,
            datetime.datetime.now(KST).date().isoformat()
        )

    except Exception:
        return stocks

    for stock in stocks:
        price = last.get(stock["symbol"])
        base = prev.get(stock["symbol"])

        if price and base:
            stock["price"] = price
            stock["pct"] = (price / base - 1) * 100

    return stocks


def get_cap_rankings(mode="up"):
    """시가총액 상위 100위(우선주·스팩 등 제외) 전체를 토스 기준으로 재계산한 뒤,
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

    ranked_at = datetime.datetime.now(KST).strftime("%Y-%m-%d %H:%M")

    source_label = "네이버" if source == "naver" else "토스 계산"

    stocks = []

    for cap_rank, row in enumerate(universe, start=1):
        stocks.append({
            "name": row["name"],
            "symbol": row["symbol"],
            "price": row["price"],
            "pct": row["pct"],
            "base_price": None,
            "rank": cap_rank,
            "theme": classify_theme(row["name"]),
            "reason": (
                f"시가총액 상위 100위 내 당일 등락률 기준 ({source_label}, 토스 시세 재계산)"
            ),
            "timestamp": ranked_at,
        })

    # 100개 전체를 토스 기준으로 재계산 → 이후부터는 시총 순이 아닌 등락률 순
    stocks = refresh_with_toss(stocks)

    if mode == "up":
        picked = [stock for stock in stocks if stock["pct"] > 0]
    else:
        picked = [stock for stock in stocks if stock["pct"] < 0]

    picked.sort(
        key=lambda stock: stock["pct"],
        reverse=(mode == "up")
    )

    return picked[:5], ranked_at


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
                f"표시합니다. ({error})"
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
        headlines = fetch_news(
            stock["name"],
            now.date()
        )

        if headlines:
            stock["reason"] = headlines[0]["title"]

            stock["theme"] = classify_theme(
                stock["name"],
                [headline["title"] for headline in headlines]
            )

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


@st.cache_data(ttl=60, show_spinner=False)
def fetch_strong_themes(limit=5):
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

    if not themes:
        state["themes_note"] = (
            "상승한 섹터를 찾지 못했습니다(장 시작 전이거나 데이터 없음). 직접 입력해 주세요."
        )
        return

    state["themes"] = " ".join(theme_hashtag(theme["name"]) for theme in themes)

    state["themes_note"] = (
        "토스 섹터 랭킹 기준(조회 시점까지, 거래대금 상위 중 상승률 순) · "
        + " · ".join(f"{t['name']} {t['pct']:+.2f}%" for t in themes)
        + f" · 갱신 {datetime.datetime.now(KST).strftime('%H:%M:%S')}"
    )


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
            f"**{pct_text(stock['pct'])}**",
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

    intro_text = md_lines(
        session.get(
            "intro_text",
            "종목 순위는 토스증권 Open API의 기간별 등락률 기준입니다. "
            "실시간 순위와 다를 수 있으므로 기준 시각을 확인해 주세요."
        )
    )

    lines = [
        f"# 📅 {snapshot['date']} | 오후 시황",
        "",
        f"📌 **한 줄 요약**: {summary_text}",
        "",
        intro_text,
        "",
        "### 🇰🇷 장중 지수 현황",
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
            "🏷️ 오늘 강했던 테마",
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
        (
            "⚠️ 한 줄 주의",
            "caution",
            "등락률 순위는 선택한 기간 기준이며 실시간 순위와 다를 수 있습니다.",
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

        price_text = b.text_input(
            "현재가",
            value=fmt(stock["price"], 0),
            key=f"{prefix}_price_{key_tag}_{stock['symbol']}",
            label_visibility="collapsed",
        )

        pct_input = c.text_input(
            "등락률",
            value=pct_text(stock["pct"]),
            key=f"{prefix}_pct_{key_tag}_{stock['symbol']}",
            label_visibility="collapsed",
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
            "자동 갱신",
            [0, 60, 120, 300],
            format_func=lambda value: (
                "끔" if value == 0 else f"{value}초"
            )
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
                 "당일 등락률 TOP 5를 뽑습니다. 네이버 금융을 우선 사용하고, "
                 "실패하면 토스 API로 시총을 직접 계산합니다.",
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

    @st.fragment(run_every=auto if auto else None)
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

        st.caption(
            f"{snapshot['date']} · "
            f"화면 조회 {snapshot['fetched_at']} · "
            f"랭킹 기준 {snapshot['ranking_at'] or '확인 불가'} · "
            + (
                "지수·환율: 토스증권 Open API / 시총 100위 종목: 네이버 금융 우선(실패 시 토스 계산)"
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

            st.text_area(
                "한 줄 요약",
                key="summary",
                value="당일 등락률 상위 종목과 시장 흐름을 확인하세요.",
                height=68,
            )

            st.text_area(
                "인트로 문구",
                key="intro_text",
                height=110,
                value=(
                    "종목 순위는 토스증권 Open API의 기간별 등락률 기준입니다. "
                    "실시간 순위와 다를 수 있으므로 기준 시각을 확인해 주세요."
                )
            )

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
                "🔄 강했던 테마 자동 불러오기",
                key="themes_btn",
                on_click=autofill_themes,
            )

            if st.session_state.get("themes_note"):
                t2.caption(st.session_state["themes_note"])

            st.text_area(
                "강했던 테마 (#태그를 띄어쓰기로 구분)",
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

            st.text_area(
                "한 줄 주의",
                key="caution",
                height=68,
                value=(
                    "등락률 순위는 선택한 기간 기준이며 "
                    "실시간 순위와 다를 수 있습니다."
                ),
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
