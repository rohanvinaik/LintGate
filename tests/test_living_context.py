"""Tests for Phase 3: Incremental Context Bootstrap with Patch Protocol."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

from lintgate.context_bootstrap import (
    ContextPatch,
    _migrate_to_managed_sections,
    _parse_managed_sections,
    apply_context_patch,
    generate_context_patch,
)
from lintgate.controlplane.session_memory import SessionMemory

# ── Sample CLAUDE.md with markers ────────────────────────────────────

SAMPLE_CLAUDE_MD = """\
# CLAUDE.md

## Project
- Name: myproject
- Mission: Build great software

## Working Mode
- Start with a short plan for non-trivial edits.

<!-- LINTGATE:BEGIN theory_alignment v1 -->
## Theory-Aligned Development
- Core theory: Test first
- Preferred approach: Composition over inheritance
<!-- LINTGATE:END theory_alignment -->

<!-- LINTGATE:BEGIN do_dont v1 -->
## Do / Do Not
- DO: Write tests
- DO NOT: Skip reviews
<!-- LINTGATE:END do_dont -->

<!-- LINTGATE:BEGIN machine_rules v1 -->
## Machine-Enforceable Rules (LintGate)
# LINTGATE_FORBID_REGEX: debugger
<!-- LINTGATE:END machine_rules -->

<!-- LINTGATE:BEGIN context_map v1 -->
## Context Map
- `.claude/rules/theory.md` - theory summaries
<!-- LINTGATE:END context_map -->

## Maintenance
- Keep under ~300 lines.
"""

# ── Pre-upgrade CLAUDE.md without markers ────────────────────────────

SAMPLE_UNMARKED_CLAUDE_MD = """\
# CLAUDE.md

## Project
- Name: myproject

## Working Mode
- Keep changes small.

## Theory-Aligned Development
- Core theory: Test first
- Preferred approach: Composition

## Do / Do Not
- DO: Write tests
- DO NOT: Skip reviews

## Machine-Enforceable Rules (LintGate)
# LINTGATE_FORBID_REGEX: debugger

## Context Map
- `.claude/rules/theory.md` - theory summaries

