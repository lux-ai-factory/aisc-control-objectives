# Dimension-First Recommendations + PDF Export — Development Plan

Status: **proposed, for review** · Owner: Alessio · Drafted 2026-06-13

This plan makes **trustworthiness dimensions** the organising axis of the Wizard's
recommendations and adds a downloadable PDF report. Today the pipeline reasons over a
flat list of items keyed by EU AI Act articles; the goal is to reason **per dimension**,
conditioned on the system's **technology and context**, recommend only what is needed to
close each dimension's residual gap, and explain it — on screen and in a PDF.

> Sister change in the catalogue repo (`/home/listuser/catalogue`): on-screen rendering of
> the dimension report + a "Download PDF" button. The catalogue stays a thin client; all
> recommendation logic and PDF rendering live here in the Wizard service.

---

## 1. Current mechanism (baseline)

```
prefilter (deterministic)  →  propose→review loop (LLM)        →  assemble plan
  ai_type/sector/article        test & checklist proposers          coverage keyed by
  overlap scoring               + 4 guards + MultiLensReviewer       EU AI Act article keys
  (matching/prefilter.py)       (relevance/coverage/parsimony)       (orchestrator.py)
```

Signals already fed to the agents (`agents/llm.py::_card_digest` / `_candidate_digest`):
- **Technology**: `target_systems` (ai_type), per-candidate `ai_type_overlap`.
- **Context**: `sectors`, `target_use_case`, `findings`, `open_issues`, `sector_overlap`.
- Per-candidate `article_overlap`, `tags`, checklist `control_topic`.

Gaps vs. the goal:
1. **Dimension is not first-class.** `ChecklistDoc.dimension_slug` exists; tools bury the
   dimension inside `tag_slugs`; `ProposedItem` and `AssessmentPlan` have no dimension; the
   coverage matrix is keyed by article, not dimension.
2. **No per-dimension sufficiency reasoning** — nothing decides "this dimension is already
   adequately tackled by the system as described, so recommend nothing / only the gap."
3. **No skills ingestion** — no channel for domain-knowledge files that tell the agents what
   to test per dimension/technology and when coverage is adequate.

---

## 2. Decisions (made, open to your override)

| # | Question | Decision | Rationale |
|---|----------|----------|-----------|
| D1 | Source of truth for the dimension taxonomy | **Derive from the catalogue tags** (`section:"dimension"`, already arriving via `catalogue_http`), backed by a small `wizard/dimensions.py` registry for labels + sub-area grouping. | Keeps Wizard and catalogue aligned automatically; the registry only adds labels/ordering, not a second taxonomy to drift. |
| D2 | Who decides a dimension is "already adequately tackled"? | **Hybrid.** The LLM judges `already_addressed` from card evidence, but a **deterministic floor** can veto a "covered" verdict: for high-risk sectors and for dimensions flagged `requires_control`, a dimension cannot be auto-marked `covered` without at least one accepted control checklist. | Pure-LLM sufficiency is too easy to talk itself out of a control on a high-risk system; the floor is a safety backstop, configurable per deploy. |
| D3 | Does this replace the propose→review loop? | **No — it wraps it.** Add a framing/assessment stage in front and a dimension-grouped output model after; the candidate→propose→guard→review loop stays. | Preserves the tested guards, multi-lens review, and prompt-cache design; lowest-risk path. |
| D4 | Skills file format | **Markdown + YAML frontmatter** (`dimension`, `ai_types`, `sectors?`, `applies_when?`), loaded from `wizard/skills/`, routed by `(dimension, ai_type, sector)` into the **stable cached** prompt block. | Human-authorable, routable, cache-friendly. **Locked against your real files before Phase 2 ships.** |
| D5 | Build order | **0 → 1 first** (model + mechanism), **stub skills**, then **3 → 4** (PDF + frontend), then **2** (skills) once files exist, then **5** (tests alongside each phase). | The new output model is the dependency for PDF and frontend; skills can land last without reshaping anything. |
| D6 | Backward compatibility of `AssessmentPlan` | **Additive.** Keep `tests/datasets/checklists/coverage/gaps`; add `dimensions: list[DimensionAssessment]` and derive the legacy fields from it. | Existing API consumers, `finalize`, and tests keep working. |

The catalogue's dimension taxonomy is **not uniform across tests and controls** — the registry
seed for `wizard/dimensions.py` is the **union of 11**:

- **Shared 6 (tests + controls):** Human Agency and Oversight · Technical Robustness and Safety ·
  Privacy and Data Governance · Transparency · Diversity, Non-discrimination and Fairness ·
  Societal and Environmental Wellbeing.
- **Control-only 5 (governance/process):** Accountability · Quality Management · Risk Management ·
  Technical Documentation · Record-keeping.

Implications for the registry/report:
- A dimension may legitimately have **controls but no tests** (the 5 control-only dimensions) — the
  report must render a dimension with only controls, and the framer must not expect tests there.
- **Slug normalisation required:** tests label it "Societal and Environmental **Wellbeing**", controls
  "Societal and Environmental **Well-being**" — both must resolve to one dimension slug.
- Each registry entry records which item types it applies to (`tests`, `controls`, or both).

---

## 3. Phases

### Phase 0 — Dimension as a first-class concept
- **`wizard/dimensions.py`** (new): registry `slug → {label, order, sub_areas[], requires_control: bool}`,
  seeded from the catalogue's dimension list. Helper to map a tool's `tag_slugs` → dimension slug(s).
