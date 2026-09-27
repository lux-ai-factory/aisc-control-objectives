"""The tables, on the qualification app's blueprint.

A table per real thing, `JSONB` only where nothing queries inside, cascading
deletes from the project, and `created_at`/`updated_at` on the root. The two
ideas worth copying from `KnowledgeGraph` are here too:

- **The uploaded graph is kept as the bytes that were uploaded**, not as a
  re-serialisation of the parse, so the file someone was given and the row are
  the same document.
- **It carries a digest**, its identity, so re-uploading the same graph is a
  no-op rather than a silent rebuild.

What is *not* here: scores and tiers. They are a pure function of the
catalogue, the ratings and the mapping, so storing them would only let them go
stale. `objectives_digest` records which catalogue a
project was assessed against, so a re-exported CSV cannot change an old
assessment's tiers without the page being able to say so.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

#: Each project has a database of its own (isolation 2026-09-25). This service
#: owns a schema in it, named after itself, and reads nothing outside that
#: schema except `project.system`, the project's saved card versions, which the
#: platform writes.
SCHEMA = "control_objectives"


def _now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    metadata = MetaData(schema=SCHEMA)


#: One saved AI card version of the project's one AI system (the platform
#: numbers them). An assessment is of exactly one. Declared so a foreign key
#: can point at it and the numbers can be read, marked external so migrations
#: never create or drop it: the platform's template 0006 makes it, and this
#: service may read it and point at it, and that is all it is granted.
project_system = Table(
    "system",
    Base.metadata,
    Column("pid", UUID(as_uuid=False), primary_key=True),
    Column("number", Integer),
    schema="project",
    info={"external": True},
)


class Project(Base):
    """One system being assessed. The aggregate everything else hangs off.

    It has no column naming its platform project: the database it is in is the
    project (I1.7). The table keeps its name (D6).
    """

    __tablename__ = "project"
    __table_args__ = (UniqueConstraint("system_id", name="uq_project_system_id"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    #: Which control-objectives catalogue this was assessed against.
    objectives_digest: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )
    #: The AI card version this assessment is of, a row of this database's
    #: project.system. One assessment per version; deleting the version takes
    #: its assessment with it.
    system_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("project.system.pid", ondelete="CASCADE",
                   name="fk_project_system_id_project_system"),
        nullable=False,
    )

    graph: Mapped[Graph | None] = relationship(
        back_populates="project", cascade="all, delete-orphan", uselist=False
    )
    risks: Mapped[list[Risk]] = relationship(
        back_populates="project", cascade="all, delete-orphan",
        order_by="Risk.position", lazy="selectin",
    )
    mapping_run: Mapped[MappingRunRow | None] = relationship(
        back_populates="project", cascade="all, delete-orphan", uselist=False
    )


class Graph(Base):
    """The uploaded AIRO graph: the bytes, and what they are."""

    __tablename__ = "graph"

    project_id: Mapped[str] = mapped_column(
        ForeignKey("project.id", ondelete="CASCADE"), primary_key=True
    )
    #: Exactly what was uploaded. An export serves these bytes back.
    jsonld: Mapped[str] = mapped_column(Text, nullable=False)
    #: sha256 of the bytes: the graph's identity.
    digest: Mapped[str] = mapped_column(String(64), nullable=False)
    risks: Mapped[int] = mapped_column(Integer, default=0)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    project: Mapped[Project] = relationship(back_populates="graph")


class Risk(Base):
    """One AIRO chain, flattened, with the assessor's severity on it.

    Rows rather than a blob because this is what gets queried and rated: "which
    projects carry an unmitigated poisoning risk" is a join.
    """

    __tablename__ = "risk"
    __table_args__ = (UniqueConstraint("project_id", "risk_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("project.id", ondelete="CASCADE"), index=True
    )
    #: The graph's own node id ("risk2").
    risk_id: Mapped[str] = mapped_column(Text, nullable=False)
    position: Mapped[int] = mapped_column(Integer, default=0)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    short_label: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(Text, default="")
    vulnerability: Mapped[str] = mapped_column(Text, default="")
    consequence: Mapped[str] = mapped_column(Text, default="")
    impact: Mapped[str] = mapped_column(Text, default="")
    stakeholder: Mapped[str] = mapped_column(Text, default="")
    control: Mapped[str] = mapped_column(Text, default="")
    follow_up_control: Mapped[str] = mapped_column(Text, default="")
    areas: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    vair_terms: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    provenance: Mapped[str] = mapped_column(Text, default="form")
    #: The assessor's 1-5. The irreplaceable part: a model did not produce it.
    severity: Mapped[int | None] = mapped_column(Integer, nullable=True)

    project: Mapped[Project] = relationship(back_populates="risks")
    mapped: Mapped[list[MappedObjectiveRow]] = relationship(
        back_populates="risk", cascade="all, delete-orphan", lazy="selectin"
    )


class MappedObjectiveRow(Base):
    """One objective a risk was mapped to, and the quote that supports it."""

    __tablename__ = "mapped_objective"
    __table_args__ = (UniqueConstraint("risk_row_id", "objective_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    risk_row_id: Mapped[int] = mapped_column(
        ForeignKey("risk.id", ondelete="CASCADE"), index=True
    )
    #: "R1.1". No foreign key: the catalogue is a CSV in the package, not a table.
    objective_id: Mapped[str] = mapped_column(Text, nullable=False)
    quote: Mapped[str] = mapped_column(Text, default="")
    rationale: Mapped[str] = mapped_column(Text, default="")

    risk: Mapped[Risk] = relationship(back_populates="mapped")


class MappingRunRow(Base):
    """How the second agentic workflow went. The mappings themselves are rows
    on the risks; this is the record of the run that bought them."""

    __tablename__ = "mapping_run"

    project_id: Mapped[str] = mapped_column(
        ForeignKey("project.id", ondelete="CASCADE"), primary_key=True
    )
    findings: Mapped[list] = mapped_column(JSONB, default=list)
    stops: Mapped[dict] = mapped_column(JSONB, default=dict)   # risk id -> its own stop
    stop: Mapped[str] = mapped_column(Text, default="clean")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str] = mapped_column(Text, default="")
    model: Mapped[str] = mapped_column(Text, default="")
    ran_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    project: Mapped[Project] = relationship(back_populates="mapping_run")
