"""Isolation (one database per project): control objectives, against real
project databases.

Stage 2 of docs/superpowers/isolation-2026-09-25 (01-specs.md sections 5, 17,
18). Two or more platform projects are made in a throwaway cluster, each with
its own `project_<hex>` database made by the platform's template runner, and
the service is built as deployed (`server.build_app()`, env `DATABASE_URL` on
`platform` for membership and `PROJECT_DATABASE_URL` with `{database}`).

Section 3 below mirrors, at the `/p/{pid}/api` paths, every case of
tests/test_api_auth.py whose route moves (I18.2); test_api_auth.py itself is
changed only in its paths by WP O1.
"""

from __future__ import annotations

import threading
import time
import uuid

import pytest
from fastapi.testclient import TestClient

from isolation_support import (
    READER_TABLES,
    READERS,
    ROOT,
    Projects,
    cluster_or_skip,
    deployed_app,
    fake_upstream,
    signer,
    start_assessment,
)

EDITOR, VIEWER, BOTH, STRANGER = "iso-editor", "iso-viewer", "iso-both", "iso-stranger"


@pytest.fixture(scope="module")
def cluster():
    return cluster_or_skip()


@pytest.fixture()
def projects(cluster):
    made = Projects(cluster)
    yield made
    made.cleanup()


@pytest.fixture()
def token(monkeypatch):
    return signer(monkeypatch)


@pytest.fixture()
def seen_llm_projects():
    return []


@pytest.fixture()
def app(monkeypatch, cluster, seen_llm_projects):
    return deployed_app(monkeypatch, cluster, seen_llm_projects=seen_llm_projects)


@pytest.fixture()
def client(app):
    return TestClient(app, raise_server_exceptions=False, follow_redirects=False)


@pytest.fixture()
def rooted(monkeypatch, cluster):
    app = deployed_app(monkeypatch, cluster, root_path=ROOT)
    return TestClient(app, raise_server_exceptions=False, follow_redirects=False)


@pytest.fixture()
def latest(monkeypatch, projects, fixtures_dir):
    return fake_upstream(monkeypatch, projects, fixtures_dir)


@pytest.fixture()
def two(projects, latest, client, token):
    """Projects A and B, each with version 1 and one assessment of it.

    BOTH is an editor of A and of B, so a 404 across them is the database's
    doing, not the membership's."""
    a = projects.make(members={EDITOR: "editor", VIEWER: "viewer", BOTH: "editor"})
    b = projects.make(members={BOTH: "editor"})
    ids = {}
    for pid in (a, b):
        latest[pid] = projects.add_version(pid, 1)
        ids[pid] = start_assessment(client, pid, token(BOTH))
    return a, b, ids[a], ids[b]


def _rows(cluster, database, sql, params=()):
    with cluster.connect(database) as conn:
        return conn.execute(sql, params).fetchall()


def _count(cluster, database, table, where="", params=()):
    return _rows(cluster, database, f"SELECT count(*) FROM {table} {where}", params)[0][0]


def _api(pid, aid=None, tail=""):
    return f"/p/{pid}/api/projects" + (f"/{aid}" if aid else "") + tail


# ── 1. placement: an assessment lives in its project's database ────────────


def test_I1_1_I5_1_an_assessment_is_written_into_its_projects_database(cluster, projects, two):
    a, b, aid, bid = two
    da, db = projects.database(a), projects.database(b)
    assert _count(cluster, da, "control_objectives.project", "WHERE id = %s", (aid,)) == 1
    assert _count(cluster, da, "control_objectives.project", "WHERE id = %s", (bid,)) == 0
    assert _count(cluster, db, "control_objectives.project", "WHERE id = %s", (bid,)) == 1
    assert _count(cluster, db, "control_objectives.risk", "WHERE project_id = %s", (aid,)) == 0
    # and nothing of it in the shared database
    with cluster.connect() as conn:
        shared = conn.execute("SELECT to_regclass('control_objectives.project')").fetchone()[0]
        if shared is not None:
            assert conn.execute(
                "SELECT count(*) FROM control_objectives.project WHERE id IN (%s, %s)", (aid, bid)
            ).fetchone()[0] == 0


