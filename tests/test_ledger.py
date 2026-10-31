"""Step 2 in the ledger. Every write records its event with the project database's
ledger.emit, in the write's own transaction; an AI mapping keeps the previous run and an assessor's own
rows (mapping_archive) and is one run in the ledger; authors are kept by subject. The scratch database
gets the platform's real ledger template (platform/project-template/0020_ledger_outbox.sql)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import text

from aisc_control_objectives.library import FAIRNESS_OVERSIGHT
from step2_support import _api

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
    # local work: no gateway token to verify, so the presented request id is cited as it is
    # (test_ledger_request_id.py checks who may set it with auth on)
    monkeypatch.setenv("AUTH_ENABLED", "false")


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
    severity = repository.get(a).severity
    assert (severity.impact, severity.likelihood) == ({}, {})
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
    http.post(_api(platform_project, a, "/map?replace=true"))        # replaces everything on the page...
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
    from aisc_control_objectives.projects import ModelUnavailable

    a = start()
    projects = Projects(repository, objectives, Broken(), model="fake/model")
    with pytest.raises(ModelUnavailable):                             # Nothing saved, a 502
        projects.map_risks_of(a, on_start=lambda s, run_id: ledger.emit(
            s, "ai.mapping.requested", item_type="assessment", item_id=a, run_id=run_id),
            on_save=lambda s, o: ledger_events.mapping_outcome(s, a, o))
    rows = outbox(repository)
    assert [r["action"] for r in rows] == ["ai.mapping.requested", "ai.mapping.failed"]
    assert rows[1]["details"]["error"] == "mapping_error" and rows[0]["run_id"] == rows[1]["run_id"]
    assert "Jane" not in json.dumps(rows) and "AIza" not in json.dumps(rows)


def test_a_partly_failed_run_records_what_it_saved(start, repository, objectives):
    """One risk's provider call fails, the others are mapped and saved: the ledger
    said ai.mapping.failed with no content, so it held no record of the mappings now live. It
    records ai.mapping.completed with them, the error's code and the risks that failed."""
    from aisc_control_objectives import ledger
    from aisc_control_objectives.api import ledger_events
    from aisc_control_objectives.projects import Projects
    from test_projects import FakeMapper

    class FailsOnRisk1(FakeMapper):
        def propose(self, risk, findings=()):
            if risk.id == "risk1":
                raise RuntimeError("provider timeout for Jane Doe")
            return super().propose(risk, findings)

    a = start()
    projects = Projects(repository, objectives, FailsOnRisk1(), model="fake/model")
    projects.map_risks_of(a, on_start=lambda s, run_id: ledger.emit(
        s, "ai.mapping.requested", item_type="assessment", item_id=a, run_id=run_id),
        on_save=lambda s, o: ledger_events.mapping_outcome(s, a, o))
    rows = outbox(repository)
    assert [r["action"] for r in rows] == ["ai.mapping.requested", "ai.mapping.completed"]
    done = rows[1]
    assert done["details"]["error"] == "mapping_error" and done["details"]["failed_risks"] == ["risk1"]
    assert done["content"]["risks"]["risk0"] == FakeMapper.BY_RISK["risk0"]
    assert "Jane" not in json.dumps(rows)


def _mapped(repository, a) -> dict:
    run = repository.get(a).mapping_run
    return {rid: [o.objective_id for o in m.objectives] for rid, m in run.mappings.items()} if run else {}


def test_a_run_whose_every_model_call_fails_keeps_the_matrix(start, repository, objectives):
    """Every propose raised (key revoked, provider down), and the empty run was saved over the
    matrix: the person's rows and the selection were gone. Now nothing is saved and the run is a 502."""
    from aisc_control_objectives import ledger
    from aisc_control_objectives.api import ledger_events
    from aisc_control_objectives.projects import ModelUnavailable, Projects
    from step2_support import FakeMapper

    class Broken:
        def propose(self, *a, **k):
            raise RuntimeError("provider 401")

    a = start()
    Projects(repository, objectives, FakeMapper(), model="fake/model").map_by_hand(a, "risk1", ["O1"])
    archived = len(archive(repository))
    projects = Projects(repository, objectives, Broken(), model="fake/model")
    with pytest.raises(ModelUnavailable):
        projects.map_risks_of(a, on_start=lambda s, run_id: ledger.emit(
            s, "ai.mapping.requested", item_type="assessment", item_id=a, run_id=run_id),
            on_save=lambda s, o: ledger_events.mapping_outcome(s, a, o))
    assert _mapped(repository, a)["risk1"] == ["O1"]
    assert list(repository.get(a).selected) == ["O1"]
    assert len(archive(repository)) == archived
    assert [r["action"] for r in outbox(repository, "ai.mapping")] == ["ai.mapping.requested", "ai.mapping.failed"]


