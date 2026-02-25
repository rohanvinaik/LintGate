"""Tests for NSIL eval harness Tier 2/3 runners."""

from lintgate.nsil.adapters.vllm import VLLMAdapter
from lintgate.nsil.eval_harness import (
    check_tier_capabilities,
    run_tier2,
    run_tier3,
)
from mcp_tools.nsil_tools import nsil_benchmark

# ── Tier 2 runner tests ─────────────────────────────────────────────────


def test_run_tier2_empty():
    """Test Tier 2 with no tasks."""
    results, diag = run_tier2([], None)
    assert results == []
    assert diag.total_tasks == 0


def test_run_tier2_with_tasks():
    """Test Tier 2 with tasks."""
    tasks = [
        {
            "id": "t2_001",
            "grammar": {"applied": True},
            "prompt": "test prompt",
            "fixture": {"applied": True},
        },
    ]
    results, diag = run_tier2(tasks, None)

    assert len(results) == 1
    assert diag.total_tasks == 1


def test_run_tier2_unsupported_grammar():
    """Test Tier 2 returns unsupported when grammar unavailable."""
    tasks = [
        {"id": "t2_002", "grammar": {}, "prompt": "test", "fixture": {}},
    ]
    # Pass capabilities indicating no grammar support
    results, diag = run_tier2(tasks, None, capabilities={"supports_grammar_constraints": False})

    assert len(results) == 1
    assert not results[0].passed
    assert "unsupported_tier" in results[0].error_message


def test_run_tier2_metrics():
    """Test Tier 2 metrics are bounded."""
    tasks = [
        {
            "id": "t2_003",
            "grammar": {"applied": True, "token_count": 100},
            "prompt": "test",
            "fixture": {"applied": True},
        },
    ]
    results, _ = run_tier2(tasks, None)

    m = results[0].metrics
    assert 0.0 <= m.policy_compliance_rate <= 1.0
    assert m.token_cost >= 0.0


def test_run_tier2_missing_fixture():
    """Test Tier 2 with missing fixture."""
    tasks = [{"id": "t2_004", "grammar": {}, "prompt": "test"}]
    results, diag = run_tier2(tasks, None)

    assert len(results) == 1
    assert not results[0].passed
    assert diag.missing_fixtures == 1


# ── Tier 3 runner tests ─────────────────────────────────────────────────


def test_run_tier3_empty():
    """Test Tier 3 with no tasks."""
    results, diag = run_tier3([], None)
    assert results == []
    assert diag.total_tasks == 0


def test_run_tier3_with_tasks():
    """Test Tier 3 with tasks."""
    tasks = [
        {
            "id": "t3_001",
            "fixture": {"iterations": 1, "remaining_violations": 0},
        },
    ]
    results, diag = run_tier3(tasks, None)

    assert len(results) == 1
    assert diag.total_tasks == 1


def test_run_tier3_unsupported_no_hooks():
    """Test Tier 3 returns unsupported when hooks unavailable."""

    # Without action hooks, should fail
    class NoHooksAdapter:
        pass

    tasks = [
        {"id": "t3_002", "fixture": {"iterations": 1, "remaining_violations": 0}},
    ]
    results, diag = run_tier3(tasks, NoHooksAdapter(), None)

    assert len(results) == 1
    assert not results[0].passed
    assert "unsupported_tier" in results[0].error_message


def test_run_tier3_with_adapter_hooks():
    """Test Tier 3 with adapter that has hooks."""

    class HookAdapter:
        def register_action_hook(self, callback):
            pass

    tasks = [
        {"id": "t3_003", "fixture": {"iterations": 2, "remaining_violations": 1}},
    ]
    results, _ = run_tier3(tasks, HookAdapter(), None)

    # Should complete without error
    assert len(results) == 1


