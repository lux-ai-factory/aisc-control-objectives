"""Tiering: of the objectives that apply, which to do first.

Applicability is all-or-nothing for a high-risk system, so it says what is
owed, not where to start. The assessor rates each macro requirement 1-5 for
this system; the wizard orders the objectives by that rating, by what the card
already admits, and by how binding the duty is, then cuts a Tier 1 that fits
in a day's work.
"""

from __future__ import annotations

import pytest

from wizard.applicability import decide
from wizard.models.profile import Fact, HighRiskFact, Profile
from wizard.models.qualification import RiskRow
from wizard.prioritising import (
    DEFAULT_SEVERITY,
    TIER_ONE_BUDGET,
    Severity,
    prioritise,
)
from wizard.risk_mapping import MappedObjective, Mapping

ALL_YES = Profile(
    high_risk=HighRiskFact(value="yes", annex_iii_point="5(b)"),
    personal_data=Fact(value="yes"),
    interacts_with_natural_persons=Fact(value="yes"),
)


@pytest.fixture()
def verdicts(objectives):
    return decide(ALL_YES, objectives, confirmed=True)


def _tiers(priorities):
    return {p.objective_id: p.tier for p in priorities}


class TestSeverity:
    """How severe each of THIS system's own risks is, 1-5."""

    def test_it_is_one_to_five_per_risk(self):
        severity = Severity.model_validate({"ratings": {"risk2": 5, "risk0": 4}})
        assert severity.of("risk2") == 5
        assert severity.of("risk0") == 4

    def test_an_unrated_risk_is_neutral(self):
        assert Severity().of("risk9") == DEFAULT_SEVERITY == 3

    def test_a_rating_outside_the_scale_is_rejected(self):
        for bad in (0, 6, -1):
            with pytest.raises(ValueError):
                Severity.model_validate({"ratings": {"risk0": bad}})

    def test_something_that_is_not_a_risk_id_is_rejected(self):
        for bad in ("banana", "R1", "5"):
            with pytest.raises(ValueError):
                Severity.model_validate({"ratings": {bad: 3}})


RISKS = [
    RiskRow(position=2, risk="Loan officers rubber-stamp the recommendation"),
    RiskRow(position=4, risk="Training data is poisoned through the bureau ingestion path"),
]

#: what a clean mapping run produces for those two risks
MAPPINGS = {
    "risk2": Mapping(risk_id="risk2", objectives=[
        MappedObjective(objective_id="R1.1", quote="q", rationale="oversight is defeated"),
        MappedObjective(objective_id="R1.4", quote="q", rationale="rubber-stamping is invisible"),
    ]),
    "risk4": Mapping(risk_id="risk4", objectives=[
        MappedObjective(objective_id="R2.3", quote="q", rationale="poisoning is a security control"),
    ]),
}


