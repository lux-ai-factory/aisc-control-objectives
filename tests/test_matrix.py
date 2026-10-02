"""Step 2 as a risk and control matrix (2026-10-01).

The page is three parts: the profile line, the **risk register** (one row per risk: impact,
likelihood, the rating in its band, the optional rationale; the AIRO chain folded away) and the
**matrix** (one row per risk -> objective, by rating: the objective, who mapped it, its score and
whether it is key). The mapping *is* the matrix: an objective is in scope (it reaches step 4) when
it is in the matrix, so there is no separate selection.
"""
from __future__ import annotations

import re

from test_objective_selection import FakeMapper, _api, client, graph, mapper, start  # noqa: F401


def page_of(http, pid, assessment) -> str:
    r = http.get(f"/p/{pid}/projects/{assessment}")
    assert r.status_code == 200, r.text
    return r.text


def body_of(http, pid, assessment) -> dict:
    return http.get(_api(pid, assessment)).json()


def rate_form(http, pid, assessment, **pairs):
    data = {}
    for risk, (impact, likelihood) in pairs.items():
        data[f"impact:{risk}"] = str(impact)
        data[f"likelihood:{risk}"] = str(likelihood)
    return http.post(f"/p/{pid}/projects/{assessment}/severity", data=data)


# ── rating ──────────────────────────────────────────────────────────────────

def test_the_register_form_saves_impact_and_likelihood(client, start, platform_project):
    http, _ = client
    a = start()
    assert rate_form(http, platform_project, a, risk2=(5, 4), risk4=(4, 2)).status_code == 303
    severity = body_of(http, platform_project, a)["severity"]
    assert severity["impact"] == {"risk2": 5, "risk4": 4}
    assert severity["likelihood"] == {"risk2": 4, "risk4": 2}


def test_the_api_saves_ratings(client, start, platform_project):
    http, _ = client
    a = start()
    r = http.post(_api(platform_project, a, "/ratings"), json={"risk1": {"impact": 2, "likelihood": 5}})
    assert r.status_code == 200, r.text
    assert r.json()["severity"]["impact"] == {"risk1": 2} and r.json()["severity"]["likelihood"] == {"risk1": 5}
    assert http.post(_api(platform_project, a, "/ratings"), json={"risk1": {"impact": 9}}).status_code == 422
    assert http.post(_api(platform_project, a, "/ratings"), json={"nope": {"impact": 2}}).status_code == 422


def test_the_register_lists_risks_by_rating_with_their_band(client, start, platform_project):
    http, _ = client
    a = start()
    rate_form(http, platform_project, a, risk2=(5, 4), risk4=(4, 3), risk0=(1, 1))
    page = page_of(http, platform_project, a)
    register = re.search(r'<table class="co-register">(.*?)</table>', page, re.S).group(1)
    rows = re.findall(r'<tr id="risk-(\w+)"', register)
    assert rows[:2] == ["risk2", "risk4"] and rows[-1] == "risk0"
    assert re.search(r'<span class="co-rating co-rating--critical">20</span>', register)
    assert re.search(r'<span class="co-rating co-rating--high">12</span>', register)
    assert re.search(r'<span class="co-rating co-rating--medium">9</span>', register)      # unrated: 3 x 3
    assert 'name="impact:risk2"' in register and 'name="likelihood:risk2"' in register
    assert 'name="comment:risk2"' in register
    # the AIRO chain folds away in its row
    assert re.search(r'<details class="co-chain">', register)


# ── the matrix ──────────────────────────────────────────────────────────────

def test_the_matrix_has_a_row_per_risk_and_objective_by_rating(client, start, platform_project):
    http, _ = client
    a = start()
    rate_form(http, platform_project, a, risk2=(5, 4), risk4=(4, 3))
    http.post(_api(platform_project, a, "/map"))
    page = page_of(http, platform_project, a)
    matrix = re.search(r'<table class="co-matrix">(.*?)</table>', page, re.S).group(1)
    pairs = re.findall(r'<tr class="co-mrow[^"]*" data-risk="(\w+)" data-objective="(\w+)"', matrix)
    expected = sum(len(v) for v in FakeMapper.BY_RISK.values())
    assert len(pairs) == expected
    assert [r for r, _ in pairs[:3]] == ["risk2"] * 3       # the Critical risk first
    assert pairs[3][0] == "risk4"
    assert "co-map-source--ai" in matrix


