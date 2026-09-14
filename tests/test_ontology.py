"""The AIRO graph, as the qualification app exports it.

`ontology.jsonld` is expanded JSON-LD: a flat list of nodes whose predicates
are full URIs. It is the system's card, in the sense the qualification app
means it, and it carries the risks the wizard ranks.

Tested against the real export, not a fixture I invented.
"""

from __future__ import annotations

import json

import pytest

from wizard.models.ontology import Ontology, OntologyRisk


@pytest.fixture(scope="module")
def mcas(fixtures_dir):
    return Ontology.from_jsonld(json.loads((fixtures_dir / "mcas.ontology.jsonld").read_text()))


class TestTheAiCard:
    """What an assessor uploads is the system's AI Card. The qualification app
    exports it two ways, and both are the same card: `ai-card.json`, which
    wraps the graph with the form's facts, and `ontology.jsonld`, the graph on
    its own. The wizard takes either."""

    def test_the_graph_on_its_own_is_a_card(self, fixtures_dir):
        raw = json.loads((fixtures_dir / "mcas.ontology.jsonld").read_text())
        assert Ontology.looks_like_one(raw)
        assert len(Ontology.from_jsonld(raw).risks) == 5

    def test_an_ai_card_json_wrapping_the_graph_is_a_card(self, fixtures_dir):
        graph = json.loads((fixtures_dir / "mcas.ontology.jsonld").read_text())
        card = {
            "system_name": "MicroCredit Assist Score (MCAS)",
            "qualification_id": "cmtvvrnw50000jzyvas8gmn3u",
            "ontology": {"chains": [], "rows": []},
            "ontology_graph": graph,
            "ontology_problems": [],
        }
        assert Ontology.looks_like_one(card)
        assert len(Ontology.from_jsonld(card).risks) == 5

    def test_an_ai_card_with_no_graph_in_it_is_not_enough(self, fixtures_dir):
        """The PDF's payload without its graph: a card a reader can read and
        the wizard cannot work from."""
        assert not Ontology.looks_like_one(
            {"system_name": "X", "ontology": {"chains": []}, "ontology_graph": None}
        )


class TestRecognising:
    def test_the_real_export_is_recognised(self, fixtures_dir):
        raw = json.loads((fixtures_dir / "mcas.ontology.jsonld").read_text())
        assert Ontology.looks_like_one(raw)

    def test_an_arbitrary_list_is_not_a_graph(self):
        assert not Ontology.looks_like_one([{"hello": "world"}])

    def test_a_graph_under_an_at_graph_key_is_recognised(self, fixtures_dir):
        raw = json.loads((fixtures_dir / "mcas.ontology.jsonld").read_text())
        assert Ontology.looks_like_one({"@graph": raw})
        assert len(Ontology.from_jsonld({"@graph": raw}).risks) == 5


class TestTheSystem:
    def test_the_system_it_describes(self, mcas):
        assert "MicroCredit Assist Score" in mcas.system_name
        assert mcas.qualification_id


class TestTheRisks:
    def test_every_risk_node_becomes_a_risk(self, mcas):
        assert len(mcas.risks) == 5
        assert all(isinstance(risk, OntologyRisk) for risk in mcas.risks)

    def test_risks_are_in_the_order_the_assessor_gave_them(self, mcas):
        assert [risk.id for risk in mcas.risks] == [f"risk{n}" for n in range(5)]

    def test_the_full_label_is_preferred_over_the_shortened_one(self, mcas):
        """The graph carries both: a <=60 char label for the card, and the
        assessor's own sentence. The mapper should read the sentence."""
        risk = mcas.by_id("risk2")
        assert risk.text == "Loan officers rubber-stamp the recommendation instead of reviewing it"
        assert risk.short_label == "Officers rubber-stamp the recommendation"

    def test_the_chain_is_followed_through_forward_edges(self, mcas):
        risk = mcas.by_id("risk2")
        assert "The mandatory human review becomes a formality" in risk.consequence
        assert risk.impact
        assert risk.stakeholder
        assert risk.areas == ["Fundamental rights", "Freedom"]

    def test_the_chain_is_followed_through_reverse_edges(self, mcas):
        """`isRiskSourceFor` and `modifiesRiskConcept` point AT the risk, so a
        forward-only walk finds neither the source nor the control."""
        risk = mcas.by_id("risk2")
        assert "Automation bias" in risk.source
        assert "Officer override rates are tracked" in risk.control

    def test_the_fairness_risk_reads_the_same_way(self, mcas):
        risk = mcas.by_id("risk1")
        assert risk.text == "Approval rates diverge across protected groups"
        assert "act as proxies for ethnicity" in risk.source
        assert "Quarterly fairness audit" in risk.control

    def test_a_follow_up_control_is_found_through_the_control(self, mcas):
        risk = mcas.by_id("risk2")
        assert "Withdraw the officer's approval rights" in risk.follow_up_control

    def test_a_missing_link_is_empty_rather_than_an_error(self, mcas):
        """Not every risk names a vulnerability or a follow-up."""
        assert any(not risk.vulnerability for risk in mcas.risks)
        assert all(isinstance(risk.vulnerability, str) for risk in mcas.risks)

    def test_the_vair_terms_are_kept(self, mcas):
        """The typed vocabulary is the graph's own judgement about the risk."""
        terms = {term for risk in mcas.risks for term in risk.vair_terms}
        assert terms
        assert all(":" not in term for term in terms)      # local names, not URIs

    def test_provenance_is_kept(self, mcas):
        assert mcas.by_id("risk2").provenance in {"form", "extracted", "reviewed"}


class TestRiskText:
    def test_the_chain_reads_as_one_passage_for_the_mapper(self, mcas):
        text = mcas.by_id("risk4").as_text()
        for part in ("poisoned", "provenance signing", "Egress allow-list"):
            assert part in text

    def test_the_impact_line_is_dropped_when_it_only_repeats_the_risk(self, mcas):
        """The AIRO builder names the Impact node after the risk it realises,
        so printing both spends tokens saying the same thing twice."""
        risk = mcas.by_id("risk2")
        assert risk.impact == risk.text
        assert risk.as_text().count(risk.text) == 1

    def test_the_stakeholder_is_the_one_bearing_this_risk(self, mcas):
        """The stakeholder node's fullLabel is the form's whole target-users
        answer, shared by every risk, so it identifies nothing about this one
        and is a span the mapper could wrongly quote."""
        risk = mcas.by_id("risk2")
        assert "Primary: bank customers aged 18+" not in risk.as_text()
        assert risk.stakeholder
        assert len(risk.stakeholder) < 90

    def test_an_absent_link_is_not_announced(self, mcas):
        bare = next(risk for risk in mcas.risks if not risk.vulnerability)
        assert "vulnerability" not in bare.as_text().lower()

    def test_the_vair_terms_are_offered_to_the_mapper(self, mcas):
        """The graph's own typing, and a better key than prose: the automation
        bias risk is typed Overreliance and ImpairedDecisionMaking."""
        risk = mcas.by_id("risk2")
        assert {"Overreliance", "ImpairedDecisionMaking"} <= set(risk.vair_terms)
        assert "Overreliance" in risk.as_text()


def test_a_graph_with_no_risks_parses(fixtures_dir):
    raw = json.loads((fixtures_dir / "mcas.ontology.jsonld").read_text())
    without = [n for n in raw if "https://w3id.org/airo#Risk" not in (n.get("@type") or [])]
    assert Ontology.from_jsonld(without).risks == []
