# Wizard — Select Tests & Controls

The Wizard matches a qualified AI system to the right evaluations. Given a
system's **system card** (JSON), it recommends the catalogue's tests and
datasets and the relevant control checklists — each with a rationale, organised
by trustworthiness dimension, and exported as a report.

It is a read-only companion to the catalogue: it reads the catalogue's tests and
checklists over their existing HTTP API and never writes to them
(**SPEC §0: strictly additive**). See [`SPEC.md`](SPEC.md) for the full design.

---

## How it works

```
system card (JSON)
      │
      ▼
  prefilter ──▶ dimension framing ──▶ propose → review loop ──▶ guards ──▶ assessment plan
 (deterministic   (11 trustworthiness   (LLM proposer +        (evidence,    (per-dimension
  candidate        dimensions)           reviewer, N rounds)    coverage,      status + items,
  scoring)                                                      pairing)       gaps, warnings)
                                                                                  │
                                                                          JSON  ◀─┴─▶  PDF
```

- **Dimension-first.** Findings are organised across 11 trustworthiness
  dimensions. Each dimension gets a status (`covered` / `partial` / `gap`), what
  the card already addresses, the residual gaps, and the items recommended to
  close them.
- **Propose → review.** A proposer agent suggests items; a reviewer agent (or a
  multi-lens panel — relevance / coverage / parsimony) gates them over up to
  `max_rounds` rounds, dropping duplicates and tightening coverage.
- **Deterministic guards.** Post-processing enforces policy independently of the
  model: G1 evidence-quote verification, G2 coverage-claim plausibility, G3
  test/dataset pairing. Each is configurable (`drop` / `demote` / `off`, etc.).
- **High-risk floor (D2).** In a high-risk deployment a dimension can't be
  auto-marked "covered" without an accepted control checklist. Which sectors are
  high-risk is EU AI Act **Annex III** domain knowledge, kept in
  `knowledge/high-risk-sectors.md` — not in config.
- **Multi-provider.** Every model runs through **LiteLLM**, so the model string
  carries the provider (`anthropic/claude-opus-4-8`, `openai/gpt-4o-mini`,
  `gemini/…`, `azure/…`, `bedrock/…`, `ollama/…`). The matching API key is read
  from the environment / `.env`.

---

## HTTP API

The service is FastAPI. Key routes (interactive docs at `/docs`):

| Method & path | Purpose |
|---|---|
| `GET  /api/config` | The effective `RunConfig` (echoed on every plan). |
| `POST /api/plans/from-card` | Run the pipeline on a posted system card → assessment plan. |
| `POST /api/plans` | Run from a qualification id (via the configured provider). |
| `GET  /api/plans` · `GET /api/plans/{id}` | List / fetch in-memory plans. |
| `POST /api/plans/{id}/finalize` | Finalize a plan, optionally deselecting items. |
| `GET  /api/plans/{id}/pdf` | Render a stored plan to PDF (Jinja2 + WeasyPrint). |
| `POST /api/plans/render-pdf` | Render a PDF from a plan supplied in the body — stateless, so it survives restarts. |

> Plans are kept in an in-memory store (lost on restart); persistence to the
> platform database is the SPEC's post-demo direction.

---

## Configuration

Three layers, lowest to highest precedence: **built-in defaults → `wizard.toml`
→ `WIZARD_*` environment variables → per-request API overrides**.

- **`wizard.toml`** (override path with `WIZARD_CONFIG_FILE`) — the model, review
  rounds, guard policy, review lenses, and the high-risk floor toggle.
- **`.env`** — provider API keys only (LiteLLM reads the one matching the
  model's provider). See [`.env.example`](.env.example).

Selected environment variables:

| Variable | Default | Meaning |
|---|---|---|
| `WIZARD_PORT` | `8090` | Port to serve on. |
| `WIZARD_CATALOGUE_URL` | `http://localhost:8001` | Catalogue backend base URL. |
| `WIZARD_ROOT_PATH` | `""` | Sub-path when behind a reverse proxy (e.g. `/wizard`). |
| `WIZARD_CONFIG_FILE` | `<repo>/wizard.toml` | Config file path. |
| `WIZARD_SKILLS_DIR` | `<repo>/skills` | Optional skills document base. |
| `WIZARD_KNOWLEDGE_DIR` | `<repo>/knowledge` | Domain-knowledge base (Annex III, …). |
| `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / … | — | Provider key, per the model. |

---

## Running

### Local (development)

```bash
uv venv && uv pip install -e ".[dev]"

# point at a running catalogue backend; provider key in .env
PYTHONPATH=src \
  WIZARD_CATALOGUE_URL=http://localhost:8000 \
  WIZARD_PORT=8090 \
  python -m wizard.server
```

The catalogue UI (a separate origin) calls this service directly to run a card
and to drive its "Recommended" filter.

### Docker

```bash
docker build -t aisc-wizard:latest .
docker run --rm -p 8090:8090 \
  -e WIZARD_CATALOGUE_URL=http://<catalogue-host>:8000 \
  -e ANTHROPIC_API_KEY=… \
  aisc-wizard:latest
```

The image bundles the WeasyPrint native stack (Pango/Cairo/GDK-Pixbuf + fonts)
so PDF export works out of the box.

### As an aisc submodule

The repo is prepared to drop into the `aisc` platform under `apps/wizard`
(Dockerfile, `env.development`, reverse-proxy root-path support). The exact,
not-yet-applied integration recipe — submodule, compose service, Caddy route —
is in [`INTEGRATION_AISC.md`](INTEGRATION_AISC.md).

---

## Testing

The offline core is fully tested with no network or running services required
(test fixtures are frozen copies of real seeds):

```bash
.venv/bin/pytest          # 257 tests
```

---

## Project layout

| Path | What |
|---|---|
| `src/wizard/matching/` | Deterministic prefilter, article/annex extraction, tag mapping, evidence verification. |
| `src/wizard/dimensions.py` | The 11-dimension registry and framing. |
| `src/wizard/agents/` | Proposer/reviewer adapters, propose→review orchestrator, guards, runner (composition root). |
| `src/wizard/models/` | `SystemCard`, `CatalogueTool`, `ChecklistDoc`, `AssessmentPlan`, … |
| `src/wizard/api/app.py` | FastAPI app factory. |
| `src/wizard/server.py` | Runnable entrypoint — wires LLM client + live catalogue into the app. |
| `src/wizard/llm_client.py` | The single LiteLLM client (provider chosen by the model string). |
| `src/wizard/rendering.py`, `templates/` | Plan → HTML/PDF report. |
| `src/wizard/config.py` | `RunConfig` policy knobs (defaults < TOML < env < per-request). |
| `knowledge/`, `skills/` | Domain-knowledge and optional skills document bases. |

---

## License & governance

Licensed under the Apache License 2.0 — see [`LICENSE.md`](LICENSE.md),
[`NOTICE.md`](NOTICE.md), [`GOVERNANCE.md`](GOVERNANCE.md), and
[`CONTRIBUTING.md`](CONTRIBUTING.md).

This product was originally developed as the **AISC** project at the Université
du Luxembourg and the Luxembourg Institute of Science and Technology (SnT,
SerVal Research Group). © 2024–2026.
