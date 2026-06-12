# Wizard to Select Tests & Datasets

Matches a qualified AI system (its **system card JSON**) against the
catalogue's tests/datasets and the controls checklists, via proposer agents
reviewed by a quality-gate agent. See `SPEC.md` for the full design — in
particular **§0: strictly additive**, this module never modifies the existing
aisc / catalogue2 / demo code.

## Status

Offline core implemented (TDD, 118 tests, no network/services required):

| Module | What |
|---|---|
| `wizard.matching.articles` | EU AI Act article/annex reference extraction (`Article 13.2`, `Article 13(3)(b)(ii)`, `Annex IV.1.a` → normalized keys) |
| `wizard.matching.tag_map` | qualification tag slugs → catalogue tag vocabulary |
| `wizard.matching.prefilter` | deterministic candidate scoring (SPEC §5.1) |
| `wizard.models.*` | SystemCard, CatalogueTool, ChecklistDoc, Proposal/Review, AssessmentPlan |
| `wizard.agents.orchestrator` | propose → review loop, hallucinated-ID guard, gap computation |
| `wizard.agents.llm` | Anthropic-backed Proposer/Reviewer adapters (duck-typed client; structured outputs) |
| `wizard.agents.runner` | composition root: card → prefilter → agents → plan (PlanRunner port) |
| `wizard.api.app` | FastAPI service (qualifications, plans, finalize, `GET /api/config`) — app factory, in-memory store |
| `wizard.config` | `RunConfig` — user-owned policy knobs (per-run override > env > defaults), echoed on every plan (`SPEC_HARDENING.md` WP0) |
| `wizard.matching.evidence` | G1 evidence-quote verification (substring + fuzzy ≥0.90) |
| guards in `orchestrator` | G1 evidence (drop/demote/off), G2 coverage-claim plausibility, G3 dataset pairing — all policy-configurable |
| `MultiLensReviewer` | optional relevance/coverage/parsimony review panel, worst-verdict merge |

Not yet built (needs live services; deferred while the demo runs): MCP server,
HTTP clients for qualification/catalogue, controls read-only DB access,
FastAPI service + UI, exports.

## Develop

```bash
uv venv && uv pip install -e ".[dev]"
.venv/bin/pytest
```

Test fixtures are frozen copies of the real seeds (MCAS system card,
`tools_seed.json`, `controls_seed.json`) — tests never touch running services.
