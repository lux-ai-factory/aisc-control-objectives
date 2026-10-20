"""An assessment: one AI card version, from its card to its scores and key objectives.

    start the assessment ────► create()          the latest version's card, stored as served;
             │                                   its risks become rows
             ▼
    the assessor rates ─────► rate()             impact and likelihood, 1-5 each, per risk
             │
             ▼
    AGENTIC  map_risks() ───► map_risks_of()     which objectives mitigate each risk
             │                map_by_hand()      or a person's mapping of one risk
             ▼
    view()                                       scores and default key objectives, computed, never stored

The risks decide the order of the catalogue. Everything derivable is computed in
`view()` rather than written down, so a changed rating cannot leave a stale score
behind. What is written down is what cannot be recomputed: the uploaded bytes, the
ratings, the mapping, the assessor's key choices, and what the mapping cost.
"""

from __future__ import annotations

import copy
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field

from aisc_control_objectives.baf_llm import ResolveError
from aisc_control_objectives.control_objectives import ControlObjectiveCatalogue
from aisc_control_objectives.db.repository import (
    AssessmentChanged,
    ProjectRecord,
    ProjectRepository,
)
from aisc_control_objectives.library import DEFAULT_PROFILE, Library
from aisc_control_objectives.models.ontology import Ontology
from aisc_control_objectives.prioritising import Priority, Severity, prioritise
from aisc_control_objectives.risk_mapping import (
    MappedObjective,
    Mapper,
    Mapping,
    MappingRun,
    map_risks,
    safe_error,
)

_log = logging.getLogger(__name__)

#: platform project pid -> (the mapper to use for it, "<provider>/<model>" of its model)
MapperFor = Callable[[str], tuple[Mapper, str]]


class ModelUnavailable(RuntimeError):
    """The project's model could not be had; nothing was saved."""


def _mapping_state(record: ProjectRecord) -> tuple:
    """What a run must find unchanged before it saves: the profile, the mappings, the selection."""
    run = record.mapping_run.model_dump_json() if record.mapping_run is not None else None
    return record.profile_version_id, run, record.selected


