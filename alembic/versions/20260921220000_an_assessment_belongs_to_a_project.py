"""an assessment belongs to a platform project

There is one database. `core.project` is the platform's list of projects, and
every assessment here is of one of them: the column is NOT NULL and its
foreign key cascades, so deleting a project takes its assessments with it.

`core.project` is not created here. The platform owns it, this service is
granted SELECT and REFERENCES on it, and that is the whole of the contract.

Revision ID: 0c6f2b4d91aa
Revises: 085f844985f5
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = '0c6f2b4d91aa'
down_revision = '085f844985f5'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('project', sa.Column('platform_project_id', postgresql.UUID(), nullable=True))
    # An existing assessment cannot be guessed into a project: a database that
    # already has rows stops here rather than inventing an owner for them.
    op.execute("ALTER TABLE project ALTER COLUMN platform_project_id SET NOT NULL")
    op.create_index(
        op.f('ix_project_platform_project_id'), 'project', ['platform_project_id'], unique=False
    )
    op.create_foreign_key(
        'fk_project_platform_project_id_core_project',
        'project', 'project', ['platform_project_id'], ['pid'],
        source_schema=None, referent_schema='core', ondelete='CASCADE',
    )


def downgrade() -> None:
    op.drop_constraint('fk_project_platform_project_id_core_project', 'project', type_='foreignkey')
    op.drop_index(op.f('ix_project_platform_project_id'), table_name='project')
    op.drop_column('project', 'platform_project_id')