def test_run_tier3_pass_condition():
    """Test Tier 3 pass/fail conditions."""
    # Passed: iterations < 3 OR no violations
    tasks_pass = [{"id": "t3_004", "fixture": {"iterations": 2, "remaining_violations": 1}}]
    results, _ = run_tier3(tasks_pass, None)
    assert results[0].passed is True

    # Passed: no violations even with more iterations
    tasks_pass2 = [{"id": "t3_005", "fixture": {"iterations": 5, "remaining_violations": 0}}]
    results2, _ = run_tier3(tasks_pass2, None)
    assert results2[0].passed is True

    # Failed: more than 3 iterations AND violations remain
    tasks_fail = [{"id": "t3_006", "fixture": {"iterations": 4, "remaining_violations": 5}}]
    results3, _ = run_tier3(tasks_fail, None)
    assert results3[0].passed is False


def test_run_tier3_metrics_bounds():
    """Test Tier 3 metrics are bounded."""
    tasks = [
        {"id": "t3_006", "fixture": {"iterations": 2, "remaining_violations": 0}},
    ]
    results, _ = run_tier3(tasks, None)

    m = results[0].metrics
    assert 0.0 <= m.policy_compliance_rate <= 1.0
    assert 0.0 <= m.task_completion_rate <= 1.0
    assert m.latency_ms_per_action >= 0.0


# ── Capability checking tests ───────────────────────────────────────────


def test_check_tier_capabilities_tier0():
    """Test tier0 is always supported."""
    caps = check_tier_capabilities("tier0", None)
    assert caps.get("supported") is True


def test_check_tier_capabilities_tier1():
    """Test tier1 is always supported."""
    caps = check_tier_capabilities("tier1", None)
    assert caps.get("supported") is True


def test_check_tier_capabilities_no_adapter():
    """Test tier2/3 without adapter."""
    caps2 = check_tier_capabilities("tier2", None)
    caps3 = check_tier_capabilities("tier3", None)
    assert caps2.get("supported") is False
    assert caps3.get("supported") is False


def test_check_tier_capabilities_with_adapter():
    """Test tier2/3 with capable adapter."""
    adapter = VLLMAdapter()
    caps2 = check_tier_capabilities("tier2", adapter)
    caps3 = check_tier_capabilities("tier3", adapter)
    # Will be False because Outlines not available in test env
    assert isinstance(caps2.get("supported"), bool)
    assert isinstance(caps3.get("supported"), bool)


# ── MCP tool tests ────────────────────────────────────────────────────


def test_nsil_benchmark_tier0_only():
    """Test benchmark with tier0 only."""
    result = nsil_benchmark(path=".", tiers=["tier0"])

    assert "tier0" in result["results"]
    assert "deltas" in result
    assert "diagnostics" in result


def test_nsil_benchmark_multiple_tiers():
    """Test benchmark with multiple tiers."""
    result = nsil_benchmark(path=".", tiers=["tier0", "tier1"])

    assert "tier0" in result["results"]
    assert "tier1" in result["results"]
    assert "deltas" in result


def test_nsil_benchmark_response_keys():
    """Test benchmark response has required keys."""
    result = nsil_benchmark(path=".", tiers=["tier0"])

    assert {"tiers", "results", "deltas"}.issubset(result.keys())


def test_nsil_benchmark_default_tiers():
    """Test benchmark uses default tier0."""
    result = nsil_benchmark(path=".")
    assert "tier0" in result["results"]


def test_nsil_benchmark_deltas_structure():
    """Test deltas have correct structure."""
    result = nsil_benchmark(path=".", tiers=["tier0", "tier1"])

    if result["deltas"]:
        delta = list(result["deltas"].values())[0]
        assert "completion_delta" in delta
        assert "from_tier" in delta
        assert "to_tier" in delta


def test_nsil_benchmark_unsupported_tiers():
    """Test unsupported tiers are reported."""
    # Request tier4 which doesn't exist
    result = nsil_benchmark(path=".", tiers=["tier0", "tier4"])

    # tier4 should be in unsupported
    if "unsupported_tiers" in result:
        assert "tier4" in result["unsupported_tiers"]


def test_nsil_benchmark_continues_on_failure():
    """Test benchmark continues even when a tier fails."""
    # This should still return tier0 results even if other tiers fail
    result = nsil_benchmark(path=".", tiers=["tier0", "tier4"])

    # tier0 should still have results
    assert "tier0" in result["results"]
