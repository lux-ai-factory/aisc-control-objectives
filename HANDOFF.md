# Handoff — Wizard (dimension-first + multi-provider + catalogue integration)

Last updated: 2026-06-14 · Backend suite: **247 passed**, ruff clean.

This is the running session dump. Older design notes are in `DIMENSION_PLAN.md`
and the dated sections of `NEXT_STEPS.md`.

---

## Where things stand

A working end-to-end system, tested live against OpenAI through the catalogue UI:

```
catalogue /wizard page ──upload card──▶ POST :8090/api/plans/from-card ──▶ LiteLLM ──▶ provider
  (WizardInfo.tsx)                         (wizard.server, FastAPI)         (any model)
        │ result persisted in browser (localStorage)
        ▼
  catalogue "Recommended" toggle ── filters the tool grid to the active plan's items
```

All work below is **committed to none of it yet** — nothing has been `git`-committed
this session; review and commit when ready (both the `wizard` repo and the
catalogue's `WizardInfo.tsx` / `Catalogue.tsx` / `CatalogueSidebar.tsx` / new
`services/wizardPlans.ts`).

---

## Done this session

### Wizard service (`/home/listuser/wizard`)
1. **Dimension-first pipeline (Phases 0–5 of `DIMENSION_PLAN.md`)** — `dimensions.py`
   registry (11 dims), `DimensionFramer` stage wrapping the propose→review loop,
   `DimensionAssessment` + `AssessmentPlan.dimensions`, D2 deterministic floor,
   PDF export (`GET /api/plans/{id}/pdf`, Jinja2 + WeasyPrint).
2. **Skills document base** (`skills/`) — `SkillsLoader` (md + YAML frontmatter,
   routed by dimension/ai_type/sector); 8 real starter skills shipped.
   `applies_when` is advisory-only (surfaced to the model, not used for routing).
3. **High-risk sectors → document base** (`knowledge/high-risk-sectors.md`) — the
   Annex III mapping is domain knowledge loaded by `knowledge.py`, **not** config.
   `wizard.toml [floor]` keeps only the `enabled` toggle.
4. **Multi-provider via LiteLLM (unified)** — there is **no** native-Anthropic
   path. Every model runs through `LiteLLMClient` (`llm_client.py`); the model
   string carries the provider (`openai/gpt-4o`, `anthropic/claude-opus-4-8`, …).
   Model is chosen in `wizard.toml`; API keys live in `.env` (LiteLLM reads the
   provider-matching key). `RunConfig.load()` merges defaults < TOML < env.
5. **Cleanup pass** — removed orphan `transcript_ref`; ruff modernization;
   extracted `frontmatter.py`; added the `LLMClient` Protocol so every external
   seam is typed (`Proposer/Reviewer/Framer`, `QualificationProvider/PlanRunner`,
   `LLMClient`).
6. **Dataset-pairing crash fix** — removed the `ProposedItem` model-validator that
   crashed the whole run when a weaker model emitted an unpaired dataset. Pairing
   is now enforced only by the deterministic guard (drop + warning), consistent
   with how hallucinated IDs / bad evidence are handled.

### Catalogue frontend (`/home/listuser/catalogue/frontend`)
7. **Result persistence + run history** — `services/wizardPlans.ts` (shared store)
   persists the selected plan + a history list in `localStorage`; survives refresh
   **and** wizard restart. `WizardInfo.tsx` restores on load and shows "Previous
   runs".
8. **"Recommended" wired to the active plan** — the catalogue sidebar's Recommended
   toggle is now interactive whenever a plan exists; toggling it filters the tool
   grid to the active plan's recommended slugs. The history marks which run is
   **● Active** (drives Recommended); clicking another run switches it. Synced
   same-tab (custom event) + cross-tab (`storage`) + on `focus`.

### Verified live
- OpenAI structured-output path through LiteLLM (cheap pre-flight + full runs).
- `gpt-4o-mini`: pipeline works but thin plans (guards strip its bad output).
- `gpt-4o`: fuller plan — 5 tests + 2 checklists, 4 dimensions with recommendations.
- PDF export returns valid `%PDF` bytes.

---

## How to bring it back up

```bash
# 1. catalogue backend (:8000) + frontend (:3000)  — images already built
cd /home/listuser/catalogue && docker compose up -d backend frontend
#    backend re-seeds its sqlite DB on boot; wait until :8000/tool/?detailed=true → 200

# 2. wizard service (:8090), pointed at the catalogue backend on :8000
cd /home/listuser/wizard && source .venv/bin/activate
PYTHONPATH=src WIZARD_CATALOGUE_URL=http://localhost:8000 WIZARD_PORT=8090 python -m wizard.server
```
- Model: `wizard.toml` → currently **`openai/gpt-4o`**. Key: `.env` → `OPENAI_API_KEY`.
- Open http://localhost:3000 → Wizard → run a card → flip "Recommended" in the catalogue.
- After editing `WizardInfo/Catalogue/CatalogueSidebar`, rebuild the frontend image:
  `cd /home/listuser/catalogue && docker compose up -d --build frontend`
  (host `npm run dev` currently fails — `node_modules` has root-owned files from a
  prior Docker build; use the Docker image, or `sudo chown` the dir to fix dev mode.)
- Tests: `cd /home/listuser/wizard && source .venv/bin/activate && python -m pytest -q`

---

## To do (next session)

**Quality / behaviour**
- Plans finish `status: "draft"`, not `"reviewed"` — the multi-lens review doesn't
  converge in 3 rounds. Try `max_rounds` ↑ in `wizard.toml`, and/or feed the
  framer's `already_addressed`/`residual_gaps` into the **proposer** prompt so it
  selects per-dimension (today the framing is only consumed at assembly). This is
  the highest-value open item.
- Consider enabling the multi-lens reviewer by default (`review.lenses`).

**Frontend UX**
- Tab (Tests/Controls) vs "Recommended" compose: if the active plan recommends
  only one type while the other tab is selected, the grid looks empty. Decide
  whether Recommended should override the tab, or show a hint.
- PDF for an **old** run after a wizard restart 404s (rendered from server memory).
  Add `POST /api/plans/render-pdf` that renders from the plan the browser holds, so
  history PDFs work regardless of restarts.

**Durability / architecture**
- Persistence is browser-local + wizard in-memory only. Server-side durable store
  (the planned `wizard` Postgres DB) is still deferred — needed for cross-device
  history and surviving cache clears.
- The dimension registry (`dimensions.py`) is still hardcoded domain knowledge;
  for consistency with the high-risk-sectors move it could live in the document
  base too (bigger change — registry is imported widely). Offered, not done.

**Inputs still wanted from Alessio**
- Real skills files (current 8 are a sensible starter set — replace/extend).
- Confirm/adjust the Annex III high-risk sector list in
  `knowledge/high-risk-sectors.md`.

**Housekeeping**
- Frontend: no tests added for the new persistence/Recommended wiring; pre-existing
  frontend test files are missing `vitest` globals (unrelated, untouched).
- 83 `E501` long-line lint warnings left deliberately (no line-length config).
- Nothing committed yet — review + commit both repos.
