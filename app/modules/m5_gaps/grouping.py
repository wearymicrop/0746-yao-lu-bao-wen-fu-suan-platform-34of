def gap_windows(sections):
    gaps = []
    for item in sections:
        if item["state"] != "unproven":
            continue
        zones = sorted(
            zone["zone"] for zone in item["zones"] if zone["value_mc"] is None
        )
        causes = item["codes"]
        if (
            gaps
            and gaps[-1]["end"] == item["start"]
            and gaps[-1]["codes"] == causes
            and gaps[-1]["zones"] == zones
        ):
            current = gaps[-1]
            current["end"] = item["end"]
            current["seconds"] += item["seconds"]
        else:
            current = {
                "start": item["start"],
                "end": item["end"],
                "seconds": item["seconds"],
                "time": item["time"],
                "codes": causes,
                "zones": zones,
                "recipe_rows": [],
                "reading_rows": [],
                "recipe_sources": [],
                "pending_sources": [],
                "retained_version": None,
                "retained_rows": [],
                "retained_state": None,
            }
            gaps.append(current)
        current["recipe_rows"] = sorted(
            set(current["recipe_rows"] + item["recipe_rows"])
        )
        current["reading_rows"] = sorted(
            set(
                current["reading_rows"]
                + [row for zone in item["zones"] for row in zone["reading_rows"]]
            )
        )
        current["recipe_sources"] = merge_sources(
            current["recipe_sources"], item["recipe_sources"]
        )
        current["pending_sources"] = merge_sources(
            current["pending_sources"], item["pending_sources"]
        )
        if current["retained_version"] is None and item["retained_version"]:
            current["retained_version"] = item["retained_version"]
            current["retained_rows"] = list(item["retained_rows"])
        if current["retained_state"] is None and item["retained_state"]:
            current["retained_state"] = item["retained_state"]
    return gaps


def merge_sources(collected, incoming):
    merged = {tuple(row["rows"]): row for row in collected}
    for row in incoming:
        merged.setdefault(tuple(row["rows"]), row)
    return [merged[key] for key in sorted(merged)]
