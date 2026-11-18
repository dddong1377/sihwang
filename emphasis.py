"""복사 전에 글의 강조를 통일합니다 (서식 포함·마크다운·카카오톡용 공통).

- 지수·환율 표(첫 칸 머리글이 '지수' 또는 '구분')의 이름 칸을 굵게
- 서식 포함(HTML)의 제목(#, ###)은 확실히 굵게
"""

import re

_INDEX_HEADERS = ("지수", "구분")


def emphasize(markdown):
    lines = str(markdown or "").split("\n")
    out = []
    bold_first_col = False
    row_in_table = 0

    for line in lines:
        stripped = line.strip()

        if not stripped.startswith("|"):
            bold_first_col = False
            row_in_table = 0
            out.append(line)
            continue

        cells = [c.strip() for c in stripped.strip("|").split("|")]
        row_in_table += 1

        if row_in_table == 1:
            bold_first_col = bool(cells) and cells[0] in _INDEX_HEADERS
            out.append(line)
            continue

        is_separator = all(re.fullmatch(r":?-+:?", c.replace(" ", "")) for c in cells if c) and any(cells)

        if bold_first_col and not is_separator and cells and cells[0] and not cells[0].startswith("**"):
            cells[0] = f"**{cells[0]}**"
            line = "| " + " | ".join(cells) + " |"

        out.append(line)

    return "\n".join(out)


def bold_headings(html_text):
    """md_to_html 결과의 제목 태그에 굵기를 명시합니다."""

    html_text = re.sub(r"<h1 style='", "<h1 style='font-weight:bold;", html_text)
    html_text = re.sub(r"<h3 style='", "<h3 style='font-weight:bold;", html_text)

    return html_text
