"""The step 2 page: every part folds, a severity can carry a comment, and the AI
button explains itself in a pop-up instead of a sentence beside it."""
from __future__ import annotations

import re

from step2_support import _api


def _page(http, platform_project, assessment) -> str:
    response = http.get(f"/p/{platform_project}/projects/{assessment}")
    assert response.status_code == 200, response.text
    return response.text


# Every part folds

def test_every_part_of_the_page_folds_and_starts_open(client, start, platform_project):
    http, _ = client
    assessment = start()
    http.post(_api(platform_project, assessment, "/map"))
    page = _page(http, platform_project, assessment)
    folds = re.findall(r'<details class="qf-section co-fold[^"]*"[^>]*>\s*<summary class="co-macro-head">', page)
    # the risk register and the matrix
    assert len(folds) == 2, len(folds)
    assert all(" open" in f for f in folds)
    assert '<div class="qf-section">' not in page and '<section class="qf-section' not in page


def test_the_register_and_the_matrix_keep_their_anchors(client, start, platform_project):
    http, _ = client
    assessment = start()
    http.post(_api(platform_project, assessment, "/map"))
    page = _page(http, platform_project, assessment)
    assert re.search(r'<details class="qf-section co-fold" open id="register">', page)
    assert re.search(r'<details class="qf-section co-fold" open id="matrix">', page)


# A comment on a severity

def test_the_ranking_form_saves_a_comment_with_the_severity(client, start, platform_project):
    http, _ = client
    assessment = start()
    response = http.post(f"/p/{platform_project}/projects/{assessment}/severity",
                         data={"impact:risk2": "5", "comment:risk2": "Officers approve 98% unread.", "comment:risk0": ""})
    assert response.status_code == 303
    body = http.get(_api(platform_project, assessment)).json()
    assert body["severity"]["impact"]["risk2"] == 5
    assert body["severity"]["comments"] == {"risk2": "Officers approve 98% unread."}
    page = _page(http, platform_project, assessment)
    assert 'name="comment:risk2"' in page and "Officers approve 98% unread." in page


def test_a_comment_is_optional_and_clearing_it_removes_it(client, start, platform_project):
    http, _ = client
    assessment = start()
    url = f"/p/{platform_project}/projects/{assessment}/severity"
    http.post(url, data={"impact:risk2": "4", "comment:risk2": "first"})
    http.post(url, data={"impact:risk2": "4", "comment:risk2": "  "})
    assert http.get(_api(platform_project, assessment)).json()["severity"]["comments"] == {}


def test_the_api_saves_comments(client, start, platform_project):
    http, _ = client
    assessment = start()
    response = http.post(_api(platform_project, assessment, "/severity-comments"),
                         json={"risk1": "Only one branch uses it."})
    assert response.status_code == 200, response.text
    assert response.json()["severity"]["comments"] == {"risk1": "Only one branch uses it."}


def test_a_comment_on_an_unknown_risk_or_too_long_is_refused(client, start, platform_project):
    http, _ = client
    assessment = start()
    api = _api(platform_project, assessment, "/severity-comments")
    assert http.post(api, json={"nope": "x"}).status_code == 422
    assert http.post(api, json={"risk1": "x" * 1001}).status_code == 422
    form = http.post(f"/p/{platform_project}/projects/{assessment}/severity", data={"comment:risk1": "x" * 1001})
    assert form.status_code == 400


def test_the_register_row_carries_the_rationale(client, start, platform_project):
    http, _ = client
    assessment = start()
    http.post(f"/p/{platform_project}/projects/{assessment}/severity",
              data={"impact:risk4": "4", "comment:risk4": "Bureau feed has no signature check."})
    page = _page(http, platform_project, assessment)
    row = re.search(r'<tr id="risk-risk4">(.*?)</tr>', page, re.S).group(1)
    assert "Bureau feed has no signature check." in row


def test_an_older_version_shows_the_comment_read_only(client, start, platform_project):
    http, _ = client
    first = start(1)
    http.post(f"/p/{platform_project}/projects/{first}/severity", data={"comment:risk2": "kept"})
    start(2)
    page = _page(http, platform_project, first)
    assert "kept" in page and 'name="comment:risk2"' not in page
    assert http.post(_api(platform_project, first, "/severity-comments"), json={"risk2": "x"}).status_code == 409


# The AI button explains itself

def test_the_ai_button_has_a_question_mark_pop_up_and_no_sentence(client, start, platform_project):
    http, _ = client
    assessment = start()
    http.post(_api(platform_project, assessment, "/map"))
    page = _page(http, platform_project, assessment)
    assert "co-map-warn" not in page
    assert re.search(r'<button type="button" class="co-help" popovertarget="ai-help"[^>]*>\?</button>', page)
    popup = re.search(r'<div id="ai-help" class="co-help-pop" popover>(.*?)</div>', page, re.S).group(1)
    assert "replaces the whole matrix" in popup and "quote" in popup


def test_the_register_starts_with_every_rating_empty(client, start, platform_project):
    http, _ = client
    assessment = start()
    page = http.get(f"/p/{platform_project}/projects/{assessment}").text
    register = page.split('id="register"', 1)[1].split('id="matrix"', 1)[0]
    assert '<option value="" selected>' in register
    assert re.search(r'<option value="\d" selected>', register) is None
    assert "Not rated" in register and "counts 3" not in register
