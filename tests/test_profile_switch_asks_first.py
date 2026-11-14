"""'Use this profile' on an assessment's page drops every link and selected objective outside the new profile. So
the page asks first and says what goes; one click alone changes nothing. A switch made can be undone: going back
to the profile it left brings back the links it dropped."""
from __future__ import annotations

from aisc_control_objectives.library import FAIRNESS_OVERSIGHT, FULL_AI_ACT
from step2_support import _api


def _body(http, pid, assessment):
    r = http.get(_api(pid, assessment))
    assert r.status_code == 200, r.text
    return r.json()


def _links(body):
    return {rid: sorted(o["objective_id"] for o in m["objectives"])
            for rid, m in body["mapping_run"]["mappings"].items()}


def _mapped_on_full_ai_act(http, platform_project, start):
    assessment = start()
    http.post(_api(platform_project, assessment, "/map"))     # risk0 -> O11, O21, O9; risk4 -> O7, O16, O11 ...
    return assessment


def test_use_this_profile_asks_before_it_drops_links(client, start, platform_project):
    http, _ = client
    assessment = _mapped_on_full_ai_act(http, platform_project, start)
    before = _body(http, platform_project, assessment)
    asked = http.post(f"/p/{platform_project}/projects/{assessment}/profile", data={"profile": FAIRNESS_OVERSIGHT})
    assert asked.status_code == 303 and "profile=ask" in asked.headers["location"], asked.headers.get("location")
    after = _body(http, platform_project, assessment)
    assert after["profile"]["id"] == FULL_AI_ACT, "the profile changed on one click"
    assert _links(after) == _links(before) and after["selected"] == before["selected"]


def test_the_question_says_what_the_switch_drops(client, start, platform_project):
    http, _ = client
    assessment = _mapped_on_full_ai_act(http, platform_project, start)
    asked = http.post(f"/p/{platform_project}/projects/{assessment}/profile", data={"profile": FAIRNESS_OVERSIGHT})
    assert asked.status_code == 303 and "profile=ask" in asked.headers["location"], asked.headers.get("location")
    page = http.get(asked.headers["location"]).text
    assert 'name="confirmed"' in page
    # O9, O16, O6 and O7 are linked on Full AI Act and are outside Fairness and oversight
    for gone in ("O9", "O16", "O6", "O7"):
        assert gone in page, f"the question does not name {gone}"


def test_a_confirmed_switch_changes_the_profile(client, start, platform_project):
    http, _ = client
    assessment = _mapped_on_full_ai_act(http, platform_project, start)
    done = http.post(f"/p/{platform_project}/projects/{assessment}/profile",
                     data={"profile": FAIRNESS_OVERSIGHT, "confirmed": "on"})
    assert done.status_code == 303
    assert _body(http, platform_project, assessment)["profile"]["id"] == FAIRNESS_OVERSIGHT


def test_switching_back_brings_back_the_dropped_links(client, start, platform_project):
    http, _ = client
    assessment = _mapped_on_full_ai_act(http, platform_project, start)
    before = _links(_body(http, platform_project, assessment))
    for profile in (FAIRNESS_OVERSIGHT, FULL_AI_ACT):
        http.post(f"/p/{platform_project}/projects/{assessment}/profile", data={"profile": profile, "confirmed": "on"})
    back = _body(http, platform_project, assessment)
    assert back["profile"]["id"] == FULL_AI_ACT
    assert _links(back) == before, "the links the switch dropped did not come back"
