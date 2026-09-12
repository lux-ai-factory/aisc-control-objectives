"""SystemCard — typed view over Qualification.systemCardJson.

The card JSON alone (the HTTP-exposed artifact) is the wizard's input; the
profile extractor reads its prose and findings, nothing else derives from it.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

class Finding(BaseModel):
    """One topic of the card. `article`/`references` are carried by some card
    versions and absent from the running qualification's output; the wizard
    treats them as hints, never as required input."""

    title: str
    summary: str
    article: str = ""
    points: list[str] = Field(default_factory=list)
    references: list[str] = Field(default_factory=list)


class TargetSystem(BaseModel):
    category: str
    subcategory: str


class Classification(BaseModel):
    sectors: list[str] = Field(default_factory=list)
    target_systems: list[TargetSystem] = Field(default_factory=list)


class SystemCard(BaseModel):
    system_name: str
    system_version: str
    qualification_id: str
    provider: str = ""
    description: str = ""
    overview: str = ""
    target_use_case: str = ""
    target_users: str = ""
    classification: Classification
    findings: list[Finding] = Field(default_factory=list)
    open_issues: list[str] = Field(default_factory=list)

    @classmethod
    def from_card_json(cls, raw: dict) -> SystemCard:
        return cls.model_validate(raw)
