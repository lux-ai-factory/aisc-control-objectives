<div align="center">
  <img src="src/aisc_control_objectives/static/laif-logo.svg#gh-light-mode-only" alt="Luxembourg AI Factory" width="220">

  <h1>AISC Control Objectives</h1>

  <p><b>Step 2 of AISC: from an AI system's risks to the control objectives to work on first.</b></p>
</div>

## What it is

This is step 2, **control objectives**, of the AISC (AI Assessment Sandbox Configurator). AISC
assesses an AI system in six steps: 1 qualification, 2 control objectives, 3 install plugins and
tools, 4 execute tests and address controls, 5 analyse results on the dashboard, 6 compose the
report. This service takes the AI Card that step 1 (the qualification app) produced for one
version of the system, turns the risks on the card into a **risk register** that the assessor
rates (impact x likelihood), and builds a **risk and control matrix**: which of the 50 EU AI Act
control objectives mitigate each risk, mapped by a model, by a person, or both. From the ratings
it scores every objective and proposes up to seven **key objectives** to start with; the assessor
can change which are key. What the matrix holds is what step 4 works on.

A project can also write its own **objective sets** and pick **objective profiles** (a choice of
objectives from the built-in set and its own sets); an assessment runs on one profile.

## How it works

```mermaid
flowchart LR
    A["AI Card of one version<br/>(from qualification)"] --> B["Risk register<br/>impact x likelihood, by a person"]
    B --> C["Map risks to objectives<br/>(one model call per risk, or by hand)"]
    C --> D["Scores and key objectives<br/>(computed on every read)"]
    D --> E["Step 4 works on<br/>what the matrix holds"]
```

- **A FastAPI service** (`src/aisc_control_objectives/`, port 8090) that serves server-rendered
  pages and a JSON API. The catalogue of objectives is a CSV shipped in the package.
- **Who may do what.** Behind the gateway (Caddy, oauth2-proxy, Keycloak) every request carries a
  Keycloak token. The service verifies it (`shared/identity`, the `aisc_identity` package of the
  aisc repo) and reads project membership from the `platform` database (`core.project`,
  `core.project_member`). Only the catalogue (`/objectives`, `/api/control-objectives`,
  `/api/macro-requirements`, `/api/config`), `/static/*` and `/health` are public. Under
  `/p/{project}` a viewer may read and an editor may change.
