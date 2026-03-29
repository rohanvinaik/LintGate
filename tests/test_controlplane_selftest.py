"""Self-test: ControlPlane MCP wrapper smoke test on its own codebase.

Tests the public MCP seam (mcp_server.controlplane_run / controlplane_get_details),
not internal engine internals. Follows test_cold_start.py pattern.

Asserts structural validity — valid JSON, run_id present, channels ran,
drilldown works — NOT policy ("no blocking findings"). Policy assertions
are brittle; this is a smoke test for wrapper regressions.

Also serves as the "eat your own dog food" CI gate (#181): if LintGate's
ControlPlane cannot analyze itself without crashing, it should not ship.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest


def _load_tool_result(json_str):
    import json as _j
    import os
    r = _j.loads(json_str)
    if isinstance(r, dict) and "file" in r and "analysis_id" in r and os.path.isfile(r.get("file","")):
        with open(r["file"]) as f:

            return _j.loads(f.read())
    return r



# ── Locate the LintGate project root ──────────────────────────────────

_THIS_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = str(_THIS_DIR.parent)
PROJECT_ROOT = _PROJECT_ROOT  # alias for MCP-wrapper tests


def _skip_if_not_in_repo():
    """Skip if we're not running from a proper LintGate checkout."""
    if not os.path.isfile(os.path.join(_PROJECT_ROOT, "pyproject.toml")):
        pytest.skip("Not running from a LintGate checkout")


# ── Helpers (same as mcp_server.py exposes) ───────────────────────────


def _make_helpers():
    """Build the helpers dict that _impl_controlplane_run expects.

    Imports the same functions from mcp_server.py so we exercise
    the real pipeline, not a mock.
    """
    # Importing lazily to avoid circular imports in test collection
    from mcp_server import (
        _build_cp_full_details,
        _build_onboarding_status,
        _collect_python_files,
        _json_dumps,
        _validate_project_root,
    )

    return {
        "_validate_project_root": _validate_project_root,
        "_json_dumps": _json_dumps,
        "_build_onboarding_status": _build_onboarding_status,
        "_collect_python_files": _collect_python_files,
        "_build_cp_full_details": _build_cp_full_details,
    }


# ── MCP Wrapper Smoke Tests ──────────────────────────────────────────


class TestControlPlaneMCPWrapper:
    """MCP wrapper returns valid, drillable payloads on the lintgate project."""

    def test_controlplane_run_returns_valid_json(self) -> None:
        from mcp_server import controlplane_run

        raw = controlplane_run(PROJECT_ROOT, scope="project", strictness="relaxed")
        result = _load_tool_result(raw)
        assert isinstance(result, dict)

    def test_controlplane_run_includes_run_id(self) -> None:
        from mcp_server import controlplane_run

        result = _load_tool_result(controlplane_run(PROJECT_ROOT, scope="project", strictness="relaxed"))
        assert "run_id" in result
        assert result["run_id"]  # non-empty

    def test_controlplane_run_channel_results_present(self) -> None:
        from mcp_server import controlplane_run

        result = _load_tool_result(controlplane_run(PROJECT_ROOT, scope="project", strictness="relaxed"))
        # Compact mode returns channel summaries
        assert "channels" in result or "channel_results" in result

    def test_controlplane_get_details_succeeds(self) -> None:
        from mcp_server import controlplane_get_details, controlplane_run

        run_result = _load_tool_result(
            controlplane_run(PROJECT_ROOT, scope="project", strictness="relaxed")
        )
        run_id = run_result["run_id"]

        details_raw = controlplane_get_details(run_id)
        details = _load_tool_result(details_raw)
        assert isinstance(details, dict)
        assert "run_id" in details

    def test_no_crash_or_malformed_payload(self) -> None:
        from mcp_server import controlplane_run

        # Should not raise
        raw = controlplane_run(PROJECT_ROOT, scope="project", strictness="relaxed")
        result = _load_tool_result(raw)
        # Basic structural sanity
        assert "run_id" in result
        assert "duration_ms" in result or "channels" in result


# ── Dogfood / impl-level Tests ───────────────────────────────────────


class TestControlPlaneSelfTest:
    """Dogfood gate: controlplane_run on LintGate's own codebase."""

    def test_controlplane_run_succeeds(self):
        """controlplane_run must return valid JSON without crashing."""
        _skip_if_not_in_repo()

        from mcp_tools.controlplane_tools import _impl_controlplane_run

        helpers = _make_helpers()

        # Run with relaxed strictness — we want crash-free, not zero-finding
        result_json = _impl_controlplane_run(
            path=_PROJECT_ROOT,
            channels="lint,deps,git,structure",
            strictness="relaxed",
            scope="project",
            files=None,
            helpers=helpers,
        )

        # Must be valid JSON
        result = _load_tool_result(result_json)

        # Must have a run_id
        assert "run_id" in result, "Missing run_id in controlplane_run output"

        # Must have channels dict with results for each requested channel
        assert "channels" in result, "Missing channels"
        channels = result["channels"]
        for ch in ("lint", "deps", "git", "structure"):
            assert ch in channels, f"Missing channel '{ch}' in channels"

        # Must have counts
        assert "counts" in result, "Missing counts"

    def test_controlplane_run_has_coherence(self):
        """controlplane_run must include coherence diagnosis."""
        _skip_if_not_in_repo()

        from mcp_tools.controlplane_tools import _impl_controlplane_run

        helpers = _make_helpers()

        result_json = _impl_controlplane_run(
            path=_PROJECT_ROOT,
            channels="lint,structure",
            strictness="relaxed",
            scope="project",
            files=None,
            helpers=helpers,
        )

        result = _load_tool_result(result_json)
        assert "coherence" in result, "Missing coherence in output"

    def test_get_details_drilldown(self):
        """controlplane_get_details must be able to drill into a self-test run."""
        _skip_if_not_in_repo()

        from mcp_tools.controlplane_tools import (
            _impl_controlplane_get_details,
            _impl_controlplane_run,
        )

        helpers = _make_helpers()

        # First, do a run
        run_json = _impl_controlplane_run(
            path=_PROJECT_ROOT,
            channels="lint,structure",
            strictness="relaxed",
            scope="project",
            files=None,
            helpers=helpers,
        )
        run_result = _load_tool_result(run_json)
        run_id = run_result["run_id"]

        # Now drill down
        details_json = _impl_controlplane_get_details(
            run_id=run_id,
            channel=None,
            severity=None,
            max_issues=5,
            sections=["findings", "channel_details", "coherence"],
            helpers=helpers,
        )
        details = _load_tool_result(details_json)

        assert details["run_id"] == run_id
        assert "channel_details" in details
        assert "coherence" in details

    def test_full_sweep_scope(self):
        """full_sweep scope must not crash and should include more files than project."""
        _skip_if_not_in_repo()

        from mcp_tools.controlplane_tools import _impl_controlplane_run

        helpers = _make_helpers()

        # Run with full_sweep to verify it doesn't crash
        result_json = _impl_controlplane_run(
            path=_PROJECT_ROOT,
            channels="structure",
            strictness="relaxed",
            scope="full_sweep",
            files=None,
            helpers=helpers,
        )

        result = _load_tool_result(result_json)
        assert "run_id" in result
        assert "channels" in result
