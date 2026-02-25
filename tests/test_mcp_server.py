"""Tests for mcp_server."""

from __future__ import annotations

from mcp_server import run_server


def test_run_server_exists() -> None:
    """Basic check that run_server exists."""
    assert callable(run_server)
