"""What the assessment pages say about their own state: a rationale the form accepts is saved as typed, the
assessments list counts as the assessment page does, and an AI card that could not be checked is not shown as
unchanged."""
from __future__ import annotations

import re

from aisc_control_objectives import upstream
from step2_support import _api


def _shown(page: str) -> str:
    page = re.sub(r"<(style|script)\b.*?</\1>", "", page, flags=re.S)
    return " ".join(re.sub(r"<[^>]+>", " ", page).split())


def _ratings(http, pid, a) -> dict:
    return http.get(_api(pid, a)).json()["severity"]


def test_a_rationale_of_the_textareas_length_with_line_breaks_is_saved(client, start, platform_project):
    """The browser counts a line break as one character against maxlength=1000 and sends it as CRLF."""
    http, _ = client
    a = start()
    typed = "\r\n".join(["a" * 99] * 10)                     # 999 characters as the textarea counts them
    r = http.post(f"/p/{platform_project}/projects/{a}/severity",
                  data={"impact:risk0": "4", "likelihood:risk0": "3", "comment:risk0": typed,
                        "impact:risk1": "2"})
    assert r.status_code == 303, f"refused: {r.status_code} {r.text[:200]!r}"
    severity = _ratings(http, platform_project, a)
    assert severity["comments"]["risk0"] == "\n".join(["a" * 99] * 10)
    assert severity["impact"]["risk1"] == 2, "the other ratings of the form were not saved"


def test_a_refused_rationale_is_said_in_a_sentence_not_a_validation_dump(client, start, platform_project):
    http, _ = client
    a = start()
    r = http.post(f"/p/{platform_project}/projects/{a}/severity", data={"comment:risk0": "a" * 1200})
    assert r.status_code == 400
    for dump in ("validation error", "[type=", "input_value", "errors.pydantic.dev"):
        assert dump not in r.text, f"the refusal shows the validator's text {dump!r}: {r.text!r}"


def test_the_list_does_not_call_an_assessment_with_one_half_rated_risk_ready_to_link(client, start, platform_project):
    http, _ = client
    a = start()
    http.post(_api(platform_project, a, "/ratings"), json={"risk0": {"impact": 3}})
    shown = _shown(http.get(f"/p/{platform_project}/projects").text)
    assert "Ready to link" not in shown, "one part of one risk rated reads as ready to link"
    assert "0 of 5 rated" in shown, f"the list does not count fully rated risks as the assessment page does: {shown!r}"


def test_the_list_counts_linked_risks_after_one_hand_made_link(client, start, platform_project):
    http, projects = client
    a = start()
    http.post(_api(platform_project, a, "/ratings"),
              json={f"risk{i}": {"impact": 3, "likelihood": 3} for i in range(5)})
    projects.map_by_hand(a, "risk0", ["O1"])
    shown = _shown(http.get(f"/p/{platform_project}/projects").text)
    assert "1 of 5 linked" in shown, f"the list does not say how many risks are linked: {shown!r}"


def test_a_card_that_could_not_be_checked_is_said_so(client, start, platform_project, monkeypatch):
    http, _ = client
    a = start()

    def refused(*args, **kwargs):
        raise upstream.UpstreamDown("qualification answered 401")

    monkeypatch.setattr(upstream, "card_jsonld", refused)
    page = http.get(f"/p/{platform_project}/projects/{a}").text
    shown = _shown(page)
    assert "could not check whether the ai card changed" in shown.lower(), \
        "a card check that failed reads as an unchanged card"
    assert "Reload the AI card" in shown, "no way to read the card again after a failed check"
