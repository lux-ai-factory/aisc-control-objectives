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
from aisc_control_objectives.risk_mapping import MappedObjective, Mapping, MappingRun


def digest_of(text: str) -> str:
    """A card's identity: the sha256 of the bytes that were uploaded."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass
class ProjectRecord:
    """One project, as the rest of the service wants it."""

    id: str
    #: The platform project this assessment belongs to: the project whose
    #: database it was read from (the row itself names none, I1.7).
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
    #: The objectives ticked to take forward; None until anything (the mapping
    #: or the assessor) has chosen, [] when everything was unticked.
    selected: list[str] | None = None
    #: The objective profile version it runs on; None is the built-in Full AI Act.
    profile_version_id: str | None = None
    #: The assessor's key choices: objective id -> key. An objective not here takes the default.
    keys: dict[str, bool] = field(default_factory=dict)


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
        """Tests and first run. Deployments use `alembic upgrade head`."""
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

    # ── writing ───────────────────────────────────────────────────────────
    # Every write takes `record`: the caller's ledger events, run inside the write's own transaction
    # with what changed (ledger phase 6, R2.4). None writes none.

    @staticmethod
    def _archive(session: Session, project: tables.Project, reason: str, risks=None,
                 run_id: str | None = None) -> int:
        """Keep the run and the mapped rows a change is about to replace (S1); the number of rows kept."""
        rows = [{"risk": r.risk_id, "objective": m.objective_id, "quote": m.quote, "rationale": m.rationale,
                 "source": m.source}
                for r in (risks if risks is not None else project.risks) for m in r.mapped]
        run = project.mapping_run
        kept_run = None if run is None or risks is not None else {
            "findings": run.findings, "stops": run.stops, "stop": run.stop, "attempts": run.attempts,
            "error": run.error, "model": run.model, "ran_at": run.ran_at.isoformat() if run.ran_at else None}
        if rows or kept_run:
            session.add(tables.MappingArchive(project_id=project.id, reason=reason, run=kept_run, rows=rows,
                                              run_id=run_id))
        return len(rows)

    def create(
        self, project: str | None, name: str, ontology: Ontology, jsonld: str, *, system_id: str,
        record=None,
    ) -> ProjectRecord:
        """`system_id` is the AI card version (a row of this database's
        project.system) the assessment is of: one assessment per version, which
        the database holds (UNIQUE system_id, and the key refuses a version it
        does not have). `project` is accepted so callers read as before, and not
        stored: the database is the project (I1.7)."""
        with self._sessions.begin() as session:
            row = tables.Project(
                id=uuid.uuid4().hex[:12],
                system_id=system_id,
                name=name,
                objectives_digest=self._objectives_digest,
            )
            session.add(row)
            self._attach_card(session, row, ontology, jsonld)
            session.flush()
            if record is not None:
                record(session, {"id": row.id, "risks": len(row.risks)})
            return self._to_record(session, row)

    def rate(self, project_id: str, impact: dict[str, int], likelihood: dict[str, int],
             comments: dict[str, str] | None = None, record=None) -> None:
        """Set the given impacts, likelihoods and, when given, comments ("" clears one), in one
        transaction. A risk not named keeps what it had. `record` gets each risk that changed, with
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
                    changed.append({"risk": row.risk_id, "before": before, "after": after})
            if record is not None and changed:
                record(session, changed)

    def save_keys(self, project_id: str, keys: dict[str, bool], record=None) -> None:
        """Record the assessor's key choices (objective id -> key), replacing earlier ones for those ids."""
        with self._sessions.begin() as session:
            before, after = {}, {}
            for objective_id, key in keys.items():
                found = session.get(tables.ObjectiveKey, (project_id, objective_id))
                before[objective_id] = None if found is None else found.key
                after[objective_id] = key
                if found is None:
                    session.add(tables.ObjectiveKey(project_id=project_id, objective_id=objective_id, key=key))
                else:
                    found.key = key
            if record is not None and before != after:
                record(session, {"before": before, "after": after})

    def save_selection(self, project_id: str, objective_ids: list[str]) -> None:
        """Replace the objectives this assessment takes forward."""
        with self._sessions.begin() as session:
            self._set_selection(session, session.get(tables.Project, project_id), objective_ids)

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
    ) -> None:
        """`selected`, when given, replaces the selection in the same transaction. The run and the
        mappings it replaces, a person's own ones too, are kept in mapping_archive first (S1)."""
        with self._sessions.begin() as session:
            project = session.get(tables.Project, project_id)
            kept = self._archive(session, project, "ai_run", run_id=run_id)
            if selected is not None:
                self._set_selection(session, project, selected)
            if project.mapping_run is not None:
                session.delete(project.mapping_run)
            for row in project.risks:
                row.mapped.clear()
            session.flush()

            by_risk_id = {row.risk_id: row for row in project.risks}
            for risk_id, mapping in run.mappings.items():
                row = by_risk_id.get(risk_id)
                if row is None:
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
                    error=run.error,
                    model=model,
                )
            )
            if record is not None:
                record(session, {"archived": kept})

    def save_risk_mapping(
        self, project_id: str, risk_id: str, objectives: list[MappedObjective],
        selected: list[str], record=None,
    ) -> None:
        """Replace one risk's mapping (a person's edit) and the selection, in one transaction. An
        assessment no AI ever mapped gets an empty run, so it reads as mapped."""
        with self._sessions.begin() as session:
            project = session.get(tables.Project, project_id)
            row = next(r for r in project.risks if r.risk_id == risk_id)
            before = sorted(m.objective_id for m in row.mapped)
            self._archive(session, project, "by_hand", risks=[row])
            row.mapped.clear()
            session.flush()
            for item in objectives:
                session.add(tables.MappedObjectiveRow(
                    risk_row_id=row.id, objective_id=item.objective_id, quote=item.quote,
                    rationale=item.rationale, source=item.source))
            if project.mapping_run is None:
                session.add(tables.MappingRunRow(project_id=project_id, findings=[], stops={},
                                                 stop="clean", attempts=0, error="", model=""))
            self._set_selection(session, project, selected)
            after = sorted(item.objective_id for item in objectives)
            if record is not None and before != after:
                record(session, {"before": before, "after": after})

    def set_profile(self, project_id: str, profile_version_id: str | None, keep: set[str],
                    record=None) -> list[str]:
        """Put the assessment on a profile version, and drop every mapping and selected objective
        not in `keep` (the new profile's objectives). Returns the objectives dropped; the mappings
        dropped are kept in mapping_archive (S1)."""
        with self._sessions.begin() as session:
            project = session.get(tables.Project, project_id)
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
                                   "mapped": [m.objective_id for m in r.mapped]} for r in project.risks]}
                session.delete(project)
                if record is not None:
                    record(session, held)

    # ── reading ───────────────────────────────────────────────────────────

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

    def previous_of(self, record: ProjectRecord) -> ProjectRecord | None:
        """The assessment of the closest earlier card version, if there is one."""
        with self._sessions() as session:
            found = session.execute(_PREVIOUS_ASSESSMENT, {"sid": record.system_id}).scalar()
            project = session.get(tables.Project, found) if found else None
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

    # ── translation ───────────────────────────────────────────────────────

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
                risks=len(ontology.risks),
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
