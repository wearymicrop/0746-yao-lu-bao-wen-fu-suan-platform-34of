from app.errors import BusinessError


def approval(result):
    balance = result["reconcile"]
    if balance["status"] != "MATCHED":
        raise BusinessError("UNBALANCED", "炉批尚未对平", 409)
    adjudication = result["soak"]["adjudication"]
    accepted = (
        balance["goal_met"]
        and balance["unproven_s"] == 0
        and adjudication["status"] == "CLEAR"
    )
    if adjudication["status"] != "CLEAR":
        decision = "配方重叠待裁决，保留上一生效结论"
    elif accepted:
        decision = "轨道证据齐全"
    else:
        decision = "转工艺复核"
    retained = adjudication.get("retained") or {}
    return {
        "status": "RELEASED" if accepted else "REVIEW",
        "batch": result["intake"]["plan"]["batch"],
        "decision": decision,
        "adjudication": adjudication["status"],
        "pending_s": adjudication["pending_s"],
        "effective_versions": [
            run["version"] for run in adjudication["effective"] if run["version"]
        ],
        "retained_version": retained.get("version"),
        "retained_s": retained.get("streak_s", 0),
    }
