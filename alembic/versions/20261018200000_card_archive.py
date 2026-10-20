"""an update to a changed card keeps what it replaces

When the card of an assessment's version changed and the assessor updates to it, the risks are read
again and the mapping rows they had are kept in mapping_archive, as any mapping change keeps them, with
the reason 'card'.

Revision ID: 20261018200000_card_archive
Revises: 20261018100000_carried
"""
from alembic import op

revision = "20261018200000_card_archive"
down_revision = "20261018100000_carried"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_mapping_archive_reason", "mapping_archive", type_="check")
    op.create_check_constraint("ck_mapping_archive_reason", "mapping_archive",
                               "reason IN ('ai_run', 'profile', 'by_hand', 'deleted', 'card')")


def downgrade() -> None:
    op.drop_constraint("ck_mapping_archive_reason", "mapping_archive", type_="check")
    op.create_check_constraint("ck_mapping_archive_reason", "mapping_archive",
                               "reason IN ('ai_run', 'profile', 'by_hand', 'deleted')")
