"""Typed views over catalogue tools and control checklists.

`from_seed` constructors accept the seed-file shapes (tools_seed.json,
controls_seed.json) used in tests; the live MCP/HTTP clients will normalize
API responses into the same models, so matching code has one input shape.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from wizard.matching.articles import article_keys


class CatalogueTool(BaseModel):
    slug: str
    name: str
    description: str = ""
    tag_slugs: set[str] = Field(default_factory=set)
    target_legal_requirements: str = ""
    control_topic: str = ""

    @classmethod
    def from_seed(cls, entry: dict) -> "CatalogueTool":
        metadata = entry.get("metadata") or {}
        return cls(
            slug=entry.get("slug") or entry["name"],
            name=entry["name"],
            description=entry.get("description") or "",
            tag_slugs=set(entry.get("tag_slugs") or []),
            target_legal_requirements=metadata.get("target_legal_requirements") or "",
            control_topic=metadata.get("control_topic") or "",
        )

    def article_keys(self) -> set[str]:
        return article_keys(self.target_legal_requirements) | article_keys(
            self.description
        )


class ChecklistQuestion(BaseModel):
    order: int
    text: str
    article: str | None = None
    category: str | None = None


class ChecklistDoc(BaseModel):
    slug: str
    name: str
    description: str = ""
    control_topic: str = ""
    dimension_slug: str = ""
    questions: list[ChecklistQuestion] = Field(default_factory=list)

    @classmethod
    def from_seed(cls, entry: dict) -> "ChecklistDoc":
        metadata = entry.get("metadata") or {}
        return cls(
            slug=entry.get("slug") or entry["name"],
            name=entry["name"],
            description=entry.get("description") or "",
            control_topic=metadata.get("control_topic") or "",
            dimension_slug=entry.get("dimension_slug") or "",
            questions=[
                ChecklistQuestion(
                    order=q.get("order", i),
                    text=q["text"],
                    article=q.get("article"),
                    category=q.get("category"),
                )
                for i, q in enumerate(entry.get("questions") or [])
            ],
        )

    def article_keys(self) -> set[str]:
        keys = article_keys(self.description)
        for question in self.questions:
            keys |= article_keys(question.article)
        return keys
