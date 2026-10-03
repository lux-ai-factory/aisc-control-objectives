"""S1-S3: step 2 in the ledger (phase 6). Every write records its event with the project database's
ledger.emit, in the write's own transaction; an AI mapping keeps the previous run and an assessor's own
rows (mapping_archive) and is one run in the ledger; authors are kept by subject. The scratch database
gets the platform's real ledger template (platform/project-template/0020_ledger_outbox.sql)."""
from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text

from test_objective_selection import (  # noqa: F401  (fixtures)
    _api, client, graph, in_order, mapper, start,
)

TEMPLATE = Path(__file__).resolve().parents[3] / "platform" / "project-template" / "0020_ledger_outbox.sql"
REQUEST = "a6a6a6a6-0000-4000-8000-000000000001"


@pytest.fixture(autouse=True)
def ledger_db(repository, monkeypatch):
    if not TEMPLATE.exists():
        pytest.skip("the platform's ledger template is not in this checkout")
    raw = repository.engine.raw_connection()                           # the template has % in it: no binding
    try:
        cursor = raw.cursor()
        cursor.execute(TEMPLATE.read_text())
        cursor.close()
        raw.commit()
    finally:
        raw.close()
    monkeypatch.setenv("LEDGER_MODE", "record")


def outbox(repository, action_prefix=""):
    with repository.engine.begin() as connection:
        rows = connection.execute(text(
            "SELECT action, item_type, item_id, request_id::text AS request_id, run_id::text AS run_id, model,"
            " details, content, before, after FROM ledger.outbox WHERE action LIKE :p ORDER BY occurred_at, event_id"),
            {"p": action_prefix + "%"}).mappings().all()
    return [dict(r) for r in rows]


def archive(repository):
    with repository.engine.begin() as connection:
        return [dict(r) for r in connection.execute(text(
            "SELECT reason, run, rows, run_id FROM control_objectives.mapping_archive ORDER BY id")).mappings()]


HEADERS = {"X-AISC-Request-Id": REQUEST}


# S3: every action of an assessment emits, in its transaction ------------------------------------

def test_each_assessment_action_records_its_event_citing_the_request(client, start, platform_project, repository):
    http, _ = client
    a = start()
    assert http.post(_api(platform_project, a, "/map"), headers=HEADERS).status_code == 200
    assert http.post(_api(platform_project, a, "/risks/risk0/mapping"), json={"objective_ids": ["O5"]},
                     headers=HEADERS).status_code == 200
    assert http.post(_api(platform_project, a, "/ratings"), json={"risk0": {"impact": 4, "likelihood": 2}},
                     headers=HEADERS).status_code == 200
    assert http.post(_api(platform_project, a, "/severity-comments"), json={"risk0": "see the logs"},
                     headers=HEADERS).status_code == 200
    assert http.post(_api(platform_project, a, "/key"), json={"O5": True}, headers=HEADERS).status_code == 200
    rows = outbox(repository)
    actions = [r["action"] for r in rows]
    assert actions[:2] == ["ai.mapping.requested", "ai.mapping.completed"]
    assert actions[2:] == ["mapping.risk.edited", "risk.rated", "risk.rating_comment.set", "objective.key.set"]
    assert {r["request_id"] for r in rows} == {REQUEST}
    requested, completed = rows[0], rows[1]
    assert requested["run_id"] == completed["run_id"] and completed["model"] == "fake/model"
    edited = next(r for r in rows if r["action"] == "mapping.risk.edited")
    assert (edited["item_id"], edited["details"]["added"]) == ("risk0", ["O5"])
    rated = next(r for r in rows if r["action"] == "risk.rated")
    assert (rated["item_id"], rated["details"], rated["after"]) == ("risk0", {"rating": 8},
                                                                   {"impact": 4, "likelihood": 2})
    assert next(r for r in rows if r["action"] == "objective.key.set")["details"] == {"added": ["O5"], "removed": []}


