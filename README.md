<div align="center">
  <img src="src/aisc_control_objectives/static/laif-logo.svg#gh-light-mode-only" alt="Luxembourg AI Factory" width="220">

  <h1>AISC Control Objectives</h1>

  <p><b>Step 2 of AISC: from an AI system's risks to the control objectives to work on first.</b></p>
</div>

## What it is

This is step 2, **Set control objectives**, of AISC (AI Assessment Sandbox Configurator), the
compose service `control-objectives`. AISC assesses an AI system in four steps: 1 Qualify the AI
system, 2 Set control objectives, 3 Choose tests and checklists, 4 Collect evidence.

This service takes the AI card that step 1 produced for one AI card version and turns the risks
on the card into a **risk register** that the assessor rates (impact x likelihood). It then builds
the **risk and control matrix**: which of the control objectives O1 to O50 mitigate each risk,
linked by the risk mapper (an AI assistant), by a person, or both. From the ratings it scores
every objective and proposes up to seven **priority objectives**; the assessor can change which
objectives are priority. The objectives of the matrix are what step 4's Evidence map and the
report work with.

A project can also write its own **objective sets** and pick **objective profiles** (a choice of
objectives from the built-in ones and its own sets); an assessment runs on one profile.

What the assessor sees and does on the page is described in the aisc repo's
`docs/appendices/2-control-objectives.md`; how this service fits with the others is in the aisc
repo's `docs/architecture.md`. This README is the reference for running and changing the service.

## How it works

```mermaid
flowchart LR
    A["AI card of one version<br/>(from qualification)"] --> B["Risk register<br/>impact x likelihood, by a person"]
    B --> C["Link risks to objectives<br/>(risk mapper, one risk at a time, or by hand)"]
    C --> D["Scores and priority objectives<br/>(computed on every read)"]
    D --> E["Step 4 works on<br/>the objectives of the matrix"]
```

- **A FastAPI service** (`src/aisc_control_objectives/`, port 8090) that serves server-rendered
  pages and a JSON API. The catalogue of objectives is a CSV shipped in the package.
- **Who may do what.** Behind the gateway (Caddy, oauth2-proxy, Keycloak) every request carries a
  Keycloak token. The service verifies it (`shared/identity`, the `aisc_identity` package of the
  aisc repo) and admits the caller with `aisc_identity.access`, which reads project membership from
  the `platform` database (`core.project`, `core.project_member`) and counts the realm `admin` as
  owner. Only the catalogue (`/objectives`, `/api/control-objectives`, `/api/macro-requirements`,
  `/api/config`), `/static/*` and `/health` are public. Under `/p/{project}` a viewer may read and
  an editor or owner may change. `{project}` is a pid or a slug: a reference shaped like a pid is
  looked up only as a pid, anything else only as a slug, and membership is read on the project
  that lookup found (so a project whose slug looks like a uuid is reached only by its pid).
