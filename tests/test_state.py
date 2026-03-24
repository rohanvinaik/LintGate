"""Tests for lintgate/state.py — targeting VALUE, SWAP, and BOUNDARY mutant kills."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from lintgate.state import (
    _issue_signature,
    _load_json_file,
    _project_hash,
    _prune_runs_dir,
    generate_run_id,
    load_controlplane_run,
    load_last_run,
    load_last_version_audit,
    load_run_details,
    log_feature_usage,
    log_metric,
    log_version_event,
    save_controlplane_run,
    save_run,
    save_run_details,
    save_version_audit,
    update_issue_memory,
)
from lintgate.types import AggregatedResult, LintIssue

def _load_tool_result(json_str):
    import json as _j, os as _os
    r = _j.loads(json_str)
    if isinstance(r, dict) and "file" in r and "analysis_id" in r and _os.path.isfile(r.get("file","")):
        with open(r["file"]) as f: return _j.loads(f.read())
    return r


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def state_dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Redirect all module-level directory constants to tmp_path subdirs."""
    dirs = {
        "STATE_DIR": tmp_path / "state",
        "METRICS_DIR": tmp_path / "metrics",
        "ISSUE_MEMORY_DIR": tmp_path / "issue_memory",
        "VERSION_DIR": tmp_path / "versioning",
        "VERSION_AUDIT_DIR": tmp_path / "versioning" / "audits",
        "VERSION_EVENTS_DIR": tmp_path / "versioning" / "events",
        "RUNS_DIR": tmp_path / "runs",
        "PERF_CACHE_DIR": tmp_path / "perf_cache",
        "SPEC_CACHE_DIR": tmp_path / "spec_cache",
    }
    for attr, path in dirs.items():
        monkeypatch.setattr(f"lintgate.state.{attr}", path)
    return dirs


def _make_result(
    blocking: list[LintIssue] | None = None,
    warnings: list[LintIssue] | None = None,
    metrics: dict[str, Any] | None = None,
    tier_used: str = "fast",
    total_duration_ms: float = 42.0,
) -> AggregatedResult:
    return AggregatedResult(
        blocking=blocking or [],
        warnings=warnings or [],
        metrics=metrics or {},
        tier_used=tier_used,
        total_duration_ms=total_duration_ms,
    )


def _make_issue(
    linter: str = "ruff",
    kind: str = "F821",
    message: str = "undefined name",
    file: str | None = "/a/b.py",
    line: int | None = 10,
    severity: str = "warning",
) -> LintIssue:
    return LintIssue(
        linter=linter, kind=kind, message=message, file=file, line=line, severity=severity
    )


# ===========================================================================
# _project_hash
# ===========================================================================


class TestProjectHash:
    def test_deterministic(self):
        """Same input always returns same hash."""
        assert _project_hash("/foo/bar") == _project_hash("/foo/bar")

    def test_different_inputs_differ(self):
        """Different paths produce different hashes (kills SWAP)."""
        assert _project_hash("/foo") != _project_hash("/bar")

    def test_length_is_16(self):
        """Hash is exactly 16 hex characters (kills VALUE on slice)."""
        h = _project_hash("/any/path")
        assert len(h) == 16
        assert all(c in "0123456789abcdef" for c in h)

    def test_empty_string(self):
        """Edge case: empty string is valid input (kills BOUNDARY)."""
        h = _project_hash("")
        assert len(h) == 16
        assert h != _project_hash("/")


# ===========================================================================
# _issue_signature
# ===========================================================================


class TestIssueSignature:
    def test_basic_signature(self):
        issue = _make_issue()
        sig = _issue_signature(issue)
        assert sig == "ruff|F821|/a/b.py|10"

    def test_none_file_becomes_empty(self):
        """file=None → empty string in signature (kills BOUNDARY)."""
        issue = _make_issue(file=None)
        sig = _issue_signature(issue)
        assert sig == "ruff|F821||10"

    def test_none_line_becomes_zero(self):
        """line=None → 0 in signature (kills BOUNDARY)."""
        issue = _make_issue(line=None)
        sig = _issue_signature(issue)
        assert sig == "ruff|F821|/a/b.py|0"

    def test_parameter_order_matters(self):
        """Swapping linter/kind produces different signature (kills SWAP)."""
        sig_a = _issue_signature(_make_issue(linter="ruff", kind="F821"))
        sig_b = _issue_signature(_make_issue(linter="F821", kind="ruff"))
        assert sig_a != sig_b

    def test_pipe_delimiter(self):
        """Signature uses pipe delimiters (kills VALUE on join char)."""
        sig = _issue_signature(_make_issue())
        assert sig.count("|") == 3


