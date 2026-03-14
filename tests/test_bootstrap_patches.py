"""Tests for lintgate.context.bootstrap_patches.

Covers: ManagedSection, ContextPatch (dataclass + serde), parse_managed_sections,
migrate_to_managed_sections, summarize_audit, _patch_constraint_accepted,
_patch_do_dont, _patch_theory_coherence, generate_context_patch, apply_context_patch.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING
from unittest import mock

if TYPE_CHECKING:
    from pathlib import Path

from lintgate.context.bootstrap_patches import (
    MANAGED_SECTION_IDS,
    ContextPatch,
    ManagedSection,
    _patch_constraint_accepted,
    _patch_do_dont,
    _patch_theory_coherence,
    apply_context_patch,
    generate_context_patch,
    migrate_to_managed_sections,
    parse_managed_sections,
    summarize_audit,
)

# ── Helpers ──────────────────────────────────────────────────────────────


def _make_managed_text(*section_ids: str, version: int = 1) -> str:
    """Build a CLAUDE.md string with managed section markers."""
    parts = ["# CLAUDE.md\n\nPreamble text.\n"]
    for sid in section_ids:
        parts.append(
            f"<!-- LINTGATE:BEGIN {sid} v{version} -->\n"
            f"Content for {sid}.\n"
            f"<!-- LINTGATE:END {sid} -->\n"
        )
    return "\n".join(parts)


def _make_sections(**kwargs: str) -> dict[str, ManagedSection]:
    """Build a dict of ManagedSection objects from keyword content."""
    sections: dict[str, ManagedSection] = {}
    for sid, content in kwargs.items():
        sections[sid] = ManagedSection(
            section_id=sid,
            version=1,
            content=content,
            start_pos=0,
            end_pos=100,
        )
    return sections


# ── ManagedSection dataclass ─────────────────────────────────────────────


class TestManagedSection:
    def test_fields(self):
        ms = ManagedSection(
            section_id="machine_rules",
            version=3,
            content="some content",
            start_pos=10,
            end_pos=50,
        )
        assert ms.section_id == "machine_rules"
        assert ms.version == 3
        assert ms.content == "some content"
        assert ms.start_pos == 10
        assert ms.end_pos == 50

    def test_zero_version(self):
        ms = ManagedSection(section_id="do_dont", version=0, content="", start_pos=0, end_pos=0)
        assert ms.version == 0
        assert ms.content == ""


# ── ContextPatch dataclass + serde ───────────────────────────────────────


class TestContextPatch:
    def test_defaults(self):
        cp = ContextPatch()
        assert cp.patch_id == ""
        assert cp.section_id == ""
        assert cp.trigger == ""
        assert cp.old_content == ""
        assert cp.new_content == ""
        assert cp.rationale == ""
        assert cp.evidence == {}
        assert cp.coherence_check is None
        assert cp.status == "pending"
        assert cp.created_at == 0.0

    def test_to_dict(self):
        cp = ContextPatch(
            patch_id="abc123",
            section_id="do_dont",
            trigger="prediction_confirmed",
            old_content="old",
            new_content="new",
            rationale="because",
            evidence={"key": "val"},
            coherence_check={"score": 0.9},
            status="applied",
            created_at=1000.0,
        )
        d = cp.to_dict()
        assert d["patch_id"] == "abc123"
        assert d["section_id"] == "do_dont"
        assert d["trigger"] == "prediction_confirmed"
        assert d["old_content"] == "old"
        assert d["new_content"] == "new"
        assert d["rationale"] == "because"
        assert d["evidence"] == {"key": "val"}
        assert d["coherence_check"] == {"score": 0.9}
        assert d["status"] == "applied"
        assert d["created_at"] == 1000.0

    def test_from_dict_full(self):
        d = {
            "patch_id": "xyz",
            "section_id": "machine_rules",
            "trigger": "constraint_accepted",
            "old_content": "o",
            "new_content": "n",
            "rationale": "r",
            "evidence": {"e": 1},
            "coherence_check": {"c": 2},
            "status": "rejected",
            "created_at": 42.0,
        }
        cp = ContextPatch.from_dict(d)
        assert cp.patch_id == "xyz"
        assert cp.section_id == "machine_rules"
        assert cp.status == "rejected"
        assert cp.created_at == 42.0
        assert cp.coherence_check == {"c": 2}

    def test_from_dict_empty(self):
        cp = ContextPatch.from_dict({})
        assert cp.patch_id == ""
        assert cp.status == "pending"
        assert cp.created_at == 0.0
        assert cp.evidence == {}
        assert cp.coherence_check is None

    def test_from_dict_partial(self):
        cp = ContextPatch.from_dict({"patch_id": "p1", "status": "applied"})
        assert cp.patch_id == "p1"
        assert cp.status == "applied"
        assert cp.section_id == ""
        assert cp.trigger == ""

    def test_roundtrip(self):
        original = ContextPatch(
            patch_id="rt",
            section_id="context_map",
            trigger="theory_coherence_update",
            old_content="old text",
            new_content="new text",
            rationale="reason",
            evidence={"nested": {"deep": True}},
            coherence_check=None,
            status="pending",
            created_at=99.9,
        )
        restored = ContextPatch.from_dict(original.to_dict())
        assert restored.to_dict() == original.to_dict()


# ── MANAGED_SECTION_IDS constant ─────────────────────────────────────────


class TestManagedSectionIds:
    def test_is_tuple(self):
        assert isinstance(MANAGED_SECTION_IDS, tuple)

    def test_known_ids(self):
        assert "machine_rules" in MANAGED_SECTION_IDS
        assert "do_dont" in MANAGED_SECTION_IDS
        assert "theory_alignment" in MANAGED_SECTION_IDS
        assert "context_map" in MANAGED_SECTION_IDS

    def test_count(self):
        assert len(MANAGED_SECTION_IDS) == 4


# ── parse_managed_sections ───────────────────────────────────────────────


class TestParseManagedSections:
    def test_empty_string(self):
        result = parse_managed_sections("")
        assert result == {}

    def test_no_markers(self):
        result = parse_managed_sections("# Just a heading\n\nSome text.\n")
        assert result == {}

    def test_single_section(self):
        text = (
            "preamble\n"
            "<!-- LINTGATE:BEGIN machine_rules v1 -->"
            "\nrule one\nrule two\n"
            "<!-- LINTGATE:END machine_rules -->"
            "\npostamble"
        )
        sections = parse_managed_sections(text)
        assert "machine_rules" in sections
        ms = sections["machine_rules"]
        assert ms.section_id == "machine_rules"
        assert ms.version == 1
        assert "rule one" in ms.content
        assert "rule two" in ms.content
        assert ms.start_pos == text.index("<!-- LINTGATE:BEGIN")
        assert ms.end_pos == text.index("<!-- LINTGATE:END machine_rules -->") + len(
            "<!-- LINTGATE:END machine_rules -->"
        )

    def test_multiple_sections(self):
        text = _make_managed_text("machine_rules", "do_dont", "theory_alignment")
        sections = parse_managed_sections(text)
        assert len(sections) == 3
        assert set(sections.keys()) == {"machine_rules", "do_dont", "theory_alignment"}
        for sid in ("machine_rules", "do_dont", "theory_alignment"):
            assert sections[sid].version == 1

    def test_higher_version(self):
        text = "<!-- LINTGATE:BEGIN do_dont v7 -->\ncontent\n<!-- LINTGATE:END do_dont -->"
        sections = parse_managed_sections(text)
        assert sections["do_dont"].version == 7

    def test_unclosed_section_skipped(self):
        text = "<!-- LINTGATE:BEGIN machine_rules v1 -->\nrule\n"
        sections = parse_managed_sections(text)
        assert sections == {}

    def test_extra_whitespace_in_markers(self):
        text = "<!--  LINTGATE:BEGIN  do_dont  v3  -->\ncontent\n<!--  LINTGATE:END  do_dont  -->"
        sections = parse_managed_sections(text)
        assert "do_dont" in sections
        assert sections["do_dont"].version == 3

    def test_content_extraction_exact(self):
        inner = "\nExact content here.\n"
        text = f"<!-- LINTGATE:BEGIN context_map v1 -->{inner}<!-- LINTGATE:END context_map -->"
        sections = parse_managed_sections(text)
        assert sections["context_map"].content == inner

    def test_mismatched_end_marker_skipped(self):
        text = "<!-- LINTGATE:BEGIN machine_rules v1 -->\ncontent\n<!-- LINTGATE:END do_dont -->"
        sections = parse_managed_sections(text)
        assert "machine_rules" not in sections


# ── migrate_to_managed_sections ──────────────────────────────────────────


class TestMigrateToManagedSections:
    def test_already_has_markers(self):
        text = (
            "<!-- LINTGATE:BEGIN machine_rules v1 -->\ncontent\n<!-- LINTGATE:END machine_rules -->"
        )
        result_text, migrated_ids = migrate_to_managed_sections(text)
        assert result_text == text
        assert migrated_ids == []

    def test_empty_string(self):
        result_text, migrated_ids = migrate_to_managed_sections("")
        assert result_text == ""
        assert migrated_ids == []

    def test_no_matching_headings(self):
        text = "# Introduction\n\nSome text.\n\n# Another Section\n\nMore text.\n"
        result_text, migrated_ids = migrate_to_managed_sections(text)
        assert migrated_ids == []
        assert "LINTGATE:BEGIN" not in result_text

    def test_machine_rules_heading(self):
        text = "# Machine-Enforceable Rules\n\nRule 1.\nRule 2.\n"
        result_text, migrated_ids = migrate_to_managed_sections(text)
        assert "machine_rules" in migrated_ids
        assert "<!-- LINTGATE:BEGIN machine_rules v1 -->" in result_text
        assert "<!-- LINTGATE:END machine_rules -->" in result_text

    def test_machine_rules_alt_heading(self):
        text = "## Machine Rules\n\nSome rule.\n"
        result_text, migrated_ids = migrate_to_managed_sections(text)
        assert "machine_rules" in migrated_ids

    def test_do_dont_heading(self):
        text = "# Do / Do Not\n\nDO: thing.\n"
        result_text, migrated_ids = migrate_to_managed_sections(text)
        assert "do_dont" in migrated_ids

    def test_do_dont_alt_heading(self):
        text = "# Do/Do Not\n\nDO: thing.\n"
        result_text, migrated_ids = migrate_to_managed_sections(text)
        assert "do_dont" in migrated_ids

    def test_theory_alignment_heading(self):
        text = "# Theory-Aligned Development\n\nClaim.\n"
        result_text, migrated_ids = migrate_to_managed_sections(text)
        assert "theory_alignment" in migrated_ids

    def test_context_map_heading(self):
        text = "# Context Map\n\nPath info.\n"
        result_text, migrated_ids = migrate_to_managed_sections(text)
        assert "context_map" in migrated_ids

    def test_multiple_sections_migrated(self):
        text = (
            "# Machine-Enforceable Rules\n\nRule.\n\n"
            "# Do / Do Not\n\nDO: x.\n\n"
            "# Context Map\n\nPaths.\n"
        )
        result_text, migrated_ids = migrate_to_managed_sections(text)
        assert set(migrated_ids) == {"machine_rules", "do_dont", "context_map"}
        # Each migrated section should have BEGIN and END markers
        for sid in migrated_ids:
            assert f"<!-- LINTGATE:BEGIN {sid} v1 -->" in result_text
            assert f"<!-- LINTGATE:END {sid} -->" in result_text

    def test_section_closed_at_next_heading(self):
        text = "# Machine-Enforceable Rules\n\nRule.\n\n# Unrelated Heading\n\nOther stuff.\n"
        result_text, migrated_ids = migrate_to_managed_sections(text)
        assert "machine_rules" in migrated_ids
        # END should appear before the unrelated heading
        end_pos = result_text.index("<!-- LINTGATE:END machine_rules -->")
        unrelated_pos = result_text.index("# Unrelated Heading")
        assert end_pos < unrelated_pos

    def test_section_closed_at_eof(self):
        text = "# Machine-Enforceable Rules\n\nRule.\nAnother rule."
        result_text, migrated_ids = migrate_to_managed_sections(text)
        assert "machine_rules" in migrated_ids
        assert result_text.rstrip().endswith("<!-- LINTGATE:END machine_rules -->")

    def test_heading_with_parenthetical_stripped(self):
        text = "# Machine-Enforceable Rules (auto-generated)\n\nRule.\n"
        result_text, migrated_ids = migrate_to_managed_sections(text)
        assert "machine_rules" in migrated_ids

    def test_case_insensitive_matching(self):
        text = "# MACHINE-ENFORCEABLE RULES\n\nRule.\n"
        result_text, migrated_ids = migrate_to_managed_sections(text)
        assert "machine_rules" in migrated_ids

    def test_migrated_text_parseable(self):
        text = "# Preamble\n\n# Machine-Enforceable Rules\n\nRule.\n\n# Do / Do Not\n\nDO: x.\n"
        result_text, migrated_ids = migrate_to_managed_sections(text)
        sections = parse_managed_sections(result_text)
        assert set(sections.keys()) == set(migrated_ids)
        for sid in migrated_ids:
            assert sections[sid].version == 1


# ── summarize_audit ──────────────────────────────────────────────────────


class TestSummarizeAudit:
    def test_empty_audit(self):
        result = summarize_audit({})
        assert result == {"files": 0, "errors": 0, "warnings": 0, "passes": 0}

    def test_empty_audit_list(self):
        result = summarize_audit({"audit": []})
        assert result == {"files": 0, "errors": 0, "warnings": 0, "passes": 0}

    def test_all_pass(self):
        audit = {"audit": [{"status": "pass"}, {"status": "pass"}]}
        result = summarize_audit(audit)
        assert result == {"files": 2, "errors": 0, "warnings": 0, "passes": 2}

    def test_all_error(self):
        audit = {"audit": [{"status": "error"}, {"status": "error"}, {"status": "error"}]}
        result = summarize_audit(audit)
        assert result == {"files": 3, "errors": 3, "warnings": 0, "passes": 0}

    def test_mixed_statuses(self):
        audit = {
            "audit": [
                {"status": "pass"},
                {"status": "error"},
                {"status": "warn"},
                {"status": "pass"},
                {"status": "warn"},
            ]
        }
        result = summarize_audit(audit)
        assert result == {"files": 5, "errors": 1, "warnings": 2, "passes": 2}

    def test_unknown_status_counted_as_file_only(self):
        audit = {"audit": [{"status": "info"}, {"status": "pass"}]}
        result = summarize_audit(audit)
        assert result["files"] == 2
        assert result["passes"] == 1
        assert result["errors"] == 0
        assert result["warnings"] == 0

    def test_missing_status_key(self):
        audit = {"audit": [{"file": "x.md"}]}
        result = summarize_audit(audit)
        assert result == {"files": 1, "errors": 0, "warnings": 0, "passes": 0}


# ── _patch_constraint_accepted ───────────────────────────────────────────


class TestPatchConstraintAccepted:
    def test_empty_rule(self):
        sections = _make_sections(machine_rules="\nexisting\n")
        assert _patch_constraint_accepted(sections, {"rule": ""}) is None

    def test_no_rule_key(self):
        sections = _make_sections(machine_rules="\nexisting\n")
        assert _patch_constraint_accepted(sections, {}) is None

    def test_no_machine_rules_section(self):
        sections = _make_sections(do_dont="\nstuff\n")
        assert _patch_constraint_accepted(sections, {"rule": "new rule"}) is None

    def test_rule_already_present(self):
        sections = _make_sections(machine_rules="\nexisting rule\n")
        assert _patch_constraint_accepted(sections, {"rule": "existing rule"}) is None

    def test_adds_new_rule(self):
        sections = _make_sections(machine_rules="\nexisting rule\n")
        result = _patch_constraint_accepted(sections, {"rule": "new rule"})
        assert result is not None
        section_id, new_content = result
        assert section_id == "machine_rules"
        assert "new rule" in new_content
        assert "existing rule" in new_content

    def test_new_content_format(self):
        sections = _make_sections(machine_rules="\nrule A\n")
        result = _patch_constraint_accepted(sections, {"rule": "rule B"})
        assert result is not None
        _, new_content = result
        # Should end with newline after the new rule
        assert new_content.endswith("rule B\n")


# ── _patch_do_dont ───────────────────────────────────────────────────────


class TestPatchDoDont:
    def test_empty_entry(self):
        sections = _make_sections(do_dont="\nexisting\n")
        assert _patch_do_dont(sections, {"entry": ""}) is None

    def test_no_entry_key(self):
        sections = _make_sections(do_dont="\nexisting\n")
        assert _patch_do_dont(sections, {}) is None

    def test_no_do_dont_section(self):
        sections = _make_sections(machine_rules="\nstuff\n")
        assert _patch_do_dont(sections, {"entry": "avoid X"}) is None

    def test_entry_already_present(self):
        sections = _make_sections(do_dont="\n- DO NOT: avoid X\n")
        assert _patch_do_dont(sections, {"entry": "avoid X"}) is None

    def test_adds_new_entry(self):
        sections = _make_sections(do_dont="\n- DO NOT: old thing\n")
        result = _patch_do_dont(sections, {"entry": "new thing"})
        assert result is not None
        section_id, new_content = result
        assert section_id == "do_dont"
        assert "- DO NOT: new thing" in new_content
        assert "old thing" in new_content

    def test_new_content_format(self):
        sections = _make_sections(do_dont="\nexisting\n")
        result = _patch_do_dont(sections, {"entry": "avoid footguns"})
        assert result is not None
        _, new_content = result
        assert new_content.endswith("- DO NOT: avoid footguns\n")


# ── _patch_theory_coherence ──────────────────────────────────────────────


class TestPatchTheoryCoherence:
    def test_empty_update(self):
        sections = _make_sections(theory_alignment="\nexisting\n")
        assert _patch_theory_coherence(sections, {"update": ""}) is None

    def test_no_update_key(self):
        sections = _make_sections(theory_alignment="\nexisting\n")
        assert _patch_theory_coherence(sections, {}) is None

    def test_no_theory_alignment_section(self):
        sections = _make_sections(do_dont="\nstuff\n")
        assert _patch_theory_coherence(sections, {"update": "new claim"}) is None

    def test_update_already_present(self):
        sections = _make_sections(theory_alignment="\n- existing claim\n")
        assert _patch_theory_coherence(sections, {"update": "existing claim"}) is None

    def test_adds_new_update(self):
        sections = _make_sections(theory_alignment="\n- old claim\n")
        result = _patch_theory_coherence(sections, {"update": "new claim"})
        assert result is not None
        section_id, new_content = result
        assert section_id == "theory_alignment"
        assert "- new claim" in new_content
        assert "old claim" in new_content

    def test_new_content_format(self):
        sections = _make_sections(theory_alignment="\nexisting\n")
        result = _patch_theory_coherence(sections, {"update": "the claim"})
        assert result is not None
        _, new_content = result
        assert new_content.endswith("- the claim\n")


# ── generate_context_patch ───────────────────────────────────────────────


class TestGenerateContextPatch:
    def test_no_claude_md(self, tmp_path: Path):
        result = generate_context_patch(str(tmp_path), "constraint_accepted", {"rule": "x"})
        assert result is None

    def test_unknown_trigger(self, tmp_path: Path):
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text(_make_managed_text("machine_rules", "do_dont", "theory_alignment"))
        result = generate_context_patch(str(tmp_path), "unknown_trigger", {"rule": "x"})
        assert result is None

    def test_constraint_accepted(self, tmp_path: Path):
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text(_make_managed_text("machine_rules", "do_dont", "theory_alignment"))
        result = generate_context_patch(
            str(tmp_path), "constraint_accepted", {"rule": "MUST lint before commit"}
        )
        assert result is not None
        assert isinstance(result, ContextPatch)
        assert result.section_id == "machine_rules"
        assert result.trigger == "constraint_accepted"
        assert "MUST lint before commit" in result.new_content
        assert result.status == "pending"
        assert len(result.patch_id) == 8

    def test_prediction_confirmed(self, tmp_path: Path):
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text(_make_managed_text("machine_rules", "do_dont", "theory_alignment"))
        result = generate_context_patch(
            str(tmp_path), "prediction_confirmed", {"entry": "skip caching"}
        )
        assert result is not None
        assert result.section_id == "do_dont"
        assert "- DO NOT: skip caching" in result.new_content

    def test_recurring_behavioral_signal(self, tmp_path: Path):
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text(_make_managed_text("machine_rules", "do_dont", "theory_alignment"))
        result = generate_context_patch(
            str(tmp_path), "recurring_behavioral_signal", {"entry": "ignore findings"}
        )
        assert result is not None
        assert result.section_id == "do_dont"

    def test_theory_coherence_update(self, tmp_path: Path):
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text(_make_managed_text("machine_rules", "do_dont", "theory_alignment"))
        result = generate_context_patch(
            str(tmp_path), "theory_coherence_update", {"update": "new theory claim"}
        )
        assert result is not None
        assert result.section_id == "theory_alignment"
        assert "- new theory claim" in result.new_content

    def test_handler_returns_none_when_no_data(self, tmp_path: Path):
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text(_make_managed_text("machine_rules", "do_dont", "theory_alignment"))
        result = generate_context_patch(str(tmp_path), "constraint_accepted", {"rule": ""})
        assert result is None

    def test_duplicate_rule_returns_none(self, tmp_path: Path):
        # Build a CLAUDE.md with "existing rule" already in machine_rules
        text = (
            "<!-- LINTGATE:BEGIN machine_rules v1 -->\n"
            "existing rule\n"
            "<!-- LINTGATE:END machine_rules -->\n"
        )
        (tmp_path / "CLAUDE.md").write_text(text)
        result = generate_context_patch(
            str(tmp_path), "constraint_accepted", {"rule": "existing rule"}
        )
        assert result is None

    def test_auto_migrates_unmarked_file(self, tmp_path: Path):
        # CLAUDE.md without markers but with a matching heading
        (tmp_path / "CLAUDE.md").write_text("# Machine-Enforceable Rules\n\nOld rule.\n")
        result = generate_context_patch(str(tmp_path), "constraint_accepted", {"rule": "new rule"})
        assert result is not None
        assert result.section_id == "machine_rules"
        assert "new rule" in result.new_content

    def test_custom_rationale_in_evidence(self, tmp_path: Path):
        (tmp_path / "CLAUDE.md").write_text(_make_managed_text("machine_rules"))
        result = generate_context_patch(
            str(tmp_path),
            "constraint_accepted",
            {"rule": "new rule", "rationale": "custom reason"},
        )
        assert result is not None
        assert result.rationale == "custom reason"

    def test_default_rationale(self, tmp_path: Path):
        (tmp_path / "CLAUDE.md").write_text(_make_managed_text("machine_rules"))
        result = generate_context_patch(
            str(tmp_path),
            "constraint_accepted",
            {"rule": "new rule"},
        )
        assert result is not None
        assert "constraint_accepted" in result.rationale

    def test_created_at_is_recent(self, tmp_path: Path):
        (tmp_path / "CLAUDE.md").write_text(_make_managed_text("machine_rules"))
        before = time.time()
        result = generate_context_patch(str(tmp_path), "constraint_accepted", {"rule": "new rule"})
        after = time.time()
        assert result is not None
        assert before <= result.created_at <= after


# ── apply_context_patch ──────────────────────────────────────────────────


class TestApplyContextPatch:
    def test_no_claude_md(self, tmp_path: Path):
        patch = ContextPatch(section_id="machine_rules", new_content="x")
        result = apply_context_patch(str(tmp_path), patch, dry_run=True)
        assert result["applied"] is False
        assert "error" in result

    def test_section_not_found(self, tmp_path: Path):
        (tmp_path / "CLAUDE.md").write_text(_make_managed_text("machine_rules"))
        patch = ContextPatch(section_id="nonexistent", new_content="x")
        result = apply_context_patch(str(tmp_path), patch, dry_run=True)
        assert result["applied"] is False
        assert "nonexistent" in result["error"]

    def test_dry_run_returns_preview(self, tmp_path: Path):
        (tmp_path / "CLAUDE.md").write_text(_make_managed_text("machine_rules"))
        patch = ContextPatch(
            section_id="machine_rules",
            old_content="Content for machine_rules.",
            new_content="\nContent for machine_rules.\nnew rule\n",
        )
        result = apply_context_patch(str(tmp_path), patch, dry_run=True)
        assert result["applied"] is False
        assert result.get("dry_run") is True
        assert "diff_preview" in result
        preview = result["diff_preview"]
        assert preview["section_id"] == "machine_rules"
        assert preview["old_version"] == 1
        assert preview["new_version"] == 2

    def test_dry_run_does_not_modify_file(self, tmp_path: Path):
        original_text = _make_managed_text("machine_rules")
        (tmp_path / "CLAUDE.md").write_text(original_text)
        patch = ContextPatch(
            section_id="machine_rules",
            new_content="\nnew stuff\n",
        )
        apply_context_patch(str(tmp_path), patch, dry_run=True)
        assert (tmp_path / "CLAUDE.md").read_text() == original_text

    @mock.patch("lintgate.context.auditor.audit_context_health")
    def test_apply_writes_file(self, mock_audit, tmp_path: Path):
        mock_audit.return_value = {"audit": [{"status": "pass"}]}
        (tmp_path / "CLAUDE.md").write_text(_make_managed_text("machine_rules"))
        patch = ContextPatch(
            section_id="machine_rules",
            old_content="\nContent for machine_rules.\n",
            new_content="\nContent for machine_rules.\nnew rule\n",
        )
        result = apply_context_patch(str(tmp_path), patch, dry_run=False)
        assert result["applied"] is True
        assert "diff_preview" in result

        new_text = (tmp_path / "CLAUDE.md").read_text()
        assert "new rule" in new_text
        assert "<!-- LINTGATE:BEGIN machine_rules v2 -->" in new_text

    @mock.patch("lintgate.context.auditor.audit_context_health")
    def test_apply_sets_status(self, mock_audit, tmp_path: Path):
        mock_audit.return_value = {"audit": []}
        (tmp_path / "CLAUDE.md").write_text(_make_managed_text("machine_rules"))
        patch = ContextPatch(
            section_id="machine_rules",
            new_content="\nupdated\n",
        )
        assert patch.status == "pending"
        apply_context_patch(str(tmp_path), patch, dry_run=False)
        assert patch.status == "applied"

    @mock.patch("lintgate.context.auditor.audit_context_health")
    def test_apply_includes_validation(self, mock_audit, tmp_path: Path):
        mock_audit.return_value = {
            "audit": [
                {"status": "pass"},
                {"status": "warn"},
            ]
        }
        (tmp_path / "CLAUDE.md").write_text(_make_managed_text("machine_rules"))
        patch = ContextPatch(
            section_id="machine_rules",
            new_content="\nupdated\n",
        )
        result = apply_context_patch(str(tmp_path), patch, dry_run=False)
        assert result["validation"] == {
            "files": 2,
            "errors": 0,
            "warnings": 1,
            "passes": 1,
        }

    @mock.patch(
        "lintgate.context.auditor.audit_context_health",
        side_effect=RuntimeError("boom"),
    )
    def test_apply_audit_failure_is_graceful(self, mock_audit, tmp_path: Path):
        (tmp_path / "CLAUDE.md").write_text(_make_managed_text("machine_rules"))
        patch = ContextPatch(
            section_id="machine_rules",
            new_content="\nupdated\n",
        )
        result = apply_context_patch(str(tmp_path), patch, dry_run=False)
        assert result["applied"] is True
        assert result["validation"] is None

    def test_apply_auto_migrates_unmarked_file(self, tmp_path: Path):
        # File with matching heading but no markers
        (tmp_path / "CLAUDE.md").write_text("# Machine-Enforceable Rules\n\nOld rule.\n")
        patch = ContextPatch(
            section_id="machine_rules",
            new_content="\nOld rule.\nnew rule\n",
        )
        result = apply_context_patch(str(tmp_path), patch, dry_run=True)
        assert result["applied"] is False
        assert result["diff_preview"]["migrated"] is True

    @mock.patch("lintgate.context.auditor.audit_context_health")
    def test_version_increments_on_apply(self, mock_audit, tmp_path: Path):
        mock_audit.return_value = {"audit": []}
        text = (
            "<!-- LINTGATE:BEGIN machine_rules v5 -->\n"
            "existing\n"
            "<!-- LINTGATE:END machine_rules -->"
        )
        (tmp_path / "CLAUDE.md").write_text(text)
        patch = ContextPatch(
            section_id="machine_rules",
            new_content="\nupdated\n",
        )
        result = apply_context_patch(str(tmp_path), patch, dry_run=False)
        assert result["diff_preview"]["old_version"] == 5
        assert result["diff_preview"]["new_version"] == 6
        new_text = (tmp_path / "CLAUDE.md").read_text()
        assert "<!-- LINTGATE:BEGIN machine_rules v6 -->" in new_text

    def test_not_migrated_flag_when_already_managed(self, tmp_path: Path):
        (tmp_path / "CLAUDE.md").write_text(_make_managed_text("machine_rules"))
        patch = ContextPatch(
            section_id="machine_rules",
            new_content="\nupdated\n",
        )
        result = apply_context_patch(str(tmp_path), patch, dry_run=True)
        assert result["diff_preview"]["migrated"] is False

    @mock.patch("lintgate.context.auditor.audit_context_health")
    def test_preamble_and_postamble_preserved(self, mock_audit, tmp_path: Path):
        mock_audit.return_value = {"audit": []}
        text = (
            "# Preamble\n\n"
            "<!-- LINTGATE:BEGIN machine_rules v1 -->\n"
            "old content\n"
            "<!-- LINTGATE:END machine_rules -->\n"
            "\n# Postamble\n"
        )
        (tmp_path / "CLAUDE.md").write_text(text)
        patch = ContextPatch(
            section_id="machine_rules",
            new_content="\nnew content\n",
        )
        apply_context_patch(str(tmp_path), patch, dry_run=False)
        new_text = (tmp_path / "CLAUDE.md").read_text()
        assert new_text.startswith("# Preamble\n")
        assert "# Postamble" in new_text
        assert "new content" in new_text
        assert "old content" not in new_text
