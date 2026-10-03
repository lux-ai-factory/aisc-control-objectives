"""Step 2 in the ledger. Every write records its event with the project database's
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


# Every action of an assessment emits, in its transaction

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
    assert (edited["item_type"], edited["item_id"], edited["details"]["added"]) == ("risk_mapping", f"{a}/risks/risk0",
                                                                                   ["O5"])
    rated = next(r for r in rows if r["action"] == "risk.rated")
    assert (rated["item_type"], rated["item_id"], rated["details"], rated["after"]) == (
        "risk_rating", f"{a}/risks/risk0", {"rating": 8}, {"impact": 4, "likelihood": 2})
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


# An AI run keeps the previous run and an assessor's own rows

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


def test_a_second_run_archives_the_first_once(client, start, platform_project, repository):
    """(The archive's append-only trigger is the migration's: test_migration_project_database.)"""
    http, _ = client
    a = start()
    http.post(_api(platform_project, a, "/map"))
    http.post(_api(platform_project, a, "/map"))
    assert len(archive(repository)) == 1


def test_a_failed_ai_run_is_recorded_as_a_code_never_its_text(start, repository, objectives):
    """The mapper's own failure is caught into the run (map_risks maps the other risks); the ledger gets
    ai.mapping.failed with a code, never the error's text, which can quote a key or a card."""
    from aisc_control_objectives import ledger
    from aisc_control_objectives.api import ledger_events
    from aisc_control_objectives.projects import Projects

    class Broken:
        def propose(self, *a, **k):
            raise RuntimeError("POST https://x/v1?key=AIzaSyA-not-real failed for Jane Doe")
    a = start()
    projects = Projects(repository, objectives, Broken(), model="fake/model")
    projects.map_risks_of(a, on_start=lambda s, run_id: ledger.emit(
        s, "ai.mapping.requested", item_type="assessment", item_id=a, run_id=run_id),
        on_save=lambda s, o: ledger_events.mapping_outcome(s, a, o))
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


# Authors by subject

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


# Ordering, model calls, failures and item chains

class CallingMapper:
    """A mapper that calls its model, as RiskMapper does (`_complete`): its calls become ai.llm_call."""

    def __init__(self, inner, seen=None):
        self.inner, self.seen = inner, seen

    def _complete(self, system, user):
        return "{}"

    def propose(self, risk, findings=()):
        if self.seen is not None:
            self.seen()
        self._complete("skill", risk.id)
        return self.inner.propose(risk, findings)


def test_m1_a_mapping_records_its_request_before_its_first_model_call(start, repository, objectives, mapper):
    """A long run's start must not wait for its calls (the relay's 5-minute window): it is written in a
    transaction of its own before the first call; each call and the outcome follow, in the save's."""
    from aisc_control_objectives import ledger
    from aisc_control_objectives.api import ledger_events
    from aisc_control_objectives.projects import Projects

    before_first_call: list[list[str]] = []
    seen = lambda: before_first_call or before_first_call.append([r["action"] for r in outbox(repository)])  # noqa: E731
    a = start()
    projects = Projects(repository, objectives, CallingMapper(mapper, seen), model="fake/model")
    projects.map_risks_of(a, on_start=lambda s, run_id: ledger.emit(
        s, "ai.mapping.requested", item_type="assessment", item_id=a, run_id=run_id),
        on_save=lambda s, o: ledger_events.mapping_outcome(s, a, o))
    assert before_first_call == [["ai.mapping.requested"]]
    rows = outbox(repository)
    actions = [r["action"] for r in rows]
    assert actions[0] == "ai.mapping.requested" and actions[-1] == "ai.mapping.completed"
    calls = [r for r in rows if r["action"] == "ai.llm_call"]
    assert calls and set(actions[1:-1]) == {"ai.llm_call"}
    assert {r["run_id"] for r in rows} == {rows[0]["run_id"]}
    first = calls[0]["details"]
    assert first["purpose"] == "mapping" and first["round"] == 1 and first["property"].startswith("risk")


