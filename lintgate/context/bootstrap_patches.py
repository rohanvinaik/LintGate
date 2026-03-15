"""Managed section parsing and context patch protocol.

Handles CLAUDE.md managed section markers (LINTGATE:BEGIN/END),
patch generation, and patch application with cumulative rebasing.

Extracted from context_bootstrap.py for module size compliance.
"""

from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_MANAGED_BEGIN_RE = re.compile(
    r"<!--\s*LINTGATE:BEGIN\s+(\w+)\s+v(\d+)\s*-->",
)
_MANAGED_END_RE = re.compile(
    r"<!--\s*LINTGATE:END\s+(\w+)\s*-->",
)

MANAGED_SECTION_IDS = (
    "machine_rules",
    "do_dont",
    "theory_alignment",
    "context_map",
    "prescriptive_rules",
)


@dataclass
class ManagedSection:
    """A parsed managed section from a CLAUDE.md file."""

    section_id: str
    version: int
    content: str
    start_pos: int  # char offset of BEGIN marker
    end_pos: int  # char offset after END marker


@dataclass
class ContextPatch:
    """A proposed patch to a managed section in CLAUDE.md."""

    patch_id: str = ""
    section_id: str = ""
    trigger: str = ""
    old_content: str = ""
    new_content: str = ""
    rationale: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)
    coherence_check: dict[str, Any] | None = None
    status: str = "pending"
    created_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "patch_id": self.patch_id,
            "section_id": self.section_id,
            "trigger": self.trigger,
            "old_content": self.old_content,
            "new_content": self.new_content,
            "rationale": self.rationale,
            "evidence": self.evidence,
            "coherence_check": self.coherence_check,
            "status": self.status,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ContextPatch:
        return cls(
            patch_id=data.get("patch_id", ""),
            section_id=data.get("section_id", ""),
            trigger=data.get("trigger", ""),
            old_content=data.get("old_content", ""),
            new_content=data.get("new_content", ""),
            rationale=data.get("rationale", ""),
            evidence=data.get("evidence", {}),
            coherence_check=data.get("coherence_check"),
            status=data.get("status", "pending"),
            created_at=data.get("created_at", 0.0),
        )


def parse_managed_sections(text: str) -> dict[str, ManagedSection]:
    """Parse LINTGATE:BEGIN/END markers from CLAUDE.md text."""
    sections: dict[str, ManagedSection] = {}

    for begin_match in _MANAGED_BEGIN_RE.finditer(text):
        section_id = begin_match.group(1)
        version = int(begin_match.group(2))
        begin_end = begin_match.end()

        end_pattern = re.compile(
            rf"<!--\s*LINTGATE:END\s+{re.escape(section_id)}\s*-->",
        )
        end_match = end_pattern.search(text, begin_end)
        if end_match is None:
            continue

        content = text[begin_end : end_match.start()]
        sections[section_id] = ManagedSection(
            section_id=section_id,
            version=version,
            content=content,
            start_pos=begin_match.start(),
            end_pos=end_match.end(),
        )

    return sections


def migrate_to_managed_sections(text: str) -> tuple[str, list[str]]:
    """Add managed section markers to a pre-upgrade CLAUDE.md.

    Returns (migrated_text, list_of_migrated_section_ids).
    If markers already exist, returns text unchanged.
    """
    if "LINTGATE:BEGIN" in text:
        return text, []

    migrated_ids: list[str] = []
    lines = text.split("\n")
    result_lines: list[str] = []
    i = 0

    heading_map: dict[str, str] = {
        "theory-aligned development": "theory_alignment",
        "do / do not": "do_dont",
        "do/do not": "do_dont",
        "machine-enforceable rules": "machine_rules",
        "machine rules": "machine_rules",
        "context map": "context_map",
    }

    open_section: str | None = None

    while i < len(lines):
        line = lines[i]
        stripped = line.strip().lower()

        if stripped.startswith("#"):
            heading_text = re.sub(r"^#+\s*", "", stripped).strip()
            heading_text = re.sub(r"\s*\(.*\)\s*$", "", heading_text).strip()

            matched_id = None
            for pattern, sid in heading_map.items():
                if pattern in heading_text:
                    matched_id = sid
                    break

            if matched_id:
                if open_section:
                    result_lines.append(f"<!-- LINTGATE:END {open_section} -->")

                result_lines.append(f"<!-- LINTGATE:BEGIN {matched_id} v1 -->")
                open_section = matched_id
                migrated_ids.append(matched_id)
                result_lines.append(line)
                i += 1
                continue

            if open_section:
                result_lines.append(f"<!-- LINTGATE:END {open_section} -->")
                open_section = None

        result_lines.append(line)
        i += 1

    if open_section:
        result_lines.append(f"<!-- LINTGATE:END {open_section} -->")

    return "\n".join(result_lines), migrated_ids


def summarize_audit(audit: dict[str, Any]) -> dict[str, int]:
    audit_items = audit.get("audit", [])
    out = {
        "files": len(audit_items),
        "errors": 0,
        "warnings": 0,
        "passes": 0,
    }
    for item in audit_items:
        status = item.get("status")
        if status == "error":
            out["errors"] += 1
        elif status == "warn":
            out["warnings"] += 1
        elif status == "pass":
            out["passes"] += 1
    return out


def _patch_constraint_accepted(
    sections: dict[str, ManagedSection],
    evidence: dict[str, Any],
) -> tuple[str, str] | None:
    """Handle constraint_accepted trigger. Returns (section_id, new_content) or None."""
    rule_text = evidence.get("rule", "")
    if not rule_text:
        return None
    section = sections.get("machine_rules")
    if section is None or rule_text in section.content:
        return None
    return "machine_rules", section.content.rstrip() + f"\n{rule_text}\n"