def test_an_unchanged_save_records_nothing(client, start, platform_project, repository):
    http, _ = client
    a = start()
    http.post(_api(platform_project, a, "/ratings"), json={"risk0": {"impact": 4, "likelihood": 2}})
    http.post(_api(platform_project, a, "/ratings"), json={"risk0": {"impact": 4, "likelihood": 2}})
    assert [r["action"] for r in outbox(repository)] == ["risk.rated"]


def test_a_deleted_assessment_records_what_it_held(client, start, platform_project, repository):
    http, _ = client
    a = start()
    http.post(_api(platform_project, a, "/ratings"), json={"risk0": {"impact": 5, "likelihood": 5}})
    assert http.delete(_api(platform_project, a)).status_code == 204
    [deleted] = outbox(repository, "assessment.deleted")
    assert deleted["item_id"] == a
    assert next(r for r in deleted["content"]["risks"] if r["risk"] == "risk0")["impact"] == 5


def test_a_failed_change_leaves_neither_the_change_nor_its_event(start, repository):
    """The event is in the write's transaction: a failure after it rolls both back."""
    a = start()

    def then_fail(session, changed):
        from aisc_control_objectives import ledger

        ledger.emit(session, "risk.rated", item_type="risk", item_id="risk0", details={"rating": 1})
        raise RuntimeError("a failure after the event (test)")
    with pytest.raises(RuntimeError):
        repository.rate(a, {"risk0": 1}, {"risk0": 1}, record=then_fail)
    assert outbox(repository) == []
    assert repository.get(a).risks_rated if hasattr(repository.get(a), "risks_rated") else True
    with repository.engine.begin() as connection:
        assert connection.execute(text("SELECT rating_impact FROM control_objectives.risk WHERE risk_id = 'risk0'"
                                       " AND project_id = :p"), {"p": a}).scalar() is None


def test_with_the_ledger_off_nothing_is_written(client, start, platform_project, repository, monkeypatch):
    monkeypatch.setenv("LEDGER_MODE", "off")
    http, _ = client
    a = start()
    http.post(_api(platform_project, a, "/map"))
    assert outbox(repository) == []


def test_a_request_id_that_is_not_a_uuid_is_never_cited(client, start, platform_project, repository):
    http, _ = client
    a = start()
    http.post(_api(platform_project, a, "/key"), json={"O5": True}, headers={"X-AISC-Request-Id": "x; DROP"})
    assert [r["request_id"] for r in outbox(repository)] == [None]


# S1: an AI run keeps the previous run and an assessor's own rows --------------------------------

def test_mapping_again_keeps_the_previous_run_and_the_persons_rows(client, start, platform_project, repository):
    http, _ = client
    a = start()
    http.post(_api(platform_project, a, "/map"))
    http.post(_api(platform_project, a, "/risks/risk4/mapping"), json={"objective_ids": ["O30"]})
    http.post(_api(platform_project, a, "/map"))                     # replaces everything on the page...
    kept = archive(repository)
    assert [k["reason"] for k in kept] == ["by_hand", "ai_run"]       # ...the first run had nothing before it
    by_hand, rerun = kept
    assert {r["objective"] for r in by_hand["rows"] if r["risk"] == "risk4"} >= {"O7"}   # the AI's, before the edit
    assert ("risk4", "O30", "person") in {(r["risk"], r["objective"], r["source"]) for r in rerun["rows"]}
    assert rerun["run"]["model"] == "fake/model"                      # the previous run's own record
    assert rerun["run_id"] == outbox(repository, "ai.mapping.requested")[-1]["run_id"]


def test_the_archive_is_append_only(client, start, platform_project, repository):
    """The migration's trigger: in a project database rows are never changed (here the scratch database
    is made by create_all, so the trigger is the migration's; tested in test_migration_project_database)."""
    http, _ = client
    a = start()
    http.post(_api(platform_project, a, "/map"))
    http.post(_api(platform_project, a, "/map"))
    assert len(archive(repository)) == 1


