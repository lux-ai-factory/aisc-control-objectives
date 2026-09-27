"""a project database: the assessment, its card, risks, mappings and runs

The one revision of a project's own database (isolation 2026-09-25, 01-specs.md
I5.4). It makes the final shape of the old chain (711cc0e45c33 to 7c3e5a9b1d24,
which lived in the shared platform database) minus what named the platform
project: the database is the project, so `project.project_id`, its index and
its keys into the platform are gone (I1.7). `system_id`, the AI card version an
assessment is of, references `project.system(pid)` of the same database ON
DELETE CASCADE, one assessment per version (I1.6).

Table, column, index and constraint names are the live ones, so the data move
copies column by column. `project.system` is not made here: the platform's
template 0006 makes it, and this role may read it and point at it. The readers
(report_ro, dashboard_ro) get SELECT on the five tables from their owner, here
(I2.6); nothing on alembic_version or the sequences.

Revision ID: 20260926000000_project_database
Revises:
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260926000000_project_database"
down_revision = None
branch_labels = None
depends_on = None

READER_TABLES = ("project", "graph", "risk", "mapped_objective", "mapping_run")


def upgrade() -> None:
    op.create_table(
        "project",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("objectives_digest", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("system_id", postgresql.UUID(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("system_id", name="uq_project_system_id"),
    )
    op.create_foreign_key(
        "fk_project_system_id_project_system",
        "project", "system", ["system_id"], ["pid"],
        referent_schema="project", ondelete="CASCADE",
    )
    op.create_table(
        "graph",
        sa.Column("project_id", sa.String(length=32), nullable=False),
        sa.Column("jsonld", sa.Text(), nullable=False),
        sa.Column("digest", sa.String(length=64), nullable=False),
        sa.Column("risks", sa.Integer(), nullable=False),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["project.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("project_id"),
    )
    op.create_table(
        "mapping_run",
        sa.Column("project_id", sa.String(length=32), nullable=False),
        sa.Column("findings", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("stops", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("stop", sa.Text(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("error", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("ran_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["project.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("project_id"),
    )
    op.create_table(
        "risk",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.String(length=32), nullable=False),
        sa.Column("risk_id", sa.Text(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("short_label", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("vulnerability", sa.Text(), nullable=False),
        sa.Column("consequence", sa.Text(), nullable=False),
        sa.Column("impact", sa.Text(), nullable=False),
        sa.Column("stakeholder", sa.Text(), nullable=False),
        sa.Column("control", sa.Text(), nullable=False),
        sa.Column("follow_up_control", sa.Text(), nullable=False),
        sa.Column("areas", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("vair_terms", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("provenance", sa.Text(), nullable=False),
        sa.Column("severity", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["project.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "risk_id"),
    )
    op.create_index(op.f("ix_risk_project_id"), "risk", ["project_id"], unique=False)
    op.create_table(
        "mapped_objective",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("risk_row_id", sa.Integer(), nullable=False),
        sa.Column("objective_id", sa.Text(), nullable=False),
        sa.Column("quote", sa.Text(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["risk_row_id"], ["risk.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("risk_row_id", "objective_id"),
    )
    op.create_index(
        op.f("ix_mapped_objective_risk_row_id"), "mapped_objective", ["risk_row_id"], unique=False
    )
    # I2.6: the two readers read the five tables, granted by their owner. A
    # reader role that does not exist (a scratch database) is skipped.
    tables = ", ".join(f"control_objectives.{t}" for t in READER_TABLES)
    op.execute(f"""
        DO $$ DECLARE r text; BEGIN
          FOREACH r IN ARRAY ARRAY['report_ro', 'dashboard_ro'] LOOP
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN
              EXECUTE format('GRANT SELECT ON {tables} TO %I', r);
            END IF;
          END LOOP;
        END $$;""")


def downgrade() -> None:
    op.drop_index(op.f("ix_mapped_objective_risk_row_id"), table_name="mapped_objective")
    op.drop_table("mapped_objective")
    op.drop_index(op.f("ix_risk_project_id"), table_name="risk")
    op.drop_table("risk")
    op.drop_table("mapping_run")
    op.drop_table("graph")
    op.drop_table("project")