@dataclass
class ProjectView:
    """An assessment with its priorities, for a page or an API."""

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
        default_profile: str = DEFAULT_PROFILE,
    ):
        """`mapper` and `model` are the service's own (its environment). With
        `mapper_for`, each map asks it for the mapper of the assessment's project
        instead, so a project that chose its own model on the platform gets it.

        `repository` is None in the service as deployed: each request works on
        the repository of the one project database it was let into, `bound()`."""
        self._repository = repository
        #: The profile a project's first assessment starts on.
        self._default_profile = default_profile
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
        # A new card version's assessment starts on the profile the previous one ran on, with its work on
        # the risks the two cards share: the repository does both in the same transaction. The objectives
        # it may carry are that profile's, read here, before the transaction.
        has_previous, version = self._repository.previous_profile(system_id)
        catalogue = self.library.catalogue_of(version) if has_previous else None
        record = self._repository.create(
            project=project, name=name or ontology.system_name,
            ontology=ontology, jsonld=jsonld, system_id=system_id, record=record,
            default_profile_version_id=self.library.pinned_version(self._default_profile),
            carry_objectives={o.id for o in catalogue} if catalogue is not None else None,
            order=(lambda ids: self._in_order(catalogue, ids)) if catalogue is not None else None,
        )
        return self.view(record.id)

    def update_card(self, project_id: str, jsonld: str, raw: object, record=None) -> ProjectView:
        """Put the assessment on its version's card as qualification serves it now, carrying the work on
        the risks that stayed the same; nothing happens when the card is the one it holds."""
        current = self._repository.get(project_id)
        catalogue = self._catalogue_for(current)
        self._repository.update_card(
            project_id, Ontology.from_jsonld(raw), jsonld, digest_before=current.digest,
            carry_objectives={o.id for o in catalogue}, order=lambda ids: self._in_order(catalogue, ids),
            record=record)
        return self.view(project_id)

    # The objective profile

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
        try:
            version_id = library.pinned_version(profile_id)
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

    def rate(self, project_id: str, impact: dict[str, int | None], likelihood: dict[str, int | None] | None = None,
             comments: dict[str, str] | None = None, record=None) -> ProjectView:
        """Impacts, likelihoods and, optionally, comments on the rating. None clears a part, "" or blank
        a comment. Everything is checked before anything is written."""
        likelihood = likelihood or {}
        known = {risk.id for risk in self._repository.get(project_id).ontology.risks}
        unknown = sorted((set(impact) | set(likelihood) | set(comments or {})) - known)
        if unknown:
            raise ValueError(f"risk(s) not on this card: {', '.join(unknown)}")
        cleaned = None if comments is None else {rid: (text or "").strip() for rid, text in comments.items()}
        # the checks: each part given 1-5, each comment short enough
        Severity(impact={rid: v for rid, v in impact.items() if v is not None},
                 likelihood={rid: v for rid, v in likelihood.items() if v is not None},
                 comments={rid: t for rid, t in (cleaned or {}).items() if t})
        self._repository.rate(project_id, impact, likelihood, cleaned, record=record)
        return self.view(project_id)

    def set_keys(self, project_id: str, keys: dict[str, bool], record=None) -> ProjectView:
        """The assessor's key choices: objective id -> key. An id outside the profile is refused."""
        catalogue = self._catalogue_for(self._repository.get(project_id))
        unknown = sorted(oid for oid in keys if catalogue.by_id(oid) is None)
        if unknown:
            raise ValueError(f"not in this assessment's objective profile: {', '.join(unknown)}")
        if record is not None:
            # what was key before the save, defaults included, for the event's added and removed
            was_key = {p.objective_id: p.key for p in self.view(project_id).priorities}
            given = record

            def record(session, change):
                return given(session, {**change, "was_key": was_key})
        self._repository.save_keys(project_id, {oid: bool(v) for oid, v in keys.items()}, record=record)
        return self.view(project_id)

    def reset_keys(self, project_id: str, record=None) -> ProjectView:
        """Remove the assessor's key choices: every objective follows the ratings again."""
        if record is not None:
            # what was key before the reset, choices included, and what is key after it: the defaults
            view = self.view(project_id)
            was_key = {p.objective_id: p.key for p in view.priorities}
            unchosen = copy.copy(view.record)
            unchosen.keys = {}
            is_key = {p.objective_id: p.key for p in self._derive(unchosen).priorities}
            given = record

            def record(session, change):
                return given(session, {**change, "was_key": was_key, "is_key": is_key})
        self._repository.clear_keys(project_id, record=record)
        return self.view(project_id)

    def map_risks_of(self, project_id: str, on_start=None, on_save=None, replace: bool = False) -> ProjectView:
        """The one agentic step: which objectives mitigate each risk. A risk a person mapped keeps their
        mapping and costs no model call, unless `replace` says to map it too.

        The model is the one chosen by the project whose database the
        assessment is in. The caller's ledger events:
        - `on_start(session, run_id)` records the request, in a transaction of its own, before the first
          model call, so a long run cannot push it past the relay's window;
        - `on_save(session, outcome)` records the run (its id, outcome, model and each model call) inside the
          save's transaction. A run that raises, or a model that cannot be had, is recorded in a
          transaction of its own, then raised again."""
        from aisc_control_objectives import ledger

        record = self._repository.get(project_id)
        run_id, calls = str(uuid.uuid4()), ledger.Calls()
        mapper, model = self._mapper, self._model
        if on_start is not None:
            self._repository.recording(lambda session: on_start(session, run_id))
        if self._mapper_for is not None:
            try:
                mapper, model = self._mapper_for(record.project)
            except (ResolveError, ValueError) as exc:
                self._record_failure(on_save, {"run_id": run_id, "run": None, "error": "model_unreachable",
                                               "model": None, "calls": calls})
                raise ModelUnavailable(str(exc)) from exc
        catalogue = self._catalogue_for(record)
        # the model is shown the catalogue its proposals are checked against: the profile's
        if hasattr(mapper, "for_catalogue"):
            mapper = mapper.for_catalogue(catalogue)
        mapper = calls.watching(mapper)                               # each model call, timed, for ai.llm_call
        by_hand = set() if replace else set(record.by_hand)
        try:
            run = map_risks([r for r in record.ontology.risks if r.id not in by_hand], mapper, catalogue)
        except Exception:
            self._record_failure(on_save, {"run_id": run_id, "run": None, "error": "mapping_error", "model": model,
                                           "calls": calls})
            raise
        # A risk whose model calls failed keeps what it had: a revoked key or a provider outage costs a
        # retry, not the matrix. When every risk failed there is nothing to save at all.
        failed = {rid for rid, mapping in run.mappings.items() if mapping.stop == "failed"}
        if run.mappings and failed == set(run.mappings):
            self._record_failure(on_save, {"run_id": run_id, "run": None, "error": "mapping_error", "model": model,
                                           "calls": calls})
            raise ModelUnavailable(safe_error(run.error) or "the model answered none of the risks")
        before = record.mapping_run.mappings if record.mapping_run else {}
        kept = MappingRun(mappings={rid: before[rid] for rid in failed | by_hand if rid in before})
        # The run took its model calls' time: saved only over the assessment it read. A newer card
        # version, another profile, a person's mapping or another run meanwhile is not overwritten:
        # the save checks it under a lock on the assessment, in its own transaction.
        def unchanged(fresh: ProjectRecord) -> bool:
            return self._repository.is_latest(fresh) and _mapping_state(fresh) == _mapping_state(record)

        # `kept`: the rows the failed risks keep, which the ledger records for them
        outcome = {"run_id": run_id, "run": run, "error": run.error, "model": model, "calls": calls,
                   "kept": kept.mappings}
        try:
            self._repository.save_mapping_run(
                project_id, run, model=model, run_id=run_id, keep=frozenset(failed | by_hand),
                selected=self._in_order(self._catalogue_for(record), _mapped_ids(run) | _mapped_ids(kept)),
                unchanged=unchanged,
                record=None if on_save is None else (lambda session, change: on_save(session, {**change, **outcome})),
            )
        except AssessmentChanged:
            self._record_failure(on_save, {"run_id": run_id, "run": None, "error": "assessment_changed",
                                           "model": model, "calls": calls})
            raise
        except Exception:
            # a save that failed for any other reason is still a run whose model calls were made
            self._record_failure(on_save, {"run_id": run_id, "run": None, "error": "mapping_error",
                                           "model": model, "calls": calls})
            raise
        return self.view(project_id)

    def _record_failure(self, on_save, outcome: dict) -> None:
        """A run that ended before its save, recorded on its own. A failure to record it is logged and
        never hides the run's own error, which the caller raises next."""
        if on_save is None:
            return
        try:
            self._repository.recording(lambda session: on_save(session, outcome))
        except Exception:                                              # noqa: BLE001
            _log.exception("the failed run %s could not be recorded", outcome["run_id"])

    def map_by_hand(self, project_id: str, risk_id: str, objective_ids: list[str], on_save=None) -> ProjectView:
        """A person's mapping of one risk, replacing that risk's mapping.

        An objective the risk already had keeps its row as it was (the AI's quote and source);
        one added is the person's. The selection follows the change, as after an AI mapping.
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
        # the selection is worked out inside the save's transaction, from what is mapped then: another
        # person's mapping of another risk, saved meanwhile, stays in it
        self._repository.save_risk_mapping(
            project_id, risk_id, items, order=lambda mapped: self._in_order(catalogue, mapped), record=on_save,
            profile_version_id=record.profile_version_id)
        return self.view(project_id)

    @staticmethod
    def _in_order(catalogue: ControlObjectiveCatalogue, objective_ids) -> list[str]:
        """Catalogue order (O9 before O10); an id the catalogue lacks goes last."""
        def key(oid):
            found = catalogue.by_id(oid)
            return (0, found.sort_key, oid) if found else (1, (), oid)
        return sorted(objective_ids, key=key)

    def delete(self, project_id: str, record=None) -> None:
        self._repository.delete(project_id, record=record)

    # Reading

    def view(self, project_id: str) -> ProjectView | None:
        record = self._repository.get(project_id)
        return self._derive(record) if record else None

    def list(self) -> list[ProjectView]:
        """The assessments of this project, and no other's (the database is the project)."""
        return [self._derive(record) for record in self._repository.list()]

    def _derive(self, record: ProjectRecord) -> ProjectView:
        """Priorities, computed from what is stored. Never written back: a changed
        rating must not leave a stale score."""
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
