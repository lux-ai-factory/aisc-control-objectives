<div align="center">
  <img src="src/aisc_control_objectives/static/laif-logo.svg#gh-light-mode-only" alt="Luxembourg AI Factory" width="220">

  <h1>AISC Control Objectives</h1>

  <p><b>A catalogue of 50 EU AI Act control objectives, and where a given system should start.</b></p>
</div>

This is the AI Safety and Compliance (AISC) module that turns an assessed system's **AI Card** into a place to begin: it takes the risks the card carries into a risk register the assessor rates (impact x likelihood), builds a risk and control matrix of the control objectives that mitigate each one (by hand, or suggested by a model), and marks the key objectives to start with.

Every objective in the catalogue stays owed. Nothing here rules anything out. What it produces is **no more than seven key objectives** by default, so an assessor has a week of work to open rather than another list of fifty; the assessor can change which are key.

```mermaid
flowchart LR
    A["AI Card<br/>(the filled AIRO graph)"] --> B["Rank the risks<br/>1 to 5, by a person"]
    B --> C["Map risks to objectives<br/>(one model call per risk)"]
    C --> D["Scores and key objectives<br/>(computed, never stored)"]
```

## Features

- **A catalogue, served.** 50 sub-requirements under 11 macro requirements (R1 Human Agency and Oversight through R11 Record-keeping and Documentation Retention), each with its legal basis, assessment mode, standards grounding and caveats.
- **One agentic step, guarded.** The model's every claim rests on a literal span of the risk it read. Quotes are verified deterministically; failures go back to the model with the findings, and the rounds are bounded.
- **Ranking is the person's job.** Which risk matters for this system is the one judgement no model makes here.
- **Nothing derived is stored.** Scores and default key objectives are recomputed from the card, the ratings and the matrix on every read, so a changed rating can never leave a stale score behind. Only the assessor's own key choices are stored.
- **Server-rendered pages** in the qualification app's own design tokens, so the modules read as one platform.
- **Persisted in Postgres**, on the qualification app's schema blueprint, with the uploaded card kept as the bytes that were uploaded.

## Getting started

### Prerequisites

