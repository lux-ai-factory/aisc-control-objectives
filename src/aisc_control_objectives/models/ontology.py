"""The AI Card, and the risks in it.

What an assessor uploads is a system's **AI Card**: the qualification app's own
position is that the filled AIRO graph IS the card, and it exports it two ways.
`ai-card.json` wraps the graph together with the form's facts and the view a
reader sees; `ontology.jsonld` is the graph on its own. Both are the same card,
and either is accepted.

The graph is expanded JSON-LD: a flat list of nodes, each with an `@id`, its
`@type`s as full URIs, and its predicates as full URIs too. It is where the
risks live.

Parsed by hand rather than with rdflib: expanded JSON-LD is plain JSON with
predictable edges, and a graph library would be a large dependency for one
traversal. Two of the edges point AT the risk rather than away from it
(`isRiskSourceFor`, `modifiesRiskConcept`), so a forward-only walk finds
neither the source nor the control.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

AIRO = "https://w3id.org/airo#"
VAIR = "https://w3id.org/vair#"
QUAL = "https://lux-ai-factory.github.io/qualification/ns#"
LABEL = "http://www.w3.org/2000/01/rdf-schema#label"


def _local(uri: str) -> str:
    return uri.rsplit("#", 1)[-1].rsplit("/", 1)[-1]


def _values(node: dict, predicate: str) -> list[Any]:
    return node.get(predicate) or []


def _literal(node: dict, predicate: str) -> str:
    for entry in _values(node, predicate):
        if "@value" in entry:
            return str(entry["@value"])
    return ""


def _refs(node: dict, predicate: str) -> list[str]:
    return [entry["@id"] for entry in _values(node, predicate) if "@id" in entry]


class _Graph:
    """The node table, with the four questions this parser asks of it.

    A tiny class rather than five closures inside the parser: the traversal
    reads as a walk over the graph once the graph can answer for itself.
    """

    def __init__(self, nodes: list):
        self.by_id: dict[str, dict] = {
            node["@id"]: node for node in nodes if isinstance(node, dict) and "@id" in node
        }

    def of_class(self, name: str) -> list[dict]:
        return [node for node in self.by_id.values() if name in self.classes(node)]

    @staticmethod
    def classes(node: dict) -> set[str]:
        return {_local(t) for t in (node.get("@type") or [])}

    @staticmethod
    def vair(node: dict | None) -> list[str]:
        return [
            _local(t)
            for t in ((node or {}).get("@type") or [])
            if str(t).startswith(VAIR)
        ]

    @staticmethod
    def text(node: dict | None) -> str:
        """The assessor's own sentence where there is one, else the card label."""
        return _literal(node or {}, QUAL + "fullLabel") or _literal(node or {}, LABEL)

    def target(self, node: dict | None, predicate: str) -> dict | None:
        """The node this one's `predicate` points at."""
        refs = _refs(node or {}, predicate)
        return self.by_id.get(refs[0]) if refs else None

    def source_of(self, node: dict, predicate: str) -> dict | None:
        """The node whose `predicate` points AT this one. AIRO attaches a risk's
        source and its control that way round, so a forward-only walk misses
        both."""
        return next(
            (other for other in self.by_id.values() if node["@id"] in _refs(other, predicate)),
            None,
        )


class OntologyRisk(BaseModel):
    """One risk and its AIRO chain, flattened to the text a reader needs."""

    id: str
    #: The assessor's own sentence (`fullLabel`), falling back to the card label.
    text: str
    #: The <=60 character label the card prints.
    short_label: str = ""
    source: str = ""
    vulnerability: str = ""
    consequence: str = ""
    impact: str = ""
    stakeholder: str = ""
    areas: list[str] = Field(default_factory=list)
    control: str = ""
    follow_up_control: str = ""
    #: VAIR terms on any node of the chain: the graph's own typing of this risk.
    vair_terms: list[str] = Field(default_factory=list)
    provenance: str = "form"

    @property
    def position(self) -> int:
        digits = "".join(character for character in self.id if character.isdigit())
        return int(digits) if digits else 0

    def as_text(self) -> str:
        """The chain as one passage: what the mapper is shown, and therefore
        what its quotes have to be spans of. Absent links are left out rather
        than labelled, so the model is never shown an empty heading."""
        parts = [f"Risk: {self.text}"]
        # The AIRO builder names the Impact node after the risk it realises, so
        # an impact that only echoes the risk is not worth a second line.
        impact = "" if self.impact.strip() == self.text.strip() else self.impact
        for label, value in (
            ("Source", self.source),
            ("Vulnerability", self.vulnerability),
            ("Consequence", self.consequence),
            ("Impact", impact),
            ("Declared control", self.control),
            ("Follow-up control", self.follow_up_control),
        ):
            if value.strip():
                parts.append(f"{label}: {value}")
        if self.stakeholder:
            parts.append(f"Borne by: {self.stakeholder}")
        if self.areas:
            parts.append(f"Impact areas: {', '.join(self.areas)}")
        if self.vair_terms:
            parts.append(f"Typed in VAIR as: {', '.join(self.vair_terms)}")
        return "\n".join(parts)