- **One database per project.** A project's assessments, sets and profiles live in the schema
  `control_objectives` of that project's own Postgres database, `project_<pid without hyphens>`.
  The service connects to a project database only after membership is decided
  (`projectdb.ProjectDatabases.open`), and migrates it with Alembic the first time it opens it,
  one database at a time (Alembic's context is global to the process).
- **Starting an assessment** asks the platform (`PLATFORM_URL`) for the project's latest AI card
  version and qualification (`QUALIFICATION_URL`) for that version's card, with the caller's own
  token. One assessment per card version; an older version's assessment is read-only.
- **The mapping** (`risk_mapping.py`) asks the model, one risk at a time, which of the
  assessment's objectives (its profile's) mitigate it, each claim resting on a literal quote of
  the risk. Deterministic checks reject unknown ids, quotes that are not in the risk, missing
  quotes and repeats; what failed goes back to the model, at most three attempts (`rounds.py`).
  Every outcome is published with its findings, so a person can correct it on the page. A run is
  saved only over the assessment it read: if a newer card version, another profile or a person's
  mapping came meanwhile, it saves nothing and answers 409. The model is reached through
  [BAF](https://pypi.org/project/besser-agentic-framework/) (`llm.py`, `baf_llm.py`); the
  project's own model and key, chosen on the platform, take precedence over the service's own.
- **Scores** (`prioritising.py`, no model): an objective scores the sum of the ratings
  (impact x likelihood, 1 to 25) of the risks mapped to it. Ratings fall in the bands Low 1-4,
  Medium 5-9, High 10-16, Critical 17-25; an unrated part counts 3. By default the key objectives
  are the first seven driven by at least one High or Critical risk, never a voluntary one. Scores
  and default keys are never stored, so a changed rating can never leave a stale score.
- **The ledger.** With `LEDGER_MODE` set to `record` or `enforce`, every write also records an
  event through the project database's `ledger.emit`, in the same transaction (`ledger.py`). The
  platform relays those events to the immudb ledger. A mapping run in which some risks failed is
  `ai.mapping.completed` with what it saved and `failed_risks`; one in which every risk failed, or
  that saved nothing, is `ai.mapping.failed` with a code.

## Install and run

### Inside the AISC stack (the usual way)

The service is the `control-objectives` service of the aisc repo's
`docker-compose.development.yml`, with a one-shot `control-objectives-migrate` that brings every
project database to the head revision before it starts. It has no published port: Caddy serves it
at `/control-objectives` behind the gateway.

From the root of the aisc repo (the repo this one is a submodule of, at `apps/control-objectives`):

```bash
./scripts/secrets.sh     # once: writes env.secrets and env.runtime (the service needs PLATFORM_RISK_MAPPER_TOKEN)
docker compose -p aisc --env-file env.runtime -f docker-compose.plugin_downloader.yml \
  -f docker-compose-infra.development.yml -f docker-compose.development.yml up -d --build
```

Then open http://localhost:8100, sign in, open a project and choose step 2. To rebuild only this
service after a change, add `control-objectives` at the end of the same `up -d --build` command.

The compose file defaults the model to `ollama` / `mistral:latest` on the host
(`host.docker.internal:11434`), which needs no key. For a hosted model, set
`CONTROL_OBJECTIVES_LLM_PROVIDER`, `CONTROL_OBJECTIVES_LLM_MODEL` and the provider's key
(`OPENAI_API_KEY`, `ANTHROPIC_API_KEY` or `MISTRAL_API_KEY` are passed through) in the env file
you start the stack with. A project can also choose its own model and key on the platform's
"Models and API keys" page.

### Standalone, for development

Prerequisites: Python 3.12, [uv](https://docs.astral.sh/uv/), this repo checked out inside the aisc
repo (for `shared/identity`), and a Postgres with the platform database and project databases made
by the platform, for example a development AISC stack. Do not point a development server at the
live stack's database.

```bash
cd apps/control-objectives
uv sync --extra dev
export PYTHONPATH=../../shared/identity          # the aisc_identity package
export DATABASE_URL=postgresql://<role>:<password>@<host>:<port>/platform
export PROJECT_DATABASE_URL=postgresql://<role>:<password>@<host>:<port>/{database}
export AUTH_ENABLED=false AUTH_DEV_ROLES=admin    # no Keycloak: a fake "development" admin
uv run python -m aisc_control_objectives.migrate_projects   # every project database, to head
uv run python -m aisc_control_objectives.server             # http://localhost:8090
```

`/objectives` is the catalogue; `/p/<project pid or slug>` opens a project. `/` redirects to the
launcher (`LAUNCHER_URL`). Starting an assessment also needs `PLATFORM_URL` and
`QUALIFICATION_URL`.

For the model, copy `.env.example` to `.env` (git-ignored, read at startup; variables already in
the environment win) and set `BAF_LLM_PROVIDER`, `BAF_LLM_MODEL` and the provider's key. The
committed `control-objectives.toml` names `openai` / `gpt-4o`; the service refuses to start if the
provider it is configured with needs a key that is not set. The providers are anthropic,
compatible, deepseek, google, groq, meta, mistral, ollama, openai, openrouter, qwen, together and
xai; each reads its own key variable, and `ollama` and `compatible` need none.

The `Dockerfile` builds the same image the stack uses. The image does not contain
`aisc_identity`: compose mounts `shared/identity` at `/app/shared/identity` and sets
`PYTHONPATH=/app/src:/app/shared/identity`, and a `docker run` outside compose has to do the same.

## Configuration

Precedence for the model: built-in defaults, then `control-objectives.toml`, then the environment.

| Variable | Default | Meaning |
|---|---|---|
| `DATABASE_URL` | `postgresql+psycopg://control_objectives_rw:...@localhost:5432/platform` | The `platform` database, read for membership only. `postgresql://` is accepted. |
| `PROJECT_DATABASE_URL` | `DATABASE_URL` with its database replaced by `{database}` | Template of a project database's URL; `{database}` becomes `project_<hex>`. |
| `AUTH_ENABLED` | `true` | Verify Keycloak tokens. `false` runs every request as a fake `development` caller. |
| `AUTH_DEV_ROLES` | *(empty)* | Realm roles of the fake caller when auth is off, e.g. `admin`. |
| `KEYCLOAK_ISSUER` | *(unset)* | Token issuer; required when auth is on. |
| `KEYCLOAK_JWKS_URL` | *(unset)* | Where Keycloak's signing keys are read; required when auth is on. |
| `PLATFORM_URL` | *(unset)* | The platform service: latest card version, and the project's model choice. |
| `PLATFORM_INTERNAL_TOKEN` | *(unset)* | Service token for the platform's internal model-choice route. Without it (or `PLATFORM_URL`) every project uses the service's own model. |
| `LLM_RESOLVE_TIMEOUT` | `5` | Seconds the platform may take to say which model a project uses. |
| `QUALIFICATION_URL` | *(unset)* | The qualification app, which serves a version's card as JSON-LD. |
| `LAUNCHER_URL` | `http://localhost:8100/` | Where projects are chosen; `/` and the header link there. |
| `BAF_LLM_PROVIDER` | `provider` in the TOML, else `mistral` | Which model provider, by BAF's name. |
| `BAF_LLM_MODEL` | `model` in the TOML, else `mistral-large-latest` | Which model. |
| `BAF_LLM_BASE_URL` | *(unset)* | Endpoint for `ollama` (optional) or `compatible` (required). |
| `BAF_LLM_API_KEY` | *(unset)* | Token of a `compatible` endpoint, if it wants one. |
| `<PROVIDER>_API_KEY` | *(unset)* | The provider's own key: `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `MISTRAL_API_KEY`, `GOOGLE_API_KEY`, `GROQ_API_KEY`, `DEEPSEEK_API_KEY`, `OPENROUTER_API_KEY`, `QWEN_API_KEY`, `TOGETHER_API_KEY`, `XAI_API_KEY`, `META_API_KEY`. |
| `CONTROL_OBJECTIVES_CONFIG_FILE` | `<repo>/control-objectives.toml` | The TOML config file. |
| `CONTROL_OBJECTIVES_FILE` | the bundled CSV | Serve a different export of the objectives. |
| `CONTROL_OBJECTIVES_PORT` | `8090` | Port to serve on. |
| `CONTROL_OBJECTIVES_ROOT_PATH` | *(empty)* | Sub-path behind a reverse proxy (`/control-objectives` in the stack). |
| `CONTROL_OBJECTIVES_CORS_ORIGINS` | *(any origin)* | Comma-separated CORS allowlist. |
| `LEDGER_MODE` | `off` | `record` or `enforce` writes a ledger event with every change. |

In the stack, compose sets these from `CONTROL_OBJECTIVES_DATABASE_URL`,
`CONTROL_OBJECTIVES_PROJECT_DATABASE_URL`, `CONTROL_OBJECTIVES_LLM_PROVIDER`,
`CONTROL_OBJECTIVES_LLM_MODEL`, `CONTROL_OBJECTIVES_LLM_BASE_URL`, `PLATFORM_RISK_MAPPER_TOKEN` and
`LEDGER_MODE`, and from `env.development` in this repo.

### The objectives data

The objectives ship as `src/aisc_control_objectives/data/ai_act_control_objectives.csv`, one row
per objective, authored outside this repo. The columns are `ID` (`O1` ... `O50`),
`Macro_Requirement` (`REQ1 Human Agency and Oversight` ... `REQ11`, the trustworthiness dimension),
`Legal_Basis`, `Sub_Requirement_Label`, `Control_Objective`, `Assessment_Mode` (`Control`, `Test`
or `Control + Test`), `Target`, `Standards_Grounding`, `Grounding_Tier_Flag` and `Notes` (tags
`GAP:`, `CONDITIONAL:`, `VOLUNTARY:`, `Paired:`). A `Control + Test` objective needs both a
control and a test; `VOLUNTARY:` marks an objective as non-binding. A missing column or a cell
that breaks these rules fails at load time, naming the row. Each assessment records the sha256 of
the catalogue it was made with. The ids were `R1.1` ... `R11.4` before; `data/objective_id_renames.csv`
maps them.

## HTTP API

Interactive docs at `/docs`. The JSON API of a project is under `/p/{project}/api`, where
`{project}` is the project's pid or slug.

| Method and path | Purpose |
|---|---|
| `GET /health` | Liveness. |
| `GET /api/config` | The model the service is configured with. |
| `GET /api/control-objectives[?mode=control\|test]` | The built-in objectives, in catalogue order. |
| `GET /api/control-objectives/{id}` | One objective, 404 if unknown. |
| `GET /api/macro-requirements` | REQ1 ... REQ11 with their objectives. |
| `GET /p/{project}/api/projects`, `GET .../projects/{id}` | The project's assessments, or one. |
| `POST .../projects/{id}/ratings` | `{"risk2": {"impact": 5, "likelihood": 4}}`; a part left out keeps its value. |
| `POST .../projects/{id}/severity` | `{"risk2": 5}` sets the impact only. |
| `POST .../projects/{id}/severity-comments` | `{"risk2": "why"}`; a blank comment clears it. |
| `POST .../projects/{id}/map` | Map every risk with the model (one call per risk); 409 if the assessment changed meanwhile. |
| `POST .../projects/{id}/risks/{risk}/mapping` | `{"objective_ids": ["O1", ...]}`: a person's mapping of one risk. |
| `POST .../projects/{id}/key` | `{"O1": true, "O7": false}`: the assessor's key choices. |
| `POST .../projects/{id}/profile` | Run the assessment on another objective profile. |
| `DELETE .../projects/{id}` | Delete an assessment and everything under it; 409 for an older version's. |
| `GET/POST .../sets`, `.../sets/{id}/...`, `GET/POST .../profiles`, `.../profiles/{id}/...` | The project's objective sets and profiles. |

An assessment is started from the page (`POST /p/{project}/projects`), on the project's latest AI
card version. In each payload, `selected` lists the objectives that go forward to step 4: every
objective in the matrix, in catalogue order.

## Storage

Postgres, schema `control_objectives` in each project database. The main tables: `project` (one
assessment of one card version, `system_id` pointing at `project.system`, which the platform
owns), `graph` (the card as the bytes that were served, with their sha256), `risk` (one AIRO chain,
with the assessor's rating and comment), `mapped_objective` (an objective a risk is mapped to, its
quote and who mapped it), `mapping_run` (how the last AI run went), `objective_selection` (what
goes to step 4), `objective_key`, the objective set and profile tables, and `mapping_archive`
(append-only copies of what a mapping change replaced). The readers `report_ro` and
`dashboard_ro` get SELECT on these tables.

## Tests

```bash
uv run --extra dev pytest -q -p no:cacheprovider tests
```

Most tests need a real Postgres (not SQLite). They drop and recreate the database that
`CONTROL_OBJECTIVES_TEST_DATABASE_URL` names, so give them a throwaway one, for example:

```bash
docker run --rm -d --name co-test-pg -p 127.0.0.1:55432:5432 \
  -e POSTGRES_USER=test -e POSTGRES_PASSWORD=test postgres:15-alpine
export CONTROL_OBJECTIVES_TEST_DATABASE_URL=postgresql+psycopg://test:test@127.0.0.1:55432/co_test
uv run --extra dev pytest -q -p no:cacheprovider tests
docker rm -f co-test-pg
```

> [!WARNING]
> Never point the tests at the live stack's Postgres (port 5432). The fixtures refuse port 5432,
> and the database names `platform`, `postgres` and `project_*`, but a copy of the data on another
> port is not protected.

From the aisc repo, `scripts/lib/co-tests.sh` does all of this: a throwaway Postgres with the
platform's roles and databases, the suite, then the container removed.

Without the variable the tests that need a database fail at setup and the rest run. Some tests
skip unless more is there: the one-database-per-project tests (`test_isolation_project_databases.py`)
need the throwaway cluster made like the platform's (the aisc repo's `init/*.sql` applied, so it
has a `platform` database with `core`) and the aisc `platform/` checkout beside this repo; the
ledger tests need `platform/project-template/0020_ledger_outbox.sql`; `test_chain.py` runs only
inside `scripts/test-pipeline-chain.sh` (it needs `CHAIN_JSON`).

Lint: `uv run --extra dev ruff check src tests` (clean). The step 2 fixtures (`client`, `graph`,
`mapper`, `start`) are in `tests/conftest.py`, their helpers in `tests/step2_support.py`.

## Layout

```
src/aisc_control_objectives/
  server.py              composition root and entry point
  api/                   routes: pages and JSON (app.py), sets and profiles, ledger events
  access.py, projectdb.py  who may be here; the one way into a project database
  projects.py            the assessment flow the routes call
  risk_mapping.py, rounds.py, skills/   the model step, its checks, and its instructions (markdown)
  prioritising.py        ratings in, scores and key objectives out
  library.py             objective sets and profiles
  db/                    tables and the repository
  models/                the objective and the AI Card (AIRO graph) models
  data/                  the objectives CSV and the id renames
  templates/, static/, rendering.py   the server-rendered pages
  llm.py, baf_llm.py, config.py, settings.py, ledger.py, upstream.py
alembic/                 migrations of a project database's control_objectives schema
tests/                   pytest
docs/                    SPEC.md and INTEGRATION_AISC.md describe an earlier design; docs/history/ is the record
```

## Contributing

- Work on `feat/unified-modules`, the only branch in use.
- **Schema changes need a migration.** Change `db/tables.py`, then add a revision under
  `alembic/versions/` (file names are timestamps). Migrations run per project database: the
  service runs them on first open, `python -m aisc_control_objectives.migrate_projects` runs
  them for every project, and by hand it is
  `uv run alembic -x url=postgresql+psycopg://.../project_<hex> upgrade head` (the `platform`
  database is refused). Never edit the `revision` / `down_revision` of an existing file.
- `baf_llm.py` is shared with the qualification app's card agent
  (`apps/qualification/services/agents/fill/baf_llm.py`); a test in the aisc repo
  (`scripts/tests/test_llm_keys.py`) requires the two to be byte-identical.
- The model's instructions are markdown in `src/aisc_control_objectives/skills/`; change how the
  mapping reasons there rather than in Python.
- Nothing derived is stored: scores and default key objectives are recomputed on every read.

See `CONTRIBUTING.md` for the contribution terms.
