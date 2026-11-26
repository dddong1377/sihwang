"""Independent content refresh engine."""

from copy import deepcopy


def can_refresh(key, state):
    if key not in state:
        return True

    generated_key = f"{key}__generated"

    if generated_key not in state:
        return False

    return state[key] == state[generated_key]


def refresh_content(
    state,
    snapshot,
    generators,
    recorder,
    force=False,
    sector_loader=None,
):
    """Refresh automatic content while preserving manual edits."""

    report = {
        "updated": [],
        "preserved": [],
        "failed": [],
        "skipped": [],
    }

    if sector_loader is not None:
        try:
            sectors = sector_loader(snapshot)

            if not isinstance(sectors, list):
                raise ValueError("Invalid sector data")

            if any(
                not isinstance(row, dict) or not row.get("name")
                for row in sectors
            ):
                raise ValueError("Invalid sector item")

            state["sector_items"] = deepcopy(sectors)

            state["sector_fetch_status"] = (
                "success" if sectors else "empty"
            )

            state["sector_fetched_at"] = snapshot.get("fetched_at")
            report["sector_status"] = state["sector_fetch_status"]

        except Exception as error:
            state["sector_items"] = []
            state["sector_fetch_status"] = "failed"
            state["sector_fetched_at"] = None

            report["sector_status"] = "failed"

            report["failed"].append({
                "key": "sectors",
                "error": str(error),
            })

    for key in ("themes", "summary", "keywords"):

        if key not in generators:
            report["skipped"].append(key)
            continue

        if not force and not can_refresh(key, state):
            report["preserved"].append(key)
            continue

        if (
            key == "themes"
            and sector_loader is not None
            and state.get("sector_fetch_status") in ("failed", "empty")
        ):
            report["skipped"].append(key)
            continue

        backup = deepcopy(dict(state))
        working = deepcopy(backup)

        try:
            value = generators[key](snapshot, working)

            if not isinstance(value, str):
                raise ValueError("Invalid generated content")

            if not value.strip():
                raise ValueError("Empty generated content")

            working[key] = value

            recorder(key, snapshot, working)

            allowed_keys = [
                key,
                f"{key}__generated",
                f"{key}__signature",
                f"{key}__generated_at",
            ]

            if key == "themes":
                allowed_keys.extend([
                    "sector_items",
                    "sector_fetch_status",
                    "sector_fetched_at",
                ])

            for name in allowed_keys:
                if name in working:
                    state[name] = working[name]

            report["updated"].append(key)

        except Exception as error:
            report["failed"].append({
                "key": key,
                "error": str(error),
            })

    return report
