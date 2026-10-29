"""Projects, read and written. The only place that touches a session.

Everything above this line works in pydantic models (`Ontology`, `MappingRun`);
everything below is SQLAlchemy. The translation happens here, so `prioritising`,
`risk_mapping` and `rendering` never learn that a database exists, and the
domain keeps validating its own shape on the way back out.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import func, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.schema import CreateSchema

from aisc_control_objectives import projectdb
from aisc_control_objectives.db import tables
from aisc_control_objectives.models.ontology import Ontology, OntologyRisk
from aisc_control_objectives.prioritising import Severity
from aisc_control_objectives.risk_mapping import Finding as MappingFinding
from aisc_control_objectives.risk_mapping import MappedObjective, Mapping, MappingRun, safe_error


def digest_of(text: str) -> str:
    """A card's identity: the sha256 of the bytes that were uploaded."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass
class ProjectRecord:
    """One project, as the rest of the service wants it."""

    id: str
    #: The platform project this assessment belongs to: the project whose
    #: database it was read from (the row itself names none).
    project: str | None
    name: str
    #: Both read from the stored card, which is the authority on them.
    system_name: str
    qualification_id: str
    objectives_digest: str
    created_at: datetime
    updated_at: datetime
    #: The AI card version (project.system pid) this assessment is of, its number,
    #: and the project's latest number: only the latest version's assessment changes.
    system_id: str = ""
    version_number: int | None = None
    latest_number: int | None = None
    jsonld: str = ""
    digest: str = ""
    ontology: Ontology = field(default_factory=Ontology)
    severity: Severity = field(default_factory=Severity)
    mapping_run: MappingRun | None = None
    #: The objectives taken forward (what the matrix holds); None until anything
    #: was mapped, [] when the matrix is empty.
    selected: list[str] | None = None
    #: The objective profile version it runs on; None is the built-in Full AI Act.
    profile_version_id: str | None = None
    #: The assessor's key choices: objective id -> key. An objective not here takes the default.
    keys: dict[str, bool] = field(default_factory=dict)
    #: The risks whose mapping a person saved; an AI run leaves them alone unless told to replace them.
    by_hand: set[str] = field(default_factory=set)
    #: What it took from the previous card version's assessment (tables.Project.carried), or None.
    carried: dict | None = None


#: A card version's number and the highest number of the project, read from
#: `project.system` of the same database: the database is the project, so the
#: highest number over the table is the project's latest.
_VERSION_NUMBERS = text(
    "SELECT s.number, (SELECT max(o.number) FROM project.system o)"
    " FROM project.system s WHERE s.pid::text = :sid"
)

_HAS_VERSION = text("SELECT 1 FROM project.system WHERE pid::text = :sid")

#: The assessment of the closest earlier card version, if any.
_PREVIOUS_ASSESSMENT = text(
    "SELECT a.id FROM control_objectives.project a JOIN project.system s ON s.pid = a.system_id"
    " WHERE s.number < (SELECT number FROM project.system WHERE pid::text = :sid)"
    " ORDER BY s.number DESC LIMIT 1"
)


class AssessmentChanged(RuntimeError):
    """The assessment changed while the model was mapping it; the run was not saved."""


