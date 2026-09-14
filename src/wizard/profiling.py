"""Propose the applicability profile from the system's graph, and check it.

The shape is the qualification app's ontology filler: the model proposes, each
fact carrying the literal span it relied on; deterministic controls check the
spans against the card; the loop is bounded and every exit publishes, with the
open findings attached, because the review page is where a person corrects it
and a withheld profile leaves them nothing to correct.

No critic here: three facts with verbatim-quote controls do not warrant paying
a second model.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Literal, Protocol

from pydantic import BaseModel

from wizard.llm import Completer, parse_into
from wizard.models.ontology import Ontology
from wizard.rounds import MAX_ATTEMPTS, Stop, review
from wizard.skills import load_skill
from wizard.models.profile import Fact, Profile


Flag = Literal["quote-not-in-graph", "quote-missing", "annex-point-missing"]


class Finding(BaseModel):
    fact: str
    flag: Flag
    detail: str


# ── the card as text, and the controls ────────────────────────────────────


def _normalise(text: str) -> str:
    """Whitespace-collapsed, case-folded: a quote is still the card's span if
    the model changed a capital or a line break, not if it changed a word."""
    return re.sub(r"\s+", " ", text).strip().casefold()


def run_controls(profile: Profile, ontology: Ontology) -> list[Finding]:
    """Every deterministic finding against a proposed profile."""
    haystack = _normalise(ontology.as_text())
    findings: list[Finding] = []
    for name in Profile.FACTS:
        fact: Fact = profile.fact(name)
        if fact.value == "undetermined":
            continue
        if not fact.quote.strip():
            findings.append(
                Finding(fact=name, flag="quote-missing", detail=f"{name} is {fact.value!r} with no quote from the graph")
            )
        elif _normalise(fact.quote) not in haystack:
            findings.append(
                Finding(fact=name, flag="quote-not-in-graph", detail=f"the quote for {name} is not a span of the graph: {fact.quote!r}")
            )
    if profile.high_risk.value == "yes" and not profile.high_risk.annex_iii_point.strip():
        findings.append(
            Finding(fact="high_risk", flag="annex-point-missing", detail="high_risk is 'yes' without the Annex III point that makes it so")
        )
    return findings


# ── the model side ─────────────────────────────────────────────────────────


class Extractor(Protocol):
    def propose(self, ontology: Ontology, findings: Sequence[Finding] = ()) -> Profile: ...


class ProfileExtractor:
    """The writer: asks the model for a Profile, with the skill as system prompt.

    BAF is text in, text out, so the answer is parsed and validated here; the
    skill states the shape the model has to produce.
    """

    SKILL = "extracting-the-applicability-profile"

    def __init__(self, complete: Completer, skill: str | None = None):
        self._complete = complete
        self._skill = skill if skill is not None else load_skill(self.SKILL)

    def propose(self, ontology: Ontology, findings: Sequence[Finding] = ()) -> Profile:
        lines = [
            "What the system's graph says about it:",
            "",
            ontology.as_text(),
            "",
        ]
        if findings:
            lines += [
                "Your previous proposal failed these checks. Fix exactly these; keep the rest.",
                *(f"- {f.fact}: {f.flag}: {f.detail}" for f in findings),
                "",
            ]
        lines.append("Now produce the profile.")
        return parse_into(self._complete(self._skill, "\n".join(lines)), Profile)


# ── the loop ───────────────────────────────────────────────────────────────


class ProfileRun(BaseModel):
    profile: Profile
    #: Findings still open on the published profile.
    findings: list[Finding] = []
    attempts: int = 0
    stop: Stop = "clean"
    error: str = ""


def _signature(findings: Sequence[Finding]) -> frozenset[tuple[str, str]]:
    return frozenset((f.fact, f.flag) for f in findings)


def extract_profile(
    ontology: Ontology, extractor: Extractor, max_attempts: int = MAX_ATTEMPTS
) -> ProfileRun:
    """Propose the profile, check it, re-propose the failing facts. Bounded and
    always publishing: see wizard.rounds for the rules."""

    def propose(findings):
        proposed = extractor.propose(ontology, findings)
        if not isinstance(proposed, Profile):
            raise TypeError(f"extractor returned {type(proposed).__name__}, not a Profile")
        return proposed

    round_ = review(
        propose=propose,
        check=lambda profile: run_controls(profile, ontology),
        empty=Profile,
        signature=_signature,
        max_attempts=max_attempts,
    )
    return ProfileRun(
        profile=round_.proposal,
        findings=round_.findings if round_.stop != "clean" else [],
        attempts=round_.attempts,
        stop=round_.stop,
        error=round_.error,
    )