- **`models/catalogue.py`**: add `CatalogueTool.dimension_slugs` (derive from `tag_slugs` via registry);
  `ChecklistDoc.dimension_slug` already exists.
- **`models/plan.py`**: add `dimension_slug: str` (+ resolved label) to `ProposedItem`.
- **`agents/orchestrator.py` / proposer**: populate `dimension_slug` where the catalogue item is in scope.
- Tests: registry mapping, tool→dimension extraction, enrichment survives into the plan.

### Phase 1 — Dimension-first mechanism *(core)*
- **`DimensionFramer`** (new stage; deterministic scope + one LLM call): from technology + sector +
  findings, decide which dimensions are **in scope**, and per dimension summarise what the card
  **already does** (evidence-backed). Technology conditioning lives here (NLP/LLM → robustness leans
  prompt-injection/jailbreak; tabular → drift/calibration; agentic → excessive-agency; high-risk sector
  → stricter oversight/transparency).
- **Per-dimension proposal**: for each in-scope dimension, the proposer emits `already_addressed` vs
  `residual_gap` and selects the **minimal** items to close the residual gap — explicitly instructed
  **not** to recommend items for a dimension already adequately covered. (Today's `parsimony` lens
  becomes a per-dimension constraint.)
- **Deterministic floor (D2)** applied after review: high-risk sector / `requires_control` dimensions
  cannot end `covered` without an accepted control.
- **Reviewer lenses** reframed per dimension: relevance (tech/sector fit), coverage (residual gap
  actually closed), parsimony (no over-recommendation).
- **New model `DimensionAssessment`** in `models/plan.py`:
  `{dimension_slug, label, in_scope, relevance_reason, already_addressed[], residual_gaps[],
  recommended_item_ids[], status: covered|partial|gap}`.
  `AssessmentPlan` gains `dimensions: list[DimensionAssessment]`; legacy fields derived from it (D6).
- Tests: in-scope selection, sufficiency → minimal recommendation, floor veto, output model shape.

### Phase 2 — Skills ingestion *(needs your files; stubbed until then)*
- **`wizard/skills/`** + **`SkillsLoader`**: parse frontmatter, index by `(dimension, ai_type, sector)`,
  inject matching skill bodies into the framer/proposer **stable cached block** keyed by
  dimension+technology (preserves the SPEC §5.4 cache design).
- Frontmatter contract (D4):
  ```yaml
  ---
  dimension: technical-robustness-and-safety
  ai_types: [natural-language-processing, agents-and-agentic-systems]
  sectors: [finance]          # optional; empty = all
  applies_when: "system exposes an LLM endpoint"   # optional, human-readable
  ---
  Guidance: what to test, what counts as adequate coverage, when to raise
  priority, what to skip for this dimension + technology.
  ```
- **Blocked on:** a sample of your real skills files to lock the contract. Until then, Phase 1 ships
  with skills disabled (empty loader) and behaves identically to the unskilled path.

### Phase 3 — PDF endpoint *(downstream)*
- `GET /api/plans/{plan_id}/pdf` in `api/app.py` → Jinja2 `plan.html.j2` (+ `styles.css`) →
  **WeasyPrint** → `application/pdf`. Reuse the `aisc/qualification` `system_card_renderer` shape
  (`renderer.py` + `template_engine.py`); add `weasyprint` + `jinja2` to deps.
- Template = cover (from system card) → **per dimension: status, what's already addressed, residual
  gaps, then the recommended tools/controls each with rationale**.
- Tests: template renders for a sample plan; WeasyPrint smoke test produces non-empty PDF bytes.

### Phase 4 — Catalogue frontend (`/home/listuser/catalogue`)
- Expand `WizardInfo.tsx` results into the dimension → items layout (mirror of the PDF).
- Add **"Download PDF"** button → `{WIZARD_URL}/api/plans/{plan_id}/pdf`.
- Optional: enable the currently-disabled "Recommended" sidebar toggle once this lands.

### Phase 5 — Tests & docs
- Extend the existing suite (`test_orchestrator`, `test_api`, `test_finalize_consistency`,
  `test_bugfixes`) per phase above.
- Update `SPEC.md` / `NEXT_STEPS.md` to reflect the dimension-first model.

---

## 4. Touchpoints summary

**Wizard repo**
- New: `wizard/dimensions.py`, `wizard/skills/` + loader, `plan.html.j2` + `styles.css`, PDF renderer module.
- Changed: `models/plan.py` (ProposedItem.dimension, DimensionAssessment, AssessmentPlan.dimensions),
  `models/catalogue.py` (tool dimension extraction), `agents/orchestrator.py` + `agents/llm.py`
  (framer stage, per-dimension prompts, floor), `api/app.py` (PDF route), `config.py` (skills dir,
  high-risk-sector floor toggle), deps (`weasyprint`, `jinja2`).

**Catalogue repo**
- Changed: `frontend/src/pages/WizardInfo.tsx` (dimension rendering + Download PDF). Already in place:
  route + sidebar entry.

---

## 5. Open items needing your input
1. **Skills files** — drop a sample (or the real set) and point me at the path; I'll fold their actual
   shape into D4 before building the loader.
2. **High-risk sectors list (D2 floor)** — confirm which sectors trigger the "cannot auto-cover without
   a control" rule (default proposal: the EU AI Act Annex III high-risk areas the catalogue already tags).
3. Anything in §2 to override.
