"""A project: one system, from its AI Card to its tiers.

    upload the AI Card ──────► create()          stored as the bytes uploaded
             │                                   its risks become rows
             ▼
    the assessor rates ─────► rate()             1-5 per risk
             │
             ▼
    AGENTIC  map_risks() ───► map_risks_of()     which objectives mitigate each risk
             │
             ▼
    view()                                       tiers, computed, never stored

The whole catalogue is the register; the risks decide the order. Everything
derivable is computed in `view()` rather than written down, so a changed rating
cannot leave a stale tier behind. What is written down is what cannot be
recomputed: the uploaded bytes, the ratings, and what the mapping cost.
"""

from __future__ import annotations

from dataclasses import dataclass

from aisc_control_objectives.control_objectives import ControlObjectiveCatalogue
from aisc_control_objectives.db.repository import ProjectRecord, ProjectRepository
from aisc_control_objectives.models.ontology import Ontology
from aisc_control_objectives.prioritising import Priority, prioritise
from aisc_control_objectives.risk_mapping import Mapper, map_risks


@dataclass
class ProjectView:
    """A project with its tiers, for a page or an API."""

    record: ProjectRecord
    priorities: list[Priority]

    @property
    def mapped(self) -> bool:
        return self.record.mapping_run is not None

    @property
    def rated(self) -> int:
        """How many of its risks the assessor has rated."""
        return len(self.record.severity.ratings)


class Projects:
    """The service the routes call. Owns the order of the flow, nothing else."""

    def __init__(
        self,
        repository: ProjectRepository,
        catalogue: ControlObjectiveCatalogue,
        mapper: Mapper,
        model: str = "",
    ):
        self._repository = repository
        self._catalogue = catalogue
        self._mapper = mapper
        self._model = model

    # ── the flow ──────────────────────────────────────────────────────────

    def create(self, name: str, jsonld: str, raw: object) -> ProjectView:
        """Take the AI Card. Its risks are what the assessor rates next."""
        ontology = Ontology.from_jsonld(raw)
        record = self._repository.create(
            name=name or ontology.system_name, ontology=ontology, jsonld=jsonld
        )
        return self.view(record.id)

    def replace_card(self, project_id: str, jsonld: str, raw: object) -> ProjectView:
        """A corrected card. Ratings for risks it still has are kept; the
        mapping goes, because it was bought against the card being replaced."""
        ontology = Ontology.from_jsonld(raw)
        self._repository.replace_ontology(project_id, ontology=ontology, jsonld=jsonld)
        return self.view(project_id)

    def rate(self, project_id: str, ratings: dict[str, int]) -> ProjectView:
        known = {risk.id for risk in self._repository.get(project_id).ontology.risks}
        unknown = sorted(set(ratings) - known)
        if unknown:
            raise ValueError(f"rated risk(s) not on this card: {', '.join(unknown)}")
        self._repository.rate(project_id, ratings)
        return self.view(project_id)

    def map_risks_of(self, project_id: str) -> ProjectView:
        """The one agentic step: which objectives mitigate each risk."""
        record = self._repository.get(project_id)
        run = map_risks(record.ontology.risks, self._mapper, self._catalogue)
        self._repository.save_mapping_run(project_id, run, model=self._model)
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
        """Tiers, computed from what is stored. Never written back: a changed
        rating must not leave a stale tier."""
        priorities = prioritise(
            self._catalogue,
            record.severity,
            record.mapping_run.mappings if record.mapping_run else {},
            record.ontology.risks,
        )
        return ProjectView(record=record, priorities=priorities)
