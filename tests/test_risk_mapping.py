"""Mapping a risk to the objectives that mitigate it.

Same shape as the profile extractor and the qualification app's filler: the
model proposes, deterministic controls check it against the catalogue and the
risk text, failing items go back, the rounds are bounded, and every exit
publishes so a person has something to correct.
"""

from __future__ import annotations

import json


from wizard.models.ontology import OntologyRisk
from wizard.risk_mapping import (
    MAX_ATTEMPTS,
    Mapping,
    MappedObjective,
    RiskMapper,
    map_risks,
    run_controls,
)

RUBBER_STAMP = OntologyRisk(
    id="risk2",
    text="Loan officers rubber-stamp the recommendation instead of reviewing it",
    source="Automation bias: agreeing with the model is faster than justifying a divergence",
    vulnerability="The dashboard presents the recommendation before the underlying factors",
    consequence="The mandatory human review becomes a formality",
    stakeholder="Loan applicants and officers",
    areas=["Fundamental rights", "Freedom"],
    control="Officer override rates are tracked against the branch median",
    vair_terms=["Overreliance"],
)


def _mapping(*items) -> Mapping:
    return Mapping(risk_id="risk2", objectives=list(items))


def _good(objective_id="R1.1", quote="The mandatory human review becomes a formality"):
    return MappedObjective(
        objective_id=objective_id, quote=quote, rationale="the oversight duty this risk defeats"
    )


class TestControls:
    def test_a_clean_mapping_raises_nothing(self, objectives):
        assert run_controls(_mapping(_good()), RUBBER_STAMP, objectives) == []

    def test_an_objective_the_catalogue_does_not_have(self, objectives):
        findings = run_controls(_mapping(_good("R42.9")), RUBBER_STAMP, objectives)
        assert [f.flag for f in findings] == ["unknown-objective"]

    def test_a_quote_that_is_not_in_the_risk(self, objectives):
        findings = run_controls(
            _mapping(_good(quote="the model was trained on Martian data")), RUBBER_STAMP, objectives
        )
        assert [f.flag for f in findings] == ["quote-not-in-risk"]

    def test_an_objective_claimed_with_no_quote(self, objectives):
        findings = run_controls(_mapping(_good(quote="")), RUBBER_STAMP, objectives)
        assert [f.flag for f in findings] == ["quote-missing"]

    def test_the_same_objective_claimed_twice(self, objectives):
        findings = run_controls(_mapping(_good(), _good()), RUBBER_STAMP, objectives)
        assert [f.flag for f in findings] == ["duplicate-objective"]

    def test_a_risk_mapped_to_nothing_at_all(self, objectives):
        """Every risk the assessor wrote down must reach some objective, or the
        mapping is silently dropping their work."""
        findings = run_controls(_mapping(), RUBBER_STAMP, objectives)
        assert [f.flag for f in findings] == ["risk-unmapped"]

    def test_quote_matching_ignores_whitespace_and_case(self, objectives):
        loose = _good(quote="  the MANDATORY human   review becomes a formality ")
        assert run_controls(_mapping(loose), RUBBER_STAMP, objectives) == []


class FakeMapper:
    """Returns queued mappings per risk and records what it was told."""

    def __init__(self, *mappings, error: Exception | None = None, raise_on: int | None = None):
        self._mappings = list(mappings)
        self._error = error
        self._raise_on = raise_on if raise_on is not None else (1 if error else None)
        self.calls: list[tuple] = []

    def propose(self, risk, findings=()):
        self.calls.append(tuple(findings))
        if self._raise_on is not None and len(self.calls) == self._raise_on:
            raise self._error
        return self._mappings.pop(0) if len(self._mappings) > 1 else self._mappings[0]