def test_I5_3_I1_6_I1_7_the_assessment_table_in_a_project_database(cluster, projects, two):
    a, _, _, _ = two
    database = projects.database(a)
    columns = {r[0] for r in _rows(
        cluster, database,
        "SELECT column_name FROM information_schema.columns"
        " WHERE table_schema = 'control_objectives' AND table_name = 'project'",
    )}
    assert "system_id" in columns and "project_id" not in columns
    keys = _rows(
        cluster, database,
        "SELECT confrelid::regclass::text, confdeltype FROM pg_constraint"
        " WHERE conrelid = 'control_objectives.project'::regclass AND contype = 'f'",
    )
    # its card version (cascading), and since 2026-10-01 the profile version it runs on (restricted)
    assert sorted(keys) == [("control_objectives.objective_profile_version", "r"), ("project.system", "c")], keys
    unique = _rows(
        cluster, database,
        "SELECT count(*) FROM pg_index i JOIN pg_attribute a"
        "  ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)"
        " WHERE i.indrelid = 'control_objectives.project'::regclass AND i.indisunique"
        "   AND i.indnatts = 1 AND a.attname = 'system_id'",
    )[0][0]
    assert unique == 1


def test_I5_4_the_database_is_at_the_baseline_and_readers_get_their_reads(cluster, projects, two):
    import re
    from pathlib import Path

    a, _, _, _ = two
    database = projects.database(a)
    # the head: the risk and control matrix (2026-10-01), on the earlier revisions and the baseline
    baseline = (Path(__file__).resolve().parents[1] / "alembic" / "versions"
                / "20261002100000_rcm.py").read_text()
    revision = re.search(r"^revision\s*=\s*['\"]([^'\"]+)", baseline, re.M).group(1)
    assert _rows(cluster, database, "SELECT version_num FROM control_objectives.alembic_version") == [
        (revision,)
    ]
    for reader in READERS:
        if not _rows(cluster, database, "SELECT 1 FROM pg_roles WHERE rolname = %s", (reader,)):
            continue
        for table in READER_TABLES:
            assert _rows(
                cluster, database, "SELECT has_table_privilege(%s, %s, 'SELECT')",
                (reader, f"control_objectives.{table}"),
            ) == [(True,)], (reader, table)
        assert _rows(
            cluster, database, "SELECT has_table_privilege(%s, %s, 'SELECT')",
            (reader, "control_objectives.alembic_version"),
        ) == [(False,)], reader


# ── 2. cross-project: every route that takes an id, under the other pid ───


def _id_routes(pid, aid, risk="r1"):
    """Every route that addresses an assessment by id (route inventory,
    02-tests.md; the risk and control matrix, 2026-10-01): the pages and forms and the JSON API."""
    return [
        ("GET", f"/p/{pid}/projects/{aid}", {}),
        ("POST", f"/p/{pid}/projects/{aid}/map", {}),
        ("POST", f"/p/{pid}/projects/{aid}/severity", {"data": {f"impact:{risk}": "3"}}),
        ("POST", f"/p/{pid}/projects/{aid}/key", {"data": {"key": "O1"}}),
        ("GET", _api(pid, aid), {}),
        ("POST", _api(pid, aid, "/map"), {}),
        ("POST", _api(pid, aid, "/severity"), {"json": {risk: 3}}),
        ("POST", _api(pid, aid, "/ratings"), {"json": {risk: {"impact": 3, "likelihood": 2}}}),
        ("POST", _api(pid, aid, "/key"), {"json": {"O1": True}}),
        ("DELETE", _api(pid, aid), {}),
    ]


def _risk_of(client, token, pid, aid):
    response = client.get(_api(pid, aid), headers=token(BOTH))
    assert response.status_code == 200, response.text[:300]
    return response.json()["risks"][0]["id"]


