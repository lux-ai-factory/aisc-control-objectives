"""Test helpers for the isolation (one database per project), 2026-09-25.

Stage 2 of docs/superpowers/isolation-2026-09-25 (02-tests.md). Nothing here is
product code: it makes a throwaway cluster look like the platform, with real
project databases made by the platform's own template runner
(`platform_service.projectdb.provision`), and builds the service the way it is
deployed (`server.build_app()`, from the environment).

The cluster is the one `CONTROL_OBJECTIVES_TEST_DATABASE_URL` points at, which
must be a throwaway: the fixtures refuse port 5432 (where the running stack's
Postgres lives) and a URL that was not set explicitly. They write
`core.project` / `core.project_member` rows into that cluster's `platform`
database (made by init/platform-db.sql), and make and drop `project_<hex>`
databases in it.

Until the platform template has 0006_project_system.sql and
0008_control_objectives.sql (WP P1), `stand_in_for_missing_template` makes
exactly what I1.5 / I2.1 say those files make, so that the control-objectives
tests fail on control-objectives, not on P1. Once the real files exist the
template runner has made them first and the stand-in does nothing.
"""

from __future__ import annotations

import os
import sys
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

import jwt
import psycopg
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

HERE = Path(__file__).resolve().parent
PLATFORM_DIR = HERE.parents[2] / "platform"
ISSUER = "http://keycloak:8080/realms/aisc"
ROOT = "/control-objectives"

#: I1.8: the example pid of the spec, and its database, in every language's tests.
EXAMPLE_PID = "3f2b8c1e-0d4a-4e7b-9a55-1c2d3e4f5a6b"
EXAMPLE_DB = "project_3f2b8c1e0d4a4e7b9a551c2d3e4f5a6b"

#: I2.6: what report_ro and dashboard_ro may SELECT in control_objectives.
READER_TABLES = ("project", "graph", "risk", "mapped_objective", "mapping_run")
READERS = ("report_ro", "dashboard_ro")


def platform_projectdb():
    """The platform's own database-name rule and template runner."""
    if str(PLATFORM_DIR) not in sys.path:
        sys.path.insert(0, str(PLATFORM_DIR))
    from platform_service import projectdb

    return projectdb


class Cluster:
    """A throwaway Postgres made like the platform's (init/*.sql applied)."""

    def __init__(self, test_url: str):
        parts = urlsplit(test_url.replace("postgresql+psycopg://", "postgresql://", 1))
        self.host = parts.hostname or "127.0.0.1"
        self.port = parts.port or 5432
        self.user = parts.username
        self.password = parts.password

    def dsn(self, database: str, user: str | None = None, password: str | None = None) -> str:
        user = user or self.user
        password = password if password is not None else (
            self.password if user == self.user else user  # dev roles: password = name
        )
        return f"postgresql://{user}:{password}@{self.host}:{self.port}/{database}"

    def sa_url(self, database: str, user: str | None = None) -> str:
        return self.dsn(database, user).replace("postgresql://", "postgresql+psycopg://", 1)

    @contextmanager
    def connect(self, database: str = "platform", autocommit: bool = True):
        with psycopg.connect(self.dsn(database), autocommit=autocommit) as conn:
            yield conn

    def database_exists(self, name: str) -> bool:
        with self.connect() as conn:
            return conn.execute(
                "SELECT 1 FROM pg_database WHERE datname = %s", (name,)
            ).fetchone() is not None

    def sessions(self, role: str) -> dict[str, int]:
        """datname -> open sessions of `role` (the snapshot is refreshed first)."""
        with self.connect() as conn:
            conn.execute("SELECT pg_stat_clear_snapshot()")
            rows = conn.execute(
                "SELECT datname, count(*) FROM pg_stat_activity"
                " WHERE usename = %s AND datname IS NOT NULL GROUP BY datname",
                (role,),
            ).fetchall()
        return {name: count for name, count in rows}


