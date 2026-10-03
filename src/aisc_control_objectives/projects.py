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
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field

from aisc_control_objectives.baf_llm import ResolveError
from aisc_control_objectives.control_objectives import ControlObjectiveCatalogue
from aisc_control_objectives.db.repository import ProjectRecord, ProjectRepository
from aisc_control_objectives.library import FULL_AI_ACT, Library
from aisc_control_objectives.models.ontology import Ontology
from aisc_control_objectives.prioritising import Priority, Severity, prioritise
from aisc_control_objectives.risk_mapping import MappedObjective, Mapping, Mapper, MappingRun, map_risks

#: platform project pid -> (the mapper to use for it, "<provider>/<model>" of its model)
MapperFor = Callable[[str], tuple[Mapper, str]]


class ModelUnavailable(RuntimeError):
    """The project's model could not be had; nothing was saved."""


@dataclass
class ProjectView:
    """A project with its tiers, for a page or an API."""

    record: ProjectRecord
    priorities: list[Priority]
    #: The objectives of the profile version it runs on.
    catalogue: ControlObjectiveCatalogue | None = None
    #: {"id", "version", "label", "update"}: the profile, and a newer version of it if one is out.
    profile: dict | None = None
    #: What the last profile switch dropped (only on the view a switch returns).
    dropped: list[str] = field(default_factory=list)

    @property
    def mapped(self) -> bool:
        return self.record.mapping_run is not None

    @property
    def rated(self) -> int:
        """How many of its risks the assessor has rated (impact, likelihood or both)."""
        severity = self.record.severity
        return len(set(severity.impact) | set(severity.likelihood))

    @property
    def fully_rated(self) -> int:
        """How many of its risks have both impact and likelihood saved."""
        severity = self.record.severity
        return len(set(severity.impact) & set(severity.likelihood))


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
        self, project: str, name: str, jsonld: str, raw: object, system_id: str, record=None
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
            ontology=ontology, jsonld=jsonld, system_id=system_id, record=record,
        )
        # a new card version's assessment starts on the profile the previous one ran on
        previous = self._repository.previous_of(record)
        if previous is not None and previous.profile_version_id is not None:
            self._repository.set_profile(record.id, previous.profile_version_id, keep=set())
        return self.view(record.id)

    # ── the objective profile ─────────────────────────────────────────────

    @property
    def library(self) -> Library:
        """The objective sets and profiles of the project whose database this is."""
        return Library(self._repository.engine, self._catalogue)

    def _catalogue_for(self, record: ProjectRecord) -> ControlObjectiveCatalogue:
        if record.profile_version_id is None:
            return self._catalogue
        return self.library.catalogue_of(record.profile_version_id)

    def use_profile(self, project_id: str, profile_id: str, record=None) -> ProjectView:
        """Run the assessment on a profile's current version (switching, or taking a newer
        version). Mappings and selected objectives outside it are dropped; the view says which."""
        library = self.library
        if profile_id == FULL_AI_ACT:
            version_id = None
        else:
            try:
                version_id = library.get_profile(profile_id).current.id
            except LookupError as exc:
                raise ValueError(f"no profile {profile_id}") from exc
        keep = {o.id for o in library.catalogue_of(version_id)}
        dropped = self._repository.set_profile(project_id, version_id, keep, record=record)
        view = self.view(project_id)
        view.dropped = dropped
        return view

    def has_version(self, system_id: str) -> bool:
        """Whether this project's database has that card version."""
        return self._repository.has_version(system_id)

    def find_by_system(self, system_id: str) -> ProjectView | None:
        record = self._repository.find_by_system(system_id)
        return self._derive(record) if record else None

    def is_latest(self, view: ProjectView) -> bool:
        return self._repository.is_latest(view.record)

    def rate(self, project_id: str, impact: dict[str, int], likelihood: dict[str, int] | None = None,
             comments: dict[str, str] | None = None, record=None) -> ProjectView:
        """Impacts, likelihoods and, optionally, comments on the rating ("" or blank clears one).
        Everything is checked before anything is written."""
        likelihood = likelihood or {}
        known = {risk.id for risk in self._repository.get(project_id).ontology.risks}
        unknown = sorted((set(impact) | set(likelihood) | set(comments or {})) - known)
        if unknown:
            raise ValueError(f"risk(s) not on this card: {', '.join(unknown)}")
        cleaned = None if comments is None else {rid: (text or "").strip() for rid, text in comments.items()}
        # the checks: each part 1-5, each comment short enough
        Severity(impact=impact, likelihood=likelihood, comments={rid: t for rid, t in (cleaned or {}).items() if t})
        self._repository.rate(project_id, impact, likelihood, cleaned, record=record)
        return self.view(project_id)

    def set_keys(self, project_id: str, keys: dict[str, bool], record=None) -> ProjectView:
        """The assessor's key choices: objective id -> key. An id outside the profile is refused."""
        catalogue = self._catalogue_for(self._repository.get(project_id))
        unknown = sorted(oid for oid in keys if catalogue.by_id(oid) is None)
        if unknown:
            raise ValueError(f"not in this assessment's objective profile: {', '.join(unknown)}")
        self._repository.save_keys(project_id, {oid: bool(v) for oid, v in keys.items()}, record=record)
        return self.view(project_id)

    def map_risks_of(self, project_id: str, on_save=None) -> ProjectView:
        """The one agentic step: which objectives mitigate each risk.

        The model is the one chosen by the project whose database the
        assessment is in (I5.6). `on_save`, the caller's ledger events, gets the run (its id, outcome,
        model and each model call) inside the save's transaction (ledger phase 6); a run that raises is
        recorded too, in a transaction of its own, then raised again."""
        from aisc_control_objectives import ledger

        record = self._repository.get(project_id)
        mapper, model = self._mapper, self._model
        if self._mapper_for is not None:
            try:
                mapper, model = self._mapper_for(record.project)
            except (ResolveError, ValueError) as exc:
                raise ModelUnavailable(str(exc)) from exc
        run_id, calls = str(uuid.uuid4()), ledger.Calls()
        if hasattr(mapper, "_complete"):                              # each model call, timed, for ai.llm_call
            mapper = copy.copy(mapper)
            mapper._complete = calls.recording(mapper._complete, "mapping")
        try:
            run = map_risks(record.ontology.risks, mapper, self._catalogue_for(record))
        except Exception as exc:
            if on_save is not None:
                outcome = {"run_id": run_id, "run": None, "error": str(exc), "model": model, "calls": calls}
                self._repository.recording(lambda session: on_save(session, outcome))
            raise
        outcome = {"run_id": run_id, "run": run, "error": run.error, "model": model, "calls": calls}
        self._repository.save_mapping_run(
            project_id, run, model=model, selected=self._scope(record, run), run_id=run_id,
            record=None if on_save is None else (lambda session, change: on_save(session, {**change, **outcome})),
        )
        return self.view(project_id)

    def map_by_hand(self, project_id: str, risk_id: str, objective_ids: list[str], on_save=None) -> ProjectView:
        """A person's mapping of one risk (replaces that risk's mapping, 2026-10-01).

        An objective the risk already had keeps its row as it was (the AI's quote and source);
        one added is the person's. The selection follows the change as after a mapping (D1, D2).
        What it replaces is kept in mapping_archive; `on_save` gets the objectives before and after."""
        record = self._repository.get(project_id)
        if risk_id not in {risk.id for risk in record.ontology.risks}:
            raise ValueError(f"no risk {risk_id} on this card")
        catalogue = self._catalogue_for(record)
        unknown = sorted({oid for oid in objective_ids if catalogue.by_id(oid) is None})
        if unknown:
            raise ValueError(f"not in this assessment's objective profile: {', '.join(unknown)}")
        before = record.mapping_run.mappings if record.mapping_run else {}
        kept = {item.objective_id: item for item in before.get(risk_id, Mapping(risk_id=risk_id)).objectives}
        items = [kept.get(oid) or MappedObjective(objective_id=oid, source="person")
                 for oid in self._in_order(catalogue, set(objective_ids))]
        after = MappingRun(mappings={**before, risk_id: Mapping(risk_id=risk_id, objectives=items)})
        self._repository.save_risk_mapping(
            project_id, risk_id, items, selected=self._scope(record, after), record=on_save)
        return self.view(project_id)

    @staticmethod
    def _in_order(catalogue: ControlObjectiveCatalogue, objective_ids) -> list[str]:
        """Catalogue order (O9 before O10); an id the catalogue lacks goes last."""
        def key(oid):
            found = catalogue.by_id(oid)
            return (0, found.sort_key, oid) if found else (1, (), oid)
        return sorted(objective_ids, key=key)

    def _scope(self, record: ProjectRecord, run: MappingRun) -> list[str]:
        """What the assessment takes forward to step 4: what its matrix holds, in catalogue order
        (the matrix is the selection, 2026-10-01)."""
        return self._in_order(self._catalogue_for(record), _mapped_ids(run))

    def delete(self, project_id: str, record=None) -> None:
        self._repository.delete(project_id, record=record)

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
        catalogue = self._catalogue_for(record)
        priorities = prioritise(
            catalogue,
            record.severity,
            record.mapping_run.mappings if record.mapping_run else {},
            record.ontology.risks,
            keys=record.keys,
        )
        return ProjectView(record=record, priorities=priorities, catalogue=catalogue,
                           profile=self.library.version_info(record.profile_version_id))


def _mapped_ids(run: MappingRun | None) -> set[str]:
    """Every objective a mapping linked to at least one risk."""
    if run is None:
        return set()
    return {item.objective_id for mapping in run.mappings.values() for item in mapping.objectives}
