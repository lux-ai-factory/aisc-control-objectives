"""an assessment is of one system version

The project has one AI system, and its AI card is what is versioned: each saved
version is a row of core.system, numbered per project by the platform. An
assessment is of exactly one of those versions (`system_id`), and a version has
at most one assessment. The card's bytes are still stored with the assessment;
its system name and qualification id are read from them, so the two columns
that copied them go.

Existing assessments belong to their project's latest version. Assessments are
never deleted here: a row with no version to belong to, or two rows that would
share one, stop the migration, and the whole upgrade rolls back.

Revision ID: 4d2a9c1e7b60
Revises: 3b91d0e7a52c
"""
import sqlalchemy as sa
from alembic import op

revision = '4d2a9c1e7b60'
down_revision = '3b91d0e7a52c'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE project ADD COLUMN system_id uuid")
    op.execute("""
        UPDATE project p SET system_id = (
          SELECT s.pid FROM core.system s WHERE s.project_id = p.project_id
           ORDER BY s.number DESC LIMIT 1)""")
    op.execute("""
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM project WHERE system_id IS NULL) THEN
            RAISE EXCEPTION 'an assessment has no system version to belong to; assessments are never deleted';
          END IF;
          IF EXISTS (SELECT 1 FROM project GROUP BY system_id HAVING count(*) > 1) THEN
            RAISE EXCEPTION 'two assessments would belong to one system version; keep one first';
          END IF;
        END $$;""")
    op.execute("ALTER TABLE project ALTER COLUMN system_id SET NOT NULL")
    op.create_unique_constraint("uq_project_system_id", "project", ["system_id"])
    op.create_foreign_key("fk_project_system_id_core_system", "project", "system",
                          ["system_id"], ["pid"], referent_schema="core", ondelete="CASCADE")
    op.execute("DROP INDEX IF EXISTS ix_project_qualification_id")
    op.drop_column("project", "qualification_id")
    op.drop_column("project", "system_name")


def downgrade() -> None:
    op.add_column("project", sa.Column("system_name", sa.Text(), nullable=False, server_default=""))
    op.add_column("project", sa.Column("qualification_id", sa.Text(), nullable=False, server_default=""))
    op.create_index("ix_project_qualification_id", "project", ["qualification_id"])
    op.drop_constraint("fk_project_system_id_core_system", "project", type_="foreignkey")
    op.drop_constraint("uq_project_system_id", "project", type_="unique")
    op.drop_column("project", "system_id")