def test_a_risk_whose_model_call_fails_keeps_its_rows(start, repository, objectives):
    """Partial failure: the risk whose call failed keeps what it had; the others are mapped anew.
    The ledger's ai.mapping.completed holds the rows really kept, not the failed call's empty mapping
    (it said risk1 = [] while the matrix showed risk1's earlier rows)."""
    from aisc_control_objectives.api import ledger_events
    from aisc_control_objectives.projects import Projects
    from step2_support import FakeMapper, SwitchableMapper

    a = start()
    Projects(repository, objectives, FakeMapper(), model="fake/model").map_risks_of(a)

    class FailsOnRisk1(SwitchableMapper):
        def propose(self, risk, findings=()):
            if risk.id == "risk1":
                raise RuntimeError("provider timeout")
            return super().propose(risk, findings)

    mapper = FailsOnRisk1()
    mapper.by_risk["risk2"] = ["O5"]
    Projects(repository, objectives, mapper, model="fake/model").map_risks_of(
        a, on_save=lambda s, o: ledger_events.mapping_outcome(s, a, o))
    mapped = _mapped(repository, a)
    assert mapped["risk1"] == FakeMapper.BY_RISK["risk1"]
    done = outbox(repository, "ai.mapping.completed")[-1]
    assert done["details"]["failed_risks"] == ["risk1"]
    assert done["content"]["risks"] == mapped
    assert mapped["risk2"] == ["O5"]
    selected = set(repository.get(a).selected)
    assert set(FakeMapper.BY_RISK["risk1"]) <= selected and "O5" in selected and "O4" not in selected
    replaced = archive(repository)[-1]["rows"]
    assert "risk1" not in {r["risk"] for r in replaced}


def test_each_model_call_of_a_run_is_recorded():
    from aisc_control_objectives import ledger

    calls = ledger.Calls()
    complete = calls.recording(lambda s, u: "{}", "mapping")
    complete("s", "u")
    complete("s", "u")
    assert [c["purpose"] for c in calls.calls] == ["mapping", "mapping"]


# Authors by subject

def test_library_actions_record_their_events(client, platform_project, repository):
    http, _ = client
    base = f"/p/{platform_project}/api"
    made = http.post(f"{base}/sets", json={"code": "LDG", "name": "Ledger set"}, headers=HEADERS)
    assert made.status_code == 201, made.text
    set_id = made.json()["id"]
    values = {"dimension": "REQ2", "label": "Logging", "text": "Keep a log.", "assessment_mode": "Control"}
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


def test_a_mapping_records_its_request_before_its_first_model_call(start, repository, objectives, mapper):
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


