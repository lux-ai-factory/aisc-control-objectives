"""Catalogue HTTP client (SPEC §2.2) — loads the live catalogue's tools and
splits them into tests (`CatalogueTool`) and control checklists (`ChecklistDoc`).

The catalogue2 backend serves both tests and controls from one endpoint
(`GET /tool/?detailed=true`); a tool is a control when it carries a `type` tag
whose slug names a control/framework, otherwise it is a test. This mirrors the
split the catalogue UI itself uses (frontend `Catalogue.tsx`), so the wizard
recommends from exactly what the catalogue contains.
"""

from __future__ import annotations

import httpx

from wizard.models.catalogue import CatalogueTool, ChecklistDoc, ChecklistQuestion


def _tag_slugs(entry: dict) -> set[str]:
    return {t["slug"] for t in entry.get("tags") or [] if t.get("slug")}


def _type_slugs(entry: dict) -> set[str]:
    return {
        t["slug"]
        for t in entry.get("tags") or []
        if t.get("section") == "type" and t.get("slug")
    }


def _dimension_slug(entry: dict) -> str:
    for t in entry.get("tags") or []:
        if t.get("section") == "dimension" and t.get("slug"):
            return t["slug"]
    return ""


def _is_control(entry: dict) -> bool:
    return any(
        "control" in slug or "framework" in slug for slug in _type_slugs(entry)
    )


def _to_tool(entry: dict) -> CatalogueTool:
    metadata = entry.get("metadata") or {}
    return CatalogueTool(
        slug=entry.get("slug") or entry["name"],
        name=entry["name"],
        description=entry.get("description") or "",
        tag_slugs=_tag_slugs(entry),
        target_legal_requirements=metadata.get("target_legal_requirements") or "",
    )


def _to_checklist(entry: dict) -> ChecklistDoc:
    metadata = entry.get("metadata") or {}
    questions = [
        ChecklistQuestion(
            order=q.get("order", i),
            text=q["text"],
            article=q.get("article"),
            category=q.get("category"),
        )
        for i, q in enumerate(entry.get("questions") or [])
    ]
    return ChecklistDoc(
        slug=entry.get("slug") or entry["name"],
        name=entry["name"],
        description=entry.get("description") or "",
        control_topic=metadata.get("control_topic") or "",
        dimension_slug=_dimension_slug(entry),
        questions=questions,
    )


def load_catalogue(
    base_url: str, timeout: float = 30.0
) -> tuple[list[CatalogueTool], list[ChecklistDoc]]:
    """Fetch the catalogue and partition it into (tests, checklists).

    Raises httpx.HTTPError on transport/HTTP failure — the caller decides
    whether that is fatal (composition root) or recoverable.
    """
    url = base_url.rstrip("/") + "/tool/?detailed=true"
    resp = httpx.get(url, timeout=timeout)
    resp.raise_for_status()
    entries = resp.json()

    tools: list[CatalogueTool] = []
    checklists: list[ChecklistDoc] = []
    for entry in entries:
        if _is_control(entry):
            checklists.append(_to_checklist(entry))
        else:
            tools.append(_to_tool(entry))
    return tools, checklists
