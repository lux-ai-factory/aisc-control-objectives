"""Tiering: every objective in the catalogue is owed, so which to do first.

The register never changes; what orders it is the system's own risks. The
assessor ranks each risk 1-5, the mapper says which objectives mitigate which
risk, and an objective inherits the severity of the worst risk it answers.
Tier 1 is the top of that, capped at what an assessor can actually start.
"""

from __future__ import annotations

import pytest

from aisc_control_objectives.models.ontology import OntologyRisk
from aisc_control_objectives.prioritising import (
    DEFAULT_SEVERITY,
    TIER_ONE_BUDGET,
    Severity,
    prioritise,
)
from aisc_control_objectives.risk_mapping import MappedObjective, Mapping


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

    def test_any_id_the_graph_uses_can_be_rated(self, objectives):
        """Risk ids come from whatever the exporter names its nodes; a pattern
        of our own would reject a valid graph and leave the assessor unable to
        rate anything, with no message explaining why."""
        odd = [
            OntologyRisk(id="Risk_1", text="named another way"),
            OntologyRisk(id="r-2", text="and another"),
        ]
        severity = Severity.model_validate({"ratings": {"Risk_1": 5, "r-2": 1}})
        priorities = prioritise(objectives, severity, {}, odd)
        assert priorities            # no raise: the graph's ids are the authority


RISKS = [
    OntologyRisk(id="risk2", text="Loan officers rubber-stamp the recommendation"),
    OntologyRisk(id="risk4", text="Training data is poisoned through the bureau ingestion path"),
]

def _maps(risk_id, *objective_ids):
    return Mapping(risk_id=risk_id, objectives=[
        MappedObjective(objective_id=oid, quote="q", rationale="because") for oid in objective_ids
    ])


#: What a clean run produces for those two risks. Deliberately more than the
#: Tier 1 budget between them, so the budget actually has to choose.
MAPPINGS = {
    "risk2": _maps("risk2", "R1.1", "R1.2", "R1.3", "R1.4", "R4.1"),
    "risk4": _maps("risk4", "R2.3", "R2.2", "R3.1", "R3.6", "R9.8"),
}


