"""the control objectives tables belong to control_objectives_owner

Migrations run as control_objectives_migrator (PROJECT_MIGRATE_DATABASE_URL), whose sessions act as
control_objectives_owner, a role no application logs in as. The service connects as control_objectives_rw,
which from here on reads and writes rows only: it cannot create, alter, truncate or put triggers on these
tables, nor lift a guard. On the mapping archive it may only insert and read. An assessment's reference to
its card version restricts: the version cannot be deleted from under it.

In a project database this stops first unless the schema is control_objectives_owner's and the session acts
as control_objectives_owner: the platform hands the schema over (template 0033) once postgres-setup has
made the role. Safe on an install whose tables control_objectives_rw made: they are handed to the owner
here. In a scratch database outside the stack only the reference changes.

Not undone by the downgrade: the reference goes back to CASCADE, the ownership stays.

Revision ID: 20261115000000_owner_role
Revises: 20261108000000_no_risk_measures
"""
from alembic import op

revision = "20261115000000_owner_role"
down_revision = "20261108000000_no_risk_measures"
branch_labels = None
depends_on = None

STOP_UNLESS_OWNER = """
DO $owner$
BEGIN
  IF current_database() ~ '^project_[0-9a-f]{32}$' AND (
       NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'control_objectives_owner')
       OR (SELECT pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname = 'control_objectives')
          IS DISTINCT FROM 'control_objectives_owner'
       OR current_user <> 'control_objectives_owner') THEN
    RAISE EXCEPTION 'control_objectives_owner is missing or does not own schema control_objectives: run postgres-setup and the platform first, then migrate as control_objectives_migrator (PROJECT_MIGRATE_DATABASE_URL)';
  END IF;
END
$owner$;
"""

HAND_OVER = """
DO $roles$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'control_objectives_owner')
     OR current_user <> 'control_objectives_owner' THEN
    RETURN;
  END IF;
  -- what the runtime role made before it stopped migrating
  REASSIGN OWNED BY control_objectives_rw TO control_objectives_owner;
  REVOKE CREATE ON SCHEMA control_objectives FROM control_objectives_rw;
  GRANT USAGE ON SCHEMA control_objectives TO control_objectives_rw;

  REVOKE ALL ON ALL TABLES IN SCHEMA control_objectives FROM control_objectives_rw;
  GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA control_objectives TO control_objectives_rw;
  REVOKE UPDATE, DELETE ON control_objectives.mapping_archive FROM control_objectives_rw;
  -- the migrations' own record is the migrator's
  REVOKE ALL ON control_objectives.alembic_version FROM control_objectives_rw;
  REVOKE ALL ON ALL SEQUENCES IN SCHEMA control_objectives FROM control_objectives_rw;
  GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA control_objectives TO control_objectives_rw;
  ALTER DEFAULT PRIVILEGES FOR ROLE control_objectives_owner IN SCHEMA control_objectives
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO control_objectives_rw;
  ALTER DEFAULT PRIVILEGES FOR ROLE control_objectives_owner IN SCHEMA control_objectives
    GRANT USAGE, SELECT ON SEQUENCES TO control_objectives_rw;
END
$roles$;
"""


def _reference(on_delete: str) -> None:
    op.execute("ALTER TABLE project DROP CONSTRAINT IF EXISTS fk_project_system_id_project_system")
    op.execute("ALTER TABLE project ADD CONSTRAINT fk_project_system_id_project_system"
               f" FOREIGN KEY (system_id) REFERENCES project.system (pid) ON DELETE {on_delete}")


def upgrade() -> None:
    op.execute(STOP_UNLESS_OWNER)
    _reference("RESTRICT")
    op.execute(HAND_OVER)


def downgrade() -> None:
    _reference("CASCADE")
