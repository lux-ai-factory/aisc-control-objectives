"""the provider's measures leave step 2

risk.control and risk.follow_up_control were copies of the measure an older card named per risk and of
its follow-up. Step 2 no longer reads, stores or shows them, so the columns go.

Each stored card (graph.jsonld) loses its RiskControl nodes and the edges to them, and its digest is
worked out again from what is left, the way step 2 always works it out (sha256 of the stored text). A
card that names no measure is left as it is, byte for byte. Running again changes nothing.

Not undone by the downgrade: the columns come back empty and the stored cards keep no measures. The
backup taken before the upgrade holds them.

Revision ID: 20261108000000_no_risk_measures
Revises: 20261030000000_priority_ids
"""
from alembic import op
from sqlalchemy import text

revision = "20261108000000_no_risk_measures"
down_revision = "20261030000000_priority_ids"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from aisc_control_objectives.db.repository import digest_of
    from aisc_control_objectives.models.ontology import card_without_measures

    op.execute("ALTER TABLE risk DROP COLUMN IF EXISTS control")
    op.execute("ALTER TABLE risk DROP COLUMN IF EXISTS follow_up_control")
    bind = op.get_bind()
    for project_id, jsonld in bind.execute(text("SELECT project_id, jsonld FROM graph")).all():
        cleaned = card_without_measures(jsonld)
        if cleaned is not None:
            bind.execute(text("UPDATE graph SET jsonld = :j, digest = :d WHERE project_id = :p"),
                         {"j": cleaned, "d": digest_of(cleaned), "p": project_id})


def downgrade() -> None:
    op.execute("ALTER TABLE risk ADD COLUMN IF NOT EXISTS control text NOT NULL DEFAULT ''")
    op.execute("ALTER TABLE risk ADD COLUMN IF NOT EXISTS follow_up_control text NOT NULL DEFAULT ''")
