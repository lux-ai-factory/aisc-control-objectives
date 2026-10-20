"""who mapped a risk to an objective: the AI or a person

A risk's mapping is made by the risk mapper or by a person. Every existing row is the risk
mapper's, so the column starts at 'ai'.

Revision ID: 20261001110000_mapping_source
Revises: 20261001100000_objective_ids
"""
import sqlalchemy as sa
from alembic import op

revision = "20261001110000_mapping_source"
down_revision = "20261001100000_objective_ids"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("mapped_objective", sa.Column("source", sa.Text(), nullable=False, server_default="ai"))


def downgrade() -> None:
    op.drop_column("mapped_objective", "source")
