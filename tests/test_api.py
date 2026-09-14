"""HTTP surface: the catalogue half.

A read-only view over the control objectives plus the effective config,
independent of any system. The project flow is exercised in test_projects.py.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from aisc_control_objectives.api.app import create_app
from aisc_control_objectives.config import RunConfig
from aisc_control_objectives.models.control_objective import ControlObjective
from aisc_control_objectives.control_objectives import ControlObjectiveCatalogue


@pytest.fixture()
def client(objectives, repository):
    """The catalogue routes need no model; a Projects with the stand-in
    mapper is enough."""
    from aisc_control_objectives.api.app import NoMapper
    from aisc_control_objectives.projects import Projects

    projects = Projects(repository, objectives, NoMapper())
    return TestClient(create_app(objectives, projects, base_config=RunConfig()))


class TestConfig:
    def test_config_echoes_the_effective_model(self, client):
        response = client.get("/api/config")
        assert response.status_code == 200
        assert response.json()["model"] == RunConfig().model


class TestControlObjectives:
    def test_lists_every_objective_in_requirement_order(self, client):
        payload = client.get("/api/control-objectives").json()
        assert len(payload) == 50
        assert payload[0]["id"] == "R1.1"
        assert payload[-1]["id"] == "R11.4"

    def test_an_objective_carries_its_derived_routing_fields(self, client):
        payload = client.get("/api/control-objectives").json()
        paired = next(item for item in payload if item["id"] == "R2.1")
        assert paired["macro_id"] == "R2"
        assert paired["requires_control"] is True
        assert paired["requires_test"] is True
        assert "control_targets" not in paired
        assert "test_targets" not in paired

    def test_filter_by_assessment_mode(self, client):
        controls = client.get("/api/control-objectives?mode=control").json()
        tests = client.get("/api/control-objectives?mode=test").json()
        assert len(controls) == 41
        assert len(tests) == 14
        # a paired objective is in both partitions
        assert "R2.1" in {item["id"] for item in controls}
        assert "R2.1" in {item["id"] for item in tests}

    def test_an_unknown_mode_is_rejected(self, client):
        assert client.get("/api/control-objectives?mode=banana").status_code == 422

    def test_fetch_one_objective_by_id(self, client):
        payload = client.get("/api/control-objectives/R1.1").json()
        assert payload["sub_requirement_label"] == "Operator oversight capability"
        assert payload["legal_bases"] == ["AI Act Art. 14"]

    def test_unknown_id_is_404(self, client):
        assert client.get("/api/control-objectives/R99.9").status_code == 404


class TestMacroRequirements:
    def test_lists_the_eleven_macro_requirements_with_their_objectives(self, client):
        payload = client.get("/api/macro-requirements").json()
        assert [macro["id"] for macro in payload] == [f"R{n}" for n in range(1, 12)]
        assert payload[0]["title"] == "Human Agency and Oversight"
        assert sum(len(macro["objectives"]) for macro in payload) == 50


def test_health_is_served(client):
    assert client.get("/health").json() == {"status": "ok"}


class TestObjectivesPage:
    """The root is the interface: every objective rendered server-side."""

    @pytest.fixture()
    def page(self, client):
        response = client.get("/objectives")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")
        return response.text

    def test_every_objective_id_is_on_the_page(self, page, objectives):
        for objective in objectives:
            assert f'class="co-obj-id">{objective.id}<' in page, objective.id

    def test_every_macro_requirement_is_a_section(self, page, objectives):
        for macro in objectives.macro_requirements():
            assert f'id="{macro.id}"' in page
            assert macro.title in page

    def test_objective_text_is_rendered_not_just_ids(self, page):
        assert "A qualified operator can monitor" in page

    def test_the_page_does_not_split_control_from_test(self, page):
        """Assessment mode stays in the data and the API, off the page.

        "Control" itself still occurs — it is in the title and in objective
        text — so this pins the markers of the distinction, not the word.
        """
        for marker in (
            "qf-tag--control",   # the badges
            "qf-tag--test",
            "Assessment mode",   # the filter
            "Need a control",    # the stat tiles
            "Need a test",
            "Need both",
            "data-control",      # the filter hooks
            "data-test",
            "Applies to",        # the target codes
        ):
            assert marker not in page, marker

    def test_page_wears_the_platform_chrome(self, page):
        """It has to read as the same product as the qualification app."""
        assert "laif-logo.svg" in page          # Luxembourg AI Factory mark
        assert "#000fdf" in page.lower()        # brand primary
        assert "#ff007e" in page.lower()        # brand accent
        assert "site-header" in page and "qf-section" in page

    def test_the_logo_is_served(self, client):
        response = client.get("/static/laif-logo.svg")
        assert response.status_code == 200
        assert "svg" in response.headers["content-type"]

    def test_the_counts_are_stated(self, page):
        assert ">50<" in page  # objectives
        assert ">11<" in page  # macro requirements

    def test_notes_survive_to_the_page(self, page):
        assert "thresholds must be documented before the test is run" in page

    def test_the_page_carries_no_api_chrome(self, page):
        assert "/api/control-objectives" not in page
        assert "/docs" not in page

    def test_markup_is_escaped_not_injected(self, repository):
        """Objective text is data: a CSV carrying markup must not become markup.

        Built from its own row rather than by mutating a loaded catalogue: the
        `objectives` fixture is session-scoped, so a mutation here would leak
        into every later test and surface as an unrelated failure.
        """
        catalogue = ControlObjectiveCatalogue(
            [
                ControlObjective(
                    id="R1.1",
                    macro_requirement="R1 Human Agency and Oversight",
                    legal_basis="AI Act Art. 14",
                    sub_requirement_label="Injected",
                    text="<script>alert(1)</script>",
                    assessment_mode="Control",
                    target="G",
                    standards_grounding="",
                    grounding_tier_flag="",
                )
            ]
        )
        from aisc_control_objectives.api.app import NoMapper
        from aisc_control_objectives.projects import Projects

        app = create_app(
            catalogue,
            Projects(repository, catalogue, NoMapper()),
            base_config=RunConfig(),
        )
        page = TestClient(app).get("/objectives").text
        assert "<script>alert(1)</script>" not in page
        assert "&lt;script&gt;" in page