class TestTiering:
    def test_every_objective_is_tiered(self, objectives):
        """The whole catalogue is the register: nothing is ruled out, it is
        only ordered."""
        priorities = prioritise(objectives, Severity(), MAPPINGS, RISKS)
        assert len(priorities) == 50
        assert all(p.tier in (1, 2, 3) for p in priorities)

    def test_a_rating_for_a_risk_the_card_does_not_have_fails_loudly(self, objectives):
        with pytest.raises(ValueError, match="risk9"):
            prioritise(objectives, Severity(ratings={"risk9": 5}), MAPPINGS, RISKS)

    def test_tier_one_never_exceeds_the_budget(self, objectives):
        priorities = prioritise(objectives, Severity(), MAPPINGS, RISKS)
        assert TIER_ONE_BUDGET == 7
        assert sum(p.tier == 1 for p in priorities) <= 7

    def test_an_objective_that_mitigates_the_worst_risk_is_tier_one(self, objectives):
        severity = Severity.model_validate({"ratings": {"risk2": 5, "risk4": 1}})
        by_id = {p.objective_id: p for p in prioritise(objectives, severity, MAPPINGS, RISKS)}
        assert by_id["R1.1"].tier == 1
        assert by_id["R1.4"].tier == 1
        assert any("rubber-stamp" in r for r in by_id["R1.1"].reasons)
        # and the objectives answering the risk rated 1 are pushed out of it
        assert by_id["R2.3"].tier == 2

    def test_a_marginal_risk_cannot_put_a_binding_duty_in_tier_one(self, objectives):
        """The binding bonus is a tiebreak among work, and it was being added
        before the threshold test, so +1 lifted a risk rated 2 over the line."""
        binding = next(o for o in objectives if "Binding" in o.grounding_tier_flag)
        mapped = {"risk2": _maps("risk2", binding.id, "R1.1")}
        severity = Severity.model_validate({"ratings": {"risk2": 2}})
        by_id = {p.objective_id: p for p in prioritise(objectives, severity, mapped, RISKS)}
        assert by_id[binding.id].tier == 2
        assert by_id["R1.1"].tier == 2

    def test_tier_one_holds_only_risk_driven_work(self, objectives):
        """Padding the budget with objectives nothing points at would make
        "start here" mean "these seven, some for no reason"."""
        priorities = prioritise(objectives, Severity(), MAPPINGS, RISKS)
        assert all(p.risk_ids for p in priorities if p.tier == 1)

    def test_tier_one_is_smaller_than_the_budget_when_little_is_driven(self, objectives):
        few = {"risk2": _maps("risk2", "R1.1", "R1.4")}
        priorities = prioritise(objectives, Severity(), few, RISKS)
        assert sum(p.tier == 1 for p in priorities) == 2

    def test_reordering_the_risks_reorders_the_work(self, objectives):
        """The whole point: the same 50 duties, a different place to start."""
        oversight_first = Severity.model_validate({"ratings": {"risk2": 5, "risk4": 1}})
        poisoning_first = Severity.model_validate({"ratings": {"risk2": 1, "risk4": 5}})
        a = _tiers(prioritise(objectives, oversight_first, MAPPINGS, RISKS))
        b = _tiers(prioritise(objectives, poisoning_first, MAPPINGS, RISKS))
        assert a["R1.1"] == 1 and b["R2.3"] == 1
        assert b["R1.1"] > a["R1.1"]      # oversight drops out of Tier 1
        assert a["R2.3"] > b["R2.3"]      # and poisoning takes its place

    def test_an_objective_mitigating_two_risks_takes_the_worse_one(self, objectives):
        both = dict(MAPPINGS)
        both["risk4"] = _maps("risk4", "R1.1")
        severity = Severity.model_validate({"ratings": {"risk2": 1, "risk4": 5}})
        by_id = {p.objective_id: p for p in prioritise(objectives, severity, both, RISKS)}
        assert by_id["R1.1"].score >= 5

    def test_an_objective_no_risk_maps_to_is_not_tier_one(self, objectives):
        """It is still owed, but nothing the assessor identified drives it."""
        severity = Severity.model_validate({"ratings": {"risk2": 5, "risk4": 5}})
        by_id = {p.objective_id: p for p in prioritise(objectives, severity, MAPPINGS, RISKS)}
        assert by_id["R8.1"].tier != 1
        assert any("no identified risk" in r for r in by_id["R8.1"].reasons)

    def test_an_unmapped_objective_sorts_below_every_mapped_one(self, objectives):
        """Owed, but not where this system's danger is: that is Later, not
        Next. Sharing a score with a mapped objective would put "nothing points
        at this" alongside "a risk you rated 4 points at this"."""
        severity = Severity.model_validate({"ratings": {"risk2": 1, "risk4": 1}})
        by_id = {p.objective_id: p for p in prioritise(objectives, severity, MAPPINGS, RISKS)}
        mapped = by_id["R1.1"]          # driven by risk2, rated 1 (the lowest)
        unmapped = by_id["R8.1"]        # driven by nothing
        assert mapped.score > unmapped.score
        assert unmapped.tier == 3

    def test_the_tiers_read_as_driven_then_undriven(self, objectives):
        severity = Severity.model_validate({"ratings": {"risk2": 5, "risk4": 4}})
        priorities = prioritise(objectives, severity, MAPPINGS, RISKS)
        driven = {p.objective_id for p in priorities if p.risk_ids and not p.non_binding}
        tier3 = {p.objective_id for p in priorities if p.tier == 3}
        # nothing a risk drives is relegated to Later
        assert not (driven & tier3)

    def test_with_no_mappings_at_all_nothing_is_tier_one(self, objectives):
        """A card with no risks, or a mapping run that failed: everything is
        owed and nothing is urgent, which is the honest answer. The page still
        renders rather than going blank."""
        priorities = prioritise(objectives, Severity(), {}, [])
        assert sum(p.tier == 1 for p in priorities) == 0
        assert sum(p.tier == 3 for p in priorities) == 50

    def test_a_binding_duty_breaks_a_tie(self, objectives):
        priorities = {p.objective_id: p for p in prioritise(objectives, Severity(), {}, [])}
        binding = next(o for o in objectives if "Binding" in o.grounding_tier_flag)
        plain = next(o for o in objectives
                     if o.macro_id == binding.macro_id and "Binding" not in o.grounding_tier_flag)
        assert priorities[binding.id].score > priorities[plain.id].score

    def test_voluntary_objectives_never_reach_the_top_tiers(self, objectives):
        """The CSV's own note, not a judgement made here: a voluntary objective
        binds nobody, so it cannot displace a legal duty however it is rated."""
        mapped = {"risk2": Mapping(risk_id="risk2", objectives=[
            MappedObjective(objective_id="R6.1", quote="q", rationale="claimed")])}
        severity = Severity.model_validate({"ratings": {"risk2": 5}})
        tiers = _tiers(prioritise(objectives, severity, mapped, RISKS))
        assert tiers["R6.1"] == 3

    def test_the_order_is_stable_for_equal_scores(self, objectives):
        first = _tiers(prioritise(objectives, Severity(), MAPPINGS, RISKS))
        second = _tiers(prioritise(objectives, Severity(), MAPPINGS, RISKS))
        assert first == second

    def test_the_budget_can_be_narrowed(self, objectives):
        priorities = prioritise(objectives, Severity(), MAPPINGS, RISKS, budget=3)
        assert sum(p.tier == 1 for p in priorities) == 3


class TestTheMcasCase:
    """Where to start is decided by the system's own risks, and nothing else."""

    def test_the_worst_risk_drives_tier_one(self, objectives):
        severity = Severity.model_validate({"ratings": {"risk2": 5, "risk4": 4}})
        priorities = prioritise(objectives, severity, MAPPINGS, RISKS)
        tier_one = {p.objective_id for p in priorities if p.tier == 1}
        assert {"R1.1", "R1.4", "R2.3"} <= tier_one
        assert len(tier_one) <= 7
