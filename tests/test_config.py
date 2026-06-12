"""RunConfig (SPEC_HARDENING WP0): defaults, env parsing, per-run overrides.

Precedence: per-run override > environment > defaults. Every knob the spec
table lists must be reachable from both env and per-run JSON.
"""

import pytest
from pydantic import ValidationError

from wizard.config import GuardsConfig, ReviewConfig, RunConfig


class TestDefaults:
    def test_documented_defaults(self):
        cfg = RunConfig()
        assert cfg.model == "claude-opus-4-8"
        assert cfg.max_rounds == 3
        assert cfg.guards.evidence == "drop"
        assert cfg.guards.coverage_claims == "strip"
        assert cfg.guards.dataset_pairing == "enforce"
        assert cfg.review.lenses == []
        assert cfg.review.reviewer_model is None

    def test_effective_reviewer_model_falls_back_to_model(self):
        assert RunConfig().effective_reviewer_model == "claude-opus-4-8"
        cfg = RunConfig(review=ReviewConfig(reviewer_model="claude-sonnet-4-6"))
        assert cfg.effective_reviewer_model == "claude-sonnet-4-6"


class TestValidation:
    def test_max_rounds_bounds(self):
        with pytest.raises(ValidationError):
            RunConfig(max_rounds=0)
        with pytest.raises(ValidationError):
            RunConfig(max_rounds=6)

    def test_evidence_policy_literal(self):
        with pytest.raises(ValidationError):
            GuardsConfig(evidence="maybe")

    def test_lens_names_literal(self):
        with pytest.raises(ValidationError):
            ReviewConfig(lenses=["vibes"])


class TestFromEnv:
    def test_reads_all_documented_vars(self):
        env = {
            "WIZARD_MODEL": "claude-sonnet-4-6",
            "WIZARD_MAX_ROUNDS": "5",
            "WIZARD_EVIDENCE_POLICY": "demote",
            "WIZARD_COVERAGE_CLAIMS": "off",
            "WIZARD_DATASET_PAIRING": "off",
            "WIZARD_REVIEW_LENSES": "relevance,coverage",
            "WIZARD_REVIEWER_MODEL": "claude-opus-4-8",
        }
        cfg = RunConfig.from_env(env)
        assert cfg.model == "claude-sonnet-4-6"
        assert cfg.max_rounds == 5
        assert cfg.guards.evidence == "demote"
        assert cfg.guards.coverage_claims == "off"
        assert cfg.guards.dataset_pairing == "off"
        assert cfg.review.lenses == ["relevance", "coverage"]
        assert cfg.review.reviewer_model == "claude-opus-4-8"

    def test_empty_env_gives_defaults(self):
        assert RunConfig.from_env({}) == RunConfig()

    def test_lenses_whitespace_and_empty(self):
        assert RunConfig.from_env({"WIZARD_REVIEW_LENSES": " relevance , parsimony "}).review.lenses == [
            "relevance",
            "parsimony",
        ]
        assert RunConfig.from_env({"WIZARD_REVIEW_LENSES": ""}).review.lenses == []

    def test_invalid_env_value_raises(self):
        with pytest.raises(ValidationError):
            RunConfig.from_env({"WIZARD_EVIDENCE_POLICY": "yolo"})


class TestWithOverrides:
    def test_deep_merge_keeps_unrelated_fields(self):
        base = RunConfig.from_env({"WIZARD_EVIDENCE_POLICY": "demote"})
        merged = base.with_overrides({"guards": {"dataset_pairing": "off"}})
        assert merged.guards.dataset_pairing == "off"
        assert merged.guards.evidence == "demote"  # untouched by partial override
        assert merged.model == base.model

    def test_top_level_override(self):
        merged = RunConfig().with_overrides({"model": "claude-sonnet-4-6", "max_rounds": 1})
        assert merged.model == "claude-sonnet-4-6"
        assert merged.max_rounds == 1

    def test_override_validated(self):
        with pytest.raises(ValidationError):
            RunConfig().with_overrides({"max_rounds": 99})

    def test_none_and_empty_overrides_are_noops(self):
        base = RunConfig()
        assert base.with_overrides(None) == base
        assert base.with_overrides({}) == base
