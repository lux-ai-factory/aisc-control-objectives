"""A project: one system, from its graph to its tiers.

The flow, and where each part of it lives:

    upload ontology.jsonld ──► create()          the graph is stored as uploaded
             │
             ▼
    AGENTIC 1  extract_profile()                 three facts, each on a quote
             │
             ▼
    the company answers ──► answer()             Confirm or Refuse, never unsure
             │                                   (this is the gate)
             ▼
    AGENTIC 2  map_risks()  ──► map_risks_of()   which objectives mitigate each risk
             │
             ▼
    the assessor rates ──► rate()                1-5 per risk
             │
             ▼
    view()                                       verdicts + tiers, computed, never stored

Everything derivable is computed in `view()` rather than written down, so a
corrected answer or a changed rating cannot leave a stale tier behind. What is
written down is what cannot be recomputed: the uploaded bytes, what the model
proposed, what the company decided, what the mapping cost, and the ratings.
"""

from __future__ import annotations

from dataclasses import dataclass

from wizard.applicability import Verdict, decide
from wizard.control_objectives import ControlObjectiveCatalogue
from wizard.db.repository import ProjectRecord, ProjectRepository
from wizard.models.ontology import Ontology
from wizard.models.profile import Answer, Profile
from wizard.prioritising import Priority, prioritise
from wizard.profiling import Extractor, extract_profile
from wizard.risk_mapping import Mapper, map_risks


@dataclass
class ProjectView:
    """A project with everything derived from it, for a page or an API."""

    record: ProjectRecord
    verdicts: list[Verdict]
    priorities: list[Priority]

    @property
    def can_map(self) -> bool:
        """The second workflow is gated on the company's answer: a tier orders
        work that is owed, and nothing is owed until they have answered."""
        return self.record.answer is not None

    @property
    def mapped(self) -> bool:
        return self.record.mapping_run is not None


class Projects:
    """The service the routes call. Owns the order of the flow, nothing else."""

    def __init__(
        self,
        repository: ProjectRepository,
        catalogue: ControlObjectiveCatalogue,
        extractor: Extractor,
        mapper: Mapper,
        model: str = "",
    ):
        self._repository = repository
        self._catalogue = catalogue
        self._extractor = extractor
        self._mapper = mapper
        self._model = model

    # ── the flow ──────────────────────────────────────────────────────────

    def create(self, name: str, jsonld: str, raw: object) -> ProjectView:
        """Upload, then run the first workflow. The profile run is saved
        whatever happened to it: a failed extraction still leaves a project
        whose three questions a person can answer by hand."""
        ontology = Ontology.from_jsonld(raw)
        record = self._repository.create(name=name or ontology.system_name, ontology=ontology, jsonld=jsonld)
        run = extract_profile(ontology, self._extractor)
        self._repository.save_profile_run(record.id, run, model=self._model)
        return self.view(record.id)

    def replace_graph(self, project_id: str, jsonld: str, raw: object) -> ProjectView:
        """A corrected export. The profile is proposed again, because it was
        read off the graph being replaced; the company's answer stands until
        they change it."""
        ontology = Ontology.from_jsonld(raw)
        self._repository.replace_ontology(project_id, ontology=ontology, jsonld=jsonld)
        run = extract_profile(ontology, self._extractor)
        self._repository.save_profile_run(project_id, run, model=self._model)
        return self.view(project_id)

    def answer(self, project_id: str, answers: dict[str, bool]) -> ProjectView:
        """The company's final word on the three questions."""
        self._repository.answer(project_id, **answers)
        return self.view(project_id)

    def map_risks_of(self, project_id: str) -> ProjectView:
        """The second workflow. Refuses to run before the company has answered,
        because the tiers it feeds would order work nobody has said is owed."""
        record = self._repository.get(project_id)
        if record.answer is None:
            raise PermissionError("answer the three questions before mapping the risks")
        run = map_risks(record.ontology.risks, self._mapper, self._catalogue)
        self._repository.save_mapping_run(project_id, run, model=self._model)
        return self.view(project_id)

    def rate(self, project_id: str, ratings: dict[str, int]) -> ProjectView:
        known = {risk.id for risk in self._repository.get(project_id).ontology.risks}
        unknown = sorted(set(ratings) - known)
        if unknown:
            raise ValueError(f"rated risk(s) not in this system's graph: {', '.join(unknown)}")
        self._repository.rate(project_id, ratings)
        return self.view(project_id)

    def delete(self, project_id: str) -> None:
        self._repository.delete(project_id)

    # ── reading ───────────────────────────────────────────────────────────

    def view(self, project_id: str) -> ProjectView | None:
        record = self._repository.get(project_id)
        return self._derive(record) if record else None

    def list(self) -> list[ProjectView]:
        return [self._derive(record) for record in self._repository.list()]

    def _derive(self, record: ProjectRecord) -> ProjectView:
        """Verdicts and tiers, computed from what is stored. Never written
        back: a changed answer or rating must not leave a stale tier."""
        profile = _profile_from(record)
        verdicts = decide(profile, self._catalogue, confirmed=record.answer is not None)
        priorities = prioritise(
            self._catalogue,
            verdicts,
            record.severity,
            record.mapping_run.mappings if record.mapping_run else {},
            record.ontology.risks,
        )
        return ProjectView(record=record, verdicts=verdicts, priorities=priorities)


def _profile_from(record: ProjectRecord) -> Profile:
    """What the rules are applied to: the company's answer once they have given
    one, the model's proposal until then, so a project shows provisional
    verdicts before it is confirmed rather than nothing at all."""
    if record.answer is not None:
        yes_no: dict[str, Answer] = {
            "high_risk": "yes" if record.answer.high_risk else "no",
            "personal_data": "yes" if record.answer.personal_data else "no",
            "interacts_with_natural_persons": (
                "yes" if record.answer.interacts_with_natural_persons else "no"
            ),
        }
        proposal = record.profile_run.profile if record.profile_run else Profile()
        confirmed = proposal.model_copy(deep=True)
        for name, value in yes_no.items():
            confirmed.fact(name).value = value
        return confirmed
    return record.profile_run.profile if record.profile_run else Profile()
