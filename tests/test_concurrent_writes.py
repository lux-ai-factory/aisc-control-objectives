"""Two people writing at once.

Each test really races: two threads, each on its own session of the test database, held together
by a barrier at the point where the old code had read what it was about to write. Where the fix
makes the second thread wait for the first one's lock, the barrier times out (BARRIER_WAIT) and the
first one goes on alone: that wait is the fix at work, not a failure.
"""
from __future__ import annotations

import threading

import pytest

#: How long the first thread waits at the barrier for the second before going on alone.
BARRIER_WAIT = 2.0


def both(first, second):
    """Run two calls on two threads; each one's result, or the exception it raised."""
    results: list = [None, None]

    def run(index, call):
        try:
            results[index] = ("ok", call())
        except Exception as exc:                                       # noqa: BLE001
            results[index] = ("raised", exc)

    threads = [threading.Thread(target=run, args=(i, c)) for i, c in enumerate((first, second))]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert not any(t.is_alive() for t in threads), "a thread is stuck"
    return results


def meeting_point():
    """A wait both threads reach; a thread whose partner never comes goes on after BARRIER_WAIT."""
    barrier = threading.Barrier(2, timeout=BARRIER_WAIT)

    def meet():
        try:
            barrier.wait()
        except threading.BrokenBarrierError:
            pass
    return meet


# A person's mapping recomputes the selection inside its own transaction

@pytest.mark.parametrize("ai_first", [True, False], ids=["after-an-ai-run", "no-ai-run"])
def test_two_people_mapping_two_risks_at_once_both_reach_the_selection(client, start, monkeypatch, ai_first):
    """Alice maps risk0 to O7 while Bob maps risk1 to O12, both having read the assessment before
    either saved. The selection (what the matrix holds) must have both, not the last writer's; with
    no AI run yet, neither may fail on the empty run both would add."""
    from aisc_control_objectives.db.repository import ProjectRepository
    from step2_support import in_order

    _, projects = client
    a = start()
    if ai_first:
        projects.map_risks_of(a)
    meet = meeting_point()
    save = ProjectRepository.save_risk_mapping

    def save_after_both_read(self, *args, **kwargs):
        meet()                                       # both have read the assessment, neither saved
        return save(self, *args, **kwargs)
    monkeypatch.setattr(ProjectRepository, "save_risk_mapping", save_after_both_read)

    results = both(lambda: projects.map_by_hand(a, "risk0", ["O7"]),
                   lambda: projects.map_by_hand(a, "risk1", ["O12"]))
    assert [r[0] for r in results] == ["ok", "ok"], results

    record = projects.view(a).record
    mapped = {rid: [o.objective_id for o in m.objectives] for rid, m in record.mapping_run.mappings.items()}
    assert mapped["risk0"] == ["O7"] and mapped["risk1"] == ["O12"]
    assert {"O7", "O12"} <= set(record.selected)
    assert record.selected == in_order({oid for oids in mapped.values() for oid in oids})


# An AI run is saved only over the assessment it read, checked inside the save's transaction

@pytest.fixture
def ledger_on(repository, monkeypatch):
    """The platform's ledger template in the scratch database, and events recorded (as test_ledger)."""
    from test_ledger import TEMPLATE

    if not TEMPLATE.exists():
        pytest.skip("the platform's ledger template is not in this checkout")
    raw = repository.engine.raw_connection()
    try:
        cursor = raw.cursor()
        cursor.execute(TEMPLATE.read_text())
        cursor.close()
        raw.commit()
    finally:
        raw.close()
    monkeypatch.setenv("LEDGER_MODE", "record")


def _map_recorded(projects, a):
    """An AI run with the route's ledger events: requested, then the run's outcome."""
    from aisc_control_objectives import ledger
    from aisc_control_objectives.api import ledger_events

    return projects.map_risks_of(
        a, on_start=lambda s, run_id: ledger.emit(s, "ai.mapping.requested", item_type="assessment",
                                                  item_id=a, run_id=run_id),
        on_save=lambda s, o: ledger_events.mapping_outcome(s, a, o))


def _actions(repository):
    from test_ledger import outbox

    return [(r["action"], (r["details"] or {}).get("error")) for r in outbox(repository, "ai.mapping")]


def test_a_persons_mapping_saved_while_the_run_saves_is_not_overwritten(client, start, repository, ledger_on,
                                                                        monkeypatch):
    """A person's mapping commits (in a session of its own) after the run's check and before its save:
    the run is refused (409 in the route), the person's rows stay, and the ledger says why it failed."""
    from aisc_control_objectives.db.repository import ProjectRepository
    from aisc_control_objectives.projects import AssessmentChanged

    _, projects = client
    a = start()
    save = ProjectRepository.save_mapping_run

    def a_person_saves_first(self, *args, **kwargs):
        projects.map_by_hand(a, "risk0", ["O5"])                     # its own session, committed
        return save(self, *args, **kwargs)
    monkeypatch.setattr(ProjectRepository, "save_mapping_run", a_person_saves_first)

    with pytest.raises(AssessmentChanged):
        _map_recorded(projects, a)
    record = projects.view(a).record
    assert [o.objective_id for o in record.mapping_run.mappings["risk0"].objectives] == ["O5"]
    assert record.mapping_run.model == ""                              # the person's empty run, not the AI's
    assert _actions(repository) == [("ai.mapping.requested", None), ("ai.mapping.failed", "assessment_changed")]


