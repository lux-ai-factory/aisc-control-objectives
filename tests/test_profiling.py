"""The profile extractor: the model proposes three facts with quotes; the
controls check the quotes; the loop is bounded and always publishes.

It reads the system's filled AIRO graph, not the prose card: the graph is the
assessor's own words, typed and cited, where the card is an LLM's summary of
them. No network: the extractor is driven by fakes.
"""

from __future__ import annotations


import pytest

from wizard.models.profile import Fact, Profile
from wizard.profiling import (
    Finding,
    ProfileExtractor,
    ProfileRun,
    extract_profile,
    load_skill,
    run_controls,
)

from helpers import FakeExtractor, good_profile  # noqa: E402


class TestTheGraphAsAPassage:
    def test_it_carries_what_the_three_questions_are_answered_from(self, mcas_graph):
        text = mcas_graph.as_text()
        assert "Finance and insurance" in text            # the Annex III domain
        assert "Evaluates creditworthiness" in text       # the purpose
        assert "Hosted explanation LLM" in text           # the Art. 50 component
        assert "Annex IV" in text                         # the answers, cited


class TestControls:
    def test_a_clean_profile_raises_nothing(self, mcas_graph):
        assert run_controls(good_profile(), mcas_graph) == []

    def test_a_quote_not_in_the_card_is_a_finding(self, mcas_graph):
        profile = good_profile()
        profile.personal_data.quote = "processes the applicant's blood type"
        findings = run_controls(profile, mcas_graph)
        assert [(f.fact, f.flag) for f in findings] == [("personal_data", "quote-not-in-graph")]

    def test_a_decided_fact_without_a_quote_is_a_finding(self, mcas_graph):
        profile = good_profile()
        profile.interacts_with_natural_persons.quote = ""
        findings = run_controls(profile, mcas_graph)
        assert [(f.fact, f.flag) for f in findings] == [
            ("interacts_with_natural_persons", "quote-missing")
        ]

    def test_an_undetermined_fact_needs_no_quote(self, mcas_graph):
        profile = good_profile()
        profile.personal_data = Fact(value="undetermined")
        assert run_controls(profile, mcas_graph) == []

    def test_high_risk_yes_must_cite_an_annex_iii_point(self, mcas_graph):
        profile = good_profile()
        profile.high_risk.annex_iii_point = ""
        findings = run_controls(profile, mcas_graph)
        assert [(f.fact, f.flag) for f in findings] == [("high_risk", "annex-point-missing")]

    def test_a_quote_straddling_two_properties_is_not_a_span_of_either(self, mcas_graph):
        """The only deterministic guard on the model's claims is that the quote
        is real. Joining the properties with a space would let a fabricated
        span made of one's tail and another's head pass as support."""
        profile = good_profile()
        first = mcas_graph.property("isAppliedWithinDomain").labels[0]
        second = mcas_graph.property("hasComponent").labels[0]
        profile.personal_data.quote = f"{first} {second}"
        findings = run_controls(profile, mcas_graph)
        assert [f.flag for f in findings] == ["quote-not-in-graph"]

    def test_quote_matching_ignores_whitespace_and_case(self, mcas_graph):
        profile = good_profile()
        profile.high_risk.quote = "  evaluates   creditworthiness for €100–€5,000 consumer loans "
        assert run_controls(profile, mcas_graph) == []


class TestExtractProfile:
    def test_a_clean_first_attempt_stops_clean(self, mcas_graph):
        run = extract_profile(mcas_graph, FakeExtractor(good_profile()))
        assert isinstance(run, ProfileRun)
        assert run.stop == "clean"
        assert run.attempts == 1
        assert run.findings == []

    def test_findings_go_back_to_the_extractor_and_a_fixed_attempt_stops_clean(self, mcas_graph):
        bad = good_profile()
        bad.personal_data.quote = "not in the card"
        extractor = FakeExtractor(bad, good_profile())
        run = extract_profile(mcas_graph, extractor)
        assert run.stop == "clean"
        assert run.attempts == 2
        # second call was told what was wrong with the first
        assert [f.flag for f in extractor.calls[1]] == ["quote-not-in-graph"]

    def test_the_same_findings_twice_running_is_a_fixpoint(self, mcas_graph):
        bad = good_profile()
        bad.personal_data.quote = "not in the card"
        run = extract_profile(mcas_graph, FakeExtractor(bad))
        assert run.stop == "fixpoint"
        assert run.attempts == 2
        assert [f.flag for f in run.findings] == ["quote-not-in-graph"]

    def test_the_attempt_cap_publishes_with_open_findings(self, mcas_graph):
        one, two, three = good_profile(), good_profile(), good_profile()
        one.personal_data.quote = "wrong one"
        two.high_risk.quote = "wrong two"
        three.interacts_with_natural_persons.quote = "wrong three"
        run = extract_profile(mcas_graph, FakeExtractor(one, two, three, three), max_attempts=3)
        assert run.stop == "cap"
        assert run.attempts == 3
        assert run.findings  # the last attempt's, attached to the published profile

    def test_a_model_failing_on_the_first_attempt_publishes_an_undetermined_profile(self, mcas_graph):
        run = extract_profile(mcas_graph, FakeExtractor(error=RuntimeError("no key")))
        assert run.stop == "failed"
        assert "no key" in run.error
        assert run.profile == Profile()
        assert run.attempts == 1

    def test_a_model_failing_on_a_retry_keeps_the_last_proposal_and_its_findings(self, mcas_graph):
        """Every exit publishes what it has: the person gets the good facts and
        the flagged one, not three empty selects."""
        first = good_profile()
        first.personal_data.quote = "not in the card"
        run = extract_profile(mcas_graph, FakeExtractor(first, raise_on=2, error=RuntimeError("rate limit")))
        assert run.stop == "failed"
        assert "rate limit" in run.error
        assert run.attempts == 2
        assert run.profile.high_risk.value == "yes"
        assert run.profile.high_risk.annex_iii_point == "5(b)"
        assert [f.flag for f in run.findings] == ["quote-not-in-graph"]

    def test_an_extractor_returning_nothing_is_a_failed_run_not_a_crash(self, mcas_graph):
        class Nothing:
            def propose(self, card, findings=()):
                return None

        run = extract_profile(mcas_graph, Nothing())
        assert run.stop == "failed"
        assert "Profile" in run.error


