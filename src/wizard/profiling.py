"""Propose the applicability profile from a system card, and check it.

The shape is the qualification app's ontology filler: the model proposes, each
fact carrying the literal span it relied on; deterministic controls check the
spans against the card; the loop is bounded and every exit publishes, with the
open findings attached, because the review page is where a person corrects it
and a withheld profile leaves them nothing to correct.

No critic here: three facts with verbatim-quote controls do not warrant paying
a second model.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Literal, Protocol, Sequence

from pydantic import BaseModel

from wizard.models.profile import Fact, Profile
from wizard.models.system_card import SystemCard

SKILLS = Path(__file__).resolve().parent / "skills"
MAX_ATTEMPTS = 3

Flag = Literal["quote-not-in-card", "quote-missing", "annex-point-missing"]


class Finding(BaseModel):
    fact: str
    flag: Flag
    detail: str


# ── the card as text, and the controls ────────────────────────────────────


def card_text(card: SystemCard) -> str:
    """Every string in the card, so a quote can be checked wherever it came from."""
    parts: list[str] = [
        card.system_name,
        card.system_version,
        card.provider,
        card.description,
        card.overview,
        card.target_use_case,
        card.target_users,
        *card.classification.sectors,
    ]
    for target in card.classification.target_systems:
        parts += [target.category, target.subcategory]
    for finding in card.findings:
        parts += [finding.title, finding.article, finding.summary, *finding.points, *finding.references]
    parts += card.open_issues
    return "\n".join(part for part in parts if part)


def _normalise(text: str) -> str:
    """Whitespace-collapsed, case-folded: a quote is still the card's span if
    the model changed a capital or a line break, not if it changed a word."""
    return re.sub(r"\s+", " ", text).strip().casefold()


def run_controls(profile: Profile, card: SystemCard) -> list[Finding]:
    """Every deterministic finding against a proposed profile."""
    haystack = _normalise(card_text(card))
    findings: list[Finding] = []
    for name in Profile.FACTS:
        fact: Fact = profile.fact(name)
        if fact.value == "undetermined":
            continue
        if not fact.quote.strip():
            findings.append(
                Finding(fact=name, flag="quote-missing", detail=f"{name} is {fact.value!r} with no quote from the card")
            )
        elif _normalise(fact.quote) not in haystack:
            findings.append(
                Finding(fact=name, flag="quote-not-in-card", detail=f"the quote for {name} is not a span of the card: {fact.quote!r}")
            )
    if profile.high_risk.value == "yes" and not profile.high_risk.annex_iii_point.strip():
        findings.append(
            Finding(fact="high_risk", flag="annex-point-missing", detail="high_risk is 'yes' without the Annex III point that makes it so")
        )
    return findings


# ── the model side ─────────────────────────────────────────────────────────


def load_skill(name: str) -> str:
    """A skill's body with its frontmatter stripped. Markdown on disk, so the
    behaviour can be changed without touching Python."""
    path = SKILLS / f"{name}.md"
    if not path.is_file():
        raise FileNotFoundError(f"skill {name!r} not found at {path}")
    text = path.read_text(encoding="utf-8")
    return re.sub(r"^---\s*\n.*?\n---\s*\n", "", text, count=1, flags=re.DOTALL).strip()


class Extractor(Protocol):
    def propose(self, card: SystemCard, findings: Sequence[Finding] = ()) -> Profile: ...


class ProfileExtractor:
    """The writer: asks the model for a Profile, with the skill as system prompt."""

    SKILL = "extracting-the-applicability-profile"

    def __init__(self, client, model: str, skill: str | None = None):
        self._client = client
        self._model = model
        self._skill = skill if skill is not None else load_skill(self.SKILL)

    def propose(self, card: SystemCard, findings: Sequence[Finding] = ()) -> Profile:
        lines = [
            "System card (JSON):",
            json.dumps(card.model_dump(), indent=2, ensure_ascii=False),
            "",
        ]
        if findings:
            lines += [
                "Your previous proposal failed these checks. Fix exactly these; keep the rest.",
                *(f"- {f.fact}: {f.flag}: {f.detail}" for f in findings),
                "",
            ]
        lines.append("Now produce the profile.")
        result = self._client.messages.parse(
            model=self._model,
            system=self._skill,
            messages=[{"role": "user", "content": [{"type": "text", "text": "\n".join(lines)}]}],
            output_format=Profile,
            temperature=0,
        )
        return result.parsed_output


# ── the loop ───────────────────────────────────────────────────────────────


class ProfileRun(BaseModel):
    profile: Profile
    #: Findings still open on the published profile.
    findings: list[Finding] = []
    attempts: int = 0
    stop: Literal["clean", "fixpoint", "cap", "failed"] = "clean"
    error: str = ""


def _signature(findings: Sequence[Finding]) -> frozenset[tuple[str, str]]:
    return frozenset((f.fact, f.flag) for f in findings)


def extract_profile(
    card: SystemCard, extractor: Extractor, max_attempts: int = MAX_ATTEMPTS
) -> ProfileRun:
    """Propose, check, re-propose the failing facts; bounded; always publishes."""
    findings: list[Finding] = []
    previous: frozenset[tuple[str, str]] | None = None
    profile = Profile()

    for attempt in range(1, max_attempts + 1):
        try:
            proposed = extractor.propose(card, findings)
            if not isinstance(proposed, Profile):
                raise TypeError(f"extractor returned {type(proposed).__name__}, not a Profile")
        except Exception as exc:  # a dead provider must still publish something
            # ...and what it publishes is the last proposal it had, with that
            # proposal's findings, not three empty facts.
            return ProfileRun(
                profile=profile, findings=findings, attempts=attempt, stop="failed", error=str(exc)
            )
        profile = proposed

        findings = run_controls(profile, card)
        if not findings:
            return ProfileRun(profile=profile, attempts=attempt, stop="clean")

        signature = _signature(findings)
        if signature == previous:
            return ProfileRun(profile=profile, findings=findings, attempts=attempt, stop="fixpoint")
        previous = signature

    return ProfileRun(profile=profile, findings=findings, attempts=max_attempts, stop="cap")