class Ontology(BaseModel):
    """A filled AIRO graph, reduced to what this service uses."""

    qualification_id: str = ""
    system_name: str = ""
    risks: list[OntologyRisk] = Field(default_factory=list)

    def by_id(self, risk_id: str) -> OntologyRisk | None:
        return next((risk for risk in self.risks if risk.id == risk_id), None)

    @staticmethod
    def graph_in(raw: Any) -> list | None:
        """The AIRO node list inside whatever was uploaded, or None.

        Three shapes reach us: the bare expanded graph, a graph under `@graph`,
        and an `ai-card.json` whose `ontology_graph` holds it. A card without
        its graph is a card a reader can read and this service cannot work from,
        so it is not accepted.
        """
        if isinstance(raw, dict):
            for key in ("ontology_graph", "@graph"):
                inner = raw.get(key)
                if inner is not None:
                    return Ontology.graph_in(inner)
            return None
        return raw if isinstance(raw, list) else None

    @staticmethod
    def looks_like_one(raw: Any) -> bool:
        """An AIRO graph names AIRO classes; no other artefact here does."""
        nodes = Ontology.graph_in(raw)
        if not isinstance(nodes, list):
            return False
        return any(
            isinstance(node, dict)
            and any(str(t).startswith(AIRO) for t in (node.get("@type") or []))
            for node in nodes
        )

    @classmethod
    def from_jsonld(cls, raw: Any) -> Ontology:
        """Parse an AI Card, in whichever shape it arrives."""
        graph = _Graph(cls.graph_in(raw) or [])
        system = next(iter(graph.of_class("AISystem")), None)
        return cls(
            qualification_id=_literal(system or {}, QUAL + "qualificationId"),
            system_name=graph.text(system),
            risks=sorted(
                (cls._risk(graph, node) for node in graph.of_class("Risk")),
                key=lambda risk: risk.position,
            ),
        )

    @staticmethod
    def _risk(graph: _Graph, node: dict) -> OntologyRisk:
        """One risk, walked out to the ends of its chain."""
        source = graph.source_of(node, AIRO + "isRiskSourceFor")
        vulnerability = graph.target(source, AIRO + "exploitsVulnerability")
        consequence = graph.target(node, AIRO + "hasConsequence")
        impact = graph.target(consequence, AIRO + "hasImpact")
        stakeholder = graph.target(impact, AIRO + "hasImpactOnStakeholder")
        control = graph.source_of(node, AIRO + "modifiesRiskConcept")
        follow_up = graph.target(control, AIRO + "isFollowedByControl")
        chain = (node, source, vulnerability, consequence, impact, control, follow_up)

        return OntologyRisk(
            id=_local(node["@id"]),
            text=graph.text(node),
            short_label=_literal(node, LABEL),
            source=graph.text(source),
            vulnerability=graph.text(vulnerability),
            consequence=graph.text(consequence),
            impact=graph.text(impact),
            # The short label, not the full text: a stakeholder node's full text
            # is the form's whole target-users answer, the same for every risk,
            # so it identifies nothing about this one and is a span the mapper
            # could wrongly quote as support.
            stakeholder=_literal(stakeholder or {}, LABEL),
            areas=[
                area
                for ref in _refs(impact or {}, AIRO + "hasImpactOnArea")
                if (area := graph.text(graph.by_id.get(ref)))
            ],
            control=graph.text(control),
            follow_up_control=graph.text(follow_up),
            vair_terms=sorted({term for member in chain for term in graph.vair(member)}),
            provenance=_literal(node, QUAL + "provenance") or "form",
        )
