"""The objective profile and set pages: Save says what it did, and leaving with unsaved picks or a typed
objective asks first, as the assessment page does."""
from __future__ import annotations

import re

import pytest


def _shown(page: str) -> str:
    page = re.sub(r"<(style|script)\b.*?</\1>", "", page, flags=re.S)
    return " ".join(re.sub(r"<[^>]+>", " ", page).split())


@pytest.fixture
def http(client):
    return client[0]


def _profile(http, pid) -> str:
    r = http.post(f"/p/{pid}/api/profiles", json={"name": "Bank", "objective_ids": ["O1", "O2"]})
    assert r.status_code == 201, r.text
    return r.json()["id"]


@pytest.mark.parametrize("name,picks,said", [
    ("Bank", ["O1", "O2"], "Nothing changed"),
    ("Bank lending", ["O1", "O2"], "Name saved"),
    ("Bank", ["O1", "O2", "O3"], "Saved as version 2"),
])
def test_saving_a_profile_says_what_was_saved(http, platform_project, name, picks, said):
    profile = _profile(http, platform_project)
    r = http.post(f"/p/{platform_project}/profiles/{profile}",
                  data={"name": name, "description": "", "objective": picks})
    assert r.status_code == 303, r.text
    shown = _shown(http.get(r.headers["location"]).text)
    assert said in shown, f"after Save the page does not say {said!r}"


def _set(http, pid) -> str:
    r = http.post(f"/p/{pid}/api/sets", json={"code": "BNK", "name": "Bank policies", "description": "ours"})
    assert r.status_code == 201, r.text
    return r.json()["id"]


@pytest.mark.parametrize("page", ["profile", "new profile", "set", "sets"])
def test_the_library_pages_ask_before_unsaved_changes_are_left(http, platform_project, page):
    path = {
        "profile": lambda: f"/profiles/{_profile(http, platform_project)}",
        "new profile": lambda: "/profiles/new",
        "set": lambda: f"/sets/{_set(http, platform_project)}",
        "sets": lambda: "/sets",
    }[page]()
    html = http.get(f"/p/{platform_project}{path}").text
    assert "<form" in html, f"the {page} page has no form to guard"
    assert re.search(r'<script src="[^"]*/static/unsaved\.js"', html), f"the {page} page loads no leave guard"
