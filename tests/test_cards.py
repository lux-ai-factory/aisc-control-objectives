"""Uploading an AI card: the extractor proposes a profile, the rules decide,
the person confirms. Driven end to end through the HTTP surface with a fake
extractor, no model.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from wizard.api.app import create_app
from wizard.config import RunConfig
from wizard.models.system_card import SystemCard

from helpers import CREDIT, FakeExtractor  # noqa: E402


@pytest.fixture()
def client(objectives):
    return TestClient(create_app(objectives, base_config=RunConfig(), extractor=FakeExtractor()))


def _upload(client, raw) -> dict:
    response = client.post("/api/cards", json=raw)
    assert response.status_code == 201, response.text
    return response.json()


class TestUploadApi:
    def test_an_uploaded_card_comes_back_assessed(self, client, mcas_raw):
        record = _upload(client, mcas_raw)
        assert record["card"]["system_name"] == "MicroCredit Assist Score (MCAS)"
        assert record["profile"]["high_risk"]["value"] == "yes"
        assert record["profile"]["high_risk"]["quote"] == CREDIT
        assert record["run"]["stop"] == "clean"
        assert len(record["verdicts"]) == 50

    def test_the_mcas_card_puts_all_fifty_in_scope_two_of_them_non_binding(self, client, mcas_raw):
        verdicts = _upload(client, mcas_raw)["verdicts"]
        assert sum(v["applies"] == "yes" for v in verdicts) == 50
        assert {v["objective_id"] for v in verdicts if v["non_binding"]} == {"R6.1", "R6.2"}

    def test_the_form_upload_does_not_run_the_model_on_the_event_loop(self, client):
        """assess_card blocks for the model's duration; an async handler would
        freeze /health and every other request for that long."""
        import inspect

        route = next(r for r in client.app.routes if getattr(r, "path", "") == "/cards" and "POST" in r.methods)
        assert not inspect.iscoroutinefunction(route.endpoint)

    def test_a_fresh_assessment_is_provisional_until_confirmed(self, client, mcas_raw):
        record = _upload(client, mcas_raw)
        assert record["confirmed"] is False
        assert all(v["provisional"] for v in record["verdicts"] if not v["non_binding"])

    def test_the_record_can_be_fetched_again(self, client, mcas_raw):
        record = _upload(client, mcas_raw)
        again = client.get(f"/api/cards/{record['id']}")
        assert again.status_code == 200
        assert again.json()["id"] == record["id"]

    def test_unknown_card_is_404(self, client):
        assert client.get("/api/cards/nope").status_code == 404

    def test_a_json_body_that_is_not_a_card_is_rejected(self, client):
        response = client.post("/api/cards", json={"hello": "world"})
        assert response.status_code == 422
        assert "system_name" in response.text


class TestConfirm:
    def test_confirming_a_different_profile_recomputes_the_verdicts(self, client, mcas_raw):
        record = _upload(client, mcas_raw)
        response = client.post(
            f"/api/cards/{record['id']}/profile",
            json={"high_risk": "no", "personal_data": "no", "interacts_with_natural_persons": "no"},
        )
        assert response.status_code == 200
        record = response.json()
        assert record["confirmed"] is True
        applying = {v["objective_id"] for v in record["verdicts"] if v["applies"] == "yes"}
        assert applying == {"R6.1", "R6.2"}
        assert not any(v["provisional"] for v in record["verdicts"])

    def test_an_override_leaves_the_models_proposal_of_record_untouched(self, client, mcas_raw):
        """run.profile is what the model said; record.profile is what is in
        force. An override must not rewrite the former."""
        record = _upload(client, mcas_raw)
        assert record["run"]["profile"]["high_risk"]["value"] == "yes"
        record = client.post(
            f"/api/cards/{record['id']}/profile", json={"high_risk": "no"}
        ).json()
        assert record["profile"]["high_risk"]["value"] == "no"
        assert record["run"]["profile"]["high_risk"]["value"] == "yes"

    def test_the_record_profile_is_not_the_run_profile_object(self, mcas_raw, objectives):
        """They start equal; they must not start shared, or an in-place write
        to the one in force would silently rewrite the proposal too."""
        from wizard.cards import assess_card

        record = assess_card(SystemCard.from_card_json(mcas_raw), FakeExtractor(), objectives)
        record.profile.high_risk.value = "no"
        assert record.run.profile.high_risk.value == "yes"

    def test_confirming_keeps_the_models_quotes_for_the_record(self, client, mcas_raw):
        record = _upload(client, mcas_raw)
        record = client.post(
            f"/api/cards/{record['id']}/profile",
            json={"high_risk": "yes", "personal_data": "yes", "interacts_with_natural_persons": "yes"},
        ).json()
        assert record["profile"]["high_risk"]["quote"] == CREDIT

    def test_a_partial_confirmation_only_overrides_the_facts_given(self, client, mcas_raw):
        record = _upload(client, mcas_raw)
        record = client.post(
            f"/api/cards/{record['id']}/profile", json={"personal_data": "no"}
        ).json()
        assert record["profile"]["personal_data"]["value"] == "no"
        assert record["profile"]["high_risk"]["value"] == "yes"

    def test_an_invalid_answer_is_rejected(self, client, mcas_raw):
        record = _upload(client, mcas_raw)
        response = client.post(
            f"/api/cards/{record['id']}/profile", json={"high_risk": "maybe"}
        )
        assert response.status_code == 422


class TestPages:
    def test_the_objectives_page_offers_the_upload(self, client):
        page = client.get("/").text
        assert 'enctype="multipart/form-data"' in page
        assert 'type="file"' in page

    def test_uploading_through_the_form_lands_on_the_card_page(self, client, mcas_raw):
        response = client.post(
            "/cards",
            files={"card": ("mcas.json", json.dumps(mcas_raw), "application/json")},
            follow_redirects=False,
        )
        assert response.status_code == 303
        location = response.headers["location"]
        assert location.startswith("/cards/")
        page = client.get(location)
        assert page.status_code == 200
        assert "MicroCredit Assist Score (MCAS)" in page.text

    def test_the_card_page_shows_the_profile_with_its_quotes_and_the_verdicts(self, client, mcas_raw):
        record = _upload(client, mcas_raw)
        page = client.get(f"/cards/{record['id']}").text
        assert CREDIT in page                       # the quote the model relied on
        assert "5(b)" in page                       # the Annex III point
        assert "R1.1" in page and "R11.4" in page   # all objectives listed
        assert "Art. 14" in page                    # reasons are printed
        assert page.count('qf-tag--voluntary">Non-binding</span>') == 2
        assert page.count('qf-tag--in">To achieve · provisional</span>') == 48
        assert "not yet confirmed" in page.lower()
        assert "laif-logo.svg" in page              # platform chrome
        assert "<b>GAP:</b>" in page                # the author's notes are kept on this page too

    def test_confirming_through_the_form_marks_the_page_confirmed(self, client, mcas_raw):
        record = _upload(client, mcas_raw)
        response = client.post(
            f"/cards/{record['id']}/profile",
            data={"high_risk": "no", "personal_data": "yes", "interacts_with_natural_persons": "no"},
            follow_redirects=False,
        )
        assert response.status_code == 303
        page = client.get(f"/cards/{record['id']}").text
        assert "not yet confirmed" not in page.lower()
        # per-objective tags, not the stat tile label that is always present
        # personal_data=yes: the three GDPR-only rows plus R11.4's GDPR half
        assert page.count('qf-tag--in">To achieve</span>') == 4
        assert page.count('<span class="qf-tag">Not applicable</span>') == 50 - 4 - 2

    def test_a_form_upload_of_garbage_is_a_readable_error(self, client):
        response = client.post(
            "/cards", files={"card": ("x.json", "{not json", "application/json")}
        )
        assert response.status_code == 400
        assert "JSON" in response.text

    def test_the_annex_point_is_shown_because_the_fact_carries_one(self, client, mcas_raw):
        """The page asks the fact whether it has an Annex III point; it does not
        branch on the fact being called high_risk."""
        from wizard.rendering import TEMPLATES

        record = _upload(client, mcas_raw)
        assert "Annex III point 5(b)" in client.get(f"/cards/{record['id']}").text
        source = (TEMPLATES / "card.html.j2").read_text()
        assert "high_risk" not in source

    def test_the_card_page_never_shows_target_codes(self, client, mcas_raw):
        record = _upload(client, mcas_raw)
        page = client.get(f"/cards/{record['id']}").text
        assert "Applies to" not in page


class TestFailedModel:
    def test_a_model_failure_still_yields_a_page_with_undetermined_verdicts(self, mcas_raw, objectives):
        class Broken:
            def propose(self, card, findings=()):
                raise RuntimeError("provider down")

        client = TestClient(create_app(objectives, base_config=RunConfig(), extractor=Broken()))
        record = _upload(client, mcas_raw)
        assert record["run"]["stop"] == "failed"
        assert "provider down" in record["run"]["error"]
        assert sum(v["applies"] == "undetermined" for v in record["verdicts"]) == 48
        page = client.get(f"/cards/{record['id']}").text
        assert "provider down" in page
