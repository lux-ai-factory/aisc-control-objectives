"""the built-in short profile is called Fairness and oversight

It was stored as "Annex IV reduced" the first time an assessment took it. Only that stored row is
renamed; its id, fairness-oversight, stays, and so does any profile of the project's own.

Revision ID: 20261018300000_profile_name
Revises: 20261018200000_card_archive
"""
from alembic import op

revision = "20261018300000_profile_name"
down_revision = "20261018200000_card_archive"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("UPDATE objective_profile SET name = 'Fairness and oversight'"
               " WHERE id = 'fairness-oversight' AND name = 'Annex IV reduced'")


def downgrade() -> None:
    op.execute("UPDATE objective_profile SET name = 'Annex IV reduced'"
               " WHERE id = 'fairness-oversight' AND name = 'Fairness and oversight'")
