"""the ledger: mapping changes keep what they replace; authors kept by subject

mapping_archive keeps the run and every mapped row (an assessor's own ones too) that an AI run, a
profile switch, a person's edit of a risk or the assessment's deletion replaces: append-only, refused
UPDATE, DELETE and TRUNCATE. The table's owner (the role migrations run as) can still drop the trigger;
the ledger's frozen copies are the check on that. Beside each author name (created_by, published_by), its Keycloak subject; the name stays what pages show.

Revision ID: 20261003000000_ledger_history
Revises: 20261002100000_rcm
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "20261003000000_ledger_history"
down_revision = "20261002100000_rcm"
branch_labels = None
depends_on = None

AUTHORS = (("objective_set", "created_by"), ("objective_set_version", "published_by"),
           ("objective_profile", "created_by"), ("objective_profile_version", "created_by"))


def upgrade() -> None:
    op.create_table(
        "mapping_archive",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.String(length=32), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("run", JSONB(), nullable=True),
        sa.Column("rows", JSONB(), nullable=False, server_default="[]"),
        sa.Column("run_id", sa.Text(), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("reason IN ('ai_run', 'profile', 'by_hand', 'deleted')", name="ck_mapping_archive_reason"),
    )
    op.create_index("ix_mapping_archive_project_id", "mapping_archive", ["project_id"])
    op.execute("""
        CREATE FUNCTION mapping_archive_is_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION 'mapping archive row % is immutable', OLD.id;
        END $$;
        CREATE TRIGGER mapping_archive_is_append_only BEFORE UPDATE OR DELETE ON mapping_archive
          FOR EACH ROW EXECUTE FUNCTION mapping_archive_is_append_only();
        CREATE FUNCTION mapping_archive_is_never_truncated() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION 'the mapping archive is append-only: it is never truncated';
        END $$;
        CREATE TRIGGER mapping_archive_is_never_truncated BEFORE TRUNCATE ON mapping_archive
          FOR EACH STATEMENT EXECUTE FUNCTION mapping_archive_is_never_truncated();
    """)
    for table, column in AUTHORS:
        op.add_column(table, sa.Column(f"{column}_sub", sa.Text(), nullable=False, server_default=""))


def downgrade() -> None:
    for table, column in AUTHORS:
        op.drop_column(table, f"{column}_sub")
    op.execute("DROP TRIGGER IF EXISTS mapping_archive_is_never_truncated ON mapping_archive")
    op.execute("DROP FUNCTION IF EXISTS mapping_archive_is_never_truncated()")
    op.execute("DROP TRIGGER IF EXISTS mapping_archive_is_append_only ON mapping_archive")
    op.execute("DROP FUNCTION IF EXISTS mapping_archive_is_append_only()")
    op.drop_table("mapping_archive")
