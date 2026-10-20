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


def started(card_version: str, change: dict) -> dict:
    """assessment.started: the card version, its risks and profile, and, when the previous version's
    assessment handed over work, which assessment it was and how many risks came along (the content
    holds what they hold)."""
    details = {"card_version": card_version, "risks": change["risks"], "profile_version": change["profile_version"]}
    if "carried_from" in change:
        details |= {"carried_from": change["carried_from"], "carried_risks": change["carried_risks"]}
    return details


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
    choices only, which would miss an objective key by default and unticked. A reset, which leaves no
    choice, compares with what is key after it (`is_key`: the defaults)."""
    before, after = change.get("was_key", change["before"]), change.get("is_key", change["after"])
    return {"added": sorted(o for o, k in after.items() if k and not before.get(o)),
            "removed": sorted(o for o, k in after.items() if not k and before.get(o))}


def card_update(change: dict) -> dict:
    """What an update to a changed card writes: the update itself, then, for each risk whose rating, comment
    or mapping is not what it was, the event a person's change of it would have written, and the key choices
    if they changed. Risk ids follow the card's rows, so an item can now be another risk: its chain says so
    rather than jumping."""
    assessment = change["assessment"]
    blank = {"impact": None, "likelihood": None, "comment": "", "mapped": []}
    changed, mapped = [], []
    for risk in sorted(set(change["before"]) | set(change["after"])):
        b, a = change["before"].get(risk, blank), change["after"].get(risk, blank)
        changed.append({"assessment": assessment, "risk": risk, "before": b, "after": a})
        if b["mapped"] != a["mapped"]:
            mapped.append({"item_type": "risk_mapping", "item_id": risk_item(assessment, risk), "before": b["mapped"],
                           "after": a["mapped"], "details": {"added": sorted(set(a["mapped"]) - set(b["mapped"])),
                                                             "removed": sorted(set(b["mapped"]) - set(a["mapped"]))}})
    keyed = None
    if change["keys_before"] != change["keys_after"]:
        before, after = change["keys_before"], change["keys_after"]
        keyed = {"item_type": "assessment", "item_id": assessment, "before": before, "after": after,
                 "details": keys({"before": before, "after": after})}
    return {
        "updated": {"item_type": "assessment", "item_id": assessment,
                    "details": {"card_version": change["card_version"], "digest_before": change["digest_before"],
                                "digest_after": change["digest_after"], "risks": change["risks"],
                                "carried_risks": change["carried_risks"]},
                    "content": change["carried"]},
        "ratings": ratings(changed), "comments": comments(changed), "mappings": mapped, "keys": keyed,
    }


def profile_save(profile_id: str, change: dict) -> dict:
    """A profile save's event: a new version when the objectives changed (`number` set), otherwise only its
    name or description."""
    if change["number"] is None:
        return {"item_type": "objective_profile", "item_id": profile_id, "before": change["before"],
                "after": change["after"]}
    return {"item_type": "objective_profile", "item_id": profile_id, "item_version": str(change["number"]),
            "details": {"version": change["number"], "dropped": change["dropped"]},
            "content": {"picks": change["picks"], **change["after"]}, "before": change["before"], "after": change["after"]}


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
        # What was saved, also when some risks failed: the ledger and the matrix say the same. A failed
        # risk keeps the rows it had (outcome["kept"]), so those are what it records for it.
        # {"risks": ...}: never empty, so a card with no mapped risk is still content
        details = {"attempts": run.attempts}
        if outcome.get("error"):
            details |= {"error": "mapping_error", "failed_risks": failed}
        kept = outcome.get("kept") or {}

        def saved(rid, mapping):
            return kept.get(rid, mapping) if mapping.stop == "failed" else mapping

        ledger.emit(session, "ai.mapping.completed", item_type="assessment", item_id=project_id, run_id=run_id,
                    model=model, details=details,
                    content={"risks": {rid: [o.objective_id for o in saved(rid, m).objectives]
                                       for rid, m in run.mappings.items()}})