class TestTiering:
    def test_only_objectives_that_apply_are_tiered(self, objectives):
        """A system whose facts are unsettled has nothing to schedule, except
        the two voluntary objectives, which apply to everyone."""
        nothing = Profile()
        priorities = prioritise(objectives, decide(nothing, objectives), Severity(), {}, RISKS)
        by_id = {p.objective_id: p for p in priorities}
        assert by_id["R1.1"].tier is None
        assert by_id["R6.1"].tier == 3

    def test_a_rating_for_a_risk_the_qualification_does_not_have_fails_loudly(
        self, objectives, verdicts
    ):
        with pytest.raises(ValueError, match="risk9"):
            prioritise(objectives, verdicts, Severity(ratings={"risk9": 5}), MAPPINGS, RISKS)

    def test_tier_one_never_exceeds_the_budget(self, objectives, verdicts):
        priorities = prioritise(objectives, verdicts, Severity(), MAPPINGS, RISKS)
        assert TIER_ONE_BUDGET == 7
        assert sum(p.tier == 1 for p in priorities) <= 7

    def test_an_objective_that_mitigates_the_worst_risk_is_tier_one(self, objectives, verdicts):
        severity = Severity.model_validate({"ratings": {"risk2": 5, "risk4": 1}})
        by_id = {p.objective_id: p for p in prioritise(objectives, verdicts, severity, MAPPINGS, RISKS)}
        assert by_id["R1.1"].tier == 1
        assert by_id["R1.4"].tier == 1
        assert any("rubber-stamp" in r for r in by_id["R1.1"].reasons)

    def test_reordering_the_risks_reorders_the_work(self, objectives, verdicts):
        """The whole point: the same 50 duties, a different place to start."""
        oversight_first = Severity.model_validate({"ratings": {"risk2": 5, "risk4": 1}})
        poisoning_first = Severity.model_validate({"ratings": {"risk2": 1, "risk4": 5}})
        a = {p.objective_id: p.tier for p in prioritise(objectives, verdicts, oversight_first, MAPPINGS, RISKS)}
        b = {p.objective_id: p.tier for p in prioritise(objectives, verdicts, poisoning_first, MAPPINGS, RISKS)}
        assert a["R1.1"] == 1 and b["R2.3"] == 1
        assert b["R1.1"] > a["R1.1"]

    def test_an_objective_mitigating_two_risks_takes_the_worse_one(self, objectives, verdicts):
        both = dict(MAPPINGS)
        both["risk4"] = Mapping(risk_id="risk4", objectives=[
            MappedObjective(objective_id="R1.1", quote="q", rationale="also this"),
        ])
        severity = Severity.model_validate({"ratings": {"risk2": 1, "risk4": 5}})
        by_id = {p.objective_id: p for p in prioritise(objectives, verdicts, severity, both, RISKS)}
        assert by_id["R1.1"].score >= 5

    def test_an_objective_no_risk_maps_to_is_not_tier_one(self, objectives, verdicts):
        """It is still owed, but nothing the assessor identified drives it."""
        severity = Severity.model_validate({"ratings": {"risk2": 5, "risk4": 5}})
        by_id = {p.objective_id: p for p in prioritise(objectives, verdicts, severity, MAPPINGS, RISKS)}
        assert by_id["R8.1"].tier != 1
        assert any("no identified risk" in r for r in by_id["R8.1"].reasons)

    def test_with_no_mappings_at_all_everything_falls_back_to_neutral(self, objectives, verdicts):
        """A qualification with no risks, or a mapping run that failed: the
        tiers must still exist rather than the page going blank."""
        priorities = prioritise(objectives, verdicts, Severity(), {}, [])
        assert sum(p.tier == 1 for p in priorities) == 7

    def test_a_binding_duty_breaks_a_tie(self, objectives, verdicts):
        priorities = {p.objective_id: p for p in prioritise(objectives, verdicts, Severity(), {}, [])}
        binding = next(o for o in objectives if "Binding" in o.grounding_tier_flag)
        plain = next(o for o in objectives
                     if o.macro_id == binding.macro_id and "Binding" not in o.grounding_tier_flag)
        assert priorities[binding.id].score > priorities[plain.id].score

    def test_voluntary_objectives_never_reach_the_top_tiers(self, objectives, verdicts):
        mapped = {"risk2": Mapping(risk_id="risk2", objectives=[
            MappedObjective(objective_id="R6.1", quote="q", rationale="claimed")])}
        severity = Severity.model_validate({"ratings": {"risk2": 5}})
        tiers = _tiers(prioritise(objectives, verdicts, severity, mapped, RISKS))
        assert tiers["R6.1"] == 3

    def test_the_order_is_stable_for_equal_scores(self, objectives, verdicts):
        first = _tiers(prioritise(objectives, verdicts, Severity(), MAPPINGS, RISKS))
        second = _tiers(prioritise(objectives, verdicts, Severity(), MAPPINGS, RISKS))
        assert first == second

    def test_the_budget_can_be_narrowed(self, objectives, verdicts):
        priorities = prioritise(objectives, verdicts, Severity(), MAPPINGS, RISKS, budget=3)
        assert sum(p.tier == 1 for p in priorities) == 3


class TestTheMcasCase:
    """Everything applies, so where to start is decided by the system's own risks."""

    def test_the_worst_risk_drives_tier_one(self, objectives, verdicts):
        severity = Severity.model_validate({"ratings": {"risk2": 5, "risk4": 4}})
        priorities = prioritise(objectives, verdicts, severity, MAPPINGS, RISKS)
        tier_one = {p.objective_id for p in priorities if p.tier == 1}
        assert {"R1.1", "R1.4", "R2.3"} <= tier_one
        assert len(tier_one) <= 7
