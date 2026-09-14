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
| `POST /api/cards` | assess a system card (body: the card JSON) |
| `GET /api/cards` · `GET /api/cards/{id}` | assessed cards |
| `POST /api/cards/{id}/profile` | confirm or override the three facts |

Every objective is served with its derived fields: `macro_id`, `macro_title`,
`requires_control`, `requires_test`, `legal_bases`.

---

## Assessing a system

Upload a system card (the JSON the qualification module produces) on the root
page, or `POST /api/cards` with it as the body. Three things happen:

1. **The model proposes an applicability profile**: three facts, each with the
   literal span of the card it rests on.

   | Fact | Governs |
   |---|---|
   | `high_risk` (with its Annex III point) | the AI Act's Chapter III duties: 44 objectives |
   | `interacts_with_natural_persons` | Art. 50 disclosure: R4.4 |
   | `personal_data` | the GDPR-based objectives: R3.3, R3.4, R3.5 |

   R6.1 and R6.2 (Art. 95 codes of conduct) are always in the register, flagged
   **non-binding**. The role is always the provider. `Target` codes play no part.

   The proposal goes through deterministic controls (`profiling.run_controls`):
   every decided fact needs a quote that is a verbatim span of the card, and
   `high_risk: yes` needs its Annex III point. Failing facts go back to the
   model, at most three attempts; the same findings twice running is a fixpoint.
   **Every exit publishes**, findings attached, because the card page is where a
   person corrects it. A dead provider publishes an all-undetermined profile
   with the error on the page.

2. **A rules table decides** (`applicability.decide`): one verdict per
   objective, `yes` / `no` / `undetermined`, with a printable reason naming
   the legal basis. No model involved. Verdicts are **provisional** until the
   profile is confirmed.

3. **A person confirms** the three facts on the card page (or
   `POST /api/cards/{id}/profile`). The model's quotes stay on the record; the
   verdicts are recomputed from the confirmed profile.

The skill the model runs under is `src/wizard/skills/extracting-the-applicability-profile.md`,
markdown so its behaviour can be changed without touching Python.

## Not here yet

**What the card says about each in-scope objective** (claims, admissions,
silence) is a separate, model-backed layer that is not built. Uploads are held
in memory and lost on restart. `frontend/` still speaks to a retired plan API.
The old design documents (`SPEC.md`, `SPEC_HARDENING.md`, `DIMENSION_PLAN.md`,
`AGENTIC_WORKFLOW.mmd`, `NEXT_STEPS.md`, `HANDOFF.md`) describe the retired
pipeline and are kept only as a record.

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
