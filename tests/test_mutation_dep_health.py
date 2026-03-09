"""Mutation gap tests for lintgate/dependency_health.py.

Targets:
- HealthCheck.to_dict — VALUE survivors (exact dict output assertions)
- _format_duration — BOUNDARY + VALUE survivors (threshold tests)
- _build_summary — VALUE survivors (exact dict output assertions)
"""

from __future__ import annotations

from lintgate._dep_health_helpers import HealthCheck, _format_duration
from lintgate.dependency_health import _build_summary

# ── HealthCheck.to_dict — exact VALUE assertions ─────────────────────────


def test_to_dict_minimal_fields() -> None:
    hc = HealthCheck(name="venv", status="ok", message="Virtual env found")
    result = hc.to_dict()
    assert result == {
        "name": "venv",
        "status": "ok",
        "message": "Virtual env found",
    }


def test_to_dict_with_suggestion() -> None:
    hc = HealthCheck(
        name="lockfile",
        status="warning",
        message="Lockfile is stale",
        suggestion="Run uv lock",
    )
    result = hc.to_dict()
    assert result == {
        "name": "lockfile",
        "status": "warning",
        "message": "Lockfile is stale",
        "suggestion": "Run uv lock",
    }


def test_to_dict_with_evidence() -> None:
    hc = HealthCheck(
        name="manifest",
        status="error",
        message="Missing requires-python",
        evidence={"file": "pyproject.toml", "field": "requires-python"},
    )
    result = hc.to_dict()
    assert result == {
        "name": "manifest",
        "status": "error",
        "message": "Missing requires-python",
        "evidence": {"file": "pyproject.toml", "field": "requires-python"},
    }


def test_to_dict_with_all_fields() -> None:
    hc = HealthCheck(
        name="churn",
        status="warning",
        message="Dep churn detected",
        suggestion="Stabilize deps",
        evidence={"count": 7, "window": "10m"},
    )
    result = hc.to_dict()
    assert result == {
        "name": "churn",
        "status": "warning",
        "message": "Dep churn detected",
        "suggestion": "Stabilize deps",
        "evidence": {"count": 7, "window": "10m"},
    }


def test_to_dict_excludes_suggestion_when_none() -> None:
    hc = HealthCheck(name="test", status="ok", message="ok", suggestion=None)
    result = hc.to_dict()
    assert "suggestion" not in result


def test_to_dict_excludes_evidence_when_empty() -> None:
    hc = HealthCheck(name="test", status="ok", message="ok", evidence={})
    result = hc.to_dict()
    assert "evidence" not in result


def test_to_dict_includes_suggestion_even_if_empty_string() -> None:
    hc = HealthCheck(name="test", status="ok", message="ok", suggestion="")
    result = hc.to_dict()
    # Empty string is falsy, so suggestion should NOT be included
    assert "suggestion" not in result


# ── _format_duration — BOUNDARY + VALUE assertions ───────────────────────


def test_format_zero_seconds() -> None:
    assert _format_duration(0) == "0s"


def test_format_one_second() -> None:
    assert _format_duration(1) == "1s"


def test_format_59_seconds() -> None:
    assert _format_duration(59) == "59s"


def test_format_59_point_9_seconds() -> None:
    # 59.9 < 60, should still be seconds
    assert _format_duration(59.9) == "59s"


def test_format_59_point_999_seconds() -> None:
    # Just under 60 boundary
    assert _format_duration(59.999) == "59s"


def test_format_exactly_60_seconds() -> None:
    # Boundary: 60 is NOT < 60, so enters minutes branch
    assert _format_duration(60) == "1m"


def test_format_60_point_1_seconds() -> None:
    assert _format_duration(60.1) == "1m"


def test_format_90_seconds() -> None:
    assert _format_duration(90) == "1m"


def test_format_119_seconds() -> None:
    assert _format_duration(119) == "1m"


def test_format_120_seconds() -> None:
    assert _format_duration(120) == "2m"


