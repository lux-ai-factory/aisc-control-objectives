"""SystemCard — typed view over Qualification.systemCardJson.

The card stores classification as human labels ("Tabular & Structured Data");
the qualification DB stores the slug forms. The wizard derives slugs from the
labels so the card JSON alone (the HTTP-exposed artifact) is a sufficient
input — `_slugify` mirrors the qualification app's slug convention
(lowercase, "&" and parentheses dropped, word separators → "-").
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field

from wizard.matching.articles import article_keys


def _slugify(label: str) -> str:
    cleaned = re.sub(r"[&()]", " ", label.lower())
    return re.sub(r"[\s/]+", "-", cleaned.replace("-", " ")).strip("-")


class Finding(BaseModel):
    title: str
    article: str
    summary: str
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
    def from_card_json(cls, raw: dict) -> "SystemCard":
        return cls.model_validate(raw)

    @property
    def sector_slugs(self) -> set[str]:
        return {_slugify(s) for s in self.classification.sectors}

    @property
    def target_system_slugs(self) -> set[str]:
        return {
            f"{_slugify(t.category)}:{_slugify(t.subcategory)}"
            for t in self.classification.target_systems
        }

    def article_keys(self) -> set[str]:
        """All keys referenced by findings and open issues — article/annex keys
        plus the synthetic "open-issue-N" keys of article-less open issues, so
        coverage of those issues is claimable and checkable too."""
        keys: set[str] = set()
        for finding in self.findings:
            keys |= article_keys(finding.article)
            keys |= article_keys(finding.references)
        for issue_keys in self.open_issue_keys():
            keys |= issue_keys
        return keys

    def open_issue_keys(self) -> list[set[str]]:
        """Coverage keys per open issue, index-aligned with open_issues.

        An issue citing no article/annex gets a synthetic "open-issue-N" key
        (1-based) — otherwise it could never be covered nor flagged as a gap.
        """
        result = []
        for index, issue in enumerate(self.open_issues, start=1):
            keys = article_keys(issue)
            result.append(keys if keys else {f"open-issue-{index}"})
        return result
