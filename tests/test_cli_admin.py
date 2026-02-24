"""Tests for lintgate.cli.admin."""

from __future__ import annotations

import json
import types
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


def test_cmd_install_unknown_agent() -> None:
    args = SimpleNamespace(agent="missing", dry_run=False)
    assert admin.cmd_install(args) == 1


def test_cmd_install_dry_run_and_write_report(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)

    fake_profile = SimpleNamespace(
        config_path=tmp_path / "cfg.json",
        config_writer=lambda _path, _cmd: True,
    )
    monkeypatch.setattr(admin, "PROFILES", {"demo": fake_profile})

    dry_run_args = SimpleNamespace(agent="demo", dry_run=True)
    assert admin.cmd_install(dry_run_args) == 0
    assert not (tmp_path / "install_report.json").exists()

    apply_args = SimpleNamespace(agent="demo", dry_run=False)
    assert admin.cmd_install(apply_args) == 0
    report = json.loads((tmp_path / "install_report.json").read_text(encoding="utf-8"))
    assert report["agent"] == "demo"
    assert report["status"] == "configured"


def test_cmd_install_reports_already_configured(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    fake_profile = SimpleNamespace(
        config_path=tmp_path / "cfg.json",
        config_writer=lambda _path, _cmd: False,
    )
    monkeypatch.setattr(admin, "PROFILES", {"demo": fake_profile})

    args = SimpleNamespace(agent="demo", dry_run=False)
    assert admin.cmd_install(args) == 0
    report = json.loads((tmp_path / "install_report.json").read_text(encoding="utf-8"))
    assert report["status"] == "already_configured"


def test_cmd_install_fails_when_command_profile_sync_blocks(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    fake_profile = SimpleNamespace(
        config_path=tmp_path / "cfg.json",
        config_writer=lambda _path, _cmd: True,
    )
    monkeypatch.setattr(admin, "PROFILES", {"antigravity": fake_profile})
    monkeypatch.setattr(
        admin,
        "sync_agent_command_profile",
        lambda *_args, **_kwargs: {"blocking_issues": 1, "summary": {}},
    )

    args = SimpleNamespace(agent="antigravity", dry_run=False)
    assert admin.cmd_install(args) == 1


def test_cmd_install_includes_command_profile_in_report(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    fake_profile = SimpleNamespace(
        config_path=tmp_path / "cfg.json",
        config_writer=lambda _path, _cmd: False,
    )
    monkeypatch.setattr(admin, "PROFILES", {"antigravity": fake_profile})
    monkeypatch.setattr(
        admin,
        "sync_agent_command_profile",
        lambda *_args, **_kwargs: {
            "blocking_issues": 0,
            "summary": {"generated_templates": 1, "migrated": 1, "recovered": 0},
        },
    )

    args = SimpleNamespace(agent="antigravity", dry_run=False)
    assert admin.cmd_install(args) == 0
    report = json.loads((tmp_path / "install_report.json").read_text(encoding="utf-8"))
    assert report["status"] == "already_configured"
    assert report["command_profile"]["blocking_issues"] == 0
    assert report["command_profile"]["summary"]["generated_templates"] == 1


def test_cmd_bootstrap_propagates_install_and_doctor(monkeypatch) -> None:
    args = SimpleNamespace(agent="claude", dry_run=False)
    monkeypatch.setattr(admin, "cmd_install", lambda _args: 2)
    assert admin.cmd_bootstrap(args) == 2

    monkeypatch.setattr(admin, "cmd_install", lambda _args: 0)
    monkeypatch.setattr(admin, "cmd_doctor", lambda _args: 7)
    assert admin.cmd_bootstrap(args) == 7


def test_cmd_doctor_unknown_and_dry_run(monkeypatch) -> None:
    unknown_args = SimpleNamespace(agent="missing", dry_run=True, fix=False)
    assert admin.cmd_doctor(unknown_args) == 1

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
    assert admin.cmd_doctor(dry_run_args) == 0


def test_cmd_doctor_enforces_missing_safety_tools(monkeypatch) -> None:
    fake_profile = SimpleNamespace(display_name="Demo Agent", schema_strict=False)
    monkeypatch.setattr(admin, "PROFILES", {"demo": fake_profile})
    monkeypatch.setattr(
        admin,
        "_load_contract",
        lambda: {"safety_critical_tools": ["must_have"], "expected_tools": {"demo": []}},
    )

    fake_tool_manager = SimpleNamespace(list_tools=lambda: [])
    fake_mcp_server = types.SimpleNamespace(mcp=SimpleNamespace(_tool_manager=fake_tool_manager))
    monkeypatch.setitem(__import__("sys").modules, "mcp_server", fake_mcp_server)

    args = SimpleNamespace(agent="demo", dry_run=False, fix=False)
    with pytest.raises(SystemExit):
        admin.cmd_doctor(args)


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
        admin.cmd_doctor(args)


def test_cmd_doctor_missing_expected_with_fix_prints_hint(monkeypatch) -> None:
    fake_profile = SimpleNamespace(display_name="Demo Agent", schema_strict=False)
    monkeypatch.setattr(admin, "PROFILES", {"demo": fake_profile})
    monkeypatch.setattr(
        admin,
        "_load_contract",
        lambda: {"safety_critical_tools": [], "expected_tools": {"demo": ["lint_project"]}},
    )

    fake_tools = [SimpleNamespace(name="lint_files")]
    fake_tool_manager = SimpleNamespace(list_tools=lambda: fake_tools)
    fake_mcp_server = types.SimpleNamespace(mcp=SimpleNamespace(_tool_manager=fake_tool_manager))
    monkeypatch.setitem(__import__("sys").modules, "mcp_server", fake_mcp_server)

    args = SimpleNamespace(agent="demo", dry_run=False, fix=True)
    with pytest.raises(SystemExit):
        admin.cmd_doctor(args)


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
        admin.cmd_doctor(args)


def test_cmd_doctor_fix_path_runs_install(monkeypatch) -> None:
    fake_profile = SimpleNamespace(display_name="Demo Agent", schema_strict=False)
    monkeypatch.setattr(admin, "PROFILES", {"demo": fake_profile})
    monkeypatch.setattr(
        admin,
        "_load_contract",
        lambda: {"safety_critical_tools": [], "expected_tools": {"demo": ["lint_files"]}},
    )

    fake_tools = [SimpleNamespace(name="lint_files")]
    fake_tool_manager = SimpleNamespace(list_tools=lambda: fake_tools)
    fake_mcp_server = types.SimpleNamespace(mcp=SimpleNamespace(_tool_manager=fake_tool_manager))
    monkeypatch.setitem(__import__("sys").modules, "mcp_server", fake_mcp_server)

    called = {"install": False}
    monkeypatch.setattr(admin, "cmd_install", lambda _args: called.__setitem__("install", True) or 0)

    args = SimpleNamespace(agent="demo", dry_run=False, fix=True)
    assert admin.cmd_doctor(args) == 0
    assert called["install"] is True


def test_cmd_doctor_blocks_when_command_profile_invalid(monkeypatch) -> None:
    fake_profile = SimpleNamespace(display_name="Antigravity", schema_strict=True)
    monkeypatch.setattr(admin, "PROFILES", {"antigravity": fake_profile})
    monkeypatch.setattr(
        admin,
        "_load_contract",
        lambda: {"safety_critical_tools": [], "expected_tools": {"antigravity": []}},
    )
    monkeypatch.setattr(
        admin,
        "sync_agent_command_profile",
        lambda *_args, **_kwargs: {"blocking_issues": 2, "summary": {}},
    )

    args = SimpleNamespace(agent="antigravity", dry_run=False, fix=False)
    with pytest.raises(SystemExit):
        admin.cmd_doctor(args)


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
