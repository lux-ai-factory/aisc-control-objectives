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


class TestRecognising:
    def test_the_real_export_is_recognised(self, fixtures_dir):
        raw = json.loads((fixtures_dir / "mcas.ontology.jsonld").read_text())
        assert Ontology.looks_like_one(raw)

    def test_a_system_card_is_not_a_graph(self, mcas_raw):
        assert not Ontology.looks_like_one(mcas_raw)

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

    def test_the_annex_iv_answers_come_with_their_citations(self, mcas):
        assert len(mcas.answers) == 13
        answer = mcas.answers[0]
        assert answer.citation.startswith("Annex IV")
        assert answer.text


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
