"""'Suggest with AI' on the assessment page: it shows that it is busy and cannot be sent twice, it sends the
person to the page where the model is really chosen, and when it fails, wholly or for some risks, it says so
on the assessment page, for each risk it could not suggest for."""
from __future__ import annotations

import re

from step2_support import _api


def _shown(page: str) -> str:
    page = re.sub(r"<(style|script)\b.*?</\1>", "", page, flags=re.S)
    return " ".join(re.sub(r"<[^>]+>", " ", page).split())


def _page(http, pid, a) -> str:
    r = http.get(f"/p/{pid}/projects/{a}")
    assert r.status_code == 200, r.text
    return r.text


def test_a_sent_suggestion_disables_its_button_until_the_page_comes_back(client, start, platform_project):
    """A second click would start a second paid run: some script of the page disables the Suggest with AI
    button once its form is sent, and the page says the run can take minutes."""
    http, _ = client
    a = start()
    page = _page(http, platform_project, a)
    scripts = re.findall(r'<script[^>]*src="([^"]+)"', page)
    bodies = [http.get(src).text for src in scripts]
    inline = re.findall(r"<script\b[^>]*>(.*?)</script>", page, flags=re.S)
    assert any(re.search(r"\.disabled\s*=\s*true|setAttribute\(\s*['\"]disabled", body) for body in bodies + inline), \
        f"no script of the page disables a sent button (scripts: {scripts})"
    assert "can take minutes" in _shown(page), "the page does not say that suggesting can take minutes"


def test_a_run_that_finds_the_assessment_changed_goes_back_to_the_page_and_says_so(client, start, platform_project,
                                                                                  monkeypatch):
    """Another change (a person's link, or another run) was saved while the AI was suggesting. This run's
    suggestions are not applied; the person is told so on the assessment page, not told to suggest again."""
    http, projects = client
    a = start()
    stored = projects._repository                  # the repository the service writes through
    save = stored.save_mapping_run

    def linked_meanwhile(*args, **kwargs):
        monkeypatch.setattr(stored, "save_mapping_run", save)
        projects.map_by_hand(a, "risk1", ["O1"])
        return save(*args, **kwargs)

    monkeypatch.setattr(stored, "save_mapping_run", linked_meanwhile)
    r = http.post(f"/p/{platform_project}/projects/{a}/map")
    assert "suggest again" not in r.text, f"the person is told to suggest again: {r.text!r}"
    assert r.status_code == 303 and f"/projects/{a}" in r.headers["location"], (r.status_code, r.text)
    shown = _shown(http.get(r.headers["location"]).text)
    assert "not applied" in shown, "the page does not say that this run's suggestions were not applied"


def test_the_help_names_the_page_where_the_model_is_chosen(client, start, platform_project):
    http, _ = client
    a = start()
    help_text = re.search(r'<div id="ai-help"[^>]*>(.*?)</div>', _page(http, platform_project, a), re.S).group(1)
    shown = _shown(help_text)
    assert "AI assistants" not in shown, f"the help names a menu that no longer exists: {shown!r}"
    assert "Connectivity" in shown and "Configurator agents" in shown, f"the help does not name Connectivity: {shown!r}"


def test_a_run_with_no_model_goes_back_to_the_page_and_names_connectivity(client, start, platform_project, mapper,
                                                                          monkeypatch):
    http, _ = client
    a = start()

    def no_key(risk, findings=()):
        raise ValueError("openai needs OPENAI_API_KEY: set it in the environment, or pick another model")

    monkeypatch.setattr(mapper, "propose", no_key)
    r = http.post(f"/p/{platform_project}/projects/{a}/map")
    assert r.status_code == 303, f"a failed run answers {r.status_code} {r.headers.get('content-type')}: {r.text!r}"
    shown = _shown(http.get(r.headers["location"]).text)
    assert "Connectivity" in shown, "the page does not send the person to Connectivity"


def test_each_risk_the_ai_could_not_reach_says_so_in_its_row(client, start, platform_project, mapper, monkeypatch):
    http, _ = client
    a = start()
    propose = mapper.propose

    def fails_on_risk1(risk, findings=()):
        if risk.id == "risk1":
            raise ConnectionError("provider unreachable")
        return propose(risk, findings)

    monkeypatch.setattr(mapper, "propose", fails_on_risk1)
    assert http.post(_api(platform_project, a, "/map")).status_code == 200
    page = _page(http, platform_project, a)
    row = re.search(r'<tr class="co-mrow" data-risk="risk1".*?</tr>', page, re.S).group(0)
    assert "could not be reached" in _shown(row), f"the risk whose model call failed says nothing: {_shown(row)!r}"
    banner = re.search(r'id="matrix".*?<div class="error">(.*?)</div>', page, re.S)
    assert banner and "1 of 5" in _shown(banner.group(1)), \
        f"the banner does not count the risks not suggested: {banner and _shown(banner.group(1))!r}"
