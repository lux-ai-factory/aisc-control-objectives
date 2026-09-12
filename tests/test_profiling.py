"""The profile extractor: the model proposes three facts with quotes; the
controls check the quotes; the loop is bounded and always publishes.

No network: the extractor is driven by fakes.
"""

from __future__ import annotations

import json

import pytest

from wizard.models.profile import Fact, Profile
from wizard.profiling import (
    ProfileExtractor,
    ProfileRun,
    card_text,
    extract_profile,
    load_skill,
    run_controls,
)

from helpers import FakeExtractor, good_profile  # noqa: E402


class TestCardText:
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


class FakeClient:
    """A parse-shaped LLM client that returns a queued Profile and records the call."""

    def __init__(self, profile: Profile):
        self.calls: list[dict] = []
        outer = self

        class _Messages:
            def parse(self, **kwargs):
                outer.calls.append(kwargs)
                return type("Parsed", (), {"parsed_output": profile})()

        self.messages = _Messages()


class TestProfileExtractor:
    def test_it_asks_for_a_profile_with_the_skill_as_system_prompt(self, mcas_card):
        client = FakeClient(good_profile())
        extractor = ProfileExtractor(client=client, model="openai/gpt-4o")
        profile = extractor.propose(mcas_card)
        assert profile == good_profile()
        call = client.calls[0]
        assert call["model"] == "openai/gpt-4o"
        assert call["output_format"] is Profile
        assert "Annex III" in call["system"]
        assert "---" not in call["system"][:5]          # frontmatter stripped

    def test_the_user_message_carries_the_whole_card(self, mcas_card):
        client = FakeClient(good_profile())
        ProfileExtractor(client=client, model="m").propose(mcas_card)
        text = json.dumps(client.calls[0]["messages"])
        assert mcas_card.system_name in text
        assert mcas_card.open_issues[0][:40] in text

    def test_on_a_retry_the_findings_are_in_the_message(self, mcas_card):
        from wizard.profiling import Finding

        client = FakeClient(good_profile())
        ProfileExtractor(client=client, model="m").propose(
            mcas_card,
            findings=[Finding(fact="personal_data", flag="quote-not-in-card", detail="x")],
        )
        text = json.dumps(client.calls[0]["messages"])
        assert "quote-not-in-card" in text
        assert "personal_data" in text


def test_the_skill_ships_with_the_package_and_states_the_rules():
    skill = load_skill("extracting-the-applicability-profile")
    assert not skill.startswith("---")
    for phrase in ("literal", "Annex III", "undetermined", "natural persons", "personal data"):
        assert phrase in skill, phrase


def test_an_unknown_skill_fails_loudly():
    with pytest.raises(FileNotFoundError):
        load_skill("no-such-skill")
