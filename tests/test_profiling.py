"""The profile extractor: the model proposes three facts with quotes; the
controls check the quotes; the loop is bounded and always publishes.

No network: the extractor is driven by fakes.
"""

from __future__ import annotations


import pytest

from wizard.models.profile import Fact, Profile
from wizard.profiling import (
    Finding,
    ProfileExtractor,
    ProfileRun,
    card_text,
    extract_profile,
    load_skill,
    run_controls,
)

from helpers import FakeExtractor, good_profile  # noqa: E402


class TestCardText:
    def test_covers_every_string_the_model_is_shown(self, mcas_card):
        """The prompt shows the model the whole card; a quote from any part of
        it must be checkable, so the haystack is derived from the same dump
        rather than from a hand-kept field list that can fall behind."""
        def leaves(value):
            if isinstance(value, str):
                if value.strip():
                    yield value
            elif isinstance(value, dict):
                for item in value.values():
                    yield from leaves(item)
            elif isinstance(value, list):
                for item in value:
                    yield from leaves(item)

        text = card_text(mcas_card)
        for leaf in leaves(mcas_card.model_dump()):
            assert leaf in text, leaf[:60]

    def test_contains_every_prose_field_and_every_finding_point(self, mcas_card):
        text = card_text(mcas_card)
        assert mcas_card.description in text
        assert mcas_card.target_use_case in text
        for finding in mcas_card.findings:
            assert finding.summary in text
            for point in finding.points:
                assert point in text
        for issue in mcas_card.open_issues:
            assert issue in text


class TestControls:
    def test_a_clean_profile_raises_nothing(self, mcas_card):
        assert run_controls(good_profile(), mcas_card) == []

    def test_a_quote_not_in_the_card_is_a_finding(self, mcas_card):
        profile = good_profile()
        profile.personal_data.quote = "processes the applicant's blood type"
        findings = run_controls(profile, mcas_card)
        assert [(f.fact, f.flag) for f in findings] == [("personal_data", "quote-not-in-card")]

    def test_a_decided_fact_without_a_quote_is_a_finding(self, mcas_card):
        profile = good_profile()
        profile.interacts_with_natural_persons.quote = ""
        findings = run_controls(profile, mcas_card)
        assert [(f.fact, f.flag) for f in findings] == [
            ("interacts_with_natural_persons", "quote-missing")
        ]

    def test_an_undetermined_fact_needs_no_quote(self, mcas_card):
        profile = good_profile()
        profile.personal_data = Fact(value="undetermined")
        assert run_controls(profile, mcas_card) == []

    def test_high_risk_yes_must_cite_an_annex_iii_point(self, mcas_card):
        profile = good_profile()
        profile.high_risk.annex_iii_point = ""
        findings = run_controls(profile, mcas_card)
        assert [(f.fact, f.flag) for f in findings] == [("high_risk", "annex-point-missing")]

    def test_quote_matching_ignores_whitespace_and_case(self, mcas_card):
        profile = good_profile()
        profile.high_risk.quote = "  evaluates   creditworthiness for €100–€5,000 consumer loans "
        assert run_controls(profile, mcas_card) == []


