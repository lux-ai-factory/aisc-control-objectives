# Wizard hardening — work-package specs

Status: **draft for review**. Follows `SPEC.md`; same constraints apply
(§0 strictly additive, demo-safe: everything in Phase A is offline-only,
TDD throughout). Each work package lists exact behavior, interface changes,
test plan, and acceptance criteria. Decisions I need from you are marked
**[DECISION]** and collected in §7.

Phasing:

- **Phase A — offline** (can run any time, no services, no network): WP0, WP1, WP2, WP3, WP4-draft.
- **Phase B — live** (needs the running stack + `ANTHROPIC_API_KEY`): WP4-live, WP5.

---

## WP0 — Run configuration (user-chosen policy, not architecture)

All formerly "decision" knobs become a typed `RunConfig` the user controls.
**Precedence: per-run API override > environment > defaults.** The effective
config is echoed verbatim on every `AssessmentPlan` (`run_config` field) so a
plan is always interpretable: you can see exactly which policies produced it.

`wizard/config.py`:

```python
class GuardsConfig(BaseModel):
    evidence: Literal["drop", "demote", "off"] = "drop"        # was D1
    coverage_claims: Literal["strip", "off"] = "strip"
    dataset_pairing: Literal["enforce", "off"] = "enforce"

class ReviewConfig(BaseModel):
    lenses: list[Literal["relevance", "coverage", "parsimony"]] = []  # was D2; [] = single reviewer
    reviewer_model: str | None = None                          # was D3; None = same as model

class RunConfig(BaseModel):
    model: str = "claude-opus-4-8"
    max_rounds: int = Field(3, ge=1, le=5)
    guards: GuardsConfig
    review: ReviewConfig
```

| Knob | Env var | Per-run JSON | Values |
|---|---|---|---|
| model | `WIZARD_MODEL` | `config.model` | any Claude model id |
| max rounds | `WIZARD_MAX_ROUNDS` | `config.max_rounds` | 1–5 |
| evidence policy | `WIZARD_EVIDENCE_POLICY` | `config.guards.evidence` | `drop` / `demote` (→ priority `optional`) / `off` |
| coverage claims | `WIZARD_COVERAGE_CLAIMS` | `config.guards.coverage_claims` | `strip` / `off` |
| dataset pairing | `WIZARD_DATASET_PAIRING` | `config.guards.dataset_pairing` | `enforce` / `off` |
| review lenses | `WIZARD_REVIEW_LENSES` | `config.review.lenses` | comma list of `relevance,coverage,parsimony`; empty = single reviewer |
| reviewer model | `WIZARD_REVIEWER_MODEL` | `config.review.reviewer_model` | model id; unset = same as `model` |
| golden recall threshold | `WIZARD_GOLDEN_RECALL` | n/a (eval-only) | float, default 0.8 |

API surface:
- `POST /api/plans` body gains optional `config` (partial, deep-merged into
  the base config and re-validated).
- `GET /api/config` returns the effective base config — the UI renders its
  settings form from this.

Not configuration: golden-plan *content* review (inherently a human judgment,
stays a process step) and Phase B timing (operational).

---

## WP1 — Deterministic guards (highest leverage, do first)

All three guards run inside the orchestrator **after the proposers and before
the reviewer**, exactly like the existing hallucinated-ID guard. Guard
findings are (a) recorded as plan `warnings`, (b) appended to the reviewer
payload as `guard_report` so the reviewer sees what was already stripped.

### G1 — Evidence verification

New module `wizard/matching/evidence.py`:

```python
def find_quote(quote: str, corpus: str) -> bool
def verify_evidence(item: ProposedItem, card: SystemCard) -> list[str]  # verified quotes
```

Matching rule, in order:
1. **Normalized substring**: lowercase, collapse all whitespace runs to one
   space, strip the quote of leading/trailing punctuation. If the normalized
   quote is a substring of the normalized corpus → verified.
2. **Fuzzy fallback**: `difflib.SequenceMatcher` best-window ratio ≥ **0.90**
   against the corpus (window = quote length ± 20%). Catches minor
   elision/punctuation drift, still rejects paraphrase.

Corpus = card `overview` + `description` + `target_use_case` + `target_users`
+ all finding `summary`/`points` + `open_issues` + (after WP2-M3) the 13
answer texts.

Orchestrator behavior:
- Evidence entries that fail verification are **removed** from the item;
  warning `evidence-not-found: <item_id>: "<first 80 chars>"`.
