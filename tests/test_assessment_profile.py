"""An assessment runs on an objective profile version.

None means the built-in Full AI Act profile. The mapper, the editor, the scores, the key objectives
and the selection see only the profile's objectives. Switching profile, or taking a profile's newer version,
drops the mappings and selected objectives outside it, and says which. A new card version's
assessment starts on the previous one's profile.
"""
from __future__ import annotations

import pytest

from aisc_control_objectives.library import FAIRNESS_OVERSIGHT, FULL_AI_ACT, Library
from step2_support import _api
from test_library import fields


@pytest.fixture
def library(repository, objectives):
    return Library(repository.engine, objectives)


@pytest.fixture
def bank(library):
    """A profile of O1, O7 and BNK1 (BNK version 1)."""
    made = library.create_set("BNK", "Bank policies", "", who="alice")
    library.add_objective(made.id, fields())
    library.publish(made.id, who="alice")
    profile = library.create_profile("Bank", "", ["O1", "O7", "BNK1"], who="alice")
    return {"set": made, "profile": profile}


def _body(http, pid, assessment):
    r = http.get(_api(pid, assessment))
    assert r.status_code == 200, r.text
    return r.json()


def _use(http, pid, assessment, profile_id):
    return http.post(_api(pid, assessment, "/profile"), json={"profile_id": profile_id})


def test_an_assessment_starts_on_full_ai_act(client, start, platform_project):
    http, _ = client
    body = _body(http, platform_project, start())
    assert body["profile"] == {"id": FULL_AI_ACT, "version": None, "label": "Full AI Act", "update": None}
    assert len(body["priorities"]) == 50


def test_on_a_profile_only_its_objectives_are_scored(client, start, platform_project, bank):
    http, _ = client
    assessment = start()
    r = _use(http, platform_project, assessment, bank["profile"].id)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["profile"]["label"] == "Bank, version 1" and body["profile"]["id"] == bank["profile"].id
    assert [p["objective_id"] for p in body["priorities"]] == ["O1", "O7", "BNK1"]


def test_the_ai_and_a_person_map_only_to_the_profiles_objectives(client, start, platform_project, bank):
    http, _ = client
    assessment = start()
    _use(http, platform_project, assessment, bank["profile"].id)
    body = http.post(_api(platform_project, assessment, "/map")).json()
    mapped = {o["objective_id"] for m in body["mapping_run"]["mappings"].values() for o in m["objectives"]}
    assert mapped <= {"O1", "O7", "BNK1"} and mapped     # the fake mapper proposes O1, O7, ... too
    refused = http.post(_api(platform_project, assessment, "/risks/risk0/mapping"), json={"objective_ids": ["O2"]})
    assert refused.status_code == 422 and "O2" in refused.text
    ok = http.post(_api(platform_project, assessment, "/risks/risk0/mapping"), json={"objective_ids": ["BNK1"]})
    assert ok.status_code == 200, ok.text


def test_switching_drops_what_is_outside_the_new_profile(client, start, platform_project, bank):
    http, _ = client
    assessment = start()
    http.post(_api(platform_project, assessment, "/map"))          # Full AI Act: risk2 -> O1, O4, O2 ...
    before = _body(http, platform_project, assessment)
    r = _use(http, platform_project, assessment, bank["profile"].id).json()
    kept = {o["objective_id"] for m in r["mapping_run"]["mappings"].values() for o in m["objectives"]}
    assert kept <= {"O1", "O7", "BNK1"}
    assert set(r["selected"]) <= {"O1", "O7", "BNK1"}
    assert set(r["dropped"]) == (set(before["selected"]) - {"O1", "O7", "BNK1"})
    # and back: nothing comes back by itself
    back = _use(http, platform_project, assessment, FULL_AI_ACT).json()
    assert back["profile"]["id"] == FULL_AI_ACT and back["dropped"] == []
    assert len(back["priorities"]) == 50


def test_the_short_built_in_profile_leaves_ten_objectives_in_step_two(client, start, platform_project):
    http, _ = client
    assessment = start()
    r = _use(http, platform_project, assessment, FAIRNESS_OVERSIGHT)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["profile"] == {"id": FAIRNESS_OVERSIGHT, "version": None,
                               "label": "Fairness and human oversight", "update": None}
    assert sorted(p["objective_id"] for p in body["priorities"]) == sorted(
        ["O1", "O2", "O3", "O4", "O11", "O17", "O19", "O21", "O22", "O23"])
    page = http.get(f"/p/{platform_project}/projects/{assessment}").text
    assert f'<option value="{FAIRNESS_OVERSIGHT}"' in page


