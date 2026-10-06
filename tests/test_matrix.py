"""Step 2 as a risk and control matrix.

The page is three parts: the profile line, the **risk register** (one row per risk: impact,
likelihood, the rating in its band, the optional rationale; the AIRO chain folded away) and the
**matrix** (one row per risk -> objective, by rating: the objective, who mapped it, its score and
whether it is key). The mapping *is* the matrix: an objective is in scope (it reaches step 4) when
it is in the matrix, so there is no separate selection.
"""
from __future__ import annotations

import re

from step2_support import FakeMapper, _api


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


# Rating

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
    assert rows[:3] == ["risk2", "risk4", "risk0"]      # the unrated ones after every rated one
    assert re.search(r'<span class="co-rating co-rating--critical">20</span>', register)
    assert re.search(r'<span class="co-rating co-rating--high">12</span>', register)
    assert re.search(r'<span class="co-rating co-rating--not-rated">–</span>\s*<span class="co-band">Not rated</span>',
                     register)
    assert 'name="impact:risk2"' in register and 'name="likelihood:risk2"' in register
    assert 'name="comment:risk2"' in register
    # the AIRO chain folds away in its row
    assert re.search(r'<details class="co-chain">', register)


# The matrix: one row per risk, its objectives as chips

def matrix_of(page: str) -> str:
    return re.search(r'<table class="co-matrix">(.*?)</table>', page, re.S).group(1)


def rows_of(matrix: str) -> list[str]:
    return re.findall(r'<tr class="co-mrow" data-risk="(\w+)"', matrix)


def chips_of(matrix: str, risk: str) -> list[tuple[str, str]]:
    """(objective id, classes) of a risk's chips, in order."""
    row = re.search(rf'<tr class="co-mrow" data-risk="{risk}">(.*?)</tr>', matrix, re.S).group(1)
    return [(oid, cls) for cls, oid in re.findall(
        r'<button type="button" class="co-chip ([^"]*)" popovertarget="[^"]+" data-objective="(\w+)"', row)]


def test_one_row_per_risk_by_rating(client, start, platform_project):
    http, _ = client
    a = start()
    rate_form(http, platform_project, a, risk2=(5, 4), risk4=(4, 3), risk0=(1, 1))
    http.post(_api(platform_project, a, "/map"))
    rows = rows_of(matrix_of(page_of(http, platform_project, a)))
    assert len(rows) == 5 and rows[:3] == ["risk2", "risk4", "risk0"]


def test_a_risks_objectives_are_chips_in_rank_order(client, start, platform_project):
    http, _ = client
    a = start()
    rate_form(http, platform_project, a, risk2=(5, 4))
    http.post(_api(platform_project, a, "/map"))
    body = body_of(http, platform_project, a)
    rank = {p["objective_id"]: p["rank"] for p in body["priorities"]}
    chips = chips_of(matrix_of(page_of(http, platform_project, a)), "risk2")
    assert sorted(o for o, _ in chips) == sorted(FakeMapper.BY_RISK["risk2"])
    assert [o for o, _ in chips] == sorted((o for o, _ in chips), key=rank.get)


def test_a_chips_colour_says_key_and_who_mapped_it(client, start, platform_project):
    http, _ = client
    a = start()
    rate_form(http, platform_project, a, risk2=(5, 4))
    http.post(_api(platform_project, a, "/map"))                     # risk2 -> O1, O4, O2: all key
    http.post(_api(platform_project, a, "/risks/risk0/mapping"), json={"objective_ids": ["O11", "O30"]})
    matrix = matrix_of(page_of(http, platform_project, a))
    risk2 = dict(chips_of(matrix, "risk2"))
    assert all("co-chip--key" in cls and "co-chip--ai" in cls for cls in risk2.values())
    risk0 = dict(chips_of(matrix, "risk0"))
    assert "co-chip--person" in risk0["O30"] and "co-chip--plain" in risk0["O30"]
    assert "co-chip--ai" in risk0["O11"]                              # kept from the AI


def test_the_legend_names_only_what_differs_from_the_default(client, start, platform_project):
    """Blue is a key objective, a yellow ring an AI suggestion; grey without a ring is the default
    and needs no entry."""
    http, _ = client
    page = page_of(http, platform_project, start())
    legend = re.search(r'<ul class="co-legend">(.*?)</ul>', page, re.S).group(1)
    items = re.findall(r"<li>(.*?)</li>", legend, re.S)
    assert len(items) == 2
    assert 'co-chip co-chip--key' in items[0] and "Key objective" in items[0]
    assert 'co-chip--ai' in items[1] and "Suggested by AI" in items[1]
    assert "Not key" not in legend and "assessor" not in legend


def test_the_ring_is_yellow_and_only_on_ai_suggestions():
    from pathlib import Path

    import aisc_control_objectives
    base = (Path(aisc_control_objectives.__file__).parent / "templates/_base.html.j2").read_text()
    ai = re.search(r"\.co-chip--ai \{([^}]*)\}", base).group(1)
    assert "border-color: #f2c200" in ai
    assert not re.search(r"\.co-chip--person \{", base)


def test_clicking_a_chip_shows_its_name_score_source_and_key(client, start, platform_project):
    http, _ = client
    a = start()
    rate_form(http, platform_project, a, risk2=(5, 4))
    http.post(_api(platform_project, a, "/map"))
    page = page_of(http, platform_project, a)
    pop = re.search(r'<div id="pop-risk2-O1" class="co-chip-pop" popover>(.*?)</div>\s*</span>', page, re.S).group(1)
    assert "Operator oversight capability" in pop
    assert "Score" in pop and "Suggested by AI" in pop
    assert re.search(r'<input type="checkbox" form="keys" name="key" value="O1" checked', pop)