class TestExtractProfile:
    def test_a_clean_first_attempt_stops_clean(self, mcas_card):
        run = extract_profile(mcas_card, FakeExtractor(good_profile()))
        assert isinstance(run, ProfileRun)
        assert run.stop == "clean"
        assert run.attempts == 1
        assert run.findings == []

    def test_findings_go_back_to_the_extractor_and_a_fixed_attempt_stops_clean(self, mcas_card):
        bad = good_profile()
        bad.personal_data.quote = "not in the card"
        extractor = FakeExtractor(bad, good_profile())
        run = extract_profile(mcas_card, extractor)
        assert run.stop == "clean"
        assert run.attempts == 2
        # second call was told what was wrong with the first
        assert [f.flag for f in extractor.calls[1]] == ["quote-not-in-card"]

    def test_the_same_findings_twice_running_is_a_fixpoint(self, mcas_card):
        bad = good_profile()
        bad.personal_data.quote = "not in the card"
        run = extract_profile(mcas_card, FakeExtractor(bad))
        assert run.stop == "fixpoint"
        assert run.attempts == 2
        assert [f.flag for f in run.findings] == ["quote-not-in-card"]

    def test_the_attempt_cap_publishes_with_open_findings(self, mcas_card):
        one, two, three = good_profile(), good_profile(), good_profile()
        one.personal_data.quote = "wrong one"
        two.high_risk.quote = "wrong two"
        three.interacts_with_natural_persons.quote = "wrong three"
        run = extract_profile(mcas_card, FakeExtractor(one, two, three, three), max_attempts=3)
        assert run.stop == "cap"
        assert run.attempts == 3
        assert run.findings  # the last attempt's, attached to the published profile

    def test_a_model_failing_on_the_first_attempt_publishes_an_undetermined_profile(self, mcas_card):
        run = extract_profile(mcas_card, FakeExtractor(error=RuntimeError("no key")))
        assert run.stop == "failed"
        assert "no key" in run.error
        assert run.profile == Profile()
        assert run.attempts == 1

    def test_a_model_failing_on_a_retry_keeps_the_last_proposal_and_its_findings(self, mcas_card):
        """Every exit publishes what it has: the person gets the good facts and
        the flagged one, not three empty selects."""
        first = good_profile()
        first.personal_data.quote = "not in the card"
        run = extract_profile(mcas_card, FakeExtractor(first, raise_on=2, error=RuntimeError("rate limit")))
        assert run.stop == "failed"
        assert "rate limit" in run.error
        assert run.attempts == 2
        assert run.profile.high_risk.value == "yes"
        assert run.profile.high_risk.annex_iii_point == "5(b)"
        assert [f.flag for f in run.findings] == ["quote-not-in-card"]

    def test_an_extractor_returning_nothing_is_a_failed_run_not_a_crash(self, mcas_card):
        class Nothing:
            def propose(self, card, findings=()):
                return None

        run = extract_profile(mcas_card, Nothing())
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
    def test_it_puts_the_skill_in_the_system_prompt(self, mcas_card):
        complete = FakeCompleter()
        profile = ProfileExtractor(complete=complete).propose(mcas_card)
        assert profile == good_profile()
        system, _ = complete.calls[0]
        assert "Annex III" in system
        assert not system.startswith("---")          # frontmatter stripped

    def test_the_user_message_carries_the_whole_card(self, mcas_card):
        complete = FakeCompleter()
        ProfileExtractor(complete=complete).propose(mcas_card)
        _, user = complete.calls[0]
        assert mcas_card.system_name in user
        assert mcas_card.open_issues[0][:40] in user

    def test_on_a_retry_the_findings_are_in_the_message(self, mcas_card):
        complete = FakeCompleter()
        ProfileExtractor(complete=complete).propose(
            mcas_card,
            findings=[Finding(fact="personal_data", flag="quote-not-in-card", detail="x")],
        )
        _, user = complete.calls[0]
        assert "quote-not-in-card" in user
        assert "personal_data" in user

    def test_a_fenced_answer_with_prose_around_it_still_parses(self, mcas_card):
        answer = "Sure!\n```json\n" + good_profile().model_dump_json() + "\n```\nHope that helps."
        profile = ProfileExtractor(complete=FakeCompleter(answer)).propose(mcas_card)
        assert profile.high_risk.value == "yes"

    def test_an_answer_with_no_json_raises_so_the_loop_can_publish_the_failure(self, mcas_card):
        with pytest.raises(ValueError, match="no JSON object"):
            ProfileExtractor(complete=FakeCompleter("I cannot help with that.")).propose(mcas_card)

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
