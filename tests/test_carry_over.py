"""A new card version's assessment starts with the work of the previous one.

The ratings, rationales, mappings and key choices of a risk come along when the new card has the same
risk, matched on its text as the card gives it (trimmed). Risk ids do not say it: the card numbers its
risks by row, so deleting one renumbers the rest. What does not come along stays on the old version, and
the new assessment names it.
"""
from __future__ import annotations

import copy
import json

from step2_support import _api

QUAL = "https://lux-ai-factory.github.io/qualification/ns#"


def card(graph, drop=(), texts=None):
    """The MCAS card with the risks in `drop` gone and the risks in `texts` reworded."""
    out = [node for node in copy.deepcopy(graph)
           if not any(node["@id"].endswith(f"#{rid}") or f"#{rid}_" in node["@id"] for rid in drop)]
    for node in out:
        for rid, text in (texts or {}).items():
            if node["@id"].endswith(f"#{rid}"):
                node[QUAL + "fullLabel"] = [{"@value": text}]
    return out


def texts_of(graph) -> dict:
    from aisc_control_objectives.models.ontology import Ontology

    return {r.id: r.text for r in Ontology.from_jsonld(graph).risks}


def _payload(http, platform_project, assessment):
    response = http.get(_api(platform_project, assessment))
    assert response.status_code == 200, response.text
    return response.json()


def _objectives(body, risk_id):
    return [o["objective_id"] for o in body["mapping_run"]["mappings"][risk_id]["objectives"]]


def worked_on(http, platform_project, assessment):
    """Ratings on three risks, the AI's mapping, a person's mapping of risk2, and two key choices."""
    http.post(_api(platform_project, assessment, "/ratings"), json={
        "risk0": {"impact": 4, "likelihood": 2}, "risk1": {"impact": 5, "likelihood": 5},
        "risk4": {"impact": 3, "likelihood": 3}})
    http.post(_api(platform_project, assessment, "/severity-comments"), json={"risk1": "seen in the pilot"})
    http.post(_api(platform_project, assessment, "/map"))
    http.post(_api(platform_project, assessment, "/risks/risk2/mapping"), json={"objective_ids": ["O30"]})
    http.post(_api(platform_project, assessment, "/key"), json={"O30": True, "O9": False})


def test_the_next_versions_assessment_takes_the_work_on_the_same_risks(client, start, graph, platform_project,
                                                                       system_version):
    http, projects = client
    first = start(1)
    worked_on(http, platform_project, first)
    old = texts_of(graph)
    # v2: risk0 reworded, risk4 gone, and risk1 and risk2 swap places (their ids follow the rows)
    v2 = card(graph, drop=["risk4"], texts={"risk0": "Applicants are wrongly ranked as high risk",
                                             "risk1": old["risk2"], "risk2": "  " + old["risk1"] + " "})
    second = projects.create(platform_project, "MCAS", json.dumps(v2), v2, system_version(platform_project, 2))
    body = _payload(http, platform_project, second.record.id)

    # old risk1 is new risk2 (its text, trimmed), old risk2 is new risk1
    assert body["severity"]["impact"] == {"risk2": 5} and body["severity"]["likelihood"] == {"risk2": 5}
    assert body["severity"]["comments"] == {"risk2": "seen in the pilot"}
    assert _objectives(body, "risk1") == ["O30"]
    assert body["mapping_run"]["mappings"]["risk1"]["objectives"][0]["source"] == "person"
    assert set(_objectives(body, "risk2")) == {"O21", "O22", "O23"}
    assert set(_objectives(body, "risk3")) == {"O17", "O19", "O6"}
    # reworded: starts unrated and unmapped
    assert _objectives(body, "risk0") == [] and "risk0" not in body["severity"]["impact"]
    # the matrix goes forward; O9 was only old risk0's, so its key choice stays behind
    assert "O30" in body["selected"] and "O9" not in body["selected"]
    keys = {p["objective_id"]: p["key"] for p in body["priorities"]}
    assert keys["O30"] is True
    assert body["carried"] == {"from": 1, "risks": 3, "new": ["Applicants are wrongly ranked as high risk"],
                               "left": [old["risk0"], old["risk4"]], "outside": []}

    # the person's mapping is still theirs: the AI leaves it alone
    after = http.post(_api(platform_project, second.record.id, "/map")).json()
    assert _objectives(after, "risk1") == ["O30"]


def test_the_page_names_what_came_along_and_what_did_not(client, start, graph, platform_project, system_version):
    http, projects = client
    first = start(1)
    worked_on(http, platform_project, first)
    old = texts_of(graph)
    v2 = card(graph, drop=["risk4"], texts={"risk0": "Applicants are wrongly ranked as high risk"})
    second = projects.create(platform_project, "MCAS", json.dumps(v2), v2, system_version(platform_project, 2))
    page = " ".join(http.get(f"/p/{platform_project}/projects/{second.record.id}").text.split())
    assert "Carried from v1: ratings and links of 3 risks. 1 risk is new or changed and starts unrated." in page
    assert "Applicants are wrongly ranked as high risk" in page
    assert f"Stayed on v1: {old['risk0']}" in page and old["risk4"] in page


def test_a_first_assessment_says_nothing_was_carried(client, start, platform_project):
    http, _ = client
    assessment = start(1)
    assert _payload(http, platform_project, assessment)["carried"] is None
    assert "Carried from" not in http.get(f"/p/{platform_project}/projects/{assessment}").text


def test_a_previous_assessment_with_no_work_carries_nothing(client, start, platform_project):
    http, _ = client
    start(1)
    second = start(2)
    assert _payload(http, platform_project, second)["carried"] is None


def test_mappings_outside_the_new_profile_are_not_carried_and_are_named(client, start, graph, platform_project,
                                                                        system_version, repository):
    """The new assessment's profile is the previous one's, so this happens only when the two disagree; the
    repository is told which objectives it may carry."""
    from aisc_control_objectives.models.ontology import Ontology

    http, _ = client
    first = start(1)
    http.post(_api(platform_project, first, "/risks/risk0/mapping"), json={"objective_ids": ["O5", "O10"]})
    record = repository.create(platform_project, "MCAS", Ontology.from_jsonld(graph), json.dumps(graph),
                               system_id=system_version(platform_project, 2),
                               carry_objectives={o for o in [f"O{n}" for n in range(1, 51)] if o != "O10"},
                               order=sorted)
    body = _payload(http, platform_project, record.id)
    assert _objectives(body, "risk0") == ["O5"] and body["selected"] == ["O5"]
    assert body["carried"]["outside"] == ["O10"]
    page = http.get(f"/p/{platform_project}/projects/{record.id}").text
    assert "Not carried, outside this profile: O10" in page


def test_two_risks_with_one_text_are_matched_in_order(client, start, graph, platform_project, system_version):
    http, projects = client
    same = "The same risk, twice"
    v1 = card(graph, texts={"risk0": same, "risk3": same})
    a = projects.create(platform_project, "MCAS", json.dumps(v1), v1, system_version(platform_project, 1)).record.id
    http.post(_api(platform_project, a, "/ratings"), json={"risk0": {"impact": 1, "likelihood": 1},
                                                          "risk3": {"impact": 2, "likelihood": 2}})
    v2 = card(graph, drop=["risk1"], texts={"risk0": same, "risk3": same})
    b = projects.create(platform_project, "MCAS", json.dumps(v2), v2, system_version(platform_project, 2)).record.id
    assert _payload(http, platform_project, b)["severity"]["impact"] == {"risk0": 1, "risk3": 2}
