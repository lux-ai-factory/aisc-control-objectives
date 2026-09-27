"""A project: one system, from its AI Card to its tiers.

    start the assessment ────► create()          the latest version's card, stored as served
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

import copy
from collections.abc import Callable
from dataclasses import dataclass

from aisc_control_objectives.baf_llm import ResolveError
from aisc_control_objectives.control_objectives import ControlObjectiveCatalogue
from aisc_control_objectives.db.repository import ProjectRecord, ProjectRepository
from aisc_control_objectives.models.ontology import Ontology
from aisc_control_objectives.prioritising import Priority, prioritise
from aisc_control_objectives.risk_mapping import Mapper, map_risks

#: platform project pid -> (the mapper to use for it, "<provider>/<model>" of its model)
MapperFor = Callable[[str], tuple[Mapper, str]]


class ModelUnavailable(RuntimeError):
    """The project's model could not be had; nothing was saved."""


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
        repository: ProjectRepository | None,
        catalogue: ControlObjectiveCatalogue,
        mapper: Mapper,
        model: str = "",
        mapper_for: MapperFor | None = None,
    ):
        """`mapper` and `model` are the service's own (its environment). With
        `mapper_for`, each map asks it for the mapper of the assessment's project
        instead, so a project that chose its own model on the platform gets it.

        `repository` is None in the service as deployed: each request works on
        the repository of the one project database it was let into, `bound()`."""
        self._repository = repository
        self._catalogue = catalogue
        self._mapper = mapper
        self._model = model
        self._mapper_for = mapper_for

    def bound(self, repository: ProjectRepository) -> Projects:
        """The same service on one project's repository (a copy, so a subclass
        and its configuration are kept as they are)."""
        bound = copy.copy(self)
        bound._repository = repository
        return bound

    # ── the flow ──────────────────────────────────────────────────────────

    def create(
        self, project: str, name: str, jsonld: str, raw: object, system_id: str
    ) -> ProjectView:
        """Take the AI card of one version. Its risks are what the assessor rates next.

        `system_id` is the card version (a row of project.system in this
        project's database) the assessment is of: one per version. `project` is
        the platform project being assessed; the database is that project, so it
        is not stored.
        """
        ontology = Ontology.from_jsonld(raw)
        record = self._repository.create(
            project=project, name=name or ontology.system_name,
            ontology=ontology, jsonld=jsonld, system_id=system_id,
        )
        return self.view(record.id)

    def has_version(self, system_id: str) -> bool:
        """Whether this project's database has that card version."""
        return self._repository.has_version(system_id)

    def find_by_system(self, system_id: str) -> ProjectView | None:
        record = self._repository.find_by_system(system_id)
        return self._derive(record) if record else None

    def is_latest(self, view: ProjectView) -> bool:
        return self._repository.is_latest(view.record)

    def rate(self, project_id: str, ratings: dict[str, int]) -> ProjectView:
        known = {risk.id for risk in self._repository.get(project_id).ontology.risks}
        unknown = sorted(set(ratings) - known)
        if unknown:
            raise ValueError(f"rated risk(s) not on this card: {', '.join(unknown)}")
        self._repository.rate(project_id, ratings)
        return self.view(project_id)

    def map_risks_of(self, project_id: str) -> ProjectView:
        """The one agentic step: which objectives mitigate each risk.

        The model is the one chosen by the project whose database the
        assessment is in (I5.6)."""
        record = self._repository.get(project_id)
        mapper, model = self._mapper, self._model
        if self._mapper_for is not None:
            try:
                mapper, model = self._mapper_for(record.project)
            except (ResolveError, ValueError) as exc:
                raise ModelUnavailable(str(exc)) from exc
        run = map_risks(record.ontology.risks, mapper, self._catalogue)
        self._repository.save_mapping_run(project_id, run, model=model)
        return self.view(project_id)

    def delete(self, project_id: str) -> None:
        self._repository.delete(project_id)

    # ── reading ───────────────────────────────────────────────────────────

    def view(self, project_id: str) -> ProjectView | None:
        record = self._repository.get(project_id)
        return self._derive(record) if record else None

    def list(self) -> list[ProjectView]:
        """The assessments of this project, and no other's (the database is the project)."""
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
