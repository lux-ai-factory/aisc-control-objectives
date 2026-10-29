"""the stored card keeps no count of its risks

graph.risks was written with every card and never read: the risk rows are the count. Dropped where it
is still there.

Revision ID: 20261029000000_graph_risk_count
Revises: 20261018300000_profile_name
"""
from alembic import op

revision = "20261029000000_graph_risk_count"
down_revision = "20261018300000_profile_name"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE graph DROP COLUMN IF EXISTS risks")


def downgrade() -> None:
    op.execute("ALTER TABLE graph ADD COLUMN IF NOT EXISTS risks integer")
    op.execute("UPDATE graph g SET risks = (SELECT count(*) FROM risk r WHERE r.project_id = g.project_id)")
