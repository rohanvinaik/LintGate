"""Tests for lintgate/context_bootstrap_patches.py — full coverage."""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest

from lintgate.context_bootstrap_patches import (
    MANAGED_SECTION_IDS,
    ContextPatch,
    ManagedSection,
    apply_context_patch,
    generate_context_patch,
    migrate_to_managed_sections,
    parse_managed_sections,
    summarize_audit,
)


# ── ContextPatch dataclass ───────────────────────────────────────────────


class TestContextPatch:
    def test_to_dict_roundtrip(self) -> None:
        patch_obj = ContextPatch(
            patch_id="abc123",
            section_id="machine_rules",
            trigger="constraint_accepted",
            old_content="old",
            new_content="new",
            rationale="test reason",
            evidence={"key": "val"},
            coherence_check={"aligned": True},
            status="pending",
            created_at=1000.0,
        )
        d = patch_obj.to_dict()
        assert d["patch_id"] == "abc123"
        assert d["section_id"] == "machine_rules"
        assert d["coherence_check"] == {"aligned": True}

    def test_from_dict(self) -> None:
        data = {
            "patch_id": "xyz",
            "section_id": "do_dont",
            "trigger": "prediction_confirmed",
            "old_content": "old text",
            "new_content": "new text",
            "rationale": "reason",
            "evidence": {"e": 1},
            "coherence_check": None,
            "status": "applied",
            "created_at": 2000.0,
        }
        cp = ContextPatch.from_dict(data)
        assert cp.patch_id == "xyz"
        assert cp.status == "applied"
        assert cp.created_at == 2000.0

    def test_from_dict_defaults(self) -> None:
        cp = ContextPatch.from_dict({})
        assert cp.patch_id == ""
        assert cp.section_id == ""
        assert cp.status == "pending"
        assert cp.created_at == 0.0
        assert cp.evidence == {}
        assert cp.coherence_check is None

    def test_to_dict_from_dict_roundtrip(self) -> None:
        original = ContextPatch(
            patch_id="rt",
            section_id="theory_alignment",
            trigger="theory_coherence_update",
            old_content="a",
            new_content="b",
            rationale="r",
            evidence={"x": [1, 2]},
            status="pending",
            created_at=500.0,
        )
        restored = ContextPatch.from_dict(original.to_dict())
        assert restored.patch_id == original.patch_id
        assert restored.section_id == original.section_id
        assert restored.evidence == original.evidence


# ── parse_managed_sections ───────────────────────────────────────────────


class TestParseManagedSections:
    def test_empty_text(self) -> None:
        assert parse_managed_sections("") == {}

    def test_no_markers(self) -> None:
        assert parse_managed_sections("# Hello\nSome text.") == {}

    def test_single_section(self) -> None:
        text = (
            "preamble\n"
            "<!-- LINTGATE:BEGIN machine_rules v1 -->\n"
            "- MUST do X\n"
            "<!-- LINTGATE:END machine_rules -->\n"
            "postamble"
        )
        sections = parse_managed_sections(text)
        assert "machine_rules" in sections
        sec = sections["machine_rules"]
        assert sec.section_id == "machine_rules"
        assert sec.version == 1
        assert "MUST do X" in sec.content

    def test_multiple_sections(self) -> None:
        text = (
            "<!-- LINTGATE:BEGIN machine_rules v2 -->\n"
            "rule1\n"
            "<!-- LINTGATE:END machine_rules -->\n"
            "gap text\n"
            "<!-- LINTGATE:BEGIN do_dont v3 -->\n"
            "dont1\n"
            "<!-- LINTGATE:END do_dont -->\n"
        )
        sections = parse_managed_sections(text)
        assert len(sections) == 2
        assert sections["machine_rules"].version == 2
        assert sections["do_dont"].version == 3

    def test_missing_end_marker(self) -> None:
        text = "<!-- LINTGATE:BEGIN machine_rules v1 -->\ncontent without end"
        sections = parse_managed_sections(text)
        assert len(sections) == 0

    def test_version_parsed_correctly(self) -> None:
        text = (
            "<!-- LINTGATE:BEGIN context_map v15 -->\n"
            "map data\n"
            "<!-- LINTGATE:END context_map -->\n"
        )
        sections = parse_managed_sections(text)
        assert sections["context_map"].version == 15

    def test_positions_tracked(self) -> None:
        text = (
            "BEFORE"
            "<!-- LINTGATE:BEGIN machine_rules v1 -->"
            "content"
            "<!-- LINTGATE:END machine_rules -->"
            "AFTER"
        )
        sections = parse_managed_sections(text)
        sec = sections["machine_rules"]
        assert sec.start_pos >= 0
        assert sec.end_pos > sec.start_pos


