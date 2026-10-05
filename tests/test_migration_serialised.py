"""Alembic keeps its context and op proxies in module globals, so two project databases migrated at
once in two threads clobber each other (code review 2026-10-05). The per-database lock only kept two
opens of the same database apart; first opens of different databases now migrate one at a time."""
import threading
import time

from aisc_control_objectives.projectdb import ProjectDatabases

PIDS = ["3f2b8c1e-0d4a-4e7b-9a55-1c2d3e4f5a6b", "701ef4b8-057d-4a93-8b30-9b19052c881e",
        "a1b2c3d4-0000-4000-8000-000000000002"]


def test_first_opens_of_different_databases_migrate_one_at_a_time():
    running, most = 0, 0
    guard = threading.Lock()

    def migrate(_engine):
        nonlocal running, most
        with guard:
            running += 1
            most = max(most, running)
        time.sleep(0.05)
        with guard:
            running -= 1

    dbs = ProjectDatabases("postgresql+psycopg://u:p@db/platform", "postgresql+psycopg://u:p@db/{database}",
                           migrate=migrate)
    from aisc_control_objectives.projectdb import database_name
    threads = [threading.Thread(target=dbs._engine_for, args=(database_name(p),)) for p in PIDS]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    dbs.dispose()
    assert most == 1
