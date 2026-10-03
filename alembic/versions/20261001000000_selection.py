"""which control objectives the project takes forward from an assessment

One row per assessment, the objective ids it takes forward to step 4. The
readers (report_ro, dashboard_ro) read it as they read the other tables. The
platform's step 4 page reads it as report_ro too, so nothing else is granted. A reader that does not exist (a scratch database) is
skipped.

Revision ID: 20261001000000_selection
Revises: 20260926000000_project_database
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261001000000_selection"
down_revision = "20260926000000_project_database"
branch_labels = None
depends_on = None

GRANTS = {
    "report_ro": ("objective_selection",),
    "dashboard_ro": ("objective_selection",),
}


def upgrade() -> None:
    op.create_table(
        "objective_selection",
        sa.Column("project_id", sa.String(length=32), nullable=False),
        sa.Column("objective_ids", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["project.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("project_id"),
    )
    op.execute("""
        INSERT INTO control_objectives.objective_selection (project_id, objective_ids, updated_at)
        SELECT r.project_id, array_agg(DISTINCT m.objective_id ORDER BY m.objective_id), now()
          FROM control_objectives.mapped_objective m
          JOIN control_objectives.risk r ON r.id = m.risk_row_id
          JOIN control_objectives.mapping_run mr ON mr.project_id = r.project_id
         GROUP BY r.project_id""")
    for role, names in GRANTS.items():
        tables = ", ".join(f"control_objectives.{t}" for t in names)
        op.execute(f"""
            DO $$ BEGIN
              IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
                EXECUTE 'GRANT SELECT ON {tables} TO {role}';
              END IF;
            END $$;""")


def downgrade() -> None:
    op.drop_table("objective_selection")