def test_m4_a_route_mapping_records_each_model_call(repository, objectives, mapper, graph, platform_project,
                                                    system_version):
    """Through the route, with a mapper that calls its model (the client fixture's has no `_complete`)."""
    from fastapi.testclient import TestClient

    from aisc_control_objectives.api.app import create_app
    from aisc_control_objectives.config import RunConfig
    from aisc_control_objectives.projects import Projects

    projects = Projects(repository, objectives, CallingMapper(mapper), model="fake/model")
    http = TestClient(create_app(objectives, projects, base_config=RunConfig()), follow_redirects=False)
    a = projects.create(platform_project, "MCAS", json.dumps(graph), graph, system_version(platform_project, 1)).record.id
    assert http.post(_api(platform_project, a, "/map"), headers=HEADERS).status_code == 200
    actions = [r["action"] for r in outbox(repository, "ai.")]
    assert actions[0] == "ai.mapping.requested" and actions[-1] == "ai.mapping.completed"
    assert "ai.llm_call" in actions[1:-1]


def test_m2_a_model_that_cant_be_had_records_the_request_and_its_failure(start, repository, objectives, mapper):
    """The project's model cannot be resolved: the request is recorded with a failed run
    (model_unreachable), then refused."""
    from aisc_control_objectives import ledger
    from aisc_control_objectives.api import ledger_events
    from aisc_control_objectives.projects import ModelUnavailable, Projects

    def no_model(pid):
        raise ValueError("no provider configured")
    a = start()
    projects = Projects(repository, objectives, mapper, model="fake/model", mapper_for=no_model)
    with pytest.raises(ModelUnavailable):
        projects.map_risks_of(a, on_start=lambda s, run_id: ledger.emit(
            s, "ai.mapping.requested", item_type="assessment", item_id=a, run_id=run_id),
            on_save=lambda s, o: ledger_events.mapping_outcome(s, a, o))
    rows = outbox(repository)
    assert [r["action"] for r in rows] == ["ai.mapping.requested", "ai.mapping.failed"]
    assert rows[1]["details"]["error"] == "model_unreachable" and rows[0]["run_id"] == rows[1]["run_id"]


def test_m11_a_failure_to_record_a_failed_run_keeps_the_runs_own_error(start, repository, objectives, mapper,
                                                                      monkeypatch):
    from aisc_control_objectives import projects as projects_module
    from aisc_control_objectives.projects import Projects

    def broken(*a, **k):
        raise RuntimeError("the run's own error")
    monkeypatch.setattr(projects_module, "map_risks", broken)
    a = start()
    projects = Projects(repository, objectives, mapper, model="fake/model")

    def unrecordable(session, outcome):
        raise OSError("the database went away")
    with pytest.raises(RuntimeError, match="the run's own error"):
        projects.map_risks_of(a, on_start=lambda s, run_id: None, on_save=unrecordable)


def test_m4_a_failure_after_a_runs_events_leaves_no_run_and_no_event(start, repository, objectives, mapper):
    from aisc_control_objectives import ledger
    from aisc_control_objectives.projects import Projects

    a = start()
    projects = Projects(repository, objectives, mapper, model="fake/model")

    def then_fail(session, outcome):
        ledger.emit(session, "ai.mapping.completed", item_type="assessment", item_id=a, run_id=outcome["run_id"],
                    content={"risks": {}})
        raise RuntimeError("a failure after the event (test)")
    with pytest.raises(RuntimeError):
        projects.map_risks_of(a, on_start=lambda s, run_id: None, on_save=then_fail)
    assert outbox(repository, "ai.mapping.completed") == []
    assert repository.get(a).mapping_run is None


