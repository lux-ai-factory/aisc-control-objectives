# Wizard — AI Act Control Objectives

The Wizard serves the **control objectives** an AI system has to satisfy: 50
sub-requirements grouped under 11 macro requirements (R1 Human Agency and
Oversight ... R11 Record-keeping and Documentation Retention), each with its
legal basis, how it is assessed, what it applies to, and its standards
grounding.

The objectives are domain data, authored outside this repo and shipped with the
package as a CSV (`src/wizard/data/ai_act_control_objectives.csv`).

---

## The data

One row per objective:

| Column | Meaning |
|---|---|
| `ID` | `R1.1`, `R9.9`, ... (macro requirement + sub-requirement) |
| `Macro_Requirement` | `R1 Human Agency and Oversight` |
| `Legal_Basis` | `AI Act Art. 14`, `AI Act Arts. 18, 19; GDPR Art. 5(1)(e)`, ... |
| `Sub_Requirement_Label` | short name of the sub-requirement |
| `Control_Objective` | the objective itself |
| `Assessment_Mode` | `Control` (36) · `Test` (9) · `Control + Test` (5) |
| `Target` | kept from the CSV, not represented |
| `Standards_Grounding` | e.g. `ISO/IEC 42001 Ann. A.9` |
| `Grounding_Tier_Flag` | `Tier 3`, `Binding + Tier 3`, ... |
| `Notes` | caveats: `GAP:`, `CONDITIONAL:`, `VOLUNTARY:`, `Paired:` |

One convention the code relies on:

- **A paired objective needs both.** `Control + Test` means an organisational
  control *and* a technical test, so the control/test partitions overlap:
  41 objectives need a control, 14 need a test, 5 are in both.

Swapping in a newer export is a file swap: replace the CSV (or point
`WIZARD_OBJECTIVES_FILE` at another one). A renamed or missing column fails at
load time rather than silently yielding empty objectives.

---

## HTTP API

FastAPI, port `8090`, interactive docs at `/docs`.

| Method & path | Purpose |
|---|---|
| `GET /health` | liveness |
| `GET /api/config` | the effective `RunConfig` (currently: the model) |
| `GET /api/control-objectives` | all 50, in requirement order (`R9.9` before `R10.1`) |
| `GET /api/control-objectives?mode=control\|test` | the control / test partition |
| `GET /api/control-objectives/{id}` | one objective, 404 if unknown |
| `GET /api/macro-requirements` | R1 ... R11 with their objectives nested |
| `POST /api/projects` | start a project (body: the AI Card) |
| `GET /api/projects` · `GET /api/projects/{id}` | the systems under assessment |
| `POST /api/projects/{id}/card` | replace the card with a corrected export |
| `POST /api/projects/{id}/severity` | rank the risks (body: `{"risk2": 5, ...}`) |
| `POST /api/projects/{id}/map` | run the mapping (one model call per risk) |
| `DELETE /api/projects/{id}` | delete a project and everything under it |

Pages: `/` the way in, `/objectives` the catalogue, `/projects` and
`/projects/{id}` the assessments.

Every objective is served with its derived fields: `macro_id`, `macro_title`,
`requires_control`, `requires_test`, `legal_bases`.

---

## Assessing a system

A project is one system, and it starts with that system's **AI Card** from the
qualification app: `ai-card.json`, or `ontology.jsonld` if that is what you
have. Both are the same card — the qualification app's own position is that the
filled AIRO graph *is* the card — and the graph is where the risks live.

All 50 objectives are the register; no question narrows it. What the wizard
works out is **where to start**, and that is decided by the system's own risks:

1. **Rank the risks the card carries.** Each AIRO chain (risk, its source, the
   vulnerability it exploits, the consequence, who bears it, the declared
   control) gets a severity, 1 marginal to 5 decisive, from the assessor. This
   is the one judgement no model makes here. Unrated risks count as 3.

2. **Map the risks to objectives** (`risk_mapping.map_risks`, one model call
   per risk): which of the 50 would mitigate *this* risk, each claim resting on
   a literal span of that risk's own chain. The proposal goes through
   deterministic controls — the objective has to exist, the quote has to be a
   verbatim span of the risk, no duplicates — and failing items go back to the
   model with the findings, at most three attempts; the same findings twice
   running is a fixpoint. **Every exit publishes**, findings attached, because
   the project page is where a person corrects it.

3. **Read the tiers** (`prioritising.prioritise`, no model involved). An
   objective inherits the severity of the worst risk it mitigates. Tier 1 is
   the top of that, capped at 7 and holding only work a risk actually drives —
   a short Tier 1 is an honest answer, a padded one is not. Tier 2 is the rest
   of the driven work; Tier 3 is what is owed but is not where this system's
   danger lies, plus the two voluntary objectives, which bind nobody and so
   cannot displace a legal duty.

Nothing derived is stored: tiers are recomputed from the card, the ranking and
the mapping on every read, so a changed rating cannot leave a stale tier behind.
What *is* stored is what cannot be recomputed — the uploaded bytes and their
digest, the risks, the ranking, and what the mapping run cost.

The skill the model runs under is
`src/wizard/skills/mapping-a-risk-to-control-objectives.md`, markdown so its
behaviour can be changed without touching Python.

## Storage

Postgres, on the qualification app's blueprint: a table per real thing
(`project`, `graph`, `risk`, `mapped_objective`, `mapping_run`), cascading
deletes from the project, and the uploaded graph kept as the bytes that were
uploaded with a sha256 digest, so the file someone was given and the row are
the same document. Migrations are Alembic (`alembic upgrade head`); the tests
run against a real Postgres, not SQLite.

## Not here yet

**What the card says about each objective** (claims, admissions, silence) is a
separate, model-backed layer that is not built. `frontend/` still speaks to a
retired plan API. The old design documents (`SPEC.md`, `SPEC_HARDENING.md`,
`DIMENSION_PLAN.md`, `AGENTIC_WORKFLOW.mmd`, `NEXT_STEPS.md`, `HANDOFF.md`)
describe the retired pipeline and are kept only as a record.

## Configuration

Three layers, lowest to highest: **built-in defaults → `wizard.toml` →
`WIZARD_*` environment variables**.

| Variable | Default | Meaning |
|---|---|---|
| `WIZARD_CONFIG_FILE` | `<repo>/wizard.toml` | config file path |
| `WIZARD_OBJECTIVES_FILE` | bundled CSV | serve a different objectives export |
| `BAF_LLM_PROVIDER` | `mistral` | which provider, BAF's name for it |
| `BAF_LLM_MODEL` | `mistral-large-latest` | which model |
| `BAF_LLM_BASE_URL` | *(unset)* | endpoint for `ollama` / `compatible` |
| `WIZARD_PORT` | `8090` | port |
| `WIZARD_ROOT_PATH` | *(empty)* | sub-path when behind a reverse proxy |
| `WIZARD_CORS_ORIGINS` | *(permissive)* | comma-separated allowlist |
| `DATABASE_URL` | local `wizard` database | the same variable the qualification app reads |

API keys live in `.env`, each provider under its own variable
(`MISTRAL_API_KEY`, `OPENAI_API_KEY`, …), read through BAF's property store.

**The model is reached BAF's way**, the same framework, version and variables
the qualification app's ontology filler uses, so the platform has one mechanism
and one place a model is named. `src/wizard/llm.py` holds the provider table.
`provider = "ollama"` runs against a model on this machine with no credential
at all, which is the committed default.

---

## Running

```bash
uv pip install -e '.[dev]'
python -m wizard.server        # http://localhost:8090/docs
pytest
```
