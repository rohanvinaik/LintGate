"""Tests for NSIL eval harness baseline."""

from lintgate.nsil.eval_harness import (
    EvalDiagnostics,
    EvalMetrics,
    EvalTaskResult,
    load_task_fixtures,
    make_tier0_task,
    make_tier1_task,
    render_comparison_table,
    run_tier0,
    run_tier1,
)

# ── Metric tests ────────────────────────────────────────────────────────


def test_eval_metrics_defaults():
    """Test EvalMetrics has correct defaults."""
    m = EvalMetrics()
    assert m.policy_compliance_rate == 0.0
    assert m.latency_ms_per_action == 0.0
    assert m.task_completion_rate == 0.0
    assert m.token_cost == 0.0
    assert m.false_positive_repair_rate == 0.0


def test_eval_metrics_to_dict():
    """Test EvalMetrics serialization."""
    m = EvalMetrics(
        policy_compliance_rate=0.8,
        latency_ms_per_action=100.0,
        task_completion_rate=0.9,
        token_cost=500.0,
        false_positive_repair_rate=0.1,
    )
    d = m.to_dict()
    assert d["policy_compliance_rate"] == 0.8
    assert d["latency_ms_per_action"] == 100.0


def test_eval_diagnostics():
    """Test EvalDiagnostics."""
    d = EvalDiagnostics()
    assert d.total_tasks == 0

    d.total_tasks = 10
    d.passed_tasks = 7
    d.failed_tasks = 2
    d.skipped_tasks = 1
    d.missing_fixtures = 1

    result = d.to_dict()
    assert result["total"] == 10
    assert result["passed"] == 7


# ── Task creation tests ─────────────────────────────────────────────────


def test_make_tier0_task():
    """Test Tier 0 task creation."""
    task = make_tier0_task(
        task_id="test_001",
        constraints=["no-rm-rf", "scope-lib"],
        fixture={"expected_action": "use safe rm"},
    )
    assert task["id"] == "test_001"
    assert task["tier"] == 0
    assert task["constraints"] == ["no-rm-rf", "scope-lib"]
    assert task["fixture"]["expected_action"] == "use safe rm"


def test_make_tier1_task():
    """Test Tier 1 task creation."""
    task = make_tier1_task(
        task_id="test_101",
        expected_outcome="passed",
        fixture={"expected_steps": 5, "steps_taken": 5},
    )
    assert task["id"] == "test_101"
    assert task["tier"] == 1
    assert task["expected_outcome"] == "passed"


# ── Tier 0 runner tests ─────────────────────────────────────────────────


def test_run_tier0_empty():
    """Test Tier 0 with no tasks."""
    results, diag = run_tier0([], None)
    assert results == []
    assert diag.total_tasks == 0
    assert diag.passed_tasks == 0
    assert diag.failed_tasks == 0


def test_run_tier0_with_tasks():
    """Test Tier 0 with tasks."""
    tasks = [
        make_tier0_task(
            "t0_001",
            ["no-rm-rf"],
            {"expected_action": "avoid rm -rf", "actions": ["ls"], "violations": 0},
        ),
    ]
    results, diag = run_tier0(tasks, None)

    assert len(results) == 1
    assert diag.total_tasks == 1
    assert diag.passed_tasks >= 0


def test_run_tier0_missing_fixture():
    """Test Tier 0 with missing fixture."""
    tasks = [
        {"id": "t0_002", "tier": 0, "constraints": [], "fixture": None},
    ]
    results, diag = run_tier0(tasks, None)

    assert len(results) == 1
    assert not results[0].passed
    assert "Missing fixture" in results[0].error_message
    assert diag.missing_fixtures == 1


def test_run_tier0_metrics_bounds():
    """Test Tier 0 metrics are bounded."""
    tasks = [
        make_tier0_task(
            "t0_003",
            ["no-rm-rf"],
            {"expected_action": "avoid rm", "actions": ["ls"], "violations": 0},
        ),
    ]
    results, _ = run_tier0(tasks, None)

    m = results[0].metrics
    assert 0.0 <= m.policy_compliance_rate <= 1.0
    assert 0.0 <= m.task_completion_rate <= 1.0
    assert m.token_cost >= 0.0


# ── Tier 1 runner tests ─────────────────────────────────────────────────


def test_run_tier1_empty():
    """Test Tier 1 with no tasks."""
    results, diag = run_tier1([], None)
    assert results == []
    assert diag.total_tasks == 0


