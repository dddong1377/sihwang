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

        total += 700 + 900

    if len(entries) >= 2:
        total += 2_500

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
6. 증권사 고지·면책·공시 문구는 무시한다."""


MAP_PROMPT = """# 역할
증권사 리포트의 일부를 읽고 '사실 메모'를 만든다.

__RULES__

# 출력 형식
- 한 줄에 메모 하나, 최대 10줄
- 줄 끝에 쪽 번호를 (p.N) 형식으로 붙인다
- 다른 설명은 쓰지 않는다

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
의견: 투자의견 || 목표주가 || 기준일 또는 전제 (원문에 없으면 각각 -)
핵심: 리포트의 핵심 한 문장 (60자 이내, 증권사 견해로 표현)
포인트: 주요 내용 한 문장 (p.N)
포인트: ... (3~5줄)
수치: 항목=값 (p.N)  (원문에 있는 수치만, 0~4줄)
리스크: 원문이 언급한 위험 요인 한 문장 (p.N) (없으면 -)

# 자료
__MATERIAL__
"""


COMPARE_PROMPT = """# 역할
같은 시점에 나온 여러 증권사 리포트의 요약을 비교하는 에디터다.

__RULES__
7. 어느 증권사의 견해인지 증권사 이름을 반드시 붙인다.
8. 자료에 없는 내용은 쓰지 않는다. 비교할 근거가 없으면 해당 줄은 -로 쓴다.

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
            parts = [part.strip() for part in value.split("||")] + ["", "", ""]
            parts = ["" if part in ("-", "없음") else part for part in parts]

            result["opinion"], result["target"], result["basis"] = parts[:3]

        elif key == "핵심":
            result["core"] = value

        elif key == "포인트" and value not in ("", "-"):
            result["points"].append(value)

        elif key == "수치" and value not in ("", "-"):
            result["numbers"].append(value)

        elif key == "리스크":
            result["risk"] = "" if value in ("-", "없음") else value

    result["points"] = result["points"][:5]
    result["numbers"] = result["numbers"][:4]

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


def summarize_report(entry, char_limit, status):
    label = entry["broker"]
    chunks, used = build_chunks(entry["pages"], char_limit)

    if not chunks:
        raise RuntimeError("읽을 수 있는 본문이 없습니다(스캔본 PDF일 수 있습니다).")

    first_page, last_page = chunks[0][0][0], chunks[-1][-1][0]
    entry["range"] = f"p.{first_page}" if first_page == last_page else f"p.{first_page}~{last_page}"
    entry["used_chars"] = used

    info = f"증권사: {label} / 제목: {entry['title'] or '-'}"

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

        material, kind = "\n".join(memos)[:8_000], "사실 메모"

    status.write(f"  · {label} 압축 정리 중")

    prompt = (
        with_rules(REDUCE_PROMPT)
        .replace("__KIND__", kind)
        .replace("__INFO__", info)
        .replace("__MATERIAL__", material)
    )

    raw = groq_chat(prompt, REDUCE_MAX_TOKENS, 0.2, f"{label} 압축")
    summary = parse_summary(raw)

    if not (summary["core"] or summary["points"]):
        raise RuntimeError("AI 응답 형식을 해석하지 못했습니다.")

    entry["raw"] = raw

    source = "\n".join(text for _, text in entry["pages"])

    shingles = make_shingles(source)

    entry["verbatim_left"] = reword_flagged(summary, shingles, label, status)
    entry["notes"] = verify_summary(entry, summary, source)

    return summary


def compare_summaries(entries, summaries, status):
    """증권사가 2곳 이상일 때 공통점·차이점과 한 줄 요약을 만듭니다."""

    blocks = []

    for entry, summary in zip(entries, summaries):
        parts = [f"[{entry['broker']}]"]

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

    lines = [f"**{number}. {entry['broker']} | {title}**{date}"]

    if summary["core"]:
        lines.append(f"- 핵심: {summary['core']}")

    if INCLUDE_RATING:
        opinion, target = summary["opinion"], summary["target"]

        if opinion or target:
            text = f"{entry['broker']} 의견 {opinion or '-'} / 목표주가 {target or '-'}"

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
        lines.append("- 주요 수치 (원문 기준)")

        for item in summary["numbers"]:
            lines.append(f"  - {item}")

    if summary["risk"]:
        lines.append(f"- 리스크: {summary['risk']}")

    lines.append("")

    return lines


