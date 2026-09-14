"""The AIRO graph the qualification app exports, and the risks in it.

`ontology.jsonld` is expanded JSON-LD: a flat list of nodes, each with an
`@id`, its `@type`s as full URIs, and its predicates as full URIs too. That is
the system's card in the sense the qualification app means it ("the filled
AIRO graph IS the card"), and it is where the risks live.

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


class Answer(BaseModel):
    """One Annex IV answer, verbatim, with the provision it answers."""

    citation: str = ""
    question_id: str = ""
    text: str = ""


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
        for label, value in (
            ("Source", self.source),
            ("Vulnerability", self.vulnerability),
            ("Consequence", self.consequence),
            ("Impact", self.impact),
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
    """A filled AIRO graph, reduced to what the wizard uses."""

    qualification_id: str = ""
    system_name: str = ""
    risks: list[OntologyRisk] = Field(default_factory=list)
    answers: list[Answer] = Field(default_factory=list)

    def by_id(self, risk_id: str) -> OntologyRisk | None:
        return next((risk for risk in self.risks if risk.id == risk_id), None)

    @staticmethod
    def looks_like_one(raw: Any) -> bool:
        """An AIRO graph names AIRO classes; no other artefact here does."""
        nodes = raw.get("@graph") if isinstance(raw, dict) else raw
        if not isinstance(nodes, list):
            return False
        return any(
            isinstance(node, dict)
            and any(str(t).startswith(AIRO) for t in (node.get("@type") or []))
            for node in nodes
        )

    @classmethod
    def from_jsonld(cls, raw: Any) -> Ontology:
        nodes = raw.get("@graph") if isinstance(raw, dict) else raw
        by_id: dict[str, dict] = {
            node["@id"]: node for node in nodes if isinstance(node, dict) and "@id" in node
        }

        def classes(node: dict) -> set[str]:
            return {_local(t) for t in (node.get("@type") or [])}

        def vair_of(node: dict | None) -> list[str]:
            if not node:
                return []
            return [_local(t) for t in (node.get("@type") or []) if str(t).startswith(VAIR)]

        def text_of(node: dict | None) -> str:
            if not node:
                return ""
            return _literal(node, QUAL + "fullLabel") or _literal(node, LABEL)

        def first(node: dict | None, predicate: str) -> dict | None:
            if not node:
                return None
            refs = _refs(node, predicate)
            return by_id.get(refs[0]) if refs else None

        def pointing_at(target_id: str, predicate: str) -> dict | None:
            """The node whose `predicate` points at this one: the source and the
            control are attached that way round in AIRO."""
            for node in by_id.values():
                if target_id in _refs(node, predicate):
                    return node
            return None

        system = next((n for n in by_id.values() if "AISystem" in classes(n)), None)
        answers = [
            Answer(
                citation=_literal(node, QUAL + "citation"),
                question_id=_literal(node, QUAL + "questionId"),
                text=_literal(node, QUAL + "text"),
            )
            for node in by_id.values()
            if _literal(node, QUAL + "text")
        ]

        risks: list[OntologyRisk] = []
        for node in by_id.values():
            if "Risk" not in classes(node):
                continue
            risk_id = _local(node["@id"])
            source = pointing_at(node["@id"], AIRO + "isRiskSourceFor")
            vulnerability = first(source, AIRO + "exploitsVulnerability")
            consequence = first(node, AIRO + "hasConsequence")
            impact = first(consequence, AIRO + "hasImpact")
            stakeholder = first(impact, AIRO + "hasImpactOnStakeholder")
            control = pointing_at(node["@id"], AIRO + "modifiesRiskConcept")
            follow_up = first(control, AIRO + "isFollowedByControl")
            areas = [
                text_of(by_id.get(ref))
                for ref in _refs(impact or {}, AIRO + "hasImpactOnArea")
            ]
            chain = [node, source, vulnerability, consequence, impact, control, follow_up]
            risks.append(
                OntologyRisk(
                    id=risk_id,
                    text=text_of(node),
                    short_label=_literal(node, LABEL),
                    source=text_of(source),
                    vulnerability=text_of(vulnerability),
                    consequence=text_of(consequence),
                    impact=text_of(impact),
                    stakeholder=text_of(stakeholder),
                    areas=[area for area in areas if area],
                    control=text_of(control),
                    follow_up_control=text_of(follow_up),
                    vair_terms=sorted({term for member in chain for term in vair_of(member)}),
                    provenance=_literal(node, QUAL + "provenance") or "form",
                )
            )

        risks.sort(key=lambda risk: risk.position)
        return cls(
            qualification_id=_literal(system or {}, QUAL + "qualificationId"),
            system_name=text_of(system),
            risks=risks,
            answers=answers,
        )
