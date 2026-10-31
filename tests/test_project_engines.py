"""The project engines held open: past MAX_OPEN_PROJECTS the least recently used one is closed, but
never under a request still using it. Its pool is disposed once its last connection comes back."""
from __future__ import annotations

from sqlalchemy import text

from aisc_control_objectives.projectdb import ProjectDatabases


def databases(tmp_path, capacity=1):
    return ProjectDatabases("sqlite://", f"sqlite:///{tmp_path}/{{database}}.db", capacity=capacity,
                            migrate=lambda engine: None)


def test_an_evicted_engine_in_use_keeps_its_pool_until_its_last_connection_returns(tmp_path):
    dbs = databases(tmp_path)
    first = dbs._engine_for("project_a")
    pool = first.pool
    with first.connect() as connection:                               # a request still on project a
        dbs._engine_for("project_b")                                  # past capacity: a is evicted
        assert first.pool is pool and pool.checkedout() == 1
        assert connection.execute(text("SELECT 1")).scalar() == 1
    dbs._engine_for("project_c")                                      # the next open closes it
    assert first.pool is not pool


def test_an_evicted_engine_nobody_uses_is_disposed_at_once(tmp_path):
    dbs = databases(tmp_path)
    first = dbs._engine_for("project_a")
    pool = first.pool
    dbs._engine_for("project_b")
    assert first.pool is not pool


def test_shutdown_disposes_an_evicted_engine_still_waiting(tmp_path):
    dbs = databases(tmp_path)
    first = dbs._engine_for("project_a")
    pool = first.pool
    connection = first.connect()
    dbs._engine_for("project_b")
    connection.close()
    dbs.dispose()
    assert first.pool is not pool
