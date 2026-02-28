"""Self-test: ControlPlane MCP wrapper smoke test on its own codebase.

Tests the public MCP seam (mcp_server.controlplane_run / controlplane_get_details),
not internal engine internals. Follows test_cold_start.py pattern.

Asserts structural validity — valid JSON, run_id present, channels ran,
drilldown works — NOT policy ("no blocking findings"). Policy assertions
are brittle; this is a smoke test for wrapper regressions.
"""

from __future__ import annotations

import json
from pathlib import Path

PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)


class TestControlPlaneSelfTest:
    """MCP wrapper returns valid, drillable payloads on the lintgate project."""

    def test_controlplane_run_returns_valid_json(self) -> None:
        from mcp_server import controlplane_run

        raw = controlplane_run(PROJECT_ROOT, scope="project", strictness="relaxed")
        result = json.loads(raw)
        assert isinstance(result, dict)

    def test_controlplane_run_includes_run_id(self) -> None:
        from mcp_server import controlplane_run

        result = json.loads(
            controlplane_run(PROJECT_ROOT, scope="project", strictness="relaxed")
        )
        assert "run_id" in result
        assert result["run_id"]  # non-empty

    def test_controlplane_run_channel_results_present(self) -> None:
        from mcp_server import controlplane_run

        result = json.loads(
            controlplane_run(PROJECT_ROOT, scope="project", strictness="relaxed")
        )
        # Compact mode returns channel summaries
        assert "channels" in result or "channel_results" in result

    def test_controlplane_get_details_succeeds(self) -> None:
        from mcp_server import controlplane_get_details, controlplane_run

        run_result = json.loads(
            controlplane_run(PROJECT_ROOT, scope="project", strictness="relaxed")
        )
        run_id = run_result["run_id"]

        details_raw = controlplane_get_details(run_id)
        details = json.loads(details_raw)
        assert isinstance(details, dict)
        assert "run_id" in details

    def test_no_crash_or_malformed_payload(self) -> None:
        from mcp_server import controlplane_run

        # Should not raise
        raw = controlplane_run(PROJECT_ROOT, scope="project", strictness="relaxed")
        result = json.loads(raw)
        # Basic structural sanity
        assert "run_id" in result
        assert "duration_ms" in result or "channels" in result
