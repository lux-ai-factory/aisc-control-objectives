"""the selection stores which of its objectives are priority

objective_selection.priority_ids: the selected objectives that are priority, the assessor's choices in
objective_key over the default rule, so the report reads what the page shows. Existing assessments get
theirs worked out by the service's own code (projects.store_every_priority), the same rule the page
uses. The readers keep their SELECT on the table, which covers the new column.

The backfill reads the assessments through the service's own table classes (db/tables.py), which
describe the head: a later migration adding a column to a table they read has to keep this one working
on a database older than itself.

Revision ID: 20261030000000_priority_ids
Revises: 20261029000000_graph_risk_count
"""
from alembic import op

revision = "20261030000000_priority_ids"
down_revision = "20261029000000_graph_risk_count"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from aisc_control_objectives.projects import store_every_priority

    op.execute("ALTER TABLE objective_selection ADD COLUMN IF NOT EXISTS priority_ids text[] NOT NULL DEFAULT '{}'")
    store_every_priority(op.get_bind())


def downgrade() -> None:
    op.execute("ALTER TABLE objective_selection DROP COLUMN IF EXISTS priority_ids")
