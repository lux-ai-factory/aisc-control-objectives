"""Alembic's entry point. The URL comes from the environment, never the file,
so the same migrations run against dev, the platform and a test database."""

from alembic import context
from sqlalchemy import create_engine

from wizard.db.tables import Base
from wizard.settings import database_url

target_metadata = Base.metadata


def run_migrations_online() -> None:
    engine = create_engine(database_url())
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


run_migrations_online()
