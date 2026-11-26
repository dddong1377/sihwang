"""시황글(마크다운)을 신문 '지면'에 담을 재료로 나눕니다.

이미지(paper_images.py)와 사이트 지면 페이지(paper_html.py)가 같은 재료를 씁니다.
카드 이미지의 파서(card_images)를 그대로 가져다 쓰므로, 시황글 형식이 바뀌어도 함께 따라갑니다.
"""

import datetime
import re

from card_images import _plain, _runs, build_blocks, parse_markdown
from kakao_text import KEYWORD_COUNT, _pick_keywords

WEEKDAY_FULL = "월화수목금토일"
WEEKDAY_NAME = {"월": "월요일", "화": "화요일", "수": "수요일", "목": "목요일", "금": "금요일", "토": "토요일", "일": "일요일"}
EDITION = {"pre": "장전판", "aft": "오후판", "week": "주간 특집판"}
RAIL_TITLE = {"pre": "숫자로 본 아침", "aft": "숫자로 본 장중", "week": "숫자로 본 한 주"}

_EMOJI_HEAD = re.compile(r"^[\s\W_]*(?=[0-9A-Za-z가-힣\[(])", re.U)


def clean_title(title):
    """'🔥 오늘의 핵심 포인트 TOP3' → '오늘의 핵심 포인트 TOP3' (앞의 이모지·기호 제거)"""

    text = _plain(title)
    found = _EMOJI_HEAD.match(text)

    return text[found.end():].strip() if found else text


def direction_of(text):
    text = str(text or "").strip()

    if text.startswith(("🔺", "▲", "+")) or "🔺" in text[:3]:
        return "up"

    if text.startswith(("🔻", "▼", "-", "−")) or "🔻" in text[:3]:
        return "down"

    return "flat"


def flat_text(runs):
    return "".join(r[0] for r in runs).strip()


def detect_kind(title):
    if "주간" in title:
        return "week"

    if "오후" in title or "장중" in title or "마감" in title:
        return "aft"

    return "pre"


def parse_date(title):
    """(date 또는 None, 표시용 날짜 글)"""

    found = re.search(r"(\d{4})년\s*(\d{1,2})월\s*(\d{1,2})일\s*(?:\(([월화수목금토일])\))?", title)

    if not found:
        return None, ""

    year, month, day = int(found.group(1)), int(found.group(2)), int(found.group(3))

    try:
        date = datetime.date(year, month, day)
    except ValueError:
        return None, ""

    weekday = WEEKDAY_FULL[date.weekday()]
    text = f"{year}년 {month}월 {day}일 {WEEKDAY_NAME[weekday]}"

    second = re.search(r"~\s*(\d{1,2})월\s*(\d{1,2})일", title)

    if second:
        text = f"{year}년 {month}월 {day}일 ~ {int(second.group(1))}월 {int(second.group(2))}일"

    return date, text


def _is_index_header(header):
    return (
        len(header) >= 3
        and header[0] in ("지수", "구분")
        and any(("등락" in h or "전일" in h) for h in header[1:])
    )


def _index_rows(block, group):
    header = block["header"]
    pct = next((i for i, h in enumerate(header) if "등락" in h or "전일" in h), len(header) - 1)
    value = 1 if pct != 1 else 2
    rows = []

    for row in block["rows"]:
        cells = [flat_text(c) for c in row] + [""] * len(header)
        name, val, change = cells[0], cells[value], cells[pct]

        if not name or name == "-":
            continue

        rows.append({"group": group, "name": name, "value": val, "change": change, "dir": direction_of(change)})

    return rows


def _flatten_items(items):
    """목록(들여쓴 하위 항목 포함)을 [{'head','detail'}] 로 바꿉니다."""

    out = []

    for it in items:
        text = flat_text(it["runs"])

        if it.get("label"):
            text = (it["label"] + " " + text).strip()

        if it.get("date"):
            text = it["date"] + " " + text

        if it.get("level") and out:
            detail = re.sub(r"^[^:：]{1,8}[:：]\s*", "", text).strip()
            out[-1]["detail"] = (out[-1]["detail"] + " · " + detail).strip(" ·") if out[-1]["detail"] else detail
        else:
            out.append({"head": text, "date": it.get("date", ""), "detail": ""})

    return out


_DIR_EMOJI = {"🔺": "up", "🔻": "down", "➖": "flat"}


def _split_issue_title(title, lead_dir):
    """이슈 제목에서 🔥·🔺·🔻·➖ 표시를 떼어 내고 방향만 남깁니다."""

    direction = _DIR_EMOJI.get(lead_dir, "flat")
    text = str(title or "").replace("🔥", " ").strip()

    for emoji, name in _DIR_EMOJI.items():
        if text.startswith(emoji):
            direction, text = name, text[len(emoji):].strip()
            break

    tail = re.search(r"\s*(🔺|🔻|➖)\s*$", text)

    if tail:
        direction = _DIR_EMOJI[tail.group(1)]
        text = text[: tail.start()].strip()

    return re.sub(r"\s{2,}", " ", text), direction


def _item_label_text(item):
    label = (item.get("label") or "").strip().rstrip(":：").strip()
    label = re.sub(r"^[^\w가-힣]+", "", label).strip()

    return label, flat_text(item["runs"])