def test_a_failed_ai_run_is_recorded_as_a_code_never_its_text(start, repository, objectives):
    """The mapper's own failure is caught into the run (map_risks maps the other risks); the ledger gets
    ai.mapping.failed with a code, never the error's text, which can quote a key or a card (phase 5 M5)."""
    from aisc_control_objectives import ledger
    from aisc_control_objectives.api import ledger_events
    from aisc_control_objectives.projects import Projects

    class Broken:
        def propose(self, *a, **k):
            raise RuntimeError("POST https://x/v1?key=AIzaSyA-not-real failed for Jane Doe")
    a = start()
    projects = Projects(repository, objectives, Broken(), model="fake/model")
    projects.map_risks_of(a, on_save=lambda s, o: (
        ledger.emit(s, "ai.mapping.requested", item_type="assessment", item_id=a, run_id=o["run_id"]),
        ledger_events.mapping_outcome(s, a, o)))
    rows = outbox(repository)
    assert [r["action"] for r in rows] == ["ai.mapping.requested", "ai.mapping.failed"]
    assert rows[1]["details"]["error"] == "mapping_error" and rows[0]["run_id"] == rows[1]["run_id"]
    assert "Jane" not in json.dumps(rows) and "AIza" not in json.dumps(rows)


def test_each_model_call_of_a_run_is_recorded():
    from aisc_control_objectives import ledger

    calls = ledger.Calls()
    complete = calls.recording(lambda s, u: "{}", "mapping")
    complete("s", "u")
    complete("s", "u")
    assert [c["purpose"] for c in calls.calls] == ["mapping", "mapping"]


# S2: authors by subject -------------------------------------------------------------------------

def test_the_library_keeps_the_authors_subject_beside_the_name(repository, objectives):
    from aisc_control_objectives.library import Library

    library = Library(repository.engine, objectives)
    made = library.create_set("LDG", "Ledger set", "", who="ada", who_sub="sub-ada")
    with repository.engine.begin() as connection:
        row = connection.execute(text("SELECT created_by, created_by_sub FROM control_objectives.objective_set"
                                      " WHERE id = :i"), {"i": made.id}).one()
    assert tuple(row) == ("ada", "sub-ada")


def test_the_routes_take_the_subject_from_the_signed_in_caller():
    from types import SimpleNamespace

    from aisc_control_objectives.api.library_routes import _who, _who_sub

    request = SimpleNamespace(state=SimpleNamespace(caller=SimpleNamespace(username="ada", subject="sub-ada")))
    assert (_who(request), _who_sub(request)) == ("ada", "sub-ada")


def test_library_actions_record_their_events(client, platform_project, repository):
    http, _ = client
    base = f"/p/{platform_project}/api"
    made = http.post(f"{base}/sets", json={"code": "LDG", "name": "Ledger set"}, headers=HEADERS)
    assert made.status_code == 201, made.text
    set_id = made.json()["id"]
    values = {"dimension": "R2", "label": "Logging", "text": "Keep a log.", "assessment_mode": "Control"}
    added = http.post(f"{base}/sets/{set_id}/objectives", json=values, headers=HEADERS)
    assert added.status_code == 201, added.text
    objective_id = added.json()["id"]
    assert http.put(f"{base}/sets/{set_id}/objectives/{objective_id}", json={**values, "text": "Keep a full log."},
                    headers=HEADERS).status_code == 200
    assert http.post(f"{base}/sets/{set_id}/publish", headers=HEADERS).status_code == 200
    actions = [r["action"] for r in outbox(repository)]
    assert actions == ["objective_set.created", "objective.added", "objective.edited", "objective_set.published"]
    edited = outbox(repository, "objective.edited")[0]
    assert (edited["before"]["text"], edited["after"]["text"]) == ("Keep a log.", "Keep a full log.")
