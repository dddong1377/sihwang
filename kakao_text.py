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


def to_kakao_text(markdown):
    lines = str(markdown or "").splitlines()
    out = []
    table = []

    def flush_table():
        if not table:
            return

        header = table[0]

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
            out.append(f"【{title}】" if level >= 3 else title)
            continue

        # 줄 전체가 굵은 글씨인 이슈 제목은 ▶ 로 눈에 띄게
        if stripped.startswith("**"):
            out.append("▶ " + _clean(stripped))
            continue

        if stripped.startswith(">"):
            stripped = stripped.lstrip("> ").strip()

        out.append(_clean(stripped) if stripped else "")

    flush_table()

    text = "\n".join(out)
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip()
