"""The qualification export: the system's own risks, as the assessor built them.

The system card is prose *about* a system. The qualification is the structured
thing behind it, and the part the wizard needs is `risks`: one AIRO chain per
row, which the ontology service turns into Risk -> RiskSource -> Vulnerability
-> Consequence -> Impact -> AreaOfImpact -> Stakeholder, with the provider's
own RiskControl hanging off it.

Those are the risks an assessor ranks. Every link but the risk itself is
optional, because a real row often names no vulnerability or no follow-up
control, and refusing to parse those would reject most real qualifications.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, computed_field


class RiskRow(BaseModel):
    """One risk and its chain, as the qualification form captured it."""

    position: int = 0
    #: The risk itself: what could go wrong.
    risk: str = Field(min_length=1)
    #: What gives rise to it (AIRO RiskSource).
    source: str = ""
    #: The weakness it exploits (AIRO Vulnerability).
    vulnerability: str = ""
    #: What follows if it materialises (AIRO Consequence).
    consequence: str = ""
    #: Who bears it: "user", "operator" (AIRO Stakeholder).
    affected: str = ""
    #: Which areas the impact falls in: "right", "freedom", "safety" ...
    impact_areas: list[str] = Field(default_factory=list)
    #: The provider's declared mitigation (AIRO RiskControl).
    control: str = ""
    #: What happens when the control does not hold.
    follow_up_control: str = ""

    @computed_field
    @property
    def id(self) -> str:
        """Matches the node ids the ontology builder mints (`risk0`, `risk1`)."""
        return f"risk{self.position}"

    def as_text(self) -> str:
        """The chain as one passage: what the mapper is shown, and therefore
        what its quotes have to be spans of. Absent links are left out rather
        than labelled, so the model is never shown an empty heading."""
        parts = [f"Risk: {self.risk}"]
        for label, value in (
            ("Source", self.source),
            ("Vulnerability", self.vulnerability),
            ("Consequence", self.consequence),
            ("Declared control", self.control),
            ("Follow-up control", self.follow_up_control),
        ):
            if value.strip():
                parts.append(f"{label}: {value}")
        if self.affected:
            parts.append(f"Borne by: {self.affected}")
        if self.impact_areas:
            parts.append(f"Impact areas: {', '.join(self.impact_areas)}")
        return "\n".join(parts)


class Qualification(BaseModel):
    """A qualification export, reduced to what the wizard uses."""

    qualification_id: str
    system_name: str
    system_version: str = ""
    risks: list[RiskRow] = Field(default_factory=list)

    @staticmethod
    def looks_like_one(raw: dict) -> bool:
        """Distinguish an export from a system card: both describe one system,
        only one carries risk rows and the form's own id/name fields."""
        return isinstance(raw, dict) and "risks" in raw and "systemName" in raw

    @classmethod
    def from_export(cls, raw: dict) -> Qualification:
        rows = []
        for position, row in enumerate(raw.get("risks") or []):
            rows.append(
                RiskRow(
                    position=row.get("position", position),
                    risk=row.get("risk") or "",
                    source=row.get("source") or "",
                    vulnerability=row.get("vulnerability") or "",
                    consequence=row.get("consequence") or "",
                    affected=row.get("affected") or "",
                    impact_areas=list(row.get("impactAreas") or []),
                    control=row.get("control") or "",
                    follow_up_control=row.get("followUpControl") or "",
                )
            )
        # model_validate rather than __init__, so a JSON that is not an export
        # fails as a ValidationError naming the missing fields, like every
        # other artefact the service is handed.
        return cls.model_validate(
            {
                "qualification_id": raw.get("id"),
                "system_name": raw.get("systemName"),
                "system_version": raw.get("systemVersion", ""),
                "risks": rows,
            }
        )
