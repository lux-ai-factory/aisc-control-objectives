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

from sqlalchemy import create_engine, func, select, text
from sqlalchemy.schema import CreateSchema
from sqlalchemy.orm import Session, sessionmaker

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
    #: The platform project this assessment belongs to.
    project: str
    name: str
    #: Both read from the stored card, which is the authority on them.
    system_name: str
    qualification_id: str
    objectives_digest: str
    created_at: datetime
    updated_at: datetime
    #: The AI card version (core.system pid) this assessment is of, its number,
    #: and the project's latest number: only the latest version's assessment changes.
    system_id: str = ""
    version_number: int | None = None
    latest_number: int | None = None
    jsonld: str = ""
    digest: str = ""
    ontology: Ontology = field(default_factory=Ontology)
    severity: Severity = field(default_factory=Severity)
    mapping_run: MappingRun | None = None


#: A card version's number and the highest number in its project, read from
#: the platform's `core.system`.
_VERSION_NUMBERS = text(
    "SELECT s.number, (SELECT max(o.number) FROM core.system o"
    " WHERE o.project_id = s.project_id) FROM core.system s WHERE s.pid = :sid"
)


class ProjectRepository:
    def __init__(self, url: str, objectives_digest: str = ""):
        self._engine = create_engine(url, pool_pre_ping=True)
        self._sessions = sessionmaker(self._engine, expire_on_commit=False)
        self._url = url
        self._objectives_digest = objectives_digest

    @property
    def engine(self):
        """The connection this repository holds.

        Exposed for the one thing outside this module that needs it: the door
        reads `core.project_member` through the same database, rather than
        opening a second one or asking the platform over HTTP.
        """
        return self._engine

    def reopened(self) -> ProjectRepository:
        """A second repository on the same database: what a restart would give."""
        return ProjectRepository(self._url, self._objectives_digest)

    def create_all(self) -> None:
        """Tests and first run. Deployments use `alembic upgrade head`."""
        with self._engine.begin() as connection:
            # On the platform the schema is already there and this role may not
            # make one; in a scratch database it is the first thing needed.
            # `core` belongs to the platform and is never created here.
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
        self, project: str, name: str, ontology: Ontology, jsonld: str, *, system_id: str
    ) -> ProjectRecord:
        """`project` is the platform project this assessment is part of, and
        `system_id` the AI card version (core.system) it is of: one assessment
        per version, which the database holds (UNIQUE system_id)."""
        with self._sessions.begin() as session:
            row = tables.Project(
                id=uuid.uuid4().hex[:12],
                project_id=project,
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

    def save_mapping_run(self, project_id: str, run: MappingRun, model: str = "") -> None:
        with self._sessions.begin() as session:
            project = session.get(tables.Project, project_id)
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

    @staticmethod
    def is_latest(record: ProjectRecord) -> bool:
        """Whether the assessment is of the project's latest card version: only
        that one may still be mapped and rated."""
        return record.version_number is not None and record.version_number == record.latest_number

    def list(self, project: str) -> list[ProjectRecord]:
        """The assessments of one platform project. A module reads what it
        needs and no more, so there is no way to list every project's."""
        with self._sessions() as session:
            rows = session.scalars(
                select(tables.Project)
                .where(tables.Project.project_id == project)
                .order_by(tables.Project.updated_at.desc())
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

    @staticmethod
    def _to_record(session: Session, project: tables.Project) -> ProjectRecord:
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
            project=project.project_id,
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
