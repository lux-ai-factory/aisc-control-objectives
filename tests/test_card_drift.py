"""The card of an assessment's version can change after the assessment started: qualification carries a
card's review into place and corrections change a version in place. The assessment keeps the card it
started from until someone updates it; the page says when the card qualification serves differs, and
"Update" re-reads it and carries the work over as a new version's assessment does. Never silently.
"""
from __future__ import annotations

import hashlib

import pytest
from fastapi.testclient import TestClient

from aisc_control_objectives.api.app import create_app
from aisc_control_objectives.projects import Projects
from test_assessment_of_a_version import AUTH, Upstream, _assessment_id, _start
from test_ledger import (  # noqa: F401 (ledger_db is autouse here too)
    archive,
    chain_breaks,
    ledger_db,
    outbox,
)


@pytest.fixture()
def upstream(monkeypatch):
    stub = Upstream()
    monkeypatch.setenv("PLATFORM_URL", stub.url)
    monkeypatch.setenv("QUALIFICATION_URL", stub.url + "/qualification")
    yield stub
    stub.close()


@pytest.fixture()
def card_v1(fixtures_dir) -> str:
    return (fixtures_dir / "mcas.ontology.jsonld").read_text()


@pytest.fixture()
def card_v2(card_v1) -> str:
    """The same card corrected in place: risk4 gone, risk0 reworded."""
    import json

    graph = [node for node in json.loads(card_v1) if "#risk4" not in node["@id"]]
    for node in graph:
        if node["@id"].endswith("#risk0"):
            for key, values in node.items():
                if key.endswith("#fullLabel"):
                    values[0]["@value"] = "Applicant wrongly ranked high risk, as v2 says it"
    return json.dumps(graph, indent=1)


def _without_measures(card: str) -> str:
    """The card as qualification serves it once it no longer writes the provider's measures: the same
    graph without its RiskControl nodes, serialised afresh (so its bytes and digest differ)."""
    import json

    graph = [node for node in json.loads(card)
             if "https://w3id.org/airo#RiskControl" not in (node.get("@type") or [])]
    return json.dumps(list(reversed(graph)), indent=4)


@pytest.fixture()
def card_v1_without_measures(card_v1) -> str:
    return _without_measures(card_v1)


@pytest.fixture()
def client(repository, objectives, upstream):
    from test_assessment_of_a_version import _Mapper

    app = create_app(objectives, Projects(repository, objectives, _Mapper(), model="fake/model"))
    return TestClient(app, raise_server_exceptions=False, follow_redirects=False)


HEADERS = {"Authorization": AUTH}
BANNER = "The AI card of v1 changed since this assessment started"


def _page(client, project, assessment):
    response = client.get(f"/p/{project}/projects/{assessment}", headers=HEADERS)
    assert response.status_code == 200, response.text
    return " ".join(response.text.split())


def _worked_v1(client, repository, platform_project, system_version, upstream, card_v1):
    v1 = system_version(platform_project, 1)
    upstream.version(platform_project, v1, 1, card_v1)
    a = _assessment_id(_start(client, platform_project))
    client.post(f"/p/{platform_project}/api/projects/{a}/ratings", headers=HEADERS,
                json={"risk0": {"impact": 4, "likelihood": 4}, "risk2": {"impact": 2, "likelihood": 3}})
    client.post(f"/p/{platform_project}/api/projects/{a}/risks/risk1/mapping", headers=HEADERS,
                json={"objective_ids": ["O21"]})
    return v1, a


def test_an_unchanged_card_shows_no_banner(client, repository, platform_project, system_version, upstream,
                                           card_v1):
    _v1, a = _worked_v1(client, repository, platform_project, system_version, upstream, card_v1)
    assert BANNER not in _page(client, platform_project, a)


def test_a_changed_card_is_named_and_updated_only_when_asked(client, repository, platform_project,
                                                             system_version, upstream, card_v1, card_v2):
    v1, a = _worked_v1(client, repository, platform_project, system_version, upstream, card_v1)
    upstream.cards[v1] = card_v2                  # risk0 reworded, risk4 gone
    page = _page(client, platform_project, a)
    assert BANNER in page and f'action="/p/{platform_project}/projects/{a}/card"' in page
    assert ">Reload the AI card</button>" in page and ">Update</button>" not in page
    # never refreshed by itself: the page read the card and changed nothing
    assert repository.get(a).digest == hashlib.sha256(card_v1.encode()).hexdigest()

    updated = client.post(f"/p/{platform_project}/projects/{a}/card", headers=HEADERS)
    assert updated.status_code == 303
    record = repository.get(a)
    assert record.digest == hashlib.sha256(card_v2.encode()).hexdigest() and record.jsonld == card_v2
    assert [r.id for r in record.ontology.risks] == ["risk0", "risk1", "risk2", "risk3"]
    assert record.ontology.risks[0].text == "Applicant wrongly ranked high risk, as v2 says it"
    assert (record.severity.impact, record.severity.likelihood) == ({"risk2": 2}, {"risk2": 3})
    assert [m.objective_id for m in record.mapping_run.mappings["risk1"].objectives] == ["O21"]
    assert record.by_hand == {"risk1"} and record.selected == ["O21"]
    assert record.carried["updated"] is True and record.carried["risks"] == 2
    page = _page(client, platform_project, a)
    assert BANNER not in page
    assert "Carried from the earlier card of v1: ratings and links of 2 risks." in page
    assert "Stayed on the earlier card: An applicant is wrongly ranked as high risk and routed to Reject" in page


