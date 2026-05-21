"""Shared helpers for dynamic rule file rendering.

Dynamic rule files (``lg_session.md``, ``lg_focus.md``) are session-scoped
files written to host-specific rule directories. They carry runtime state
that survives context compaction.

Each host renderer calls these helpers to produce content, then wraps it
in host-specific formatting (frontmatter, file extension, etc.).

Token budgets are hard-capped:
- session: MAX 1200 tokens (~4800 chars)
- focus: MAX 250 tokens (~1000 chars)
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lintgate.runtime_state import RuntimeState

# ── Token budgets ────────────────────────────────────────────────────

SESSION_TOKEN_BUDGET = 1200  # ~4800 chars
FOCUS_TOKEN_BUDGET = 250  # ~1000 chars
_CHARS_PER_TOKEN = 4

# ── Dynamic file prefix (distinguishes from user-created files) ──────

DYNAMIC_PREFIX = "lg_"
SESSION_FILENAME = "lg_session"
FOCUS_FILENAME = "lg_focus"


# ── Content rendering ────────────────────────────────────────────────


def render_session_content(runtime: RuntimeState) -> str:
    """Render session state content for a dynamic rule file.

    Returns plain markdown content (no frontmatter — callers add that).
    Hard-capped at SESSION_TOKEN_BUDGET tokens.
    """
    lines: list[str] = []

    # Generation watermark
    lines.append(f"<!-- LG_GEN:{runtime.generation} LG_TS:{int(runtime.timestamp)} -->")
    lines.append("# Session State")
    lines.append("")

    # Mode
    mode_line = f"Mode: {runtime.mode}"
    if runtime.mode == "habit" and runtime.habit_score > 0:
        mode_line += f" (score: {runtime.habit_score:.2f})"
    lines.append(mode_line)

    # Workflow mode (orthogonal to economy mode)
    if runtime.workflow_mode:
        lines.append(f"Workflow: {runtime.workflow_mode}")

    # Active files
    if runtime.active_files:
        basenames = [f.rsplit("/", 1)[-1] for f in runtime.active_files[:5]]
        lines.append(f"Focus: [{', '.join(basenames)}]")

    # Status line
    parts = []
    parts.append(f"Coherence: {runtime.coherence_state}")
    if runtime.last_test_status:
        parts.append(f"Tests: {runtime.last_test_status}")
    if runtime.blocking_issues > 0:
        parts.append(f"Blocking: {runtime.blocking_issues}")
    if runtime.warning_issues > 0:
        parts.append(f"Warnings: {runtime.warning_issues}")
    lines.append(" | ".join(parts))
    lines.append("")

    # Compass capsule
    if runtime.true_north:
        lines.append("## Direction")
        lines.append(f"True North: {runtime.true_north}")
    if runtime.toward:
        lines.append("DO: " + "; ".join(runtime.toward[:6]))
    if runtime.away:
        lines.append("AVOID: " + "; ".join(runtime.away[:6]))
    if runtime.forbidden:
        lines.append("FORBIDDEN: " + "; ".join(runtime.forbidden[:6]))
    if runtime.true_north or runtime.toward or runtime.away:
        lines.append("")

    # Behavioral trajectory
    behavioral_parts = []
    if runtime.approach_failures > 0:
        behavioral_parts.append(f"Failed approaches: {runtime.approach_failures}")
    if runtime.prediction_accuracy >= 0:
        behavioral_parts.append(f"Prediction accuracy: {runtime.prediction_accuracy:.0%}")
    if runtime.top_constraint:
        behavioral_parts.append(f"Top constraint: {runtime.top_constraint}")
    if behavioral_parts:
        lines.append("## Behavioral")
        lines.extend(behavioral_parts)
        lines.append("")

    # Token economics
    if runtime.estimated_tokens_pct > 0:
        lines.append(
            f"Context: {runtime.estimated_tokens_pct:.0f}% used"
            f" | Tools: {runtime.tool_calls_total}"
            f" | Compactions: {runtime.compaction_count}"
        )

    return _truncate_to_budget(lines, SESSION_TOKEN_BUDGET)


def render_focus_content(runtime: RuntimeState) -> str:
    """Render compact focus state content for a dynamic rule file.

    Returns plain markdown content (no frontmatter — callers add that).
    Hard-capped at FOCUS_TOKEN_BUDGET tokens.
    """
    lines: list[str] = []

    # Generation watermark
    lines.append(f"<!-- LG_GEN:{runtime.generation} LG_TS:{int(runtime.timestamp)} -->")

    # Focus intent
    if runtime.focus_intent:
        lines.append(f"Focus: {runtime.focus_intent}")

    # Active files (basenames only)
    if runtime.active_files:
        basenames = [f.rsplit("/", 1)[-1] for f in runtime.active_files[:3]]
        lines.append(f"Files: {', '.join(basenames)}")

    # Status
    parts = []
    if runtime.blocking_issues > 0:
        parts.append(f"Blocking: {runtime.blocking_issues}")
    if runtime.last_test_status:
        parts.append(f"Tests: {runtime.last_test_status}")
    parts.append(f"Mode: {runtime.mode}")
    lines.append(" | ".join(parts))

    return _truncate_to_budget(lines, FOCUS_TOKEN_BUDGET)


# ── File operations ──────────────────────────────────────────────────


def write_dynamic_file(project_root: str, relative_path: str, content: str) -> bool:
    """Write a dynamic rule file. Returns True on success."""
    full_path = Path(project_root) / relative_path
    try:
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(content, encoding="utf-8")
        return True
    except OSError:
        return False


def delete_dynamic_file(project_root: str, relative_path: str) -> bool:
    """Delete a dynamic rule file. Returns True if deleted."""
    full_path = Path(project_root) / relative_path
    try:
        full_path.unlink()
        return True
    except OSError:
        return False


def read_generation_from_file(project_root: str, relative_path: str) -> int | None:
    """Read the LG_GEN watermark from a dynamic rule file without full parse.

    Returns the generation number or None if the file is missing/unparseable.
    """
    full_path = Path(project_root) / relative_path
    try:
        # Only read first 200 chars — watermark is always on line 1
        with open(full_path, encoding="utf-8") as f:
            head = f.read(200)
        marker = "LG_GEN:"
        idx = head.find(marker)
        if idx < 0:
            return None
        start = idx + len(marker)
        end = head.find(" ", start)
        if end < 0:
            end = head.find(">", start)
        if end < 0:
            return None
        return int(head[start:end])
    except (OSError, ValueError):
        return None


# ── Internal helpers ─────────────────────────────────────────────────


def _truncate_to_budget(lines: list[str], token_budget: int) -> str:
    """Truncate line list to approximate token budget."""
    char_budget = token_budget * _CHARS_PER_TOKEN
    result: list[str] = []
    total = 0
    for line in lines:
        total += len(line) + 1  # +1 for newline
        if total > char_budget:
            break
        result.append(line)
    return "\n".join(result)
