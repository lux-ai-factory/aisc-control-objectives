---
dimension: technical-robustness-safety
ai_types: [tabular-and-structured-data, predictive-and-analytical-ai, anomaly-detection]
applies_when: "system makes predictions or scores from structured/tabular features"
---
For tabular and predictive systems, Article 15 robustness centres on behaviour
under realistic distribution shift, not point accuracy on a frozen test split.

Adequate coverage requires evidence of:
- **Data and concept drift monitoring** with defined thresholds and an action on
  breach (retrain, alert, fall back). A one-off validation score is not enough.
- **Calibration** of probabilistic outputs (e.g. reliability/Brier), especially
  where scores drive a downstream decision threshold.
- **Performance stability** across known segments and over time, not just the
  aggregate metric.
- For anomaly detection: false-positive/false-negative trade-off documented at
  the operating point actually deployed.

Raise priority when the prediction gates access to an essential service. A model
validated once at training time with no live drift control is a residual gap.
