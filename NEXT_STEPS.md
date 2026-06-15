# Wizard — Next Steps & Handoff

_Last updated: 2026-06-13._

---

## Unified multi-provider LLMs via LiteLLM + config file (2026-06-13)

**Every model runs through LiteLLM** — there is no vendor-specific client path.
The model string carries the provider, so the agents are not tied to Anthropic.
**The model is chosen in a TOML config file** (`wizard.toml`, override path with
`WIZARD_CONFIG_FILE`); **API keys stay in `.env`** (LiteLLM reads the one matching
the model's provider). Precedence: defaults < `wizard.toml` < `WIZARD_*` env <
per-run API override.

- **`llm_client.py`** — `LiteLLMClient` is the single client. It exposes the
  **same** duck-typed surface the agents already use (`messages.parse(...) ->
  .parsed_output`) over `litellm.completion`: content blocks flattened,
  `output_format`→`response_format`, `effort`→`reasoning_effort`, `thinking`
  ignored, `drop_params=True` so provider-unsupported params are dropped not raised.
- **`config.py`** — `DEFAULT_MODEL = "anthropic/claude-opus-4-8"`; `from_file`
  (TOML via stdlib `tomllib`, no new dep) and `load(env, path)` merging file<env.
  No `provider` field — the model string is the single source of truth.
- **`server.py`** — `_build_client()` always returns `LiteLLMClient()`; the key
  check is delegated to LiteLLM at call time.
- **`wizard.toml`** (committed default) + **`.env.example`** document the model
  strings and their keys.

To run on e.g. OpenAI: set `model = "openai/gpt-4o-mini"` in `wizard.toml` and
`OPENAI_API_KEY=…` in `.env`. To stay on Claude: `model = "anthropic/claude-opus-4-8"`
+ `ANTHROPIC_API_KEY`. The agents are unchanged either way.

Caveat: the codebase's prompt blocks still carry `cache_control`, but the
LiteLLM path flattens them, so Anthropic prompt-caching/extended-thinking are not
re-implemented here — a possible future refinement if the cache savings matter.

---

## Dimension-first recommendations + PDF export (2026-06-13)

The recommendation axis is now the **trustworthiness dimension**, conditioned on
the system's technology + context, with a downloadable PDF report. All phases of
`DIMENSION_PLAN.md` landed test-first (suite: **227 passed**). Pipeline now:

```
prefilter → FRAME dimensions (LLM) → propose→review loop → enrich items with
dimensions → group into DimensionAssessment + apply D2 floor → AssessmentPlan
                                                                   └─ GET /api/plans/{id}/pdf (Jinja2 + WeasyPrint)
```

- **`wizard/dimensions.py`** — registry of the 11 dimensions (label/order/
  `applies_to`/`requires_control`), slug normalisation (well-being + "and"-infix
  aliases), `dimension_slugs_from_tags`.
- **`models/plan.py`** — `ProposedItem.dimension_slugs`, `DimensionFrame`/
  `DimensionFraming` (framer I/O), `DimensionAssessment`, `AssessmentPlan.dimensions`
  (additive — legacy item lists/coverage unchanged), `build_dimension_assessments`
  (status derivation + D2 floor).
- **`agents/llm.py`** — `LLMDimensionFramer` (technology/sector conditioning);
  framer + proposers inject matching skill bodies into the cached stable block.
- **`agents/orchestrator.py`** — optional `framer` + `high_risk`; frames up front,
  assembles dimensions after review. No framer ⇒ identical legacy behaviour.
- **`config.py`** — `FloorConfig` (`enabled` toggle only; env `WIZARD_FLOOR_ENABLED`).
  The high-risk sector *list* is domain knowledge in the document base
  (`knowledge/high-risk-sectors.md`), loaded by `wizard/knowledge.py` and injected
  into the runner — never in config.
- **`skills.py`** + `skills/` — `SkillsLoader` (md + YAML frontmatter, routed by
  `(dimension, ai_type, sector)`). **Disabled by default** until real files land;
  point `WIZARD_SKILLS_DIR` at a folder to enable.
- **`rendering.py`** + `templates/` — `render_plan_html` / `render_plan_pdf`.
- **Catalogue `WizardInfo.tsx`** — dimension-grouped results + **Download PDF**.

**Decisions taken (2026-06-13, with Alessio's go-ahead):**
- **Skills** — a real starter library now ships in `skills/` (8 files: robustness
  for LLM/agentic and for tabular/predictive, fairness, privacy, transparency,
  human oversight, risk-management, record-keeping), grounded in the relevant EU
  AI Act articles and routed by the catalogue's real ai_type/sector slugs. A test
  asserts they parse, route, and use known dimension slugs. Replace/extend freely.
- **`applies_when`** — **advisory only**, not used for routing (free text is too
  brittle to gate on); it is surfaced into the injected guidance so the model
  knows when it bites. Hard routing stays on `ai_types`/`sectors`.
- **High-risk sectors (D2)** — moved out of config into the **document base**
  (`knowledge/high-risk-sectors.md`), a citable Annex III mapping maintained by a
  compliance reviewer. `wizard/knowledge.py` loads it; `wizard.toml` keeps only the
  `[floor] enabled` on/off switch. A test guards that the shipped slugs are real
  catalogue vocabulary and that horizontal non-high-risk sectors are excluded.

**Still open (natural next refinement, not blocking):**
- The framer's `already_addressed`/`residual_gaps` are consumed at assembly but do
  not yet *steer* the proposer's per-dimension selection prompt — feeding them in
  is the next improvement.

---

_Earlier (2026-06-12):_

Working notes for what's done, what's next, and how to pick it back up — for
**Alessio** and for **Claude**. Edit freely; the "Your notes" section at the
bottom is yours.

---

## Where we are (2026-06-12)

The **full LLM pipeline is wired end to end**, driven from the catalogue UI:

```
catalogue /wizard page  ──upload system card──▶  POST :8090/api/plans/from-card
   └─ frontend/src/pages/WizardInfo.tsx              └─ wizard.server (composition root)
                                                         ├─ clients/catalogue_http.py  (live catalogue :8001 → tests + checklists)
                                                         ├─ agents/runner.WizardPlanRunner  (prefilter → propose → review)
                                                         └─ anthropic.Anthropic()  (claude-opus-4-8)
```

Built this session (all **additive** — 139 offline tests still green):

- `src/wizard/clients/catalogue_http.py` — loads `GET :8001/tool/?detailed=true`,
  splits into `CatalogueTool` (tests) vs `ChecklistDoc` (controls) by the `type`
  tag. Verified live: 20 tests, 17 checklists.
- `src/wizard/api/app.py` — added `POST /api/plans/from-card` (accepts an
  uploaded card, no qualification lookup) + CORS.
- `src/wizard/server.py` — composition root + uvicorn entry on `:8090`. Reads
  `ANTHROPIC_API_KEY` from env or `wizard/.env`.
- `pyproject.toml` + `.venv` — added/installed `anthropic`, `httpx`, `uvicorn`.
- Catalogue frontend: `WizardInfo.tsx` (upload → run → render plan), bundled
  `frontend/public/sample-system-card.json`, `/wizard` route, and the grayed
  "Recommended" sidebar toggle with the info popup linking here.

**Decision made:** controls/checklists come from the **catalogue's own controls
tab** (loaded over `:8001`), not the separate aisc controls Postgres DB the
original SPEC §2.3 describes. Reason: it's what the catalogue UI shows and what
"recommend tests *and* controls" means in this context, and it needs no DB role.

---

## ▶ For Alessio — do these next

1. **Place the API key** (one line, git-ignored):
   ```
   printf 'ANTHROPIC_API_KEY=sk-ant-...\n' > /home/listuser/wizard/.env
   ```
2. **Start the wizard** (or ask Claude to):
   ```
   cd /home/listuser/wizard && PYTHONPATH=src .venv/bin/python -m wizard.server
   ```
3. **Test it:** open the catalogue `/wizard` page → "Try a sample card" → confirm
   real recommendations come back. (Catalogue dev server: `:3007`; backend `:8001`.)
4. **Decisions to make** (these gate the next build phase):
   - **Exports** — should a finalized plan push into the Execution Engine and
     Controls Engine? If yes, which endpoints? (SPEC Q3 — needs a look at
     `aisc/apps/backend`. If none fit, export a config file the webapp imports.)
   - **Persistence** — plans are in-memory only today. Need the `wizard` Postgres
     DB? (post-demo item in SPEC.)
   - **Frontend** — keep this thin React page, or the SPEC's Jinja+HTMX 3-screen
     flow (pick → run+SSE progress → export)? (SPEC Q1.)
   - **Reviewer model / lenses** — default is single-reviewer on `claude-opus-4-8`.
     Want the multi-lens panel on by default? (`WIZARD_REVIEW_LENSES=relevance,coverage,parsimony`.)

---

## ▶ For Claude — pick up here

**Gotchas (read before touching this):**
- Package isn't pip-installed; run with `PYTHONPATH=src` (pytest uses
  `pythonpath=["src"]` in pyproject).
- The venv has **no pip** — use `uv pip install ...` to add deps.
- `WizardPlanRunner` loads catalogue data **once at startup** — restart the
  wizard after catalogue changes, or add a refresh endpoint.
- `item_id` in a plan is the catalogue **slug** (prefilter keys candidates by
  `item.slug`), not the numeric tool id. Exports must resolve slug → id.
- LLM agent interface in `agents/llm.py` is correct for the current SDK
  (`messages.parse(output_format=…)`, `thinking={"type":"adaptive"}`,
  `output_config={"effort":"high"}`) — don't "fix" it.
- Catalogue runs on host port **:8001** (`:8000` is taken by `aisc-backend`).
  The catalogue frontend defaults its API to `:8000`, so the dev server must be
  started with `VITE_API_URL=http://localhost:8001`.

**Still deferred from the SPEC (next candidates, roughly in order):**
1. **Exports** (SPEC §6) — Execution Engine config draft + Controls `Submission`
   drafts. Blocked on Q3. Highest user value once the demo flow is approved.
2. **Persistence** — replace `InMemoryPlanStore` with the `wizard` Postgres DB so
   plans/transcripts survive restarts.
3. **SSE progress** (`GET /api/plans/{id}/events`) + background execution — the
   run is synchronous inside the request today; fine for MCAS-sized catalogues.
4. **MCP server** (SPEC §4) — only needed if agents should *search* the catalogue
   from inside the loop rather than select from the prefiltered candidate set.
5. **Qualification HTTP client** — to support the pick-a-qualification flow
   (`POST /api/plans` by `qualification_id`) in addition to upload.
6. **Live integration test** (`WIZARD_LIVE_TESTS=1`) — full MCAS run asserting
   structural invariants (every open_issue covered or gapped; all ids resolvable).
7. Caddy `/wizard` route + homepage link-up (cosmetic; deferred in SPEC §0).

**Key files:** `server.py` (wiring), `clients/catalogue_http.py` (data), `api/app.py`
(routes), `agents/runner.py` (pipeline), `agents/llm.py` (SDK calls),
`config.py` (knobs). Frontend: `catalogue2/frontend/src/pages/WizardInfo.tsx`.

---

## Your notes

_(Alessio — jot anything here.)_

-
