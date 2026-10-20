"""which risks a person mapped

An AI run maps only the risks no person has mapped, so a risk carries whether a person saved its
mapping. Unticking the AI's objectives leaves no row of the person's, hence a mark of its own. A risk
that already has a person's row is marked; every other one starts unmarked.

Revision ID: 20261018000000_mapped_by_hand
Revises: 20261003000000_ledger_history
"""
import sqlalchemy as sa
from alembic import op

revision = "20261018000000_mapped_by_hand"
down_revision = "20261003000000_ledger_history"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("risk", sa.Column("mapped_by_hand", sa.Boolean(), nullable=False, server_default="false"))
    op.execute("UPDATE risk SET mapped_by_hand = true WHERE EXISTS"
               " (SELECT 1 FROM mapped_objective m WHERE m.risk_row_id = risk.id AND m.source = 'person')")


def downgrade() -> None:
    op.drop_column("risk", "mapped_by_hand")
