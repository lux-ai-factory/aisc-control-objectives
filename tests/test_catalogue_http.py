"""Catalogue HTTP client → typed models (no network; entries built inline).

These lock the contract by which trustworthiness dimensions arrive from the
live catalogue: a `section: "dimension"` tag stays inside a tool's flat
`tag_slugs` (and is recovered via the registry), while for a control it is
lifted into `dimension_slug`. Both must surface through `dimension_slugs()`.
"""

from wizard.clients.catalogue_http import _is_control, _to_checklist, _to_tool


def _tag(slug, section):
    return {"slug": slug, "section": section}


def test_tool_dimension_recovered_from_tags():
    entry = {
        "name": "Some Eval",
        "slug": "some-eval",
        "tags": [
            _tag("technical-robustness-safety", "dimension"),
            _tag("natural-language-processing", "ai_type"),
            _tag("finance-and-insurance", "sector"),
            _tag("test", "type"),
        ],
    }
    assert not _is_control(entry)
    tool = _to_tool(entry)
    assert tool.dimension_slugs() == ["technical-robustness-safety"]


def test_control_dimension_lifted_and_normalised():
    entry = {
        "name": "Wellbeing Control",
        "slug": "wellbeing-control",
        "tags": [
            # control taxonomy spells it "well-being"
            _tag("societal-environmental-well-being", "dimension"),
            _tag("framework", "type"),
        ],
    }
    assert _is_control(entry)
    checklist = _to_checklist(entry)
    assert checklist.dimension_slug == "societal-environmental-well-being"
    # normalised onto the canonical slug
    assert checklist.dimension_slugs() == ["societal-environmental-wellbeing"]
