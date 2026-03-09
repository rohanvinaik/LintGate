"""Tests for mcp_tools/_behavior_impl.py — sub-module level coverage."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

from mcp_tools._behavior_impl import (
    _build_constraint_recommendation,
    _compute_coverage_gap,
    _find_similar_failures,
    _seed_theory_constraints,
)

# ── helpers ────────────────────────────────────────────────────────


def _make_helpers(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "_validate_project_root": lambda p: "/test/project",
        "_json_dumps": lambda d, **kw: json.dumps(d),
        "_json_loads": lambda s: json.loads(s),
        "_build_onboarding_status": lambda p: {"config_state": "config_enabled"},
    }
    base.update(overrides)
    return base


def _make_approach(
    outcome: str = "failed",
    approach_sig: str = "pytest:run",
    error_sigs: list[str] | None = None,
    event_count: int = 1,
) -> SimpleNamespace:
    return SimpleNamespace(
        outcome=outcome,
        approach_sig=approach_sig,
        error_sigs=error_sigs or [],
        event_count=event_count,
    )


# ── _build_constraint_recommendation ──────────────────────────────


def test_recommendation_good_coverage():
    result = _build_constraint_recommendation(
        coverage_gap=0, recall=1.0, uncertainty=[], similar_failures=[]
    )
    assert result == "Good constraint coverage. Proceed with awareness of known constraints."


def test_recommendation_coverage_gap_singular():
    result = _build_constraint_recommendation(
        coverage_gap=1, recall=1.0, uncertainty=[], similar_failures=[]
    )
    assert "1 unverified constraint area." in result
    assert "1 unverified constraint areas" not in result
    assert "Consider researching" in result


def test_recommendation_coverage_gap_plural():
    result = _build_constraint_recommendation(
        coverage_gap=3, recall=1.0, uncertainty=[], similar_failures=[]
    )
    assert "3 unverified constraint areas" in result


def test_recommendation_low_recall():
    result = _build_constraint_recommendation(
        coverage_gap=0, recall=0.5, uncertainty=[], similar_failures=[]
    )
    assert "50% prediction recall" in result
    assert "Consider researching" in result


def test_recommendation_uncertainty_singular():
    result = _build_constraint_recommendation(
        coverage_gap=0, recall=1.0, uncertainty=["zone1"], similar_failures=[]
    )
    assert "1 uncertainty zone." in result
    assert "1 uncertainty zones" not in result


def test_recommendation_uncertainty_plural():
    result = _build_constraint_recommendation(
        coverage_gap=0, recall=1.0, uncertainty=["z1", "z2"], similar_failures=[]
    )
    assert "2 uncertainty zones" in result


def test_recommendation_similar_failures_singular():
    result = _build_constraint_recommendation(
        coverage_gap=0, recall=1.0, uncertainty=[], similar_failures=[{"sig": "x"}]
    )
    assert "1 similar past failure." in result
    assert "1 similar past failures" not in result


def test_recommendation_similar_failures_plural():
    result = _build_constraint_recommendation(
        coverage_gap=0,
        recall=1.0,
        uncertainty=[],
        similar_failures=[{"sig": "a"}, {"sig": "b"}],
    )
    assert "2 similar past failures" in result


def test_recommendation_all_parts():
    result = _build_constraint_recommendation(
        coverage_gap=2,
        recall=0.75,
        uncertainty=["z1"],
        similar_failures=[{"sig": "x"}],
    )
    assert "2 unverified constraint areas" in result
    assert "75% prediction recall" in result
    assert "1 uncertainty zone" in result
    assert "1 similar past failure" in result
    assert "Consider researching" in result


def test_recommendation_zero_recall():
    result = _build_constraint_recommendation(
        coverage_gap=0, recall=0.0, uncertainty=[], similar_failures=[]
    )
    assert "0% prediction recall" in result


# ── _find_similar_failures ────────────────────────────────────────


def test_find_similar_failures_empty_approaches():
    result = _find_similar_failures([], "pytest:run_tests")
    assert result == []


def test_find_similar_failures_no_colon_in_sig():
    approaches = [_make_approach(outcome="failed", approach_sig="pytest:run")]
    result = _find_similar_failures(approaches, "nocoherentsig")
    assert result == []


def test_find_similar_failures_match():
    approaches = [
        _make_approach(
            outcome="failed",
            approach_sig="pytest:run_tests",
            error_sigs=["ImportError"],
            event_count=3,
        ),
    ]
    result = _find_similar_failures(approaches, "pytest:other_tests")
    assert len(result) == 1
    assert result[0]["sig"] == "pytest:run_tests"
    assert result[0]["count"] == 3
    assert result[0]["error"] == "ImportError"


def test_find_similar_failures_no_match_different_binary():
    approaches = [
        _make_approach(outcome="failed", approach_sig="git:commit"),
    ]
    result = _find_similar_failures(approaches, "pytest:run")
    assert result == []


def test_find_similar_failures_skips_non_failed():
    approaches = [
        _make_approach(outcome="success", approach_sig="pytest:run"),
    ]
    result = _find_similar_failures(approaches, "pytest:check")
    assert result == []


def test_find_similar_failures_empty_error_sigs():
    approaches = [
        _make_approach(outcome="failed", approach_sig="pytest:run", error_sigs=[]),
    ]
    result = _find_similar_failures(approaches, "pytest:check")
    assert len(result) == 1
    assert result[0]["error"] == ""


def test_find_similar_failures_long_error_truncated():
    long_error = "A" * 200
    approaches = [
        _make_approach(
            outcome="failed",
            approach_sig="pytest:run",
            error_sigs=[long_error],
        ),
    ]
    result = _find_similar_failures(approaches, "pytest:check")
    assert len(result) == 1
    assert len(result[0]["error"]) == 80


def test_find_similar_failures_multiple_matches():
    approaches = [
        _make_approach(outcome="failed", approach_sig="pytest:a", error_sigs=["err1"]),
        _make_approach(outcome="failed", approach_sig="pytest:b", error_sigs=["err2"]),
        _make_approach(outcome="success", approach_sig="pytest:c"),
    ]
    result = _find_similar_failures(approaches, "pytest:d")
    assert len(result) == 2


# ── _compute_coverage_gap ─────────────────────────────────────────


def test_coverage_gap_no_relevant():
    gap, recall, matched = _compute_coverage_gap(["some claim"], [])
    assert gap == 0
    assert recall == 1.0
    assert matched == set()


def test_coverage_gap_no_declared():
    hyps = [SimpleNamespace(id="h1", claim="test hypothesis about errors")]
    gap, recall, matched = _compute_coverage_gap([], hyps)
    assert gap == 1
    assert recall == 0.0
    assert matched == set()


def test_coverage_gap_full_match():
    hyps = [SimpleNamespace(id="h1", claim="test hypothesis about errors")]
    gap, recall, matched = _compute_coverage_gap(["hypothesis about errors"], hyps)
    assert gap == 0
    assert recall == 1.0
    assert "h1" in matched


def test_coverage_gap_partial_match():
    hyps = [
        SimpleNamespace(id="h1", claim="test hypothesis about errors"),
        SimpleNamespace(id="h2", claim="performance regression in module"),
    ]
    gap, recall, matched = _compute_coverage_gap(["hypothesis about errors"], hyps)
    assert gap == 1
    assert recall == 0.5
    assert "h1" in matched
    assert "h2" not in matched


def test_coverage_gap_single_word_no_match():
    hyps = [SimpleNamespace(id="h1", claim="test hypothesis about errors")]
    gap, recall, matched = _compute_coverage_gap(["errors"], hyps)
    assert gap == 1
    assert recall == 0.0


# ── _seed_theory_constraints ──────────────────────────────────────


def test_seed_theory_constraints_with_anti_patterns():
    output: dict[str, Any] = {}
    mock_profile = {
        "theory_profile": {
            "anti_patterns": [
                {"claims": ["avoid global state", "prefer pure functions"]},
            ],
        },
    }
    with patch("lintgate.theory_extractor.extract_theory", return_value=mock_profile):
        _seed_theory_constraints("/test/project", output)
    assert "theory_constraints" in output
    assert len(output["theory_constraints"]) == 2
    assert "avoid global state" in output["theory_constraints"]
    assert "hint" in output


def test_seed_theory_constraints_empty_anti_patterns():
    output: dict[str, Any] = {}
    mock_profile = {"theory_profile": {"anti_patterns": []}}
    with patch("lintgate.theory_extractor.extract_theory", return_value=mock_profile):
        _seed_theory_constraints("/test/project", output)
    assert "theory_constraints" not in output


def test_seed_theory_constraints_extraction_error():
    output: dict[str, Any] = {}
    with patch("lintgate.theory_extractor.extract_theory", side_effect=RuntimeError("fail")):
        _seed_theory_constraints("/test/project", output)
    assert "theory_constraints" not in output


def test_seed_theory_constraints_caps_at_5():
    output: dict[str, Any] = {}
    mock_profile = {
        "theory_profile": {
            "anti_patterns": [
                {"claims": [f"claim_{i}" for i in range(10)]},
            ],
        },
    }
    with patch("lintgate.theory_extractor.extract_theory", return_value=mock_profile):
        _seed_theory_constraints("/test/project", output)
    assert len(output["theory_constraints"]) == 5


def test_seed_theory_constraints_truncates_long_claims():
    output: dict[str, Any] = {}
    long_claim = "A" * 200
    mock_profile = {
        "theory_profile": {
            "anti_patterns": [{"claims": [long_claim]}],
        },
    }
    with patch("lintgate.theory_extractor.extract_theory", return_value=mock_profile):
        _seed_theory_constraints("/test/project", output)
    assert len(output["theory_constraints"][0]) == 120


# ── impl_behavior_precheck (integration-style) ────────────────────


def test_behavior_precheck_deprecation_message():
    from mcp_tools._behavior_impl import impl_behavior_precheck

    constraint_output = json.dumps(
        {
            "recommendation": "Good constraint coverage.",
            "coverage": {"constraints_verified": 0, "agent_reported": 0},
        }
    )
    hygiene_output = json.dumps({"status": "pass"})

    tools = {
        "constraint_check": MagicMock(return_value=constraint_output),
        "hygiene_check": MagicMock(return_value=hygiene_output),
    }
    helpers = _make_helpers()

    result_raw = impl_behavior_precheck(helpers, tools, path="/test", planned_action="pytest")
    result = json.loads(result_raw)
    assert "deprecation" in result
    assert "behavior_precheck is deprecated" in result["deprecation"]["message"]


def test_behavior_precheck_with_hygiene_warnings():
    from mcp_tools._behavior_impl import impl_behavior_precheck

    constraint_output = json.dumps({"recommendation": "ok", "coverage": {}})
    hygiene_output = json.dumps(
        {
            "status": "warnings",
            "command_class": "pip_install",
            "warnings": [{"check": "venv", "message": "no venv"}],
            "recommendation": "create venv",
        }
    )

    tools = {
        "constraint_check": MagicMock(return_value=constraint_output),
        "hygiene_check": MagicMock(return_value=hygiene_output),
    }
    helpers = _make_helpers()

    result_raw = impl_behavior_precheck(
        helpers, tools, path="/test", planned_action="pip install foo"
    )
    result = json.loads(result_raw)
    assert "hygiene" in result
    assert result["hygiene"]["command_class"] == "pip_install"


def test_behavior_precheck_prediction_missing_type():
    from mcp_tools._behavior_impl import impl_behavior_precheck

    constraint_output = json.dumps({"recommendation": "ok", "coverage": {}})
    hygiene_output = json.dumps({"status": "pass"})

    tools = {
        "constraint_check": MagicMock(return_value=constraint_output),
        "hygiene_check": MagicMock(return_value=hygiene_output),
    }
    helpers = _make_helpers()

    result_raw = impl_behavior_precheck(
        helpers,
        tools,
        path="/test",
        planned_action="pytest",
        prediction="will pass",
        prediction_type=None,
        prediction_value=0,
    )
    result = json.loads(result_raw)
    assert "prediction_error" in result
    assert "prediction_type is required" in result["prediction_error"]["errors"][0]


def test_behavior_precheck_prediction_missing_value():
    from mcp_tools._behavior_impl import impl_behavior_precheck

    constraint_output = json.dumps({"recommendation": "ok", "coverage": {}})
    hygiene_output = json.dumps({"status": "pass"})

    tools = {
        "constraint_check": MagicMock(return_value=constraint_output),
        "hygiene_check": MagicMock(return_value=hygiene_output),
    }
    helpers = _make_helpers()

    result_raw = impl_behavior_precheck(
        helpers,
        tools,
        path="/test",
        planned_action="pytest",
        prediction="will pass",
        prediction_type="exit_code",
        prediction_value=None,
    )
    result = json.loads(result_raw)
    assert "prediction_error" in result
    assert "prediction_value is required" in result["prediction_error"]["errors"][0]


def test_behavior_precheck_prediction_invalid_type():
    from mcp_tools._behavior_impl import impl_behavior_precheck

    constraint_output = json.dumps({"recommendation": "ok", "coverage": {}})
    hygiene_output = json.dumps({"status": "pass"})

    tools = {
        "constraint_check": MagicMock(return_value=constraint_output),
        "hygiene_check": MagicMock(return_value=hygiene_output),
    }
    helpers = _make_helpers()

    result_raw = impl_behavior_precheck(
        helpers,
        tools,
        path="/test",
        planned_action="pytest",
        prediction="will pass",
        prediction_type="invalid_type",
        prediction_value=0,
    )
    result = json.loads(result_raw)
    assert "prediction_error" in result
    assert "invalid" in result["prediction_error"]["errors"][0]


def test_behavior_precheck_prediction_registered():
    from mcp_tools._behavior_impl import impl_behavior_precheck

    constraint_output = json.dumps({"recommendation": "ok", "coverage": {}})
    hygiene_output = json.dumps({"status": "pass"})
    pred_output = json.dumps(
        {
            "status": "registered",
            "prediction_tracking": {"pending_count": 1},
        }
    )

    tools = {
        "constraint_check": MagicMock(return_value=constraint_output),
        "hygiene_check": MagicMock(return_value=hygiene_output),
        "prediction_register": MagicMock(return_value=pred_output),
    }
    helpers = _make_helpers()

    result_raw = impl_behavior_precheck(
        helpers,
        tools,
        path="/test",
        planned_action="pytest",
        prediction="exit 0",
        prediction_type="exit_code",
        prediction_value=0,
    )
    result = json.loads(result_raw)
    assert result["prediction_tracking"]["prediction_registered"] is True
    assert result["prediction_tracking"]["pending_count"] == 1
    # prediction_register was called with the exact prediction parameters
    tools["prediction_register"].assert_called_once_with(
        path="/test",
        planned_action="pytest",
        prediction="exit 0",
        prediction_type="exit_code",
        prediction_value=0,
    )
    # No prediction_error key when prediction is valid
    assert "prediction_error" not in result