def test_a_risk_with_nothing_mapped_has_an_empty_row_to_fill(client, start, platform_project):
    http, _ = client
    a = start()
    page = page_of(http, platform_project, a)
    matrix = re.search(r'<table class="co-matrix">(.*?)</table>', page, re.S).group(1)
    assert len(re.findall(r'<tr class="co-mrow co-mrow--empty" data-risk="(\w+)"', matrix)) == 5
    assert "No objective yet" in matrix
    assert f'action="/p/{platform_project}/projects/{a}/risks/risk0/mapping"' in page


def test_suggest_with_ai_keeps_its_question_mark(client, start, platform_project):
    http, _ = client
    page = page_of(http, platform_project, start())
    assert re.search(r'<button class="btn" type="submit">Suggest with AI</button>', page)
    assert 'popovertarget="ai-help"' in page


def test_tiers_and_the_selection_are_gone(client, start, platform_project):
    http, _ = client
    a = start()
    http.post(_api(platform_project, a, "/map"))
    page = page_of(http, platform_project, a)
    assert "Tier 1" not in page and "Take forward" not in page and 'id="selection"' not in page
    assert http.post(_api(platform_project, a, "/selection"), json={"objective_ids": []}).status_code == 404


# ── key objectives ──────────────────────────────────────────────────────────

def test_key_is_set_by_default_and_the_assessor_can_change_it(client, start, platform_project):
    http, _ = client
    a = start()
    rate_form(http, platform_project, a, risk2=(5, 4))
    http.post(_api(platform_project, a, "/map"))           # risk2 -> O1, O4, O2
    keys = {p["objective_id"]: p["key"] for p in body_of(http, platform_project, a)["priorities"]}
    assert keys["O1"] and keys["O2"] and keys["O4"]
    r = http.post(_api(platform_project, a, "/key"), json={"O1": False, "O7": True})
    assert r.status_code == 200, r.text
    keys = {p["objective_id"]: p["key"] for p in r.json()["priorities"]}
    assert not keys["O1"] and keys["O7"] and keys["O2"]
    assert http.post(_api(platform_project, a, "/key"), json={"O99": True}).status_code == 422


def test_the_key_form_ticks_exactly_the_key_objectives(client, start, platform_project):
    http, _ = client
    a = start()
    rate_form(http, platform_project, a, risk2=(5, 4))
    http.post(_api(platform_project, a, "/map"))
    page = page_of(http, platform_project, a)
    assert f'<form id="keys" method="post" action="/p/{platform_project}/projects/{a}/key">' in page
    # the key tick sits on an objective's first row only
    assert page.count('form="keys" name="key" value="O1"') == 1
    r = http.post(f"/p/{platform_project}/projects/{a}/key", data={"key": ["O2", "O11"]})
    assert r.status_code == 303
    keys = {p["objective_id"]: p["key"] for p in body_of(http, platform_project, a)["priorities"]}
    assert keys["O2"] and keys["O11"] and not keys["O1"] and not keys["O4"]


# ── in scope is in the matrix ───────────────────────────────────────────────

def test_what_is_in_the_matrix_is_in_scope_in_catalogue_order(client, start, platform_project):
    http, _ = client
    a = start()
    http.post(_api(platform_project, a, "/map"))
    mapped = {oid for oids in FakeMapper.BY_RISK.values() for oid in oids}
    assert body_of(http, platform_project, a)["selected"] == sorted(mapped, key=lambda o: int(o[1:]))
    http.post(_api(platform_project, a, "/risks/risk0/mapping"), json={"objective_ids": ["O30"]})
    selected = body_of(http, platform_project, a)["selected"]
    assert "O30" in selected and "O9" not in selected          # O9 was only risk0's
