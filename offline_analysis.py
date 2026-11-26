"""Offline adapter for existing afternoon analysis functions."""

import ast
import re
import json
import math
import datetime

from pathlib import Path
from zoneinfo import ZoneInfo
from functools import lru_cache


REQUIRED = {
    "THEME_KEYWORDS",
    "_split_words",
    "_keyword_regex",
    "THEMES",
    "num",
    "collect_summary_inputs",
    "fallback_market_summary",
    "generate_core_keywords",
    "content_signature",
    "record_content_generation",
    "content_freshness",
}


def forbidden(*args, **kwargs):
    raise RuntimeError(
        "External API calls are disabled in offline analysis"
    )


@lru_cache(maxsize=1)
def load_analysis():

    path = Path(__file__).with_name("afternoon.py")

    tree = ast.parse(
        path.read_text(encoding="utf-8")
    )

    scope = {
        "re": re,
        "json": json,
        "math": math,
        "datetime": datetime,
        "KST": ZoneInfo("Asia/Seoul"),
        "fetch_strong_themes": forbidden,
        "extractive_summary": forbidden,
    }

    for node in tree.body:
        names = []

        if isinstance(node, ast.FunctionDef):
            names = [node.name]

        elif isinstance(node, ast.Assign):
            names = [
                target.id
                for target in node.targets
                if isinstance(target, ast.Name)
            ]

        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name):
                names = [node.target.id]

        if REQUIRED.intersection(names):

            exec(
                compile(
                    ast.Module(
                        body=[node],
                        type_ignores=[],
                    ),
                    "<offline-analysis>",
                    "exec",
                ),
                scope,
            )

    missing = REQUIRED - scope.keys()

    if missing:
        raise RuntimeError(
            f"Missing analysis dependencies: {missing}"
        )

    return scope


def require_fixture(snapshot):
    if snapshot.get("synthetic") is not True:
        raise ValueError(
            "Offline adapter requires synthetic market data"
        )


def generate_themes(snapshot, state):
    require_fixture(snapshot)

    return " ".join(
        "#" + row["name"]
        for row in state.get("sector_items", [])
    )


def generate_summary(snapshot, state):
    require_fixture(snapshot)

    engine = load_analysis()

    data = engine["collect_summary_inputs"](
        snapshot,
        state,
        allow_fetch=False,
    )

    # 기사 본문 추출 기능은 별도 검증 전이므로
    # 이번 테스트에서는 시장 데이터 요약만 사용합니다.
    data["articles"] = []
    data["issue_titles"] = []

    return "[가상 데이터] " + engine[
        "fallback_market_summary"
    ](data)


def generate_keywords(snapshot, state):
    require_fixture(snapshot)

    return load_analysis()[
        "generate_core_keywords"
    ](snapshot, state)


def record_generation(key, snapshot, state):
    require_fixture(snapshot)

    return load_analysis()[
        "record_content_generation"
    ](key, snapshot, state)


def freshness(key, snapshot, state):
    require_fixture(snapshot)

    return load_analysis()[
        "content_freshness"
    ](key, snapshot, state)


def load_sectors(snapshot):
    require_fixture(snapshot)

    return snapshot["sector_items"]
