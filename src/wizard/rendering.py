"""Render an AssessmentPlan to a dimension-first HTML report and PDF (Phase 3).

Mirrors the `aisc/qualification` renderer pattern (Jinja2 template + WeasyPrint)
but lives inside the Wizard service. The report is organised by trustworthiness
dimension: per dimension its status, what the card already addresses, residual
gaps, and the recommended items with rationale resolved from the plan.

WeasyPrint is imported lazily so importing this module (and the API) never
requires the native PDF stack until a PDF is actually requested.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from wizard.models.plan import AssessmentPlan, ProposedItem

_TEMPLATES = Path(__file__).parent / "templates"


def _lookup_item(item_id: str, items_by_id: dict[str, ProposedItem]):
    return items_by_id.get(item_id)


@lru_cache(maxsize=1)
def _env() -> Environment:
    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATES)),
        autoescape=select_autoescape(["html", "xml", "j2"]),
    )
    env.filters["lookup_item"] = _lookup_item
    return env


@lru_cache(maxsize=1)
def _css() -> str:
    return (_TEMPLATES / "styles.css").read_text(encoding="utf-8")


def render_plan_html(plan: AssessmentPlan) -> str:
    """The dimension-first report as a standalone HTML document."""
    items_by_id = {
        item.item_id: item
        for item in (*plan.tests, *plan.datasets, *plan.checklists)
    }
    return _env().get_template("plan.html.j2").render(
        plan=plan,
        dimensions=plan.dimensions,
        items_by_id=items_by_id,
        css=_css(),
    )


def render_plan_pdf(plan: AssessmentPlan) -> bytes:
    """The same report rendered to PDF bytes via WeasyPrint."""
    from weasyprint import HTML

    html = render_plan_html(plan)
    return HTML(string=html).write_pdf()