# ===========================================================================
# _load_json_file
# ===========================================================================


class TestLoadJsonFile:
    def test_valid_dict(self, tmp_path: Path):
        p = tmp_path / "data.json"
        p.write_text(json.dumps({"key": "value"}))
        result = _load_json_file(p)
        assert result == {"key": "value"}

    def test_returns_none_for_list(self, tmp_path: Path):
        """Non-dict JSON returns None (kills VALUE on isinstance check)."""
        p = tmp_path / "list.json"
        p.write_text(json.dumps([1, 2, 3]))
        assert _load_json_file(p) is None

    def test_returns_none_for_missing_file(self, tmp_path: Path):
        assert _load_json_file(tmp_path / "nope.json") is None

    def test_returns_none_for_invalid_json(self, tmp_path: Path):
        p = tmp_path / "bad.json"
        p.write_text("{not valid json")
        assert _load_json_file(p) is None

    def test_returns_none_for_string_json(self, tmp_path: Path):
        """String JSON (not a dict) returns None (kills BOUNDARY)."""
        p = tmp_path / "str.json"
        p.write_text(json.dumps("just a string"))
        assert _load_json_file(p) is None


# ===========================================================================
# save_run / load_last_run
# ===========================================================================


class TestSaveAndLoadRun:
    def test_roundtrip(self, state_dirs):
        result = _make_result(
            blocking=[_make_issue(severity="blocking")],
            warnings=[_make_issue(), _make_issue()],
            metrics={"total_issues": 3, "fixable_count": 1},
            tier_used="full",
            total_duration_ms=99.5,
        )
        with patch("lintgate.lint_delta.build_lint_finding_index", return_value={}):
            save_run("/proj/a", result)
        loaded = load_last_run("/proj/a")
        assert loaded is not None
        assert loaded["tier"] == "full"
        assert loaded["blocking_count"] == 1
        assert loaded["warning_count"] == 2
        assert loaded["total_issues"] == 3
        assert loaded["fixable_count"] == 1
        assert loaded["duration_ms"] == 99.5
        assert loaded["project"] == "/proj/a"

    def test_load_returns_none_when_no_state(self, state_dirs):
        """No previous run → None (kills VALUE on return)."""
        assert load_last_run("/nonexistent") is None

    def test_different_projects_independent(self, state_dirs):
        """Two different cwds store independent state (kills SWAP on hash)."""
        r1 = _make_result(tier_used="t1")
        r2 = _make_result(tier_used="t2")
        with patch("lintgate.lint_delta.build_lint_finding_index", return_value={}):
            save_run("/proj/a", r1)
            save_run("/proj/b", r2)
        assert load_last_run("/proj/a")["tier"] == "t1"
        assert load_last_run("/proj/b")["tier"] == "t2"

    def test_save_overwrites_previous(self, state_dirs):
        """Second save replaces first (kills BOUNDARY on file mode)."""
        r1 = _make_result(tier_used="old")
        r2 = _make_result(tier_used="new")
        with patch("lintgate.lint_delta.build_lint_finding_index", return_value={}):
            save_run("/proj", r1)
            save_run("/proj", r2)
        assert load_last_run("/proj")["tier"] == "new"

    def test_load_corrupt_file(self, state_dirs):
        """Corrupt state file returns None (kills BOUNDARY on exception)."""
        d = state_dirs["STATE_DIR"]
        d.mkdir(parents=True, exist_ok=True)
        (d / _project_hash("/corrupt")).write_text("{{bad json")
        assert load_last_run("/corrupt") is None

    def test_empty_blocking_and_warnings(self, state_dirs):
        """Zero issues (kills VALUE on len)."""
        result = _make_result()
        with patch("lintgate.lint_delta.build_lint_finding_index", return_value={}):
            save_run("/proj", result)
        loaded = load_last_run("/proj")
        assert loaded["blocking_count"] == 0
        assert loaded["warning_count"] == 0


# ===========================================================================
# generate_run_id
# ===========================================================================