def test_I16_5_I5_2_an_assessment_of_A_under_Bs_pid_is_404_on_every_route(
    cluster, projects, two, client, token
):
    a, b, aid, _ = two
    risk = _risk_of(client, token, a, aid)
    before = _rows(cluster, projects.database(a),
                   "SELECT id, rating_impact, rating_likelihood FROM control_objectives.risk ORDER BY id")
    for method, path, kwargs in _id_routes(b, aid, risk):
        response = client.request(method, path, headers=token(BOTH), **kwargs)
        assert response.status_code == 404, (method, path, response.status_code)
        assert "MCAS" not in response.text, path
    # nothing of A changed, and it is still there
    assert _rows(cluster, projects.database(a),
                 "SELECT id, rating_impact, rating_likelihood FROM control_objectives.risk ORDER BY id") == before
    assert _count(cluster, projects.database(a), "control_objectives.mapping_run") == 0
    assert _count(cluster, projects.database(a), "control_objectives.objective_selection") == 0
    assert client.get(_api(a, aid), headers=token(BOTH)).status_code == 200


def test_I16_5_the_same_by_slug_and_for_an_admin(cluster, projects, two, client, token):
    a, b, aid, _ = two
    admin = token("iso-admin", ("admin",))
    for ref in (b, projects.slug(b)):
        for method, path, kwargs in _id_routes(ref, aid):
            for headers in (token(BOTH), admin):
                response = client.request(method, path, headers=headers, **kwargs)
                assert response.status_code == 404, (method, path, response.status_code)


def test_I16_5_a_list_shows_only_its_own_projects_assessments(two, client, token, projects):
    a, b, aid, bid = two
    for ref, mine, theirs in ((a, aid, bid), (b, bid, aid), (projects.slug(a), aid, bid)):
        listed = client.get(_api(ref), headers=token(BOTH))
        assert listed.status_code == 200, listed.text[:300]
        assert [item["id"] for item in listed.json()] == [mine]
        page = client.get(f"/p/{ref}/projects", headers=token(BOTH))
        assert page.status_code == 200
        assert theirs not in page.text


def test_I5_2_the_old_api_projects_paths_are_404(two, client, token):
    a, _, aid, _ = two
    for headers in (token(BOTH), token("iso-admin", ("admin",))):
        for method, path, kwargs in (
            ("GET", f"/api/projects?project={a}", {}),
            ("GET", f"/api/projects/{aid}", {}),
            ("POST", f"/api/projects/{aid}/map", {}),
            ("POST", f"/api/projects/{aid}/severity", {"json": {}}),
            ("DELETE", f"/api/projects/{aid}", {}),
        ):
            response = client.request(method, path, headers=headers, **kwargs)
            assert response.status_code == 404, (method, path, response.status_code)
    assert client.get(_api(a, aid), headers=token(BOTH)).status_code == 200


def test_I5_2_a_slug_opens_the_same_database_as_the_pid(two, client, token, projects):
    a, _, aid, _ = two
    slug = projects.slug(a)
    assert client.get(_api(slug, aid), headers=token(BOTH)).json()["id"] == aid
    assert client.get(f"/p/{slug}/projects/{aid}", headers=token(BOTH)).status_code == 200


def test_I5_2_I5_1_a_project_that_does_not_exist_is_404_without_a_database(
    cluster, client, token
):
    ghost = str(uuid.uuid4())
    admin = token("iso-admin", ("admin",))
    for path in (_api(ghost), _api(ghost, "abcdef012345"), f"/p/{ghost}/projects"):
        assert client.get(path, headers=admin).status_code == 404, path
    assert not cluster.database_exists("project_" + ghost.replace("-", ""))


# ── 3. I18.2: every test_api_auth.py case whose route moved ────────────────