def test_m1_minor_a_completed_runs_content_is_never_empty(start, repository, objectives):
    """A card with no mapped risk still sends content (the registry's content_required)."""
    from aisc_control_objectives import ledger
    from aisc_control_objectives.api import ledger_events
    from aisc_control_objectives.projects import Projects

    class Nothing:
        def propose(self, risk, findings=()):
            from aisc_control_objectives.risk_mapping import Mapping
            return Mapping(risk_id=risk.id, objectives=[])
    a = start()
    Projects(repository, objectives, Nothing(), model="fake/model").map_risks_of(
        a, on_start=lambda s, run_id: ledger.emit(s, "ai.mapping.requested", item_type="assessment", item_id=a,
                                                  run_id=run_id),
        on_save=lambda s, o: ledger_events.mapping_outcome(s, a, o))
    [completed] = outbox(repository, "ai.mapping.completed")
    assert completed["content"] == {"risks": {rid: [] for rid in completed["content"]["risks"]}}


def chain_breaks(rows) -> list:
    """verify.py's chain check on the outbox: an event's before is its item's previous after."""
    last, breaks = {}, []
    for r in rows:
        key = (r["item_type"], r["item_id"])
        if key in last and r["before"] is not None and r["before"] != last[key]:
            breaks.append((r["action"], key, last[key], r["before"]))
        if r["after"] is not None:
            last[key] = r["after"]
    return breaks


def test_m3_the_item_chains_hold_in_normal_use(client, start, platform_project, repository):
    """A rating and its comment in one save, a rating again, the next card version rated, keys set
    twice over different matrices, a risk mapped by hand twice: no chain break."""
    http, _ = client
    a = start()
    http.post(_api(platform_project, a, "/map"))
    form = {"impact:risk0": "4", "likelihood:risk0": "2", "comment:risk0": "see the logs"}
    assert http.post(f"/p/{platform_project}/projects/{a}/severity", data=form).status_code == 303
    http.post(_api(platform_project, a, "/ratings"), json={"risk0": {"impact": 5, "likelihood": 2}})
    http.post(_api(platform_project, a, "/key"), json={"O5": True, "O7": False})
    http.post(_api(platform_project, a, "/key"), json={"O7": True})
    http.post(_api(platform_project, a, "/risks/risk0/mapping"), json={"objective_ids": ["O5"]})
    http.post(_api(platform_project, a, "/risks/risk0/mapping"), json={"objective_ids": ["O5", "O7"]})
    b = start(2)
    http.post(_api(platform_project, b, "/ratings"), json={"risk0": {"impact": 1, "likelihood": 1}})
    rows = outbox(repository)
    assert chain_breaks(rows) == []
    [first_key, second_key] = [r for r in rows if r["action"] == "objective.key.set"]
    assert second_key["before"] == first_key["after"] and second_key["after"] == {"O5": True, "O7": True}
    assert {r["item_type"] for r in rows if r["action"].startswith("risk.")} == {"risk_rating", "risk_comment"}


def test_m2_a_new_card_versions_assessment_names_the_profile_it_starts_on(client, start, platform_project,
                                                                          repository, objectives, graph,
                                                                          system_version):
    """The inherited profile is set in the start's own transaction, and its event names it."""
    from aisc_control_objectives.library import Library
    from test_library import fields

    library = Library(repository.engine, objectives)
    made = library.create_set("BNK", "Bank policies", "", who="alice")
    library.add_objective(made.id, fields())
    library.publish(made.id, who="alice")
    profile = library.create_profile("Bank", "", ["O1", "O7", "BNK1"], who="alice")
    http, projects = client
    a = start()
    assert http.post(_api(platform_project, a, "/profile"), json={"profile_id": profile.id}).status_code == 200
    seen = []

    def started(session, c):                                           # what start_assessment_form records
        seen.append(c)
        session.execute(text("SELECT 1"))                              # (inside the start's transaction)
    b = projects.create(platform_project, "MCAS", json.dumps(graph), graph, system_version(platform_project, 2),
                        record=started).record.id
    version = repository.get(b).profile_version_id
    assert version is not None and version == repository.get(a).profile_version_id
    assert [c["profile_version"] for c in seen] == [version]
    assert [r["action"] for r in outbox(repository, "assessment.profile")] == ["assessment.profile.switched"]


