from app.quality import instant, integer, issue, token

TRIAL_MARKS = ("试签", "trial", "TRIAL", "Trial")


def recipe_kind(raw, version):
    """Classify a recipe submission without trusting its row position.

    ``kind`` is honoured when present; otherwise the version label decides.
    A trial signature is provisional evidence and never outranks a formal
    version covering the same seconds.
    """
    kind = raw.get("kind")
    if isinstance(kind, str):
        normal = kind.strip().lower()
        if normal in ("trial", "试签"):
            return "trial"
        if normal in ("formal", "正式"):
            return "formal"
    return "trial" if any(mark in version for mark in TRIAL_MARKS) else "formal"


def recipe_windows(records, zones):
    """Parse recipe rows and collapse identical retries.

    A retried submission of the same version/window/targets is one recipe
    window carrying several evidence rows, so it cannot add a second
    overlapping window or count the same seconds twice.
    """
    parsed, isolated = [], []
    for row, raw in records:
        try:
            start, end = instant(raw["start"]), instant(raw["end"])
            if (
                start >= end
                or not isinstance(raw["target_mc"], dict)
                or set(raw["target_mc"]) != set(zones)
            ):
                raise ValueError
            version = token(raw["version"])
            parsed.append(
                {
                    "row": row,
                    "rows": [row],
                    "version": version,
                    "kind": recipe_kind(raw, version),
                    "start": start,
                    "end": end,
                    "target_mc": {
                        z: integer(raw["target_mc"][z], 0, 1600000) for z in zones
                    },
                }
            )
        except (KeyError, TypeError, ValueError):
            isolated.append(issue("BAD_RECIPE", row, raw))

    grouped = {}
    for item in parsed:
        key = (
            item["version"],
            item["start"],
            item["end"],
            tuple(sorted(item["target_mc"].items())),
        )
        if key in grouped:
            kept = grouped[key]
            kept["rows"].append(item["row"])
            if item["kind"] == "formal":
                kept["kind"] = "formal"
        else:
            grouped[key] = item

    versions = sorted(
        grouped.values(),
        key=lambda item: (item["start"], item["end"], item["rows"][0], item["version"]),
    )
    for item in versions:
        item["rows"].sort()
    return versions, isolated
