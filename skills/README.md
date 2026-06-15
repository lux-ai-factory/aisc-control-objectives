# Wizard skills

Domain-knowledge files that tell the assessment agents what to test per
trustworthiness **dimension** and **technology**, and when coverage is adequate.
Drop `*.md` files here (or point `WIZARD_SKILLS_DIR` elsewhere). With no files,
the pipeline behaves exactly as the unskilled path.

Each file is Markdown with a YAML frontmatter header:

```markdown
---
dimension: technical-robustness-and-safety   # required; catalogue dimension slug
ai_types: [natural-language-processing, agents-and-agentic-systems]  # optional; empty = all
sectors: [finance-and-insurance]             # optional; empty = all sectors
applies_when: "system exposes an LLM endpoint"   # optional, human-readable note
---
Guidance body: what to test, what counts as adequate coverage, when to raise
priority, what to skip for this dimension + technology.
```

Routing: a skill is selected when its `dimension` is in scope **and** its
`ai_types`/`sectors` (when given) intersect the system's. Selected bodies are
injected into the framer/proposer **stable, cached** prompt block, so they do
not disturb the prompt-cache design. The `dimension` slug is normalised, so the
"and"-infixed form (`technical-robustness-and-safety`) and the catalogue's
canonical form (`technical-robustness-safety`) both work.

`applies_when` is **advisory only** — it is *not* evaluated for routing (a free
text string is too brittle to gate on). It is surfaced to the agent inside the
skill body as guidance for the model's own judgement. Use `ai_types`/`sectors`
for hard routing; use `applies_when` to tell the model when the guidance bites.
