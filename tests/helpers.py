"""Shared test doubles and the MCAS spans the profile tests quote.

One copy, so a change to the fixture card or to the extractor's contract is
made in one place.
"""

from __future__ import annotations

from wizard.models.profile import Fact, HighRiskFact, Profile

# Literal spans from tests/fixtures/mcas_system_card.json.
CREDIT = "Evaluates creditworthiness for €100–€5,000 consumer loans"
DEMOGRAPHIC = "limited demographic fields"
CHATBOT = "LLM chatbot that produces natural-language explanations"


def good_profile() -> Profile:
    """A profile whose three quotes are all genuine spans of the MCAS card."""
    return Profile(
        high_risk=HighRiskFact(value="yes", quote=CREDIT, source="target_use_case", annex_iii_point="5(b)"),
        personal_data=Fact(value="yes", quote=DEMOGRAPHIC, source="findings[0].summary"),
        interacts_with_natural_persons=Fact(value="yes", quote=CHATBOT, source="description"),
    )


class FakeExtractor:
    """Returns queued profiles (the last one repeats), records the findings it
    was told about, and can be made to raise on a given attempt."""

    def __init__(self, *profiles: Profile, raise_on: int | None = None, error: Exception | None = None):
        self._profiles = list(profiles) or [good_profile()]
        self._raise_on = raise_on if raise_on is not None else (1 if error else None)
        self._error = error or RuntimeError("provider down")
        self.calls: list[tuple] = []

    def propose(self, card, findings=()):
        self.calls.append(tuple(findings))
        if self._raise_on is not None and len(self.calls) == self._raise_on:
            raise self._error
        return self._profiles.pop(0) if len(self._profiles) > 1 else self._profiles[0]
