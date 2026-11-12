import datetime
import hmac
import html
import io
import json
import math
import os
import re
import tempfile
import time
from zoneinfo import ZoneInfo

import requests
import streamlit as st
import streamlit.components.v1 as components

from usage_store import load_usage, add_usage


st.set_page_config(page_title="MTNW 리포트 압축", layout="wide")

KST = ZoneInfo("Asia/Seoul")


# ===============================================================
# 1. 설정
# ===============================================================

GROQ_MODEL = "openai/gpt-oss-120b"
GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_TIMEOUT = 180

# 한 요청(입력 + 최대 출력)의 안전 예산 (TPM 8,000 기준)
GROQ_TPM_SAFE_BUDGET = 7500
GROQ_REASONING_EFFORT = "low"
CHARS_PER_TOKEN_ESTIMATE = 1.65

# 장전·오후·주간 페이지와 같은 파일을 써서 하루 사용량을 함께 집계합니다.
GROQ_DAILY_TOKEN_LIMIT = 200_000

# 리포트 한 건에서 읽을 글자 수(앞부분부터). 화면 슬라이더로 바꿀 수 있습니다.
DEFAULT_CHAR_LIMIT = 24_000
MIN_CHAR_LIMIT = 6_000
MAX_CHAR_LIMIT = 60_000

# 긴 리포트를 나눠 읽을 때 한 번에 AI에 보내는 글자 수
CHUNK_CHARS = 6_000

# 정리 상세도: 포인트 수 / 핵심 글자 수 / 수치 줄 수 / 리스크 줄 수 / 출력 토큰 / 메모 합계 글자 수
DETAIL_LEVELS = {
    "간결": {"points": "3~4", "core": 80, "nums": 4, "risks": 1, "tokens": 1_500, "memo_cap": 8_000, "max_points": 4},
    "보통": {"points": "4~6", "core": 120, "nums": 5, "risks": 2, "tokens": 2_000, "memo_cap": 7_000, "max_points": 6},
    "상세": {"points": "5~7", "core": 160, "nums": 6, "risks": 3, "tokens": 2_400, "memo_cap": 6_500, "max_points": 7},
}
DEFAULT_DETAIL = "상세"

STYLE_GUIDE = (
    "애널리스트 리포트를 압축한 글처럼 쓴다. 수치를 나열하지 말고 '무엇이 → 왜 → 그래서 어떻게 전망하는가'의 "
    "논리가 이어지는 문장으로 쓰고, 수치는 그 문장의 근거로 곁들인다. "
    "포인트의 소제목은 핵심 주장을 담은 짧은 구절(15자 안팎)로 쓴다."
)

DEPTH_GUIDE = {
    "간결": "각 포인트는 한 문장으로 쓴다.",
    "보통": "각 포인트는 두 문장 안팎으로, 이유와 근거 수치를 함께 쓴다.",
    "상세": (
        "각 포인트는 두세 문장으로, 이유·근거 수치·전망이 이어지게 쓴다. "
        "포인트는 가능하면 ① 실적·사업 현황 ② 전망과 추정의 근거 ③ 밸류에이션·목표주가 산출 근거 "
        "④ 투자 포인트·촉매 순서로 구성한다 (리포트 성격에 맞게 조정)."
    ),
}


def detail_params():
    return DETAIL_LEVELS.get(st.session_state.get("rd_detail"), DETAIL_LEVELS[DEFAULT_DETAIL])


MAP_MAX_TOKENS = 1_100
REDUCE_MAX_TOKENS = 1_500
COMPARE_MAX_TOKENS = 1_200
REWORD_MAX_TOKENS = 1_200

# 원문과 이 글자 수(공백 제외) 이상 똑같이 이어지면 '그대로 옮겨 적음'으로 봅니다.
VERBATIM_MIN_CHARS = 30

# 정리글에 투자의견·목표주가를 넣을지 (증권사의 의견으로만 표시합니다)
INCLUDE_RATING = True

MAX_FILES = 8

# 법적·윤리적 위험이 있는 표현: 포함된 한 줄 요약은 코드가 버립니다.
BANNED_WORDS = (
    "무조건", "반드시", "100%", "수익 보장", "수익보장",
    "원금 보장", "확정 수익", "지금 사라", "당장 사라",
    "지금 팔라", "당장 팔라", "사야", "팔아야", "담아야",
    "대박", "폭망", "패닉", "떡상 확정", "급등 확정",
    "100배", "인생역전", "폭등", "대폭락",
)

# 정리글 전체에서 찾아 경고할 권유성 표현
SOLICITATION_PATTERN = re.compile(
    r"(매수하세요|매수하십시오|매수하라|사세요|사라\b|팔아라|파세요|매도하세요|"
    r"매도하라|담으세요|담아라|지금이 기회|놓치지 마|무조건|확실한 수익|반드시 (?:오른|상승))"
)

KNOWN_BROKERS = (
    "미래에셋증권", "삼성증권", "KB증권", "NH투자증권", "한국투자증권",
    "신한투자증권", "하나증권", "키움증권", "대신증권", "메리츠증권",
    "유안타증권", "IBK투자증권", "교보증권", "한화투자증권", "SK증권",
    "DB금융투자", "DB증권", "유진투자증권", "현대차증권", "상상인증권",
    "BNK투자증권", "다올투자증권", "iM증권", "LS증권", "케이프투자증권",
    "부국증권", "리딩투자증권", "토스증권", "카카오페이증권",
)

DISCLAIMER_LINES = (
    "※ 이 글은 각 증권사가 발간한 리포트의 내용을 AI가 보조하여 요약·정리한 참고 자료입니다. "
    "요약 과정에서 오류나 누락이 있을 수 있으며, 정확한 내용·전제·위험 고지는 반드시 원문을 확인하시기 바랍니다.",
    "※ 투자의견·목표주가·전망은 해당 증권사의 견해이며 당사의 의견이나 투자 권유가 아닙니다.",
    "※ 리포트 원문의 저작권은 각 증권사에 있습니다. 투자 판단과 그에 따른 책임은 투자자 본인에게 있습니다.",
)

# ===============================================================
# 2. 공통: Secrets / 로그인 / 토큰 사용량
# ===============================================================

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

        st.session_state["pw_ok"] = hmac.compare_digest(
            entered,
            expected.encode(),
        )

        st.session_state["pw"] = ""

    st.title("MTNW 시황")

    st.text_input(
        "비밀번호",
        type="password",
        key="pw",
        on_change=verify,
    )

    if st.session_state.get("pw_ok") is False:
        st.error("비밀번호가 올바르지 않습니다.")

    return False


if not login():
    st.stop()


def estimate_tokens(text):
    if not text:
        return 0

    return math.ceil(len(text) / CHARS_PER_TOKEN_ESTIMATE)


def show_token_status(box):
    try:
        usage = load_usage()
        used = int(usage.get("tokens", 0))
        remaining = max(0, GROQ_DAILY_TOKEN_LIMIT - used)

        with box.container():
            with st.container(border=True):
                col1, col2, col3 = st.columns(3)

                col1.metric("오늘 남은 토큰 (추정)", f"{remaining:,}")
                col2.metric(
                    "오늘 사용 토큰",
                    f"{used:,} / {GROQ_DAILY_TOKEN_LIMIT:,}",
                )
                col3.metric("오늘 API 호출", f"{int(usage.get('calls', 0))}회")

                st.progress(min(1.0, used / GROQ_DAILY_TOKEN_LIMIT))

                st.caption(
                    "장전 시황 페이지와 함께 집계한 추정치입니다. "
                    "정확한 잔량은 Groq 콘솔의 limits 페이지에서 확인하세요."
                )
    except Exception:
        pass


# ===============================================================
# 3. Groq 호출
# ===============================================================

def parse_reset_seconds(value):
    if not value:
        return None

    value = str(value).strip().lower().strip(". ")

    try:
        if value.endswith("ms"):
            return float(value[:-2]) / 1000

        minutes = re.search(r"(\d+(?:\.\d+)?)m(?!s)", value)
        seconds = re.search(r"(\d+(?:\.\d+)?)s", value)

        if minutes or seconds:
            total = 0

            if minutes:
                total += float(minutes.group(1)) * 60

            if seconds:
                total += float(seconds.group(1))

            return total

        return float(value)

    except (TypeError, ValueError):
        return None


def sleep_with_countdown(seconds, label):
    seconds = int(max(0, math.ceil(seconds)))

    if seconds <= 0:
        return

    box = st.empty()

    for remaining in range(seconds, 0, -1):
        box.caption(f"⏳ {label} · {remaining}초 대기")
        time.sleep(1)

    box.empty()


def wait_for_tpm(next_tokens):
    """직전 호출의 남은 분당 토큰이 부족하면 초기화될 때까지 기다립니다."""

    last = st.session_state.get("wk_last_rate")

    if not last:
        return

    try:
        remaining = int(last.get("remaining"))
    except (TypeError, ValueError):
        remaining = None

    if remaining is not None and remaining >= next_tokens:
        return

    reset = parse_reset_seconds(last.get("reset"))

    if reset is None:
        reset = 60

    elapsed = time.time() - last.get("at", time.time())

    if elapsed >= reset:
        return

    sleep_with_countdown(
        min(max(2, math.ceil(reset - elapsed) + 3), 90),
        "Groq 토큰 제한 초기화 대기",
    )


def groq_api_key():
    key = ""

    try:
        key = st.secrets.get("GROQ_API_KEY", "")
    except Exception:
        pass

    if not key:
        key = os.environ.get("GROQ_API_KEY", "")

    return str(key).strip()


