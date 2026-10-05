"""The Sets and Profiles pages and their API."""
from __future__ import annotations

import re

import pytest

from aisc_control_objectives.library import FULL_AI_ACT
from test_library import fields


@pytest.fixture
def http(client):
    return client[0]


def api(pid, suffix=""):
    return f"/p/{pid}/api{suffix}"


def made_set(http, pid, code="BNK"):
    r = http.post(api(pid, "/sets"), json={"code": code, "name": "Bank policies", "description": "ours"})
    assert r.status_code == 201, r.text
    return r.json()


# The API

def test_a_set_is_made_with_its_code_and_listed_after_the_built_in_one(http, platform_project):
    made = made_set(http, platform_project)
    assert made["code"] == "BNK" and made["latest"] is None
    listed = http.get(api(platform_project, "/sets")).json()
    assert [(s["code"], s["read_only"]) for s in listed] == [("O", True), ("BNK", False)]


@pytest.mark.parametrize("code", ["bnk", "O", "TOOLONG"])
def test_a_bad_code_is_refused(http, platform_project, code):
    r = http.post(api(platform_project, "/sets"), json={"code": code, "name": "x"})
    assert r.status_code == 422 and "code" in r.text


def test_objectives_are_written_published_and_versioned(http, platform_project):
    s = made_set(http, platform_project)
    base = api(platform_project, f"/sets/{s['id']}")
    r = http.post(base + "/objectives", json=fields())
    assert r.status_code == 201 and r.json()["id"] == "BNK1"
    assert http.post(base + "/objectives", json=fields(dimension="R99")).status_code == 422
    assert http.put(base + "/objectives/BNK1", json=fields(label="Sign-off")).status_code == 200
    http.post(base + "/objectives", json=fields(label="Second"))
    assert http.post(base + "/objectives/BNK2/retire").status_code == 200
    r = http.post(base + "/publish")
    assert r.status_code == 200 and r.json()["version"] == 1
    assert http.post(base + "/publish").status_code == 422
    got = http.get(base).json()
    assert got["set"]["latest"] == 1
    assert [(o["id"], o["retired"]) for o in got["draft"]] == [("BNK1", False), ("BNK2", True)]
    assert [o["sub_requirement_label"] for o in http.get(base + "/versions/1").json()] == ["Sign-off"]


def test_only_an_unpublished_set_is_deleted(http, platform_project):
    s = made_set(http, platform_project)
    base = api(platform_project, f"/sets/{s['id']}")
    http.post(base + "/objectives", json=fields())
    http.post(base + "/publish")
    assert http.delete(base).status_code == 422
    other = made_set(http, platform_project, "TMP")
    assert http.delete(api(platform_project, f"/sets/{other['id']}")).status_code == 204
    assert http.get(api(platform_project, "/sets/nope")).status_code == 404


def test_profiles_are_made_and_saved_as_versions(http, platform_project):
    s = made_set(http, platform_project)
    base = api(platform_project, f"/sets/{s['id']}")
    http.post(base + "/objectives", json=fields())
    http.post(base + "/publish")
    listed = http.get(api(platform_project, "/profiles")).json()
    assert listed[0]["id"] == FULL_AI_ACT and listed[0]["read_only"]
    r = http.post(api(platform_project, "/profiles"), json={"name": "Bank", "objective_ids": ["O1", "BNK1"]})
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    assert http.post(api(platform_project, "/profiles"), json={"name": "Bad", "objective_ids": ["BNK9"]}).status_code == 422
    r = http.post(api(platform_project, f"/profiles/{pid}/versions"), json={"objective_ids": ["O1", "O2", "BNK1"]})
    assert r.status_code == 200 and r.json()["current"]["number"] == 2 and r.json()["dropped"] == []
    got = http.get(api(platform_project, f"/profiles/{pid}")).json()
    assert got["picks"] == ["O1", "O2", "BNK1"] and got["pins"] == {"BNK": 1}


# The pages

def test_the_menu_has_sets_and_profiles(http, platform_project):
    page = http.get(f"/p/{platform_project}/sets").text
    assert f'href="/p/{platform_project}/sets"' in page and f'href="/p/{platform_project}/profiles"' in page


def test_a_set_is_made_and_written_on_its_pages(http, platform_project):
    page = http.get(f"/p/{platform_project}/sets").text
    assert 'name="code"' in page and 'pattern="[A-Z]{2,6}"' in page and "AI Act control objectives" in page
    r = http.post(f"/p/{platform_project}/sets", data={"code": "BNK", "name": "Bank policies", "description": ""})
    assert r.status_code == 303
    url = r.headers["location"]
    set_id = url.rsplit("/", 1)[1]
    r = http.post(f"/p/{platform_project}/sets/{set_id}/objectives", data=fields())
    assert r.status_code == 303
    page = http.get(f"/p/{platform_project}/sets/{set_id}").text
    assert "BNK1" in page and "Model change approval" in page and "not published yet" in page
    assert http.post(f"/p/{platform_project}/sets/{set_id}/objectives/BNK1",
                     data=fields(label="Sign-off")).status_code == 303
    assert http.post(f"/p/{platform_project}/sets/{set_id}/publish").status_code == 303
    page = http.get(f"/p/{platform_project}/sets/{set_id}").text
    assert "Sign-off" in page and "Version 1" in page
    bad = http.post(f"/p/{platform_project}/sets/{set_id}/objectives", data=fields(text=""))
    assert bad.status_code == 400 and "text" in bad.text


def test_a_profile_is_picked_on_its_pages(http, platform_project):
    s = made_set(http, platform_project)
    base = api(platform_project, f"/sets/{s['id']}")
    http.post(base + "/objectives", json=fields())
    http.post(base + "/publish")
    page = http.get(f"/p/{platform_project}/profiles").text
    assert "Full AI Act" in page and f'href="/p/{platform_project}/profiles/new"' in page
    picker = http.get(f"/p/{platform_project}/profiles/new").text
    assert 'name="objective" value="O1"' in picker and 'name="objective" value="BNK1"' in picker
    r = http.post(f"/p/{platform_project}/profiles", data={"name": "Bank", "description": "", "objective": ["O1", "BNK1"]})
    assert r.status_code == 303
    profile_url = r.headers["location"]
    page = http.get(profile_url).text
    assert re.search(r'name="objective" value="BNK1" checked', page) and "Version 1" in page
    # a newer set version is offered
    http.put(base + "/objectives/BNK1", json=fields(label="Sign-off"))
    http.post(base + "/publish")
    page = http.get(profile_url).text
    assert "BNK version 2 is out" in page
    assert http.post(profile_url, data={"name": "Bank", "description": "", "objective": ["O1", "BNK1"]}).status_code == 303
    assert "Version 2" in http.get(profile_url).text


def test_the_full_ai_act_profile_reads_but_does_not_change(http, platform_project):
    page = http.get(f"/p/{platform_project}/profiles/{FULL_AI_ACT}").text
    assert "Full AI Act" in page and "O50" in page and 'type="submit"' not in page