def cluster_or_skip() -> Cluster:
    url = os.environ.get("CONTROL_OBJECTIVES_TEST_DATABASE_URL")
    if not url:
        pytest.skip("isolation tests need a throwaway cluster: set CONTROL_OBJECTIVES_TEST_DATABASE_URL")
    cluster = Cluster(url)
    if cluster.port == 5432:
        pytest.skip("isolation tests refuse port 5432 (the running stack); use a throwaway port")
    try:
        with cluster.connect() as conn:
            conn.execute("SELECT to_regclass('core.project')").fetchone()
    except psycopg.Error as exc:  # pragma: no cover - environment
        pytest.skip(f"the throwaway cluster has no platform database with core: {exc}")
    # core.project_member and the rest come from the platform's own migrations,
    # run as platform_rw exactly as the platform service does at its first query
    platform_projectdb()
    from platform_service.migrate import migrate

    with psycopg.connect(cluster.dsn("platform", "platform_rw")) as conn:
        migrate(conn)
    return cluster


def stand_in_for_missing_template(cluster: Cluster, database: str) -> None:
    """What 0006_project_system.sql and 0008_control_objectives.sql make, only
    when the template runner did not already (see the module docstring)."""
    with cluster.connect(database) as conn:
        if conn.execute("SELECT to_regclass('project.system') IS NULL").fetchone()[0]:
            conn.execute(
                """
                CREATE SCHEMA IF NOT EXISTS project;
                CREATE TABLE project.system (
                    pid uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                    number integer NOT NULL CHECK (number > 0) UNIQUE,
                    name text NOT NULL, version text, provider text, description text,
                    created_at timestamptz NOT NULL DEFAULT now(),
                    updated_at timestamptz NOT NULL DEFAULT now(),
                    created_by text
                );
                GRANT USAGE ON SCHEMA project TO control_objectives_rw;
                GRANT SELECT, REFERENCES ON project.system TO control_objectives_rw;
                """
            )
        if conn.execute("SELECT to_regnamespace('control_objectives') IS NULL").fetchone()[0]:
            conn.execute(
                f"""
                GRANT CONNECT ON DATABASE "{database}" TO control_objectives_rw;
                CREATE SCHEMA IF NOT EXISTS control_objectives;
                GRANT USAGE, CREATE ON SCHEMA control_objectives TO control_objectives_rw;
                ALTER ROLE control_objectives_rw IN DATABASE "{database}"
                    SET search_path = control_objectives;
                """
            )
            for reader in READERS:
                exists = conn.execute(
                    "SELECT 1 FROM pg_roles WHERE rolname = %s", (reader,)
                ).fetchone()
                if exists:
                    conn.execute(f'GRANT USAGE ON SCHEMA control_objectives TO "{reader}"')


class Projects:
    """Platform projects in the throwaway `platform`, each with its database."""

    def __init__(self, cluster: Cluster):
        self.cluster = cluster
        self.made: list[str] = []

    def make(self, slug: str | None = None, members: dict[str, str] | None = None) -> str:
        pid = str(uuid.uuid4())
        slug = slug or f"iso-{pid[:8]}"
        with self.cluster.connect() as conn:
            conn.execute(
                "INSERT INTO core.project (pid, name, slug) VALUES (%s, %s, %s)",
                (pid, slug, slug),
            )
            for subject, role in (members or {}).items():
                conn.execute(
                    "INSERT INTO core.project_member (project_id, subject, role)"
                    " VALUES (%s, %s, %s)",
                    (pid, subject, role),
                )
        self.made.append(pid)
        name = platform_projectdb().provision(self.cluster.dsn("platform"), pid)
        stand_in_for_missing_template(self.cluster, name)
        return pid

    def database(self, pid: str) -> str:
        return platform_projectdb().database_name(pid)

    def add_version(self, pid: str, number: int) -> str:
        """A saved card version in the project's own project.system (the
        platform is its writer; the test plays the platform)."""
        system = str(uuid.uuid4())
        with self.cluster.connect(self.database(pid)) as conn:
            conn.execute(
                "INSERT INTO project.system (pid, number, name) VALUES (%s, %s, %s)",
                (system, number, "MCAS"),
            )
        return system

    def slug(self, pid: str) -> str:
        with self.cluster.connect() as conn:
            return conn.execute("SELECT slug FROM core.project WHERE pid = %s", (pid,)).fetchone()[0]

    def cleanup(self) -> None:
        projectdb = platform_projectdb()
        for pid in self.made:
            projectdb.drop(self.cluster.dsn("platform"), pid)
            with self.cluster.connect() as conn:
                conn.execute("DELETE FROM core.project WHERE pid = %s", (pid,))