class ProjectRepository:
    """The assessments of one project, in that project's database.

    Given a URL it makes its own engine (tests, scripts); given an engine (the
    one `ProjectDatabases.open` let the caller into) it uses that. `pid` is the
    project the database belongs to, which every record it returns carries.
    """

    def __init__(
        self, url_or_engine: str | Engine, objectives_digest: str = "", *, pid: str | None = None
    ):
        if isinstance(url_or_engine, str):
            self._engine = projectdb.make_engine(url_or_engine, pool_pre_ping=True)
            self._url: str | None = url_or_engine
        else:
            self._engine = url_or_engine
            self._url = None
        self._sessions = sessionmaker(self._engine, expire_on_commit=False)
        self._objectives_digest = objectives_digest
        self._pid = pid

    @property
    def engine(self):
        """The connection this repository holds."""
        return self._engine

    @property
    def pid(self) -> str | None:
        """The platform project whose database this is."""
        return self._pid

    def reopened(self) -> ProjectRepository:
        """A second repository on the same database: what a restart would give."""
        return ProjectRepository(self._url or self._engine, self._objectives_digest, pid=self._pid)

    def create_all(self) -> None:
        """For tests. Deployments use `alembic upgrade head`."""
        with self._engine.begin() as connection:
            # In a project database the platform's template has made the schema
            # and this role may not make one; in a scratch database it is the
            # first thing needed. `project` belongs to the platform and is
            # never created here.
            missing = connection.execute(
                text("SELECT to_regnamespace(:s) IS NULL"), {"s": tables.SCHEMA}
            ).scalar()
            if missing:
                connection.execute(CreateSchema(tables.SCHEMA))
        tables.Base.metadata.create_all(
            self._engine,
            tables=[t for t in tables.Base.metadata.sorted_tables
                    if not t.info.get("external")],
        )

    # Writing. Every write takes `record`: the caller's ledger events, run inside the write's own
    # transaction with what changed. None writes none.

    @staticmethod
    def _archive(session: Session, project: tables.Project, reason: str, risks=None,
                 run_id: str | None = None, with_run: bool | None = None) -> int:
        """Keep the run and the mapped rows a change is about to replace; the number of rows kept.
        The run is kept when every risk is replaced, or when `with_run` says so."""
        rows = [{"risk": r.risk_id, "objective": m.objective_id, "quote": m.quote, "rationale": m.rationale,
                 "source": m.source}
                for r in (risks if risks is not None else project.risks) for m in r.mapped]
        run = project.mapping_run
        if with_run is None:
            with_run = risks is None
        kept_run = None if run is None or not with_run else {
            "findings": run.findings, "stops": run.stops, "stop": run.stop, "attempts": run.attempts,
            "error": run.error, "model": run.model, "ran_at": run.ran_at.isoformat() if run.ran_at else None}
        if rows or kept_run:
            session.add(tables.MappingArchive(project_id=project.id, reason=reason, run=kept_run, rows=rows,
                                              run_id=run_id))
        return len(rows)

    def create(
        self, project: str | None, name: str, ontology: Ontology, jsonld: str, *, system_id: str,
        record=None, default_profile_version_id: str | None = None,
        carry_objectives: set[str] | None = None, order: Callable[[set[str]], list[str]] | None = None,
    ) -> ProjectRecord:
        """`system_id` is the AI card version (a row of this database's
        project.system) the assessment is of: one assessment per version, which
        the database holds (UNIQUE system_id, and the key refuses a version it
        does not have). `project` is accepted but not stored: the database is
        the project.

        The previous card version's assessment, if there is one, hands over its work on the risks the
        two cards share (`_carry`); `carry_objectives` are the objectives it may carry (None: any),
        `order` puts the carried selection in catalogue order."""
        with self._sessions.begin() as session:
            row = tables.Project(
                id=uuid.uuid4().hex[:12],
                system_id=system_id,
                name=name,
                objectives_digest=self._objectives_digest,
            )
            # a new card version's assessment starts on the profile the previous one ran on, in this same
            # transaction, so its start event can say so
            previous = session.execute(_PREVIOUS_ASSESSMENT, {"sid": system_id}).scalar()
            source = session.get(tables.Project, previous) if previous is not None else None
            if source is not None:
                row.profile_version_id = source.profile_version_id
            else:
                row.profile_version_id = default_profile_version_id
            session.add(row)
            self._attach_card(session, row, ontology, jsonld)
            session.flush()
            carried = None
            if source is not None:
                number, _latest = self._version_numbers(session, source.system_id)
                carried = self._carry(session, row, self._work_of(session, source), number,
                                      carry_objectives, order)
            if record is not None:
                change = {"id": row.id, "risks": len(row.risks), "profile_version": row.profile_version_id}
                if carried is not None:
                    change |= {"carried_from": source.id, "carried_risks": row.carried["risks"], "carried": carried}
                record(session, change)
            return self._to_record(session, row)

    def update_card(
        self, project_id: str, ontology: Ontology, jsonld: str, *, digest_before: str,
        carry_objectives: set[str] | None = None, order: Callable[[set[str]], list[str]] | None = None,
        record=None,
    ) -> bool:
        """Put the assessment on its version's card as it reads now, carrying the work over as a new
        version's assessment does (`_carry`). The risks are read again; their mapped rows and the run
        are kept in mapping_archive first. `digest_before` is the card the caller saw: if the assessment
        holds another one now, AssessmentChanged is raised and nothing is written. False when the card
        is the one it already holds.

        `record` gets the digests, the risks before and after (as `rate` and `save_risk_mapping` hand
        them over) and the key choices before and after, so each item that changed can say so."""
        with self._sessions.begin() as session:
            project = session.get(tables.Project, project_id, with_for_update=True)
            if project.graph is None or project.graph.digest != digest_before:
                raise AssessmentChanged("the assessment's card changed meanwhile: look again")
            if digest_of(jsonld) == digest_before:
                return False
            work = self._work_of(session, project)
            before = self._states(project.risks)
            keys_before = self._keys_of(session, project_id)
            self._archive(session, project, "card", with_run=True)
            for key in session.scalars(select(tables.ObjectiveKey)
                                       .where(tables.ObjectiveKey.project_id == project_id)):
                session.delete(key)
            project.risks.clear()
            session.delete(project.graph)
            if project.mapping_run is not None:
                # what the last run found was about the risks as they were
                project.mapping_run.findings, project.mapping_run.stops = [], {}
            if project.selection is not None:
                project.selection.objective_ids = []
            session.flush()
            session.expire(project, ["graph", "risks"])
            self._attach_card(session, project, ontology, jsonld)
            session.flush()
            number, _latest = self._version_numbers(session, project.system_id)
            project.carried = None
            carried = self._carry(session, project, work, number, carry_objectives, order)
            if project.carried is not None:
                # read back as "carried from the earlier card of vN", not from another version
                project.carried = {**project.carried, "updated": True}
            project.updated_at = tables._now()
            session.flush()
            session.expire(project, ["risks"])
            if record is not None:
                record(session, {"assessment": project_id, "digest_before": digest_before,
                                 "digest_after": digest_of(jsonld), "risks": len(project.risks),
                                 "carried_risks": len(carried["risks"]) if carried else 0, "carried": carried,
                                 "before": before, "after": self._states(project.risks),
                                 "keys_before": keys_before, "keys_after": self._keys_of(session, project_id)})
            return True

    @staticmethod
    def _states(risks) -> dict[str, dict]:
        """Each risk's rating, comment and mapped objectives, by id."""
        return {r.risk_id: {"impact": r.rating_impact, "likelihood": r.rating_likelihood,
                            "comment": r.severity_comment, "mapped": sorted(m.objective_id for m in r.mapped)}
                for r in risks}

    @staticmethod
    def _work_of(session: Session, project: tables.Project) -> dict:
        """What an assessment holds that a person or a run put there, risk by risk, as plain values: the
        rows may be gone by the time it is carried."""
        return {
            "risks": [{"text": r.text, "impact": r.rating_impact, "likelihood": r.rating_likelihood,
                       "comment": r.severity_comment, "by_hand": r.mapped_by_hand,
                       "mapped": [(m.objective_id, m.quote, m.rationale, m.source) for m in r.mapped]}
                      for r in sorted(project.risks, key=lambda r: r.position)],
            "keys": ProjectRepository._keys_of(session, project.id),
        }

    @staticmethod
    def _carry(session: Session, target: tables.Project, work: dict, number: int | None,
               keep: set[str] | None, order: Callable[[set[str]], list[str]] | None) -> dict | None:
        """Give `target`'s risks the work held on the same risks in `work`, and note what came along on
        `target.carried`. The same risk is the one with the same text, trimmed: the card numbers its risks
        by row, so an id names another risk once a row above it is deleted. Two risks with one text are
        matched in order. A mapping to an objective outside `keep` is not carried, nor is a key choice for
        an objective the carried matrix does not hold. Returns what was carried, for the ledger; None
        when the earlier assessment held nothing."""
        def worked(risk):
            return (risk["impact"] is not None or risk["likelihood"] is not None or risk["comment"]
                    or risk["mapped"] or risk["by_hand"])

        if not any(worked(risk) for risk in work["risks"]):
            return None
        waiting: dict[str, list[dict]] = {}
        for risk in work["risks"]:
            waiting.setdefault(risk["text"].strip(), []).append(risk)
        risks = session.scalars(select(tables.Risk).where(tables.Risk.project_id == target.id)
                                .order_by(tables.Risk.position)).all()
        new, outside, carried, mapped = [], set(), {}, set()
        for row in risks:
            matches = waiting.get(row.text.strip())
            if not matches:
                new.append(row.text)
                continue
            risk = matches.pop(0)
            risk["taken"] = True
            if not worked(risk):
                continue
            row.rating_impact, row.rating_likelihood = risk["impact"], risk["likelihood"]
            row.severity_comment, row.mapped_by_hand = risk["comment"], risk["by_hand"]
            for objective_id, quote, rationale, source in risk["mapped"]:
                if keep is not None and objective_id not in keep:
                    outside.add(objective_id)
                    continue
                row.mapped.append(tables.MappedObjectiveRow(objective_id=objective_id, quote=quote,
                                                            rationale=rationale, source=source))
                mapped.add(objective_id)
            carried[row.risk_id] = {"impact": row.rating_impact, "likelihood": row.rating_likelihood,
                                    "comment": row.severity_comment, "mapped": [m.objective_id for m in row.mapped]}
        if mapped or any(row.mapped_by_hand for row in risks):
            # mapped, so it reads as mapped, as after a person's first mapping
            if target.mapping_run is None:
                target.mapping_run = tables.MappingRunRow(findings=[], stops={}, stop="clean", attempts=0,
                                                          error="", model="")
            ids = order(mapped) if order is not None else sorted(mapped)
            if target.selection is None:
                target.selection = tables.ObjectiveSelectionRow(objective_ids=ids)
            else:
                target.selection.objective_ids = ids
        keys = {oid: key for oid, key in work["keys"].items() if oid in mapped}
        for objective_id, key in keys.items():
            session.add(tables.ObjectiveKey(project_id=target.id, objective_id=objective_id, key=key))
        target.carried = {"from": number, "risks": len(carried), "new": new,
                          "left": [r["text"] for r in work["risks"] if worked(r) and not r.get("taken")],
                          "outside": sorted(outside)}
        session.flush()
        return {"risks": carried, "keys": keys}

    def previous_profile(self, system_id: str) -> tuple[bool, str | None]:
        """Whether an earlier card version has an assessment, and the profile version it runs on: the one
        an assessment of `system_id` would start on."""
        with self._sessions() as session:
            previous = session.execute(_PREVIOUS_ASSESSMENT, {"sid": system_id}).scalar()
            if previous is None:
                return False, None
            return True, session.get(tables.Project, previous).profile_version_id

    def rate(self, project_id: str, impact: dict[str, int | None], likelihood: dict[str, int | None],
             comments: dict[str, str] | None = None, record=None) -> None:
        """Set the given impacts, likelihoods (None clears one) and, when given, comments ("" clears
        one), in one transaction. A risk not named keeps what it had. `record` gets each risk that changed, with
        its rating and comment before and after."""
        with self._sessions.begin() as session:
            project = session.get(tables.Project, project_id)
            changed = []
            for row in project.risks:
                before = {"impact": row.rating_impact, "likelihood": row.rating_likelihood,
                          "comment": row.severity_comment}
                if row.risk_id in impact:
                    row.rating_impact = impact[row.risk_id]
                if row.risk_id in likelihood:
                    row.rating_likelihood = likelihood[row.risk_id]
                if comments is not None and row.risk_id in comments:
                    row.severity_comment = comments[row.risk_id]
                after = {"impact": row.rating_impact, "likelihood": row.rating_likelihood,
                         "comment": row.severity_comment}
                if after != before:
                    changed.append({"assessment": project_id, "risk": row.risk_id, "before": before, "after": after})
            if changed:
                project.updated_at = tables._now()          # Child rows only: no UPDATE would move it
            if record is not None and changed:
                record(session, changed)

    def save_keys(self, project_id: str, keys: dict[str, bool], record=None) -> None:
        """Record the assessor's key choices (objective id -> key), replacing earlier ones for those ids."""
        with self._sessions.begin() as session:
            # the whole key map before and after, read in this transaction, so one save's after is the
            # next one's before whatever ids each names
            before = self._keys_of(session, project_id)
            for objective_id, key in keys.items():
                found = session.get(tables.ObjectiveKey, (project_id, objective_id))
                if found is None:
                    session.add(tables.ObjectiveKey(project_id=project_id, objective_id=objective_id, key=key))
                else:
                    found.key = key
            session.flush()
            after = self._keys_of(session, project_id)
            if before != after:
                session.get(tables.Project, project_id).updated_at = tables._now()
            if record is not None and before != after:
                record(session, {"before": before, "after": after})

    def clear_keys(self, project_id: str, record=None) -> None:
        """Remove every key choice of the assessment, so each objective takes the default again."""
        with self._sessions.begin() as session:
            before = self._keys_of(session, project_id)
            if not before:
                return
            for key in session.scalars(select(tables.ObjectiveKey)
                                       .where(tables.ObjectiveKey.project_id == project_id)):
                session.delete(key)
            session.flush()
            session.get(tables.Project, project_id).updated_at = tables._now()
            if record is not None:
                record(session, {"before": before, "after": self._keys_of(session, project_id)})

    @staticmethod
    def _keys_of(session: Session, project_id: str) -> dict[str, bool]:
        return {k.objective_id: k.key for k in session.scalars(
            select(tables.ObjectiveKey).where(tables.ObjectiveKey.project_id == project_id)
            .order_by(tables.ObjectiveKey.objective_id))}

    @staticmethod
    def _set_selection(session: Session, project: tables.Project, objective_ids) -> None:
        # in the order given (the service gives catalogue order: O9 before O10), once each
        ids = list(dict.fromkeys(objective_ids))
        if project.selection is None:
            session.add(tables.ObjectiveSelectionRow(project_id=project.id, objective_ids=ids))
        else:
            project.selection.objective_ids = ids

    def save_mapping_run(
        self, project_id: str, run: MappingRun, model: str = "",
        selected: list[str] | None = None, record=None, run_id: str | None = None,
        unchanged: Callable[[ProjectRecord], bool] | None = None, keep: frozenset[str] = frozenset(),
    ) -> None:
        """`selected`, when given, replaces the selection in the same transaction. The run and the
        mappings it replaces, a person's own ones too, are kept in mapping_archive first. The risks in
        `keep` (their model calls failed, or a person mapped them) keep their rows as they are and are
        not archived; every other risk is the AI's from now on.

        `unchanged`, when given, is asked about the assessment as it is now, read under a lock on its
        row in this transaction; False raises AssessmentChanged and nothing is written. So a person's
        mapping or another run saved after the caller read the assessment is never overwritten, and
        two runs saving at once take turns rather than colliding on the run's key."""
        with self._sessions.begin() as session:
            project = session.get(tables.Project, project_id, with_for_update=True)
            if unchanged is not None and not unchanged(self._to_record(session, project)):
                raise AssessmentChanged("The assessment changed while the AI was suggesting links: suggest again.")
            replaced = [row for row in project.risks if row.risk_id not in keep]
            kept = self._archive(session, project, "ai_run", risks=replaced if keep else None, run_id=run_id,
                                 with_run=True)
            project.updated_at = tables._now()
            if selected is not None:
                self._set_selection(session, project, selected)
            if project.mapping_run is not None:
                session.delete(project.mapping_run)
            for row in replaced:
                row.mapped.clear()
                row.mapped_by_hand = False
            session.flush()

            by_risk_id = {row.risk_id: row for row in project.risks}
            for risk_id, mapping in run.mappings.items():
                row = by_risk_id.get(risk_id)
                if row is None or risk_id in keep:
                    continue
                for item in mapping.objectives:
                    session.add(
                        tables.MappedObjectiveRow(
                            risk_row_id=row.id,
                            objective_id=item.objective_id,
                            quote=item.quote,
                            rationale=item.rationale,
                            source="ai",
                        )
                    )
            session.add(
                tables.MappingRunRow(
                    project_id=project_id,
                    findings=[f.model_dump() for f in run.findings],
                    stops={rid: m.stop for rid, m in run.mappings.items()},
                    stop=run.stop,
                    attempts=run.attempts,
                    # Never the provider's raw text
                    error=safe_error(run.error),
                    model=model,
                )
            )
            if record is not None:
                record(session, {"archived": kept})

    def save_risk_mapping(
        self, project_id: str, risk_id: str, objectives: list[MappedObjective],
        order: Callable[[set[str]], list[str]], record=None, profile_version_id: str | None = "",
    ) -> None:
        """Replace one risk's mapping (a person's edit) and the selection, in one transaction. An
        assessment no AI ever mapped gets an empty run, so it reads as mapped.

        The assessment row is locked first, so two people mapping two risks at once take turns, and
        the selection is every objective mapped now, read in this transaction and put in `order`
        (the service's catalogue order): a selection worked out from an earlier read would drop
        what the other person just mapped.

        `profile_version_id`, when given (None is the built-in profile), is the profile the objectives
        were checked against: if the locked row is on another one now, AssessmentChanged is raised and
        nothing is written, so a profile switch in between cannot leave an objective
        outside the profile mapped and selected."""
        with self._sessions.begin() as session:
            project = session.get(tables.Project, project_id, with_for_update=True)
            if profile_version_id != "" and project.profile_version_id != profile_version_id:
                raise AssessmentChanged("The assessment's objective profile changed meanwhile: suggest again.")
            row = next(r for r in project.risks if r.risk_id == risk_id)
            before = sorted(m.objective_id for m in row.mapped)
            self._archive(session, project, "by_hand", risks=[row])
            row.mapped.clear()
            row.mapped_by_hand = True
            session.flush()
            for item in objectives:
                session.add(tables.MappedObjectiveRow(
                    risk_row_id=row.id, objective_id=item.objective_id, quote=item.quote,
                    rationale=item.rationale, source=item.source))
            if project.mapping_run is None:
                session.add(tables.MappingRunRow(project_id=project_id, findings=[], stops={},
                                                 stop="clean", attempts=0, error="", model=""))
            session.flush()
            mapped = set(session.scalars(
                select(tables.MappedObjectiveRow.objective_id).join(
                    tables.Risk, tables.Risk.id == tables.MappedObjectiveRow.risk_row_id)
                .where(tables.Risk.project_id == project_id)))
            self._set_selection(session, project, order(mapped))
            after = sorted(item.objective_id for item in objectives)
            if before != after:
                project.updated_at = tables._now()
            if record is not None and before != after:
                record(session, {"before": before, "after": after})

    def set_profile(self, project_id: str, profile_version_id: str | None, keep: set[str],
                    record=None) -> list[str]:
        """Put the assessment on a profile version, and drop every mapping and selected objective
        not in `keep` (the new profile's objectives). Returns the objectives dropped; the mappings
        dropped are kept in mapping_archive."""
        with self._sessions.begin() as session:
            # locked, so a person's mapping saved meanwhile is either before this or sees the new profile
            project = session.get(tables.Project, project_id, with_for_update=True)
            version_before = project.profile_version_id
            losing = [r for r in project.risks if any(m.objective_id not in keep for m in r.mapped)]
            if losing:
                self._archive(session, project, "profile", risks=losing)
            project.profile_version_id = profile_version_id
            dropped: set[str] = set()
            for risk in project.risks:
                for row in list(risk.mapped):
                    if row.objective_id not in keep:
                        dropped.add(row.objective_id)
                        risk.mapped.remove(row)
            if project.selection is not None:
                ids = list(project.selection.objective_ids)
                dropped |= {oid for oid in ids if oid not in keep}
                project.selection.objective_ids = [oid for oid in ids if oid in keep]
            if record is not None and version_before != profile_version_id:
                record(session, {"before": version_before, "after": profile_version_id, "dropped": sorted(dropped)})
            return sorted(dropped)

    def recording(self, record) -> None:
        """A transaction for ledger events alone: what happened changed nothing here (a failed AI run)."""
        with self._sessions.begin() as session:
            record(session)

    def delete(self, project_id: str, record=None) -> None:
        """Delete an assessment and all it holds. `record` gets what it held, for the ledger to freeze."""
        with self._sessions.begin() as session:
            project = session.get(tables.Project, project_id)
            if project is not None:
                held = {"system_id": str(project.system_id), "name": project.name,
                        "risks": [{"risk": r.risk_id, "impact": r.rating_impact, "likelihood": r.rating_likelihood,
                                   "comment": r.severity_comment,
                                   "mapped": [m.objective_id for m in r.mapped]} for r in project.risks],
                        "keys": self._keys_of(session, project_id),
                        "selected": None if project.selection is None else list(project.selection.objective_ids)}
                # the mapping's rows (quote, rationale, source) and its run, kept as a change keeps them;
                # the archive has no key to the assessment, so it outlives it
                self._archive(session, project, "deleted")
                session.delete(project)
                if record is not None:
                    record(session, held)

    # Reading

    def get(self, project_id: str) -> ProjectRecord | None:
        with self._sessions() as session:
            project = session.get(tables.Project, project_id)
            return self._to_record(session, project) if project else None

    def find_by_system(self, system_id: str) -> ProjectRecord | None:
        """The assessment of one AI card version, if it has one."""
        with self._sessions() as session:
            project = session.scalar(
                select(tables.Project).where(tables.Project.system_id == system_id)
            )
            return self._to_record(session, project) if project else None

    def has_version(self, system_id: str) -> bool:
        """Whether this database's project.system has that card version."""
        with self._engine.connect() as connection:
            return connection.execute(_HAS_VERSION, {"sid": system_id}).first() is not None

    @staticmethod
    def is_latest(record: ProjectRecord) -> bool:
        """Whether the assessment is of the project's latest card version: only
        that one may still be mapped and rated."""
        return record.version_number is not None and record.version_number == record.latest_number

    def list(self) -> list[ProjectRecord]:
        """The assessments of this project, newest first. There is no way to
        list another project's: they are in another database."""
        with self._sessions() as session:
            rows = session.scalars(
                select(tables.Project).order_by(tables.Project.updated_at.desc())
            ).all()
            return [self._to_record(session, row) for row in rows]

    def orphan_rows(self) -> int:
        """Rows whose project is gone: should always be zero, and a test says so."""
        with self._sessions() as session:
            return session.scalar(
                select(func.count())
                .select_from(tables.Risk)
                .where(~tables.Risk.project_id.in_(select(tables.Project.id)))
            )

    # Translation between rows and domain models

    @staticmethod
    def _attach_card(
        session: Session,
        project: tables.Project,
        ontology: Ontology,
        jsonld: str,
    ) -> None:
        session.add(
            tables.Graph(
                project_id=project.id,
                jsonld=jsonld,
                digest=digest_of(jsonld),
            )
        )
        for risk in ontology.risks:
            session.add(
                tables.Risk(
                    project_id=project.id,
                    risk_id=risk.id,
                    position=risk.position,
                    text=risk.text,
                    short_label=risk.short_label,
                    source=risk.source,
                    vulnerability=risk.vulnerability,
                    consequence=risk.consequence,
                    impact=risk.impact,
                    stakeholder=risk.stakeholder,
                    control=risk.control,
                    follow_up_control=risk.follow_up_control,
                    areas=list(risk.areas),
                    vair_terms=list(risk.vair_terms),
                    provenance=risk.provenance,
                )
            )

    @staticmethod
    def _version_numbers(session: Session, system_id: str) -> tuple[int | None, int | None]:
        """The number of this card version, and the project's latest number."""
        numbers = session.execute(_VERSION_NUMBERS, {"sid": system_id}).first()
        return (numbers[0], numbers[1]) if numbers else (None, None)

    @staticmethod
    def _stored_card(graph: tables.Graph | None) -> Ontology:
        """The stored card, parsed; empty when there is none or it no longer parses."""
        if graph is None or not graph.jsonld:
            return Ontology()
        try:
            return Ontology.from_jsonld(json.loads(graph.jsonld))
        except Exception:  # a stored card that no longer parses still lists
            return Ontology()

    def _to_record(self, session: Session, project: tables.Project) -> ProjectRecord:
        version_number, latest_number = ProjectRepository._version_numbers(
            session, project.system_id
        )
        card = ProjectRepository._stored_card(project.graph)
        risks = [
            OntologyRisk(
                id=row.risk_id,
                text=row.text,
                short_label=row.short_label,
                source=row.source,
                vulnerability=row.vulnerability,
                consequence=row.consequence,
                impact=row.impact,
                stakeholder=row.stakeholder,
                areas=list(row.areas or []),
                control=row.control,
                follow_up_control=row.follow_up_control,
                vair_terms=list(row.vair_terms or []),
                provenance=row.provenance,
            )
            for row in project.risks
        ]

        record = ProjectRecord(
            id=project.id,
            project=self._pid,
            name=project.name,
            system_name=card.system_name,
            qualification_id=card.qualification_id,
            objectives_digest=project.objectives_digest,
            created_at=project.created_at,
            updated_at=project.updated_at,
            system_id=project.system_id,
            version_number=version_number,
            latest_number=latest_number,
            jsonld=project.graph.jsonld if project.graph else "",
            digest=project.graph.digest if project.graph else "",
            ontology=Ontology(
                qualification_id=card.qualification_id,
                system_name=card.system_name,
                risks=risks,
            ),
            selected=list(project.selection.objective_ids) if project.selection else None,
            profile_version_id=project.profile_version_id,
            severity=Severity(
                impact={row.risk_id: row.rating_impact for row in project.risks if row.rating_impact is not None},
                likelihood={row.risk_id: row.rating_likelihood for row in project.risks
                            if row.rating_likelihood is not None},
                comments={row.risk_id: row.severity_comment for row in project.risks if row.severity_comment},
            ),
            keys={row.objective_id: row.key for row in session.scalars(
                select(tables.ObjectiveKey).where(tables.ObjectiveKey.project_id == project.id)).all()},
            by_hand={row.risk_id for row in project.risks if row.mapped_by_hand},
            carried=project.carried,
        )
        if project.mapping_run is not None:
            stops = project.mapping_run.stops or {}
            record.mapping_run = MappingRun(
                mappings={
                    row.risk_id: Mapping(
                        risk_id=row.risk_id,
                        objectives=[
                            MappedObjective(
                                objective_id=item.objective_id,
                                quote=item.quote,
                                rationale=item.rationale,
                                source=item.source,
                            )
                            for item in row.mapped
                        ],
                        stop=stops.get(row.risk_id, "clean"),
                    )
                    for row in project.risks
                },
                findings=[
                    MappingFinding.model_validate(f) for f in project.mapping_run.findings
                ],
                stop=project.mapping_run.stop,
                attempts=project.mapping_run.attempts,
                error=project.mapping_run.error,
                model=project.mapping_run.model,
            )
        return record