def test_a_route_mapping_records_each_model_call(repository, objectives, mapper, graph, platform_project,
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


def test_a_model_that_cant_be_had_records_the_request_and_its_failure(start, repository, objectives, mapper):
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


def test_a_failure_to_record_a_failed_run_keeps_the_runs_own_error(start, repository, objectives, mapper,
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


def test_a_failure_after_a_runs_events_leaves_no_run_and_no_event(start, repository, objectives, mapper):
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


def test_a_completed_runs_content_is_never_empty(start, repository, objectives):
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


def test_the_item_chains_hold_in_normal_use(client, start, platform_project, repository):
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


def test_a_new_card_versions_assessment_names_the_profile_it_starts_on(client, start, platform_project,
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


def test_a_profile_switch_keeps_what_it_drops(client, start, platform_project, repository, objectives):
    from aisc_control_objectives.library import Library

    library = Library(repository.engine, objectives)
    profile = library.create_profile("Only O1", "", ["O1"], who="alice")
    http, _ = client
    a = start()
    http.post(_api(platform_project, a, "/map"))

    def mapped():
        run = repository.get(a).mapping_run
        return {(risk, m.objective_id) for risk, mapping in run.mappings.items() for m in mapping.objectives}

    before = mapped()
    assert http.post(_api(platform_project, a, "/profile"), json={"profile_id": profile.id}).status_code == 200
    after = mapped()
    [kept] = [k for k in archive(repository) if k["reason"] == "profile"]
    archived = {(r["risk"], r["objective"]) for r in kept["rows"]}
    dropped = before - after
    assert {o for _, o in after} <= {"O1"}
    # every row the switch drops is archived; the archive keeps each losing risk's whole mapping,
    # so it may also hold that risk's O1 rows, but nothing that was not mapped before
    assert dropped and dropped <= archived <= before


def test_deleting_an_assessment_archives_its_mapping_and_keeps_its_keys(client, start, platform_project,
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


def test_the_routes_store_the_authors_subject(repository, objectives, mapper, platform_project):
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
    values = {"dimension": "REQ2", "label": "Logging", "text": "Keep a log.", "assessment_mode": "Control"}
    assert http.post(f"{base}/sets/{set_id}/objectives", json=values).status_code == 201
    assert http.post(f"{base}/sets/{set_id}/publish").status_code == 200
    with repository.engine.begin() as connection:
        assert tuple(connection.execute(text("SELECT created_by, created_by_sub FROM control_objectives.objective_set"
                                             " WHERE id = :i"), {"i": set_id}).one()) == ("ada", "sub-ada")
        assert connection.execute(text("SELECT published_by_sub FROM control_objectives.objective_set_version"
                                       " WHERE set_id = :i"), {"i": set_id}).scalar() == "sub-ada"


def test_an_objectives_item_is_its_set_and_its_id(client, platform_project, repository):
    """A set deleted and made again with the same code numbers its objectives again; the item names
    the set, so the new objective's history doesn't continue the deleted one's."""
    http, _ = client
    base = f"/p/{platform_project}/api"
    values = {"dimension": "REQ2", "label": "Logging", "text": "Keep a log.", "assessment_mode": "Control"}
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


def test_unticking_a_default_key_objective_is_recorded_as_removed(client, start, platform_project, repository):
    """objective.key.set compared explicit choices only: an objective key by default,
    unticked on the first save, was never reported as removed, and every default left ticked
    was reported as added. It is now compared with what was key before the save."""
    http, _ = client
    a = start()
    http.post(_api(platform_project, a, "/map"))
    http.post(_api(platform_project, a, "/ratings"),
              json={f"risk{i}": {"impact": 5, "likelihood": 5} for i in range(5)})
    body = http.get(_api(platform_project, a)).json()
    defaults = [p["objective_id"] for p in body["priorities"] if p["key"]]
    assert defaults, "the fixture should make some objective key by default"
    dropped, *kept = defaults
    form = {dropped: False, **dict.fromkeys(kept, True)}
    assert http.post(_api(platform_project, a, "/key"), json=form).status_code == 200
    event = [r for r in outbox(repository) if r["action"] == "objective.key.set"][-1]
    assert event["details"] == {"added": [], "removed": [dropped]}


def test_resetting_the_keys_records_what_stopped_and_started_being_key(client, start, platform_project,
                                                                      repository):
    """A reset is a key save: before and after are the stored choices (the chain holds), and added and
    removed compare what was key, choices included, with the defaults that are key now."""
    http, _ = client
    a = start()
    http.post(_api(platform_project, a, "/ratings"), json={"risk2": {"impact": 5, "likelihood": 4}})
    http.post(_api(platform_project, a, "/map"))                      # risk2 -> O1, O4, O2: key by default
    http.post(_api(platform_project, a, "/key"), json={"O1": False, "O9": True})
    assert http.post(_api(platform_project, a, "/keys/reset"), headers=HEADERS).status_code == 200
    rows = outbox(repository)
    event = [r for r in rows if r["action"] == "objective.key.set"][-1]
    assert event["item_type"] == "assessment" and event["item_id"] == a
    assert event["request_id"] == REQUEST
    assert event["before"] == {"O1": False, "O9": True} and event["after"] == {}
    assert event["details"] == {"added": ["O1"], "removed": ["O9"]}
    http.post(_api(platform_project, a, "/key"), json={"O4": False})
    assert chain_breaks(outbox(repository)) == []


def test_a_reset_that_leaves_what_is_key_as_it_was_is_still_recorded(client, start, platform_project,
                                                                     repository):
    """As every key save: the event is written when the stored choices change, even if no objective
    changes key; a reset with no choice to remove writes nothing."""
    http, _ = client
    a = start()
    http.post(_api(platform_project, a, "/ratings"), json={"risk2": {"impact": 5, "likelihood": 4}})
    http.post(_api(platform_project, a, "/map"))
    http.post(_api(platform_project, a, "/key"), json={"O1": True})   # key by default anyway
    http.post(_api(platform_project, a, "/keys/reset"))
    events = [r for r in outbox(repository) if r["action"] == "objective.key.set"]
    assert len(events) == 2
    assert events[-1]["before"] == {"O1": True} and events[-1]["after"] == {}
    assert events[-1]["details"] == {"added": [], "removed": []}
    http.post(_api(platform_project, a, "/keys/reset"))
    assert len([r for r in outbox(repository) if r["action"] == "objective.key.set"]) == 2


def test_a_start_that_carries_work_says_from_where_and_what(client, start, platform_project, repository, graph,
                                                            system_version):
    """What the start hands its event: the assessment it carried from, how many risks, and what they hold."""
    http, projects = client
    a = start()
    http.post(_api(platform_project, a, "/ratings"), json={"risk0": {"impact": 4, "likelihood": 2}})
    http.post(_api(platform_project, a, "/risks/risk0/mapping"), json={"objective_ids": ["O5"]})
    http.post(_api(platform_project, a, "/key"), json={"O5": True})
    seen = []
    projects.create(platform_project, "MCAS", json.dumps(graph), graph, system_version(platform_project, 2),
                    record=lambda session, c: seen.append(c))
    [c] = seen
    assert (c["carried_from"], c["carried_risks"]) == (a, 1)
    assert c["carried"]["risks"]["risk0"] == {"impact": 4, "likelihood": 2, "comment": "", "mapped": ["O5"]}
    assert c["carried"]["keys"] == {"O5": True}


def test_a_renamed_profile_is_recorded_as_edited_and_a_new_version_as_saved(client, platform_project, repository):
    http, _ = client
    base = f"/p/{platform_project}/api/profiles"
    pid = http.post(base, json={"name": "Bank", "objective_ids": ["O1"]}, headers=HEADERS).json()["id"]
    http.post(f"{base}/{pid}/versions", json={"objective_ids": ["O1"], "name": "Bank, high risk"}, headers=HEADERS)
    http.post(f"{base}/{pid}/versions", json={"objective_ids": ["O1", "O7"]}, headers=HEADERS)
    rows = outbox(repository, "objective_profile")
    assert [r["action"] for r in rows] == ["objective_profile.created", "objective_profile.edited",
                                           "objective_profile.version_saved"]
    edited = rows[1]
    assert (edited["item_id"], edited["before"]["name"], edited["after"]["name"]) == (pid, "Bank", "Bank, high risk")
    assert rows[2]["details"] == {"version": 2, "dropped": []}
    assert chain_breaks(rows) == []


def test_a_key_choice_deleted_outright_is_recorded_as_removed():
    """A card update deletes the key choices of a risk that went: the event says which stopped being key."""
    from aisc_control_objectives.api import ledger_events

    assert ledger_events.keys({"before": {"O5": True, "O7": False}, "after": {}}) == {"added": [],
                                                                                       "removed": ["O5"]}


def test_saving_a_persons_mapping_again_unchanged_changes_and_records_nothing(client, start, platform_project,
                                                                             repository):
    http, _ = client
    a = start()
    http.post(_api(platform_project, a, "/risks/risk0/mapping"), json={"objective_ids": ["O5"]})
    archived = len(archive(repository))
    http.post(_api(platform_project, a, "/risks/risk0/mapping"), json={"objective_ids": ["O5"]})
    assert len(archive(repository)) == archived
    assert len(outbox(repository, "mapping.risk.edited")) == 1


def test_keeping_the_ais_mapping_by_hand_records_that_a_person_now_owns_it(client, start, platform_project,
                                                                           repository):
    """Saved unchanged, the AI's mapping of a risk becomes the person's: the next run skips it, and the
    ledger says why."""
    http, projects = client
    a = start()
    http.post(_api(platform_project, a, "/map"))
    mapped = [o.objective_id for o in projects.view(a).record.mapping_run.mappings["risk0"].objectives]
    http.post(_api(platform_project, a, "/risks/risk0/mapping"), json={"objective_ids": mapped})
    assert "risk0" in projects.view(a).record.by_hand
    [edited] = outbox(repository, "mapping.risk.edited")
    assert edited["before"] == edited["after"] == sorted(mapped)
    assert edited["details"] == {"added": [], "removed": [], "by_hand": True}


def test_any_error_getting_the_model_records_the_request_and_its_failure_and_answers_502(
        repository, objectives, mapper, start, platform_project):
    """Not only the expected refusals: a bug or an outage while the model is being had is recorded as
    model_unreachable, and the caller gets a 502 that never quotes the error's secrets."""
    from fastapi.testclient import TestClient

    from aisc_control_objectives.api.app import create_app
    from aisc_control_objectives.config import RunConfig
    from aisc_control_objectives.library import FULL_AI_ACT
    from aisc_control_objectives.projects import Projects

    def broken(pid):
        raise RuntimeError("connection to https://llm.example/v1?key=sk-proj-abcdefghijklmnopqrstuvwxyz refused")
    projects = Projects(repository, objectives, mapper, model="fake/model", default_profile=FULL_AI_ACT,
                        mapper_for=broken)
    http = TestClient(create_app(objectives, projects, base_config=RunConfig()))
    a = start()                                                       # same database, another Projects
    r =http.post(_api(platform_project, a, "/map"))
    assert r.status_code == 502
    assert "sk-proj-abcdefghijklmnopqrstuvwxyz" not in r.text
    rows = outbox(repository)
    assert [x["action"] for x in rows] == ["ai.mapping.requested", "ai.mapping.failed"]
    assert rows[1]["details"]["error"] == "model_unreachable"


# The page's forms and the JSON API record each action with one recorder

ROUTE_PAIRS = {
    "on_mapping_requested": ("/map", {}, "/map", None),
    "on_mapping_run": ("/map", {}, "/map", None),
    "on_mapping_edited": ("/risks/risk0/mapping", {"objective": "O5"}, "/risks/risk0/mapping",
                          {"objective_ids": ["O5"]}),
    "on_rated": ("/severity", {"impact:risk0": "4"}, "/ratings", {"risk0": {"impact": 4}}),
    "on_keys_set": ("/key", {"key": "O5"}, "/key", {"O5": True}),
    "on_profile_switched": ("/profile", {"profile": FAIRNESS_OVERSIGHT}, "/profile",
                            {"profile_id": FAIRNESS_OVERSIGHT}),
}


@pytest.mark.parametrize("recorder", sorted(ROUTE_PAIRS))
def test_a_form_and_its_api_route_record_with_the_same_recorder(recorder, client, start, platform_project,
                                                                repository, monkeypatch):
    from aisc_control_objectives.api import ledger_events

    made = []
    real = getattr(ledger_events, recorder)

    def spy(*args):
        made.append(args)
        return real(*args)
    monkeypatch.setattr(ledger_events, recorder, spy)
    http, _ = client
    a = start()
    form_path, form, api_path, body = ROUTE_PAIRS[recorder]
    assert http.post(f"/p/{platform_project}/projects/{a}{form_path}", data=form).status_code == 303
    assert http.post(_api(platform_project, a, api_path), json=body).status_code == 200
    assert len(made) == 2 and made[0] == made[1]


def test_resetting_the_keys_on_the_page_and_through_the_api_records_with_the_same_recorder(
        client, start, platform_project, repository, monkeypatch):
    from aisc_control_objectives.api import ledger_events

    made = []
    real = ledger_events.on_keys_set
    monkeypatch.setattr(ledger_events, "on_keys_set", lambda *args: made.append(args) or real(*args))
    http, _ = client
    a = start()
    assert http.post(f"/p/{platform_project}/projects/{a}/keys/reset", data={"confirmed": "1"}).status_code == 303
    assert http.post(_api(platform_project, a, "/keys/reset")).status_code == 200
    assert made == [(a,), (a,)]