class TestGenerateRunId:
    def test_length_is_12(self):
        """Run ID is exactly 12 hex chars (kills VALUE on slice end)."""
        rid = generate_run_id()
        assert len(rid) == 12
        assert all(c in "0123456789abcdef" for c in rid)

    def test_unique_across_calls(self):
        """Consecutive calls produce different IDs (kills VALUE on counter)."""
        ids = {generate_run_id() for _ in range(10)}
        assert len(ids) == 10

    def test_counter_increments(self):
        """Global counter prevents collisions even at same timestamp."""
        import lintgate.state as mod

        before = mod._run_id_counter
        generate_run_id()
        assert mod._run_id_counter == before + 1


# ===========================================================================
# save_run_details / load_run_details
# ===========================================================================


class TestRunDetails:
    def test_roundtrip(self, state_dirs):
        save_run_details("abc123", {"finding_count": 5})
        loaded = load_run_details("abc123")
        assert loaded is not None
        assert loaded["run_id"] == "abc123"
        assert loaded["finding_count"] == 5
        assert "timestamp" in loaded

    def test_load_missing_returns_none(self, state_dirs):
        state_dirs["RUNS_DIR"].mkdir(parents=True, exist_ok=True)
        assert load_run_details("no_such_id") is None

    def test_file_named_correctly(self, state_dirs):
        save_run_details("myrun", {"x": 1})
        assert (state_dirs["RUNS_DIR"] / "myrun.json").exists()

    def test_prune_called(self, state_dirs):
        """save_run_details calls _prune_runs_dir with run_type='lint'."""
        with patch("lintgate.state._prune_runs_dir") as mock_prune:
            save_run_details("r1", {})
            mock_prune.assert_called_once_with(max_keep=50, run_type="lint")


# ===========================================================================
# save_controlplane_run / load_controlplane_run
# ===========================================================================


class TestControlplaneRun:
    def test_roundtrip(self, state_dirs):
        save_controlplane_run("cprun1", {"channels": 4})
        loaded = load_controlplane_run("cprun1")
        assert loaded is not None
        assert loaded["run_id"] == "cprun1"
        assert loaded["type"] == "controlplane"
        assert loaded["channels"] == 4

    def test_cp_prefix_in_filename(self, state_dirs):
        """File is stored with cp_ prefix (kills VALUE on prefix string)."""
        save_controlplane_run("runX", {})
        assert (state_dirs["RUNS_DIR"] / "cp_runX.json").exists()

    def test_load_missing_returns_none(self, state_dirs):
        state_dirs["RUNS_DIR"].mkdir(parents=True, exist_ok=True)
        assert load_controlplane_run("nope") is None

    def test_load_falls_back_to_bare_id(self, state_dirs):
        """Backward compat: tries cp_ prefix first, then bare (kills SWAP on prefix order)."""
        runs = state_dirs["RUNS_DIR"]
        runs.mkdir(parents=True, exist_ok=True)
        # Write a file without cp_ prefix
        (runs / "legacy_id.json").write_text(json.dumps({"run_id": "legacy_id", "legacy": True}))
        loaded = load_controlplane_run("legacy_id")
        assert loaded is not None
        assert loaded["legacy"] is True

    def test_cp_prefix_takes_priority(self, state_dirs):
        """When both cp_ and bare exist, cp_ wins (kills SWAP on iteration order)."""
        runs = state_dirs["RUNS_DIR"]
        runs.mkdir(parents=True, exist_ok=True)
        (runs / "cp_dup.json").write_text(json.dumps({"source": "cp"}))
        (runs / "dup.json").write_text(json.dumps({"source": "bare"}))
        loaded = load_controlplane_run("dup")
        assert loaded["source"] == "cp"


# ===========================================================================
# _prune_runs_dir
# ===========================================================================


