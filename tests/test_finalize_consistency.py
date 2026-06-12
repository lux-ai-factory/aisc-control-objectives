"""Finding 1: finalize must keep the plan internally consistent."""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from wizard.api.app import create_app
from wizard.models.plan import AssessmentPlan, ProposedItem
from wizard.models.system_card import SystemCard

FIXTURES = Path(__file__).parent / "fixtures"


def rich_plan(qid: str, name: str) -> AssessmentPlan:
    return AssessmentPlan(
        plan_id="plan-rich",
        qualification_id=qid,
        system_name=name,
        created_at=datetime.now(timezone.utc),
        status="reviewed",
        tests=[
            ProposedItem(
                item_id="tool-x", item_type="test", priority="must",
                rationale="r", covers=["article-13", "article-10"],
            ),
            ProposedItem(
                item_id="tool-y", item_type="test", priority="should",
                rationale="r", covers=["article-10"],
            ),
        ],
        datasets=[
            ProposedItem(
                item_id="data-1", item_type="dataset", priority="must",
                rationale="r", covers=[], paired_test_id="tool-x",
            ),
        ],
        coverage={"article-13": ["tool-x"], "article-10": ["tool-x", "tool-y"]},
        review_rounds=1,
    )


class Provider:
    def __init__(self, raw):
        self.raw = raw

    def list_qualifications(self):
        return []

    def get_system_card(self, qid):
        return SystemCard.from_card_json(self.raw) if qid == self.raw["qualification_id"] else None


class Runner:
    def __init__(self, plan):
        self.plan = plan

    def run(self, card, config=None):
        return self.plan


@pytest.fixture()
def client():
    raw = json.loads((FIXTURES / "mcas_system_card.json").read_text())
    plan = rich_plan(raw["qualification_id"], raw["system_name"])
    app = create_app(qualification_provider=Provider(raw), plan_runner=Runner(plan))
    return TestClient(app), raw["qualification_id"]


def _create_and_finalize(client, qid, deselect):
    api, _ = client, qid
    plan_id = api.post("/api/plans", json={"qualification_id": qid}).json()["plan_id"]
    return api.post(f"/api/plans/{plan_id}/finalize", json={"deselect": deselect})


def test_coverage_rebuilt_after_deselection(client):
    api, qid = client
    body = _create_and_finalize(api, qid, ["tool-x"]).json()
    assert "tool-x" not in body["coverage"].get("article-10", [])
    # article-13 was covered only by tool-x → key gone, loss recorded as a gap
    assert "article-13" not in body["coverage"]
    assert any("article-13" in g for g in body["gaps"])


def test_paired_dataset_cascades_on_deselection(client):
    api, qid = client
    body = _create_and_finalize(api, qid, ["tool-x"]).json()
    assert body["datasets"] == []
    assert any("data-1" in w for w in body["warnings"])


def test_exported_plan_cannot_be_refinalized(client):
    api, qid = client
    plan_id = api.post("/api/plans", json={"qualification_id": qid}).json()["plan_id"]
    api.post(f"/api/plans/{plan_id}/finalize")
    # simulate export having happened (store-level status move)
    # then a second finalize must 409 rather than regress the status
    second = api.post(f"/api/plans/{plan_id}/finalize")
    assert second.status_code == 409


def test_untouched_finalize_keeps_everything(client):
    api, qid = client
    body = _create_and_finalize(api, qid, []).json()
    assert body["status"] == "finalized"
    assert {i["item_id"] for i in body["tests"]} == {"tool-x", "tool-y"}
    assert body["coverage"]["article-13"] == ["tool-x"]
    assert body["datasets"][0]["item_id"] == "data-1"
