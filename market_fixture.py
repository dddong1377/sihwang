"""Synthetic market data for offline development."""

import datetime
import math
import random
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")

SCENARIOS = (
    "normal",
    "bull",
    "bear",
    "mixed",
    "missing",
    "ambiguous",
)

SECTORS = (
    "가상반도체",
    "가상로봇",
    "가상바이오",
    "가상자동차",
    "가상전력",
    "가상금융",
    "가상소프트웨어",
    "가상화학",
    "가상조선",
    "가상유통",
)


def build_fixture(scenario="normal", revision=0):
    if scenario not in SCENARIOS:
        raise ValueError("Unknown scenario")

    revision = int(revision)

    if revision < 0:
        raise ValueError("Invalid revision")

    rng = random.Random(20261126 + revision)

    now = datetime.datetime.now(KST)

    bias = {
        "normal": 0.3,
        "bull": 2.5,
        "bear": -2.5,
        "mixed": 0,
        "missing": 0.3,
        "ambiguous": 0.3,
    }[scenario]

    stocks = []

    for i in range(1, 101):
        base = 10000 + i * 1000

        pct = round(rng.uniform(-8, 8) + bias, 2)

        price = max(1, round(base * (1 + pct / 100)))

        pct = (price / base - 1) * 100

        sector = SECTORS[(i - 1) % len(SECTORS)]

        stocks.append({
            "symbol": f"TEST{i:03d}",
            "name": f"가상종목{i:03d}",
            "rank": i,
            "price": price,
            "pct": pct,
            "base_price": base,
            "base_source": "fixture",
            "price_source": "fixture",
            "base_ambiguous": False,
            "theme": sector,
            "reason": "가상 데이터 검증용",
            "headlines": [],
            "sources": [],
            "timestamp": now.isoformat(),
            "live": False,
            "synthetic": True,
        })

    if scenario == "missing":
        stocks[-1]["price"] = None
        stocks[-1]["pct"] = None

    if scenario == "ambiguous":
        row = stocks[-1]

        row.update({
            "base_ambiguous": True,
            "base_source": "limit",
            "candidate_analysis": {
                "count": 2,
                "candidates": [
                    row["base_price"] - 100,
                    row["base_price"],
                ],
                "pct_min": row["pct"],
                "pct_max": (
                    row["price"] /
                    (row["base_price"] - 100) - 1
                ) * 100,
            },
        })

    valid = [
        row for row in stocks
        if row["price"] is not None
        and row["pct"] is not None
        and math.isfinite(row["pct"])
    ]

    missing = [
        row["name"] for row in stocks
        if row not in valid
    ]

    weak = [
        row["name"] for row in valid
        if row["base_ambiguous"]
    ]

    gainers = sorted(
        (row for row in valid if row["pct"] > 0),
        key=lambda row: -row["pct"],
    )[:5]

    losers = sorted(
        (row for row in valid if row["pct"] < 0),
        key=lambda row: row["pct"],
    )[:5]

    market_rates = {
        "normal": (0.8, 1.2),
        "bull": (3.2, 4.1),
        "bear": (-3.2, -4.1),
        "mixed": (1.2, -0.9),
        "missing": (0.8, 1.2),
        "ambiguous": (0.8, 1.2),
    }

    up, down = market_rates[scenario]

    drift = revision * 0.03

    indices = {}

    for key, base, pct in (
        ("kospi", 3000, up + drift),
        ("kosdaq", 800, down + drift),
    ):
        indices[key] = {
            "price": round(base * (1 + pct / 100), 2),
            "pct": pct,
            "previous_close": base,
            "trade_date": now.date().isoformat(),
            "timestamp": now.isoformat(),
            "fetched_at": now.isoformat(),
            "basis_source": "fixture",
            "synthetic": True,
        }

    sectors = []

    for sector in SECTORS:
        members = [
            row for row in valid
            if row["theme"] == sector
        ]

        if not members:
            continue

        pct = sum(
            row["pct"] for row in members
        ) / len(members)

        if pct <= 0:
            continue

        members.sort(key=lambda row: row["rank"])

        sectors.append({
            "name": sector,
            "pct": pct,
            "stocks": members[:5],
            "stock_names": [
                row["name"] for row in members[:5]
            ],
            "leading_stock": members[0]["name"],
            "synthetic": True,
        })

    sectors.sort(key=lambda row: -row["pct"])

    for rank, row in enumerate(sectors, 1):
        row["rank"] = rank

    quality = {
        "source": "fixture",
        "expected": 100,
        "available": len(valid),
        "missing": missing,
        "weak": weak,
        "complete": len(valid) == 100 and not weak,
        "ranking_safe": False,
        "ranking_harmless": [],
        "ranking_risk": ["가상 데이터 — 실제 게시 금지"],
        "synthetic": True,
    }

    return {
        "date": now.strftime("%Y년 %m월 %d일"),
        "fetched_at": now.strftime("%H:%M:%S"),
        "ranking_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "duration": "1d",
        "universe": "cap100",
        "note": "가상 데이터 테스트 — 실제 시세 아님",
        "quality": quality,
        "indices": indices,
        "global": {},
        "gainers": gainers,
        "losers": losers,
        "all_stocks": stocks,
        "sector_items": sectors[:5],
        "synthetic": True,
        "scenario": scenario,
        "revision": revision,
    }