def groq_chat(prompt, max_tokens, temperature, stage):
    api_key = groq_api_key()

    if not api_key:
        raise RuntimeError(
            "Groq API 키가 설정되지 않았습니다. "
            "Streamlit Secrets에 GROQ_API_KEY를 등록해 주세요."
        )

    input_estimate = estimate_tokens(prompt)
    safe_max = min(max_tokens, GROQ_TPM_SAFE_BUDGET - input_estimate)

    if safe_max < 500:
        raise RuntimeError(
            f"{stage}: 입력이 너무 깁니다(추정 {input_estimate:,}토큰)."
        )

    wait_for_tpm(input_estimate + safe_max)

    payload = {
        "model": GROQ_MODEL,
        "messages": [
            {
                "role": "system",
                "content": (
                    "당신은 한국 주식시장 전문 시황 에디터입니다. "
                    "제공된 자료에 없는 사실이나 수치를 만들어내지 마세요."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": temperature,
        "max_tokens": safe_max,
    }

    if GROQ_REASONING_EFFORT:
        payload["reasoning_effort"] = GROQ_REASONING_EFFORT
        payload["include_reasoning"] = False

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    response = None

    for attempt in range(3):
        try:
            response = requests.post(
                GROQ_API_URL,
                headers=headers,
                json=payload,
                timeout=GROQ_TIMEOUT,
            )
        except requests.exceptions.RequestException as error:
            raise RuntimeError(
                f"{stage}: Groq 네트워크 요청 실패: {str(error)[:200]}"
            )

        st.session_state["wk_last_rate"] = {
            "remaining": response.headers.get("x-ratelimit-remaining-tokens"),
            "reset": response.headers.get("x-ratelimit-reset-tokens"),
            "at": time.time(),
        }

        if response.status_code == 200:
            break

        body = response.text[:1500]
        lowered = body.lower()

        # 추론 옵션을 지원하지 않으면 빼고 재시도
        if (
            response.status_code == 400
            and "reasoning_effort" in payload
            and "reasoning" in lowered
        ):
            payload.pop("reasoning_effort", None)
            payload.pop("include_reasoning", None)
            continue

        # 분당 토큰 제한: 안내된 시간만큼 기다린 뒤 재시도
        if (
            response.status_code == 429
            and attempt < 2
            and ("tokens per minute" in lowered or "tpm" in lowered)
        ):
            wait = None

            retry_after = response.headers.get("retry-after")

            if retry_after:
                try:
                    wait = float(retry_after)
                except ValueError:
                    wait = None

            if wait is None:
                found = re.search(r"try again in\s+([0-9hms.]+)", body, re.I)

                if found:
                    wait = parse_reset_seconds(found.group(1))

            sleep_with_countdown(
                min((wait if wait is not None else 20) + 2, 90),
                f"{stage} 토큰 제한 대기",
            )
            continue

        hint = (
            " (일일 사용 한도일 수 있습니다)"
            if response.status_code == 429
            and ("per day" in lowered or "daily" in lowered)
            else ""
        )

        raise RuntimeError(
            f"{stage}: Groq HTTP {response.status_code}{hint}: {body[:300]}"
        )

    if response is None or response.status_code != 200:
        raise RuntimeError(f"{stage}: Groq 응답을 받지 못했습니다.")

    data = response.json()
    usage = data.get("usage") or {}

    # 잘린 응답이라도 처리된 토큰은 하루 한도에서 차감되므로 기록
    add_usage(usage.get("total_tokens"))

    choices = data.get("choices") or []

    if not choices:
        raise RuntimeError(f"{stage}: Groq 응답에 choices가 없습니다.")

    choice = choices[0]

    if choice.get("finish_reason") == "length":
        raise RuntimeError(
            f"{stage}: 출력 한도에 도달해 응답이 잘렸습니다. 다시 시도해 주세요."
        )

    result = (choice.get("message") or {}).get("content")

    if isinstance(result, list):
        result = "\n".join(
            str(item.get("text", ""))
            for item in result
            if isinstance(item, dict)
        )

    result = str(result or "").strip()

    if not result:
        raise RuntimeError(f"{stage}: Groq 응답이 비어 있습니다.")

    return result



# ===============================================================
# 4. PDF 읽기 / 정리
# ===============================================================

DISCLOSURE_MARKERS = (
    "Compliance Notice", "컴플라이언스", "Disclosure", "투자등급 공시",
    "투자의견 비율", "주가 및 목표주가 변동추이", "본 조사분석자료는",
    "조사분석자료는 당사", "고지사항",
)

SHORT_BROKER_NAMES = {
    "삼성": "삼성증권", "미래에셋": "미래에셋증권", "키움": "키움증권",
    "대신": "대신증권", "메리츠": "메리츠증권", "유안타": "유안타증권",
    "현대차": "현대차증권", "교보": "교보증권", "한화": "한화투자증권",
    "신한": "신한투자증권", "한투": "한국투자증권", "NH": "NH투자증권",
    "KB": "KB증권", "하나": "하나증권",
}


@st.cache_data(show_spinner=False)
def read_pdf_pages(data):
    """PDF에서 [(쪽번호, 텍스트)]를 읽습니다."""

    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))

    if reader.is_encrypted:
        try:
            reader.decrypt("")
        except Exception:
            raise RuntimeError("암호가 걸린 PDF입니다. 암호를 풀어서 올려 주세요.")

    pages = []

    for number, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text() or ""
        except Exception:
            text = ""

        pages.append((number, text))

    # 거의 읽히지 않으면 pdfplumber로 한 번 더 시도(설치돼 있을 때만)
    if sum(len(text.strip()) for _, text in pages) < 100:
        try:
            import pdfplumber

            with pdfplumber.open(io.BytesIO(data)) as pdf:
                pages = [
                    (number, page.extract_text() or "")
                    for number, page in enumerate(pdf.pages, start=1)
                ]
        except Exception:
            pass

    return pages


def clean_pages(pages):
    """쪽마다 반복되는 머리글·바닥글을 지우고 공백을 정리합니다."""

    counts = {}

    for _, text in pages:
        for line in {item.strip() for item in text.splitlines() if item.strip()}:
            counts[line] = counts.get(line, 0) + 1

    repeated = set()

    if len(pages) >= 4:
        repeated = {
            line for line, count in counts.items()
            if count >= max(3, int(len(pages) * 0.5)) and len(line) <= 80
        }

    cleaned = []

    for number, text in pages:
        lines = [
            item.strip() for item in text.splitlines()
            if item.strip() and item.strip() not in repeated
        ]

        body = re.sub(r"[ \t]+", " ", "\n".join(lines)).strip()

        cleaned.append((number, body))

    return cleaned


def drop_disclosure(pages):
    """증권사 고지·공시 쪽(분석 내용이 아닌 쪽)은 요약 대상에서 뺍니다."""

    kept = [
        (number, text) for number, text in pages
        if text and sum(marker in text for marker in DISCLOSURE_MARKERS) < 2
    ]

    return kept or pages


def guess_broker(first_text, filename):
    haystack = filename + "\n" + first_text[:3000]

    for name in KNOWN_BROKERS:
        if name in haystack:
            return name

    for key, name in SHORT_BROKER_NAMES.items():
        if key in filename:
            return name

    return ""


def guess_date(first_text):
    found = re.search(
        r"(20\d{2})\s*[.\-/년]\s*(\d{1,2})\s*[.\-/월]\s*(\d{1,2})",
        first_text[:1500],
    )

    if not found:
        return ""

    year, month, day = (int(value) for value in found.groups())

    if not (1 <= month <= 12 and 1 <= day <= 31):
        return ""

    return f"{year:04d}-{month:02d}-{day:02d}"


def guess_company(first_text):
    """첫 쪽에서 '종목명(6자리 코드)' 꼴을 찾아 종목을 추정합니다. 못 찾으면 빈 값입니다."""

    head = first_text[:2500]

    found = re.search(r"([가-힣A-Za-z&][가-힣A-Za-z0-9&\.\- ]{1,20}?)\s*[\(（]\s*(\d{6})\s*(?:[,.]?\s*(?:KS|KQ|KOSPI|KOSDAQ))?\s*[\)）]", head)

    if found:
        name = found.group(1).strip()
        name = re.sub(r"^(?:\d+\s*)?(?:기업분석|Company|Analysis|BUY|Buy|HOLD|Hold)\s*", "", name).strip()

        if name:
            return f"{name}({found.group(2)})"

    return ""


def code_from_text(name, pages):
    """리포트 본문에서 '종목명(6자리 코드)' 표기를 찾아 코드를 돌려줍니다."""

    pattern = re.compile(re.escape(name) + r"\s*[\(（]\s*(\d{6})")

    for _, text in pages:
        found = pattern.search(text)

        if found:
            return found.group(1)

    return ""


@st.cache_data(ttl=86_400, show_spinner=False)
def lookup_code_online(name):
    """본문에서 못 찾았을 때만 네이버 자동완성에서 종목코드를 찾습니다. 이름이 정확히 같을 때만 씁니다."""

    try:
        response = requests.get(
            "https://ac.stock.naver.com/ac",
            params={"q": name, "target": "stock"},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=5,
        )

        if response.status_code != 200:
            return ""

        stack, matches = [response.json()], []

        while stack:
            node = stack.pop()

            if isinstance(node, dict):
                code, title = str(node.get("code", "")), str(node.get("name", ""))

                if re.fullmatch(r"\d{6}", code) and title.strip() == name:
                    matches.append(code)

                stack.extend(node.values())

            elif isinstance(node, list):
                stack.extend(node)

        return matches[0] if len(set(matches)) == 1 else ""

    except Exception:
        return ""


def resolve_company(raw, pages):
    """'삼성전자' → '삼성전자(005930)'. 이미 코드가 있으면 모양만 '이름(코드)'로 맞춥니다."""

    raw = re.sub(r"\s+", " ", str(raw or "")).strip()

    found = re.match(r"^(.*?)\s*[\(（]\s*(\d{6})\s*[\)）]$", raw)

    if found and found.group(1).strip():
        return f"{found.group(1).strip()}({found.group(2)})"

    if not raw or re.search(r"\d{6}", raw):
        return raw

    # 산업·시황 리포트처럼 종목이 아닌 입력은 그대로 둡니다. 종목명은 보통 공백이 없거나 짧습니다.
    code = code_from_text(raw, pages) or (lookup_code_online(raw) if len(raw) <= 15 else "")

    return f"{raw}({code})" if code else raw


def guess_title(filename):
    stem = re.sub(r"\.pdf$", "", filename, flags=re.I)

    return re.sub(r"[_]+", " ", stem).strip()


def build_chunks(pages, char_limit):
    """앞부분부터 char_limit 글자까지를 CHUNK_CHARS 단위 묶음으로 나눕니다."""

    chunks, current, size, used = [], [], 0, 0

    for number, text in pages:
        if used >= char_limit:
            break

        text = text[:max(0, char_limit - used)]
        used += len(text)

        for start in range(0, len(text), CHUNK_CHARS):
            part = text[start:start + CHUNK_CHARS]

            if size + len(part) > CHUNK_CHARS and current:
                chunks.append(current)
                current, size = [], 0

            current.append((number, part))
            size += len(part)

    if current:
        chunks.append(current)

    return chunks, used


def chunk_text(chunk):
    return "\n\n".join(f"[p.{number}]\n{text}" for number, text in chunk)


def estimate_run_tokens(entries, char_limit):
    """예상 토큰(입력+출력 대략치). 실제와 다를 수 있습니다."""

    total = 0

    for entry in entries:
        chunks, _ = build_chunks(entry["pages"], char_limit)

        if not chunks:
            continue

        for chunk in chunks:
            chars = sum(len(text) for _, text in chunk)
            total += math.ceil(chars / CHARS_PER_TOKEN_ESTIMATE) + 700

            if len(chunks) > 1:
                total += 800

        total += 700 + detail_params()["tokens"]

        if st.session_state.get("rd_check", True):
            total += 3_500 * math.ceil((detail_params()["max_points"] + 3) / CHECK_BATCH)

    names = [re.sub(r"[\(（].*?[\)）]|\s", "", entry["company"]).lower() for entry in entries]
    total += 2_500 * len({name for name in names if names.count(name) >= 2})

    return total


# ===============================================================
# 5. AI 프롬프트
# ===============================================================

COMMON_RULES = """# 반드시 지킬 규칙
1. 원문에 있는 사실·수치·전망만 쓴다. 추측하거나 새 수치를 만들지 않는다.
2. 원문 문장을 그대로 옮기지 않는다. 의미를 이해한 뒤 짧은 한국어 문장으로 다시 쓴다. (수치·고유명사·종목명은 그대로 둔다.)
3. 수치에는 단위와 기준 시점을 붙인다. 원문에 없으면 쓰지 않는다.
4. 투자 권유로 읽히는 표현(사라, 팔아라, 지금이 기회 등)과 명령형·단정형 표현을 쓰지 않는다.
5. 의견·전망은 반드시 '증권사가 ~라고 봤다/제시했다'처럼 증권사의 견해로 표현한다.
6. 증권사 고지·면책·공시 문구는 무시한다.
7. 금액은 원문 표기 단위 그대로 쓴다. 억원↔조원 등으로 환산·반올림하지 않는다. 표의 단위(십억원, 백만원 등)를 확인해 그대로 옮긴다.
8. 확정 실적과 추정·전망 수치를 구분한다. 추정치는 '전망·추정·예상'을 밝히고, 연도 옆에 E·F 표기가 있으면 추정으로 본다.
9. 투자의견·목표주가는 이 종목에 대한 것만 쓴다. 업종 투자의견과 섞지 않는다. 목표주가는 하나만 쓰고, 이전 목표주가를 언급할 때는 '이전'이라고 밝힌다.
10. 원문에 가정·조건이 붙은 전망은 조건을 함께 쓴다. 원문이 밝힌 주요 위험은 빠뜨리지 않는다.
11. 원문에 없는 해석·평가(괜찮은, 우려된다, 필요하다 등)를 덧붙이지 않는다. 원문의 강도보다 세게 쓰지 않는다(예: 원문이 '기대'면 '확실'이라 쓰지 않는다).
12. 영문 약어는 원문에 풀이가 있을 때만 괄호로 풀어 쓴다. 없으면 약어만 쓰고 뜻을 지어내지 않는다.
13. 글자 수('(160자)' 등)를 문장에 쓰지 않는다."""


MAP_PROMPT = """# 역할
증권사 리포트의 일부를 읽고, 나중에 글로 압축할 수 있도록 '논리 메모'를 만든다.

__RULES__

# 출력 형식
- 한 줄에 메모 하나, 최대 12줄
- 각 줄은 '주장·전망 — 근거 — 수치' 형태로, 리포트가 무엇을 왜 그렇게 보는지 드러나게 쓴다
- 표의 숫자만 나열하지 말고 변화의 원인이나 의미를 함께 적는다
- 연도별 핵심 추정치(매출·영업이익·EPS 등)는 한 줄로 묶는다
- 투자의견·목표주가·산출 근거(적용 PER 등)는 반드시 포함한다
- 줄 끝에 쪽 번호를 (p.N) 형식으로 붙인다. 다른 설명은 쓰지 않는다

# 리포트 정보
__INFO__

# 원문 일부 (p.__FIRST__~__LAST__)
__MATERIAL__
"""


REDUCE_PROMPT = """# 역할
증권사 리포트 한 건을 게시판용으로 압축하는 정리 에디터다.

__RULES__

# 입력 자료 종류: __KIND__
__INFO__

# 출력 형식 (아래 형식의 줄만 출력하고 다른 설명은 쓰지 않는다)
의견: 이 종목의 투자의견 || 목표주가 금액 || 목표기간·기준주가·산출 근거 (예: 12개월, 기준주가 28,000원, PER 10배) (원문에 없으면 각각 -)
핵심: 리포트의 결론과 논리를 이어 쓴 1~2문장 (__CORE__자 이내, 증권사 견해로 표현)
포인트: 소제목 || 설명 (p.N)   ← 구분 기호는 반드시 '||' 하나만 쓴다
포인트: ... (__POINTS__줄, 리스크는 포인트에 쓰지 말고 아래 리스크 줄에만 쓴다)
수치: 항목=값 (p.N)  (가장 중요한 지표만, 0~__NUMS__줄)
리스크: 원문이 언급한 위험 요인 (p.N) (최대 __RISKS__줄, 없으면 -)

# 작성 방침
__STYLE__
__DEPTH__

# 자료
__MATERIAL__
"""


COMPARE_PROMPT = """# 역할
같은 종목에 대해 나온 여러 증권사 리포트의 요약을 비교하는 에디터다.

__RULES__
14. 어느 증권사의 견해인지 증권사 이름을 반드시 붙인다. 종목이 서로 다르면 종목명을 함께 적고, 다른 종목끼리의 의견을 같은 종목의 의견처럼 섞지 않는다.
15. 자료에 없는 내용은 쓰지 않는다. 비교할 근거가 없으면 해당 줄은 -로 쓴다.

# 출력 형식 (아래 형식의 줄만 출력)
한줄: 전체를 아우르는 사실 중심 한 줄 요약 (25~40자, 투자 권유 금지)
공통: 증권사들이 비슷하게 본 점 (최대 3줄, 각 줄에 증권사 이름 포함)
차이: 증권사 간 시각이 갈리는 점 (최대 3줄, 각 줄에 증권사 이름 포함)

# 증권사별 요약
__MATERIAL__
"""


REWORD_PROMPT = """아래 번호 문장을 원문과 같은 표현이 이어지지 않도록 다른 말로 고쳐 쓴다.
- 의미와 수치·고유명사·쪽 번호 표시(p.N)는 그대로 둔다.
- 새 내용을 더하지 않는다.
- '번호. 문장' 형식으로만 출력한다.

__MATERIAL__
"""


def with_rules(prompt):
    return prompt.replace("__RULES__", COMMON_RULES)


# ===============================================================
# 6. 요약 / 검사
# ===============================================================

PAGE_REF_PATTERN = re.compile(r"\s*[\(\[]\s*(?:p|P)\.?\s*\d+(?:\s*[,~\-–]\s*(?:p\.?\s*)?\d+)*\s*[\)\]]")
CHAR_COUNT_PATTERN = re.compile(r"\s*[\(\[]\s*(?:약\s*)?\d+\s*자\s*(?:이내|내외)?\s*[\)\]]")


def clean_text(text):
    """AI가 붙인 글자 수 표기 (160자)를 지웁니다. 쪽 표기는 화면용 글에 남깁니다."""

    text = CHAR_COUNT_PATTERN.sub("", str(text or ""))

    return re.sub(r"\s+", " ", text).strip()


def parse_summary(text):
    result = {
        "opinion": "", "target": "", "basis": "",
        "core": "", "points": [], "numbers": [], "risk": "",
    }

    for raw in str(text or "").splitlines():
        line = raw.strip().lstrip("-*•· ").replace("**", "")
        found = re.match(r"^(의견|핵심|포인트|수치|리스크)\s*[:：]\s*(.*)$", line)

        if not found:
            continue

        key, value = found.group(1), found.group(2).strip()

        if key == "의견":
            parts = [part.strip() for part in value.replace("&&", "||").split("||")] + ["", "", ""]
            parts = ["" if part in ("-", "없음") else part for part in parts]
            parts[1] = re.sub(r"^목표주가\s*[=:：]?\s*", "", parts[1]).strip("-: ")
            parts[0] = re.sub(r"^(?:투자의견)\s*[=:：]?\s*", "", parts[0]).strip("-: ")
            parts[2] = re.sub(r"\s*\(p\.\d+(?:~\d+)?\)", "", parts[2]).strip("-: ")

            result["opinion"], result["target"], result["basis"] = parts[:3]

        elif key == "핵심":
            result["core"] = value

        elif key == "포인트" and value not in ("", "-"):
            value = value.replace("&&", "||")

            if "||" in value:
                head, _, body = value.partition("||")
                head = head.strip().strip("*")

                if head.startswith("리스크") and body.strip():
                    note = re.sub(r"\s+", " ", body).strip()
                    result["risk"] = f"{result['risk']} / {note}" if result["risk"] else note
                    continue

                if head and body.strip():
                    value = f"**{head}**: {body.strip()}"

                else:
                    value = (head or body).strip()

            result["points"].append(value)

        elif key == "수치" and value not in ("", "-"):
            result["numbers"].append(value)

        elif key == "리스크" and value not in ("", "-", "없음"):
            result["risk"] = f"{result['risk']} / {value}" if result["risk"] else value

    level = detail_params()

    result["core"] = clean_text(result["core"])
    result["risk"] = clean_text(result["risk"])
    result["points"] = [clean_text(item) for item in result["points"]]
    result["numbers"] = [clean_text(item) for item in result["numbers"]]

    result["points"] = result["points"][:level["max_points"]]
    result["numbers"] = result["numbers"][:level["nums"]]

    return result


def compact(text):
    return re.sub(r"\s+", "", str(text or ""))


def make_shingles(source):
    """원문을 공백 없이 이어 붙인 뒤 VERBATIM_MIN_CHARS 길이 조각 집합으로 만듭니다."""

    text = compact(source)
    size = VERBATIM_MIN_CHARS

    return {text[i:i + size] for i in range(0, max(0, len(text) - size + 1))}


def has_verbatim(line, shingles):
    text = compact(line)
    size = VERBATIM_MIN_CHARS

    return any(
        text[i:i + size] in shingles
        for i in range(0, max(0, len(text) - size + 1))
    )


def strip_commas(text):
    return re.sub(r"(?<=\d),(?=\d)", "", str(text or ""))


def unknown_numbers(text, source_plain):
    """text 안의 숫자(2자리 이상) 중 원문에 없는 것을 돌려줍니다. 쪽 번호(p.N)는 제외합니다."""

    text = re.sub(r"\(?p\.\d+(?:~\d+)?\)?", " ", strip_commas(text))

    missing = []

    for token in re.findall(r"\d+(?:\.\d+)?", text):
        if len(token.replace(".", "")) >= 2 and token not in source_plain:
            missing.append(token)

    return missing


def verify_summary(entry, summary, source):
    """AI가 쓴 수치가 원문에 있는지 확인합니다. 없는 목표주가·수치는 빼고 알림을 남깁니다."""

    plain = strip_commas(source)
    label = entry["broker"]
    notes = []

    name = re.sub(r"[\(（].*?[\)）]|\s", "", entry.get("company", ""))

    if name and name not in re.sub(r"\s", "", source):
        notes.append(f"{label}: 입력한 종목 '{entry['company']}'이 읽은 본문에 나오지 않습니다. 종목이 맞는지 확인해 주세요.")

    if summary["target"] and unknown_numbers(summary["target"], plain):
        notes.append(f"{label}: 목표주가 '{summary['target']}'를 원문에서 확인하지 못해 뺐습니다.")
        summary["target"] = ""

    if summary["opinion"] and summary["opinion"].lower() not in source.lower():
        notes.append(
            f"{label}: 투자의견 '{summary['opinion']}'이 원문에 같은 표기로 없습니다. 원문과 대조해 주세요."
        )

    kept = []

    for item in summary["numbers"]:
        if unknown_numbers(item, plain):
            notes.append(f"{label}: 원문에서 확인되지 않는 수치를 뺐습니다: {item[:50]}")
        else:
            kept.append(item)

    summary["numbers"] = kept

    for text in summary["points"] + [summary["core"], summary["risk"]]:
        if text and unknown_numbers(text, plain):
            notes.append(f"{label}: 원문에서 확인되지 않는 수치가 있는 문장 → {text[:60]}…")

    return [f"[확인 필요] {note}" for note in notes]


TOP_NOTICE = (
    "※ 투자 참고용 요약입니다. 증권사의 견해이며 투자 권유가 아닙니다. "
    "정확한 내용은 원문을 확인하시고, 투자 판단과 책임은 투자자 본인에게 있습니다."
)

CHECK_BATCH = 5
CHECK_MAX_TOKENS = 1_400

CHECK_PROMPT = """# 역할
투자정보 콘텐츠의 독립적인 품질 검증 담당자다. 아래 '요약'이 '발췌'(증권사 리포트 원문 일부)와 맞는지 대조한다.
요약이 정확하다고 가정하지 않는다. 그러나 존재하지 않는 문제를 만들어내지 않는다. 맞으면 반드시 '정상'으로 답한다.

# 확인 항목
금액의 원문 단위와 변환 / 수치와 적용 연도 / 목표주가·투자의견·목표기간 / 종목 의견과 업종 의견의 구분 /
확정 실적과 추정치의 구분 / 조건부 전망의 조건 누락 / 원문에 없는 표현·추론 / 의미 왜곡·지나친 단순화 /
숫자는 맞지만 해석이 틀린 부분 / 일반 독자가 오해할 문장

# 판정
- 정상: 발췌와 맞다
- 수정: 발췌와 명백히 다르다 (수정문을 반드시 쓴다. 수정문의 숫자는 발췌에 있는 것만 쓴다)
- 확인: 발췌만으로 판단할 수 없거나 자료가 충돌한다 (발췌에 근거가 없다는 이유만으로 '수정'을 쓰지 않는다)
- 가독: 내용은 맞지만 오해 소지가 있거나 더 쉽게 쓸 수 있다

# 출력 형식 (요약 번호마다 한 줄, 다른 설명은 쓰지 않는다)
번호 || 판정 || 사유 한 문장 || 수정문 (판정이 '수정'일 때만, 아니면 -)

__MATERIAL__
"""


def source_windows(entry):
    """원문을 240자 조각(120자씩 겹침)으로 나눠 (쪽, 조각) 목록으로 만듭니다."""

    if "windows" not in entry:
        windows = []

        for page, text in entry["pages"]:
            flat = re.sub(r"\s+", " ", text)

            for start in range(0, max(1, len(flat)), 120):
                piece = flat[start:start + 240]

                if piece.strip():
                    windows.append((page, piece))

        entry["windows"] = windows

    return entry["windows"]


def find_evidence(entry, claim, limit=3):
    """문장과 숫자·단어가 가장 많이 겹치는 원문 조각을 찾습니다."""

    plain = strip_commas(claim)
    numbers = {t for t in re.findall(r"\d+(?:\.\d+)?", plain) if len(t.replace(".", "")) >= 2}
    words = set(re.findall(r"[가-힣A-Za-z]{2,}", plain))

    windows = source_windows(entry)
    scored = []

    for index, (_, piece) in enumerate(windows):
        flat = strip_commas(piece)
        score = 3 * sum(1 for n in numbers if n in flat) + sum(1 for w in words if w in piece)

        if score:
            scored.append((score, index))

    scored.sort(reverse=True)

    picked = []

    for _, index in scored:
        if all(abs(index - other) > 1 for other in picked):
            picked.append(index)

        if len(picked) >= limit:
            break

    return [windows[i] for i in sorted(picked)]


def code_checks(entry, summary, source):
    """AI 없이 코드로 하는 검사: 목표주가 일치, 원문 표 단위, 확정/추정 표현."""

    label = entry["broker"]
    plain = strip_commas(source)
    texts = [summary["core"]] + summary["points"] + summary["numbers"] + [summary["risk"]]
    notes = []

    target = re.sub(r"\D", "", summary["target"] or "")

    if target:
        for text in texts:
            for found in re.finditer(r"목표(?:주가|가)[^\d]{0,8}(\d[\d,]{3,8})\s*원", text or ""):
                before = text[max(0, found.start() - 12):found.start()]

                if re.sub(r"\D", "", found.group(1)) != target and not re.search(r"이전|기존|종전|직전|상향 전|하향 전", before):
                    notes.append(
                        f"[확인 필요] {label}: 목표주가가 {found.group(1)}원으로도 적혀 있어 "
                        f"목표주가 칸({summary['target']})과 다릅니다 → {text[:60]}…"
                    )

    declared = set(re.findall(r"단위\s*[:：]?\s*\(?\s*(십억원|백만원|억원|조원|천원|만원)", source))
    nospace = re.sub(r"\s", "", plain)
    seen = set()

    if declared:
        for text in texts:
            for found in re.finditer(r"(\d+(?:\.\d+)?)\s*(조|억|만)\s*원?", strip_commas(text or "")):
                token = found.group(1) + found.group(2)

                if len(found.group(1)) >= 3 and token not in seen and token not in nospace and found.group(1) in nospace:
                    seen.add(token)
                    notes.append(
                        f"[확인 필요] {label}: '{found.group(0).strip()}'은 원문 표의 단위({', '.join(sorted(declared))})와 "
                        "달라 환산이 맞는지 확인이 필요합니다."
                    )

    this_year = datetime.datetime.now(KST).year

    for text in [summary["core"]] + summary["points"]:
        for sentence in re.split(r"(?<=[다요\.])\s+", text or ""):
            if re.search(r"전망|예상|추정|목표|예정|가정|기대|것으로", sentence):
                continue

            found = re.search(r"(20\d{2})년\s*(?:([1-4])\s*분기)?", sentence)

            if not found or not re.search(r"기록|달성|확정|실현", sentence):
                continue

            year, quarter = int(found.group(1)), int(found.group(2) or 0)

            if year > this_year or (year == this_year and quarter in (0, 4)):
                notes.append(
                    f"[확인 필요] {label}: 아직 확정되지 않은 시점의 수치를 확정 실적처럼 쓴 것은 아닌지 확인하세요 → {sentence[:60]}…"
                )

    return notes


def build_claims(summary):
    """AI 대조를 할 문장 목록 [(종류, 번호, 문장, 검색 보조어)]."""

    claims = []

    rating = " / ".join(
        part for part in (
            f"투자의견 {summary['opinion']}" if summary["opinion"] else "",
            f"목표주가 {summary['target']}" if summary["target"] else "",
            f"기준 {summary['basis']}" if summary["basis"] else "",
        ) if part
    )

    if rating:
        claims.append(("rating", 0, rating, "투자의견 목표주가 목표기간"))

    if summary["core"]:
        claims.append(("core", 0, summary["core"], ""))

    for index, text in enumerate(summary["points"]):
        claims.append(("points", index, text, ""))

    if summary["risk"]:
        claims.append(("risk", 0, summary["risk"], "리스크 위험"))

    return claims


def cross_check(entry, summary, source, status):
    """요약 문장을 원문 발췌와 AI로 다시 대조합니다. 안전할 때만 자동 수정하고, 나머지는 알립니다."""

    label = entry["broker"]
    claims = build_claims(summary)
    plain = strip_commas(source)
    shingles = make_shingles(source)
    notes, checked = [], 0

    for start in range(0, len(claims), CHECK_BATCH):
        batch = claims[start:start + CHECK_BATCH]
        status.write(f"  · {label} 원문 대조 검증 ({start + 1}~{start + len(batch)}/{len(claims)})")

        blocks, pages = [], {}

        for number, (_, _, text, hint) in enumerate(batch, start=1):
            evidence = find_evidence(entry, f"{text} {hint}")
            pages[number] = "·".join(f"p.{p}" for p in sorted({p for p, _ in evidence}))
            excerpt = " | ".join(f"(p.{p}) {piece}" for p, piece in evidence) or "(관련 발췌를 찾지 못함)"
            blocks.append(f"[{number}] 요약: {text}\n발췌: {excerpt}")

        prompt = CHECK_PROMPT.replace("__MATERIAL__", "\n\n".join(blocks))

        try:
            raw = groq_chat(prompt, CHECK_MAX_TOKENS, 0.1, f"{label} 검증")

        except Exception as error:
            notes.append(f"[확인 필요] {label}: 문장 {len(batch)}개는 AI 대조에 실패해 검증되지 않았습니다 ({str(error)[:60]})")
            continue

        answered = set()

        for line in raw.splitlines():
            found = re.match(r"^\W*(\d+)\W*\|\|\s*(정상|수정|확인|가독)\s*\|\|\s*(.*?)\s*(?:\|\|\s*(.*))?$", line.strip())

            if not found:
                continue

            number, verdict = int(found.group(1)), found.group(2)
            reason, fix = found.group(3).strip(), (found.group(4) or "").strip()

            if not 1 <= number <= len(batch):
                continue

            answered.add(number)

            kind, index, text, _ = batch[number - 1]
            where = f" (원문 {pages[number]})" if pages[number] else ""
            short = text[:40] + ("…" if len(text) > 40 else "")

            if verdict == "정상":
                continue

            if verdict == "가독":
                notes.append(f"[가독성] {label}: {reason} → '{short}'")

            elif verdict == "확인" or not fix or fix == "-":
                notes.append(f"[확인 필요] {label}: {reason}{where} → '{short}'")

            else:
                safe = (
                    kind in ("core", "points")
                    and not unknown_numbers(fix, plain)
                    and not has_verbatim(fix, shingles)
                )

                if safe:
                    if kind == "points" and text.startswith("**") and not fix.startswith("**"):
                        head = re.match(r"^\*\*.+?\*\*", text).group(0)
                        fix = f"{head}: {fix}"

                    apply_item(summary, kind, index, fix)
                    notes.append(f"[자동 수정] {label}: {reason}{where} — 수정 전: {text[:70]} → 수정 후: {fix[:70]}")

                else:
                    notes.append(f"[수정 필수] {label}: {reason}{where} → '{short}' / 제안: {fix[:90]}")

        checked += len(answered)

        if len(answered) < len(batch):
            notes.append(f"[확인 필요] {label}: 문장 {len(batch) - len(answered)}개는 AI 응답을 해석하지 못해 검증되지 않았습니다.")

    entry["checked"] = (checked, len(claims))

    return notes


def flagged_items(summary, shingles):
    """원문과 그대로 겹치는 문장 위치 [(필드, 번호, 문장)]."""

    items = []

    if summary["core"] and has_verbatim(summary["core"], shingles):
        items.append(("core", 0, summary["core"]))

    for index, text in enumerate(summary["points"]):
        if has_verbatim(text, shingles):
            items.append(("points", index, text))

    if summary["risk"] and has_verbatim(summary["risk"], shingles):
        items.append(("risk", 0, summary["risk"]))

    return items


def apply_item(summary, field, index, text):
    if field == "points":
        summary["points"][index] = text
    else:
        summary[field] = text


def reword_flagged(summary, shingles, label, status):
    """원문과 겹친 문장을 AI로 한 번 고쳐 쓰고, 그래도 남는 문장을 돌려줍니다."""

    items = flagged_items(summary, shingles)

    if not items:
        return []

    status.write(f"  · {label}: 원문과 비슷한 문장 {len(items)}개를 다시 쓰는 중")

    material = "\n".join(
        f"{number}. {text}" for number, (_, _, text) in enumerate(items, start=1)
    )

    try:
        raw = groq_chat(
            REWORD_PROMPT.replace("__MATERIAL__", material),
            REWORD_MAX_TOKENS,
            0.4,
            f"{label} 문장 다듬기",
        )

        for line in raw.splitlines():
            found = re.match(r"^\s*(\d+)[.)]\s*(.+)$", line)

            if not found:
                continue

            number = int(found.group(1))

            if 1 <= number <= len(items):
                field, index, _ = items[number - 1]
                apply_item(summary, field, index, found.group(2).strip())

    except Exception as error:
        status.write(f"  ⚠️ 문장 다듬기 실패: {str(error)[:120]}")

    return flagged_items(summary, shingles)


def join_memos(memos, cap):
    """메모를 합치되, 뒤쪽 구간이 통째로 잘리지 않도록 구간별로 같은 분량씩 남깁니다."""

    if sum(len(memo) for memo in memos) + len(memos) <= cap:
        return "\n".join(memos)

    each = cap // max(1, len(memos))
    kept = []

    for memo in memos:
        lines, size = [], 0

        for line in memo.splitlines():
            if size + len(line) + 1 > each:
                break

            lines.append(line)
            size += len(line) + 1

        kept.append("\n".join(lines))

    return "\n".join(kept)


def summarize_report(entry, char_limit, status):
    label = entry["broker"]
    detail_name = st.session_state.get("rd_detail")
    detail_name = detail_name if detail_name in DETAIL_LEVELS else DEFAULT_DETAIL
    level = DETAIL_LEVELS[detail_name]
    chunks, used = build_chunks(entry["pages"], char_limit)

    if not chunks:
        raise RuntimeError("읽을 수 있는 본문이 없습니다(스캔본 PDF일 수 있습니다).")

    first_page, last_page = chunks[0][0][0], chunks[-1][-1][0]
    entry["range"] = f"p.{first_page}" if first_page == last_page else f"p.{first_page}~{last_page}"
    entry["used_chars"] = used

    info = (
        f"증권사: {label} / 대상 종목: {entry['company']} / 제목: {entry['title'] or '-'}\n"
        "이 리포트의 대상은 위 '대상 종목'이다. 다른 종목·업종 비교 내용은 대상 종목과 구분해 쓰고, "
        "핵심·포인트는 모두 대상 종목 기준으로 쓴다."
    )

    if len(chunks) == 1:
        material, kind = chunk_text(chunks[0]), "원문"

    else:
        memos = []

        for number, chunk in enumerate(chunks, start=1):
            status.write(f"  · {label} 읽는 중 ({number}/{len(chunks)})")

            prompt = (
                with_rules(MAP_PROMPT)
                .replace("__INFO__", info)
                .replace("__FIRST__", str(chunk[0][0]))
                .replace("__LAST__", str(chunk[-1][0]))
                .replace("__MATERIAL__", chunk_text(chunk))
            )

            memos.append(
                groq_chat(prompt, MAP_MAX_TOKENS, 0.2, f"{label} 메모 {number}").strip()
            )

        material, kind = join_memos(memos, level["memo_cap"]), "사실 메모"

    status.write(f"  · {label} 압축 정리 중")

    prompt = (
        with_rules(REDUCE_PROMPT)
        .replace("__KIND__", kind)
        .replace("__INFO__", info)
        .replace("__CORE__", str(level["core"]))
        .replace("__POINTS__", level["points"])
        .replace("__NUMS__", str(level["nums"]))
        .replace("__RISKS__", str(level["risks"]))
        .replace("__STYLE__", STYLE_GUIDE)
        .replace("__DEPTH__", DEPTH_GUIDE[detail_name])
        .replace("__MATERIAL__", material)
    )

    raw = groq_chat(prompt, level["tokens"], 0.2, f"{label} 압축")
    summary = parse_summary(raw)

    if not (summary["core"] or summary["points"]):
        raise RuntimeError("AI 응답 형식을 해석하지 못했습니다.")

    entry["raw"] = raw

    source = "\n".join(text for _, text in entry["pages"])

    shingles = make_shingles(source)

    entry["verbatim_left"] = reword_flagged(summary, shingles, label, status)
    entry["notes"] = verify_summary(entry, summary, source)
    entry["notes"] += code_checks(entry, summary, source)

    if st.session_state.get("rd_check", True):
        entry["notes"] += cross_check(entry, summary, source, status)

    return summary


def compare_summaries(entries, summaries, status):
    """증권사가 2곳 이상일 때 공통점·차이점과 한 줄 요약을 만듭니다."""

    blocks = []

    for entry, summary in zip(entries, summaries):
        parts = [f"[{entry['broker']}] 대상 종목: {entry['company']}"]

        if INCLUDE_RATING and (summary["opinion"] or summary["target"]):
            parts.append(
                f"의견: {summary['opinion'] or '-'} / 목표주가: {summary['target'] or '-'}"
            )

        parts.append(f"핵심: {summary['core'] or '-'}")
        parts.append("포인트: " + " ; ".join(summary["points"]))

        blocks.append("\n".join(parts))

    prompt = with_rules(COMPARE_PROMPT).replace("__MATERIAL__", "\n\n".join(blocks))

    status.write("  · 증권사 간 비교 정리 중")

    raw = groq_chat(prompt, COMPARE_MAX_TOKENS, 0.4, "증권사 비교")

    headline, common, diff = "", [], []

    for line in raw.splitlines():
        text = line.strip().lstrip("-*•· ").replace("**", "")
        found = re.match(r"^(한줄|공통|차이)\s*[:：]\s*(.*)$", text)

        if not found:
            continue

        key, value = found.group(1), found.group(2).strip()

        if value in ("", "-"):
            continue

        if key == "한줄":
            if len(value) >= 5 and not any(word in value for word in BANNED_WORDS):
                headline = value
        elif key == "공통":
            common.append(value)
        else:
            diff.append(value)

    return headline, common[:3], diff[:3]


# ===============================================================
# 7. 결과 조립
# ===============================================================

def clean_cell(text):
    return str(text or "").replace("|", "/").replace("\n", " ").strip() or "-"


def broker_block(number, entry, summary):
    title = entry["title"] or "제목 미입력"
    date = f" ({entry['date']})" if entry["date"] else ""

    lines = [f"**{number}. {entry['broker']} | {entry['company']} | {title}**{date}"]

    if summary["core"]:
        lines.append(f"- 핵심: {summary['core']}")

    if INCLUDE_RATING:
        opinion, target = summary["opinion"], summary["target"]

        if opinion or target:
            text = f"{entry['broker']} 의견 {opinion or '미제시'} / 목표주가 {target or '미제시'}"

            if summary["basis"]:
                text += f" ({summary['basis']})"

            lines.append(f"- 투자의견·목표주가: {text} ※ 증권사의 견해이며 당사 의견이 아닙니다")
        else:
            lines.append("- 투자의견·목표주가: 원문에서 확인되지 않음")

    if summary["points"]:
        lines.append("- 주요 내용")

        for point in summary["points"]:
            lines.append(f"  - {point}")

    if summary["numbers"]:
        lines.append("- 주요 수치(원문 기준): " + " · ".join(re.sub(r"\s*\(p\.\d+(?:~\d+)?\)", "", item) for item in summary["numbers"]))

    if summary["risk"]:
        lines.append(f"- 리스크: {summary['risk']}")

    lines.append("")

    return lines


def build_report(entries, summaries, comparisons, failures):
    now = datetime.datetime.now(KST)

    lines = [
        f"# 📑 증권사 리포트 압축 정리 | {now.year}년 {now.month}월 {now.day}일",
        "",
    ]

    targets = " · ".join(
        f"{entry['broker']} – {entry['company']}" for entry in entries
    )

    lines += [f"📌 **이번 정리 대상**: {targets}", "", TOP_NOTICE, ""]

    lines += [
        f"※ 아래 {len(entries)}개 증권사 리포트를 요약했습니다. 각 내용은 해당 증권사의 견해입니다.",
        "",
        "### 🏦 증권사별 요약",
        "",
    ]

    for number, (entry, summary) in enumerate(zip(entries, summaries), start=1):
        lines += broker_block(number, entry, summary)

    for company, common, diff in comparisons:
        lines += [f"### 🔎 {company} 증권사 간 비교", ""]

        for item in common:
            lines.append(f"- 공통: {item}")

        for item in diff:
            lines.append(f"- 차이: {item}")

        lines.append("")

    lines += [
        "### 📚 출처",
        "",
        "| 증권사 | 종목 | 리포트 제목 | 작성자 | 발간일 | 참고 쪽 | 원문 링크 |",
        "|---|---|---|---|---|---|---|",
    ]

    for entry in entries:
        lines.append(
            f"| {clean_cell(entry['broker'])} | {clean_cell(entry['company'])} "
            f"| {clean_cell(entry['title'])} "
            f"| {clean_cell(entry['author'])} | {clean_cell(entry['date'])} "
            f"| {clean_cell(entry.get('range'))} | {clean_cell(entry['link'])} |"
        )

    lines.append("")

    if failures:
        lines += [
            f"※ 다음 리포트는 정리하지 못해 제외했습니다: {', '.join(failures)}",
            "",
        ]

    lines += list(DISCLAIMER_LINES)

    return "\n".join(lines)


def run_digest(entries, char_limit, status):
    state = st.session_state

    state["rd_ok"] = False

    done_entries, summaries, failures, warnings = [], [], [], []

    for entry in entries:
        status.write(f"📄 {entry['broker']} 리포트를 정리하는 중...")

        try:
            summary = summarize_report(entry, char_limit, status)

        except Exception as error:
            failures.append(entry["broker"])
            warnings.append(f"{entry['broker']}: 정리 실패 - {str(error)[:200]}")
            status.write(f"⚠️ {entry['broker']} 실패: {str(error)[:150]}")
            continue

        done_entries.append(entry)
        summaries.append(summary)

        warnings.extend(entry.get("notes", []))

        for _, _, text in entry.get("verbatim_left", []):
            warnings.append(
                f"{entry['broker']}: 원문과 {VERBATIM_MIN_CHARS}자 이상 같은 문장이 남아 있습니다 "
                f"→ 직접 고쳐 주세요: {text[:60]}…"
            )

    if not done_entries:
        raise RuntimeError("정리된 리포트가 없습니다. 아래 경고를 확인해 주세요: " + " / ".join(warnings))

    # 같은 종목을 다룬 리포트가 2건 이상일 때만 비교합니다. 종목이 다르면 비교하지 않습니다.
    groups = {}

    for entry, summary in zip(done_entries, summaries):
        key = re.sub(r"[\(（].*?[\)）]|\s", "", entry["company"]).lower()
        groups.setdefault(key, []).append((entry, summary))

    comparisons = []

    for members in groups.values():
        if len(members) < 2:
            continue

        try:
            _, common, diff = compare_summaries(
                [entry for entry, _ in members], [summary for _, summary in members], status
            )

            if common or diff:
                comparisons.append((members[0][0]["company"], common, diff))

        except Exception as error:
            warnings.append(f"증권사 비교 실패: {str(error)[:150]}")

    report = build_report(done_entries, summaries, comparisons, failures)

    for match in SOLICITATION_PATTERN.finditer(report):
        warnings.append(f"권유성 표현 의심: '{match.group(0)}' — 게시 전에 확인해 주세요.")

    state["rd_report"] = report
    state["rd_warnings"] = warnings

    done_checks = [entry.get("checked") for entry in done_entries if entry.get("checked")]

    if state.get("rd_check", True):
        total_done = sum(c[0] for c in done_checks)
        total_all = sum(c[1] for c in done_checks)
        scope = (
            f"검증 범위: 코드 검사(수치 원문 존재·목표주가 일치·단위·확정/추정 표현) + "
            f"AI 원문 대조 {total_done}/{total_all}문장. 대조하지 못한 문장은 '확인 필요'로 표시됩니다."
        )
    else:
        scope = "검증 범위: 코드 검사(수치 원문 존재·목표주가 일치·단위·확정/추정 표현)만 했습니다. AI 원문 대조는 하지 않았습니다."

    state["rd_scope"] = scope
    state["rd_note"] = (
        f"생성 완료 · {datetime.datetime.now(KST).strftime('%H:%M:%S')} · "
        f"{len(done_entries)}건 정리" + (f" · {len(failures)}건 실패" if failures else "")
    )
    state["rd_note_level"] = "warn" if (failures or warnings) else "ok"

# ===============================================================
# 10. 마크다운 → HTML / 복사 버튼
# ===============================================================

def md_to_html(markdown):
    """마크다운을 간단한 HTML로 변환(표 머리글 회색, 열 너비는 글자 길이에 비례)."""

    import unicodedata

    output = []
    table_rows = []
    para = []

    def inline(text):
        safe = html.escape(text)
        return re.sub(r"\*\*(.*?)\*\*", r"<b>\1</b>", safe)

    def visual_width(text):
        text = text.replace("*", "")
        return sum(
            2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
            for ch in text
        )

    def flush_para():
        if para:
            output.append("<p style='margin:0 0 10px'>" + "<br>".join(para) + "</p>")
            para.clear()

    def flush_table():
        if not table_rows:
            return

        columns = max(len(row) for row in table_rows)

        weights = []

        for index in range(columns):
            longest = max(
                visual_width(row[index]) if index < len(row) else 0
                for row in table_rows
            )
            weights.append(max(longest, 6) + 3)

        total = sum(weights)
        widths = [round(weight * 100 / total) for weight in weights]
        widths[-1] = 100 - sum(widths[:-1])

        output.append(
            "<table border='1' cellpadding='4' cellspacing='0' "
            "style='border-collapse:collapse;margin:0 0 10px;width:100%'>"
        )

        for row_index, row in enumerate(table_rows):
            cells_html = []

            for col_index in range(columns):
                cell = row[col_index] if col_index < len(row) else ""
                safe = inline(cell)

                numeric = bool(
                    re.fullmatch(r"[\s\d,.+\-%원🔺🔻➖▲▼]+", cell.replace("*", ""))
                )

                wrap_style = (
                    "white-space:nowrap" if numeric
                    else "word-break:break-all;overflow-wrap:anywhere"
                )

                if row_index == 0:
                    cells_html.append(
                        f"<th width='{widths[col_index]}%' "
                        f"style='width:{widths[col_index]}%;"
                        f"background-color:#F2F3F5;font-weight:normal;"
                        f"text-align:left;{wrap_style}'>{safe}</th>"
                    )
                else:
                    cells_html.append(f"<td style='{wrap_style}'>{safe}</td>")

            output.append("<tr>" + "".join(cells_html) + "</tr>")

        output.append("</table>")
        table_rows.clear()

    for line in markdown.splitlines():
        if line.startswith("|"):
            flush_para()

            cells = [cell.strip() for cell in line.split("|")[1:-1]]

            if all(re.fullmatch(r":?-+:?", cell) for cell in cells):
                continue

            table_rows.append(cells)
            continue

        flush_table()

        if line.startswith("# "):
            flush_para()
            output.append(
                "<h1 style='margin:0 0 12px'>" + html.escape(line[2:]) + "</h1>"
            )

        elif line.startswith("### "):
            flush_para()
            output.append(
                "<h3 style='margin:18px 0 6px'>" + html.escape(line[4:]) + "</h3>"
            )

        elif line.strip():
            indent = len(line) - len(line.lstrip(" "))
            para.append("&nbsp;" * indent + inline(line.strip()))

        else:
            flush_para()

    flush_para()
    flush_table()

    return "\n".join(output)


def copy_buttons(markdown):
    payload = json.dumps(
        {"md": markdown, "html": md_to_html(markdown)},
        ensure_ascii=False,
    ).replace("</", "<\\/")

    page = f"""
    <button id="rich">복사 (서식 포함)</button>
    <button id="plain">복사 (마크다운)</button>
    <span id="status"></span>

    <script>
    const P = {payload};
    const s = document.getElementById('status');

    document.getElementById('rich').onclick = async () => {{
        try {{
            await navigator.clipboard.write([
                new ClipboardItem({{
                    'text/html': new Blob([P.html], {{type: 'text/html'}}),
                    'text/plain': new Blob([P.md], {{type: 'text/plain'}})
                }})
            ]);
            s.textContent = ' 복사 완료';
        }} catch (e) {{
            s.textContent = ' 복사 실패';
        }}
    }};

    document.getElementById('plain').onclick = async () => {{
        try {{
            await navigator.clipboard.writeText(P.md);
            s.textContent = ' 복사 완료';
        }} catch (e) {{
            s.textContent = ' 복사 실패';
        }}
    }};
    </script>
    """

    components.html(page, height=45)




# ===============================================================
# 10-2. PDF 만들기
# ===============================================================

FONT_CANDIDATES = (
    ("/usr/share/fonts/truetype/nanum/NanumGothic.ttf", "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf", 0),
    ("fonts/NanumGothic.ttf", "fonts/NanumGothicBold.ttf", 0),
    ("/usr/share/fonts/truetype/nanum/NanumBarunGothic.ttf", "/usr/share/fonts/truetype/nanum/NanumBarunGothicBold.ttf", 0),
    ("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc", None, 0),
)


def register_korean_font():
    """한글 글꼴을 등록하고 (보통체, 굵은체 이름)을 돌려줍니다. 없으면 PDF 내장 한글 글꼴로 대체합니다."""

    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    for regular, bold, index in FONT_CANDIDATES:
        if not os.path.exists(regular):
            continue

        try:
            pdfmetrics.registerFont(TTFont("KR", regular, subfontIndex=index))

            if bold and os.path.exists(bold):
                pdfmetrics.registerFont(TTFont("KR-Bold", bold, subfontIndex=index))
                bold_name = "KR-Bold"
            else:
                bold_name = "KR"

            pdfmetrics.registerFontFamily("KR", normal="KR", bold=bold_name, italic="KR", boldItalic=bold_name)

            return "KR", bold_name, True

        except Exception:
            continue

    from reportlab.pdfbase.cidfonts import UnicodeCIDFont

    pdfmetrics.registerFont(UnicodeCIDFont("HYGothic-Medium"))

    return "HYGothic-Medium", "HYGothic-Medium", False


EMOJI_PATTERN = re.compile("[\U00010000-\U0010ffff☀-➿️‍]")


def pdf_inline(text):
    text = EMOJI_PATTERN.sub("", str(text or ""))
    text = html.escape(text, quote=False)

    return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text).strip()


