from bisect import bisect_left, bisect_right

from app.modules.m4_soak.recipes import recipe_windows
from app.quality import rows, utc

EFFECTIVE = "EFFECTIVE"
TRIAL = "TRIAL"
UNRESOLVED = "UNRESOLVED"
MISSING = "MISSING"

PENDING = (UNRESOLVED, MISSING)


def covering(versions, start):
    """Every distinct recipe window covering ``start`` (left-closed)."""
    return [
        version for version in versions if version["start"] <= start < version["end"]
    ]


def evidence(recipes, chosen=None):
    """Record every covering source and how the ruling treated it."""
    return [
        {
            "version": recipe["version"],
            "kind": recipe["kind"],
            "rows": list(recipe["rows"]),
            "start": recipe["start"],
            "end": recipe["end"],
            "disposition": "CHOSEN" if recipe is chosen else "PENDING",
        }
        for recipe in recipes
    ]


def adjudicate(recipes):
    """Ruling for one slice of time.

    Arrival order is not evidence. One distinct window is effective; a trial
    signature alone is marked provisional. Two distinct windows covering the
    same seconds cannot be ranked from the record, so the ruling fails: the
    slice stays unresolved, every source is kept as pending evidence, and the
    caller carries the previously effective version along so the old
    conclusion and its duration are not erased. Identical retries never reach
    here because ``recipe_windows`` collapses them.
    """
    if not recipes:
        return None, MISSING, [], [], "缺少配方"
    if len(recipes) == 1:
        only = recipes[0]
        status = TRIAL if only["kind"] == "trial" else EFFECTIVE
        decision = "采用试签版" if status == TRIAL else "采用正式版"
        return only, status, evidence(recipes, only), [], decision
    disputed = evidence(recipes)
    return None, UNRESOLVED, disputed, disputed, "重叠无法裁决，保留上一生效结论"


def merge_runs(records):
    """Merge adjacent slices sharing the same version."""
    runs = []
    for start, end, version in records:
        if runs and runs[-1]["end"] == start and runs[-1]["version"] == version:
            runs[-1]["end"] = end
            runs[-1]["seconds"] += end - start
        else:
            runs.append(
                {"start": start, "end": end, "seconds": end - start, "version": version}
            )
    return runs


def verdict(zone_rows, targets):
    """Judge a slice, keeping missing evidence ahead of a low reading."""
    if any(item["value_mc"] is None for item in zone_rows):
        return "unproven"
    if any(
        item["value_mc"] < targets[item["zone"]]
        for item in zone_rows
        if item["zone"] in targets
    ):
        return "cold"
    return "proven"


def attach_context(sections, streaks):
    """Annotate every slice with the continuous soak standing around it."""
    starts = [streak["start"] for streak in streaks]
    ends = [streak["end"] for streak in streaks]
    for section in sections:
        start, end = section["start"], section["end"]
        index = bisect_right(starts, start) - 1
        inside = (
            index
            if index >= 0
            and section["state"] == "proven"
            and streaks[index]["start"] <= start
            and end <= streaks[index]["end"]
            else None
        )
        previous = bisect_right(ends, start) - 1
        following = bisect_left(starts, end)
        section.update(
            {
                "streak_index": inside + 1 if inside is not None else None,
                "streak_start": streaks[inside]["start"] if inside is not None else None,
                "streak_end": streaks[inside]["end"] if inside is not None else None,
                "streak_s": streaks[inside]["seconds"] if inside is not None else 0,
                "run_before_s": start - streaks[inside]["start"]
                if inside is not None
                else streaks[previous]["seconds"]
                if previous >= 0 and streaks[previous]["end"] == start
                else 0,
                "run_after_s": streaks[inside]["end"] - end
                if inside is not None
                else streaks[following]["seconds"]
                if following < len(streaks) and streaks[following]["start"] == end
                else 0,
                "previous_streak_s": streaks[previous]["seconds"]
                if previous >= 0
                else 0,
                "next_streak_s": streaks[following]["seconds"]
                if following < len(streaks)
                else 0,
            }
        )