class TestTheLoop:
    def test_a_clean_first_attempt(self, objectives):
        run = map_risks([RUBBER_STAMP], FakeMapper(_mapping(_good())), objectives)
        assert run.stop == "clean"
        assert run.attempts == 1
        assert run.mappings["risk2"].objectives[0].objective_id == "R1.1"

    def test_findings_go_back_and_a_fixed_attempt_is_clean(self, objectives):
        mapper = FakeMapper(_mapping(_good("R42.9")), _mapping(_good()))
        run = map_risks([RUBBER_STAMP], mapper, objectives)
        assert run.stop == "clean"
        assert [f.flag for f in mapper.calls[1]] == ["unknown-objective"]

    def test_the_same_findings_twice_is_a_fixpoint(self, objectives):
        run = map_risks([RUBBER_STAMP], FakeMapper(_mapping(_good("R42.9"))), objectives)
        assert run.stop == "fixpoint"
        assert run.findings

    def test_the_cap_publishes_what_it_has(self, objectives):
        bad = [_mapping(_good("R42.9")), _mapping(_good(quote="nope")), _mapping(_good(quote=""))]
        run = map_risks([RUBBER_STAMP], FakeMapper(*bad, bad[-1]), objectives)
        assert run.stop == "cap"
        assert run.attempts == MAX_ATTEMPTS

    def test_what_publishes_after_a_fixpoint_holds_no_rejected_objective(self, objectives):
        """Every exit publishes, but an invented objective id must not survive
        into the tiers: publishing it would schedule work on a duty that does
        not exist."""
        stubborn = _mapping(_good("R42.9"), _good("R1.1"))
        run = map_risks([RUBBER_STAMP], FakeMapper(stubborn), objectives)
        assert run.stop == "fixpoint"
        published = {o.objective_id for o in run.mappings["risk2"].objectives}
        assert published == {"R1.1"}
        assert any(f.flag == "unknown-objective" for f in run.findings)

    def test_an_unquoted_objective_is_dropped_too(self, objectives):
        run = map_risks([RUBBER_STAMP], FakeMapper(_mapping(_good(quote=""), _good("R1.3"))), objectives)
        published = {o.objective_id for o in run.mappings["risk2"].objectives}
        assert published == {"R1.3"}

    def test_a_dead_model_publishes_an_empty_mapping_not_a_crash(self, objectives):
        run = map_risks([RUBBER_STAMP], FakeMapper(error=RuntimeError("no key")), objectives)
        assert run.stop == "failed"
        assert "no key" in run.error
        assert run.mappings["risk2"].objectives == []

    def test_every_risk_is_attempted(self, objectives):
        second = RUBBER_STAMP.model_copy(update={"id": "risk3", "text": "Something else entirely"})
        mapper = FakeMapper(_mapping(_good()))
        run = map_risks([RUBBER_STAMP, second], mapper, objectives)
        assert set(run.mappings) == {"risk2", "risk3"}


class FakeCompleter:
    def __init__(self, answer):
        self.answer = answer
        self.calls: list[tuple[str, str]] = []

    def __call__(self, system, user, temperature=0):
        self.calls.append((system, user))
        return self.answer


class TestTheMapper:
    def test_it_shows_the_model_the_risk_and_the_objectives(self, objectives):
        answer = json.dumps({"risk_id": "risk2", "objectives": [
            {"objective_id": "R1.1", "quote": "Automation bias", "rationale": "why"}]})
        complete = FakeCompleter(answer)
        mapping = RiskMapper(complete=complete, catalogue=objectives).propose(RUBBER_STAMP)
        assert mapping.objectives[0].objective_id == "R1.1"
        system, user = complete.calls[0]
        assert "mitigate" in system.lower()
        assert "rubber-stamp" in user
        assert "R1.1" in user and "Operator oversight capability" in user
        assert "R11.4" in user                      # the whole catalogue is offered

    def test_the_skill_carries_a_literal_answer_schema(self):
        from wizard.profiling import load_skill

        skill = load_skill(RiskMapper.SKILL)
        blocks = [b for b in skill.split("```") if b.strip().startswith(("json", "{"))]
        assert blocks, "the skill shows no JSON object"
        example = json.loads(blocks[-1].removeprefix("json").strip())
        assert set(example) == {"risk_id", "objectives"}
        assert {"objective_id", "quote", "rationale"} <= set(example["objectives"][0])
