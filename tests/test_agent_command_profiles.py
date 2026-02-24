"""Tests for lintgate.agent_command_profiles."""

from __future__ import annotations

import tomllib

from lintgate import agent_command_profiles as acp


def test_sync_returns_none_for_unknown_agent() -> None:
    assert acp.sync_agent_command_profile("unknown-agent", apply=True) is None


def test_sync_generates_template_for_antigravity(tmp_path, monkeypatch) -> None:
    profile = acp.CommandProfile(
        id="antigravity",
        commands_dir=tmp_path / ".gemini" / "commands",
        templates=(
            acp.CommandTemplate(
                file_name="plan.toml",
                prompt="Plan this task.",
                description="Planning helper.",
            ),
        ),
    )
    monkeypatch.setattr(acp, "_default_command_profiles", lambda: {"antigravity": profile})

    report = acp.sync_agent_command_profile("antigravity", apply=True)
    assert report is not None
    assert report["blocking_issues"] == 0
    assert report["summary"]["generated_templates"] == 1
    generated = profile.commands_dir / "plan.toml"
    parsed = tomllib.loads(generated.read_text(encoding="utf-8"))
    assert parsed["prompt"] == "Plan this task."
    assert parsed["description"] == "Planning helper."


def test_sync_migrates_legacy_command_schema(tmp_path, monkeypatch) -> None:
    commands_dir = tmp_path / ".gemini" / "commands"
    commands_dir.mkdir(parents=True)
    legacy_file = commands_dir / "plan.toml"
    legacy_text = """
[project]
name = "Legacy Project"

[system_instructions]
goal = "Build safely"

[[steps]]
title = "Inspect state"
"""
    legacy_file.write_text(legacy_text, encoding="utf-8")

    profile = acp.CommandProfile(id="antigravity", commands_dir=commands_dir, templates=())
    monkeypatch.setattr(acp, "_default_command_profiles", lambda: {"antigravity": profile})

    report = acp.sync_agent_command_profile("antigravity", apply=True)
    assert report is not None
    assert report["blocking_issues"] == 0
    assert report["summary"]["migrated"] == 1
    backup = commands_dir / "plan.toml.bak"
    assert backup.exists()
    assert backup.read_text(encoding="utf-8") == legacy_text

    parsed = tomllib.loads(legacy_file.read_text(encoding="utf-8"))
    assert "prompt" in parsed
    assert isinstance(parsed["prompt"], str)
    assert parsed["prompt"]
    assert set(parsed) == {"prompt", "description"}


def test_sync_recovers_invalid_toml(tmp_path, monkeypatch) -> None:
    commands_dir = tmp_path / ".gemini" / "commands"
    commands_dir.mkdir(parents=True)
    broken = commands_dir / "plan.toml"
    broken.write_text("{{{{not valid toml", encoding="utf-8")

    profile = acp.CommandProfile(id="antigravity", commands_dir=commands_dir, templates=())
    monkeypatch.setattr(acp, "_default_command_profiles", lambda: {"antigravity": profile})

    report = acp.sync_agent_command_profile("antigravity", apply=True)
    assert report is not None
    assert report["blocking_issues"] == 0
    assert report["summary"]["recovered"] == 1
    assert (commands_dir / "plan.toml.bak").exists()
    parsed = tomllib.loads(broken.read_text(encoding="utf-8"))
    assert set(parsed) == {"prompt", "description"}


def test_sync_reports_blocking_without_apply(tmp_path, monkeypatch) -> None:
    commands_dir = tmp_path / ".gemini" / "commands"
    commands_dir.mkdir(parents=True)
    (commands_dir / "plan.toml").write_text("[project]\nname='legacy'\n", encoding="utf-8")

    profile = acp.CommandProfile(id="antigravity", commands_dir=commands_dir, templates=())
    monkeypatch.setattr(acp, "_default_command_profiles", lambda: {"antigravity": profile})

    report = acp.sync_agent_command_profile("antigravity", apply=False)
    assert report is not None
    assert report["blocking_issues"] == 1
    assert report["entries"][0]["status"] == "needs_migration"


def test_sync_all_command_profiles_runs_registered_profiles(tmp_path, monkeypatch) -> None:
    profile = acp.CommandProfile(
        id="antigravity",
        commands_dir=tmp_path / ".gemini" / "commands",
        templates=(),
    )
    monkeypatch.setattr(acp, "_default_command_profiles", lambda: {"antigravity": profile})

    reports = acp.sync_all_command_profiles(apply=True)
    assert len(reports) == 1
    assert reports[0]["profile"] == "antigravity"


def test_sync_skips_directory_creation_when_disallowed(tmp_path, monkeypatch) -> None:
    commands_dir = tmp_path / ".gemini" / "commands"
    profile = acp.CommandProfile(
        id="antigravity",
        commands_dir=commands_dir,
        templates=(
            acp.CommandTemplate(
                file_name="plan.toml",
                prompt="Plan this task.",
                description="Planning helper.",
            ),
        ),
    )
    monkeypatch.setattr(acp, "_default_command_profiles", lambda: {"antigravity": profile})

    report = acp.sync_agent_command_profile("antigravity", apply=True, allow_create_dirs=False)
    assert report is not None
    assert report["exists"] is False
    assert report["summary"]["generated_templates"] == 0
    assert commands_dir.exists() is False