def signer(monkeypatch):
    """Tokens the service accepts, signed by a stand-in for Keycloak's key."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("KEYCLOAK_ISSUER", ISSUER)
    monkeypatch.setenv("KEYCLOAK_JWKS_URL", "http://keycloak:8080/unused-in-tests")
    monkeypatch.setattr(
        "aisc_identity.service.key_for_jwks", lambda url: (lambda _t: key.public_key())
    )

    def token(subject: str, roles=("primary-user",)) -> dict[str, str]:
        encoded = jwt.encode(
            {
                "sub": subject,
                "preferred_username": subject,
                "iss": ISSUER,
                "exp": int(time.time()) + 300,
                "realm_access": {"roles": list(roles)},
            },
            key,
            algorithm="RS256",
        )
        return {"Authorization": f"Bearer {encoded}"}

    return token


class FakeMapper:
    """Stands in for the model: maps every risk to nothing, never calls out."""

    def __init__(self, *args, **kwargs):
        pass

    def propose(self, risk, findings=()):
        from aisc_control_objectives.risk_mapping import Mapping

        return Mapping(risk_id=risk.id)


def deployed_app(monkeypatch, cluster: Cluster, *, root_path: str = "",
                 platform_url: str | None = None, seen_llm_projects: list | None = None):
    """The service as composed by `server.build_app()`, pointed at the cluster.

    Env (the isolation contract, 02-tests.md decisions): `DATABASE_URL` is the
    `platform` database (membership only, I5.1); `PROJECT_DATABASE_URL` is the
    template for a project database, `{database}` replaced by `project_<hex>`.
    No model is ever reached: the mapper is a fake and the ollama URL is a
    closed port.
    """
    from aisc_control_objectives import baf_llm, server

    monkeypatch.setenv("DATABASE_URL", platform_url or cluster.sa_url("platform", "control_objectives_rw"))
    monkeypatch.setenv(
        "PROJECT_DATABASE_URL",
        cluster.sa_url("{database}", "control_objectives_rw"),
    )
    monkeypatch.setenv("BAF_LLM_PROVIDER", "ollama")
    monkeypatch.setenv("BAF_LLM_MODEL", "mistral:latest")
    monkeypatch.setenv("BAF_LLM_BASE_URL", "http://127.0.0.1:1")
    monkeypatch.delenv("PLATFORM_INTERNAL_TOKEN", raising=False)
    monkeypatch.setenv("CONTROL_OBJECTIVES_ROOT_PATH", root_path)
    monkeypatch.setattr(server, "_build_completer", lambda config: (lambda *a, **k: ""))
    monkeypatch.setattr(server, "RiskMapper", FakeMapper, raising=False)
    monkeypatch.setattr(baf_llm, "build_llm", lambda *a, **k: None)
    monkeypatch.setattr(baf_llm, "completer", lambda llm: (lambda *a, **k: ""))
    real_config_for = baf_llm.config_for

    def config_for(project, system, env=None, fallback=None):
        if seen_llm_projects is not None:
            seen_llm_projects.append(str(project))
        return fallback or real_config_for(project, system, env=env, fallback=fallback)

    monkeypatch.setattr(baf_llm, "config_for", config_for)
    return server.build_app()


def fake_upstream(monkeypatch, projects: Projects, fixtures_dir: Path) -> dict[str, str]:
    """The platform's latest version and qualification's card, answered locally.

    Returns project pid -> the pid of the version the next start will use; set
    it with `projects.add_version` first. `project` may be the pid or the slug.
    """
    from aisc_control_objectives import upstream

    latest: dict[str, str] = {}
    jsonld = (fixtures_dir / "mcas.ontology.jsonld").read_text()

    def pid_of(project: str) -> str:
        if project in latest:
            return project
        for pid in latest:
            if projects.slug(pid) == project:
                return pid
        raise AssertionError(f"no latest version set for {project}")

    def latest_version(project, authorization):
        pid = pid_of(project)
        return {"pid": latest[pid], "project_id": pid, "number": 1}

    def card_jsonld(project, system_pid, authorization):
        return jsonld

    monkeypatch.setattr(upstream, "latest_version", latest_version)
    monkeypatch.setattr(upstream, "card_jsonld", card_jsonld)
    return latest


def start_assessment(client, project: str, headers: dict[str, str]) -> str:
    """Start an assessment through the page, as the button does; its id."""
    response = client.post(f"/p/{project}/projects", data={"name": "MCAS"}, headers=headers)
    assert response.status_code == 303, (response.status_code, response.text[:300])
    return response.headers["location"].rstrip("/").rsplit("/", 1)[1]
