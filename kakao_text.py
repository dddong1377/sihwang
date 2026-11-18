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


def _strip_labels(text):
    """'- 내용: ', '- 📌 관련 종목: ', '- ⏰ 체크: ' 같은 머리말을 줄여 한 줄을 짧게 만듭니다."""

    found = re.match(r"^-\s*내용\s*[:：]\s*(.+)$", text)

    if found:
        return found.group(1)

    found = re.match(r"^-\s*📌\s*관련 종목\s*[:：]\s*(.+)$", text)

    if found:
        names = [n.strip() for n in re.split(r"[,，]", found.group(1)) if n.strip()]

        return "📌 " + "·".join(names)

    found = re.match(r"^-\s*⏰\s*체크\s*[:：]\s*(.+)$", text)

    if found:
        return "⏰ " + found.group(1)

    return text


def to_kakao_text(markdown):
    body_text = str(markdown or "")
    lines = body_text.splitlines()
    out = []
    table = []
    keyword_section = False

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

        out.append(_strip_labels(_clean(stripped)) if stripped else "")

    flush_table()

    text = "\n".join(out)
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip()