- Python 3.12+
- PostgreSQL (the platform's instance will do)
- An API key for one hosted model provider, or [Ollama](https://ollama.com) for a model on your own machine

### Install and run

```bash
uv pip install -e '.[dev]'

# who is in which project: the platform database
export DATABASE_URL=postgresql://user:password@localhost:5432/platform
# each project's own database; {database} becomes project_<pid without hyphens>
export PROJECT_DATABASE_URL=postgresql://user:password@localhost:5432/{database}
python -m aisc_control_objectives.migrate_projects   # every project database, to head

python -m aisc_control_objectives.server        # http://localhost:8090
```

Then open `/` for the way in, `/objectives` for the catalogue, `/projects` to assess a system.

### Point it at a model

The model is reached **BAF's way** ([besser-agentic-framework](https://pypi.org/project/besser-agentic-framework/)), the same framework, version and variables the qualification app's ontology filler uses, so the platform has one mechanism and one place a model is named.

```bash
# in .env (git-ignored; copy .env.example)
BAF_LLM_PROVIDER=openai
BAF_LLM_MODEL=gpt-4o
OPENAI_API_KEY=sk-...
```

Thirteen providers are wired (`src/aisc_control_objectives/llm.py`): anthropic, compatible, deepseek, google, groq, meta, mistral, ollama, openai, openrouter, qwen, together, xai. **Each reads its own variable**, never a shared one. `ollama` and `compatible` need no credential at all.

> [!NOTE]
> The committed `control-objectives.toml` asks for `openai` / `gpt-4o`. Environment variables override it, so a deployment never has to edit the file.

## Assessing a system

A project is one system, and it starts with that system's **AI Card** exported from the qualification app: `ai-card.json`, or `ontology.jsonld` if that is what you have. Both are the same card (the qualification app's position is that the filled AIRO graph *is* the card), and the graph is where the risks live.

### 1. Rank the risks

Each AIRO chain on the card (the risk, its source, the vulnerability that source exploits, the consequence, the impact and who bears it, the declared control and its follow-up) is a row of the risk register and gets a rating from the assessor: impact and likelihood, 1 to 5 each, whose product (1 to 25) falls in the 5x5 bands Low 1-4, Medium 5-9, High 10-16, Critical 17-25. An unrated part counts 3; an optional rationale goes with it.

### 2. Map the risks to objectives

`risk_mapping.map_risks`, one model call per risk: which objectives in the catalogue would mitigate *this* risk, each claim resting on a verbatim span of that risk's own chain. The proposal then goes through deterministic controls, and only what survives them is published:

| Finding | What it caught |
|---|---|
| `unknown-objective` | an id that is not in the catalogue |
| `quote-not-in-risk` | a "quote" that is not a literal span of the risk (the usual failure: the model quotes the objective's own text) |
| `quote-missing` | a claim with nothing behind it |
| `duplicate-objective` | the same objective claimed twice for one risk |
| `risk-unmapped` | a risk the model returned nothing for |

Failing items go back to the model with their findings, at most three attempts. The loop ends `clean`, `fixpoint` (the same findings twice running), `cap` (the round limit) or `failed` (no answer, or not the shape asked for), and **every exit publishes**, findings attached: the project page is where a person corrects it, and withholding a flawed answer leaves them nothing to correct.

A person can map a risk instead, or correct what the model proposed: each risk on the project page has
an editor listing all fifty objectives by dimension. An objective kept from the model stays the
model's, with its quote; one the person adds is theirs (`mapped_objective.source`, `ai` or `person`).
Mapping with AI again replaces the whole mapping, edits by hand included.

### 3. Score the objectives, mark the key ones

`prioritising.prioritise`, no model involved. An objective scores the sum of the ratings of the
risks mapped to it:

    S(o) = sum of (impact x likelihood) over the risks r mapped to o

They are ranked by S, then by the highest single rating, then a directly binding duty before one
only grounded in standards, then catalogue order. By default the **key** objectives are the first
seven driven by at least one High or Critical risk (never a voluntary one); the assessor turns key on
or off for any objective, and that choice is stored. What the matrix holds is what goes forward to
step 4, Collect evidence.

> [!IMPORTANT]
> Re-rate a risk and the scores move, with no model call and no cost.

## The data

The objectives are domain data, authored outside this repo and shipped with the package as a CSV (`src/aisc_control_objectives/data/ai_act_control_objectives.csv`). One row per objective:

| Column | Meaning |
|---|---|
| `ID` | `O1` ... `O50`, in catalogue order (until 2026-10-01 `R1.1` ... `R11.4`; the table is `data/objective_id_renames.csv`) |
| `Macro_Requirement` | `R1 Human Agency and Oversight` |
| `Legal_Basis` | `AI Act Art. 14`, `AI Act Arts. 18, 19; GDPR Art. 5(1)(e)`, ... |
| `Sub_Requirement_Label` | short name of the sub-requirement |
| `Control_Objective` | the objective itself |
| `Assessment_Mode` | `Control` (36), `Test` (9), `Control + Test` (5) |
| `Target` | kept from the CSV, not represented |
| `Standards_Grounding` | e.g. `ISO/IEC 42001 Ann. A.9` |
| `Grounding_Tier_Flag` | `Tier 3`, `Binding + Tier 3`, ... |
| `Notes` | caveats: `GAP:`, `CONDITIONAL:`, `VOLUNTARY:`, `Paired:` |

Two conventions the code relies on:

- **A paired objective needs both.** `Control + Test` means an organisational control *and* a technical test, so the two partitions overlap: 41 objectives need a control, 14 need a test, 5 are in both.
- **`VOLUNTARY:` is the author's own judgement**, not one made here, and it is what flags an objective non-binding.

Swapping in a newer export is a file swap: replace the CSV, or point `CONTROL_OBJECTIVES_FILE` at another one. A renamed or missing column fails at load time rather than silently yielding empty objectives. Every project records the sha256 of the catalogue it was assessed against, so a re-export cannot quietly change an old assessment's scores.

## HTTP API

FastAPI, port `8090`, interactive docs at `/docs`. The pages are `/`, `/objectives`, `/projects` and `/projects/{id}`; the JSON API mirrors them.

| Method and path | Purpose |
|---|---|
| `GET /health` | liveness |
| `GET /api/config` | the effective `RunConfig` (which model) |
| `GET /api/control-objectives` | every objective, in catalogue order (`O9` before `O10`) |
| `GET /api/control-objectives?mode=control\|test` | the control / test partition |
| `GET /api/control-objectives/{id}` | one objective, 404 if unknown |
| `GET /api/macro-requirements` | R1 through R11 with their objectives nested |
| `GET /p/{project}/api/projects`, `GET /p/{project}/api/projects/{id}` | the project's assessments (`{project}` is its pid or slug) |
| `POST /p/{project}/api/projects/{id}/ratings` | rate the risks, body `{"risk2": {"impact": 5, "likelihood": 4}, ...}` |
| `POST /p/{project}/api/projects/{id}/severity` | the route from before the matrix: `{"risk2": 5}` sets the impact |
| `POST /p/{project}/api/projects/{id}/key` | the assessor's key choices, body `{"O1": true, "O7": false}` |
| `POST /p/{project}/api/projects/{id}/map` | run the mapping (one model call per risk) |
| `POST /p/{project}/api/projects/{id}/risks/{risk}/mapping` | a person maps one risk, body `{"objective_ids": ["O1", ...]}` (replaces that risk's mapping; kept rows stay the AI's, added ones are the person's; unknown ids 422) |
| `DELETE /p/{project}/api/projects/{id}` | delete an assessment and everything under it |

**Scope.** What the matrix holds goes forward to step 4 (Collect evidence), where tests and controls are linked to it: every objective mapped to at least one risk, in catalogue order, whoever mapped it. The payload's `selected` lists them. There is no separate selection since the risk and control matrix (2026-10-01).

An assessment is started from the page (`POST /p/{project}/projects`), of the project's latest AI card version. Everything under `/p/{project}` needs a signed-in member of that project (an editor to change anything), and is answered from that project's own database: an id of another project is a 404.

Every objective is served with its derived fields: `macro_id`, `macro_title`, `requires_control`, `requires_test`, `legal_bases`.

## Storage

Postgres, one database per project (`project_<pid without hyphens>`, schema `control_objectives`), on the qualification app's blueprint: a table per real thing, `JSONB` only where nothing queries inside, cascading deletes from the project, and Alembic migrations in place of its Prisma ones.

| Table | Holds |
|---|---|
| `project` | one assessment of one AI card version (`system_id`, a row of `project.system` in the same database), with the catalogue digest it was assessed against |
| `graph` | the uploaded card, **as the bytes that were uploaded**, with their sha256 |
| `risk` | one AIRO chain flattened, with the assessor's rating (impact, likelihood) and rationale on it |
| `mapped_objective` | one objective a risk was mapped to, with the quote behind it |
| `mapping_run` | how the run went: findings, per-risk stops, attempts, the model that bought it |

Verdicts, scores and default key objectives are deliberately **not** stored: they are a pure function of the catalogue, the ranking and the mapping, so writing them down would only let them go stale.

## Configuration

Three layers, lowest to highest: **built-in defaults, then `control-objectives.toml`, then `CONTROL_OBJECTIVES_*` / `BAF_*` environment variables**.

| Variable | Default | Meaning |
|---|---|---|
| `DATABASE_URL` | local `platform` database | the platform database, read for membership only |
| `PROJECT_DATABASE_URL` | `DATABASE_URL` with `{database}` | a project's own database, `{database}` replaced by `project_<hex>` |
| `CONTROL_OBJECTIVES_CONFIG_FILE` | `<repo>/control-objectives.toml` | config file path |
| `CONTROL_OBJECTIVES_FILE` | bundled CSV | serve a different objectives export |
| `BAF_LLM_PROVIDER` | `mistral` | which provider, BAF's name for it |
| `BAF_LLM_MODEL` | `mistral-large-latest` | which model |
| `BAF_LLM_BASE_URL` | *(unset)* | endpoint for `ollama` / `compatible` |
| `<PROVIDER>_API_KEY` | *(unset)* | the key, under the provider's own name |
| `CONTROL_OBJECTIVES_PORT` | `8090` | port |
| `CONTROL_OBJECTIVES_ROOT_PATH` | *(empty)* | sub-path when behind a reverse proxy |
| `CONTROL_OBJECTIVES_CORS_ORIGINS` | *(permissive)* | comma-separated allowlist |

A `.env` next to the repo root is loaded at startup if present (plain `KEY=VALUE` lines, an `export ` prefix and quotes are fine). Existing environment variables always win.

## Project structure

```
src/aisc_control_objectives/
  data/ai_act_control_objectives.csv   the objectives themselves (domain data)
  models/                              control_objective.py · ontology.py (the AI Card)
  control_objectives.py                loading and validating the catalogue
  risk_mapping.py                      the agentic step, and its controls
  rounds.py                            the review loop policy, shared and stated once
  prioritising.py                      ratings in, scores and key objectives out (no model)
  projects.py                          the flow the routes call
  db/                                  tables.py · repository.py
  api/app.py                           routes, pages and JSON
  rendering.py, templates/, static/    the server-rendered pages
  skills/                              the model's instructions, in markdown
  llm.py, config.py, settings.py, server.py
alembic/versions/                      timestamped migrations
tests/                                 pytest, against a real Postgres
```

The skill the model runs under is `src/aisc_control_objectives/skills/mapping-a-risk-to-control-objectives.md`: markdown, so its behaviour can be changed without touching Python.

## Testing

```bash
pytest
```

192 tests. They run against a **real Postgres**, not SQLite, because testing on a different engine from production is how you find out `text[]` does not exist on the day you deploy. Each run creates and drops its own database, so `CONTROL_OBJECTIVES_TEST_DATABASE_URL` must name a scratch database on a throwaway Postgres: the suite refuses to run without it, on port 5432, or against `platform`, `postgres` or a `project_` database. The isolation tests also make real project databases in that cluster's `platform` (made by the repository's `init/*.sql`).

## Deployment

The `Dockerfile` follows the AISC app pattern: a self-contained container joined to the platform networks, the package installed editable so config resolves against `/app`.

```bash
docker build -t aisc-control-objectives .
docker run -p 8090:8090 --env-file .env -e DATABASE_URL=... aisc-control-objectives
```

`env.development` holds the settings for running inside the platform compose (served behind Caddy under `/control-objectives`, which is what `CONTROL_OBJECTIVES_ROOT_PATH` is for).

> [!WARNING]
> Run `python -m aisc_control_objectives.migrate_projects` (the `control-objectives-migrate` one-shot) before starting the service. The service also migrates a project database the first time it opens it.

## Not here yet

**What the card says about each objective** (claims, admissions, silence) is a separate, model-backed layer that is not built.

> [!NOTE]
> `frontend/` still speaks to a retired plan API.

## Documentation

| Where | What it is |
|---|---|
| this README | how to run and configure the service; the current contract |
| `docs/SPEC.md` | what the service is specified to do |
| `docs/INTEGRATION_AISC.md` | how it is wired into the AISC platform compose |
| `docs/history/` | session notes and superseded design documents, kept as a record. They describe a pipeline that no longer exists; do not read them as guidance. |
