"""One AI mapping of an assessment runs at a time. A second run asked while one is going is refused before it
calls any model or records anything, and both map routes answer 409 with the reason; once the first run ends,
a new run may start."""
from __future__ import annotations

import pytest

from test_concurrent_writes import _actions, _map_recorded, ledger_on  # noqa: F401  (fixture)


def mapping_lock(repository, assessment):
    lock = getattr(repository, "mapping_lock", None)
    assert lock is not None, "the repository has no lock that keeps a second AI mapping run of one assessment out"
    return lock(assessment)


def refusal():
    from aisc_control_objectives import projects
    found = getattr(projects, "MappingInProgress", None)
    assert found is not None, "no error says an AI mapping of this assessment is already running"
    return found


def test_a_second_ai_run_while_one_runs_is_refused_before_any_model_call(client, start, repository, ledger_on):  # noqa: F811
    _, projects = client
    a = start()
    with mapping_lock(repository, a) as got:
        assert got
        with pytest.raises(refusal()):
            _map_recorded(projects, a)
    assert _actions(repository) == []
    _map_recorded(projects, a)                                   # the lock is free again
    assert _actions(repository)[0] == ("ai.mapping.requested", None)


@pytest.mark.parametrize("route", ["/p/{project}/api/projects/{a}/map", "/p/{project}/projects/{a}/map"])
def test_the_map_routes_say_a_run_is_already_going(client, start, repository, platform_project, route):
    from aisc_control_objectives import projects as module

    http, _ = client
    a = start()
    with mapping_lock(repository, a):
        r = http.post(route.format(project=platform_project, a=a), follow_redirects=False)
    message = getattr(module, "MAPPING_IN_PROGRESS", None)
    assert message is not None, "no message says an AI mapping of this assessment is already running"
    assert r.status_code == 409, (r.status_code, r.text[:300])
    if "/api/" in route:
        assert r.json()["detail"] == message
