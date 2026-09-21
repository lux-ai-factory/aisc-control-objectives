<div align="center">
  <img src="src/aisc_control_objectives/static/laif-logo.svg#gh-light-mode-only" alt="Luxembourg AI Factory" width="220">

  <h1>AISC Control Objectives</h1>

  <p><b>A catalogue of 50 EU AI Act control objectives, and where a given system should start.</b></p>
</div>

This is the AI Safety and Compliance (AISC) module that turns an assessed system's **AI Card** into a place to begin: it takes the risks the card carries, lets the assessor rank them, asks a model which control objectives mitigate each one, and orders the catalogue into three tiers.

Every objective in the catalogue stays owed. Nothing here rules anything out. What it produces is a **Tier 1 of no more than seven**, so an assessor has a week of work to open rather than another list of fifty.

```mermaid
flowchart LR
    A["AI Card<br/>(the filled AIRO graph)"] --> B["Rank the risks<br/>1 to 5, by a person"]
    B --> C["Map risks to objectives<br/>(one model call per risk)"]
    C --> D["Tier 1 · Tier 2 · Tier 3<br/>(computed, never stored)"]
```

## Features

- **A catalogue, served.** 50 sub-requirements under 11 macro requirements (R1 Human Agency and Oversight through R11 Record-keeping and Documentation Retention), each with its legal basis, assessment mode, standards grounding and caveats.
- **One agentic step, guarded.** The model's every claim rests on a literal span of the risk it read. Quotes are verified deterministically; failures go back to the model with the findings, and the rounds are bounded.
- **Ranking is the person's job.** Which risk matters for this system is the one judgement no model makes here.
- **Nothing derived is stored.** Tiers are recomputed from the card, the ranking and the mapping on every read, so a changed rating can never leave a stale tier behind.
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

export DATABASE_URL=postgresql://user:password@localhost:5432/control-objectives
alembic upgrade head

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

Each AIRO chain on the card (the risk, its source, the vulnerability that source exploits, the consequence, the impact and who bears it, the declared control and its follow-up) is shown in full and gets a severity from the assessor: 1 marginal for this system, 5 decisive. Unrated risks count as 3.

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

### 3. Read the tiers

`prioritising.prioritise`, no model involved. An objective inherits the severity of the worst risk it mitigates.

- **Tier 1, start here.** The top of the driven work, capped at seven, and holding only objectives a risk actually points at. A short Tier 1 is an honest answer; a padded one is not.
- **Tier 2, next.** The rest of the work the identified risks drive.
- **Tier 3, later.** Owed, but not where this system's danger lies, plus the two voluntary objectives, which bind nobody and so cannot displace a legal duty.

> [!IMPORTANT]
> Re-rank a risk and the tiers move, with no model call and no cost. That is the point: the same 50 duties, a different place to start.

## The data

The objectives are domain data, authored outside this repo and shipped with the package as a CSV (`src/aisc_control_objectives/data/ai_act_control_objectives.csv`). One row per objective:

| Column | Meaning |
|---|---|
| `ID` | `R1.1`, `R9.9`, ... (macro requirement + sub-requirement) |
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

Swapping in a newer export is a file swap: replace the CSV, or point `CONTROL_OBJECTIVES_FILE` at another one. A renamed or missing column fails at load time rather than silently yielding empty objectives. Every project records the sha256 of the catalogue it was assessed against, so a re-export cannot quietly change an old assessment's tiers.

## HTTP API

FastAPI, port `8090`, interactive docs at `/docs`. The pages are `/`, `/objectives`, `/projects` and `/projects/{id}`; the JSON API mirrors them.

| Method and path | Purpose |
|---|---|
| `GET /health` | liveness |
| `GET /api/config` | the effective `RunConfig` (which model) |
| `GET /api/control-objectives` | every objective, in requirement order (`R9.9` before `R10.1`) |
| `GET /api/control-objectives?mode=control\|test` | the control / test partition |
| `GET /api/control-objectives/{id}` | one objective, 404 if unknown |
| `GET /api/macro-requirements` | R1 through R11 with their objectives nested |
| `POST /api/projects` | start a project (body: the AI Card) |
| `GET /api/projects`, `GET /api/projects/{id}` | the systems under assessment |
| `POST /api/projects/{id}/card` | replace the card with a corrected export |
| `POST /api/projects/{id}/severity` | rank the risks, body `{"risk2": 5, ...}` |
| `POST /api/projects/{id}/map` | run the mapping (one model call per risk) |
| `DELETE /api/projects/{id}` | delete a project and everything under it |

Every objective is served with its derived fields: `macro_id`, `macro_title`, `requires_control`, `requires_test`, `legal_bases`.

## Storage

Postgres, on the qualification app's blueprint: a table per real thing, `JSONB` only where nothing queries inside, cascading deletes from the project, and Alembic migrations in place of its Prisma ones.

| Table | Holds |
|---|---|
| `project` | one system under assessment, with the catalogue digest it was assessed against |
| `graph` | the uploaded card, **as the bytes that were uploaded**, with their sha256 |
| `risk` | one AIRO chain flattened, with the assessor's severity on it |
| `mapped_objective` | one objective a risk was mapped to, with the quote behind it |
| `mapping_run` | how the run went: findings, per-risk stops, attempts, the model that bought it |

Verdicts, scores and tiers are deliberately **not** stored: they are a pure function of the catalogue, the ranking and the mapping, so writing them down would only let them go stale.

## Configuration

Three layers, lowest to highest: **built-in defaults, then `control-objectives.toml`, then `CONTROL_OBJECTIVES_*` / `BAF_*` environment variables**.

| Variable | Default | Meaning |
|---|---|---|
| `DATABASE_URL` | local `control_objectives` database | the same variable the qualification app reads |
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
  prioritising.py                      severity in, tiers out (no model)
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

192 tests. They run against a **real Postgres**, not SQLite, because testing on a different engine from production is how you find out `text[]` does not exist on the day you deploy. Each run creates and drops its own database; point `CONTROL_OBJECTIVES_TEST_DATABASE_URL` somewhere else if the default (`.../control_objectives_test`) is not right for your machine.

## Deployment

The `Dockerfile` follows the AISC app pattern: a self-contained container joined to the platform networks, the package installed editable so config resolves against `/app`.

```bash
docker build -t aisc-control-objectives .
docker run -p 8090:8090 --env-file .env -e DATABASE_URL=... aisc-control-objectives
```

`env.development` holds the settings for running inside the platform compose (served behind Caddy under `/control-objectives`, which is what `CONTROL_OBJECTIVES_ROOT_PATH` is for).

> [!WARNING]
> Run `alembic upgrade head` against the target database before starting the service. The container does not migrate on boot.

## Not here yet

**What the card says about each objective** (claims, admissions, silence) is a separate, model-backed layer that is not built.

> [!NOTE]
> `frontend/` still speaks to a retired plan API, and the design documents at the repo root (`SPEC.md`, `SPEC_HARDENING.md`, `DIMENSION_PLAN.md`, `AGENTIC_WORKFLOW.mmd`, `NEXT_STEPS.md`, `HANDOFF.md`) describe a pipeline that no longer exists. They are kept only as a record.
