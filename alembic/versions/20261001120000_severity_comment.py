"""an optional comment on a risk's severity

2026-10-01: the assessor can say why a risk is rated as it is. Empty for every risk there is.

Revision ID: 20261001120000_severity_comment
Revises: 20261001110000_mapping_source
"""
import sqlalchemy as sa
from alembic import op

revision = "20261001120000_severity_comment"
down_revision = "20261001110000_mapping_source"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("risk", sa.Column("severity_comment", sa.Text(), nullable=False, server_default=""))


def downgrade() -> None:
    op.drop_column("risk", "severity_comment")
