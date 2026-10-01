"""objective sets and objective profiles

2026-10-01: a project writes its own objective sets (a code it chooses, objectives numbered in it,
published as versions that never change) and assembles objective profiles from the built-in set and
those versions; an assessment pins a profile version. An assessment with none is on the built-in
Full AI Act profile, so every existing one keeps working as it is. The readers (report_ro,
dashboard_ro) read the published tables, as they read the others; a reader that does not exist (a
scratch database) is skipped.

Revision ID: 20261002000000_objective_sets
Revises: 20261001120000_severity_comment
"""
import sqlalchemy as sa
from alembic import op

revision = "20261002000000_objective_sets"
down_revision = "20261001120000_severity_comment"
branch_labels = None
depends_on = None

READ = ("objective_set", "objective_set_version", "objective_set_version_item",
        "objective_profile", "objective_profile_version", "objective_profile_version_item")
GRANTS = {"report_ro": READ, "dashboard_ro": READ}


def _fields() -> list:
    return [
        sa.Column("dimension", sa.Text(), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("legal_basis", sa.Text(), nullable=False, server_default=""),
        sa.Column("assessment_mode", sa.Text(), nullable=False),
        sa.Column("target", sa.Text(), nullable=False, server_default=""),
        sa.Column("standards_grounding", sa.Text(), nullable=False, server_default=""),
        sa.Column("grounding_tier_flag", sa.Text(), nullable=False, server_default=""),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
    ]


def upgrade() -> None:
    op.create_table(
        "objective_set",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("next_number", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.Text(), nullable=False, server_default=""),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", name="uq_objective_set_code"),
        sa.CheckConstraint("code ~ '^[A-Z]{2,6}$'", name="ck_objective_set_code"),
    )
    op.create_table(
        "objective_draft",
        *_fields(),
        sa.Column("set_id", sa.String(length=32), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("objective_id", sa.Text(), nullable=False),
        sa.Column("retired", sa.Boolean(), nullable=False, server_default="false"),
        sa.ForeignKeyConstraint(["set_id"], ["objective_set.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("set_id", "number", name="pk_objective_draft"),
        sa.UniqueConstraint("objective_id", name="uq_objective_draft_objective_id"),
    )
    op.create_table(
        "objective_set_version",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("set_id", sa.String(length=32), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_by", sa.Text(), nullable=False, server_default=""),
        sa.ForeignKeyConstraint(["set_id"], ["objective_set.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("set_id", "number", name="uq_objective_set_version_number"),
    )
    op.create_index("ix_objective_set_version_set_id", "objective_set_version", ["set_id"])
    op.create_table(
        "objective_set_version_item",
        *_fields(),
        sa.Column("set_version_id", sa.String(length=32), nullable=False),
        sa.Column("objective_id", sa.Text(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["set_version_id"], ["objective_set_version.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("set_version_id", "objective_id", name="pk_objective_set_version_item"),
    )
    op.create_table(
        "objective_profile",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.Text(), nullable=False, server_default=""),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "objective_profile_version",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("profile_id", sa.String(length=32), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.Text(), nullable=False, server_default=""),
        sa.ForeignKeyConstraint(["profile_id"], ["objective_profile.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("profile_id", "number", name="uq_objective_profile_version_number"),
    )
    op.create_index("ix_objective_profile_version_profile_id", "objective_profile_version", ["profile_id"])
    op.create_table(
        "objective_profile_version_item",
        sa.Column("profile_version_id", sa.String(length=32), nullable=False),
        sa.Column("objective_id", sa.Text(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("set_code", sa.Text(), nullable=False),
        sa.Column("set_version_number", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["profile_version_id"], ["objective_profile_version.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("profile_version_id", "objective_id", name="pk_objective_profile_version_item"),
    )
    op.add_column("project", sa.Column("profile_version_id", sa.String(length=32), nullable=True))
    op.create_foreign_key("fk_project_profile_version_id", "project", "objective_profile_version",
                          ["profile_version_id"], ["id"], ondelete="RESTRICT")
    for role, names in GRANTS.items():
        tables = ", ".join(f"control_objectives.{t}" for t in names)
        op.execute(f"""
            DO $$ BEGIN
              IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
                EXECUTE 'GRANT SELECT ON {tables} TO {role}';
              END IF;
            END $$;""")


def downgrade() -> None:
    op.drop_constraint("fk_project_profile_version_id", "project", type_="foreignkey")
    op.drop_column("project", "profile_version_id")
    for table in ("objective_profile_version_item", "objective_profile_version", "objective_profile",
                  "objective_set_version_item", "objective_set_version", "objective_draft", "objective_set"):
        op.drop_table(table)
