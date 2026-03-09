"""Helper functions for context bootstrap: metadata, commands, quick wins, rules.

Extracted from context_bootstrap.py for module size compliance.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .bootstrap_defaults import ZERO_STATE_ANTI_PATTERNS
from .context_bootstrap_render import normalize_sentence

try:
    import tomllib  # type: ignore[import-not-found]
except ModuleNotFoundError:  # pragma: no cover - Python <3.11
    import tomli as tomllib  # type: ignore[import-not-found]

_NEGATIVE_CUE_RE = re.compile(
    r"\b(?:do not|don't|never|avoid|anti-pattern|pitfall|risk|wrong|"
    r"fail|ruin|break|undermine|bypass|must not)\b",
    re.IGNORECASE,
)
_NO_THEORY = "(no theory content found)"
_PERF_ANTI_PATTERN_CUE = "O(n\u00b2)"


def _collect_machine_rule_lines(
    guidance: dict[str, Any],
    theory: dict[str, Any],
    max_machine_rules: int,
) -> list[str]:
    lines: list[str] = []
    seen: set[str] = set()

    for rule in guidance.get("rules", []):
        line = _rule_to_line(rule)
        if not line:
            continue
        if line in seen:
            continue
        seen.add(line)
        lines.append(line)

    proposed = theory.get("enforceable_rules", {}).get("proposed_rules", [])
    for item in proposed:
        line = str(item.get("add_line", "")).strip()
        if not line or line in seen:
            continue
        seen.add(line)
        lines.append(line)

    return lines[:max_machine_rules]


def _rule_to_line(rule: dict[str, Any]) -> str:
    kind = str(rule.get("kind", "")).strip()
    pattern = str(rule.get("pattern", "")).strip()
    if not pattern:
        return ""
    if kind == "forbid_regex":
        return f"LINTGATE_FORBID_REGEX: {pattern}"
    if kind == "require_regex":
        return f"LINTGATE_REQUIRE_REGEX: {pattern}"
    return ""


def _project_metadata(root: Path) -> dict[str, str]:
    name = root.name
    description = ""

    pyproject = root / "pyproject.toml"
    if pyproject.exists():
        try:
            parsed = tomllib.loads(pyproject.read_text())
            project = parsed.get("project", {})
            if isinstance(project, dict):
                maybe_name = project.get("name")
                maybe_desc = project.get("description")
                if isinstance(maybe_name, str) and maybe_name.strip():
                    name = maybe_name.strip()
                if isinstance(maybe_desc, str) and maybe_desc.strip():
                    description = maybe_desc.strip()
        except Exception:
            pass

    if not description:
        description = _read_readme_description(root)

    return {
        "name": name,
        "description": description,
    }


def _read_readme_description(root: Path) -> str:
    for candidate in ("README.md", "README.MD", "readme.md"):
        path = root / candidate
        if not path.exists():
            continue
        try:
            lines = path.read_text().splitlines()
        except OSError:
            return ""
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("#"):
                continue
            if stripped.startswith("[") and "http" in stripped:
                continue
            return normalize_sentence(stripped)
    return ""


def _select_actionable_anti_patterns(claims: list[str], max_items: int = 5) -> list[str]:
    selected: list[str] = []
    seen: set[str] = set()

    for claim in claims:
        text = normalize_sentence(str(claim))
        key = text.lower()
        if not text or key in seen:
            continue
        if not _NEGATIVE_CUE_RE.search(text):
            continue
        if len(text) > 260:
            text = text[:257].rstrip() + "..."
        seen.add(key)
        selected.append(text)
        if len(selected) >= max_items:
            break

    if selected:
        return selected

    if max_items <= 0:
        return []

    defaults = ZERO_STATE_ANTI_PATTERNS[:max_items]
    if max_items >= 4:
        perf_item = next(
            (item for item in ZERO_STATE_ANTI_PATTERNS if _PERF_ANTI_PATTERN_CUE in item),
            None,
        )
        if perf_item and perf_item not in defaults[:4]:
            if perf_item in defaults:
                defaults.remove(perf_item)
            defaults.insert(min(3, len(defaults)), perf_item)
            defaults = defaults[:max_items]

    return defaults


def _recommended_commands(root: Path) -> list[str]:
    commands: list[str] = []

    has_python = (root / "pyproject.toml").exists() or any(root.glob("*.py"))
    has_node = (root / "package.json").exists()
    has_rust = (root / "Cargo.toml").exists()

    if has_python:
        prefix = "uv run " if (root / "uv.lock").exists() else ""
        commands.append(f"{prefix}ruff check .")
        commands.append(f"{prefix}pytest -q")
        commands.append(f"{prefix}mypy .")

    if has_node:
        commands.append("npm run lint")
        commands.append("npm test")

    if has_rust:
        commands.append("cargo fmt --check")
        commands.append("cargo clippy --all-targets -- -D warnings")
        commands.append("cargo test")

    if not commands:
        commands.append("Run the project's lint and test commands before concluding work.")

    deduped: list[str] = []
    seen: set[str] = set()
    for cmd in commands:
        if cmd in seen:
            continue
        seen.add(cmd)
        deduped.append(cmd)
    return deduped


def _build_quick_wins(
    root: Path,
    guidance: dict[str, Any],
    theory: dict[str, Any],
) -> list[str]:
    """Generate actionable quick-win suggestions for the project."""
    wins: list[str] = []

    has_config = (root / "lintgate.yaml").exists() or (root / ".claude" / "lintgate.yaml").exists()
    if not has_config:
        wins.append(
            "Create `.claude/lintgate.yaml` with `controlplane: {enabled: true}` "
            "to activate the full supervision mesh (lint + tests + deps + git + behavior + structure)."
        )

    rules = guidance.get("rules", [])
    do_not_count = len(guidance.get("directives", {}).get("do_not", []))
    if do_not_count > 0 and not rules:
        wins.append(
            f"Found {do_not_count} DO NOT directive(s) but no LINTGATE_FORBID_REGEX rules. "
            "Run `extract_theory_constraints` to generate enforceable rules."
        )

    proposed = theory.get("enforceable_rules", {}).get("proposed_rules", [])
    if proposed:
        wins.append(
            f"{len(proposed)} rule(s) proposed from theory extraction — review and "
            "add to CLAUDE.md to make them enforceable."
        )

    if (root / "pyproject.toml").exists():
        has_lock = (root / "uv.lock").exists() or (root / "poetry.lock").exists()
        if not has_lock:
            wins.append(
                "No lockfile found. Run `uv lock` or `poetry lock` for reproducible installs."
            )

    return wins
