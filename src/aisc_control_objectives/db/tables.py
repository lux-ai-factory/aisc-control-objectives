"""The tables, on the qualification app's blueprint.

A table per real thing, `JSONB` only where nothing queries inside, cascading
deletes from the project, and `created_at`/`updated_at` on the root. The two
ideas worth copying from `KnowledgeGraph` are here too:

- **The uploaded graph is kept as the bytes that were uploaded**, not as a
  re-serialisation of the parse, so the file someone was given and the row are
  the same document.
- **It carries a digest**, its identity, so re-uploading the same graph is a
  no-op rather than a silent rebuild.

What is *not* here: scores and default key objectives. They are a pure function
of the catalogue, the ratings and the mapping, so storing them would only let
them go stale. `objectives_digest` records which catalogue a project was
assessed against, so a re-exported CSV cannot change an old assessment's scores
without the page being able to say so.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    MetaData,
    PrimaryKeyConstraint,
    String,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

#: Each project has a database of its own. This service
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
    project.
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
    #: The objective profile version this assessment runs on; None is the built-in
    #: Full AI Act profile, all fifty objectives.
    profile_version_id: Mapped[str | None] = mapped_column(
        String(32),
        ForeignKey("objective_profile_version.id", ondelete="RESTRICT",
                   name="fk_project_profile_version_id"),
        nullable=True,
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
    selection: Mapped[ObjectiveSelectionRow | None] = relationship(
        back_populates="project", cascade="all, delete-orphan", uselist=False, lazy="selectin"
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
    #: The assessor's rating, impact x likelihood, each 1-5. The irreplaceable part: a model did
    #: not produce it. Not the AIRO chain's `impact` text above.
    rating_impact: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rating_likelihood: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: Why the assessor rated it so, optional; "" when there is none. The column name is older
    #: than the impact x likelihood rating and is kept because existing databases have it.
    severity_comment: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")

    project: Mapped[Project] = relationship(back_populates="risks")
    mapped: Mapped[list[MappedObjectiveRow]] = relationship(
        back_populates="risk", cascade="all, delete-orphan", lazy="selectin",
        order_by="MappedObjectiveRow.id",
    )


class MappedObjectiveRow(Base):
    """One objective a risk was mapped to, and the quote that supports it."""

    __tablename__ = "mapped_objective"
    __table_args__ = (UniqueConstraint("risk_row_id", "objective_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    risk_row_id: Mapped[int] = mapped_column(
        ForeignKey("risk.id", ondelete="CASCADE"), index=True
    )
    #: "O1". No foreign key: the catalogue is a CSV in the package, not a table.
    objective_id: Mapped[str] = mapped_column(Text, nullable=False)
    quote: Mapped[str] = mapped_column(Text, default="")
    rationale: Mapped[str] = mapped_column(Text, default="")
    #: Who mapped it: "ai" (the risk mapper) or "person".
    source: Mapped[str] = mapped_column(Text, nullable=False, default="ai", server_default="ai")

    risk: Mapped[Risk] = relationship(back_populates="mapped")


class MappingRunRow(Base):
    """How the last AI mapping run went. The mappings themselves are rows
    on the risks; this is the record of the run that produced them."""

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


class MappingArchive(Base):
    """What a mapping change would otherwise delete: before an AI run replaces the
    mappings, a profile switch drops some, or a person replaces a risk's, the run and every mapped row as
    they were (an assessor's own ones too) are kept here. Append-only; no key to the assessment, so a
    deleted assessment's history stays with the project's database."""

    __tablename__ = "mapping_archive"
    __table_args__ = (CheckConstraint("reason IN ('ai_run', 'profile', 'by_hand', 'deleted')", name="ck_mapping_archive_reason"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    #: why: "ai_run" (a new AI mapping), "profile" (a profile switch), "by_hand" (a person's edit of a risk)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    #: the run row as it was (findings, stops, stop, attempts, error, model, ran_at), or null
    run: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    #: every mapped row it replaces: risk, objective, quote, rationale, source
    rows: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    #: the ledger run that replaced it (ai_run), when there is one
    run_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    archived_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ObjectiveSelectionRow(Base):
    """Which objectives the project takes forward from this assessment: every
    objective its matrix holds, rewritten with each mapping change. Only these
    reach step 4, where tests and controls are linked to them. No row: nothing
    mapped yet; a row with no ids: the matrix is empty."""

    __tablename__ = "objective_selection"

    project_id: Mapped[str] = mapped_column(
        ForeignKey("project.id", ondelete="CASCADE"), primary_key=True
    )
    #: "O1", ... No foreign key, as for mapped_objective: the catalogue is a CSV.
    objective_ids: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )

    project: Mapped[Project] = relationship(back_populates="selection")


# Objective sets and profiles


class _ObjectiveFields:
    """What an objective of a user's set says, as the built-in CSV's columns do."""

    #: R1 ... R11, the trustworthiness dimension.
    dimension: Mapped[str] = mapped_column(Text, nullable=False)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    legal_basis: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    assessment_mode: Mapped[str] = mapped_column(Text, nullable=False)
    target: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    standards_grounding: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    grounding_tier_flag: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")


class ObjectiveSet(Base):
    """A set of objectives a user writes. Its code starts every id in it (BNK1, BNK2, ...)."""

    __tablename__ = "objective_set"
    __table_args__ = (
        UniqueConstraint("code", name="uq_objective_set_code"),
        CheckConstraint("code ~ '^[A-Z]{2,6}$'", name="ck_objective_set_code"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    code: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    #: The number the next objective gets: numbers are never reused.
    next_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    created_by: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    #: the author's Keycloak subject; created_by keeps the name shown
    created_by_sub: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")


class ObjectiveDraft(_ObjectiveFields, Base):
    """The set's working copy of one objective: edited freely until published."""

    __tablename__ = "objective_draft"
    __table_args__ = (
        PrimaryKeyConstraint("set_id", "number", name="pk_objective_draft"),
        UniqueConstraint("objective_id", name="uq_objective_draft_objective_id"),
    )

    set_id: Mapped[str] = mapped_column(ForeignKey("objective_set.id", ondelete="CASCADE"))
    number: Mapped[int] = mapped_column(Integer)
    #: The set's code and the number: "BNK3".
    objective_id: Mapped[str] = mapped_column(Text, nullable=False)
    #: Kept, with its number, but left out of the next version.
    retired: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")


class ObjectiveSetVersion(Base):
    """A published version of a set: numbered 1, 2, ... and never changed."""

    __tablename__ = "objective_set_version"
    __table_args__ = (UniqueConstraint("set_id", "number", name="uq_objective_set_version_number"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    set_id: Mapped[str] = mapped_column(ForeignKey("objective_set.id", ondelete="RESTRICT"), index=True)
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    published_by: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    #: the author's Keycloak subject; published_by keeps the name shown
    published_by_sub: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")


class ObjectiveSetVersionItem(_ObjectiveFields, Base):
    """One objective as a published set version words it."""

    __tablename__ = "objective_set_version_item"
    __table_args__ = (PrimaryKeyConstraint("set_version_id", "objective_id", name="pk_objective_set_version_item"),)

    set_version_id: Mapped[str] = mapped_column(ForeignKey("objective_set_version.id", ondelete="CASCADE"))
    objective_id: Mapped[str] = mapped_column(Text)
    position: Mapped[int] = mapped_column(Integer, nullable=False)


class ObjectiveProfile(Base):
    """A user's choice of objectives, from the built-in set and published sets."""

    __tablename__ = "objective_profile"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    created_by: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    #: the author's Keycloak subject; created_by keeps the name shown
    created_by_sub: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")


class ObjectiveProfileVersion(Base):
    """A saved profile: numbered 1, 2, ... and never changed. Assessments pin one."""

    __tablename__ = "objective_profile_version"
    __table_args__ = (UniqueConstraint("profile_id", "number", name="uq_objective_profile_version_number"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    profile_id: Mapped[str] = mapped_column(ForeignKey("objective_profile.id", ondelete="RESTRICT"), index=True)
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    created_by: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    #: the author's Keycloak subject; created_by keeps the name shown
    created_by_sub: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")


class ObjectiveProfileVersionItem(Base):
    """One objective a profile version takes, and from which set version."""

    __tablename__ = "objective_profile_version_item"
    __table_args__ = (
        PrimaryKeyConstraint("profile_version_id", "objective_id", name="pk_objective_profile_version_item"),
    )

    profile_version_id: Mapped[str] = mapped_column(
        ForeignKey("objective_profile_version.id", ondelete="CASCADE"))
    objective_id: Mapped[str] = mapped_column(Text)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    set_code: Mapped[str] = mapped_column(Text, nullable=False)
    #: The set version it was taken from; None for the built-in set.
    set_version_number: Mapped[int | None] = mapped_column(Integer, nullable=True)



class ObjectiveKey(Base):
    """The assessor's choice that an objective is, or is not, key. No row: the default
    (the first seven driven by a High or Critical risk)."""

    __tablename__ = "objective_key"
    __table_args__ = (PrimaryKeyConstraint("project_id", "objective_id", name="pk_objective_key"),)

    project_id: Mapped[str] = mapped_column(ForeignKey("project.id", ondelete="CASCADE"))
    objective_id: Mapped[str] = mapped_column(Text)
    key: Mapped[bool] = mapped_column(Boolean, nullable=False)
