"""the risk and control matrix: impact x likelihood, key objectives, scope is the matrix

2026-10-01: a risk is rated impact x likelihood, each 1-5; its severity becomes its impact and its
likelihood starts unrated. The tiers give way to key objectives, which the assessor can set
(objective_key; no row is the default). The separate selection gives way to the matrix: what an
assessment takes forward to step 4 is what its matrix holds, so every selection is rewritten to its
mapped objectives in catalogue order (the built-in set by number, then the project's sets by code
and number). The readers read objective_key as they read the rest; a reader that does not exist
(a scratch database) is skipped.

Revision ID: 20261002100000_rcm
Revises: 20261002000000_objective_sets
"""
import sqlalchemy as sa
from alembic import op

revision = "20261002100000_rcm"
down_revision = "20261002000000_objective_sets"
branch_labels = None
depends_on = None

GRANTS = {"report_ro": ("objective_key",), "dashboard_ro": ("objective_key",)}

#: The order of an objective id: O by number first, then a set's code and number.
ORDER = ("CASE WHEN o ~ '^O[0-9]+$' THEN 0 WHEN o ~ '^[A-Z]{2,6}[0-9]+$' THEN 1 ELSE 2 END,"
         " substring(o from '^([A-Z]+)'), substring(o from '([0-9]+)$')::int, o")


def upgrade() -> None:
    op.alter_column("risk", "severity", new_column_name="rating_impact")
    op.add_column("risk", sa.Column("rating_likelihood", sa.Integer(), nullable=True))
    op.create_table(
        "objective_key",
        sa.Column("project_id", sa.String(length=32), nullable=False),
        sa.Column("objective_id", sa.Text(), nullable=False),
        sa.Column("key", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["project.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("project_id", "objective_id", name="pk_objective_key"),
    )
    op.execute(f"""
        UPDATE control_objectives.objective_selection s SET objective_ids = coalesce((
            SELECT array_agg(o ORDER BY {ORDER})
              FROM (SELECT DISTINCT m.objective_id AS o
                      FROM control_objectives.mapped_objective m
                      JOIN control_objectives.risk r ON r.id = m.risk_row_id
                     WHERE r.project_id = s.project_id) mapped), '{{}}')""")
    for role, names in GRANTS.items():
        tables = ", ".join(f"control_objectives.{t}" for t in names)
        op.execute(f"""
            DO $$ BEGIN
              IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
                EXECUTE 'GRANT SELECT ON {tables} TO {role}';
              END IF;
            END $$;""")


def downgrade() -> None:
    op.drop_table("objective_key")
    op.drop_column("risk", "rating_likelihood")
    op.alter_column("risk", "rating_impact", new_column_name="severity")