def _patch_do_dont(
    sections: dict[str, ManagedSection],
    evidence: dict[str, Any],
) -> tuple[str, str] | None:
    """Handle prediction_confirmed / recurring_behavioral_signal triggers."""
    entry = evidence.get("entry", "")
    if not entry:
        return None
    section = sections.get("do_dont")
    if section is None or entry in section.content:
        return None
    return "do_dont", section.content.rstrip() + f"\n- DO NOT: {entry}\n"


def _patch_theory_coherence(
    sections: dict[str, ManagedSection],
    evidence: dict[str, Any],
) -> tuple[str, str] | None:
    """Handle theory_coherence_update trigger."""
    update_text = evidence.get("update", "")
    if not update_text:
        return None
    section = sections.get("theory_alignment")
    if section is None or update_text in section.content:
        return None
    return "theory_alignment", section.content.rstrip() + f"\n- {update_text}\n"


def _patch_prescriptive_rules(
    sections: dict[str, ManagedSection],
    evidence: dict[str, Any],
) -> tuple[str, str] | None:
    """Handle prescriptive_spec_composed trigger — update prescriptive_rules section."""
    target_key = evidence.get("target_key", "")
    problem_class = evidence.get("problem_class", "pure")
    summary = evidence.get("summary", "")
    if not target_key:
        return None
    section = sections.get("prescriptive_rules")
    if section is None:
        # Bootstrap fresh section
        return "prescriptive_rules", (
            f"## Prescriptive Specifications\n\n- `{target_key}` ({problem_class}): {summary}\n"
        )
    if target_key in section.content:
        return None  # Already listed
    return (
        "prescriptive_rules",
        section.content.rstrip() + f"\n- `{target_key}` ({problem_class}): {summary}\n",
    )


_TRIGGER_HANDLERS: dict[str, Any] = {
    "constraint_accepted": _patch_constraint_accepted,
    "prediction_confirmed": _patch_do_dont,
    "recurring_behavioral_signal": _patch_do_dont,
    "theory_coherence_update": _patch_theory_coherence,
    "prescriptive_spec_composed": _patch_prescriptive_rules,
}


def generate_context_patch(
    project_root: str,
    trigger: str,
    evidence: dict[str, Any],
) -> ContextPatch | None:
    """Generate a patch for a managed section in CLAUDE.md.

    Args:
        project_root: Repository root.
        trigger: One of "constraint_accepted", "prediction_confirmed",
                 "recurring_behavioral_signal", "theory_coherence_update".
        evidence: Supporting data for the patch.

    Returns:
        ContextPatch or None if no update needed / already present.
    """
    claude_path = Path(project_root) / "CLAUDE.md"
    if not claude_path.exists():
        return None

    text = claude_path.read_text()
    if "LINTGATE:BEGIN" not in text:
        text, _ = migrate_to_managed_sections(text)

    sections = parse_managed_sections(text)

    handler = _TRIGGER_HANDLERS.get(trigger)
    if handler is None:
        return None

    result = handler(sections, evidence)
    if result is None:
        return None

    section_id, new_content = result
    section = sections[section_id]

    return ContextPatch(
        patch_id=uuid.uuid4().hex[:8],
        section_id=section_id,
        trigger=trigger,
        old_content=section.content,
        new_content=new_content,
        rationale=evidence.get("rationale", f"Auto-generated from {trigger}"),
        evidence=evidence,
        status="pending",
        created_at=time.time(),
    )


def apply_context_patch(
    project_root: str,
    patch: ContextPatch,
    *,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Apply a context patch to CLAUDE.md.

    Args:
        project_root: Repository root.
        patch: The ContextPatch to apply.
        dry_run: If True (default), return diff preview without writing.

    Returns:
        Dict with "applied", "diff_preview", and optionally "validation".
    """
    from .auditor import audit_context_health

    claude_path = Path(project_root) / "CLAUDE.md"
    if not claude_path.exists():
        return {"applied": False, "error": "CLAUDE.md not found"}

    text = claude_path.read_text()

    migrated = False
    if "LINTGATE:BEGIN" not in text:
        text, migrated_ids = migrate_to_managed_sections(text)
        migrated = bool(migrated_ids)

    sections = parse_managed_sections(text)
    section = sections.get(patch.section_id)
    if section is None:
        return {"applied": False, "error": f"Section '{patch.section_id}' not found"}

    new_version = section.version + 1
    new_begin = f"<!-- LINTGATE:BEGIN {patch.section_id} v{new_version} -->"

    before = text[: section.start_pos]
    end_marker = f"<!-- LINTGATE:END {patch.section_id} -->"
    after = text[section.end_pos :]

    new_text = before + new_begin + patch.new_content + end_marker + after

    diff_preview = {
        "section_id": patch.section_id,
        "old_version": section.version,
        "new_version": new_version,
        "old_content": patch.old_content.strip(),
        "new_content": patch.new_content.strip(),
        "migrated": migrated,
    }

    if dry_run:
        return {"applied": False, "dry_run": True, "diff_preview": diff_preview}

    claude_path.write_text(new_text)
    patch.status = "applied"

    validation = None
    try:
        audit_result = audit_context_health(project_root)
        validation = summarize_audit(audit_result)
    except Exception:
        pass

    return {
        "applied": True,
        "diff_preview": diff_preview,
        "validation": validation,
    }