def build_report(entries, summaries, headline, common, diff, failures):
    now = datetime.datetime.now(KST)

    lines = [
        f"# 📑 증권사 리포트 압축 정리 | {now.year}년 {now.month}월 {now.day}일",
        "",
    ]

    if headline:
        lines += [f"📌 **한 줄 요약**: {headline}", ""]

    lines += [
        f"※ 아래 {len(entries)}개 증권사 리포트를 요약했습니다. 각 내용은 해당 증권사의 견해입니다.",
        "",
        "### 🏦 증권사별 요약",
        "",
    ]

    for number, (entry, summary) in enumerate(zip(entries, summaries), start=1):
        lines += broker_block(number, entry, summary)

    if common or diff:
        lines += ["### 🔎 증권사 간 비교", ""]

        for item in common:
            lines.append(f"- 공통: {item}")

        for item in diff:
            lines.append(f"- 차이: {item}")

        lines.append("")

    lines += [
        "### 📚 출처",
        "",
        "| 증권사 | 리포트 제목 | 작성자 | 발간일 | 참고 쪽 | 원문 링크 |",
        "|---|---|---|---|---|---|",
    ]

    for entry in entries:
        lines.append(
            f"| {clean_cell(entry['broker'])} | {clean_cell(entry['title'])} "
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

    headline, common, diff = "", [], []

    if len(done_entries) >= 2:
        try:
            headline, common, diff = compare_summaries(done_entries, summaries, status)

        except Exception as error:
            warnings.append(f"증권사 비교 실패: {str(error)[:150]}")

    if not headline:
        headline = summaries[0]["core"] if len(done_entries) == 1 else ""

        if any(word in headline for word in BANNED_WORDS):
            headline = ""

    report = build_report(done_entries, summaries, headline, common, diff, failures)

    for match in SOLICITATION_PATTERN.finditer(report):
        warnings.append(f"권유성 표현 의심: '{match.group(0)}' — 게시 전에 확인해 주세요.")

    state["rd_report"] = report
    state["rd_warnings"] = warnings
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

            col4, col5 = st.columns([1.4, 2])

            col4.text_input("리포트 제목", key=f"rd_title_{uid}")
            col5.text_input("원문 링크 (선택)", key=f"rd_link_{uid}")

        entries.append({
            "file": uploaded.name,
            "pages": cleaned,
            "total_pages": len(pages),
            "broker": state[f"rd_broker_{uid}"].strip(),
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
            "- **출처**: 증권사명·리포트 제목·발간일·참고 쪽을 출처 표에 남깁니다. 비어 있는 칸은 직접 채워 주세요.\n"
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

        missing = [entry["file"] for entry in entries if not entry["broker"]]

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

        clicked = st.button(
            "✨ 압축 리포트 생성",
            type="primary",
            use_container_width=True,
            disabled=(not entries or bool(missing)),
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

    for warning in state.get("rd_warnings") or []:
        st.warning(warning)

    st.text_area(
        "압축 리포트 (수정 가능 · 복사 버튼은 수정한 내용으로 복사됩니다)",
        key="rd_report",
        height=560,
    )

    report = state.get("rd_report", "").strip()

    if report:
        confirmed = st.checkbox(
            "게시 전 확인: 각 증권사 리포트의 이용약관·재배포 허용 범위와 "
            "(외부 공개 시) 준법 검토를 확인했습니다",
            key="rd_ok",
        )

        if confirmed:
            copy_buttons(report)
        else:
            st.info("위 확인란에 체크하면 복사 버튼이 나타납니다.")

        with st.container(border=True):
            st.markdown(report)


main()
