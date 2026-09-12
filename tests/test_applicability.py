"""Applicability is deterministic: a rules table over a three-fact profile.

The model never decides whether an objective applies. It proposes the profile;
this module turns a profile into 50 verdicts, each with a printable reason.
"""

from __future__ import annotations

from collections import Counter

from wizard.applicability import CLAUSES, FACT_FOR_REGIME, Verdict, decide
from wizard.models.profile import Fact, HighRiskFact, Profile


def _profile(high_risk="undetermined", personal="undetermined", interacts="undetermined"):
    return Profile(
        high_risk=HighRiskFact(value=high_risk, annex_iii_point="5(b)" if high_risk == "yes" else ""),
        personal_data=Fact(value=personal),
        interacts_with_natural_persons=Fact(value=interacts),
    )


class TestRegimes:
    """The regime is a property of the objective row, one per legal basis."""

    def test_objectives_touching_each_regime(self, objectives):
        touching = Counter()
        for objective in objectives:
            for regime in set(objective.regimes):
                touching[regime] += 1
        assert touching == {"ai_act": 44, "gdpr": 4, "conditional": 1, "voluntary": 2}

    def test_the_exceptions_are_the_expected_rows(self, objectives):
        assert objectives.by_id("R4.4").regimes == ["conditional"]
        assert objectives.by_id("R4.4").trigger_fact == "interacts_with_natural_persons"
        assert {o.id for o in objectives if "gdpr" in o.regimes} == {"R3.3", "R3.4", "R3.5", "R11.4"}
        assert {o.id for o in objectives if "voluntary" in o.regimes} == {"R6.1", "R6.2"}

    def test_a_basis_without_an_instrument_inherits_the_one_before_it(self, objectives):
        """"AI Act Art. 10(2)(f)-(g); Art. 9" cites the AI Act twice; "AI Act
        Art. 11; Annex IV" likewise. Only an explicit instrument switches regime."""
        assert objectives.by_id("R5.2").legal_bases == ["AI Act Art. 10(2)(f)-(g)", "Art. 9"]
        assert objectives.by_id("R5.2").regimes == ["ai_act", "ai_act"]
        annex = next(o for o in objectives if "Annex IV" in o.legal_basis)
        assert annex.regimes == ["ai_act", "ai_act"]

    def test_a_mixed_basis_has_one_regime_per_basis_in_order(self, objectives):
        # R11.4: "AI Act Arts. 18, 19; GDPR Art. 5(1)(e)"
        objective = objectives.by_id("R11.4")
        assert objective.legal_bases == ["AI Act Arts. 18, 19", "GDPR Art. 5(1)(e)"]
        assert objective.regimes == ["ai_act", "gdpr"]