def test_run_tier1_with_tasks():
    """Test Tier 1 with tasks."""
    tasks = [
        make_tier1_task(
            "t1_001",
            "passed",
            {
                "expected_outcome": "passed",
                "actual_outcome": "passed",
                "expected_steps": 3,
                "steps_taken": 3,
            },
        ),
    ]
    results, diag = run_tier1(tasks, None)

    assert len(results) == 1
    assert diag.total_tasks == 1
    assert results[0].passed is True


def test_run_tier1_mismatch():
    """Test Tier 1 with outcome mismatch."""
    tasks = [
        make_tier1_task(
            "t1_002",
            "passed",
            {
                "expected_outcome": "passed",
                "actual_outcome": "failed",
                "expected_steps": 3,
                "steps_taken": 2,
            },
        ),
    ]
    results, _ = run_tier1(tasks, None)

    assert len(results) == 1
    assert results[0].passed is False
    assert results[0].metrics.task_completion_rate < 1.0


def test_run_tier1_metrics():
    """Test Tier 1 metrics computation."""
    tasks = [
        make_tier1_task(
            "t1_003",
            "passed",
            {
                "expected_outcome": "passed",
                "actual_outcome": "passed",
                "expected_steps": 5,
                "steps_taken": 5,
            },
        ),
    ]
    results, _ = run_tier1(tasks, None)

    m = results[0].metrics
    assert m.task_completion_rate == 1.0
    assert m.token_cost >= 0.0


# ── Comparison table tests ─────────────────────────────────────────────


def test_render_comparison_table_empty():
    """Test comparison table with no results."""
    table = render_comparison_table([])
    assert isinstance(table, str)
    assert "Tier0" in table
    assert "Tier1" in table


def test_render_comparison_table_with_results():
    """Test comparison table with results."""
    t0_results = [
        EvalTaskResult(
            task_id="t0_001",
            passed=True,
            metrics=EvalMetrics(
                policy_compliance_rate=0.9,
                latency_ms_per_action=100.0,
                task_completion_rate=0.8,
                token_cost=500.0,
                false_positive_repair_rate=0.1,
            ),
        ),
    ]
    t1_results = [
        EvalTaskResult(
            task_id="t1_001",
            passed=True,
            metrics=EvalMetrics(
                policy_compliance_rate=0.85,
                latency_ms_per_action=120.0,
                task_completion_rate=0.75,
                token_cost=600.0,
                false_positive_repair_rate=0.08,
            ),
        ),
    ]

    table = render_comparison_table(t0_results, t1_results)

    assert "0.90" in table
    assert "0.85" in table
    assert "Policy Compliance" in table
    assert "passed" in table.lower()


def test_render_comparison_table_stable_columns():
    """Test column order is stable."""
    table1 = render_comparison_table([])
    table2 = render_comparison_table([])

    assert table1 == table2


def test_render_comparison_table_metrics_order():
    """Test metrics appear in correct order."""
    table = render_comparison_table([])

    # Check order of metric rows
    lines = table.split("\n")
    policy_idx = next(i for i, line in enumerate(lines) if "Policy Compliance" in line)
    latency_idx = next(i for i, line in enumerate(lines) if "Latency" in line)
    completion_idx = next(i for i, line in enumerate(lines) if "Task Completion" in line)

    assert policy_idx < latency_idx < completion_idx


# ── Fixture loading tests ───────────────────────────────────────────────


def test_load_task_fixtures_empty_dir():
    """Test loading from non-existent directory."""
    fixtures = load_task_fixtures("/nonexistent/path")
    assert fixtures == {}


def test_load_task_fixtures_valid():
    """Test loading fixtures from valid directory."""
    import json
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmpdir:
        # Write test fixture
        fixture_file = Path(tmpdir) / "test_task.json"
        fixture_file.write_text(json.dumps({"expected_action": "test"}))

        fixtures = load_task_fixtures(tmpdir)
        assert "test_task" in fixtures
        assert fixtures["test_task"]["expected_action"] == "test"


# ── Edge case tests ────────────────────────────────────────────────────


def test_zero_denominators():
    """Test that zero denominators don't cause division errors."""
    tasks = [
        make_tier0_task(
            "t0_edge",
            [],
            {"expected_action": "", "actions": [], "violations": 0},
        ),
    ]
    results, _ = run_tier0(tasks, None)
    # Should not crash
    assert len(results) == 1


def test_missing_task_fixtures_structured_failure():
    """Test missing fixtures return structured failure diagnostics."""
    tasks = [
        {"id": "missing_fixture", "tier": 0, "fixture": None},
    ]
    results, diag = run_tier0(tasks, None)

    assert not results[0].passed
    assert results[0].error_message is not None
    assert diag.missing_fixtures == 1
    # Should not crash
    assert len(results) == 1
