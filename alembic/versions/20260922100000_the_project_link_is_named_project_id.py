"""the project link is named project_id

Every module names the project it belongs to, and each named it differently:
`platform_project_id` here and in the engine, `projectId` in controls and
qualification. One name, `project_id`, so a query across schemas reads the same
whichever schema it is in.

The index and the key are renamed with it, because a constraint carrying the
old name is the old name, still there to be read and copied.

Revision ID: 3b91d0e7a52c
Revises: 0c6f2b4d91aa
"""
from alembic import op

revision = '3b91d0e7a52c'
down_revision = '0c6f2b4d91aa'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column('project', 'platform_project_id', new_column_name='project_id')
    op.execute('ALTER INDEX ix_project_platform_project_id RENAME TO ix_project_project_id')
    op.execute(
        'ALTER TABLE project RENAME CONSTRAINT '
        'fk_project_platform_project_id_core_project TO fk_project_project_id_core_project'
    )


def downgrade() -> None:
    op.execute(
        'ALTER TABLE project RENAME CONSTRAINT '
        'fk_project_project_id_core_project TO fk_project_platform_project_id_core_project'
    )
    op.execute('ALTER INDEX ix_project_project_id RENAME TO ix_project_platform_project_id')
    op.alter_column('project', 'project_id', new_column_name='platform_project_id')