def publish_text(report):
    """복사·PDF용 글: 쪽 표기 (p.N)를 지우고, 출처 표의 '참고 쪽' 열을 뺍니다."""

    lines, drop = [], None

    for raw in report.splitlines():
        line = raw

        if line.lstrip().startswith("|"):
            cells = line.strip().strip("|").split("|")

            if any(cell.strip() == "참고 쪽" for cell in cells):
                drop = [cell.strip() for cell in cells].index("참고 쪽")

            if drop is not None and drop < len(cells):
                del cells[drop]
                line = "|" + "|".join(cells) + "|"

        else:
            drop = None
            line = PAGE_REF_PATTERN.sub("", line)

        lines.append(line)

    return "\n".join(lines)


def build_pdf(report):
    """마크다운 형태의 리포트를 PDF 바이트로 바꿉니다."""

    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import HRFlowable, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    font, bold, _ = register_korean_font()
    width = A4[0] - 36 * mm

    base = dict(fontName=font, fontSize=9.5, leading=14.5, textColor=colors.HexColor("#222222"))

    styles = {
        "title": ParagraphStyle("t", **{**base, "fontName": bold, "fontSize": 16, "leading": 22, "spaceAfter": 6}),
        "h3": ParagraphStyle("h", **{**base, "fontName": bold, "fontSize": 12, "leading": 17, "spaceBefore": 12, "spaceAfter": 4}),
        "body": ParagraphStyle("b", **{**base, "spaceAfter": 3}),
        "broker": ParagraphStyle("br", **{**base, "fontName": bold, "fontSize": 10.5, "leading": 15, "spaceBefore": 10, "spaceAfter": 3}),
        "note": ParagraphStyle("n", **{**base, "fontSize": 8, "leading": 12, "textColor": colors.HexColor("#666666"), "spaceAfter": 2}),
        "cell": ParagraphStyle("c", **{**base, "fontSize": 8, "leading": 11, "wordWrap": "CJK"}),
        "head": ParagraphStyle("hc", **{**base, "fontName": bold, "fontSize": 8, "leading": 11, "wordWrap": "CJK"}),
    }

    story, table_rows = [], []

    def flush_table():
        if not table_rows:
            return

        rows = [row for row in table_rows if not re.match(r"^\|?\s*:?-{2,}", row)]
        data = [[cell.strip() for cell in row.strip().strip("|").split("|")] for row in rows]
        columns = max(len(row) for row in data)
        data = [row + [""] * (columns - len(row)) for row in data]

        weights = [
            min(34, max(10, max(len(row[i]) for row in data))) for i in range(columns)
        ]
        scale = width / sum(weights)

        cells = [
            [Paragraph(pdf_inline(cell), styles["head" if r == 0 else "cell"]) for cell in row]
            for r, row in enumerate(data)
        ]

        table = Table(cells, colWidths=[w * scale for w in weights], repeatRows=1)
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EEEEEE")),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#BBBBBB")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]))

        story.append(table)
        story.append(Spacer(1, 6))
        table_rows.clear()

    for raw in report.splitlines():
        line = raw.rstrip()

        if line.lstrip().startswith("|"):
            table_rows.append(line)
            continue

        flush_table()

        if not line.strip():
            continue

        if line.startswith("# "):
            story.append(Paragraph(pdf_inline(line[2:]), styles["title"]))
            story.append(HRFlowable(width="100%", thickness=0.8, color=colors.HexColor("#999999"), spaceAfter=6))

        elif line.startswith("### "):
            story.append(Paragraph(pdf_inline(line[4:]), styles["h3"]))

        elif re.match(r"^\*\*\d+\.", line):
            story.append(Paragraph(pdf_inline(line), styles["broker"]))

        elif re.match(r"^\s{2,}-\s", line):
            story.append(Paragraph(
                pdf_inline(line.strip()[2:]),
                ParagraphStyle("b2", parent=styles["body"], leftIndent=26, bulletIndent=15),
                bulletText="–",
            ))

        elif re.match(r"^-\s", line):
            story.append(Paragraph(
                pdf_inline(line[2:]),
                ParagraphStyle("b1", parent=styles["body"], leftIndent=14, bulletIndent=3),
                bulletText="•",
            ))

        elif line.startswith("※"):
            story.append(Paragraph(pdf_inline(line), styles["note"]))

        else:
            story.append(Paragraph(pdf_inline(line), styles["body"]))

    flush_table()

    buffer = io.BytesIO()

    document = SimpleDocTemplate(
        buffer, pagesize=A4,
        leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm, bottomMargin=16 * mm,
        title="증권사 리포트 압축 정리",
    )

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont(font, 8)
        canvas.setFillColor(colors.HexColor("#888888"))
        canvas.drawCentredString(A4[0] / 2, 9 * mm, str(doc.page))
        canvas.restoreState()

    document.build(story, onFirstPage=footer, onLaterPages=footer)

    return buffer.getvalue()