class TestDecide:
    def test_every_objective_gets_exactly_one_verdict_in_catalogue_order(self, objectives):
        verdicts = decide(_profile(), objectives)
        assert [v.objective_id for v in verdicts] == [o.id for o in objectives]
        assert all(isinstance(v, Verdict) for v in verdicts)

    def test_a_confirmed_high_risk_system_with_personal_data_and_a_chatbot_gets_all_fifty(
        self, objectives
    ):
        verdicts = decide(_profile("yes", "yes", "yes"), objectives, confirmed=True)
        assert all(v.applies == "yes" for v in verdicts)
        assert not any(v.provisional for v in verdicts)

    def test_voluntary_objectives_always_apply_and_are_flagged_non_binding(self, objectives):
        by_id = {v.objective_id: v for v in decide(_profile("no", "no", "no"), objectives)}
        for oid in ("R6.1", "R6.2"):
            assert by_id[oid].applies == "yes"
            assert by_id[oid].non_binding is True
            assert "non-binding" in by_id[oid].reason.lower()
        assert sum(v.non_binding for v in by_id.values()) == 2

    def test_a_non_high_risk_system_keeps_only_the_voluntary_objectives(self, objectives):
        verdicts = decide(_profile("no", "no", "no"), objectives, confirmed=True)
        applying = {v.objective_id for v in verdicts if v.applies == "yes"}
        assert applying == {"R6.1", "R6.2"}
        assert sum(v.applies == "no" for v in verdicts) == 48

    def test_high_risk_alone_does_not_pull_in_the_conditional_and_gdpr_rows(self, objectives):
        by_id = {v.objective_id: v for v in decide(_profile("yes", "no", "no"), objectives)}
        assert by_id["R1.1"].applies == "yes"
        assert by_id["R4.4"].applies == "no"          # no natural-person interaction
        assert by_id["R3.3"].applies == "no"          # no personal data
        assert by_id["R11.4"].applies == "yes"        # its AI Act half carries it
        assert sum(v.applies == "yes" for v in by_id.values()) == 44 + 2

    def test_the_chatbot_fact_alone_triggers_r4_4(self, objectives):
        by_id = {v.objective_id: v for v in decide(_profile("no", "no", "yes"), objectives)}
        assert by_id["R4.4"].applies == "yes"
        assert by_id["R1.1"].applies == "no"

    def test_personal_data_alone_triggers_every_gdpr_basis(self, objectives):
        by_id = {v.objective_id: v for v in decide(_profile("no", "yes", "no"), objectives)}
        assert {oid for oid, v in by_id.items() if v.applies == "yes"} == {
            "R3.3", "R3.4", "R3.5", "R11.4", "R6.1", "R6.2",
        }

    def test_a_mixed_basis_applies_when_any_of_its_bases_applies(self, objectives):
        """R11.4 carries an AI Act basis and a GDPR one. A non-high-risk system
        that processes personal data still owes the GDPR half."""
        by_id = {v.objective_id: v for v in decide(_profile("no", "yes", "no"), objectives)}
        assert by_id["R11.4"].applies == "yes"
        assert by_id["R11.4"].reason == "Personal data is processed: GDPR Art. 5(1)(e) applies."

    def test_a_mixed_basis_is_undetermined_only_when_no_basis_settles_it(self, objectives):
        by_id = {v.objective_id: v for v in decide(_profile("undetermined", "no", "no"), objectives)}
        assert by_id["R11.4"].applies == "undetermined"
        by_id = {v.objective_id: v for v in decide(_profile("undetermined", "yes", "no"), objectives)}
        assert by_id["R11.4"].applies == "yes"

    def test_a_mixed_basis_that_applies_under_neither_says_so_for_both(self, objectives):
        by_id = {v.objective_id: v for v in decide(_profile("no", "no", "no"), objectives)}
        assert by_id["R11.4"].applies == "no"
        assert "AI Act Arts. 18, 19" in by_id["R11.4"].reason
        assert "GDPR Art. 5(1)(e)" in by_id["R11.4"].reason

    def test_an_undetermined_fact_yields_undetermined_not_a_guess(self, objectives):
        by_id = {v.objective_id: v for v in decide(_profile(), objectives)}
        assert by_id["R1.1"].applies == "undetermined"
        assert by_id["R4.4"].applies == "undetermined"
        assert by_id["R3.3"].applies == "undetermined"
        assert by_id["R6.1"].applies == "yes"

    def test_unconfirmed_verdicts_are_provisional_except_the_voluntary_ones(self, objectives):
        by_id = {v.objective_id: v for v in decide(_profile("yes", "yes", "yes"), objectives)}
        assert by_id["R1.1"].provisional is True
        assert by_id["R6.1"].provisional is False

    def test_reasons_name_the_legal_basis(self, objectives):
        by_id = {v.objective_id: v for v in decide(_profile("yes", "yes", "yes"), objectives)}
        assert "Art. 14" in by_id["R1.1"].reason
        assert "GDPR Art. 25" in by_id["R3.4"].reason
        assert "Art. 50" in by_id["R4.4"].reason

    def test_negative_reasons_say_why_not(self, objectives):
        by_id = {v.objective_id: v for v in decide(_profile("no", "no", "no"), objectives)}
        assert "not" in by_id["R1.1"].reason.lower()
        assert "natural persons" in by_id["R4.4"].reason.lower()
        assert "personal data" in by_id["R3.3"].reason.lower()


class TestFactTablesCoverTheProfile:
    """Adding a fourth fact to Profile must not KeyError at request time."""

    def test_every_fact_has_reason_clauses_for_all_three_answers(self):
        assert set(CLAUSES) == set(Profile.FACTS)
        for fact, clauses in CLAUSES.items():
            assert set(clauses) == {"yes", "no", "undetermined"}, fact
            for answer, clause in clauses.items():
                assert "{basis}" in clause, (fact, answer)

    def test_every_governing_fact_is_a_fact_of_the_profile(self, objectives):
        assert set(FACT_FOR_REGIME.values()) <= set(Profile.FACTS)
        triggers = {o.trigger_fact for o in objectives if o.trigger_fact}
        assert triggers <= set(Profile.FACTS)

    def test_every_regime_in_the_catalogue_is_decidable(self, objectives):
        """A regime with no governing fact and no trigger would crash decide()."""
        for objective in objectives:
            for regime in objective.regimes:
                if regime == "voluntary":
                    continue
                assert FACT_FOR_REGIME.get(regime) or objective.trigger_fact, objective.id