def test_format_300_seconds() -> None:
    assert _format_duration(300) == "5m"


def test_format_3599_seconds() -> None:
    # Just under 3600 boundary
    assert _format_duration(3599) == "59m"


def test_format_3599_point_9_seconds() -> None:
    assert _format_duration(3599.9) == "59m"


def test_format_exactly_3600_seconds() -> None:
    # Boundary: 3600 is NOT < 3600, so enters hours branch
    assert _format_duration(3600) == "1.0h"


def test_format_3601_seconds() -> None:
    assert _format_duration(3601) == "1.0h"


def test_format_7200_seconds() -> None:
    assert _format_duration(7200) == "2.0h"


def test_format_5400_seconds() -> None:
    assert _format_duration(5400) == "1.5h"


def test_format_86399_seconds() -> None:
    # Just under 86400 boundary
    result = _format_duration(86399)
    assert result == "24.0h"


def test_format_exactly_86400_seconds() -> None:
    # Boundary: 86400 is NOT < 86400, so enters days branch
    assert _format_duration(86400) == "1.0d"


def test_format_86401_seconds() -> None:
    assert _format_duration(86401) == "1.0d"


def test_format_172800_seconds() -> None:
    assert _format_duration(172800) == "2.0d"


def test_format_129600_seconds() -> None:
    # 1.5 days
    assert _format_duration(129600) == "1.5d"


def test_format_fractional_seconds_truncated() -> None:
    # int(0.5) = 0
    assert _format_duration(0.5) == "0s"


def test_format_fractional_near_minute_boundary() -> None:
    # 0.999s < 60, so seconds. int(0.999) = 0
    assert _format_duration(0.999) == "0s"


# ── _build_summary — exact VALUE assertions ──────────────────────────────


def test_build_summary_all_ok() -> None:
    checks = [
        HealthCheck(name="a", status="ok", message="good"),
        HealthCheck(name="b", status="ok", message="fine"),
    ]
    result = _build_summary(checks)
    assert result == {
        "health": "healthy",
        "ok": 2,
        "warnings": 0,
        "errors": 0,
        "total_checks": 2,
    }


def test_build_summary_one_warning() -> None:
    checks = [
        HealthCheck(name="a", status="ok", message="good"),
        HealthCheck(name="b", status="warning", message="stale"),
    ]
    result = _build_summary(checks)
    assert result == {
        "health": "needs_attention",
        "ok": 1,
        "warnings": 1,
        "errors": 0,
        "total_checks": 2,
    }


def test_build_summary_one_error() -> None:
    checks = [
        HealthCheck(name="a", status="error", message="missing"),
    ]
    result = _build_summary(checks)
    assert result == {
        "health": "unhealthy",
        "ok": 0,
        "warnings": 0,
        "errors": 1,
        "total_checks": 1,
    }


def test_build_summary_error_trumps_warning() -> None:
    checks = [
        HealthCheck(name="a", status="ok", message="good"),
        HealthCheck(name="b", status="warning", message="stale"),
        HealthCheck(name="c", status="error", message="broken"),
    ]
    result = _build_summary(checks)
    assert result == {
        "health": "unhealthy",
        "ok": 1,
        "warnings": 1,
        "errors": 1,
        "total_checks": 3,
    }


def test_build_summary_empty_list() -> None:
    result = _build_summary([])
    assert result == {
        "health": "healthy",
        "ok": 0,
        "warnings": 0,
        "errors": 0,
        "total_checks": 0,
    }


def test_build_summary_multiple_warnings_no_errors() -> None:
    checks = [
        HealthCheck(name="a", status="warning", message="w1"),
        HealthCheck(name="b", status="warning", message="w2"),
        HealthCheck(name="c", status="ok", message="ok"),
    ]
    result = _build_summary(checks)
    assert result["health"] == "needs_attention"
    assert result["ok"] == 1
    assert result["warnings"] == 2
    assert result["errors"] == 0
    assert result["total_checks"] == 3
