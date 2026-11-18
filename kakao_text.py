"""마크다운 시황글을 카카오톡 채널 소식 등 '일반 텍스트만 받는 입력 칸'에 맞게 바꿉니다.

표·제목·굵게 같은 마크다운 기호를 없애고 이모지와 줄바꿈은 그대로 둡니다.
"""

import re


def _clean(text):
    return re.sub(r"[*`]", "", str(text or "")).strip()


def _split_row(line):
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _is_separator(cells):
    return bool(cells) and all(
        re.fullmatch(r":?-+:?", cell.replace(" ", "")) for cell in cells if cell
    ) and any(cells)


def _row_text(header, row):
    header = [_clean(h) for h in header]
    row = [_clean(c) for c in row]

    row = [re.sub(r"\s+(?=[🔺🔻➖▲▼])", "", c) for c in row]

    rank = ""

    if header and header[0] == "순위" and row:
        rank = f"{row[0]}. "
        header, row = header[1:], row[1:]

    pct_index = next((i for i, h in enumerate(header) if "등락" in h), None)

    if pct_index is not None and 1 <= pct_index < len(row):
        head = [c for c in row[:pct_index] if c and c != "-"]

        if head:
            head[0] = f"[{head[0]}]"

        main = " ".join(head)
        rest = [c for c in row[pct_index + 1:] if c and c != "-"]
        text = f"{main} ({row[pct_index]})"

        if rest:
            text += " · " + " · ".join(rest)

        return rank + text

    return rank + " · ".join(c for c in row if c and c != "-")


KEYWORD_COUNT = 5


def _pick_keywords(line, body):
    """키워드 줄에서 본문에 가장 많이 나온 KEYWORD_COUNT개를 원래 순서대로 고릅니다."""

    words = [w.strip() for w in re.split(r"[,，]", _clean(line).replace("#", " , ")) if w.strip()]

    if len(words) <= KEYWORD_COUNT:
        return words

    lowered = body.lower()
    ranked = sorted(
        range(len(words)),
        key=lambda i: (-lowered.count(words[i].lower()), i),
    )[:KEYWORD_COUNT]

    return [words[i] for i in sorted(ranked)]


CONTENT_LIMIT = 100  # 이슈 내용: 이 글자 수 안에서 문장 단위로 자름
FLOW_LIMIT = 120     # 주간 흐름


def _shorten(text, limit):
    """문장 단위로 limit 글자 안에 들어가게 줄입니다. 첫 문장은 길어도 자르지 않습니다."""

    text = " ".join(str(text or "").split())

    if len(text) <= limit:
        return text

    sentences = re.split(r"(?<=[.!?。])\s+", text)
    result = sentences[0]

    for sentence in sentences[1:]:
        if len(result) + 1 + len(sentence) > limit:
            break

        result += " " + sentence

    return result


def _strip_labels(text):
    """'- 내용: ', '- 📌 관련 종목: ', '- ⏰ 체크: ' 같은 머리말을 줄여 한 줄을 짧게 만듭니다."""

    found = re.match(r"^-\s*내용\s*[:：]\s*(.+)$", text)

    if found:
        return _shorten(found.group(1), CONTENT_LIMIT)

    found = re.match(r"^📝\s*주간 흐름\s*[:：]\s*(.+)$", text)

    if found:
        return "📝 주간 흐름: " + _shorten(found.group(1), FLOW_LIMIT)

    found = re.match(r"^-\s*📌\s*관련 종목\s*[:：]\s*(.+)$", text)

    if found:
        names = [n.strip() for n in re.split(r"[,，]", found.group(1)) if n.strip()]

        return "📌 " + "·".join(names)

    found = re.match(r"^-\s*⏰\s*체크\s*[:：]\s*(.+)$", text)

    if found:
        return "⏰ " + found.group(1)

    return text


def _merge_theme_lines(lines):
    """'- 로봇 (+10.53%)' 다음 줄 '- 대표 종목: A' 를 '· 로봇 +10.53% · A' 한 줄로 합칩니다."""

    merged = []
    i = 0

    while i < len(lines):
        line = lines[i]
        nxt = lines[i + 1] if i + 1 < len(lines) else ""

        theme = re.match(r"^-\s*(.+?)\s*\(([^)]*%[^)]*)\)\s*$", line)
        stocks = re.match(r"^-\s*대표 종목\s*[:：]\s*(.+)$", nxt)

        if theme and stocks:
            merged.append(f"· {theme.group(1)} {theme.group(2)} · {stocks.group(1)}")
            i += 2
            continue

        merged.append(line)
        i += 1

    return merged


def _check_line(text):
    """'- [기업 실적·미국] 월마트 실적 발표' → '· 월마트 실적 발표 (미국)'"""

    found = re.match(r"^-\s*\[([^\]]+)\]\s*(.+)$", text)

    if not found:
        return text

    tag, rest = found.groups()
    country = tag.split("·")[-1].strip() if "·" in tag else ""

    return f"· {rest}" + (f" ({country})" if country else "")


def to_kakao_text(markdown):
    body_text = str(markdown or "")
    lines = body_text.splitlines()
    out = []
    table = []
    keyword_section = False
    check_section = False

    def flush_table():
        if not table:
            return

        header = table[0]

        if any("이유" in _clean(h) for h in header) and "종목" in _clean(header[0]):
            for row in table[1:]:
                cells = [_clean(c) for c in row]

                if not cells or not cells[0]:
                    continue

                out.append(f"▪ {cells[0]}")

                if len(cells) > 1 and cells[1] and cells[1] != "-":
                    out.append(f"→ {cells[1]}")

            table.clear()
            return

        for row in table[1:]:
            text = _row_text(header, row)

            if text:
                out.append(text if re.match(r"\d+\. ", text) else "· " + text)

        table.clear()

    for raw in lines:
        stripped = raw.strip()

        if stripped.startswith("|"):
            cells = _split_row(stripped)

            if not _is_separator(cells):
                table.append(cells)

            continue

        flush_table()

        if stripped.startswith("#"):
            title = _clean(stripped.lstrip("#"))

            if out and out[-1] != "":
                out.append("")

            level = len(stripped) - len(stripped.lstrip("#"))
            keyword_section = "핵심 키워드" in title
            check_section = "체크" in title or "일정" in title

            if keyword_section:
                title = re.sub(r"핵심 키워드\s*\d+", f"핵심 키워드 {KEYWORD_COUNT}", title)

            out.append(f"【{title}】" if level >= 3 else title)
            continue

        if keyword_section and stripped:
            picked = _pick_keywords(stripped, body_text.replace(stripped, ""))
            out.append(", ".join(picked))
            keyword_section = False
            continue

        # 줄 전체가 굵은 글씨인 이슈 제목은 ▶ 로 눈에 띄게
        if stripped.startswith("**"):
            out.append("▶ " + _clean(stripped))
            continue

        if stripped.startswith(">"):
            stripped = stripped.lstrip("> ").strip()

        line = _strip_labels(_clean(stripped)) if stripped else ""

        if check_section and line:
            line = _check_line(line)

        out.append(line)

    flush_table()

    out = _merge_theme_lines(out)

    text = "\n".join(out)
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip()