- An item left with **zero** verified evidence entries is **dropped before
  review** (same severity as a hallucinated ID — verbatim evidence is a hard
  prompt requirement, so an item with none is non-compliant). Warning
  `evidence-empty: <item_id>`.

Tests: exact match; whitespace/case drift; punctuation drift passing fuzzy;
paraphrase rejected; item dropped at zero evidence; partial strip keeps item;
warnings recorded; reviewer payload contains `guard_report`.

### G2 — Coverage-claim plausibility

Rules applied to every `covers` key of every proposed item:
1. **Card-known**: key must ∈ `card.article_keys()`. Covering an article the
   card never references is meaningless for gap accounting → claim stripped,
   warning `coverage-claim-unknown-key: <item_id>: <key>`.
2. **Checklist-supported** (checklist items only): key must ∈ the checklist's
   own `article_keys()`. A checklist cannot cover an article none of its
   questions cite → claim stripped, warning
   `coverage-claim-unsupported: <item_id>: <key>`.
   (Not applied to tests: tool article metadata is too sparse to be
   authoritative — the reviewer owns that judgment.)

An item whose claims are all stripped stays in the plan (it may still be a
valid `optional` pick) but contributes nothing to coverage; the gap
computation then does its existing job.

Tests: each rule, strip-vs-keep, gap recomputation after stripping, warnings.

### G3 — Dataset pairing

Schema change in `wizard/models/plan.py`:

```python
class ProposedItem(BaseModel):
    ...
    paired_test_id: str | None = None  # REQUIRED when item_type == "dataset"
```

- pydantic `model_validator`: `item_type == "dataset"` and
  `paired_test_id is None` → `ValidationError` (so the LLM gets a structured
  retry at the SDK layer, not a bad item downstream).
- Orchestrator guard: a dataset's `paired_test_id` must reference a test item
  **present in the same merged proposal** (post-ID-guard). Else drop with
  warning `dataset-unpaired: <item_id>`.
- Proposer system prompt updated to state the field explicitly.

Tests: validator rejects unpaired dataset at parse time; orchestrator drops
dataset whose pair was itself dropped (cascading case); happy path.

---

## WP2 — Matching depth

### M1 — Subcategory keyword scoring (tools)

The card knows the system is *RAG*; the catalogue has *RAGAS*. Add a fourth
score component at **subcategory** granularity.

`wizard/matching/prefilter.py`:
- For each target-system slug `category:subcategory`, tokenize the
  subcategory on `-`; keep tokens with `len >= 4` **or** present in an
  explicit allowlist of short domain tokens (`{"rag", "ner", "ocr", "asr"}`);
  drop tokens in an explicit stopword set
  (`{"data", "model", "models", "system", "systems", "analysis", "general",
  "based", "classification", "regression"}` — tuned in tests, final set lives
  in code).
- A subcategory **hits** a tool if any of its kept tokens appears in
  `tool.name + " " + tool.description` (case-insensitive, word-boundary).
- `score += w_subcat * |distinct subcategories hit|`, default `w_subcat = 2.0`
  (in `Weights`); add `subcategory_overlap: set[str]` to `ScoredCandidate`
  and to the candidate digest the agents see.

Golden additions (MCAS): RAGAS gains a subcategory hit via "rag"/"retrieval";
ranking of the existing top tools must not regress (assert the prior top-5
set is preserved modulo insertion).

### M2 — Topic scoring (checklists)

Fixes "Risk Management scores 0". New module `wizard/matching/topics.py`:

```python
TOPIC_MAP: dict[str, set[str]]  # question group / finding title slug -> control_topic slugs
def card_topics(card: SystemCard) -> set[str]
def topic_overlap(card: SystemCard, checklist: ChecklistDoc) -> set[str]
```

- Both sides slugified with the existing `_slugify` (promoted to a shared
  `wizard/matching/slug.py`).
- `card_topics` = topics mapped from (a) the 6 question groups that have at
  least one non-empty answer (needs M3) and (b) finding titles.
- Initial `TOPIC_MAP` (validated in tests against the fixture topic
  vocabulary — every mapped topic slug must exist in `controls_seed.json`):

| group / finding | control topics |
|---|---|
| `data` | data-and-data-governance, data-governance |
| `documentation` | records, logging, technical-documentation |
| `transparency` | transparency |
| `oversight` | human-oversight |
| `risk` | risk-management, accuracy, robustness, cybersecurity |
| `accountability` | quality-system-management, incidents-management, post-market-monitoring |

- Checklist score: `score += w_topic * |topic_overlap|`, default
  `w_topic = 1.0` (article overlap stays the stronger signal at 2.0);
  `topic_overlap` added to `ScoredCandidate` and the digest.

