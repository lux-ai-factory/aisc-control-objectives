---
dimension: diversity-non-discrimination-fairness
ai_types: [tabular-and-structured-data, predictive-and-analytical-ai, natural-language-processing]
sectors: [finance-and-insurance, public-sector, health, education, social-&-welfare-issues]
applies_when: "outputs influence decisions about people (credit, eligibility, ranking)"
---
When a system's output influences a decision about a person, fairness is in
scope regardless of whether protected attributes are used as features — proxies
re-introduce bias.

Adequate coverage requires evidence of:
- **Group fairness metrics** computed across the relevant protected groups
  (e.g. demographic parity difference, equal-opportunity / equalised-odds gaps,
  disparate impact ratio), reported at the deployed decision threshold.
- A stated, justified **fairness criterion** appropriate to the use case (error
  parity vs. selection parity are not interchangeable).
- **Subgroup performance**, not only the aggregate, including small groups.
- For generative/NLP outputs: representational-harm and toxicity-bias testing.

Self-asserted "quarterly fairness reviews" are not coverage without an
independent, reproducible measurement. Raise priority to "must" in high-risk
sectors. Mind that fixing a fairness gap can trade off against accuracy — record
the chosen balance.
