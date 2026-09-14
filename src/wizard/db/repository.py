"""Projects, read and written. The only place that touches a session.

Everything above this line works in pydantic models (`Ontology`, `Profile`,
`MappingRun`); everything below is SQLAlchemy. The translation happens here, so
`prioritising`, `risk_mapping` and `rendering` never learn that a database
exists, and the domain keeps validating its own shape on the way back out.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import create_engine, delete, func, select
from sqlalchemy.orm import Session, sessionmaker

from wizard.db import tables
from wizard.models.ontology import Ontology, OntologyRisk
from wizard.models.profile import Profile
from wizard.prioritising import Severity
from wizard.profiling import Finding as ProfileFinding
from wizard.profiling import ProfileRun
from wizard.risk_mapping import Finding as MappingFinding
from wizard.risk_mapping import MappedObjective, Mapping, MappingRun


def digest_of(text: str) -> str:
    """A graph's identity: the sha256 of the bytes that were uploaded."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass
class Answer:
    """What the company decided about the three Annex III questions."""

    high_risk: bool
    personal_data: bool
    interacts_with_natural_persons: bool
    answered_at: datetime | None = None


@dataclass
class ProjectRecord:
    """One project, as the rest of the service wants it."""

    id: str
    name: str
    system_name: str
    qualification_id: str
    objectives_digest: str
    created_at: datetime
    updated_at: datetime
    jsonld: str = ""
    digest: str = ""
    ontology: Ontology = field(default_factory=Ontology)
    severity: Severity = field(default_factory=Severity)
    profile_run: ProfileRun | None = None
    answer: Answer | None = None
    mapping_run: MappingRun | None = None

    @property
    def confirmed(self) -> bool:
        """The gate on the second workflow: tiers order work that is owed, and
        nothing is owed until the company has answered."""
        return self.answer is not None