def test_the_api_updates_too(client, repository, platform_project, system_version, upstream, card_v1, card_v2):
    v1, a = _worked_v1(client, repository, platform_project, system_version, upstream, card_v1)
    upstream.cards[v1] = card_v2
    body = client.post(f"/p/{platform_project}/api/projects/{a}/card", headers=HEADERS).json()
    assert body["digest"] == hashlib.sha256(card_v2.encode()).hexdigest() and body["carried"]["updated"] is True


def test_updating_an_unchanged_card_changes_nothing(client, repository, platform_project, system_version,
                                                    upstream, card_v1):
    _v1, a = _worked_v1(client, repository, platform_project, system_version, upstream, card_v1)
    before = repository.get(a)
    assert client.post(f"/p/{platform_project}/projects/{a}/card", headers=HEADERS).status_code == 303
    after = repository.get(a)
    assert (after.updated_at, after.carried) == (before.updated_at, before.carried)
    assert outbox(repository, "assessment.card") == []


def test_an_older_versions_assessment_only_shows_the_banner(client, repository, platform_project,
                                                            system_version, upstream, card_v1, card_v2):
    v1, a = _worked_v1(client, repository, platform_project, system_version, upstream, card_v1)
    v2 = system_version(platform_project, 2)
    upstream.version(platform_project, v2, 2, card_v1)
    upstream.cards[v1] = card_v2
    page = _page(client, platform_project, a)
    assert BANNER in page and f"/projects/{a}/card" not in page
    assert client.post(f"/p/{platform_project}/projects/{a}/card", headers=HEADERS).status_code == 409
    assert repository.get(a).digest == hashlib.sha256(card_v1.encode()).hexdigest()


def test_qualification_down_shows_the_page_without_a_banner(client, repository, platform_project,
                                                            system_version, upstream, card_v1):
    _v1, a = _worked_v1(client, repository, platform_project, system_version, upstream, card_v1)
    upstream.down.add("qualification")
    assert BANNER not in _page(client, platform_project, a)


def test_an_update_records_what_changed_and_keeps_the_chains(client, repository, platform_project,
                                                             system_version, upstream, card_v1, card_v2):
    v1, a = _worked_v1(client, repository, platform_project, system_version, upstream, card_v1)
    upstream.cards[v1] = card_v2
    client.post(f"/p/{platform_project}/projects/{a}/card", headers=HEADERS)
    [updated] = outbox(repository, "assessment.card_updated")
    assert updated["item_id"] == a
    assert updated["details"] == {"card_version": v1, "digest_before": hashlib.sha256(card_v1.encode()).hexdigest(),
                                  "digest_after": hashlib.sha256(card_v2.encode()).hexdigest(), "risks": 4,
                                  "carried_risks": 2}
    # risk0 was reworded: its rating stayed on the earlier card, and its item says so
    unrated = [r for r in outbox(repository, "risk.rated") if r["item_id"] == f"{a}/risks/risk0"][-1]
    assert unrated["after"] == {"impact": None, "likelihood": None}
    client.post(f"/p/{platform_project}/api/projects/{a}/ratings", headers=HEADERS,
                json={"risk0": {"impact": 1, "likelihood": 1}, "risk2": {"impact": 5, "likelihood": 5}})
    assert chain_breaks(outbox(repository)) == []
    # what the update replaced is kept, as for any mapping change
    assert ("card", "risk1", "O21") in {(k["reason"], r["risk"], r["objective"]) for k in archive(repository)
                                         for r in k["rows"]}


def test_a_card_that_only_lost_the_providers_measures_has_not_changed(client, repository, platform_project,
                                                                       system_version, upstream, card_v1,
                                                                       card_v1_without_measures):
    """The assessment started from a card that still named a measure per risk; qualification now serves
    the same card without them. Step 2 never used those nodes, so the assessor is not told it changed."""
    v1, a = _worked_v1(client, repository, platform_project, system_version, upstream, card_v1)
    assert "airo#RiskControl" in card_v1 and "airo#RiskControl" not in card_v1_without_measures
    upstream.cards[v1] = card_v1_without_measures
    assert BANNER not in _page(client, platform_project, a)


def test_a_real_change_still_shows_once_the_measures_are_gone(client, repository, platform_project,
                                                             system_version, upstream, card_v1, card_v2):
    """Losing the measures hides nothing else: the corrected card (risk0 reworded, risk4 gone) without
    its measures is still a changed card."""
    v1, a = _worked_v1(client, repository, platform_project, system_version, upstream, card_v1)
    upstream.cards[v1] = _without_measures(card_v2)
    assert BANNER in _page(client, platform_project, a)