# ===============================================================
# 11. 화면
# ===============================================================

def collect_entries(files):
    """업로드한 파일과 입력칸 값을 정리해 (entries, 오류 목록)을 돌려줍니다."""

    state = st.session_state
    entries, problems = [], []

    for number, uploaded in enumerate(files[:MAX_FILES]):
        uid = f"{number}_{uploaded.name}_{uploaded.size}"

        try:
            pages = read_pdf_pages(uploaded.getvalue())

        except Exception as error:
            problems.append(f"{uploaded.name}: {str(error)[:150]}")
            continue

        cleaned = drop_disclosure(clean_pages(pages))
        body = "\n".join(text for _, text in cleaned)

        first = cleaned[0][1] if cleaned else ""

        defaults = {
            f"rd_broker_{uid}": guess_broker(first, uploaded.name),
            f"rd_company_{uid}": guess_company(first),
            f"rd_title_{uid}": guess_title(uploaded.name),
            f"rd_date_{uid}": guess_date(first),
            f"rd_author_{uid}": "",
            f"rd_link_{uid}": "",
        }

        for key, value in defaults.items():
            state.setdefault(key, value)

        with st.container(border=True):
            st.markdown(f"**{uploaded.name}** · {len(pages)}쪽 · 본문 {len(body):,}자")

            if len(body.strip()) < 200:
                st.warning(
                    "텍스트를 거의 읽지 못했습니다. 이미지로 된 스캔본 PDF는 지원하지 않습니다."
                )

            col1, col2, col3 = st.columns([1.4, 1, 1])

            col1.text_input("증권사 (필수)", key=f"rd_broker_{uid}")
            col2.text_input("발간일", key=f"rd_date_{uid}", placeholder="2026-11-10")
            col3.text_input("애널리스트 (선택)", key=f"rd_author_{uid}")

            col4, col5, col6 = st.columns([1.2, 1.6, 1.4])

            col4.text_input(
                "종목 (필수)",
                key=f"rd_company_{uid}",
                placeholder="삼성전자(005930)",
                help="이 리포트가 다루는 종목입니다. 산업·시황 리포트는 '반도체 산업'처럼 적어 주세요.",
            )
            col5.text_input("리포트 제목", key=f"rd_title_{uid}")
            col6.text_input("원문 링크 (선택)", key=f"rd_link_{uid}")

            typed = state[f"rd_company_{uid}"].strip()
            shown = resolve_company(typed, pages)

            if typed and shown != typed:
                st.caption(f"종목 표기: {shown} (종목코드를 자동으로 붙였습니다)")

        entries.append({
            "file": uploaded.name,
            "pages": cleaned,
            "total_pages": len(pages),
            "broker": state[f"rd_broker_{uid}"].strip(),
            "company": resolve_company(state[f"rd_company_{uid}"], pages),
            "title": state[f"rd_title_{uid}"].strip(),
            "date": state[f"rd_date_{uid}"].strip(),
            "author": state[f"rd_author_{uid}"].strip(),
            "link": state[f"rd_link_{uid}"].strip(),
        })

    if len(files) > MAX_FILES:
        problems.append(f"한 번에 {MAX_FILES}개까지만 처리합니다. 나머지 {len(files) - MAX_FILES}개는 제외했습니다.")

    return entries, problems