def test_a_new_card_version_starts_on_the_previous_profile(client, start, platform_project, bank):
    http, _ = client
    first = start(1)
    _use(http, platform_project, first, bank["profile"].id)
    second = start(2)
    assert _body(http, platform_project, second)["profile"]["label"] == "Bank, version 1"


def test_a_newer_profile_version_is_offered_and_taken_on_request(client, start, platform_project, bank, library):
    http, _ = client
    assessment = start()
    _use(http, platform_project, assessment, bank["profile"].id)
    library.save_profile(bank["profile"].id, ["O1", "BNK1"], who="alice")
    body = _body(http, platform_project, assessment)
    assert body["profile"]["update"] == {"from": 1, "to": 2}
    assert [p["objective_id"] for p in body["priorities"]] == ["O1", "O7", "BNK1"]   # pinned until taken
    taken = _use(http, platform_project, assessment, bank["profile"].id).json()
    assert taken["profile"]["label"] == "Bank, version 2" and taken["profile"]["update"] is None
    assert [p["objective_id"] for p in taken["priorities"]] == ["O1", "BNK1"]


def test_an_unknown_profile_is_refused(client, start, platform_project):
    http, _ = client
    assert _use(http, platform_project, start(), "nope").status_code == 422


def test_an_older_versions_profile_is_read_only(client, start, platform_project, bank):
    http, _ = client
    first = start(1)
    start(2)
    assert _use(http, platform_project, first, bank["profile"].id).status_code == 409


def test_the_page_names_the_profile_and_offers_the_others(client, start, platform_project, bank):
    http, _ = client
    assessment = start()
    _use(http, platform_project, assessment, bank["profile"].id)
    page = http.get(f"/p/{platform_project}/projects/{assessment}").text
    assert "Bank, version 1" in page
    assert f'action="/p/{platform_project}/projects/{assessment}/profile"' in page
    assert f'<option value="{FULL_AI_ACT}"' in page
    # the mapping editor offers only the profile's objectives
    assert 'name="objective" value="BNK1"' in page and 'name="objective" value="O2"' not in page


def test_the_page_form_switches_profile(client, start, platform_project, bank):
    http, _ = client
    assessment = start()
    r = http.post(f"/p/{platform_project}/projects/{assessment}/profile", data={"profile": bank["profile"].id})
    assert r.status_code == 303
    assert _body(http, platform_project, assessment)["profile"]["label"] == "Bank, version 1"


def test_the_page_offers_a_newer_version_only_when_there_is_one(client, start, platform_project, bank, library):
    http, _ = client
    assessment = start()
    _use(http, platform_project, assessment, bank["profile"].id)
    page = http.get(f"/p/{platform_project}/projects/{assessment}").text
    assert "is out" not in page and "Take version" not in page
    library.save_profile(bank["profile"].id, ["O1", "BNK1"], who="alice")
    page = http.get(f"/p/{platform_project}/projects/{assessment}").text
    assert "Version 2 is out." in page and "Take version 2" in page


def test_the_mappers_prompt_lists_the_profiles_objectives(client, start, platform_project, bank, objectives):
    """The model is shown the catalogue its proposals are checked against: the profile's (code review
    2026-10-05). It was shown the built-in catalogue, so a profile's own objectives were never
    proposed and the ones the profile dropped were proposed and refused, round after round."""
    import json
    import re

    from aisc_control_objectives.risk_mapping import RiskMapper

    http, projects = client
    prompts: list[str] = []

    def complete(_skill, prompt):
        prompts.append(prompt)
        risk_id = prompt.split("\n", 1)[0].removeprefix("Risk id: ").strip()
        return json.dumps({"risk_id": risk_id, "objectives": []})

    projects._mapper = RiskMapper(complete=complete, catalogue=objectives, skill="map")
    assessment = start()
    _use(http, platform_project, assessment, bank["profile"].id)
    assert http.post(_api(platform_project, assessment, "/map")).status_code == 200
    listed = {m.group(1) for p in prompts for m in re.finditer(r"^(\w+) \| ", p, re.M)}
    assert prompts and listed == {"O1", "O7", "BNK1"}
