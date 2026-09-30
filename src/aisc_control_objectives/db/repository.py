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

    def create(
        self, project: str | None, name: str, ontology: Ontology, jsonld: str, *, system_id: str
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
            return self._to_record(session, row)

    def rate(self, project_id: str, ratings: dict[str, int]) -> None:
        with self._sessions.begin() as session:
            project = session.get(tables.Project, project_id)
            for row in project.risks:
                if row.risk_id in ratings:
                    row.severity = ratings[row.risk_id]

    def save_selection(self, project_id: str, objective_ids: list[str]) -> None:
        """Replace the objectives this assessment takes forward."""
        with self._sessions.begin() as session:
            self._set_selection(session, session.get(tables.Project, project_id), objective_ids)

    @staticmethod
    def _set_selection(session: Session, project: tables.Project, objective_ids) -> None:
        ids = sorted(set(objective_ids))
        if project.selection is None:
            session.add(tables.ObjectiveSelectionRow(project_id=project.id, objective_ids=ids))
        else:
            project.selection.objective_ids = ids

    def save_mapping_run(
        self, project_id: str, run: MappingRun, model: str = "",
        selected: list[str] | None = None,
    ) -> None:
        """`selected`, when given, replaces the selection in the same transaction."""
        with self._sessions.begin() as session:
            project = session.get(tables.Project, project_id)
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

    def delete(self, project_id: str) -> None:
        with self._sessions.begin() as session:
            project = session.get(tables.Project, project_id)
            if project is not None:
                session.delete(project)

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
            severity=Severity(
                ratings={
                    row.risk_id: row.severity
                    for row in project.risks
                    if row.severity is not None
                }
            ),
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