def _gated(pid, aid):
    return [
        ("GET", _api(pid)),
        ("GET", _api(pid, aid)),
        ("POST", _api(pid, aid, "/map")),
        ("POST", _api(pid, aid, "/severity")),
        ("DELETE", _api(pid, aid)),
        ("GET", f"/p/{pid}/projects/{aid}"),
        ("POST", f"/p/{pid}/projects/{aid}/map"),
        ("POST", f"/p/{pid}/projects/{aid}/severity"),
    ]


def test_I18_2_every_moved_route_is_401_without_a_token(two, client, rooted, cluster, projects):
    """test_api_auth: test_every_route_but_the_public_ones_is_401_without_a_token,
    test_behind_the_root_path_it_is_the_same."""
    a, _, aid, _ = two
    for method, path in _gated(a, aid):
        kwargs = {"json": {}} if path.endswith("/severity") else {}
        assert client.request(method, path, **kwargs).status_code == 401, (method, path)
        for variant in (path, ROOT + path):
            assert rooted.request(method, variant, **kwargs).status_code == 401, (method, variant)
    assert _count(cluster, projects.database(a), "control_objectives.project") == 1


def _bypass(pid, aid):
    return [
        f"{ROOT}/p/{pid}/api/projects/{aid}",
        f"{ROOT}{ROOT}/p/{pid}/api/projects/{aid}",
        f"/p/{pid}/api/projects/{aid}/",
        f"/%70/{pid}/api/projects/{aid}",
        f"/p%2F{pid}/api/projects/{aid}",
        f"/p/{pid}%2Fapi/projects/{aid}",
        f"//p/{pid}/api/projects/{aid}",
        f"/p/{pid}/api//projects/{aid}",
        f"/p/{pid}/api/projects%2F{aid}",
        f"/control%2Dobjectives/p/{pid}/api/projects/{aid}",
    ]


def test_I18_2_no_variant_of_a_moved_path_answers_without_a_token(two, rooted):
    """test_api_auth: test_no_variant_of_a_gated_path_answers_without_a_token."""
    a, _, aid, _ = two
    for path in _bypass(a, aid):
        assert rooted.get(path).status_code == 401, path


def test_I18_2_no_variant_shows_a_stranger_the_assessment(two, rooted, token):
    """test_api_auth: test_no_variant_shows_a_stranger_the_assessment."""
    a, _, aid, _ = two
    headers = token(STRANGER)
    for path in _bypass(a, aid):
        response = rooted.get(path, headers=headers)
        assert response.status_code in (404, 307), (path, response.status_code)
        assert "MCAS" not in response.text, path
        if response.status_code == 307:
            follow = rooted.get(response.headers["location"], headers=headers)
            assert follow.status_code == 404, (path, response.headers["location"])


def test_I18_2_a_viewer_reads_through_the_api(two, client, token):
    """test_api_auth: test_a_viewer_reads_through_the_api, test_the_gateway_header_is_a_token_too."""
    a, _, aid, _ = two
    listed = client.get(_api(a), headers=token(VIEWER))
    assert listed.status_code == 200 and [i["id"] for i in listed.json()] == [aid]
    assert client.get(_api(a, aid), headers=token(VIEWER)).status_code == 200
    gateway = {"X-Auth-Request-Access-Token": token(VIEWER)["Authorization"].split()[1]}
    assert client.get(_api(a, aid), headers=gateway).status_code == 200


def test_I18_2_I18_3_a_viewer_cannot_change_anything(cluster, projects, two, client, token):
    """test_api_auth: test_a_viewer_cannot_change_anything_through_the_api."""
    a, _, aid, _ = two
    risk = _risk_of(client, token, a, aid)
    headers = token(VIEWER)
    assert client.post(_api(a, aid, "/map"), headers=headers).status_code == 403
    assert client.post(_api(a, aid, "/severity"), headers=headers, json={risk: 3}).status_code == 403
    assert client.delete(_api(a, aid), headers=headers).status_code == 403
    assert _count(cluster, projects.database(a), "control_objectives.project") == 1
    assert _count(cluster, projects.database(a), "control_objectives.mapping_run") == 0


