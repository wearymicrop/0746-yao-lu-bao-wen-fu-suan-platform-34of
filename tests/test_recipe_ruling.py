import json

import pytest

from app.errors import BusinessError
from app.modules.m7_archive.signoff import approval
from app.pipeline import calculate


def trial_row(version, start, end, target=1140000, **extra):
    return dict(
        {
            "version": version,
            "start": start,
            "end": end,
            "target_mc": {"北墙": target, "窑心": target, "南墙": target},
        },
        **extra,
    )


def shift(clean, minutes):
    stamp = clean["plan"]["start"]
    return f"2026-11-20T{9 + (minutes // 60):02d}:{minutes % 60:02d}:00+08:00"


def test_late_trial_does_not_overwrite_effective_stretch(clean):
    # The formal version already governs 09:00-09:20; a trial signature
    # arriving later and covering its final 4 minutes cannot silently win.
    clean["recipes"].append(
        trial_row("R17-试签", shift(clean, 16), shift(clean, 20))
    )
    soak = calculate(clean)["soak"]
    disputed = [item for item in soak["sections"] if item["recipe_status"] == "UNRESOLVED"]
    assert [item["seconds"] for item in disputed] == [120, 120]
    assert all(item["version"] is None for item in disputed)
    assert all(
        [source["version"] for source in item["pending_sources"]]
        == ["R17", "R17-试签"]
        for item in disputed
    )
    # The stretch that stood before the dispute is named, not erased.
    assert disputed[0]["retained_version"] == "R17"
    assert soaked_before(soak) == 960


def test_retained_duration_is_a_continuous_run(clean):
    clean["recipes"].append(
        trial_row("R17-试签", shift(clean, 16), shift(clean, 20))
    )
    soak = calculate(clean)["soak"]
    assert soak["adjudication"]["retained"]["version"] == "R17"
    assert soak["adjudication"]["retained"]["streak_s"] == 960
    assert soak["adjudication"]["retained"]["state"] == "proven"
    # 09:00-09:16 proven under R17, then the dispute; the 960 second run is
    # still identified as the conclusion that stands, and R18's own run is
    # listed separately rather than being added on.
    assert [(run["version"], run["seconds"]) for run in soak["adjudication"]["retained"]["runs"]] == [
        ("R17", 960),
        ("R18", 1320),
    ]


def soaked_before(soak):
    return soak["adjudication"]["retained"]["streak_s"]


def test_two_formal_versions_stay_unresolved(clean):
    clean["recipes"].append(trial_row("R17-B", shift(clean, 10), shift(clean, 20)))
    soak = calculate(clean)["soak"]
    disputed = [item for item in soak["sections"] if item["recipe_status"] == "UNRESOLVED"]
    assert len(disputed) == 5 and sum(item["seconds"] for item in disputed) == 600
    assert soak["adjudication"]["status"] == "PENDING"
    assert all(item["retained_version"] == "R17" for item in disputed)


def test_retry_of_same_recipe_counts_once(clean):
    retry = dict(clean["recipes"][0])
    clean["recipes"].append(retry)
    soak = calculate(clean)["soak"]
    assert soak["unresolved_s"] == 0 and soak["adjudication"]["status"] == "CLEAR"
    # The retry is one window with two evidence rows, so no time is split.
    first = next(item for item in soak["recipes"] if item["version"] == "R17")
    assert first["rows"] == [1, 3]
    assert [(row["version"], row["seconds"]) for row in soak["streaks"]] == [
        ("R17", 1200),
        ("R18", 1320),
    ]


def test_retry_with_different_targets_is_not_the_same_recipe(clean):
    changed = dict(clean["recipes"][0], target_mc=dict(clean["recipes"][0]["target_mc"]))
    changed["target_mc"]["北墙"] = 1149000
    clean["recipes"].append(changed)
    soak = calculate(clean)["soak"]
    assert soak["adjudication"]["status"] == "PENDING"
    assert soak["adjudication"]["pending_s"] == 1200


def test_trial_alone_is_effective_but_marked(clean):
    clean["recipes"][1]["version"] = "R18-试签"
    soak = calculate(clean)["soak"]
    section = next(item for item in soak["sections"] if item["version"] == "R18-试签")
    assert section["recipe_status"] == "TRIAL"
    assert soak["adjudication"]["status"] == "CLEAR"