def run_with_status(entries, char_limit):
    state = st.session_state

    with st.status("리포트 압축 정리 중... (리포트 1건당 약 1~2분)", expanded=True) as status:
        try:
            run_digest(entries, char_limit, status)

            status.update(label="완료", state="complete", expanded=False)

        except Exception as error:
            state["rd_note"] = f"생성 실패: {error}"
            state["rd_note_level"] = "error"

            status.update(label="오류로 중단됨", state="error", expanded=True)


def main():
    state = st.session_state

    st.title("MTNW 리포트 압축")

    token_box = st.empty()
    show_token_status(token_box)

    st.caption(
        "증권사 리포트(PDF)를 올리면 증권사별로 구분해 압축·정리하고, "
        "출처와 면책 문구를 붙인 게시용 글을 만듭니다. 생성 결과는 새로고침하면 사라집니다."
    )

    with st.expander("게시 전 법적 유의사항 (꼭 읽어 주세요)", expanded=False):
        st.markdown(
            "- **저작권**: 리포트 원문의 저작권은 증권사에 있습니다. 이 도구는 원문 문장을 다시 쓰도록 하고, "
            f"원문과 {VERBATIM_MIN_CHARS}자 이상 같은 문장이 남으면 경고합니다. 표·그래프는 옮기지 않습니다.\n"
            "- **이용약관**: 증권사마다 리포트의 재배포·2차 가공을 제한하는 경우가 있습니다. "
            "외부에 공개하기 전에 각 증권사의 약관과 리포트 첫·마지막 쪽 고지를 확인하세요.\n"
            "- **투자 권유**: 투자의견·목표주가는 '증권사의 견해'로만 표시하며 당사 의견처럼 쓰지 않습니다. "
            "외부 공개용 게시는 준법감시(컴플라이언스) 검토를 받는 것을 권장합니다.\n"
            "- **출처**: 증권사명·종목·리포트 제목·발간일을 출처 표에 남깁니다(참고 쪽은 화면에만 표시되고 복사·PDF에서는 빠집니다). 비어 있는 칸은 직접 채워 주세요.\n"
            "- 이 안내는 법률 자문이 아닙니다. 확실하지 않은 부분은 법무·준법 담당자에게 확인하세요."
        )

    # -----------------------------------------------------------
    # ① 리포트 올리기
    # -----------------------------------------------------------

    st.markdown("### 📥 ① 증권사 리포트 올리기")

    files = st.file_uploader(
        "증권사 리포트 PDF (여러 개 가능, 증권사별로 구분됩니다)",
        type=["pdf"],
        accept_multiple_files=True,
        key="rd_files",
    )

    entries, problems = collect_entries(files or [])

    for problem in problems:
        st.warning(problem)

    # -----------------------------------------------------------
    # ② 옵션 · 생성
    # -----------------------------------------------------------

    st.markdown("### ✨ ② 압축 정리 생성")

    with st.container(border=True):
        char_limit = st.slider(
            "리포트 한 건당 읽을 분량 (앞부분부터, 글자 수)",
            min_value=MIN_CHAR_LIMIT,
            max_value=MAX_CHAR_LIMIT,
            value=DEFAULT_CHAR_LIMIT,
            step=2_000,
            help="분량이 늘수록 정확해지지만 토큰을 더 씁니다. 증권사 리포트는 앞쪽에 요약·결론이 있는 경우가 많습니다.",
            key="rd_limit",
        )

        st.radio(
            "정리 상세도",
            list(DETAIL_LEVELS),
            index=list(DETAIL_LEVELS).index(DEFAULT_DETAIL),
            horizontal=True,
            key="rd_detail",
            help="상세할수록 포인트가 많고 근거·수치가 더 담기지만 토큰을 더 씁니다.",
        )

        st.checkbox(
            "AI 원문 대조 검증 (토큰 추가 사용)",
            value=True,
            key="rd_check",
            help="요약 문장을 원문 발췌와 다시 대조해 수치·목표주가·단위·확정/추정 구분 오류를 찾습니다. "
                 "원문 숫자로 바로잡을 수 있는 경우만 자동 수정하고 나머지는 알림으로 남깁니다.",
        )

        missing = [entry["file"] for entry in entries if not entry["broker"]]
        missing_company = [entry["file"] for entry in entries if not entry["company"]]

        if entries:
            estimate = estimate_run_tokens(entries, char_limit)

            try:
                remaining = max(0, GROQ_DAILY_TOKEN_LIMIT - int(load_usage().get("tokens", 0)))
            except Exception:
                remaining = None

            text = f"예상 사용 토큰: 약 {estimate:,}"

            if remaining is not None:
                text += f" (오늘 남은 토큰 추정 {remaining:,})"

            st.caption(text)

            if remaining is not None and estimate > remaining:
                st.warning("예상 토큰이 오늘 남은 양보다 많습니다. 리포트 수나 읽을 분량을 줄여 주세요.")

        if missing:
            st.error("증권사 이름이 비어 있는 리포트가 있습니다: " + ", ".join(missing))

        if missing_company:
            st.error("종목이 비어 있는 리포트가 있습니다: " + ", ".join(missing_company))

        clicked = st.button(
            "✨ 압축 리포트 생성",
            type="primary",
            use_container_width=True,
            disabled=(not entries or bool(missing) or bool(missing_company)),
        )

        if clicked:
            run_with_status(entries, char_limit)
            show_token_status(token_box)

        if state.get("rd_note"):
            level = state.get("rd_note_level")

            if level == "error":
                st.error(state["rd_note"])
            elif level == "warn":
                st.warning(state["rd_note"])
            else:
                st.success(state["rd_note"])

    # -----------------------------------------------------------
    # ③ 결과
    # -----------------------------------------------------------

    st.markdown("### 📝 ③ 결과")

    warnings = state.get("rd_warnings") or []

    groups = (
        ("[수정 필수]", "① 수정 필수", st.error),
        ("[확인 필요]", "② 확인 필요", st.warning),
        ("[가독성]", "③ 가독성 개선 권장", st.info),
        ("[자동 수정]", "④ 자동 수정된 항목 (수정 전후 확인)", st.info),
    )

    if state.get("rd_report"):
        st.caption(state.get("rd_scope") or "")

    shown = set()

    for prefix, title, show in groups:
        items = [w for w in warnings if w.startswith(prefix)]

        if items:
            shown.update(items)
            st.markdown(f"**{title} ({len(items)})**")

            for item in items:
                show(item[len(prefix):].strip())

    for warning in warnings:
        if warning not in shown:
            st.warning(warning)

    if state.get("rd_report") and not warnings:
        st.success("자동 검증에서 걸린 항목이 없습니다. (정상 판정이 오류 없음을 보증하지는 않으므로 게시 전 원문을 확인하세요.)")

    st.text_area(
        "압축 리포트 (수정 가능 · 복사 버튼은 수정한 내용으로 복사됩니다)",
        key="rd_report",
        height=560,
    )

    report = state.get("rd_report", "").strip()

    if report:
        stamp = datetime.datetime.now(KST).strftime("%Y%m%d_%H%M")

        st.download_button(
            "💾 이 리포트 파일로 저장 (.md)",
            data=report.encode("utf-8"),
            file_name=f"report_digest_{stamp}.md",
            mime="text/markdown",
            key="rd_download",
            help="수정한 내용 그대로 내 컴퓨터에 저장합니다. 새로고침하면 화면의 결과는 사라지니 필요하면 먼저 저장하세요.",
        )

        try:
            pdf_bytes = build_pdf(publish_text(report))

            st.download_button(
                "📄 이 리포트 PDF로 저장",
                data=pdf_bytes,
                file_name=f"report_digest_{stamp}.pdf",
                mime="application/pdf",
                key="rd_download_pdf",
            )

            if not register_korean_font()[2]:
                st.caption("한글 글꼴(나눔고딕)을 찾지 못해 기본 글꼴로 만들었습니다. packages.txt에 fonts-nanum을 추가하세요.")

        except ImportError:
            st.caption("PDF를 만들려면 requirements.txt에 reportlab을 추가하세요.")

        except Exception as error:
            st.caption(f"PDF 생성 실패: {str(error)[:150]}")

        confirmed = st.checkbox(
            "게시 전 확인: 각 증권사 리포트의 이용약관·재배포 허용 범위와 "
            "(외부 공개 시) 준법 검토를 확인했습니다",
            key="rd_ok",
        )

        if confirmed:
            copy_buttons(publish_text(report))
        else:
            st.info("위 확인란에 체크하면 복사 버튼이 나타납니다.")

        with st.container(border=True):
            st.markdown(report)


main()
