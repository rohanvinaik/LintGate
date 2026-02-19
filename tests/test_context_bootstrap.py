from __future__ import annotations

import json

import mcp_server
from lintgate.context_bootstrap import (
    _select_actionable_anti_patterns,
    bootstrap_context_files,
)


def test_bootstrap_context_files_generates_theory_grounded_drafts(tmp_path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "\n".join(
            [
                "[project]",
                'name = "demo"',
                'description = "Demo project for context bootstrap tests"',
            ]
        )
        + "\n"
    )
    (tmp_path / "README.md").write_text("# Demo\n\nA compact demo project.\n")
    (tmp_path / "AGENTS.md").write_text(
        "\n".join(
            [
                "DO NOT import pandas",
                "MUST use typed function signatures",
            ]
        )
        + "\n"
    )
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "THEORY.md").write_text(
        "\n".join(
            [
                "# Core Theory",
                "This system uses compositional transformations because they remain inspectable.",
                "",
                "# Problem-Solving Approach",
                "Rather than one-shot generation, iterate with narrow edits and validation.",
                "",
                "# Anti-Patterns",
                "Using black-box helper scripts will undermine correctness over time.",
            ]
        )
        + "\n"
    )

    payload = bootstrap_context_files(str(tmp_path), write=False, overwrite=False)
    file_map = {entry["relative_path"]: entry for entry in payload["files"]}

    assert ".claude/CLAUDE.md" in file_map
    assert "AGENTS.md" in file_map
    assert ".claude/rules/theory.md" in file_map
    assert file_map[".claude/CLAUDE.md"]["status"] == "planned"
    assert "LINTGATE_FORBID_REGEX:" in file_map[".claude/CLAUDE.md"]["content"]
    assert "DO NOT:" in file_map[".claude/CLAUDE.md"]["content"]


def test_bootstrap_write_respects_overwrite_flag(tmp_path) -> None:
    (tmp_path / "README.md").write_text("# Repo\n\nExample.\n")
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir(parents=True, exist_ok=True)
    (claude_dir / "CLAUDE.md").write_text("custom-sentinel\n")

    first = bootstrap_context_files(
        str(tmp_path),
        write=True,
        overwrite=False,
        include_theory_rules_doc=False,
    )
    first_map = {entry["relative_path"]: entry for entry in first["files"]}
    assert first_map[".claude/CLAUDE.md"]["status"] == "skipped_exists"
    assert (claude_dir / "CLAUDE.md").read_text().strip() == "custom-sentinel"

    second = bootstrap_context_files(
        str(tmp_path),
        write=True,
        overwrite=True,
        include_theory_rules_doc=False,
    )
    second_map = {entry["relative_path"]: entry for entry in second["files"]}
    assert second_map[".claude/CLAUDE.md"]["status"] == "written"
    assert (claude_dir / "CLAUDE.md").read_text().startswith("# ")


def test_select_actionable_anti_patterns_filters_non_negative_claims() -> None:
    claims = [
        "This project introduces a retrieval stack for clinical context.",
        "Using black-box generated scripts will break auditability.",
        "Avoid one-off task-specific helpers that bypass abstractions.",
    ]
    selected = _select_actionable_anti_patterns(claims, max_items=5)

    assert "retrieval stack" not in " ".join(selected).lower()
    assert any("break auditability" in item.lower() for item in selected)
    assert any("avoid one-off task-specific helpers" in item.lower() for item in selected)


def test_mcp_bootstrap_context_files_returns_payload(tmp_path) -> None:
    (tmp_path / "README.md").write_text("# Repo\n\nMinimal description.\n")

    output = mcp_server.bootstrap_context_files(
        path=str(tmp_path),
        write=False,
        include_theory_rules_doc=False,
    )
    payload = json.loads(output)

    rel_paths = {item["relative_path"] for item in payload["files"]}
    assert rel_paths == {".claude/CLAUDE.md", "AGENTS.md", ".claude/rules/inquiry.md"}
    assert payload["source_signals"]["audit_summary"]["files"] >= 0