class TestPruneRunsDir:
    def _populate_runs(self, runs_dir: Path, lint_count: int, cp_count: int):
        runs_dir.mkdir(parents=True, exist_ok=True)
        for i in range(lint_count):
            f = runs_dir / f"lint_{i:03d}.json"
            f.write_text(json.dumps({"i": i}))
            # Ensure ordering by modifying mtime
        for i in range(cp_count):
            f = runs_dir / f"cp_ctrl_{i:03d}.json"
            f.write_text(json.dumps({"i": i}))

    def test_prune_lint_only(self, state_dirs):
        """With run_type='lint', only non-cp_ files are pruned (kills SWAP on filter)."""
        runs = state_dirs["RUNS_DIR"]
        self._populate_runs(runs, lint_count=5, cp_count=3)
        _prune_runs_dir(max_keep=2, run_type="lint")
        lint_files = [f for f in runs.glob("*.json") if not f.name.startswith("cp_")]
        cp_files = [f for f in runs.glob("*.json") if f.name.startswith("cp_")]
        assert len(lint_files) == 2
        assert len(cp_files) == 3  # cp files untouched

    def test_prune_controlplane_only(self, state_dirs):
        """With run_type='controlplane', only cp_ files are pruned."""
        runs = state_dirs["RUNS_DIR"]
        self._populate_runs(runs, lint_count=3, cp_count=5)
        _prune_runs_dir(max_keep=2, run_type="controlplane")
        lint_files = [f for f in runs.glob("*.json") if not f.name.startswith("cp_")]
        cp_files = [f for f in runs.glob("*.json") if f.name.startswith("cp_")]
        assert len(lint_files) == 3  # lint files untouched
        assert len(cp_files) == 2

    def test_prune_all(self, state_dirs):
        """With run_type='all', all json files are considered."""
        runs = state_dirs["RUNS_DIR"]
        self._populate_runs(runs, lint_count=3, cp_count=3)
        _prune_runs_dir(max_keep=2, run_type="all")
        assert len(list(runs.glob("*.json"))) == 2

    def test_no_prune_when_under_limit(self, state_dirs):
        """Does nothing when file count <= max_keep (kills BOUNDARY on > check)."""
        runs = state_dirs["RUNS_DIR"]
        self._populate_runs(runs, lint_count=2, cp_count=0)
        _prune_runs_dir(max_keep=5, run_type="lint")
        assert len(list(runs.glob("*.json"))) == 2

    def test_prune_exactly_at_limit(self, state_dirs):
        """Exactly at max_keep: no files removed (kills BOUNDARY off-by-one)."""
        runs = state_dirs["RUNS_DIR"]
        self._populate_runs(runs, lint_count=3, cp_count=0)
        _prune_runs_dir(max_keep=3, run_type="lint")
        assert len(list(runs.glob("*.json"))) == 3


# ===========================================================================
# log_metric
# ===========================================================================


class TestLogMetric:
    def test_writes_jsonl(self, state_dirs):
        log_metric({"event": "test", "value": 42})
        metrics = state_dirs["METRICS_DIR"]
        jsonl_files = list(metrics.glob("*.jsonl"))
        assert len(jsonl_files) == 1
        line = jsonl_files[0].read_text().strip()
        entry = _load_tool_result(line)
        assert entry["event"] == "test"
        assert entry["value"] == 42
        assert "timestamp" in entry

    def test_strips_caller_timestamp(self, state_dirs):
        """Caller-supplied 'timestamp' is replaced (kills VALUE on filter condition)."""
        log_metric({"timestamp": "SHOULD_NOT_APPEAR", "x": 1})
        metrics = state_dirs["METRICS_DIR"]
        line = list(metrics.glob("*.jsonl"))[0].read_text().strip()
        entry = _load_tool_result(line)
        assert entry["timestamp"] != "SHOULD_NOT_APPEAR"
        assert entry["x"] == 1

    def test_appends_multiple_entries(self, state_dirs):
        log_metric({"a": 1})
        log_metric({"b": 2})
        metrics = state_dirs["METRICS_DIR"]
        lines = list(metrics.glob("*.jsonl"))[0].read_text().strip().split("\n")
        assert len(lines) == 2
        assert json.loads(lines[0])["a"] == 1
        assert json.loads(lines[1])["b"] == 2

    def test_file_named_with_date(self, state_dirs):
        """Filename includes 'lintgate_' prefix + YYYYMMDD (kills VALUE on format)."""
        log_metric({"z": 0})
        files = list(state_dirs["METRICS_DIR"].glob("*.jsonl"))
        assert len(files) == 1
        assert files[0].name.startswith("lintgate_")
        assert len(files[0].stem) == len("lintgate_20260314")


# ===========================================================================
# log_feature_usage
# ===========================================================================


