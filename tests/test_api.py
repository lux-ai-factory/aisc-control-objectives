"""Wizard service API (SPEC §7), tested in-process with TestClient.

The app is a factory taking two injected dependencies:
- qualification_provider: lists qualifications / fetches a system card
- plan_runner: executes the matching pipeline for a card → AssessmentPlan
so these tests run without any live service, network, or LLM.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from wizard.api.app import create_app
from wizard.models.plan import AssessmentPlan, ProposedItem
from wizard.models.system_card import SystemCard

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def mcas_raw() -> dict:
    return json.loads((FIXTURES / "mcas_system_card.json").read_text())


class FakeQualificationProvider:
    def __init__(self, cards: dict[str, dict]):
        self._cards = cards

    def list_qualifications(self) -> list[dict]:
        return [
            {"id": qid, "system_name": c["system_name"], "has_system_card": True}
            for qid, c in self._cards.items()
        ]

    def get_system_card(self, qualification_id: str) -> SystemCard | None:
        raw = self._cards.get(qualification_id)
        return SystemCard.from_card_json(raw) if raw else None


class FakePlanRunner:
    """Synchronous runner; the production runner wraps the orchestrator."""

    def __init__(self):
        self.calls: list[str] = []
        self.received_configs: list = []

    def run(self, card: SystemCard, config=None) -> AssessmentPlan:
        self.calls.append(card.qualification_id)
        self.received_configs.append(config)
        return AssessmentPlan(
            plan_id="plan-1",
            qualification_id=card.qualification_id,
            system_name=card.system_name,
            created_at=datetime.now(timezone.utc),
            status="reviewed",
            tests=[
                ProposedItem(
                    item_id="ai-fairness-360",
                    item_type="test",
                    priority="must",
                    rationale="r",
                    evidence=[],
                    covers=["article-10"],
                )
            ],
            review_rounds=1,
        )


@pytest.fixture()
def runner():
    return FakePlanRunner()


@pytest.fixture()
def client(mcas_raw, runner):
    provider = FakeQualificationProvider({mcas_raw["qualification_id"]: mcas_raw})
    app = create_app(qualification_provider=provider, plan_runner=runner)
    return TestClient(app)


class TestConfig:
    def test_get_config_returns_effective_base(self, client):
        resp = client.get("/api/config")
        assert resp.status_code == 200
        body = resp.json()
        assert body["model"] == "claude-opus-4-8"
        assert body["guards"]["evidence"] == "drop"
        assert body["review"]["lenses"] == []

    def test_per_run_config_override_reaches_runner(self, client, runner, mcas_raw):
        resp = client.post(
            "/api/plans",
            json={
                "qualification_id": mcas_raw["qualification_id"],
                "config": {"guards": {"evidence": "demote"}, "max_rounds": 2},
            },
        )
        assert resp.status_code == 201
        effective = runner.received_configs[0]
        assert effective.guards.evidence == "demote"
        assert effective.max_rounds == 2
        # untouched knobs keep base values
        assert effective.guards.dataset_pairing == "enforce"

    def test_invalid_config_rejected_422(self, client, mcas_raw):
        resp = client.post(
            "/api/plans",
            json={
                "qualification_id": mcas_raw["qualification_id"],
                "config": {"max_rounds": 99},
            },
        )
        assert resp.status_code == 422


class TestQualificationList:
    def test_lists_qualifications(self, client, mcas_raw):
        resp = client.get("/api/qualifications")
        assert resp.status_code == 200
        items = resp.json()
        assert items[0]["id"] == mcas_raw["qualification_id"]
        assert items[0]["has_system_card"] is True


class TestPlans:
    def test_create_plan_runs_pipeline(self, client, mcas_raw):
        resp = client.post(
            "/api/plans", json={"qualification_id": mcas_raw["qualification_id"]}
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["status"] == "reviewed"
        assert body["plan_id"]

    def test_create_plan_unknown_qualification_404(self, client):
        resp = client.post("/api/plans", json={"qualification_id": "nope"})
        assert resp.status_code == 404

    def test_get_plan_roundtrip(self, client, mcas_raw):
        plan_id = client.post(
            "/api/plans", json={"qualification_id": mcas_raw["qualification_id"]}
        ).json()["plan_id"]
        resp = client.get(f"/api/plans/{plan_id}")
        assert resp.status_code == 200
        assert resp.json()["tests"][0]["item_id"] == "ai-fairness-360"

    def test_get_unknown_plan_404(self, client):
        assert client.get("/api/plans/missing").status_code == 404

    def test_list_plans(self, client, mcas_raw):
        client.post("/api/plans", json={"qualification_id": mcas_raw["qualification_id"]})
        resp = client.get("/api/plans")
        assert resp.status_code == 200
        assert len(resp.json()) >= 1


class TestFinalize:
    def test_finalize_reviewed_plan(self, client, mcas_raw):
        plan_id = client.post(
            "/api/plans", json={"qualification_id": mcas_raw["qualification_id"]}
        ).json()["plan_id"]
        resp = client.post(f"/api/plans/{plan_id}/finalize")
        assert resp.status_code == 200
        assert resp.json()["status"] == "finalized"

    def test_finalize_with_deselection(self, client, mcas_raw):
        plan_id = client.post(
            "/api/plans", json={"qualification_id": mcas_raw["qualification_id"]}
        ).json()["plan_id"]
        resp = client.post(
            f"/api/plans/{plan_id}/finalize",
            json={"deselect": ["ai-fairness-360"]},
        )
        assert resp.status_code == 200
        assert resp.json()["tests"] == []

    def test_finalize_twice_conflict(self, client, mcas_raw):
        plan_id = client.post(
            "/api/plans", json={"qualification_id": mcas_raw["qualification_id"]}
        ).json()["plan_id"]
        client.post(f"/api/plans/{plan_id}/finalize")
        assert client.post(f"/api/plans/{plan_id}/finalize").status_code == 409