- **One database per project.** A project's assessments, sets and profiles live in the schema
  `control_objectives` of that project's own Postgres database, `project_<pid without hyphens>`.
  The service connects to a project database only after membership is decided
  (`projectdb.ProjectDatabases.open`), and migrates it with Alembic the first time it opens it,
  one database at a time (Alembic's context is global to the process).
- **Starting an assessment** asks the platform (`PLATFORM_URL`) for the project's latest AI card
  version and qualification (`QUALIFICATION_URL`) for that version's AI card, with the caller's
  own token. There is one assessment per AI card version; an older version's assessment is
  read-only, and every change to it answers 409.
- **The risk mapper** (`risk_mapping.py`) asks a model, one risk at a time, which of the
  assessment's objectives (its profile's) mitigate it. Each claim must quote the risk chain (the
  risk, its source, weakness, consequence, impact and controls) literally. Deterministic checks
  reject unknown ids, quotes that are not in the chain, missing quotes and repeats; what failed
  goes back to the model, for at most three attempts in all per risk (`rounds.py`). Claims that
  still fail are dropped, and the rest is published with its findings, so a person can correct it
  on the page. A run is saved only over the assessment it read: if a newer AI card version,
  another profile or a person's link came meanwhile, it saves nothing and answers 409. A person's
  links are checked again under the same lock, so a profile switch in between also answers 409. A
  run in which every model call failed (revoked key, quota, outage) saves nothing and answers 502;
  in a run where only some failed, the failed risks keep the links they had. A provider's error is
  stored with credentials and key-like strings taken out and cut to 300 characters
  (`risk_mapping.safe_error`), and only editors and owners see it, on the page and in the API; a
  viewer sees how the run stopped and nothing more. The model is reached through
  [BAF](https://pypi.org/project/besser-agentic-framework/) (`llm.py`, `baf_llm.py`); the
  project's own model and key, chosen on the platform under Manage > AI assistants, take
  precedence over the service's own.
- **Scores** (`prioritising.py`, no model): an objective scores the sum of the ratings
  (impact x likelihood, 1 to 25) of the risks linked to it. Ratings fall in the bands Low 1-4,
  Medium 5-9, High 10-16, Critical 17-25; a risk is "Not rated" and counts 0 until both its impact
  and likelihood are set. Objectives are ranked by score, then highest risk, then binding duty
  before standards-grounded, then catalogue order, with voluntary objectives last. By default the
  priority objectives are the first seven by rank whose highest risk is High or Critical; a
  voluntary objective is never priority by default. The assessor's own choices override the
  default. Scores are never stored, so a changed rating can never leave a stale score. The
  selection keeps the resulting priority objectives (`objective_selection.priority_ids`) for the
  readers that do not rank (the report, the Evidence map): every change that can move them writes
  them again.
- **The ledger.** With `LEDGER_MODE` set to `record` or `enforce` (the stack sets `record`), every
  write also records an event through the project database's `ledger.emit`, in the same
  transaction (`ledger.py`). The platform relays those events to the immudb ledger. A mapping run
  in which some risks failed is `ai.mapping.completed` with what it saved and `failed_risks`; one
  in which every risk failed, or that saved nothing, is `ai.mapping.failed` with a code (when every
  risk failed, that event is the only thing the run writes). An event cites the request the
  gateway witnessed (`X-AISC-Request-Id`) only when the request carries a gateway token that
  verifies (signed by Keycloak, `azp` the gateway client `AISC_GATEWAY_CLIENT_ID`, and any Bearer
  for the same person); a caller reaching the service directly cannot make an event cite a request
  id it chose. With `AUTH_ENABLED=false` the presented id is kept.

## Install and run

### Inside the AISC stack (the usual way)

The service is the `control-objectives` service of the aisc repo's
`docker-compose.development.yml`, with a one-shot `control-objectives-migrate` that brings every
project database to the head revision before it starts. It has no published port: Caddy serves it
at `/control-objectives` behind the gateway.

From the aisc folder (the repo this one is a submodule of, at `apps/control-objectives`):

```bash
uv run scripts/aisc.py start
```

It makes the secrets if they are missing (the service needs `PLATFORM_RISK_MAPPER_TOKEN`), builds
and starts the stack, and keeps port 443 answering on an https install.

Then open http://localhost:8100, sign in, open a project and choose step 2. To rebuild only this
service after a change, run `uv run scripts/aisc.py secrets` in the aisc folder. It prints this
install's exact compose command, which ends in `up -d`. Append `--build control-objectives` to it.

The compose file defaults the risk mapper's model to `ollama` / `mistral:latest` on the host
(`host.docker.internal:11434`), which needs no key. For a hosted model, set
`CONTROL_OBJECTIVES_LLM_PROVIDER`, `CONTROL_OBJECTIVES_LLM_MODEL` and the provider's key
(`OPENAI_API_KEY`, `ANTHROPIC_API_KEY` or `MISTRAL_API_KEY` are passed through) in the env file
you start the stack with. A project can also choose its own model and key under Manage > AI
assistants.

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
| `PLATFORM_URL` | *(unset)* | The platform service: latest AI card version, and the project's model choice. |
| `PLATFORM_INTERNAL_TOKEN` | *(unset)* | Service token for the platform's internal model-choice route. Without it (or `PLATFORM_URL`) every project uses the service's own model. |
| `LLM_RESOLVE_TIMEOUT` | `5` | Seconds the platform may take to say which model a project uses. |
| `QUALIFICATION_URL` | *(unset)* | The qualification app, which serves a version's AI card as JSON-LD. |
| `LAUNCHER_URL` | `http://localhost:8100/` | Where projects are chosen; `/` and the header link go there. |
| `BAF_LLM_PROVIDER` | `provider` in the TOML, else `mistral` | Which model provider, by BAF's name. |
| `BAF_LLM_MODEL` | `model` in the TOML, else `mistral-large-latest` | Which model. |
| `BAF_LLM_BASE_URL` | *(unset)* | Endpoint for `ollama` (optional) or `compatible` (required). |
| `BAF_LLM_API_KEY` | *(unset)* | Token of a `compatible` endpoint, if it wants one. |
| `<PROVIDER>_API_KEY` | *(unset)* | The provider's own key: `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `MISTRAL_API_KEY`, `GOOGLE_API_KEY`, `GROQ_API_KEY`, `DEEPSEEK_API_KEY`, `OPENROUTER_API_KEY`, `QWEN_API_KEY`, `TOGETHER_API_KEY`, `XAI_API_KEY`, `META_API_KEY`. |
| `CONTROL_OBJECTIVES_CONFIG_FILE` | `<repo>/control-objectives.toml` | The TOML config file. |
| `CONTROL_OBJECTIVES_FILE` | the bundled CSV | Serve a different export of the objectives. |
| `CONTROL_OBJECTIVES_PORT` | `8090` | Port to serve on. |
| `CONTROL_OBJECTIVES_ROOT_PATH` | *(empty)* | Sub-path behind a reverse proxy (`/control-objectives` in the stack). |
| `CONTROL_OBJECTIVES_CORS_ORIGINS` | *(none: same-origin only)* | Comma-separated CORS allowlist. |
| `LEDGER_MODE` | `off` (the stack sets `record`) | `record` or `enforce` writes a ledger event with every change. |
| `AISC_GATEWAY_CLIENT_ID` | `aisc-gateway` | The `azp` of the gateway's token; only a request carrying such a token may name the request id ledger events cite. |

In the stack, compose sets every value the service needs: the database URLs from
`CONTROL_OBJECTIVES_DATABASE_URL` and `CONTROL_OBJECTIVES_PROJECT_DATABASE_URL` (or the generated
`control_objectives_rw` password), the model from `CONTROL_OBJECTIVES_LLM_PROVIDER`,
`CONTROL_OBJECTIVES_LLM_MODEL` and `CONTROL_OBJECTIVES_LLM_BASE_URL`, `PLATFORM_INTERNAL_TOKEN`
from `PLATFORM_RISK_MAPPER_TOKEN`, `LEDGER_MODE`, the root path, the Keycloak and service URLs.
The port stays at its default, 8090. `env.development` in this repo is the service's compose env
file; it holds only commented examples (a hosted provider, `CONTROL_OBJECTIVES_FILE`).

### The objectives data

The objectives ship as `src/aisc_control_objectives/data/ai_act_control_objectives.csv`, one row
per objective, authored outside this repo. The columns are `ID` (`O1` ... `O50`),
`Macro_Requirement` (`REQ1 Human Agency and Oversight` ... `REQ11`, the trustworthiness dimension),
`Legal_Basis`, `Sub_Requirement_Label`, `Control_Objective`, `Assessment_Mode` (`Control`, `Test`
or `Control + Test`), `Target`, `Standards_Grounding`, `Grounding_Tier_Flag` and `Notes` (tags
`GAP:`, `CONDITIONAL:`, `VOLUNTARY:`, `Paired:`). A `Control + Test` objective needs both a
control and a test; `VOLUNTARY:` marks an objective as non-binding. A missing column or a cell
that breaks these rules fails at load time, naming the row. Each assessment records the sha256 of
the catalogue it was made with. `data/objective_id_renames.csv` maps the older ids `R1.1` ...
`R11.4` to `O1` ... `O50`.

## HTTP API

Interactive docs at `/docs`. The JSON API of a project is under `/p/{project}/api`, where
`{project}` is the project's pid or slug (a pid-shaped reference is only ever a pid). Reading
takes the viewer role, every `POST`, `PUT` and `DELETE` the editor role. On an older AI card
version's assessment every change answers 409.

The routes and payload fields that say "key" are the priority objectives: `key` on an objective
of `priorities` is whether it is a priority objective, `key_default` whether the default rule
makes it one.

**Service and catalogue (public)**

| Method and path | Purpose |
|---|---|
| `GET /health` | Liveness. |
| `GET /api/config` | The service's default model; a project's own choice on the platform wins. |
| `GET /api/control-objectives[?mode=control\|test]` | The built-in objectives, in catalogue order. |
| `GET /api/control-objectives/{id}` | One objective, 404 if unknown. |
| `GET /api/macro-requirements` | REQ1 ... REQ11 with their objectives. |

**Assessments** (under `/p/{project}/api`)

| Method and path | Purpose |
|---|---|
| `GET /projects`, `GET /projects/{id}` | The project's assessments, or one. The last AI run's error text is included for editors and owners only. |
| `POST /projects/{id}/ratings` | `{"risk2": {"impact": 5, "likelihood": 4}}`; a part left out keeps its value, `null` clears it. |
| `POST /projects/{id}/severity-comments` | `{"risk2": "why"}`; a blank comment clears it. |
| `POST /projects/{id}/map[?replace=true]` | Run the risk mapper on every risk no person has linked; `replace=true` includes those too. 409 if the assessment changed meanwhile, 502 if every model call failed (nothing saved). |
| `POST /projects/{id}/risks/{risk}/mapping` | `{"objective_ids": ["O1", ...]}`: a person's links for one risk; 409 if the assessment moved to another profile meanwhile. |
| `POST /projects/{id}/key` | `{"O1": true, "O7": false}`: the assessor's priority choices, which win over the default. |
| `POST /projects/{id}/keys/reset` | Remove every priority choice: each objective follows the default rule again. |
| `POST /projects/{id}/profile` | `{"profile_id": ...}`: run the assessment on another objective profile; links and selected objectives outside it are dropped. |
| `POST /projects/{id}/card` | Read the AI card of the assessment's version again after it changed in qualification; ratings, rationales, links and priority choices stay on the risks whose text is unchanged. |
| `DELETE /projects/{id}` | Delete an assessment and everything under it. |

**Objective sets and profiles** (under `/p/{project}/api`)

| Method and path | Purpose |
|---|---|
| `GET /sets`, `POST /sets` | List the project's sets, or make one (`code`, `name`, `description`). |
| `GET /sets/{set_id}`, `DELETE /sets/{set_id}` | One set, or delete it. |
| `POST /sets/{set_id}/objectives` | Add an objective to the set's draft. |
| `PUT /sets/{set_id}/objectives/{objective_id}` | Change a draft objective. |
| `POST /sets/{set_id}/objectives/{objective_id}/retire`, `.../restore` | Retire an objective, or bring it back. |
| `POST /sets/{set_id}/publish` | Publish the draft as the set's next fixed version. |
| `GET /sets/{set_id}/versions/{number}` | One published version. |
| `GET /profiles`, `POST /profiles` | List the project's profiles, or make one (`name`, `description`, `objective_ids`). |
| `GET /profiles/{profile_id}` | One profile. |
| `POST /profiles/{profile_id}/versions` | Save a new version of a profile. |
| `POST /profiles/{profile_id}/newer` | A new version on the sets' latest versions; `dropped` lists what they retired. |

An assessment is started from the page (`POST /p/{project}/projects`), on the project's latest AI
card version; it answers 502 when the platform or qualification is down or answers with something
that is not what was asked for (a sign-in page, another JSON shape, an AI card that is not UTF-8).
In each assessment payload, `selected` lists the objectives that go forward to step 4: every
objective in the matrix, in catalogue order.

## Storage

Postgres, schema `control_objectives` in each project database. The main tables: `project` (one
assessment of one AI card version, `system_id` pointing at `project.system`, which the platform
owns, and what it carried from the previous version's assessment), `graph` (the AI card as the
bytes that were served, with their sha256), `risk` (one AIRO chain, with the assessor's rating and
comment, and whether a person linked it), `mapped_objective` (an objective a risk is linked to,
its quote and who linked it), `mapping_run` (how the last AI run went), `objective_selection`
(what goes to step 4), `objective_key` (the assessor's priority choices), the objective set and
profile tables, and `mapping_archive` (append-only copies of what a mapping change replaced). The
readers `report_ro` and `dashboard_ro` get SELECT on these tables, except `mapping_archive` and
the set editor's `objective_draft`, which no reader is granted.

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

Lint: `uv run --extra dev ruff check src tests`. The step 2 fixtures (`client`, `graph`,
`mapper`, `start`) are in `tests/conftest.py`, their support code in `tests/step2_support.py`.

## Layout

```
src/aisc_control_objectives/
  server.py              composition root and entry point
  api/                   routes: pages and JSON (app.py), sets and profiles, ledger events
  access.py, projectdb.py  who may be here; the one way into a project database
  projects.py            the assessment flow the routes call
  risk_mapping.py, rounds.py, skills/   the risk mapper, its checks, and its instructions (markdown)
  prioritising.py        ratings in, scores and priority objectives out
  library.py             objective sets and profiles
  db/                    tables and the repository
  models/                the objective and the AI card (AIRO graph) models
  data/                  the objectives CSV and the id renames
  templates/, static/, rendering.py   the server-rendered pages
  llm.py, baf_llm.py, config.py, settings.py, ledger.py, upstream.py
alembic/                 migrations of a project database's control_objectives schema
tests/                   pytest
docs/                    SPEC.md and INTEGRATION_AISC.md describe an earlier design
```

## Contributing

- Work on `pilot`, the only branch in use.
- **Schema changes need a migration.** Change `db/tables.py`, then add a revision under
  `alembic/versions/` (file names are timestamps). Migrations run per project database: the
  service runs them on first open, `python -m aisc_control_objectives.migrate_projects` runs
  them for every project, and by hand it is
  `uv run alembic -x url=postgresql+psycopg://.../project_<hex> upgrade head` (the `platform`
  database is refused). Never edit the `revision` / `down_revision` of an existing file.
- `baf_llm.py` is shared with the qualification app's card agent
  (`apps/qualification/services/agents/fill/baf_llm.py`); a test in the aisc repo
  (`scripts/tests/test_llm_keys.py`) requires the two to be byte-identical.
- The risk mapper's instructions are markdown in `src/aisc_control_objectives/skills/`; change how
  the mapping reasons there rather than in Python.
- Scores are recomputed on every read. The one derived value stored is the selection's
  `priority_ids`, written again by every change that can move the priority objectives.

See `CONTRIBUTING.md` for the contribution terms.