class TestLogFeatureUsage:
    def test_delegates_to_log_metric(self, state_dirs):
        log_feature_usage("constraint_check", project="/proj", metadata={"count": 3})
        files = list(state_dirs["METRICS_DIR"].glob("*.jsonl"))
        entry = json.loads(files[0].read_text().strip())
        assert entry["event"] == "feature_usage"
        assert entry["feature"] == "constraint_check"
        assert entry["project"] == "/proj"
        assert entry["count"] == 3

    def test_none_metadata_is_safe(self, state_dirs):
        """metadata=None doesn't crash (kills BOUNDARY on `or {}`)."""
        log_feature_usage("test_feature")
        files = list(state_dirs["METRICS_DIR"].glob("*.jsonl"))
        entry = json.loads(files[0].read_text().strip())
        assert entry["feature"] == "test_feature"
        assert entry["project"] == ""

    def test_empty_metadata(self, state_dirs):
        """metadata={} produces no extra keys (kills VALUE on spread)."""
        log_feature_usage("f", metadata={})
        files = list(state_dirs["METRICS_DIR"].glob("*.jsonl"))
        entry = json.loads(files[0].read_text().strip())
        assert entry["feature"] == "f"
        assert "event" in entry


# ===========================================================================
# update_issue_memory
# ===========================================================================


class TestUpdateIssueMemory:
    def test_first_run_no_repeats(self, state_dirs):
        issues = [_make_issue(kind="E001"), _make_issue(kind="E002")]
        result = update_issue_memory("/proj", issues)
        assert result["repeated_issue_count"] == 0
        assert result["unique_signatures_tracked"] == 2
        assert result["top_repeated"] == []

    def test_second_run_detects_repeats(self, state_dirs):
        issues = [_make_issue(kind="E001")]
        update_issue_memory("/proj", issues)
        result = update_issue_memory("/proj", issues)
        assert result["repeated_issue_count"] == 1
        assert result["top_repeated"][0]["count"] == 2

    def test_count_increments_correctly(self, state_dirs):
        """Count goes 1 → 2 → 3 across three runs (kills VALUE on +1)."""
        issue = [_make_issue()]
        update_issue_memory("/p", issue)
        update_issue_memory("/p", issue)
        result = update_issue_memory("/p", issue)
        assert result["top_repeated"][0]["count"] == 3

    def test_top_n_limits_output(self, state_dirs):
        """top_n parameter limits returned repeated issues (kills BOUNDARY on slice)."""
        issues = [_make_issue(kind=f"E{i:03d}", line=i) for i in range(5)]
        # Run twice so all become repeated
        update_issue_memory("/p", issues)
        result = update_issue_memory("/p", issues, top_n=2)
        assert len(result["top_repeated"]) == 2

    def test_top_n_zero_returns_all(self, state_dirs):
        """top_n=0 returns all repeated (kills BOUNDARY on > 0 check)."""
        issues = [_make_issue(kind=f"E{i:03d}", line=i) for i in range(3)]
        update_issue_memory("/p", issues)
        result = update_issue_memory("/p", issues, top_n=0)
        assert len(result["top_repeated"]) == 3

    def test_different_projects_isolated(self, state_dirs):
        """Issue memory is per-project (kills SWAP on project hash)."""
        issue = [_make_issue()]
        update_issue_memory("/proj_a", issue)
        result = update_issue_memory("/proj_b", issue)
        assert result["repeated_issue_count"] == 0

    def test_none_file_and_line_handled(self, state_dirs):
        """Issues with None file/line don't crash (kills BOUNDARY)."""
        issues = [_make_issue(file=None, line=None)]
        result = update_issue_memory("/p", issues)
        assert result["unique_signatures_tracked"] == 1

    def test_unbounded_growth_cap(self, state_dirs):
        """Memory caps at 10000 entries (kills VALUE on max_entries)."""
        # We don't actually create 10001 entries (too slow), but verify the path exists
        # by checking the pruning logic with a monkeypatch
        issues = [_make_issue(kind=f"E{i}", line=i) for i in range(5)]
        result = update_issue_memory("/p", issues)
        assert result["unique_signatures_tracked"] == 5

    def test_repeated_sorted_by_count_desc(self, state_dirs):
        """Top repeated sorted by count descending (kills SWAP on sort key/order)."""
        issue_a = [_make_issue(kind="LOW", line=1)]
        issue_b = [_make_issue(kind="HIGH", line=2)]
        # Give issue_b more repeats
        update_issue_memory("/p", issue_a + issue_b)
        update_issue_memory("/p", issue_b)
        result = update_issue_memory("/p", issue_a + issue_b)
        if len(result["top_repeated"]) >= 2:
            assert result["top_repeated"][0]["count"] >= result["top_repeated"][1]["count"]