# ── migrate_to_managed_sections ──────────────────────────────────────────


class TestMigrateToManagedSections:
    def test_already_has_markers(self) -> None:
        text = "# Title\n<!-- LINTGATE:BEGIN machine_rules v1 -->\ncontent\n<!-- LINTGATE:END machine_rules -->"
        result, ids = migrate_to_managed_sections(text)
        assert result == text
        assert ids == []

    def test_do_dont_heading(self) -> None:
        text = "# Project\n\n## Do / Do Not\n\n- DO NOT foo\n\n## Other\n\nstuff"
        result, ids = migrate_to_managed_sections(text)
        assert "do_dont" in ids
        assert "<!-- LINTGATE:BEGIN do_dont v1 -->" in result
        assert "<!-- LINTGATE:END do_dont -->" in result

    def test_machine_rules_heading(self) -> None:
        text = "# Proj\n## Machine-Enforceable Rules\n- MUST test\n"
        result, ids = migrate_to_managed_sections(text)
        assert "machine_rules" in ids

    def test_theory_alignment_heading(self) -> None:
        text = "# Proj\n## Theory-Aligned Development\n- Align\n"
        result, ids = migrate_to_managed_sections(text)
        assert "theory_alignment" in ids

    def test_context_map_heading(self) -> None:
        text = "# Proj\n## Context Map\n- map\n"
        result, ids = migrate_to_managed_sections(text)
        assert "context_map" in ids

    def test_multiple_sections_migrated(self) -> None:
        text = (
            "# Proj\n"
            "## Machine Rules\n"
            "r1\n"
            "## Do / Do Not\n"
            "d1\n"
            "## Other\n"
            "stuff\n"
        )
        result, ids = migrate_to_managed_sections(text)
        assert "machine_rules" in ids
        assert "do_dont" in ids
        assert len(ids) == 2

    def test_no_matching_headings(self) -> None:
        text = "# Project\n## Setup\n## Contributing\n"
        result, ids = migrate_to_managed_sections(text)
        assert ids == []
        assert result == text

    def test_open_section_closed_at_eof(self) -> None:
        text = "# Proj\n## Machine Rules\nrule1\nrule2\n"
        result, ids = migrate_to_managed_sections(text)
        assert "machine_rules" in ids
        assert result.endswith("<!-- LINTGATE:END machine_rules -->")

    def test_open_section_closed_by_next_heading(self) -> None:
        text = "# Proj\n## Machine Rules\nrule1\n## Unrelated\nstuff\n"
        result, ids = migrate_to_managed_sections(text)
        assert "machine_rules" in ids
        end_pos = result.index("<!-- LINTGATE:END machine_rules -->")
        unrelated_pos = result.index("## Unrelated")
        assert end_pos < unrelated_pos


# ── summarize_audit ──────────────────────────────────────────────────────


class TestSummarizeAudit:
    def test_empty_audit(self) -> None:
        result = summarize_audit({})
        assert result == {"files": 0, "errors": 0, "warnings": 0, "passes": 0}

    def test_counts_statuses(self) -> None:
        audit = {
            "audit": [
                {"file": "a.md", "status": "pass"},
                {"file": "b.md", "status": "error"},
                {"file": "c.md", "status": "warn"},
                {"file": "d.md", "status": "pass"},
                {"file": "e.md", "status": "error"},
            ]
        }
        result = summarize_audit(audit)
        assert result["files"] == 5
        assert result["errors"] == 2
        assert result["warnings"] == 1
        assert result["passes"] == 2

    def test_unknown_status_ignored(self) -> None:
        audit = {"audit": [{"file": "a.md", "status": "skip"}]}
        result = summarize_audit(audit)
        assert result["files"] == 1
        assert result["errors"] == 0
        assert result["warnings"] == 0
        assert result["passes"] == 0


# ── generate_context_patch ───────────────────────────────────────────────