Golden additions (MCAS): Risk Management scores > 0; Transparency_Checklist
still ranks above it; unmapped/sector-foreign topics stay at 0.

### M3 — Use the 13 qualification answers

- Extract answers from `demo/seed/qualification/microcredit.sql` into
  `tests/fixtures/mcas_answers.json` (offline, read-only — same extraction
  pattern as the card).
- `SystemCard` gains `answers: list[QualificationAnswer]`
  (`tool_id`/`group`, `question_id`, `answer`), default `[]`;
  `SystemCard.from_card_json(raw, answers=None)` keeps the existing call
  signature working.
- Answers feed: (a) the G1 evidence corpus, (b) `card_topics` group presence
  (M2), (c) the proposer/reviewer payloads as `answers` (full text — they are
  short; ~13 × 1 paragraph).
- The live `QualificationProvider` already returns answers per SPEC §4
  (`get_system_card` = card + answers); only the offline fixture and model
  change are new.

---

## WP3 — Agent architecture

### A1 — Reviewer model override

`WIZARD_REVIEWER_MODEL` env var, default = `WIZARD_MODEL`. One-line change in
`WizardPlanRunner` + test asserting the override reaches the reviewer call
and not the proposers'.

### A2 — Perspective-diverse review (opt-in)

New `MultiLensReviewer` in `wizard/agents/llm.py` implementing the `Reviewer`
protocol:

- Lenses, each a system-prompt suffix on the existing reviewer prompt:
  - `relevance` — "is each item genuinely warranted by this card, this sector?"
  - `coverage` — "is every open issue and finding article addressed; what's missing?"
  - `parsimony` — "is anything redundant, overlapping, or disproportionate to
    the system's risk profile?"
- Each lens produces a full `Review` (separate API calls, separate contexts).
- **Merge rule** (deterministic): per item, final verdict = worst across
  lenses (`reject > revise > accept`; lenses that omit an item count as
  accept). `coverage_ok` = AND of all lenses. `notes_for_revision` =
  concatenation, each block prefixed `[lens]`.
- Config: `WIZARD_REVIEW_LENSES` env — comma list; **default empty = current
  single reviewer** (cost-neutral by default). `WizardPlanRunner` builds
  `MultiLensReviewer` only when the var is set.

Tests (stubbed clients): merge worst-verdict; AND of coverage; note labeling;
runner builds single vs multi from env.

### A3 — Reviewer sees full item detail (offline version)

Today the reviewer judges from the same 400-char digests the proposers saw.
Change: the reviewer payload gains `proposed_item_details` — for every
proposed id, the **full** model dump (all checklist questions with articles;
full tool description + tags + `target_legal_requirements`). Data is already
in the candidates; no MCP needed. The MCP-tool-using reviewer (agent fetches
on demand) stays a Phase B upgrade — this gets ~the same verification power
offline at zero architecture cost.

Test: reviewer call payload contains a full question list for a proposed
checklist; non-proposed candidates are *not* expanded (token control).

### A4 — Failure policy

