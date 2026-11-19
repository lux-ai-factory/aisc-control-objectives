"""'Suggest with AI' exists only where the install turns AI actions on: AISC_AI_ACTIONS=on. Unset, or any
other value, the assessment page has no such button and both routes that start a model run refuse, so a
hidden button cannot be pressed by posting its form by hand."""
from __future__ import annotations

import re

import pytest

from aisc_control_objectives.settings import ai_actions_on


def _counting(mapper, monkeypatch) -> list:
    """The risks the model is asked about, from here on."""
    asked = []
    propose = mapper.propose
    monkeypatch.setattr(mapper, "propose", lambda risk, findings=(): asked.append(risk.id) or propose(risk, findings))
    return asked


def _shown(page: str) -> str:
    page = re.sub(r"<(style|script)\b.*?</\1>", "", page, flags=re.S)
    return " ".join(re.sub(r"<[^>]+>", " ", page).split())


@pytest.mark.parametrize("value, on", [
    (None, False), ("", False), ("off", False), ("no", False), ("true", False),
    ("on", True), ("ON", True), (" on ", True),
])
def test_ai_actions_are_on_only_when_the_install_says_on(value, on):
    env = {} if value is None else {"AISC_AI_ACTIONS": value}
    assert ai_actions_on(env) is on


def test_with_ai_actions_off_the_page_has_no_suggest_button(client, start, platform_project, monkeypatch):
    http, _ = client
    a = start()
    monkeypatch.delenv("AISC_AI_ACTIONS", raising=False)
    page = http.get(f"/p/{platform_project}/projects/{a}").text
    assert "Suggest with AI" not in _shown(page)
    assert f"/projects/{a}/map\"" not in page


def test_with_ai_actions_on_the_page_has_the_suggest_button(client, start, platform_project, monkeypatch):
    http, _ = client
    a = start()
    monkeypatch.setenv("AISC_AI_ACTIONS", "on")
    page = http.get(f"/p/{platform_project}/projects/{a}").text
    assert "Suggest with AI" in _shown(page)


def test_with_ai_actions_off_the_form_route_refuses_and_runs_no_model(client, start, platform_project, mapper,
                                                                      monkeypatch):
    http, _ = client
    a = start()
    monkeypatch.delenv("AISC_AI_ACTIONS", raising=False)
    asked = _counting(mapper, monkeypatch)
    r = http.post(f"/p/{platform_project}/projects/{a}/map", follow_redirects=False)
    assert r.status_code == 403, (r.status_code, r.text)
    assert "turned off" in _shown(r.text)
    assert asked == []


def test_with_ai_actions_off_the_api_route_refuses_and_runs_no_model(client, start, platform_project, mapper,
                                                                     monkeypatch):
    http, _ = client
    a = start()
    monkeypatch.delenv("AISC_AI_ACTIONS", raising=False)
    asked = _counting(mapper, monkeypatch)
    r = http.post(f"/p/{platform_project}/api/projects/{a}/map")
    assert r.status_code == 403, (r.status_code, r.text)
    assert "turned off" in r.json()["detail"]
    assert asked == []