def test_pending_overlap_blocks_release_but_keeps_old_result(clean):
    clean["recipes"].append(trial_row("R17-试签", shift(clean, 16), shift(clean, 20)))
    result = calculate(clean)
    assert result["reconcile"]["status"] == "MATCHED"
    assert result["reconcile"]["adjudication"] == "PENDING"
    signed = approval(result)
    assert signed["status"] == "REVIEW"
    assert signed["decision"] == "配方重叠待裁决，保留上一生效结论"
    assert signed["retained_version"] == "R17" and signed["retained_s"] == 960


def test_clean_batch_adjudication_is_clear(clean_result):
    assert clean_result["soak"]["adjudication"]["status"] == "CLEAR"
    assert clean_result["soak"]["adjudication"]["pending_s"] == 0
    assert [run["version"] for run in clean_result["soak"]["adjudication"]["effective"]] == [
        "R17",
        "R18",
    ]


def test_dirty_trial_row_is_kept_as_pending_evidence(dirty_result):
    soak = dirty_result["soak"]
    assert soak["adjudication"]["status"] == "PENDING"
    assert soak["adjudication"]["pending_s"] == 240
    assert soak["adjudication"]["decision"] == "配方重叠未决，保留上一生效结论"
    gap = next(row for row in dirty_result["gaps"]["gaps"] if row["codes"] == ["RECIPE_OVERLAP"])
    assert [source["version"] for source in gap["pending_sources"]] == [
        "R17",
        "R18-试签",
    ]
    assert gap["retained_version"] == "R17" and gap["decision"] == "配方待核"


def test_gap_keeps_pending_and_retained_after_grouping(dirty_result):
    gap = dirty_result["gaps"]["gaps"][1]
    assert gap["recipe_rows"] == [1, 3]
    assert gap["retained_state"] == "proven"
    assert gap["retained_rows"] == [1]
    json.dumps(gap)  # the page payload stays serialisable for the bundle digest


def test_rerun_keeps_archived_record_when_source_changes(client):
    from app.manifest import digest

    first = client.post(
        "/api/runs", json={"source": "dirty", "operator": "值班员"}
    ).json()
    # A conflicting write to the same identifier must not replace the bundle.
    with pytest.raises(BusinessError) as failure:
        client.app.state.ledger.put_run(dict(first, approval={"status": "edited"}))
    assert failure.value.code == "RUN_CONFLICT"
    stored = client.get(f"/api/runs/{first['id']}").json()
    assert stored["approval"] == first["approval"]
    assert stored["manifest"]["approval_sha256"] == digest(first["approval"])


def test_rerun_failure_preserves_old_bundle(client, dirty):
    source = client.post("/api/sources", json=dirty).json()["source"]
    first = client.post(
        "/api/runs", json={"source": source, "operator": "值班员"}
    ).json()
    output = client.post(f"/api/runs/{first['id']}/rerun")
    assert output.status_code == 200 and output.json()["reused"] is True
    # The live source disappears after sign-off: the archive stays readable
    # and the failed recovery says so instead of returning a fresh decision.
    with client.app.state.ledger.connect() as conn:
        conn.execute("DELETE FROM sources")
    missing = client.post(f"/api/runs/{first['id']}/rerun")
    assert missing.status_code == 409 and missing.json()["code"] == "RERUN_UNAVAILABLE"
    stored = client.get(f"/api/runs/{first['id']}").json()
    assert stored["id"] == first["id"] and stored["approval"] == first["approval"]


def test_recovery_does_not_drop_archived_evidence(tmp_path):
    from fastapi.testclient import TestClient

    from app.main import create_app

    path = tmp_path / "state.sqlite3"
    with TestClient(create_app(path)) as client:
        record = client.post(
            "/api/runs", json={"source": "dirty", "operator": "值班员"}
        ).json()
    with TestClient(create_app(path)) as client:
        stored = client.get(f"/api/runs/{record['id']}").json()
        assert stored["id"] == record["id"]
        assert stored["manifest"] == record["manifest"]
        assert stored["result"]["soak"]["adjudication"] == record["result"]["soak"]["adjudication"]
        assert client.post(f"/api/runs/{record['id']}/rerun").json()["reused"] is True
