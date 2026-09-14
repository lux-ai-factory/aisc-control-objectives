"""The qualification export: where a system's own risks live.

The system card is prose about the system; the qualification carries the AIRO
risk chain the assessor actually built. Nullable fields are normal (a risk may
name no vulnerability, a control may have no follow-up), so parsing must not
demand a full chain.
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from wizard.models.qualification import Qualification, RiskRow


@pytest.fixture(scope="module")
def mcas_qualification(fixtures_dir):
    return Qualification.from_export(json.loads((fixtures_dir / "mcas.qualification.json").read_text()))


class TestParsing:
    def test_the_system_it_belongs_to(self, mcas_qualification):
        assert mcas_qualification.system_name == "MicroCredit Assist Score (MCAS)"
        assert mcas_qualification.qualification_id

    def test_every_risk_row_becomes_a_risk(self, mcas_qualification):
        assert len(mcas_qualification.risks) == 5
        assert all(isinstance(risk, RiskRow) for risk in mcas_qualification.risks)

    def test_the_chain_is_kept_whole(self, mcas_qualification):
        risk = mcas_qualification.risks[2]
        assert risk.risk.startswith("Loan officers rubber-stamp")
        assert "Automation bias" in risk.source
        assert "dashboard presents the recommendation" in risk.vulnerability
        assert "formality" in risk.consequence
        assert risk.affected == "user"
        assert risk.impact_areas == ["right", "freedom"]
        assert "override rates are tracked" in risk.control
        assert "retraining" in risk.follow_up_control

    def test_a_missing_link_in_the_chain_is_empty_not_an_error(self, mcas_qualification):
        """Risk 1 names no vulnerability; risk 3 has no follow-up control."""
        assert mcas_qualification.risks[1].vulnerability == ""
        assert mcas_qualification.risks[3].follow_up_control == ""

    def test_risks_keep_the_order_the_assessor_gave_them(self, mcas_qualification):
        assert [risk.position for risk in mcas_qualification.risks] == [0, 1, 2, 3, 4]

    def test_each_risk_has_a_stable_id(self, mcas_qualification):
        assert [risk.id for risk in mcas_qualification.risks] == [f"risk{n}" for n in range(5)]

    def test_the_id_is_serialised(self, mcas_qualification):
        """A client rates risks by id, so an id that exists only in Python is
        an id the API never gave them."""
        assert mcas_qualification.model_dump()["risks"][2]["id"] == "risk2"

    def test_a_qualification_with_no_risks_parses(self):
        qualification = Qualification.from_export(
            {"id": "q1", "systemName": "X", "systemVersion": "1", "risks": []}
        )
        assert qualification.risks == []

    def test_something_that_is_not_a_qualification_is_rejected(self):
        with pytest.raises(ValidationError):
            Qualification.from_export({"hello": "world"})

    def test_a_system_card_is_not_a_qualification(self, mcas_raw):
        """Both are JSON about the same system; only one carries the risks."""
        assert not Qualification.looks_like_one(mcas_raw)

    def test_an_export_is_recognised(self, fixtures_dir):
        raw = json.loads((fixtures_dir / "mcas.qualification.json").read_text())
        assert Qualification.looks_like_one(raw)


class TestRiskText:
    def test_the_chain_reads_as_one_passage_for_the_model(self, mcas_qualification):
        """What the mapper is shown, and what its quotes must come from."""
        text = mcas_qualification.risks[4].as_text()
        for part in ("poisoned", "provenance signing", "misranks", "Egress allow-list"):
            assert part in text

    def test_an_absent_link_is_not_announced(self, mcas_qualification):
        assert "vulnerability" not in mcas_qualification.risks[1].as_text().lower()
