"""The applicability profile: the three facts about a system that decide which
control objectives bind it.

The model *proposes* a profile from the card, each fact with the literal span
it relied on; a person confirms or overrides it. Nothing downstream is allowed
to read applicability off anything else.
"""

from __future__ import annotations

from typing import ClassVar, Literal

from pydantic import BaseModel, Field

Answer = Literal["yes", "no", "undetermined"]


class Fact(BaseModel):
    value: Answer = "undetermined"
    #: A literal span copied from the card. Empty only when undetermined.
    quote: str = ""
    #: Where the quote came from, e.g. "findings[3].points[1]". Informational.
    source: str = ""
    rationale: str = ""


class HighRiskFact(Fact):
    #: The Annex III point that makes the system high-risk, e.g. "5(b)".
    annex_iii_point: str = ""


class Profile(BaseModel):
    """Everything the rules table needs. Defaults to fully undetermined."""

    FACTS: ClassVar[tuple[str, ...]] = (
        "high_risk",
        "personal_data",
        "interacts_with_natural_persons",
    )

    #: Is the system high-risk under AI Act Annex III?
    high_risk: HighRiskFact = Field(default_factory=HighRiskFact)
    #: Does it process personal data (GDPR applies)?
    personal_data: Fact = Field(default_factory=Fact)
    #: Does it interact directly with natural persons (AI Act Art. 50)?
    interacts_with_natural_persons: Fact = Field(default_factory=Fact)

    def fact(self, name: str) -> Fact:
        return getattr(self, name)