class TestGenerateContextPatch:
    def _write_claude_md(self, tmp_path: object, content: str) -> str:
        p = tmp_path  # type: ignore[assignment]
        (p / "CLAUDE.md").write_text(content)
        return str(p)

    def test_no_claude_md(self, tmp_path: object) -> None:
        assert generate_context_patch(str(tmp_path), "constraint_accepted", {"rule": "x"}) is None

    def test_constraint_accepted(self, tmp_path: object) -> None:
        content = (
            "# Proj\n"
            "<!-- LINTGATE:BEGIN machine_rules v1 -->\n"
            "- existing rule\n"
            "<!-- LINTGATE:END machine_rules -->\n"
        )
        root = self._write_claude_md(tmp_path, content)
        patch_obj = generate_context_patch(root, "constraint_accepted", {"rule": "- new rule"})
        assert patch_obj is not None
        assert patch_obj.section_id == "machine_rules"
        assert "- new rule" in patch_obj.new_content
        assert patch_obj.status == "pending"

    def test_constraint_accepted_duplicate(self, tmp_path: object) -> None:
        content = (
            "# Proj\n"
            "<!-- LINTGATE:BEGIN machine_rules v1 -->\n"
            "- existing rule\n"
            "<!-- LINTGATE:END machine_rules -->\n"
        )
        root = self._write_claude_md(tmp_path, content)
        result = generate_context_patch(root, "constraint_accepted", {"rule": "- existing rule"})
        assert result is None

    def test_constraint_accepted_empty_rule(self, tmp_path: object) -> None:
        content = (
            "# Proj\n"
            "<!-- LINTGATE:BEGIN machine_rules v1 -->\n"
            "content\n"
            "<!-- LINTGATE:END machine_rules -->\n"
        )
        root = self._write_claude_md(tmp_path, content)
        assert generate_context_patch(root, "constraint_accepted", {"rule": ""}) is None

    def test_prediction_confirmed(self, tmp_path: object) -> None:
        content = (
            "# Proj\n"
            "<!-- LINTGATE:BEGIN do_dont v1 -->\n"
            "- DO NOT foo\n"
            "<!-- LINTGATE:END do_dont -->\n"
        )
        root = self._write_claude_md(tmp_path, content)
        patch_obj = generate_context_patch(
            root, "prediction_confirmed", {"entry": "bar after baz"}
        )
        assert patch_obj is not None
        assert patch_obj.section_id == "do_dont"
        assert "DO NOT: bar after baz" in patch_obj.new_content

    def test_recurring_behavioral_signal(self, tmp_path: object) -> None:
        content = (
            "# Proj\n"
            "<!-- LINTGATE:BEGIN do_dont v1 -->\n"
            "stuff\n"
            "<!-- LINTGATE:END do_dont -->\n"
        )
        root = self._write_claude_md(tmp_path, content)
        patch_obj = generate_context_patch(
            root, "recurring_behavioral_signal", {"entry": "repeat error"}
        )
        assert patch_obj is not None
        assert "DO NOT: repeat error" in patch_obj.new_content

    def test_theory_coherence_update(self, tmp_path: object) -> None:
        content = (
            "# Proj\n"
            "<!-- LINTGATE:BEGIN theory_alignment v1 -->\n"
            "claim1\n"
            "<!-- LINTGATE:END theory_alignment -->\n"
        )
        root = self._write_claude_md(tmp_path, content)
        patch_obj = generate_context_patch(
            root, "theory_coherence_update", {"update": "new alignment info"}
        )
        assert patch_obj is not None
        assert patch_obj.section_id == "theory_alignment"
        assert "- new alignment info" in patch_obj.new_content

    def test_unknown_trigger(self, tmp_path: object) -> None:
        content = (
            "# Proj\n"
            "<!-- LINTGATE:BEGIN machine_rules v1 -->\n"
            "r\n"
            "<!-- LINTGATE:END machine_rules -->\n"
        )
        root = self._write_claude_md(tmp_path, content)
        assert generate_context_patch(root, "unknown_trigger", {}) is None

    def test_missing_section_returns_none(self, tmp_path: object) -> None:
        content = (
            "# Proj\n"
            "<!-- LINTGATE:BEGIN context_map v1 -->\n"
            "map\n"
            "<!-- LINTGATE:END context_map -->\n"
        )
        root = self._write_claude_md(tmp_path, content)
        # machine_rules section is missing
        assert generate_context_patch(root, "constraint_accepted", {"rule": "x"}) is None

    def test_migration_before_patch(self, tmp_path: object) -> None:
        # CLAUDE.md has no markers, but has matching headings
        content = "# Proj\n## Machine Rules\n- old rule\n## Other\nstuff\n"
        root = self._write_claude_md(tmp_path, content)
        patch_obj = generate_context_patch(root, "constraint_accepted", {"rule": "- new rule"})
        assert patch_obj is not None

    def test_prediction_confirmed_empty_entry(self, tmp_path: object) -> None:
        content = (
            "# Proj\n"
            "<!-- LINTGATE:BEGIN do_dont v1 -->\n"
            "x\n"
            "<!-- LINTGATE:END do_dont -->\n"
        )
        root = self._write_claude_md(tmp_path, content)
        assert generate_context_patch(root, "prediction_confirmed", {"entry": ""}) is None

    def test_theory_update_empty_update(self, tmp_path: object) -> None:
        content = (
            "# Proj\n"
            "<!-- LINTGATE:BEGIN theory_alignment v1 -->\n"
            "x\n"
            "<!-- LINTGATE:END theory_alignment -->\n"
        )
        root = self._write_claude_md(tmp_path, content)
        assert generate_context_patch(root, "theory_coherence_update", {"update": ""}) is None

    def test_rationale_from_evidence(self, tmp_path: object) -> None:
        content = (
            "# Proj\n"
            "<!-- LINTGATE:BEGIN machine_rules v1 -->\n"
            "existing\n"
            "<!-- LINTGATE:END machine_rules -->\n"
        )
        root = self._write_claude_md(tmp_path, content)
        patch_obj = generate_context_patch(
            root,
            "constraint_accepted",
            {"rule": "- new", "rationale": "custom rationale"},
        )
        assert patch_obj is not None
        assert patch_obj.rationale == "custom rationale"


