"""Tests for lintgate.cli.admin."""

from __future__ import annotations

import json
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

import lintgate.cli.admin as admin


def test_load_contract_missing_and_present(tmp_path, monkeypatch) -> None:
    module_file = tmp_path / "pkg" / "cli" / "admin.py"
    module_file.parent.mkdir(parents=True, exist_ok=True)
    module_file.write_text("# stub\n", encoding="utf-8")
    monkeypatch.setattr(admin, "__file__", str(module_file))

    # Missing contract path.
    assert admin._load_contract() == {}

    # Present contract path.
    contract_path = module_file.parent.parent / "mcp_contract.yaml"
    contract_path.write_text("safety_critical_tools:\n  - lint_files\n", encoding="utf-8")
    loaded = admin._load_contract()
    assert loaded["safety_critical_tools"] == ["lint_files"]


def test_resolve_server_command_prefers_path(monkeypatch) -> None:
    monkeypatch.setattr(admin.shutil, "which", lambda _name: "/usr/local/bin/lintgate-mcp")
    command, source = admin._resolve_server_command()
    assert command == "/usr/local/bin/lintgate-mcp"
    assert source == "PATH"


def test_resolve_server_command_falls_back_to_python_sibling(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(admin.shutil, "which", lambda _name: None)
    fake_python = tmp_path / "bin" / "python"
    fake_mcp = tmp_path / "bin" / "lintgate-mcp"
    fake_python.parent.mkdir(parents=True)
    fake_python.write_text("", encoding="utf-8")
    fake_mcp.write_text("#!/bin/sh\n", encoding="utf-8")
    fake_mcp.chmod(0o755)
    monkeypatch.setattr(admin.sys, "executable", str(fake_python))

    command, source = admin._resolve_server_command()
    assert command == str(fake_mcp)
    assert source == "python_sibling"


def test_resolve_server_command_raises_when_missing(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(admin.shutil, "which", lambda _name: None)
    fake_python = tmp_path / "bin" / "python"
    fake_python.parent.mkdir(parents=True)
    fake_python.write_text("", encoding="utf-8")
    monkeypatch.setattr(admin.sys, "executable", str(fake_python))
    monkeypatch.setattr(admin, "__file__", str(tmp_path / "pkg" / "cli" / "admin.py"))

    with pytest.raises(RuntimeError, match="Unable to resolve lintgate MCP executable"):
        admin._resolve_server_command()


def test_resolve_server_command_falls_back_to_repo_venv(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(admin.shutil, "which", lambda _name: None)
    fake_python = tmp_path / "bin" / "python"
    fake_python.parent.mkdir(parents=True)
    fake_python.write_text("", encoding="utf-8")
    monkeypatch.setattr(admin.sys, "executable", str(fake_python))

    fake_admin = tmp_path / "repo" / "lintgate" / "cli" / "admin.py"
    fake_admin.parent.mkdir(parents=True)
    fake_admin.write_text("# stub\n", encoding="utf-8")
    monkeypatch.setattr(admin, "__file__", str(fake_admin))

    repo_venv_bin = tmp_path / "repo" / ".venv" / "bin"
    repo_venv_bin.mkdir(parents=True)
    repo_mcp = repo_venv_bin / "lintgate-mcp"
    repo_mcp.write_text("#!/bin/sh\n", encoding="utf-8")
    repo_mcp.chmod(0o755)

    command, source = admin._resolve_server_command()
    assert command == str(repo_mcp)
    assert source == "repo_venv"


def test_load_configured_server_command_and_runnable(tmp_path, monkeypatch) -> None:
    config_path = tmp_path / "claude.json"
    config_path.write_text(
        json.dumps({"mcpServers": {"lintgate": {"command": "lintgate-mcp", "args": []}}}),
        encoding="utf-8",
    )
    command = admin._load_configured_server_command(config_path)
    assert command == "lintgate-mcp"

    monkeypatch.setattr(admin.shutil, "which", lambda _name: "/usr/bin/lintgate-mcp")
    assert admin._command_runnable("lintgate-mcp") is True
    assert admin._command_runnable("/missing/command") is False


def test_load_configured_server_command_handles_bad_shapes(tmp_path) -> None:
    bad_json = tmp_path / "bad.json"
    bad_json.write_text("{invalid", encoding="utf-8")
    assert admin._load_configured_server_command(bad_json) is None

    no_mcp = tmp_path / "no_mcp.json"
    no_mcp.write_text(json.dumps({"mcpServers": []}), encoding="utf-8")
    assert admin._load_configured_server_command(no_mcp) is None

    no_lintgate = tmp_path / "no_lintgate.json"
    no_lintgate.write_text(json.dumps({"mcpServers": {"lintgate": []}}), encoding="utf-8")
    assert admin._load_configured_server_command(no_lintgate) is None

    no_command = tmp_path / "no_command.json"
    no_command.write_text(json.dumps({"mcpServers": {"lintgate": {"args": []}}}), encoding="utf-8")
    assert admin._load_configured_server_command(no_command) is None


def test_cmd_install_unknown_agent() -> None:
    args = SimpleNamespace(agent="missing", dry_run=False)
    assert admin.cmd_install(args) == 1  # type: ignore[arg-type]


def test_cmd_install_dry_run_and_write_report(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(admin, "_resolve_server_command", lambda: ("/tmp/lintgate-mcp", "test"))

    fake_profile = SimpleNamespace(
        config_path=tmp_path / "cfg.json",
        config_writer=lambda _path, _cmd: True,
    )
    monkeypatch.setattr(admin, "PROFILES", {"demo": fake_profile})

    dry_run_args = SimpleNamespace(agent="demo", dry_run=True)
    assert admin.cmd_install(dry_run_args) == 0  # type: ignore[arg-type]
    assert not (tmp_path / "install_report.json").exists()

    apply_args = SimpleNamespace(agent="demo", dry_run=False)
    assert admin.cmd_install(apply_args) == 0  # type: ignore[arg-type]
    report = json.loads((tmp_path / "install_report.json").read_text(encoding="utf-8"))
    assert report["agent"] == "demo"
    assert report["status"] == "configured"


def test_cmd_install_reports_already_configured(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(admin, "_resolve_server_command", lambda: ("/tmp/lintgate-mcp", "test"))
    fake_profile = SimpleNamespace(
        config_path=tmp_path / "cfg.json",
        config_writer=lambda _path, _cmd: False,
    )
    monkeypatch.setattr(admin, "PROFILES", {"demo": fake_profile})

    args = SimpleNamespace(agent="demo", dry_run=False)
    assert admin.cmd_install(args) == 0  # type: ignore[arg-type]
    report = json.loads((tmp_path / "install_report.json").read_text(encoding="utf-8"))
    assert report["status"] == "already_configured"


def test_cmd_install_fails_when_server_command_unresolved(monkeypatch) -> None:
    fake_profile = SimpleNamespace(
        config_path=Path("/tmp/cfg.json"),
        config_writer=lambda _path, _cmd: True,
    )
    monkeypatch.setattr(admin, "PROFILES", {"demo": fake_profile})
    monkeypatch.setattr(
        admin,
        "_resolve_server_command",
        lambda: (_ for _ in ()).throw(RuntimeError("missing binary")),
    )

    args = SimpleNamespace(agent="demo", dry_run=False)
    assert admin.cmd_install(args) == 1  # type: ignore[arg-type]


def test_cmd_bootstrap_propagates_install_and_doctor(monkeypatch) -> None:
    args = SimpleNamespace(agent="claude", dry_run=False)
    monkeypatch.setattr(admin, "cmd_install", lambda _args: 2)
    assert admin.cmd_bootstrap(args) == 2  # type: ignore[arg-type]

    monkeypatch.setattr(admin, "cmd_install", lambda _args: 0)
    monkeypatch.setattr(admin, "cmd_doctor", lambda _args: 7)
    assert admin.cmd_bootstrap(args) == 7  # type: ignore[arg-type]


def test_cmd_doctor_unknown_and_dry_run(monkeypatch) -> None:
    unknown_args = SimpleNamespace(agent="missing", dry_run=True, fix=False)
    assert admin.cmd_doctor(unknown_args) == 1  # type: ignore[arg-type]

    fake_profile = SimpleNamespace(display_name="Demo Agent", schema_strict=False)
    monkeypatch.setattr(admin, "PROFILES", {"demo": fake_profile})
    monkeypatch.setattr(
        admin,
        "_load_contract",
        lambda: {
            "safety_critical_tools": ["lint_files"],
            "expected_tools": {"demo": ["lint_files", "lint_project"]},
        },
    )
    dry_run_args = SimpleNamespace(agent="demo", dry_run=True, fix=False)
    assert admin.cmd_doctor(dry_run_args) == 0  # type: ignore[arg-type]


def test_cmd_doctor_enforces_missing_safety_tools(monkeypatch) -> None:
    fake_profile = SimpleNamespace(display_name="Demo Agent", schema_strict=False)
    monkeypatch.setattr(admin, "PROFILES", {"demo": fake_profile})
    monkeypatch.setattr(
        admin,
        "_load_contract",
        lambda: {
            "safety_critical_tools": ["must_have"],
            "expected_tools": {"demo": []},
        },
    )

    fake_tool_manager = SimpleNamespace(list_tools=lambda: [])
    fake_mcp_server = types.SimpleNamespace(mcp=SimpleNamespace(_tool_manager=fake_tool_manager))
    monkeypatch.setitem(__import__("sys").modules, "mcp_server", fake_mcp_server)

    args = SimpleNamespace(agent="demo", dry_run=False, fix=False)
    with pytest.raises(SystemExit):
        admin.cmd_doctor(args)  # type: ignore[arg-type]


def test_cmd_doctor_flags_missing_expected_tools(monkeypatch) -> None:
    fake_profile = SimpleNamespace(display_name="Demo Agent", schema_strict=False)
    monkeypatch.setattr(admin, "PROFILES", {"demo": fake_profile})
    monkeypatch.setattr(
        admin,
        "_load_contract",
        lambda: {
            "safety_critical_tools": [],
            "expected_tools": {"demo": ["lint_project"]},
        },
    )

    fake_tools = [SimpleNamespace(name="lint_files")]
    fake_tool_manager = SimpleNamespace(list_tools=lambda: fake_tools)
    fake_mcp_server = types.SimpleNamespace(mcp=SimpleNamespace(_tool_manager=fake_tool_manager))
    monkeypatch.setitem(__import__("sys").modules, "mcp_server", fake_mcp_server)

    args = SimpleNamespace(agent="demo", dry_run=False, fix=False)
    with pytest.raises(SystemExit):
        admin.cmd_doctor(args)  # type: ignore[arg-type]


def test_cmd_doctor_missing_expected_with_fix_prints_hint(monkeypatch) -> None:
    fake_profile = SimpleNamespace(display_name="Demo Agent", schema_strict=False)
    monkeypatch.setattr(admin, "PROFILES", {"demo": fake_profile})
    monkeypatch.setattr(
        admin,
        "_load_contract",
        lambda: {
            "safety_critical_tools": [],
            "expected_tools": {"demo": ["lint_project"]},
        },
    )

    fake_tools = [SimpleNamespace(name="lint_files")]
    fake_tool_manager = SimpleNamespace(list_tools=lambda: fake_tools)
    fake_mcp_server = types.SimpleNamespace(mcp=SimpleNamespace(_tool_manager=fake_tool_manager))
    monkeypatch.setitem(__import__("sys").modules, "mcp_server", fake_mcp_server)

    args = SimpleNamespace(agent="demo", dry_run=False, fix=True)
    with pytest.raises(SystemExit):
        admin.cmd_doctor(args)  # type: ignore[arg-type]


def test_cmd_doctor_schema_error_path(monkeypatch) -> None:
    fake_profile = SimpleNamespace(display_name="Demo Agent", schema_strict=False)
    monkeypatch.setattr(admin, "PROFILES", {"demo": fake_profile})
    monkeypatch.setattr(
        admin,
        "_load_contract",
        lambda: {"safety_critical_tools": [], "expected_tools": {"demo": []}},
    )

    fake_tools = [SimpleNamespace(name="lint_files")]
    fake_tool_manager = SimpleNamespace(list_tools=lambda: fake_tools)
    fake_mcp_server = types.SimpleNamespace(mcp=SimpleNamespace(_tool_manager=fake_tool_manager))
    monkeypatch.setitem(__import__("sys").modules, "mcp_server", fake_mcp_server)

    from lintgate.mcp_schema import ProviderSchemaError

    monkeypatch.setattr(
        "lintgate.mcp_schema.compile_and_validate_schemas",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            ProviderSchemaError("bad schema", "demo", "inputSchema")
        ),
    )

    args = SimpleNamespace(agent="demo", dry_run=False, fix=False)
    with pytest.raises(SystemExit):
        admin.cmd_doctor(args)  # type: ignore[arg-type]


def test_cmd_doctor_fix_path_runs_install(monkeypatch) -> None:
    fake_profile = SimpleNamespace(display_name="Demo Agent", schema_strict=False)
    monkeypatch.setattr(admin, "PROFILES", {"demo": fake_profile})
    monkeypatch.setattr(
        admin,
        "_load_contract",
        lambda: {
            "safety_critical_tools": [],
            "expected_tools": {"demo": ["lint_files"]},
        },
    )

    fake_tools = [SimpleNamespace(name="lint_files")]
    fake_tool_manager = SimpleNamespace(list_tools=lambda: fake_tools)
    fake_mcp_server = types.SimpleNamespace(mcp=SimpleNamespace(_tool_manager=fake_tool_manager))
    monkeypatch.setitem(__import__("sys").modules, "mcp_server", fake_mcp_server)

    called = {"install": False}

    def _fake_install(_args):  # type: ignore[no-untyped-def]
        called["install"] = True
        return 0

    monkeypatch.setattr(admin, "cmd_install", _fake_install)

    args = SimpleNamespace(agent="demo", dry_run=False, fix=True)
    assert admin.cmd_doctor(args) == 0  # type: ignore[arg-type]
    assert called["install"] is True


def test_cmd_doctor_fails_unrunnable_configured_command(tmp_path, monkeypatch) -> None:
    config_path = tmp_path / "claude.json"
    config_path.write_text(
        json.dumps({"mcpServers": {"lintgate": {"command": "/missing/lintgate-mcp", "args": []}}}),
        encoding="utf-8",
    )
    fake_profile = SimpleNamespace(
        display_name="Demo Agent",
        schema_strict=False,
        config_path=config_path,
    )
    monkeypatch.setattr(admin, "PROFILES", {"demo": fake_profile})
    monkeypatch.setattr(
        admin,
        "_load_contract",
        lambda: {"safety_critical_tools": [], "expected_tools": {"demo": []}},
    )

    args = SimpleNamespace(agent="demo", dry_run=False, fix=False)
    with pytest.raises(SystemExit):
        admin.cmd_doctor(args)  # type: ignore[arg-type]


def test_cmd_doctor_fix_path_exits_when_install_repair_fails(tmp_path, monkeypatch) -> None:
    config_path = tmp_path / "claude.json"
    config_path.write_text(
        json.dumps({"mcpServers": {"lintgate": {"command": "/missing/lintgate-mcp", "args": []}}}),
        encoding="utf-8",
    )
    fake_profile = SimpleNamespace(
        display_name="Demo Agent",
        schema_strict=False,
        config_path=config_path,
    )
    monkeypatch.setattr(admin, "PROFILES", {"demo": fake_profile})
    monkeypatch.setattr(
        admin,
        "_load_contract",
        lambda: {"safety_critical_tools": [], "expected_tools": {"demo": []}},
    )
    monkeypatch.setattr(admin, "cmd_install", lambda _args: 1)

    args = SimpleNamespace(agent="demo", dry_run=False, fix=True)
    with pytest.raises(SystemExit):
        admin.cmd_doctor(args)  # type: ignore[arg-type]


def test_main_routes_subcommands(monkeypatch) -> None:
    monkeypatch.setattr(admin, "cmd_install", lambda _args: 11)
    monkeypatch.setattr(admin, "cmd_bootstrap", lambda _args: 12)
    monkeypatch.setattr(admin, "cmd_doctor", lambda _args: 13)

    monkeypatch.setattr("sys.argv", ["admin.py", "install", "--agent", "claude"])
    assert admin.main() == 11

    monkeypatch.setattr("sys.argv", ["admin.py", "bootstrap", "--agent", "claude"])
    assert admin.main() == 12

    monkeypatch.setattr("sys.argv", ["admin.py", "doctor", "--agent", "claude"])
    assert admin.main() == 13


def test_main_returns_zero_for_unknown_command(monkeypatch) -> None:
    monkeypatch.setattr(
        admin.argparse.ArgumentParser,
        "parse_args",
        lambda _self: SimpleNamespace(command="unknown"),
    )
    assert admin.main() == 0
