"""The tables, on the qualification app's blueprint.

A table per real thing, `JSONB` only where nothing queries inside, cascading
deletes from the project, and `created_at`/`updated_at` on the root. The two
ideas worth copying from `KnowledgeGraph` are here too:

- **The uploaded graph is kept as the bytes that were uploaded**, not as a
  re-serialisation of the parse, so the file someone was given and the row are
  the same document.
- **It carries a digest**, its identity, so re-uploading the same graph is a
  no-op rather than a silent rebuild.

What is *not* here: verdicts, scores and tiers. They are a pure function of the
catalogue, the company's answer, the ratings and the mapping, so storing them
would only let them go stale. `objectives_digest` records which catalogue a
project was assessed against, so a re-exported CSV cannot change an old
assessment's tiers without the page being able to say so.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Project(Base):
    """One system being assessed. The aggregate everything else hangs off."""

    __tablename__ = "project"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    system_name: Mapped[str] = mapped_column(Text, default="")
    #: From the graph, so a corrected re-export finds its project.
    qualification_id: Mapped[str] = mapped_column(Text, default="", index=True)
    #: Which control-objectives catalogue this was assessed against.
    objectives_digest: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )

    graph: Mapped[Graph | None] = relationship(
        back_populates="project", cascade="all, delete-orphan", uselist=False
    )
    risks: Mapped[list[Risk]] = relationship(
        back_populates="project", cascade="all, delete-orphan",
        order_by="Risk.position", lazy="selectin",
    )
    profile_run: Mapped[ProfileRunRow | None] = relationship(
        back_populates="project", cascade="all, delete-orphan", uselist=False
    )
    answer: Mapped[Answer | None] = relationship(
        back_populates="project", cascade="all, delete-orphan", uselist=False
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


class ProfileRunRow(Base):
    """What the first agentic workflow proposed, and how its rounds ended."""

    __tablename__ = "profile_run"

    project_id: Mapped[str] = mapped_column(
        ForeignKey("project.id", ondelete="CASCADE"), primary_key=True
    )
    #: The model's three facts with their quotes: the proposal of record, kept
    #: whatever the company decides afterwards.
    profile: Mapped[dict] = mapped_column(JSONB, nullable=False)
    findings: Mapped[list] = mapped_column(JSONB, default=list)
    stop: Mapped[str] = mapped_column(Text, default="clean")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str] = mapped_column(Text, default="")
    model: Mapped[str] = mapped_column(Text, default="")
    ran_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    project: Mapped[Project] = relationship(back_populates="profile_run")


class Answer(Base):
    """The company's decision on the three Annex III questions.

    Booleans, not three-way: a model may be unsure, a register may not. Absent
    until somebody has confirmed, which is what gates the second workflow.
    """

    __tablename__ = "answer"

    project_id: Mapped[str] = mapped_column(
        ForeignKey("project.id", ondelete="CASCADE"), primary_key=True
    )
    high_risk: Mapped[bool] = mapped_column(Boolean, nullable=False)
    personal_data: Mapped[bool] = mapped_column(Boolean, nullable=False)
    interacts_with_natural_persons: Mapped[bool] = mapped_column(Boolean, nullable=False)
    answered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    project: Mapped[Project] = relationship(back_populates="answer")


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
