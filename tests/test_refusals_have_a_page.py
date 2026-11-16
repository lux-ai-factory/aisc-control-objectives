"""A refused form is said on a page of the site, with its reason and a way back, never as a bare text line or
as raw JSON. A refusal is worded for what the person did, and an answer that refused is not called no answer."""
from __future__ import annotations

import re

import httpx

from aisc_control_objectives.library import FAIRNESS_OVERSIGHT


def _shown(page: str) -> str:
    page = re.sub(r"<(style|script)\b.*?</\1>", "", page, flags=re.S)
    return " ".join(re.sub(r"<[^>]+>", " ", page).split())


def _is_a_page(response) -> bool:
    return response.headers.get("content-type", "").startswith("text/html") and "<html" in response.text.lower()


def test_a_refused_risk_mapping_answers_a_page_with_a_link_back(client, start, platform_project):
    http, _ = client
    a = start()
    r = http.post(f"/p/{platform_project}/projects/{a}/risks/risk0/mapping", data={"objective": "O999"})
    assert r.status_code == 400
    assert _is_a_page(r), f"a bare refusal: {r.headers.get('content-type')} {r.text!r}"
    assert f'href="/p/{platform_project}/projects/{a}' in r.text, "the refusal page has no link back to the assessment"
    assert "O999" in _shown(r.text), "the refusal page does not say what was refused"


def test_a_refused_profile_form_answers_a_page_with_a_link_back(client, platform_project):
    http, _ = client
    r = http.post(f"/p/{platform_project}/profiles", data={"name": "", "objective": ["O1"]})
    assert r.status_code == 400
    assert _is_a_page(r), f"a bare refusal: {r.headers.get('content-type')} {r.text!r}"
    assert f'href="/p/{platform_project}/profiles' in r.text, "the refusal page has no link back to the profiles"


def test_an_unknown_assessment_answers_a_page_not_json(client, platform_project):
    http, _ = client
    r = http.get(f"/p/{platform_project}/projects/no-such-assessment")
    assert r.status_code == 404
    assert _is_a_page(r), f"an unknown id answers {r.headers.get('content-type')}: {r.text!r}"
    assert f'href="/p/{platform_project}/projects"' in r.text, "the page has no link back to the assessments"


def test_a_hand_mapping_refused_by_a_profile_switch_is_worded_for_the_edit(client, start, platform_project,
                                                                          monkeypatch):
    """Two tabs: one switches the profile while the other saves a risk's objectives. The person did not ask
    the AI anything, so 'suggest again' is the wrong advice; they go back to that risk with a notice."""
    http, projects = client
    a = start()
    stored = projects._repository                  # the repository the service writes through
    save = stored.save_risk_mapping

    def switched_meanwhile(*args, **kwargs):
        monkeypatch.setattr(stored, "save_risk_mapping", save)
        projects.use_profile(a, FAIRNESS_OVERSIGHT)
        return save(*args, **kwargs)

    monkeypatch.setattr(stored, "save_risk_mapping", switched_meanwhile)
    r = http.post(f"/p/{platform_project}/projects/{a}/risks/risk0/mapping", data={"objective": "O1"})
    assert "suggest again" not in r.text, f"the edit is told to suggest again: {r.text!r}"
    assert r.status_code == 303 and r.headers["location"].endswith("#map-risk0"), (r.status_code, r.text)
    page = _shown(http.get(r.headers["location"]).text)
    assert "profile changed" in page, "the page does not say that the profile changed meanwhile"


def _answers(monkeypatch, status: int | None):
    """The platform answers `status`, or (None) does not answer at all."""
    monkeypatch.setenv("PLATFORM_URL", "http://platform.invalid")
    monkeypatch.setenv("QUALIFICATION_URL", "http://qualification.invalid")

    def get(url, headers=None, timeout=None):
        if status is None:
            raise httpx.ConnectError("connection refused")
        return httpx.Response(status, json={"detail": "refused"}, request=httpx.Request("GET", url))

    # every call of the service client: the one GET of upstream
    monkeypatch.setattr(httpx.Client, "request",
                        lambda self, method, url, headers=None, timeout=None, **kw: get(url, headers, timeout))


def test_start_assessment_says_a_refusal_is_a_refusal(client, platform_project, monkeypatch):
    http, _ = client
    _answers(monkeypatch, 401)
    r = http.post(f"/p/{platform_project}/projects", data={"name": ""})
    shown = _shown(r.text)
    assert "did not answer" not in shown, f"a 401 is called no answer: {shown!r}"
    assert "sign in again" in shown.lower(), f"the refusal does not say to sign in again: {shown!r}"


def test_start_assessment_says_no_answer_once(client, platform_project, monkeypatch):
    http, _ = client
    _answers(monkeypatch, None)
    r = http.post(f"/p/{platform_project}/projects", data={"name": ""})
    shown = _shown(r.text).lower()
    assert shown.count("did not answer") == 1, f"'did not answer' is said {shown.count('did not answer')} times: {shown!r}"
