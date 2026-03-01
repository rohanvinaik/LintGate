"""Tests for ControlPlane run cache and controlplane_get_details drill-down."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from unittest import mock

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from lintgate.state import (
    load_controlplane_run,
    load_run_details,
    save_controlplane_run,
    save_run_details,
)

# ── ControlPlane Run Cache ─────────────────────────────────────────────


class TestControlplaneRunCache:
    def test_save_and_load_roundtrip(self, tmp_path: Path) -> None:
        runs_dir = tmp_path / "runs"
        runs_dir.mkdir()
        with mock.patch("lintgate.state.RUNS_DIR", runs_dir):
            data = {
                "coherence": {"state": "isolated"},
                "channels": {
                    "lint": {
                        "status": "fail",
                        "findings": [{"kind": "F821", "message": "Undefined 'x'"}],
                    }
                },
            }
            save_controlplane_run("abc123", data)
            loaded = load_controlplane_run("abc123")

            assert loaded is not None
            assert loaded["run_id"] == "abc123"
            assert loaded["type"] == "controlplane"
            assert loaded["coherence"]["state"] == "isolated"
            assert len(loaded["channels"]["lint"]["findings"]) == 1

    def test_missing_run_returns_none(self, tmp_path: Path) -> None:
        runs_dir = tmp_path / "runs"
        runs_dir.mkdir()
        with mock.patch("lintgate.state.RUNS_DIR", runs_dir):
            assert load_controlplane_run("nonexistent") is None

    def test_cp_prefix_used(self, tmp_path: Path) -> None:
        """Saved files use cp_ prefix to avoid collision with lint runs."""
        runs_dir = tmp_path / "runs"
        runs_dir.mkdir()
        with mock.patch("lintgate.state.RUNS_DIR", runs_dir):
            save_controlplane_run("test_id", {"data": 1})
            assert (runs_dir / "cp_test_id.json").exists()
            # Non-prefixed file should not exist
            assert not (runs_dir / "test_id.json").exists()

    def test_lint_pruning_does_not_delete_controlplane_runs(
        self, tmp_path: Path
    ) -> None:
        runs_dir = tmp_path / "runs"
        runs_dir.mkdir()
        with mock.patch("lintgate.state.RUNS_DIR", runs_dir):
            save_controlplane_run("keep_cp", {"coherence": {"state": "stable"}})
            for i in range(60):
                save_run_details(f"lint_{i:03d}", {"index": i})
            assert (runs_dir / "cp_keep_cp.json").exists()

    def test_controlplane_pruning_does_not_delete_lint_runs(
        self, tmp_path: Path
    ) -> None:
        runs_dir = tmp_path / "runs"
        runs_dir.mkdir()
        with mock.patch("lintgate.state.RUNS_DIR", runs_dir):
            save_run_details("keep_lint", {"index": 1})
            for i in range(60):
                save_controlplane_run(f"cp_{i:03d}", {"coherence": {"state": "stable"}})
            assert load_run_details("keep_lint") is not None


# ── ControlPlane Get Details ───────────────────────────────────────────


class TestControlplaneGetDetails:
    """Tests for the controlplane_get_details MCP tool logic.

    These test the underlying load + filtering logic directly
    rather than going through the full MCP tool wrapper.
    """

    @pytest.fixture()
    def runs_dir(self, tmp_path: Path) -> Path:
        d = tmp_path / "runs"
        d.mkdir()
        return d

    @pytest.fixture()
    def sample_run(self, runs_dir: Path) -> str:
        """Save a sample run and return its run_id."""
        run_id = "sample123"
        data = {
            "run_id": run_id,
            "type": "controlplane",
            "timestamp": 1000.0,
            "coherence": {
                "state": "isolated",
                "summary": "Lint failing",
                "recommended_action": "Fix lint",
                "silent_channels": ["tests"],
                "loud_channels": ["lint"],
            },
            "duration_ms": 42.5,
            "partial": False,
            "incomplete_channels": [],
            "finding_index": {},
            "channels": {
                "lint": {
                    "status": "fail",
                    "severity": "blocking",
                    "duration_ms": 30.0,
                    "error": None,
                    "findings": [
                        {
                            "kind": "F821",
                            "severity": "blocking",
                            "message": "Undefined 'x'",
                            "file": "foo.py",
                            "line": 10,
                            "linter": "ruff",
                        },
                        {
                            "kind": "E501",
                            "severity": "warning",
                            "message": "Line too long",
                            "file": "bar.py",
                            "line": 5,
                            "linter": "ruff",
                        },
                    ],
                    "repairs": [
                        {
                            "action_id": "r1",
                            "kind": "command",
                            "summary": "Fix E501",
                            "safe": True,
                            "payload": {},
                        },
                    ],
                    "metrics": {"pattern_alerts": []},
                },
                "tests": {
                    "status": "pass",
                    "severity": "none",
                    "duration_ms": 10.0,
                    "error": None,
                    "findings": [],
                    "repairs": [],
                    "metrics": {},
                },
            },
        }
        run_file = runs_dir / f"cp_{run_id}.json"
        with open(run_file, "w") as f:
            json.dump(data, f)
        return run_id

    def test_load_by_run_id(self, runs_dir: Path, sample_run: str) -> None:
        with mock.patch("lintgate.state.RUNS_DIR", runs_dir):
            loaded = load_controlplane_run(sample_run)
            assert loaded is not None
            assert loaded["coherence"]["state"] == "isolated"

    def test_filter_by_channel(self, runs_dir: Path, sample_run: str) -> None:
        """Only findings from specified channel are returned."""
        with mock.patch("lintgate.state.RUNS_DIR", runs_dir):
            details = load_controlplane_run(sample_run)
            assert details is not None
            # Filter to just lint channel
            lint_findings = details["channels"]["lint"]["findings"]
            tests_findings = details["channels"]["tests"]["findings"]
            assert len(lint_findings) == 2
            assert len(tests_findings) == 0

    def test_filter_by_severity(self, runs_dir: Path, sample_run: str) -> None:
        """Severity filtering works at the finding level."""
        with mock.patch("lintgate.state.RUNS_DIR", runs_dir):
            details = load_controlplane_run(sample_run)
            assert details is not None
            lint_findings = details["channels"]["lint"]["findings"]
            blocking = [f for f in lint_findings if f["severity"] == "blocking"]
            warnings = [f for f in lint_findings if f["severity"] == "warning"]
            assert len(blocking) == 1
            assert len(warnings) == 1

    def test_repairs_present(self, runs_dir: Path, sample_run: str) -> None:
        with mock.patch("lintgate.state.RUNS_DIR", runs_dir):
            details = load_controlplane_run(sample_run)
            assert details is not None
            repairs = details["channels"]["lint"]["repairs"]
            assert len(repairs) == 1
            assert repairs[0]["safe"] is True

    def test_tool_default_sections_include_evidence(
        self, runs_dir: Path, sample_run: str
    ) -> None:
        from mcp_server import controlplane_get_details

        with mock.patch("lintgate.state.RUNS_DIR", runs_dir):
            payload = json.loads(controlplane_get_details(sample_run))
        assert "evidence" in payload
        assert "lint" in payload["evidence"]