def measure(data, plan, alignment):
    versions, isolated = recipe_windows(rows(data, "recipes"), plan["zones"])
    edges = set(alignment["breakpoints"])
    edges.update(
        edge
        for version in versions
        for edge in (version["start"], version["end"])
        if plan["start"] < edge < plan["end"]
    )
    sorted_edges = sorted(edges)
    counters = dict.fromkeys(plan["zones"], 0)
    sections, streaks = [], []
    confirmed = []
    retained = None
    for start, end in zip(sorted_edges, sorted_edges[1:]):
        chosen, status, found, pending, decision = adjudicate(covering(versions, start))
        zone_rows, codes = [], []
        if status in PENDING:
            codes.append("RECIPE_OVERLAP" if status == UNRESOLVED else "RECIPE_GAP")
        for zone, bands in alignment["zones"].items():
            while counters[zone] < len(bands) and bands[counters[zone]]["end"] <= start:
                counters[zone] += 1
            index = counters[zone]
            band = (
                bands[index]
                if index < len(bands) and bands[index]["start"] <= start
                else None
            )
            value = band["value_mc"] if band else None
            target = chosen["target_mc"][zone] if chosen else None
            absent = (band["code"] if band else "NO_READING") if value is None else None
            below = value is not None and target is not None and value < target
            if absent:
                codes.append(absent)
            zone_rows.append(
                {
                    "zone": zone,
                    "value_mc": value,
                    "target_mc": target,
                    "reading_rows": band["reading_rows"] if band else [],
                    "calibration_row": band["calibration_row"] if band else None,
                    "code": absent or ("BELOW_TARGET" if below else None),
                }
            )
        below_target = any(item["code"] == "BELOW_TARGET" for item in zone_rows)
        state = "unproven" if codes else "cold" if below_target else "proven"
        retained_state = (
            verdict(zone_rows, retained["target_mc"])
            if chosen is None and retained is not None
            else None
        )
        seconds = end - start
        section = {
            "start": start,
            "end": end,
            "time": utc(start),
            "seconds": seconds,
            "state": state,
            "version": chosen["version"] if chosen else None,
            "recipe_status": status,
            "recipe_decision": decision,
            "recipe_sources": found,
            "pending_sources": pending,
            "retained_version": retained["version"] if retained else None,
            "retained_rows": list(retained["rows"]) if retained else [],
            "retained_state": retained_state,
            "recipe_rows": sorted({row for recipe in found for row in recipe["rows"]}),
            "codes": sorted(set(codes))
            or (["BELOW_TARGET"] if below_target else ["PROVEN"]),
            "zones": zone_rows,
        }
        sections.append(section)
        if state == "proven":
            if (
                streaks
                and streaks[-1]["end"] == start
                and streaks[-1]["version"] == section["version"]
            ):
                streaks[-1]["end"] = end
                streaks[-1]["seconds"] += seconds
            else:
                streaks.append(
                    {
                        "start": start,
                        "end": end,
                        "seconds": seconds,
                        "version": section["version"],
                    }
                )
        # A per-version cumulative run: it shows how much was continuously
        # proven under each version, independently of a later dispute.
        if chosen is not None and state == "proven":
            if (
                confirmed
                and confirmed[-1]["version"] == chosen["version"]
                and confirmed[-1]["end"] == start
            ):
                confirmed[-1]["end"] = end
                confirmed[-1]["seconds"] += seconds
            else:
                confirmed.append(
                    {
                        "start": start,
                        "end": end,
                        "seconds": seconds,
                        "version": chosen["version"],
                    }
                )
        if chosen is not None:
            retained = chosen

    attach_context(sections, streaks)
    disputed = [item for item in sections if item["recipe_status"] == UNRESOLVED]
    windows = merge_runs(
        (item["start"], item["end"], item["version"]) for item in disputed
    )
    for window in windows:
        window["time"] = utc(window["start"])
    settling = disputed[0] if disputed else None
    before = (
        next(
            (
                streak
                for streak in confirmed
                if streak["end"] == settling["start"]
                and streak["version"] == settling["retained_version"]
            ),
            None,
        )
        if settling and settling["retained_version"]
        else None
    )
    adjudication = {
        "status": "PENDING" if disputed else "CLEAR",
        "decision": "配方重叠未决，保留上一生效结论"
        if disputed
        else "配方已裁决",
        "windows": windows,
        "pending_s": sum(item["seconds"] for item in disputed),
        "effective": merge_runs(
            (item["start"], item["end"], item["version"])
            for item in sections
            if item["version"] is not None
        ),
        "retained": {
            "version": settling["retained_version"] if settling else None,
            "rows": list(settling["retained_rows"]) if settling else [],
            "state": settling["retained_state"] if settling else None,
            "streak_s": before["seconds"] if before else 0,
            "streak_start": before["start"] if before else None,
            "streak_end": before["end"] if before else None,
            "longest_s": max((run["seconds"] for run in confirmed), default=0),
            "runs": confirmed,
        }
        if settling
        else None,
    }
    longest = max((item["seconds"] for item in streaks), default=0)
    return {
        "sections": sections,
        "streaks": streaks,
        "recipes": versions,
        "isolated": isolated,
        "longest_s": longest,
        "goal_s": plan["goal_s"],
        "goal_met": longest >= plan["goal_s"],
        "unresolved_s": adjudication["pending_s"],
        "unresolved_count": len(disputed),
        "missing_s": sum(
            item["seconds"] for item in sections if item["recipe_status"] == MISSING
        ),
        "adjudication": adjudication,
    }