class FakeCompleter:
    """Records (system, user) and returns a queued answer, the way BAF's
    text-in/text-out predict does."""

    def __init__(self, answer: str | None = None):
        self.answer = answer if answer is not None else good_profile().model_dump_json()
        self.calls: list[tuple[str, str]] = []

    def __call__(self, system: str, user: str, temperature: float = 0) -> str:
        self.calls.append((system, user))
        return self.answer


class TestProfileExtractor:
    def test_it_puts_the_skill_in_the_system_prompt(self, mcas_graph):
        complete = FakeCompleter()
        profile = ProfileExtractor(complete=complete).propose(mcas_graph)
        assert profile == good_profile()
        system, _ = complete.calls[0]
        assert "Annex III" in system
        assert not system.startswith("---")          # frontmatter stripped

    def test_the_user_message_carries_what_the_graph_says(self, mcas_graph):
        complete = FakeCompleter()
        ProfileExtractor(complete=complete).propose(mcas_graph)
        _, user = complete.calls[0]
        assert mcas_graph.system_name in user
        assert "Finance and insurance" in user
        assert mcas_graph.answers[0].text[:40] in user

    def test_on_a_retry_the_findings_are_in_the_message(self, mcas_graph):
        complete = FakeCompleter()
        ProfileExtractor(complete=complete).propose(
            mcas_graph,
            findings=[Finding(fact="personal_data", flag="quote-not-in-graph", detail="x")],
        )
        _, user = complete.calls[0]
        assert "quote-not-in-graph" in user
        assert "personal_data" in user

    def test_a_fenced_answer_with_prose_around_it_still_parses(self, mcas_graph):
        answer = "Sure!\n```json\n" + good_profile().model_dump_json() + "\n```\nHope that helps."
        profile = ProfileExtractor(complete=FakeCompleter(answer)).propose(mcas_graph)
        assert profile.high_risk.value == "yes"

    def test_an_answer_with_no_json_raises_so_the_loop_can_publish_the_failure(self, mcas_graph):
        with pytest.raises(ValueError, match="no JSON object"):
            ProfileExtractor(complete=FakeCompleter("I cannot help with that.")).propose(mcas_graph)

    def test_the_skill_carries_a_literal_answer_schema(self):
        """Nothing constrains the answer provider-side any more, so the skill
        has to show the exact object, and that example has to be valid JSON
        with every fact and every field of a fact in it."""
        import json as _json
        import re as _re

        skill = load_skill(ProfileExtractor.SKILL)
        blocks = _re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", skill, _re.DOTALL)
        assert blocks, "the skill shows no JSON object"
        example = _json.loads(blocks[-1])
        assert set(example) == set(Profile.FACTS)
        for fact, shape in example.items():
            assert {"value", "quote", "source", "rationale"} <= set(shape), fact
        assert "annex_iii_point" in example["high_risk"]

    def test_the_examples_shape_is_a_valid_profile(self):
        """The shape the skill asks for must be the shape the model accepts."""
        import json as _json
        import re as _re

        skill = load_skill(ProfileExtractor.SKILL)
        example = _json.loads(
            _re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", skill, _re.DOTALL)[-1]
        )
        for fact in example.values():
            fact["value"] = "undetermined"
        assert Profile.model_validate(example)


def test_the_skill_ships_with_the_package_and_states_the_rules():
    skill = load_skill("extracting-the-applicability-profile")
    assert not skill.startswith("---")
    for phrase in ("literal", "Annex III", "undetermined", "natural persons", "personal data"):
        assert phrase in skill, phrase


def test_an_unknown_skill_fails_loudly():
    with pytest.raises(FileNotFoundError):
        load_skill("no-such-skill")