def test_I18_2_an_editor_may(cluster, projects, two, client, token):
    """test_api_auth: test_an_editor_may."""
    a, _, aid, _ = two
    risk = _risk_of(client, token, a, aid)
    headers = token(EDITOR)
    assert client.post(_api(a, aid, "/map"), headers=headers).status_code == 200
    rated = client.post(_api(a, aid, "/severity"), headers=headers, json={risk: 3})
    assert rated.status_code == 200
    assert client.delete(_api(a, aid), headers=headers).status_code == 204
    assert _count(cluster, projects.database(a), "control_objectives.project") == 0


def test_I18_2_an_admin_may_without_being_a_member(two, client, token):
    """test_api_auth: test_an_admin_may_without_being_a_member."""
    a, _, aid, _ = two
    admin = token("iso-admin", ("admin",))
    assert client.get(_api(a), headers=admin).status_code == 200
    assert client.get(_api(a, aid), headers=admin).status_code == 200
    assert client.post(_api(a, aid, "/map"), headers=admin).status_code == 200


def test_I18_2_a_stranger_is_told_nothing_exists(cluster, projects, two, client, token):
    """test_api_auth: test_a_stranger_is_told_nothing_exists, test_an_unknown_project_is_404."""
    a, _, aid, _ = two
    headers = token(STRANGER)
    for method, path in _gated(a, aid)[:5]:
        kwargs = {"json": {"risk0": 3}} if path.endswith("/severity") else {}
        response = client.request(method, path, headers=headers, **kwargs)
        assert response.status_code == 404, (method, path, response.status_code)
    assert client.get(_api(str(uuid.uuid4())), headers=headers).status_code == 404
    # a member asking for an id that is not in the database gets the answer
    # a stranger gets for one that is: no oracle
    unknown = client.get(_api(a, "000000000000"), headers=token(EDITOR))
    assert unknown.status_code == 404
    assert _count(cluster, projects.database(a), "control_objectives.project") == 1


def test_I18_2_a_member_of_one_project_cannot_reach_anothers_by_id(two, client, token):
    """test_api_auth: test_a_member_of_one_project_cannot_reach_anothers_assessment_by_id,
    now with the id under the caller's own pid (EDITOR is in A only; B's id)."""
    a, b, _, bid = two
    headers = token(EDITOR)
    assert client.get(_api(a, bid), headers=headers).status_code == 404
    assert client.post(_api(a, bid, "/map"), headers=headers).status_code == 404
    assert client.delete(_api(a, bid), headers=headers).status_code == 404
    assert client.get(_api(b, bid), headers=headers).status_code == 404
    assert client.get(_api(b), headers=headers).status_code == 404


def test_I18_2_pages_open_by_pid_or_slug_and_behind_the_root_path(two, client, rooted, token, projects):
    """test_api_auth: test_a_page_opens_its_own_projects_assessment_by_pid_or_slug,
    test_a_page_opens_by_slug_behind_the_root_path."""
    a, _, aid, _ = two
    slug = projects.slug(a)
    for ref in (a, slug):
        assert client.get(f"/p/{ref}/projects/{aid}", headers=token(VIEWER)).status_code == 200
    assert rooted.get(f"{ROOT}/p/{slug}/projects/{aid}", headers=token(VIEWER)).status_code == 200


# ── 4. I5.3 numbers, I5.6 the mapper's project ─────────────────────────────


def test_I5_3_a_newer_version_in_project_system_makes_it_read_only(projects, two, client, token):
    a, _, aid, _ = two
    projects.add_version(a, 2)
    body = client.get(_api(a, aid), headers=token(EDITOR)).json()
    assert (body["version_number"], body["latest_number"]) == (1, 2)
    risk = body["risks"][0]["id"]
    assert client.post(_api(a, aid, "/severity"), headers=token(EDITOR),
                       json={risk: 3}).status_code == 409
    assert client.post(_api(a, aid, "/map"), headers=token(EDITOR)).status_code == 409


