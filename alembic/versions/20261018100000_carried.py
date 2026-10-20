"""what an assessment took from the previous card version's

A new card version's assessment starts with the ratings, rationales, mappings and key choices of the
risks the two cards share. The assessment keeps what it took and what it left, so its page can say so.
Null for every existing assessment: it took nothing.

Revision ID: 20261018100000_carried
Revises: 20261018000000_mapped_by_hand
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "20261018100000_carried"
down_revision = "20261018000000_mapped_by_hand"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("project", sa.Column("carried", JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("project", "carried")