def build_model(markdown):
    title, summary, sections = parse_markdown(markdown)
    title = _plain(title)
    kind = detect_kind(title)
    date, date_text = parse_date(title)

    found = re.search(
        r"국내 시장 데이터 조회:\s*([^\n]+)",
        str(markdown or ""),
    )

    market_at = found.group(1).strip() if found else ""

    model = {
        "kind": kind,
        "edition": EDITION[kind],
        "rail_title": RAIL_TITLE[kind],
        "title": clean_title(title),
        "date_iso": date.isoformat() if date else "",
        "date_text": date_text,
        "market_at": market_at,
        "headline": summary,
        "indices": [],
        "bands": [],
        "issues": [],
        "flow": "",
        "pieces": [],
    }

    body_text = str(markdown or "")

    for section in sections:
        sec_title = clean_title(section["title"])

        if "주간 주목 종목" in sec_title:
            continue

        blocks = build_blocks(section["lines"])

        if not blocks:
            continue

        is_index_sec = any(k in sec_title for k in ("지수", "성적표"))
        piece_blocks = []

        for block in blocks:
            if block["type"] == "table" and _is_index_header(block["header"]):
                model["indices"] += _index_rows(block, "해외" if "해외" in sec_title else "국내")
            elif block["type"] == "table" and is_index_sec and model["kind"] == "week":
                model["bands"].append({"title": sec_title, "header": block["header"], "rows": [[flat_text(c) for c in r] for r in block["rows"]], "align": block["align"]})
            elif block["type"] == "issue":
                title_text, direction = _split_issue_title(block["title"], block["dir"])
                issue = {
                    "num": block["num"],
                    "dir": direction,
                    "title": title_text,
                    "fire": "🔥" in block["title"],
                    "items": [],
                    "section": sec_title,
                }

                for it in block["items"]:
                    label, text = _item_label_text(it)

                    if text:
                        issue["items"].append({"label": label, "text": text})

                model["issues"].append(issue)
            elif block["type"] in ("p", "note") and "주간 흐름" in sec_title:
                model["flow"] = flat_text(block["runs"])
            elif is_index_sec and block["type"] in ("p", "note"):
                continue  # 지수표 아래 안내 문구(※ …)는 지면에서 생략
            else:
                piece_blocks.append(block)

        if not piece_blocks:
            continue

        model["pieces"].append(_make_piece(sec_title, piece_blocks, body_text))

    model["pieces"] = [p for p in model["pieces"] if p]

    return model


def _make_piece(sec_title, blocks, body_text):
    if "키워드" in sec_title:
        line = ""

        for block in blocks:
            if block["type"] in ("p", "note"):
                line = flat_text(block["runs"])
                break

            if block["type"] == "list" and block["items"]:
                line = ", ".join(flat_text(it["runs"]) for it in block["items"])
                break

        if not line:
            return None

        words = _pick_keywords(line, body_text.replace(line, ""))

        return {"type": "keywords", "title": re.sub(r"\s*\d+$", "", sec_title) + f" {KEYWORD_COUNT}" if re.search(r"\d+$", sec_title) else sec_title, "words": [w.lstrip("#") for w in words]}

    if "주의" in sec_title:
        text = " ".join(flat_text(b["runs"]) for b in blocks if b["type"] in ("p", "note")) or " ".join(
            flat_text(it["runs"]) for b in blocks if b["type"] == "list" for it in b["items"]
        )

        return {"type": "caution", "title": sec_title, "text": text} if text else None

    if "테마" in sec_title:
        paragraph = next((flat_text(b["runs"]) for b in blocks if b["type"] == "p"), "")

        if paragraph:
            tags = [t.strip() for t in re.split(r"\s*[,，·/]\s*(?![^()]*\))", paragraph) if t.strip()]

            return {"type": "tags", "title": sec_title, "tags": tags}

        items = []

        for block in blocks:
            if block["type"] == "list":
                items += _flatten_items(block["items"])

        return {"type": "rows", "title": sec_title, "rows": items} if items else None

    tables = [b for b in blocks if b["type"] == "table"]
    lists = [b for b in blocks if b["type"] == "list"]

    if tables and not lists:
        table = tables[0]

        return {
            "type": "table",
            "title": sec_title,
            "header": table["header"],
            "rows": [[flat_text(c) for c in r] for r in table["rows"]],
            "align": table["align"],
            "name": table["name"],
            "extra": [flat_text(b["runs"]) for b in blocks if b["type"] in ("p", "note")],
        }

    items = []

    for block in blocks:
        if block["type"] == "list":
            items += _flatten_items(block["items"])
        elif block["type"] in ("p", "note"):
            if block["type"] == "p":
                items.append({"head": flat_text(block["runs"]), "date": "", "detail": ""})
        elif block["type"] == "table":
            for row in block["rows"]:
                items.append({"head": " · ".join(flat_text(c) for c in row if flat_text(c)), "date": "", "detail": ""})

    return {"type": "rows", "title": sec_title, "rows": items} if items else None


# ---------------------------------------------------------------
# 호수 (저장된 글 수로 자동 부여)
# ---------------------------------------------------------------

def issue_number(model, saved_days):
    """saved_days: 저장된 날짜 ISO 글 목록. 호수 = (이 날짜까지 저장된 날 수), 아직 저장 전이면 +1."""

    iso = model.get("date_iso")

    if not iso:
        return None

    days = {d for d in saved_days if isinstance(d, str)}

    if model["kind"] == "week":
        weeks = set()

        for d in days | {iso}:
            try:
                year, week, _ = datetime.date.fromisoformat(d).isocalendar()
            except ValueError:
                continue

            if d <= iso:
                weeks.add((year, week))

        return len(weeks) or 1

    return len({d for d in days | {iso} if d <= iso})