class ProjectRepository:
    def __init__(self, url: str, objectives_digest: str = ""):
        self._engine = create_engine(url, pool_pre_ping=True)
        self._sessions = sessionmaker(self._engine, expire_on_commit=False)
        self._url = url
        self._objectives_digest = objectives_digest

    def reopened(self) -> ProjectRepository:
        """A second repository on the same database: what a restart would give."""
        return ProjectRepository(self._url, self._objectives_digest)

    def create_all(self) -> None:
        """Tests and first run. Deployments use `alembic upgrade head`."""
        tables.Base.metadata.create_all(self._engine)

    # ── writing ───────────────────────────────────────────────────────────

    def create(self, name: str, ontology: Ontology, jsonld: str) -> ProjectRecord:
        with self._sessions.begin() as session:
            project = tables.Project(
                id=uuid.uuid4().hex[:12],
                name=name,
                system_name=ontology.system_name,
                qualification_id=ontology.qualification_id,
                objectives_digest=self._objectives_digest,
            )
            session.add(project)
            self._attach_graph(session, project, ontology, jsonld)
            session.flush()
            return self._to_record(project)

    def replace_ontology(self, project_id: str, ontology: Ontology, jsonld: str) -> ProjectRecord:
        """A corrected export replaces the graph and keeps the project.

        Ratings name risks, so a rating whose risk the new graph does not have
        goes with it; the graph is the authority on what risks exist. The
        mapping goes too: it was bought against the graph being replaced.
        """
        with self._sessions.begin() as session:
            project = session.get(tables.Project, project_id)
            kept = {
                row.risk_id: row.severity
                for row in project.risks
                if row.severity is not None
            }
            session.execute(delete(tables.Risk).where(tables.Risk.project_id == project_id))
            session.execute(
                delete(tables.MappingRunRow).where(tables.MappingRunRow.project_id == project_id)
            )
            if project.graph is not None:
                session.delete(project.graph)
            session.flush()
            # Severities are set as the rows are created: reading them back off
            # `project.risks` straight after a delete-and-re-add reads a stale
            # collection, and silently loses the assessor's ratings.
            self._attach_graph(session, project, ontology, jsonld, severities=kept)
            session.flush()
            project.system_name = ontology.system_name
            project.qualification_id = ontology.qualification_id
            session.flush()
            return self._to_record(project)

    def save_profile_run(self, project_id: str, run: ProfileRun, model: str = "") -> None:
        with self._sessions.begin() as session:
            project = session.get(tables.Project, project_id)
            if project.profile_run is not None:
                session.delete(project.profile_run)
                session.flush()
            session.add(
                tables.ProfileRunRow(
                    project_id=project_id,
                    profile=run.profile.model_dump(),
                    findings=[f.model_dump() for f in run.findings],
                    stop=run.stop,
                    attempts=run.attempts,
                    error=run.error,
                    model=model,
                )
            )

    def answer(
        self,
        project_id: str,
        high_risk: bool,
        personal_data: bool,
        interacts_with_natural_persons: bool,
    ) -> None:
        """The company's final word. Replaces any previous answer; the model's
        proposal is untouched, so a refusal stays visible."""
        with self._sessions.begin() as session:
            project = session.get(tables.Project, project_id)
            if project.answer is not None:
                session.delete(project.answer)
                session.flush()
            session.add(
                tables.Answer(
                    project_id=project_id,
                    high_risk=high_risk,
                    personal_data=personal_data,
                    interacts_with_natural_persons=interacts_with_natural_persons,
                )
            )

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

    def rate(self, project_id: str, ratings: dict[str, int]) -> None:
        with self._sessions.begin() as session:
            project = session.get(tables.Project, project_id)
            for row in project.risks:
                if row.risk_id in ratings:
                    row.severity = ratings[row.risk_id]

    def delete(self, project_id: str) -> None:
        with self._sessions.begin() as session:
            project = session.get(tables.Project, project_id)
            if project is not None:
                session.delete(project)

    # ── reading ───────────────────────────────────────────────────────────

    def get(self, project_id: str) -> ProjectRecord | None:
        with self._sessions() as session:
            project = session.get(tables.Project, project_id)
            return self._to_record(project) if project else None

    def list(self) -> list[ProjectRecord]:
        with self._sessions() as session:
            rows = session.scalars(
                select(tables.Project).order_by(tables.Project.updated_at.desc())
            ).all()
            return [self._to_record(row) for row in rows]

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
    def _attach_graph(
        session: Session,
        project: tables.Project,
        ontology: Ontology,
        jsonld: str,
        severities: dict[str, int] | None = None,
    ):
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
                    severity=(severities or {}).get(risk.id),
                )
            )

    @staticmethod
    def _to_record(project: tables.Project) -> ProjectRecord:
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
        mappings = {
            row.risk_id: Mapping(
                risk_id=row.risk_id,
                objectives=[
                    MappedObjective(
                        objective_id=item.objective_id, quote=item.quote, rationale=item.rationale
                    )
                    for item in row.mapped
                ],
                stop=(project.mapping_run.stops or {}).get(row.risk_id, "clean")
                if project.mapping_run
                else "clean",
            )
            for row in project.risks
        }

        record = ProjectRecord(
            id=project.id,
            name=project.name,
            system_name=project.system_name,
            qualification_id=project.qualification_id,
            objectives_digest=project.objectives_digest,
            created_at=project.created_at,
            updated_at=project.updated_at,
            jsonld=project.graph.jsonld if project.graph else "",
            digest=project.graph.digest if project.graph else "",
            ontology=Ontology(
                qualification_id=project.qualification_id,
                system_name=project.system_name,
                risks=risks,
            ),
            severity=Severity(
                ratings={
                    row.risk_id: row.severity for row in project.risks if row.severity is not None
                }
            ),
        )
        if project.profile_run is not None:
            record.profile_run = ProfileRun(
                profile=Profile.model_validate(project.profile_run.profile),
                findings=[ProfileFinding.model_validate(f) for f in project.profile_run.findings],
                stop=project.profile_run.stop,
                attempts=project.profile_run.attempts,
                error=project.profile_run.error,
            )
        if project.answer is not None:
            record.answer = Answer(
                high_risk=project.answer.high_risk,
                personal_data=project.answer.personal_data,
                interacts_with_natural_persons=project.answer.interacts_with_natural_persons,
                answered_at=project.answer.answered_at,
            )
        if project.mapping_run is not None:
            record.mapping_run = MappingRun(
                mappings=mappings,
                findings=[MappingFinding.model_validate(f) for f in project.mapping_run.findings],
                stop=project.mapping_run.stop,
                attempts=project.mapping_run.attempts,
                error=project.mapping_run.error,
            )
            record.mapping_run.model = project.mapping_run.model
        return record