## Maintenance
- Keep under ~300 lines.
"""


# ── _parse_managed_sections ──────────────────────────────────────────


class TestParseManagedSections:
    def test_parses_all_sections(self) -> None:
        sections = _parse_managed_sections(SAMPLE_CLAUDE_MD)
        assert "theory_alignment" in sections
        assert "do_dont" in sections
        assert "machine_rules" in sections
        assert "context_map" in sections

    def test_section_content(self) -> None:
        sections = _parse_managed_sections(SAMPLE_CLAUDE_MD)
        theory = sections["theory_alignment"]
        assert "Core theory: Test first" in theory.content
        assert theory.version == 1

    def test_section_positions(self) -> None:
        sections = _parse_managed_sections(SAMPLE_CLAUDE_MD)
        for section in sections.values():
            assert section.start_pos < section.end_pos
            assert section.start_pos >= 0

    def test_empty_text(self) -> None:
        sections = _parse_managed_sections("")
        assert sections == {}

    def test_no_markers(self) -> None:
        sections = _parse_managed_sections("# CLAUDE.md\n\nSome content")
        assert sections == {}

    def test_unclosed_marker(self) -> None:
        text = "<!-- LINTGATE:BEGIN test_section v1 -->\nContent without end"
        sections = _parse_managed_sections(text)
        assert sections == {}


# ── _migrate_to_managed_sections ─────────────────────────────────────


class TestMigrateToManagedSections:
    def test_adds_markers_to_unmarked_file(self) -> None:
        migrated, ids = _migrate_to_managed_sections(SAMPLE_UNMARKED_CLAUDE_MD)
        assert "LINTGATE:BEGIN" in migrated
        assert "LINTGATE:END" in migrated
        assert len(ids) > 0

    def test_migrates_theory_alignment(self) -> None:
        migrated, ids = _migrate_to_managed_sections(SAMPLE_UNMARKED_CLAUDE_MD)
        assert "theory_alignment" in ids
        sections = _parse_managed_sections(migrated)
        assert "theory_alignment" in sections

    def test_migrates_do_dont(self) -> None:
        migrated, ids = _migrate_to_managed_sections(SAMPLE_UNMARKED_CLAUDE_MD)
        assert "do_dont" in ids
        sections = _parse_managed_sections(migrated)
        assert "do_dont" in sections

    def test_migrates_machine_rules(self) -> None:
        migrated, ids = _migrate_to_managed_sections(SAMPLE_UNMARKED_CLAUDE_MD)
        assert "machine_rules" in ids
        sections = _parse_managed_sections(migrated)
        assert "machine_rules" in sections

    def test_already_marked_passes_through(self) -> None:
        migrated, ids = _migrate_to_managed_sections(SAMPLE_CLAUDE_MD)
        assert ids == []
        assert migrated == SAMPLE_CLAUDE_MD

    def test_preserves_content(self) -> None:
        migrated, _ = _migrate_to_managed_sections(SAMPLE_UNMARKED_CLAUDE_MD)
        # Original content should still be present
        assert "Core theory: Test first" in migrated
        assert "DO NOT: Skip reviews" in migrated
        assert "LINTGATE_FORBID_REGEX: debugger" in migrated
        assert "Keep under ~300 lines" in migrated


# ── ContextPatch ─────────────────────────────────────────────────────


class TestContextPatch:
    def test_defaults(self) -> None:
        patch = ContextPatch()
        assert patch.patch_id == ""
        assert patch.status == "pending"

    def test_roundtrip(self) -> None:
        patch = ContextPatch(
            patch_id="abc123",
            section_id="machine_rules",
            trigger="constraint_accepted",
            old_content="old",
            new_content="new",
            rationale="test",
            status="pending",
        )
        d = patch.to_dict()
        restored = ContextPatch.from_dict(d)
        assert restored.patch_id == "abc123"
        assert restored.section_id == "machine_rules"
        assert restored.trigger == "constraint_accepted"
        assert restored.old_content == "old"
        assert restored.new_content == "new"
        assert restored.status == "pending"

    def test_from_dict_defaults(self) -> None:
        restored = ContextPatch.from_dict({})
        assert restored.patch_id == ""
        assert restored.status == "pending"


# ── generate_context_patch ───────────────────────────────────────────


class TestGenerateContextPatch:
    def _write_claude_md(self, tmp_path: Path, content: str = SAMPLE_CLAUDE_MD) -> str:
        (tmp_path / "CLAUDE.md").write_text(content)
        return str(tmp_path)

    def test_constraint_accepted_targets_machine_rules(self, tmp_path: Path) -> None:
        root = self._write_claude_md(tmp_path)
        patch = generate_context_patch(
            root,
            trigger="constraint_accepted",
            evidence={"rule": "# LINTGATE_FORBID_REGEX: print\\("},
        )
        assert patch is not None
        assert patch.section_id == "machine_rules"
        assert "LINTGATE_FORBID_REGEX: print\\(" in patch.new_content

    def test_prediction_confirmed_targets_do_dont(self, tmp_path: Path) -> None:
        root = self._write_claude_md(tmp_path)
        patch = generate_context_patch(
            root,
            trigger="prediction_confirmed",
            evidence={"entry": "Use global state for config"},
        )
        assert patch is not None
        assert patch.section_id == "do_dont"
        assert "DO NOT: Use global state for config" in patch.new_content

    def test_recurring_behavioral_signal_targets_do_dont(self, tmp_path: Path) -> None:
        root = self._write_claude_md(tmp_path)
        patch = generate_context_patch(
            root,
            trigger="recurring_behavioral_signal",
            evidence={"entry": "Attempt brute force solutions"},
        )
        assert patch is not None
        assert patch.section_id == "do_dont"

    def test_theory_coherence_update_targets_theory_alignment(self, tmp_path: Path) -> None:
        root = self._write_claude_md(tmp_path)
        patch = generate_context_patch(
            root,
            trigger="theory_coherence_update",
            evidence={"update": "Prefer functional patterns"},
        )
        assert patch is not None
        assert patch.section_id == "theory_alignment"

    def test_idempotency_returns_none(self, tmp_path: Path) -> None:
        root = self._write_claude_md(tmp_path)
        patch = generate_context_patch(
            root,
            trigger="constraint_accepted",
            evidence={"rule": "# LINTGATE_FORBID_REGEX: debugger"},
        )
        # "debugger" already exists in the sample
        assert patch is None

    def test_none_when_no_claude_md(self, tmp_path: Path) -> None:
        patch = generate_context_patch(
            str(tmp_path),
            trigger="constraint_accepted",
            evidence={"rule": "test rule"},
        )
        assert patch is None

    def test_none_with_empty_evidence(self, tmp_path: Path) -> None:
        root = self._write_claude_md(tmp_path)
        patch = generate_context_patch(
            root,
            trigger="constraint_accepted",
            evidence={},
        )
        assert patch is None

    def test_unknown_trigger_returns_none(self, tmp_path: Path) -> None:
        root = self._write_claude_md(tmp_path)
        patch = generate_context_patch(
            root,
            trigger="unknown_trigger",
            evidence={"rule": "test"},
        )
        assert patch is None

    def test_migration_on_unmarked_file(self, tmp_path: Path) -> None:
        root = self._write_claude_md(tmp_path, content=SAMPLE_UNMARKED_CLAUDE_MD)
        patch = generate_context_patch(
            root,
            trigger="constraint_accepted",
            evidence={"rule": "# LINTGATE_FORBID_REGEX: eval\\("},
        )
        # Should still work after migration
        assert patch is not None
        assert patch.section_id == "machine_rules"


# ── apply_context_patch ──────────────────────────────────────────────


class TestApplyContextPatch:
    def _setup(self, tmp_path: Path) -> tuple[str, ContextPatch]:
        (tmp_path / "CLAUDE.md").write_text(SAMPLE_CLAUDE_MD)
        root = str(tmp_path)
        patch = generate_context_patch(
            root,
            trigger="constraint_accepted",
            evidence={"rule": "# LINTGATE_FORBID_REGEX: eval\\("},
        )
        assert patch is not None
        return root, patch

    def test_dry_run_returns_diff_without_writing(self, tmp_path: Path) -> None:
        root, patch = self._setup(tmp_path)
        original = (tmp_path / "CLAUDE.md").read_text()
        result = apply_context_patch(root, patch, dry_run=True)
        assert result["applied"] is False
        assert result["dry_run"] is True
        assert result["diff_preview"]["section_id"] == "machine_rules"
        # File unchanged
        assert (tmp_path / "CLAUDE.md").read_text() == original

    def test_apply_writes_file(self, tmp_path: Path) -> None:
        root, patch = self._setup(tmp_path)
        result = apply_context_patch(root, patch, dry_run=False)
        assert result["applied"] is True
        new_content = (tmp_path / "CLAUDE.md").read_text()
        assert "LINTGATE_FORBID_REGEX: eval\\(" in new_content

    def test_apply_increments_version(self, tmp_path: Path) -> None:
        root, patch = self._setup(tmp_path)
        apply_context_patch(root, patch, dry_run=False)
        new_content = (tmp_path / "CLAUDE.md").read_text()
        assert "LINTGATE:BEGIN machine_rules v2" in new_content

    def test_preserves_user_content_outside_markers(self, tmp_path: Path) -> None:
        root, patch = self._setup(tmp_path)
        apply_context_patch(root, patch, dry_run=False)
        new_content = (tmp_path / "CLAUDE.md").read_text()
        # Content outside managed sections preserved
        assert "## Project" in new_content
        assert "## Working Mode" in new_content
        assert "## Maintenance" in new_content
        assert "Keep under ~300 lines" in new_content

    def test_apply_sets_patch_status(self, tmp_path: Path) -> None:
        root, patch = self._setup(tmp_path)
        apply_context_patch(root, patch, dry_run=False)
        assert patch.status == "applied"

    def test_no_claude_md(self, tmp_path: Path) -> None:
        patch = ContextPatch(section_id="machine_rules")
        result = apply_context_patch(str(tmp_path), patch)
        assert result["applied"] is False
        assert "error" in result

    def test_section_not_found(self, tmp_path: Path) -> None:
        (tmp_path / "CLAUDE.md").write_text(SAMPLE_CLAUDE_MD)
        patch = ContextPatch(section_id="nonexistent_section")
        result = apply_context_patch(str(tmp_path), patch)
        assert result["applied"] is False
        assert "not found" in result.get("error", "")


# ── SessionMemory.pending_patches ────────────────────────────────────


class TestSessionMemoryPendingPatches:
    def test_roundtrip(self) -> None:
        session = SessionMemory()
        patch = ContextPatch(
            patch_id="p1",
            section_id="machine_rules",
            trigger="constraint_accepted",
        )
        session.pending_patches.append(patch.to_dict())
        d = session.to_dict()
        restored = SessionMemory.from_dict(d)
        assert len(restored.pending_patches) == 1
        assert restored.pending_patches[0]["patch_id"] == "p1"

    def test_from_dict_without_pending_patches(self) -> None:
        old_data = {"session_id": "abc", "behavior_compass": {}}
        session = SessionMemory.from_dict(old_data)
        assert session.pending_patches == []


# ── Rendered output has markers ──────────────────────────────────────


class TestRenderedOutputHasMarkers:
    def test_render_claude_md_has_markers(self) -> None:
        from lintgate.context_bootstrap import _render_claude_md

        text = _render_claude_md(
            metadata={"name": "test"},
            facet_summaries={},
            anti_patterns=["Avoid shortcuts"],
            rule_lines=["# LINTGATE_FORBID_REGEX: print\\("],
        )
        assert "LINTGATE:BEGIN theory_alignment v1" in text
        assert "LINTGATE:END theory_alignment" in text
        assert "LINTGATE:BEGIN do_dont v1" in text
        assert "LINTGATE:END do_dont" in text
        assert "LINTGATE:BEGIN machine_rules v1" in text
        assert "LINTGATE:END machine_rules" in text
        assert "LINTGATE:BEGIN context_map v1" in text
        assert "LINTGATE:END context_map" in text

    def test_rendered_output_parseable(self) -> None:
        from lintgate.context_bootstrap import _render_claude_md

        text = _render_claude_md(
            metadata={"name": "test"},
            facet_summaries={"core_theory": "Test first"},
            anti_patterns=["Don't skip tests"],
            rule_lines=[],
        )
        sections = _parse_managed_sections(text)
        assert len(sections) == 4
        assert "theory_alignment" in sections
        assert "do_dont" in sections
        assert "machine_rules" in sections
        assert "context_map" in sections