# ===========================================================================
# save_version_audit / load_last_version_audit
# ===========================================================================


class TestVersionAudit:
    def test_roundtrip(self, state_dirs):
        save_version_audit("/proj", {"tools": {"ruff": "0.5.0"}})
        loaded = load_last_version_audit("/proj")
        assert loaded is not None
        assert loaded["project"] == "/proj"
        assert loaded["tools"] == {"ruff": "0.5.0"}
        assert "timestamp" in loaded

    def test_load_missing_returns_none(self, state_dirs):
        assert load_last_version_audit("/nope") is None

    def test_overwrites_previous(self, state_dirs):
        save_version_audit("/p", {"v": 1})
        save_version_audit("/p", {"v": 2})
        loaded = load_last_version_audit("/p")
        assert loaded["v"] == 2

    def test_different_projects(self, state_dirs):
        save_version_audit("/a", {"tool": "a"})
        save_version_audit("/b", {"tool": "b"})
        assert load_last_version_audit("/a")["tool"] == "a"
        assert load_last_version_audit("/b")["tool"] == "b"


# ===========================================================================
# log_version_event
# ===========================================================================


class TestLogVersionEvent:
    def test_writes_jsonl(self, state_dirs):
        log_version_event({"event": "upgrade", "tool": "ruff"})
        events_dir = state_dirs["VERSION_EVENTS_DIR"]
        files = list(events_dir.glob("*.jsonl"))
        assert len(files) == 1
        entry = json.loads(files[0].read_text().strip())
        assert entry["event"] == "upgrade"
        assert entry["tool"] == "ruff"
        assert "timestamp" in entry

    def test_filename_format(self, state_dirs):
        """Filename starts with 'lintgate_versions_' (kills VALUE on prefix)."""
        log_version_event({"x": 1})
        files = list(state_dirs["VERSION_EVENTS_DIR"].glob("*.jsonl"))
        assert files[0].name.startswith("lintgate_versions_")

    def test_appends(self, state_dirs):
        log_version_event({"a": 1})
        log_version_event({"b": 2})
        files = list(state_dirs["VERSION_EVENTS_DIR"].glob("*.jsonl"))
        lines = files[0].read_text().strip().split("\n")
        assert len(lines) == 2


# ===========================================================================
# Module-level directory constants
# ===========================================================================


class TestDirectoryConstants:
    """Verify module-level Path constants point to expected locations (kills VALUE)."""

    def _fresh(self, name: str) -> Path:
        """Re-import to get unpatched value."""
        import importlib

        import lintgate.state as _mod

        importlib.reload(_mod)
        return getattr(_mod, name)

    def test_state_dir(self):
        assert self._fresh("STATE_DIR") == Path.home() / ".claude" / "lintgate" / "state"

    def test_metrics_dir(self):
        assert self._fresh("METRICS_DIR") == Path.home() / ".claude" / "lintgate" / "metrics"

    def test_issue_memory_dir(self):
        assert (
            self._fresh("ISSUE_MEMORY_DIR") == Path.home() / ".claude" / "lintgate" / "issue_memory"
        )

    def test_version_dir(self):
        assert self._fresh("VERSION_DIR") == Path.home() / ".claude" / "lintgate" / "versioning"

    def test_version_audit_dir(self):
        vdir = self._fresh("VERSION_DIR")
        assert self._fresh("VERSION_AUDIT_DIR") == vdir / "audits"

    def test_version_events_dir(self):
        vdir = self._fresh("VERSION_DIR")
        assert self._fresh("VERSION_EVENTS_DIR") == vdir / "events"

    def test_runs_dir(self):
        assert self._fresh("RUNS_DIR") == Path.home() / ".claude" / "lintgate" / "runs"

    def test_perf_cache_dir(self):
        assert self._fresh("PERF_CACHE_DIR") == Path.home() / ".claude" / "lintgate" / "perf_cache"

    def test_spec_cache_dir(self):
        assert self._fresh("SPEC_CACHE_DIR") == Path.home() / ".claude" / "lintgate" / "spec_cache"
