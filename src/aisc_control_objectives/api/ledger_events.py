"""What step 2's ledger events say, worked out from what a write changed.

The handlers call `ledger.emit` themselves, in the write's transaction (api/app.py, library_routes.py);
these helpers only shape the details, and write the AI run's own events (`mapping_outcome`).
"""
from __future__ import annotations

from aisc_control_objectives import ledger


def risk_item(assessment: str, risk: str) -> str:
    """A risk's item in the ledger: risk ids repeat across a project's card versions, so the assessment's id
    goes first. The rating, the comment and the mapping are three items of their own."""
    return f"{assessment}/risks/{risk}"


def objective_item(set_id: str, objective_id: str) -> str:
    """An objective's item: a set made again with a deleted one's code numbers its objectives again, so the
    set's id goes first."""
    return f"{set_id}/objectives/{objective_id}"


def ratings(changed: list[dict]) -> list[dict]:
    """risk.rated, one per risk whose impact or likelihood changed."""
    out = []
    for c in changed:
        b, a = c["before"], c["after"]
        if (b["impact"], b["likelihood"]) == (a["impact"], a["likelihood"]):
            continue
        score = a["impact"] * a["likelihood"] if a["impact"] and a["likelihood"] else None
        out.append({"item_type": "risk_rating", "item_id": risk_item(c["assessment"], c["risk"]),
                    "details": {"rating": score},
                    "before": {"impact": b["impact"], "likelihood": b["likelihood"]},
                    "after": {"impact": a["impact"], "likelihood": a["likelihood"]}})
    return out


def comments(changed: list[dict]) -> list[dict]:
    """risk.rating_comment.set, one per risk whose comment changed (an empty one: cleared)."""
    return [{"item_type": "risk_comment", "item_id": risk_item(c["assessment"], c["risk"]),
             "content": {"comment": c["after"]["comment"] or ""},
             "before": {"comment": c["before"]["comment"]}, "after": {"comment": c["after"]["comment"]}}
            for c in changed if c["before"]["comment"] != c["after"]["comment"]]


def keys(change: dict) -> dict:
    """objective.key.set: which objectives became key, which stopped being key. Compared with what was
    key before the save (`was_key`: the assessor's choice, else the default), not with the explicit
    choices only, which would miss an objective key by default and unticked."""
    before, after = change.get("was_key", change["before"]), change["after"]
    return {"added": sorted(o for o, k in after.items() if k and not before.get(o)),
            "removed": sorted(o for o, k in after.items() if not k and before.get(o))}


#: Why a run failed, as the ledger says it: a code from this list, never the error's text.
FAILURES = ("mapping_error", "model_unreachable", "assessment_changed")


def mapping_outcome(session, project_id: str, outcome: dict) -> None:
    """The run's own events after ai.mapping.requested: each model call, then completed or failed."""
    run_id, model = outcome["run_id"], outcome.get("model") or None
    outcome["calls"].emit_all(session, run_id, model)
    run = outcome.get("run")
    # the risks whose model calls failed; the others were mapped and saved
    failed = sorted(rid for rid, m in run.mappings.items() if m.stop == "failed") if run is not None else []
    if run is None or (outcome.get("error") and len(failed) == len(run.mappings)):
        # a code, never the error's text: it can quote a key in a URL or a card's personal data, and
        # immudb keeps what it is given for ever
        code = outcome.get("error") if run is None and outcome.get("error") in FAILURES else "mapping_error"
        details = {"error": code, "attempts": run.attempts if run is not None else 0}
        if failed:
            details["failed_risks"] = failed
        ledger.emit(session, "ai.mapping.failed", item_type="assessment", item_id=project_id, run_id=run_id,
                    model=model, details=details)
    else:
        # What was saved, also when some risks failed: the ledger and the matrix say the same.
        # {"risks": ...}: never empty, so a card with no mapped risk is still content
        details = {"attempts": run.attempts}
        if outcome.get("error"):
            details |= {"error": "mapping_error", "failed_risks": failed}
        ledger.emit(session, "ai.mapping.completed", item_type="assessment", item_id=project_id, run_id=run_id,
                    model=model, details=details,
                    content={"risks": {rid: [o.objective_id for o in m.objectives] for rid, m in run.mappings.items()}})
