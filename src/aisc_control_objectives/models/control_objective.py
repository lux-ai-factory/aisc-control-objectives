"""The AI Act control objectives: this service's domain data.

One `ControlObjective` per row of the requirements CSV. The CSV is authored
outside this repo (the requirements-to-methodologies work), so the model keeps
every column verbatim and derives only what the service relies on: the macro
requirement, the assessment mode, and the *regime* of each legal basis, which
is what applicability is decided on.

Everything derived is validated at construction, so a re-exported CSV that
breaks a convention fails when it is loaded, naming the row, and never at the
first request.
"""

from __future__ import annotations

from typing import ClassVar, Literal

from pydantic import BaseModel, Field, computed_field, model_validator

#: How an objective is assessed. "Control + Test" is a paired objective: an
#: organisational control *and* a technical test, both required.
AssessmentMode = Literal["Control", "Test", "Control + Test"]

#: What governs a legal basis. `ai_act` and `gdpr` are read off the basis
#: text; `conditional` and `voluntary` are the author's note tags and cover
#: the whole row.
Regime = Literal["ai_act", "gdpr", "conditional", "voluntary"]

#: A CONDITIONAL note must name its condition in words this list knows. A note
#: naming none of them fails at load rather than passing as unconditional.
CONDITIONAL_CONDITIONS: tuple[str, ...] = ("natural persons",)


class ControlObjective(BaseModel):
    """A single row: one sub-requirement with its control objective."""

    #: Note tags the author uses. The first three qualify the objective and
    #: are called out on the pages; "Paired" only explains a Control + Test row.
    NOTE_TAGS: ClassVar[tuple[str, ...]] = ("GAP", "CONDITIONAL", "VOLUNTARY", "Paired")

    id: str = Field(pattern=r"^R\d+\.\d+$")
    macro_requirement: str = Field(pattern=r"^R\d+\s+\S")
    legal_basis: str = Field(min_length=1)
    sub_requirement_label: str
    #: The objective itself (CSV column `Control_Objective`).
    text: str = Field(min_length=1)
    assessment_mode: AssessmentMode
    #: Raw `Target` cell, kept for fidelity to the CSV. Not represented anywhere.
    target: str
    standards_grounding: str
    grounding_tier_flag: str
    notes: str = ""

    @model_validator(mode="after")
    def _derivations_hold(self) -> ControlObjective:
        if self.id.split(".", 1)[0] != self.macro_id:
            raise ValueError(f"id {self.id!r} is not under macro requirement {self.macro_id!r}")
        self.regimes  # raises on a basis or a condition it cannot place
        return self

    @computed_field
    @property
    def macro_id(self) -> str:
        """"R1" from "R1 Human Agency and Oversight"."""
        return self.macro_requirement.split(None, 1)[0]

    @computed_field
    @property
    def macro_title(self) -> str:
        """"Human Agency and Oversight" from "R1 Human Agency and Oversight"."""
        return self.macro_requirement.split(None, 1)[1]

    @computed_field
    @property
    def requires_control(self) -> bool:
        return "Control" in self.assessment_mode

    @computed_field
    @property
    def requires_test(self) -> bool:
        return "Test" in self.assessment_mode

    @computed_field
    @property
    def legal_bases(self) -> list[str]:
        """The instruments cited, one per entry ("AI Act Arts. 18, 19" and
        "GDPR Art. 5(1)(e)" are separate bases, articles within one are not)."""
        return [basis.strip() for basis in self.legal_basis.split(";") if basis.strip()]

    @property
    def note_tag(self) -> str:
        """The author's tag opening the note ("GAP", "CONDITIONAL", ...), or ""."""
        head, sep, _ = self.notes.partition(":")
        return head.strip() if sep and head.strip() in self.NOTE_TAGS else ""

    @property
    def note_body(self) -> str:
        return self.notes.partition(":")[2] if self.note_tag else self.notes

    @property
    def condition(self) -> str | None:
        """For a CONDITIONAL row, the words that say when it applies."""
        if self.note_tag != "CONDITIONAL":
            return None
        for phrase in CONDITIONAL_CONDITIONS:
            if phrase in self.notes:
                return phrase
        raise ValueError(f"conditional note names no known condition: {self.notes!r}")

    @computed_field
    @property
    def regimes(self) -> list[Regime]:
        """One regime per entry of `legal_bases`, index-aligned."""
        if self.note_tag == "VOLUNTARY":
            return ["voluntary" for _ in self.legal_bases]
        if self.note_tag == "CONDITIONAL":
            self.condition
            return ["conditional" for _ in self.legal_bases]
        regimes: list[Regime] = []
        for basis in self.legal_bases:
            if "AI Act" in basis:
                regimes.append("ai_act")
            elif "GDPR" in basis:
                regimes.append("gdpr")
            elif regimes and basis.startswith(("Art", "Annex", "Recital")):
                # "AI Act Art. 11; Annex IV": the second cites the same instrument.
                regimes.append(regimes[-1])
            else:
                raise ValueError(f"legal basis {basis!r} fits no regime")
        return regimes

    @property
    def sort_key(self) -> tuple[int, int]:
        """Requirement order, so R9.9 precedes R10.1 (string order would not)."""
        macro, _, sub = self.id[1:].partition(".")
        return (int(macro), int(sub))


class MacroRequirement(BaseModel):
    """A macro requirement (R1 ... R11) with the objectives under it."""

    id: str
    title: str
    objectives: list[ControlObjective] = Field(default_factory=list)

    @property
    def label(self) -> str:
        return f"{self.id} {self.title}"
