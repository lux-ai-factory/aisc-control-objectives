---
dimension: privacy-data-governance
applies_when: "system processes personal data at training or inference time"
---
Privacy and data governance (Article 10) is in scope for any system trained on
or operating over personal data. It is broader than "we don't store inputs".

Adequate coverage requires evidence of:
- **PII detection / minimisation** in inputs, training data, and logs — i.e. a
  concrete mechanism (detection + redaction), not a policy statement.
- **Data lineage and purpose limitation**: where training/reference data came
  from, the legal basis, and that its use matches the stated purpose.
- **Memorisation / leakage** testing for generative and retrieval systems (can
  the model reproduce training records or leak retrieved private context?).
- Governance of **bias-relevant data quality** (representativeness, gaps).

For RAG and NLP over user content, treat the retrieval corpus and prompt logs as
in-scope personal-data stores. Absence of a leakage test on a generative system
handling personal data is a residual gap.