# ── apply_context_patch ──────────────────────────────────────────────────


class TestApplyContextPatch:
    def _setup(self, tmp_path: object) -> tuple:
        p = tmp_path  # type: ignore[assignment]
        content = (
            "preamble\n"
            "<!-- LINTGATE:BEGIN machine_rules v1 -->\n"
            "- rule1\n"
            "<!-- LINTGATE:END machine_rules -->\n"
            "postamble"
        )
        (p / "CLAUDE.md").write_text(content)
        patch_obj = ContextPatch(
            patch_id="test1",
            section_id="machine_rules",
            old_content="\n- rule1\n",
            new_content="\n- rule1\n- rule2\n",
        )
        return str(p), patch_obj

    def test_dry_run(self, tmp_path: object) -> None:
        root, patch_obj = self._setup(tmp_path)
        result = apply_context_patch(root, patch_obj, dry_run=True)
        assert result["applied"] is False
        assert result["dry_run"] is True
        assert "diff_preview" in result
        assert result["diff_preview"]["old_version"] == 1
        assert result["diff_preview"]["new_version"] == 2

    def test_actual_apply(self, tmp_path: object) -> None:
        root, patch_obj = self._setup(tmp_path)
        with patch("lintgate.context_auditor.audit_context_health") as mock_audit:
            mock_audit.return_value = {
                "audit": [{"file": "CLAUDE.md", "status": "pass"}]
            }
            result = apply_context_patch(root, patch_obj, dry_run=False)
        assert result["applied"] is True
        assert result["diff_preview"]["new_version"] == 2
        assert patch_obj.status == "applied"
        # Verify file was written
        import pathlib
        written = (pathlib.Path(root) / "CLAUDE.md").read_text()
        assert "v2" in written
        assert "rule2" in written

    def test_no_claude_md(self, tmp_path: object) -> None:
        patch_obj = ContextPatch(section_id="machine_rules")
        result = apply_context_patch(str(tmp_path), patch_obj, dry_run=False)
        assert result["applied"] is False
        assert "error" in result

    def test_missing_section(self, tmp_path: object) -> None:
        p = tmp_path  # type: ignore[assignment]
        content = (
            "<!-- LINTGATE:BEGIN context_map v1 -->\n"
            "map\n"
            "<!-- LINTGATE:END context_map -->\n"
        )
        (p / "CLAUDE.md").write_text(content)
        patch_obj = ContextPatch(section_id="machine_rules", new_content="x")
        result = apply_context_patch(str(p), patch_obj, dry_run=False)
        assert result["applied"] is False
        assert "not found" in result["error"]

    def test_migration_on_apply(self, tmp_path: object) -> None:
        p = tmp_path  # type: ignore[assignment]
        content = "# Proj\n## Machine Rules\n- rule1\n## Other\nstuff\n"
        (p / "CLAUDE.md").write_text(content)
        patch_obj = ContextPatch(
            section_id="machine_rules",
            new_content="\n- rule1\n- rule2\n",
        )
        with patch("lintgate.context_auditor.audit_context_health") as mock_audit:
            mock_audit.return_value = {"audit": []}
            result = apply_context_patch(str(p), patch_obj, dry_run=False)
        assert result["applied"] is True
        assert result["diff_preview"]["migrated"] is True

    def test_audit_exception_handled(self, tmp_path: object) -> None:
        root, patch_obj = self._setup(tmp_path)
        with patch("lintgate.context_auditor.audit_context_health") as mock_audit:
            mock_audit.side_effect = RuntimeError("audit crash")
            result = apply_context_patch(root, patch_obj, dry_run=False)
        assert result["applied"] is True
        assert result["validation"] is None


# ── MANAGED_SECTION_IDS constant ─────────────────────────────────────────


class TestConstants:
    def test_managed_section_ids(self) -> None:
        assert "machine_rules" in MANAGED_SECTION_IDS
        assert "do_dont" in MANAGED_SECTION_IDS
        assert "theory_alignment" in MANAGED_SECTION_IDS
        assert "context_map" in MANAGED_SECTION_IDS
        assert len(MANAGED_SECTION_IDS) == 4