def test_I5_3_a_start_with_a_version_not_in_project_system_stores_nothing(
    cluster, projects, latest, client, token
):
    a = projects.make(members={EDITOR: "editor"})
    latest[a] = str(uuid.uuid4())  # the platform names a version this database lacks
    response = client.post(f"/p/{a}/projects", data={"name": "x"}, headers=token(EDITOR))
    # a 4xx that says so (decision, 02-tests.md: 409), never an unhandled 500
    assert 400 <= response.status_code < 500, (response.status_code, response.text[:200])
    table = _rows(cluster, projects.database(a), "SELECT to_regclass('control_objectives.project')")[0][0]
    if table is not None:
        assert _count(cluster, projects.database(a), "control_objectives.project") == 0


def test_I5_6_the_mapper_resolves_its_llm_with_the_databases_pid(
    two, client, token, seen_llm_projects
):
    a, b, aid, bid = two
    assert client.post(_api(a, aid, "/map"), headers=token(BOTH)).status_code == 200
    assert client.post(_api(b, bid, "/map"), headers=token(BOTH)).status_code == 200
    assert seen_llm_projects == [a, b]


# ── 5. I5.1 membership before the database; I5.5 first open; I17.1 pools ───


def test_I5_1_a_stranger_or_a_viewers_write_never_opens_the_database(
    cluster, projects, client, token
):
    a = projects.make(members={VIEWER: "viewer"})
    database = projects.database(a)
    assert client.get(_api(a), headers=token(STRANGER)).status_code == 404
    assert client.get(f"/p/{a}/projects", headers=token(STRANGER)).status_code == 404
    assert client.post(_api(a, "abcdef012345", "/map"), headers=token(VIEWER)).status_code == 403
    assert client.delete(_api(a, "abcdef012345"), headers=token(VIEWER)).status_code == 403
    assert cluster.sessions("control_objectives_rw").get(database, 0) == 0
    assert _rows(cluster, database, "SELECT to_regclass('control_objectives.alembic_version')") == [
        (None,)
    ]


def test_I5_1_membership_unknown_is_503_and_opens_nothing(monkeypatch, cluster, projects, token):
    a = projects.make(members={EDITOR: "editor"})
    app = deployed_app(monkeypatch, cluster,
                       platform_url="postgresql+psycopg://x:y@127.0.0.1:1/platform")
    client = TestClient(app, raise_server_exceptions=False, follow_redirects=False)
    assert client.get(_api(a), headers=token(EDITOR)).status_code == 503
    assert cluster.sessions("control_objectives_rw").get(projects.database(a), 0) == 0


