"""Startup tests for mcp_server.run_server."""

from __future__ import annotations

from types import SimpleNamespace

import mcp_server


def test_run_server_syncs_command_profiles_before_mcp_run(monkeypatch) -> None:
    called = {"sync": False, "compiled": False, "enforced": False, "run": False}

    monkeypatch.setattr(
        "lintgate.agent_command_profiles.sync_all_command_profiles",
        lambda apply=True, **_kwargs: called.__setitem__("sync", apply is True) or [],
    )
    monkeypatch.setattr(
        "lintgate.mcp_schema.compile_and_validate_schemas",
        lambda *_args, **_kwargs: called.__setitem__("compiled", True),
    )
    monkeypatch.setattr(
        "lintgate.mcp_schema.enforce_mcp_contract",
        lambda *_args, **_kwargs: called.__setitem__("enforced", True),
    )
    monkeypatch.setattr(mcp_server.mcp, "run", lambda: called.__setitem__("run", True))
    monkeypatch.setattr(mcp_server.mcp, "_tool_manager", SimpleNamespace(list_tools=lambda: []))

    mcp_server.run_server()

    assert called["sync"] is True
    assert called["compiled"] is True
    assert called["enforced"] is True
    assert called["run"] is True