def test_an_objective_under_several_risks_has_one_key_choice(client, start, platform_project):
    http, _ = client
    a = start()
    http.post(_api(platform_project, a, "/map"))
    page = page_of(http, platform_project, a)
    chips = len(re.findall(r'data-objective="O21"', matrix_of(page)))
    assert chips == 2 and page.count('form="keys" name="key" value="O21"') == chips
    assert "keys.js" in page


def test_a_risk_with_nothing_mapped_says_so_and_can_be_edited(client, start, platform_project):
    http, _ = client
    a = start()
    page = page_of(http, platform_project, a)
    matrix = matrix_of(page)
    assert matrix.count("No objective yet.") == 5
    assert re.search(r'<button type="button" class="co-edit" popovertarget="edit-risk0"', matrix)
    assert re.search(rf'<div id="edit-risk0" class="co-edit-pop" popover>\s*<form method="post" '
                     rf'action="/p/{platform_project}/projects/{a}/risks/risk0/mapping"', page)
    assert "Edit this risk's objectives" not in page


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


# Key objectives

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


def test_the_key_form_saves_exactly_the_ticked_objectives(client, start, platform_project):
    http, _ = client
    a = start()
    rate_form(http, platform_project, a, risk2=(5, 4))
    http.post(_api(platform_project, a, "/map"))
    page = page_of(http, platform_project, a)
    assert f'<form id="keys" method="post" action="/p/{platform_project}/projects/{a}/key">' in page
    r = http.post(f"/p/{platform_project}/projects/{a}/key", data={"key": ["O2", "O11"]})
    assert r.status_code == 303
    keys = {p["objective_id"]: p["key"] for p in body_of(http, platform_project, a)["priorities"]}
    assert keys["O2"] and keys["O11"] and not keys["O1"] and not keys["O4"]


# In scope is in the matrix

def test_what_is_in_the_matrix_is_in_scope_in_catalogue_order(client, start, platform_project):
    http, _ = client
    a = start()
    http.post(_api(platform_project, a, "/map"))
    mapped = {oid for oids in FakeMapper.BY_RISK.values() for oid in oids}
    assert body_of(http, platform_project, a)["selected"] == sorted(mapped, key=lambda o: int(o[1:]))
    http.post(_api(platform_project, a, "/risks/risk0/mapping"), json={"objective_ids": ["O30"]})
    selected = body_of(http, platform_project, a)["selected"]
    assert "O30" in selected and "O9" not in selected          # O9 was only risk0's



# The register's dropdowns, and who mapped a row

def test_an_unrated_part_starts_empty_and_a_rated_one_shows_its_value(client, start, platform_project):
    """Ratings start empty (workshop feedback 2026-10-06): no 3 preselected; posting the form with an
    empty part leaves that part unrated."""
    http, _ = client
    a = start()
    rate_form(http, platform_project, a, risk2=(5, 4))
    page = page_of(http, platform_project, a)
    row = re.search(r'<tr id="risk-risk0">(.*?)</tr>', page, re.S).group(1)
    for part in ("impact", "likelihood"):
        select = re.search(rf'<select class="co-select" name="{part}:risk0"[^>]*>(.*?)</select>', row, re.S).group(1)
        assert '<option value="" selected>' in select, part
        assert re.search(r'<option value="\d" selected>', select) is None, part
    rated = re.search(r'<tr id="risk-risk2">(.*?)</tr>', page, re.S).group(1)
    assert re.search(r'<option value="4" selected>4</option>', rated) and 'value=""' not in rated


def test_the_register_counts_risks_with_both_parts_saved(client, start, platform_project):
    http, _ = client
    a = start()
    http.post(_api(platform_project, a, "/ratings"), json={"risk2": {"impact": 5, "likelihood": 4}, "risk0": {"impact": 2}})
    assert "1 of 5 rated" in page_of(http, platform_project, a)


def test_a_chip_mapped_by_a_person_says_assessor(client, start, platform_project):
    http, _ = client
    a = start()
    http.post(_api(platform_project, a, "/risks/risk0/mapping"), json={"objective_ids": ["O5"]})
    page = page_of(http, platform_project, a)
    pop = re.search(r'<div id="pop-risk0-O5" class="co-chip-pop" popover>(.*?)</div>\s*</span>', page, re.S).group(1)
    assert "Mapped by the assessor" in pop
    assert "by hand" not in page.lower()


def test_the_key_script_is_served(client):
    http, _ = client
    r = http.get("/static/keys.js")
    assert r.status_code == 200 and "name=\"key\"" in r.text.replace("'", '"')



# The risk in full

def test_the_register_and_the_matrix_write_the_risk_in_full(client, start, platform_project, graph):
    http, _ = client
    a = start()
    full = next(r for r in body_of(http, platform_project, a)["risks"] if r["id"] == "risk2")["text"]
    page = page_of(http, platform_project, a)
    row = re.search(r'<tr id="risk-risk2">(.*?)</tr>', page, re.S).group(1)
    assert f"<b>{full}</b>" in row
    mrow = re.search(r'<tr class="co-mrow" data-risk="risk2">(.*?)</tr>', page, re.S).group(1)
    assert full in mrow


def test_full_information_folds_under_the_risk_across_the_whole_table(client, start, platform_project):
    http, _ = client
    page = page_of(http, platform_project, start())
    info = re.search(r'<tr class="co-reg-info" data-risk="risk2">\s*<td colspan="5">\s*'
                     r'<details class="co-chain">\s*<summary>Full information</summary>(.*?)</details>', page, re.S)
    assert info and "Source" in info.group(1)
    assert "AIRO chain" not in page