def test_I5_5_concurrent_first_requests_share_one_migration(cluster, projects, client, token):
    a = projects.make(members={EDITOR: "editor"})
    statuses: list[int] = []
    barrier = threading.Barrier(6)

    def hit():
        barrier.wait()
        statuses.append(client.get(_api(a), headers=token(EDITOR)).status_code)

    threads = [threading.Thread(target=hit) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert statuses == [200] * 6
    assert _count(cluster, projects.database(a), "control_objectives.alembic_version") == 1


def _settled_sessions(cluster, role, predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while True:
        seen = cluster.sessions(role)
        if predicate(seen) or time.monotonic() > deadline:
            return seen
        time.sleep(0.2)


def test_I17_1_at_most_two_connections_per_project_and_two_to_platform(
    cluster, projects, client, token
):
    a = projects.make(members={EDITOR: "editor"})
    assert client.get(_api(a), headers=token(EDITOR)).status_code == 200
    barrier = threading.Barrier(8)
    statuses: list[int] = []

    def hit():
        barrier.wait()
        statuses.append(client.get(_api(a), headers=token(EDITOR)).status_code)

    threads = [threading.Thread(target=hit) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert statuses == [200] * 8
    seen = cluster.sessions("control_objectives_rw")
    assert seen.get(projects.database(a), 0) <= 2, seen
    assert seen.get("platform", 0) <= 2, seen


def test_I17_1_at_most_twenty_project_databases_are_held_open(cluster, projects, client, token):
    admin = token("iso-admin", ("admin",))
    pids = [projects.make() for _ in range(21)]
    for pid in pids:
        assert client.get(_api(pid), headers=admin).status_code == 200, pid
    names = {projects.database(p) for p in pids}
    seen = _settled_sessions(
        cluster, "control_objectives_rw",
        lambda s: len(names & set(s)) <= 20 and s.get(projects.database(pids[0]), 0) == 0,
    )
    held = names & set(seen)
    assert len(held) <= 20, sorted(held)
    assert seen.get(projects.database(pids[0]), 0) == 0, "the least recently used was not closed"


def test_I2_5_I17_1_a_dropped_database_is_evicted_and_answers_404(cluster, projects, client, token):
    a = projects.make(members={EDITOR: "editor"})
    b = projects.make(members={EDITOR: "editor"})
    assert client.get(_api(a), headers=token(EDITOR)).status_code == 200
    assert client.get(_api(b), headers=token(EDITOR)).status_code == 200
    # the platform's delete order: unregister, DROP DATABASE ... WITH (FORCE),
    # then the core.project row; a request can arrive in between
    from isolation_support import platform_projectdb

    platform_projectdb().drop(cluster.dsn("platform"), a)
    for _ in range(3):
        response = client.get(_api(a), headers=token(EDITOR))
        assert response.status_code == 404, (response.status_code, response.text[:200])
    assert client.get(_api(b), headers=token(EDITOR)).status_code == 200
    assert not cluster.database_exists(projects.database(a)), "a request re-created it"


# ── 6. I5.5 the migrate one-shot ────────────────────────────────────────────


def test_I5_5_migrate_projects_upgrades_what_it_may_enter_and_skips_the_rest(cluster, projects):
    import os
    import subprocess
    import sys

    a, b, shut = projects.make(), projects.make(), projects.make()
    with cluster.connect(projects.database(shut)) as conn:
        conn.execute(f'REVOKE CONNECT ON DATABASE "{projects.database(shut)}" FROM control_objectives_rw')
    env = dict(os.environ)
    env["DATABASE_URL"] = cluster.sa_url("platform", "control_objectives_rw")
    env["PROJECT_DATABASE_URL"] = cluster.sa_url("{database}", "control_objectives_rw")
    run = subprocess.run(
        [sys.executable, "-m", "aisc_control_objectives.migrate_projects"],
        env=env, capture_output=True, text=True, timeout=300,
    )
    assert run.returncode == 0, run.stdout + run.stderr
    for pid in (a, b):
        assert _count(cluster, projects.database(pid), "control_objectives.alembic_version") == 1
    assert _rows(cluster, projects.database(shut),
                 "SELECT to_regclass('control_objectives.alembic_version')") == [(None,)]
    # a second run changes nothing and still exits 0
    again = subprocess.run(
        [sys.executable, "-m", "aisc_control_objectives.migrate_projects"],
        env=env, capture_output=True, text=True, timeout=300,
    )
    assert again.returncode == 0, again.stdout + again.stderr
    # I18.7: it prints no credential
    assert "control_objectives_rw:control_objectives_rw" not in run.stdout + run.stderr


def test_I5_5_migrate_projects_exits_2_on_a_permanent_error(cluster, projects):
    import os
    import subprocess
    import sys

    broken = projects.make()
    with cluster.connect(projects.database(broken)) as conn:
        conn.execute("REVOKE CREATE ON SCHEMA control_objectives FROM control_objectives_rw")
    env = dict(os.environ)
    env["DATABASE_URL"] = cluster.sa_url("platform", "control_objectives_rw")
    env["PROJECT_DATABASE_URL"] = cluster.sa_url("{database}", "control_objectives_rw")
    run = subprocess.run(
        [sys.executable, "-m", "aisc_control_objectives.migrate_projects"],
        env=env, capture_output=True, text=True, timeout=300,
    )
    assert run.returncode == 2, (run.returncode, run.stdout + run.stderr)