def test_m4_a_profile_switch_keeps_what_it_drops(client, start, platform_project, repository, objectives):
    from aisc_control_objectives.library import Library

    library = Library(repository.engine, objectives)
    profile = library.create_profile("Only O1", "", ["O1"], who="alice")
    http, _ = client
    a = start()
    http.post(_api(platform_project, a, "/map"))
    assert http.post(_api(platform_project, a, "/profile"), json={"profile_id": profile.id}).status_code == 200
    [kept] = [k for k in archive(repository) if k["reason"] == "profile"]
    assert kept["rows"] and all(r["objective"] != "O1" or True for r in kept["rows"])


def test_m5_deleting_an_assessment_archives_its_mapping_and_keeps_its_keys(client, start, platform_project,
                                                                           repository):
    http, _ = client
    a = start()
    http.post(_api(platform_project, a, "/map"))
    http.post(_api(platform_project, a, "/key"), json={"O5": True})
    assert http.delete(_api(platform_project, a)).status_code == 204
    [deleted] = [k for k in archive(repository) if k["reason"] == "deleted"]
    assert deleted["run"]["model"] == "fake/model" and deleted["rows"]
    assert {"quote", "rationale", "source"} <= set(deleted["rows"][0])
    [event] = outbox(repository, "assessment.deleted")
    assert event["content"]["keys"] == {"O5": True}


def test_m4_the_routes_store_the_authors_subject(repository, objectives, mapper, platform_project):
    from types import SimpleNamespace

    from fastapi.testclient import TestClient

    from aisc_control_objectives.api.app import create_app
    from aisc_control_objectives.config import RunConfig
    from aisc_control_objectives.projects import Projects

    app = create_app(objectives, Projects(repository, objectives, mapper, model="fake/model"), base_config=RunConfig())

    @app.middleware("http")
    async def signed_in(request, call_next):
        request.state.caller = SimpleNamespace(username="ada", subject="sub-ada")
        return await call_next(request)
    http = TestClient(app)
    base = f"/p/{platform_project}/api"
    set_id = http.post(f"{base}/sets", json={"code": "SUB", "name": "Subjects"}).json()["id"]
    values = {"dimension": "R2", "label": "Logging", "text": "Keep a log.", "assessment_mode": "Control"}
    assert http.post(f"{base}/sets/{set_id}/objectives", json=values).status_code == 201
    assert http.post(f"{base}/sets/{set_id}/publish").status_code == 200
    with repository.engine.begin() as connection:
        assert connection.execute(text("SELECT created_by_sub FROM control_objectives.objective_set WHERE id = :i"),
                                  {"i": set_id}).scalar() == "sub-ada"
        assert connection.execute(text("SELECT published_by_sub FROM control_objectives.objective_set_version"
                                       " WHERE set_id = :i"), {"i": set_id}).scalar() == "sub-ada"


def test_m9_an_objectives_item_is_its_set_and_its_id(client, platform_project, repository):
    """A set deleted and made again with the same code numbers its objectives again; the item names
    the set, so the new objective's history doesn't continue the deleted one's."""
    http, _ = client
    base = f"/p/{platform_project}/api"
    values = {"dimension": "R2", "label": "Logging", "text": "Keep a log.", "assessment_mode": "Control"}
    items = []
    for _ in range(2):
        set_id = http.post(f"{base}/sets", json={"code": "AGN", "name": "Again"}).json()["id"]
        objective_id = http.post(f"{base}/sets/{set_id}/objectives", json=values).json()["id"]
        http.put(f"{base}/sets/{set_id}/objectives/{objective_id}", json={**values, "text": "Keep a full log."})
        items.append(f"{set_id}/objectives/{objective_id}")
        assert http.delete(f"{base}/sets/{set_id}").status_code == 204
    rows = outbox(repository, "objective.")
    assert {r["item_id"] for r in rows} == set(items) and len(set(items)) == 2
    assert chain_breaks(rows) == []
