"""an assessment's version belongs to its project

system_id points at core.system (pid) and project_id at core.project (pid),
but nothing tied the two together: an assessment could name project A and a
card version of project B. (system_id, project_id) now points at core.system
(pid, project_id), ON DELETE CASCADE as the system_id key.

The key needs core.system to be unique on (pid, project_id), which the
platform makes (init/platform-db.sql on a fresh volume, platform migration
0004 and init/project-databases.sql on an existing one); this role cannot.
Where it is not there yet, the key is left out with a notice, and
init/project-databases.sql adds it under this name on its next run.
8190233523 is the lock those places take too. Rows that break the rule stop
the upgrade, and the whole upgrade rolls back: assessments are never deleted.

Revision ID: 7c3e5a9b1d24
Revises: 4d2a9c1e7b60
"""
from alembic import op

revision = '7c3e5a9b1d24'
down_revision = '4d2a9c1e7b60'
branch_labels = None
depends_on = None

KEY = "fk_project_system_id_project_id_core_system"


def upgrade() -> None:
    op.execute(f"""
        DO $$
        BEGIN
          PERFORM pg_advisory_xact_lock(8190233523);
          IF EXISTS (SELECT 1 FROM pg_constraint
                      WHERE conrelid = 'control_objectives.project'::regclass AND conname = '{KEY}') THEN
            RETURN;
          END IF;
          IF EXISTS (SELECT 1 FROM pg_constraint
                      WHERE conrelid = 'core.system'::regclass AND conname = 'system_pid_project_id_key') THEN
            ALTER TABLE control_objectives.project ADD CONSTRAINT {KEY}
              FOREIGN KEY (system_id, project_id) REFERENCES core.system (pid, project_id) ON DELETE CASCADE;
          ELSE
            RAISE NOTICE 'core.system has no unique (pid, project_id) yet: {KEY} is left to init/project-databases.sql';
          END IF;
        END $$;""")


def downgrade() -> None:
    op.execute(f"ALTER TABLE control_objectives.project DROP CONSTRAINT IF EXISTS {KEY}")