def test_two_ai_runs_meeting_at_the_save_one_saves_the_other_is_refused_and_recorded(client, start, repository,
                                                                                    ledger_on, monkeypatch):
    """Two runs on one assessment, both past their model calls, save at once: one saves, the other is
    refused as changed (was a 500 on the run's key, with no failed event and its model calls unrecorded)."""
    from aisc_control_objectives.db.repository import ProjectRepository
    from aisc_control_objectives.projects import AssessmentChanged

    _, projects = client
    a = start()
    meet = meeting_point()
    save = ProjectRepository.save_mapping_run

    def save_together(self, *args, **kwargs):
        meet()
        return save(self, *args, **kwargs)
    monkeypatch.setattr(ProjectRepository, "save_mapping_run", save_together)

    results = both(lambda: _map_recorded(projects, a), lambda: _map_recorded(projects, a))
    outcomes = sorted(r[0] for r in results)
    assert outcomes == ["ok", "raised"], results
    assert isinstance(next(r[1] for r in results if r[0] == "raised"), AssessmentChanged), results
    assert sorted(_actions(repository), key=str) == sorted([
        ("ai.mapping.requested", None), ("ai.mapping.requested", None),
        ("ai.mapping.completed", None), ("ai.mapping.failed", "assessment_changed")], key=str)


def test_a_save_that_fails_is_recorded(client, start, repository, ledger_on, monkeypatch):
    """Whatever the save raises, the run is recorded as failed (its model calls with it), then raised."""
    from aisc_control_objectives.db.repository import ProjectRepository

    _, projects = client
    a = start()

    def broken(self, *args, **kwargs):
        raise RuntimeError("the database went away")
    monkeypatch.setattr(ProjectRepository, "save_mapping_run", broken)

    with pytest.raises(RuntimeError, match="went away"):
        _map_recorded(projects, a)
    assert _actions(repository) == [("ai.mapping.requested", None), ("ai.mapping.failed", "mapping_error")]


# Numbers and codes of objective sets, allocated under a lock or refused by name

@pytest.fixture
def library(repository, objectives):
    from aisc_control_objectives.library import Library

    return Library(repository.engine, objectives)


@pytest.fixture
def bnk(library):
    from test_library import ALICE, fields

    made = library.create_set("BNK", "Bank policies", "Our own controls", who=ALICE)
    library.add_objective(made.id, fields())
    return made


def test_two_objectives_added_to_one_set_at_once_take_the_next_two_numbers(library, bnk, monkeypatch):
    """Both editors read the set's next number before either saved: both objectives are kept, with
    two numbers (was a 500 on the second, its objective lost)."""
    from aisc_control_objectives.library import Library
    from test_library import fields

    meet = meeting_point()
    checked = Library._checked

    def checked_after_both_read(self, *args, **kwargs):
        meet()                                        # both have read next_number, neither saved
        return checked(self, *args, **kwargs)
    monkeypatch.setattr(Library, "_checked", checked_after_both_read)

    results = both(lambda: library.add_objective(bnk.id, fields(label="One")),
                   lambda: library.add_objective(bnk.id, fields(label="Two")))
    assert [r[0] for r in results] == ["ok", "ok"], results
    assert sorted(r[1] for r in results) == ["BNK2", "BNK3"]
    assert [o.id for o in library.get_set(bnk.id).draft] == ["BNK1", "BNK2", "BNK3"]


def test_two_publishes_of_one_draft_at_once_make_one_version(library, bnk, monkeypatch):
    """The second publish waits for the first and finds nothing new (was a 500 on the version number)."""
    from aisc_control_objectives.library import Library
    from test_library import ALICE

    meet = meeting_point()
    latest = Library._latest_version

    def latest_after_both_read(self, *args, **kwargs):
        found = latest(self, *args, **kwargs)
        meet()                                        # both have read the latest version, neither saved
        return found
    monkeypatch.setattr(Library, "_latest_version", latest_after_both_read)

    results = both(lambda: library.publish(bnk.id, who=ALICE), lambda: library.publish(bnk.id, who=ALICE))
    assert sorted(r[0] for r in results) == ["ok", "raised"], results
    assert next(r[1] for r in results if r[0] == "ok") == 1
    refused = next(r[1] for r in results if r[0] == "raised")
    assert isinstance(refused, ValueError) and "nothing changed since version 1" in str(refused), refused


def test_two_sets_made_with_one_code_at_once_the_second_is_told_the_code_is_taken(library, monkeypatch):
    """Both checked the code was free before either saved: the second gets the same message as when
    the code was already taken (was a 500)."""
    from aisc_control_objectives import library as library_module
    from test_library import ALICE

    meet = meeting_point()
    new_id = library_module._new_id

    def new_id_after_both_checked():
        meet()                                        # both found the code free, neither saved
        return new_id()
    monkeypatch.setattr(library_module, "_new_id", new_id_after_both_checked)

    results = both(lambda: library.create_set("BNK", "One", "", who=ALICE),
                   lambda: library.create_set("BNK", "Two", "", who=ALICE))
    assert sorted(r[0] for r in results) == ["ok", "raised"], results
    refused = next(r[1] for r in results if r[0] == "raised")
    assert isinstance(refused, ValueError) and "the code BNK is already a set's" in str(refused), refused
    assert [s.code for s in library.sets()] == ["O", "BNK"]
