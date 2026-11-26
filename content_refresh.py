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
):
    """Refresh automatic content while preserving manual edits."""

    report = {
        "updated": [],
        "preserved": [],
        "failed": [],
        "skipped": [],
    }

    for key in ("themes", "summary", "keywords"):

        if key not in generators:
            report["skipped"].append(key)
            continue

        if not force and not can_refresh(key, state):
            report["preserved"].append(key)
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
