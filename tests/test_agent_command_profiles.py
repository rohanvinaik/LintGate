"""Tests for lintgate.agent_command_profiles."""

from __future__ import annotations

from pathlib import Path

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


def test_candidate_with_suffix_increments_existing_collisions(tmp_path) -> None:
    path = tmp_path / "plan.toml"
    (tmp_path / "plan.toml.bak").write_text("x", encoding="utf-8")
    (tmp_path / "plan.toml.bak.1").write_text("x", encoding="utf-8")
    candidate = acp._candidate_with_suffix(path, ".bak")
    assert candidate.name == "plan.toml.bak.2"


def test_legacy_prompt_extracts_all_known_fields() -> None:
    parsed = {
        "system_instructions": {"role": "Architect", "goal": "Ship", "constraints": "No regressions"},
        "project": {"name": "Atlas", "type": "library", "objective": "Refactor"},
        "framework": {"name": "FastAPI"},
        "steps": [{"name": "Inspect"}, "skip-me", {"description": "Validate"}],
        "output": {"format": "markdown"},
    }
    prompt, description = acp._legacy_prompt(parsed, "plan.toml", "raw text")
    assert "Role: Architect" in prompt
    assert "Goal: Ship" in prompt
    assert "Constraints: No regressions" in prompt
    assert "Project: Atlas" in prompt
    assert "Project type: library" in prompt
    assert "Objective: Refactor" in prompt
    assert "Framework: FastAPI" in prompt
    assert "Plan:" in prompt
    assert "1. Inspect" in prompt
    assert "3. Validate" in prompt
    assert "Output format: markdown" in prompt
    assert description == "Migrated command profile file: Atlas"


def test_is_valid_schema_rejects_non_dict_and_bad_types(tmp_path) -> None:
    profile = acp.CommandProfile(id="x", commands_dir=tmp_path, templates=())
    assert acp._is_valid_schema([], profile) is False
    assert acp._is_valid_schema({"prompt": "   "}, profile) is False
    assert acp._is_valid_schema({"prompt": "ok", "description": 42}, profile) is False


def test_sync_template_existing_file_hits_skip_branch(tmp_path, monkeypatch) -> None:
    commands_dir = tmp_path / ".gemini" / "commands"
    commands_dir.mkdir(parents=True)
    existing = commands_dir / "plan.toml"
    existing.write_text('prompt = "ok"\n', encoding="utf-8")
    profile = acp.CommandProfile(
        id="antigravity",
        commands_dir=commands_dir,
        templates=(acp.CommandTemplate(file_name="plan.toml", prompt="p", description="d"),),
    )
    monkeypatch.setattr(acp, "_default_command_profiles", lambda: {"antigravity": profile})

    report = acp.sync_agent_command_profile("antigravity", apply=True)
    assert report is not None
    assert report["summary"]["generated_templates"] == 0
    assert report["summary"]["valid"] == 1


def test_sync_template_write_error_is_reported(tmp_path, monkeypatch) -> None:
    commands_dir = tmp_path / ".gemini" / "commands"
    profile = acp.CommandProfile(
        id="antigravity",
        commands_dir=commands_dir,
        templates=(acp.CommandTemplate(file_name="plan.toml", prompt="p", description="d"),),
    )
    monkeypatch.setattr(acp, "_default_command_profiles", lambda: {"antigravity": profile})

    original_write_text = Path.write_text

    def _raise_for_plan(path_obj: Path, *args, **kwargs):  # type: ignore[no-untyped-def]
        if path_obj == commands_dir / "plan.toml":
            raise OSError("disk full")
        return original_write_text(path_obj, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", _raise_for_plan)

    report = acp.sync_agent_command_profile("antigravity", apply=True)
    assert report is not None
    assert report["blocking_issues"] == 1
    assert report["summary"]["errors"] == 1
    assert any(e.get("status") == "error" and "template_write_error" in e.get("reason", "") for e in report["entries"])


def test_sync_read_error_is_reported(tmp_path, monkeypatch) -> None:
    commands_dir = tmp_path / ".gemini" / "commands"
    commands_dir.mkdir(parents=True)
    target = commands_dir / "plan.toml"
    target.write_text('prompt = "ok"\n', encoding="utf-8")
    profile = acp.CommandProfile(id="antigravity", commands_dir=commands_dir, templates=())
    monkeypatch.setattr(acp, "_default_command_profiles", lambda: {"antigravity": profile})

    original_read_text = Path.read_text

    def _raise_for_plan(path_obj: Path, *args, **kwargs):  # type: ignore[no-untyped-def]
        if path_obj == target:
            raise OSError("permission denied")
        return original_read_text(path_obj, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", _raise_for_plan)

    report = acp.sync_agent_command_profile("antigravity", apply=False)
    assert report is not None
    assert report["blocking_issues"] == 1
    assert report["summary"]["errors"] == 1
    assert report["entries"][0]["status"] == "error"
    assert "read_error" in report["entries"][0]["reason"]