- `AssessmentPlan.status` gains `"failed"`.
- `WizardPlanRunner.run` wraps the loop: any exception from an agent call
  (after the SDK's own retries) → plan with `status="failed"`, empty item
  lists, `warnings=[f"run-failed: {type(e).__name__}: {e}"]`, and
  `review_rounds` = rounds completed. Never a half-plan that looks finished.
- API: `POST /api/plans` returns the failed plan with HTTP **502** (body is
  the plan, so the UI can show what happened); `GET` endpoints serve it
  normally.

Tests: proposer raising → failed plan, 502, stored and retrievable; reviewer
raising in round 2 → `review_rounds == 1` recorded.

### A5 — Transcripts

- `wizard/agents/transcript.py`: `Transcript` accumulates per round
  `{round, proposals, guard_report, review}` (model dumps).
- Orchestrator fills it; `WizardPlanRunner` writes
  `$WIZARD_DATA_DIR/transcripts/<plan_id>.json` (env, default `./data`;
  created on demand) and sets `plan.transcript_ref` to the absolute path.
- The audit chain becomes: plan → transcript → exact agent inputs/outputs per
  round.

Tests: file written, ref set, content round-trips, contains guard report;
`WIZARD_DATA_DIR` honored (tmp_path in tests).

---

## WP4 — Golden evaluation

### E1 — MCAS golden plan

- `tests/golden/mcas_expected.json`:

```json
{
  "must_include": ["<tool/checklist slugs>"],
  "should_include": ["..."],
  "must_exclude": ["..."],
  "open_issue_coverage_required": true
}
```

- **Process**: I draft the golden from the card + catalogue (deterministic
  reasoning, documented inline as `_comment` fields); **you review and amend
  it** — it encodes the domain expert's judgment, not mine. [DECISION D4]
- New opt-in live test `tests/live/test_golden_mcas.py`
  (`@pytest.mark.skipif(not os.environ.get("WIZARD_LIVE_TESTS"))`):
  runs `WizardPlanRunner` with a real `anthropic.Anthropic()` client against
  the fixtures (still no local services needed — fixtures are local), asserts:
  - recall over `must_include` ≥ **0.8**
  - zero `must_exclude` present
  - every MCAS open issue covered or gapped
  - plan status `reviewed`
  - structural sanity (every id resolvable, every accepted item has evidence)
- Metrics printed (`precision`, `recall`, rounds, token usage from the SDK
  response `usage`) so prompt/model changes are comparable run-to-run.

### E2 — Second golden system (anti-overfit)

- Synthesize `tests/fixtures/medassist_system_card.json`: a plausible
  health-sector triage-support system (CV + NLP, sector `health`, open issues
  on Articles 9/15) — deliberately orthogonal to MCAS. Fixture-only; nothing
  is seeded into the live apps.
- Same golden format + live test. Drafted by me, reviewed by you.

Acceptance for WP4: both live tests green at least once, metrics recorded in
`tests/golden/RESULTS.md` (date, model, scores) as the baseline.

---

## WP5 — Live wiring & ops (Phase B — requires the running stack)

In dependency order:

1. **Investigation step** (read-only, 30 min): confirm how to *list*
   qualifications — the qualification app exposes `system-card.json` per id
   but no list route was found. Options: read-only query on the
   `qualification` DB (mirrors the controls pattern) vs adding nothing and
   entering ids manually in the UI. Outcome updates SPEC §2.1.
2. **Clients** (`wizard/clients/`): `CatalogueClient` (httpx,
   `GET /tool/?detailed=true`, `GET /tags/`), `QualificationClient`
   (system-card.json + answers via 1.), `ControlsReadClient` (SQLAlchemy,
   read-only role created by `scripts/setup_db.sql`). Each normalizes into
   the existing models — contract-tested against the live stack with
   recorded-response fallbacks (respx) for CI.
3. **Tag-map runtime validation** (SPEC §2.4): on startup, fetch `GET /tags/`
   and warn on unmapped categories. Test with respx.
4. **Persistence**: SQLAlchemy `plans` table (JSONB payload + indexed
   `qualification_id`, `status`, `created_at`), replacing `InMemoryPlanStore`
   behind the same interface; `create_all` on startup (alembic deferred).
5. **MCP server** (SPEC §4) + reviewer-with-tools upgrade of A3.
6. **UI** (SPEC §8) + SSE progress events.
7. **Exports** (SPEC §6): controls `Submission` drafts (direct insert, demo
   pattern); execution-engine target per SPEC Q3 investigation.
8. **First live end-to-end run** against the real stack = WP4 goldens + a
   manual click-through.

Everything here stays additive (§0): new DB role, new database, new
processes; zero edits to existing modules.

---

## §6 Execution order

```
Phase A (offline, start immediately):
  WP0                        (config plumbing — everything else reads from it)
  WP1 G1 → G2 → G3          (guards; ~independent, sequenced for shared plumbing)
  WP2 M3 → M2 → M1          (answers first — M2 depends on them)
  WP3 A4 → A5 → A1 → A3 → A2
  WP4 E1/E2 drafts          (golden files + live-test code, awaiting your review)
Phase B (after demo period, live stack available):
  WP5 1..8, then WP4 live runs to set the baseline
```

Rationale: guards raise the floor before anything else; answers/topic/subcat
matching deepen the candidate signal the agents consume; failure policy and
transcripts before any live run so the first real API calls are already
auditable; goldens last in A because they encode review decisions.

## §7 Decisions → configuration

The former decision list D1–D4 is **resolved by WP0**: evidence strictness,
review lenses, reviewer model, and the golden recall threshold are runtime
configuration chosen by the user, with the defaults shown in the WP0 table.
Two process points remain (not expressible as config):

- **Golden-plan content (E1/E2)**: I draft, you review/amend — the golden
  encodes domain judgment.
- **Phase B start**: tell me when the demo window is over and the stack is
  fair game for read-only integration work.
