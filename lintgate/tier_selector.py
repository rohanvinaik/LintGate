"""Phase 2: Select which linters to run based on change classification.

This is the core "smart triggering" logic. Maps a ChangeClassification
to a LintTier (set of linters + files to lint).

Default: AGGRESSIVE (Tier 2 for all code changes, per user preference).

Debounce: Since each PostToolUse hook invocation is a fresh process,
debounce state is persisted to a small file in ~/.claude/lintgate/.
This prevents re-running the full suite on the same file within seconds
when the agent makes rapid sequential edits.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from .types import SKIP_TIER, ChangeClassification, LintTier, ProjectConfig

# ─── Tier definitions ────────────────────────────────────────────────────

TIER_0_LINTERS = ["ruff_check"]
TIER_1_LINTERS = [
    "ruff_check",
    "ruff_format",
    "import_checker",
    "version_checker",
    "context_rule_checker",
    "redefinition_checker",
]
TIER_2_LINTERS = [
    "ruff_check",
    "ruff_format",
    "mypy",
    "ty",
    "bandit_fast",
    "pip_audit",
    "secret_checker",
    "complexity_checker",
    "structure_checker",
    "version_checker",
    "context_rule_checker",
    "redefinition_checker",
    "performance_checker",
]
TIER_3_LINTERS = [
    "ruff_check",
    "ruff_format",
    "mypy",
    "ty",
    "import_checker",
    "complexity_checker",
    "bandit",
    "bandit_fast",
    "pip_audit",
    "secret_checker",
    "structure_checker",
    "architecture_checker",
    "dead_code_checker",
    "version_checker",
    "context_rule_checker",
    "redefinition_checker",
    "performance_checker",
]

# ─── Debounce state (file-backed, persists across process invocations) ──

_DEBOUNCE_FILE = Path.home() / ".claude" / "lintgate" / "debounce.json"
_DEBOUNCE_MAX_ENTRIES = 200  # Cap to prevent unbounded growth
_PROJECT_WIDE_CHANGE_KINDS = frozenset({"config", "dependency", "build"})
_PROJECT_SCAN_FILE_LIMIT = 200


def select_tier(
    classification: ChangeClassification,
    config: ProjectConfig,
) -> LintTier:
    """Select which linters to run based on change classification.

    Uses aggressive defaults: Tier 2 for all code changes.

    Args:
        classification: What changed (from change_classifier.py)
        config: Project config (from .claude/lintgate.yaml)

    Returns:
        LintTier describing which linters to run on which files.
        SKIP_TIER if linting should be skipped entirely.
    """

    # No files changed (read-only bash, failed command)
    if classification.risk_level == "none":
        return SKIP_TIER

    # Docs only: skip entirely
    if classification.change_kind == "docs":
        return SKIP_TIER

    # Cosmetic (formatting-only): skip — it's just whitespace
    if classification.risk_level == "cosmetic":
        return SKIP_TIER

    python_files = classification.files_by_language.get("python", [])

    # Config/dependency/build changes often don't touch Python files directly.
    # For these, run a bounded project scan so tier-1 checks still execute.
    if not python_files and classification.change_kind in _PROJECT_WIDE_CHANGE_KINDS:
        python_files = _collect_project_python_files(config.project_root)

    # No Python files? For now, skip. Future: language-specific linters.
    if not python_files:
        return SKIP_TIER

    # Check debouncing for tier 2+ (don't re-lint same files within interval)
    tier_key = _estimate_tier_key(classification)
    debounce_interval = config.debounce.get(tier_key, 0.0)
    if debounce_interval > 0 and _should_debounce(python_files, debounce_interval):
        # Debounced: fall back to tier 0 (fast, always useful)
        return LintTier(
            name="tier_0_debounced",
            linters=TIER_0_LINTERS,
            files=python_files,
            reason=f"Debounced (same files within {debounce_interval}s)",
        )

    # Record lint time for future debounce checks (persisted to disk)
    _record_lint_times(python_files)

    # ─── Tier selection logic (aggressive defaults) ───────────────────

    # Config only: tier 1
    if classification.change_kind == "config":
        return LintTier(
            name="tier_1_config",
            linters=TIER_1_LINTERS,
            files=python_files,
            reason="Config file changed",
        )

    # Import only: tier 1
    if classification.import_only:
        return LintTier(
            name="tier_1_import",
            linters=TIER_1_LINTERS,
            files=python_files,
            reason="Import-only change",
        )

    # Dependency changes: tier 1
    if classification.change_kind == "dependency":
        return LintTier(
            name="tier_1_dependency",
            linters=TIER_1_LINTERS,
            files=python_files,
            reason="Dependency file changed",
        )

    # Build/install command changes: tier 1
    if classification.change_kind == "build":
        return LintTier(
            name="tier_1_build",
            linters=TIER_1_LINTERS,
            files=python_files,
            reason="Build/install command changed project environment",
        )

    # Architectural changes (pipeline-critical paths or high-risk structural): tier 3
    # Any non-trivial change to a pipeline-critical file gets the full suite.
    if classification.risk_level == "architectural" or (
        classification.touches_pipeline_critical
        and classification.change_kind not in ("docs", "config")
    ):
        linters = TIER_3_LINTERS + config.extra_tier3_linters
        return LintTier(
            name="tier_3_architectural",
            linters=linters,
            files=python_files,
            reason="Architectural change to critical path",
            strictness="strict",
        )

    # Test files only: tier 2 but relaxed
    if classification.touches_test_files and not classification.touches_pipeline_critical:
        return LintTier(
            name="tier_2_test",
            linters=TIER_2_LINTERS,
            files=python_files,
            reason="Test file change",
            strictness="relaxed",
        )

    # Structural changes: tier 2
    if classification.risk_level == "structural":
        return LintTier(
            name="tier_2_structural",
            linters=TIER_2_LINTERS,
            files=python_files,
            reason="Structural change (function/class modified)",
        )

    # Logic changes: tier 2 (aggressive default)
    if classification.change_kind == "logic":
        return LintTier(
            name="tier_2_logic",
            linters=TIER_2_LINTERS,
            files=python_files,
            reason="Logic change",
        )

    # Default fallback: tier 2 (aggressive)
    return LintTier(
        name="tier_2_default",
        linters=TIER_2_LINTERS,
        files=python_files,
        reason="Default (aggressive)",
    )


# ─── Debounce helpers ────────────────────────────────────────────────────


def _estimate_tier_key(classification: ChangeClassification) -> str:
    """Estimate which tier debounce interval to use."""
    if classification.risk_level == "architectural":
        return "tier_3"
    if classification.change_kind in ("import", "config", "dependency", "build"):
        return "tier_1"
    return "tier_2"


def _collect_project_python_files(
    project_root: str,
    limit: int = _PROJECT_SCAN_FILE_LIMIT,
) -> list[str]:
    """Collect a bounded set of Python files from project root."""
    from .discovery import discover_project_files

    return discover_project_files(project_root, limit=limit)


def _load_debounce_state() -> dict[str, float]:
    """Load debounce state from disk. Fast — ~0.5ms for typical file."""
    try:
        if _DEBOUNCE_FILE.exists():
            with open(_DEBOUNCE_FILE) as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
    except (json.JSONDecodeError, OSError):
        pass
    return {}


def _save_debounce_state(state: dict[str, float]) -> None:
    """Save debounce state to disk. Prunes stale entries."""
    try:
        _DEBOUNCE_FILE.parent.mkdir(parents=True, exist_ok=True)

        # Prune entries older than 60 seconds (no debounce interval is that long)
        now = time.time()
        pruned = {k: v for k, v in state.items() if now - v < 60}

        # Cap total entries to prevent unbounded growth
        if len(pruned) > _DEBOUNCE_MAX_ENTRIES:
            # Keep the most recent entries
            sorted_items = sorted(pruned.items(), key=lambda x: x[1], reverse=True)
            pruned = dict(sorted_items[:_DEBOUNCE_MAX_ENTRIES])

        with open(_DEBOUNCE_FILE, "w") as f:
            json.dump(pruned, f)
    except OSError:
        pass  # Non-fatal — debounce is a performance optimization, not correctness


def _should_debounce(files: list[str], interval_s: float) -> bool:
    """Check if any file was linted too recently (persisted across processes)."""
    state = _load_debounce_state()
    now = time.time()
    for f in files:
        last = state.get(f, 0)
        if now - last < interval_s:
            return True
    return False


def _record_lint_times(files: list[str]) -> None:
    """Record lint timestamps for debounce (persisted across processes)."""
    state = _load_debounce_state()
    now = time.time()
    for f in files:
        state[f] = now
    _save_debounce_state(state)
