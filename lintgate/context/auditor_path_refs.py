"""Path reference validation for context files.

Extracted from context_auditor_checks.py — connected component #2.

Contains: check_path_references, extract_path_refs, find_dead_paths,
and all private helpers for generated-pattern detection and bare-name
resolution.
"""

from __future__ import annotations

import os
import re
from typing import Any

_BACKTICK_PATH_RE = re.compile(r"`([^`]+)`")

_PATH_EXTENSIONS = (".py", ".md", ".yaml", ".yml", ".toml", ".json")
_URL_PREFIXES = ("https://",)
_URL_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")

_SHELL_CMD_PREFIXES = (
    "uv ",
    "uv run ",
    "pip ",
    "python ",
    "python3 ",
    "npm ",
    "npx ",
    "cargo ",
    "go ",
    "git ",
    "docker ",
    "make ",
    "brew ",
    "curl ",
    "wget ",
    "grep ",
    "rg ",
    "find ",
    "cat ",
    "ls ",
    "cd ",
    "mkdir ",
    "rm ",
    "cp ",
    "mv ",
    "chmod ",
    "chown ",
    "sudo ",
    "apt ",
    "yum ",
)

_HF_MODEL_ID_RE = re.compile(r"^[A-Za-z0-9_-]+/[A-Za-z0-9._-]+$")


def check_path_references(
    checks: list[dict[str, Any]],
    suggestions: list[str],
    text: str,
    project_root: str,
    thresholds: dict[str, Any],
) -> None:
    path_refs = extract_path_refs(text)
    max_refs = int(thresholds.get("max_path_references", 50))

    if not path_refs:
        checks.append(
            {
                "check": "path_references",
                "status": "pass",
                "detail": "No path references found in backticks",
            }
        )
        return

    if len(path_refs) > max_refs:
        checks.append(
            {
                "check": "path_reference_volume",
                "status": "warn",
                "detail": f"{len(path_refs)} path references found (threshold: {max_refs}).",
            }
        )
        suggestions.append(
            "Reduce path-reference density by moving exhaustive file lists to "
            "separate docs and keeping this context file concise."
        )

    # Detect generated-artifact patterns from project structure + config
    generated_patterns = _detect_generated_patterns(project_root)
    extra_patterns = thresholds.get("generated_path_patterns", [])
    if isinstance(extra_patterns, list):
        generated_patterns.extend(str(p) for p in extra_patterns if p)

    dead_paths = find_dead_paths(path_refs, project_root, generated_patterns or None)

    if dead_paths:
        dead_str = ", ".join(dead_paths[:5])
        more = f" (+{len(dead_paths) - 5} more)" if len(dead_paths) > 5 else ""
        checks.append(
            {
                "check": "path_references",
                "status": "warn",
                "detail": f"{len(dead_paths)} referenced path(s) don't exist: {dead_str}{more}",
            }
        )
        for p in dead_paths[:3]:
            suggestions.append(f"Remove or update dead path reference: `{p}`")
    else:
        checks.append(
            {
                "check": "path_references",
                "status": "pass",
                "detail": f"All {len(path_refs)} path references verified",
            }
        )


def extract_path_refs(text: str) -> list[str]:
    """Extract likely file-path references from backtick-quoted text."""
    stripped = re.sub(r"```[\s\S]*?```", "", text)
    refs: list[str] = []
    for candidate in _BACKTICK_PATH_RE.findall(stripped):
        candidate = candidate.strip()
        is_path_like = "/" in candidate or candidate.endswith(_PATH_EXTENSIONS)
        if not is_path_like:
            continue
        if candidate.startswith(_URL_PREFIXES) or _URL_SCHEME_RE.match(candidate):
            continue
        if (
            "\n" in candidate
            or "\u251c" in candidate
            or "\u2514" in candidate
            or "\u2502" in candidate
        ):
            continue
        if " " in candidate and os.sep not in candidate:
            continue
        candidate_lower = candidate.lower()
        if any(candidate_lower.startswith(prefix) for prefix in _SHELL_CMD_PREFIXES):
            continue
        if (
            "/" in candidate
            and not any(candidate.endswith(ext) for ext in _PATH_EXTENSIONS)
            and candidate.count("/") == 1
            and _HF_MODEL_ID_RE.match(candidate)
        ):
            continue
        refs.append(candidate)
    return refs


def _detect_generated_patterns(project_root: str) -> list[str]:
    """Detect build-artifact path patterns from project structure.

    Scans for build tool markers and returns glob patterns for paths
    that are typically generated (not checked in) and may be referenced
    in documentation but not present on disk at audit time.
    """
    patterns: list[str] = []

    # Python packaging
    if os.path.exists(os.path.join(project_root, "setup.py")) or os.path.exists(
        os.path.join(project_root, "pyproject.toml")
    ):
        patterns.extend(["*.egg-info", "*.egg-info/*", "dist", "dist/*", "build", "build/*"])

    # Makefile-based builds
    if os.path.exists(os.path.join(project_root, "Makefile")):
        patterns.extend(["build", "build/*", "out", "out/*"])

    # Node.js / webpack / bundlers
    if os.path.exists(os.path.join(project_root, "package.json")):
        patterns.extend(["dist", "dist/*", "bundle", "bundle/*", "node_modules", "node_modules/*"])

    # Webpack specifically
    if os.path.exists(os.path.join(project_root, "webpack.config.js")):
        patterns.extend(["dist", "dist/*", "bundle", "bundle/*"])

    # Rust
    if os.path.exists(os.path.join(project_root, "Cargo.toml")):
        patterns.extend(["target", "target/*"])

    # Deduplicate
    return list(dict.fromkeys(patterns))


def _matches_generated_pattern(ref: str, patterns: list[str]) -> bool:
    """Check if a path reference matches any generated-artifact pattern."""
    import fnmatch

    ref_clean = ref.removeprefix("./")
    for pattern in patterns:
        if fnmatch.fnmatch(ref_clean, pattern):
            return True
        # Also check bare name against pattern
        parts = ref_clean.split("/")
        if parts and fnmatch.fnmatch(parts[0], pattern):
            return True
    return False


def find_dead_paths(
    path_refs: list[str],
    project_root: str,
    generated_patterns: list[str] | None = None,
) -> list[str]:
    """Check which path references don't exist on disk.

    Args:
        path_refs: Extracted path references from context files.
        project_root: Project root directory.
        generated_patterns: Optional glob patterns for build artifacts
            that may not exist on disk but are valid references.
    """
    dead: list[str] = []
    effective_patterns = generated_patterns or []

    for ref in path_refs:
        if "*" in ref or "?" in ref:
            continue
        if ref.startswith("~/") or ref.startswith("~\\"):
            expanded = os.path.expanduser(ref)
            if not os.path.exists(expanded):
                dead.append(ref)
            continue

        # Skip references matching generated-artifact patterns
        if effective_patterns and _matches_generated_pattern(ref, effective_patterns):
            continue

        ref_clean = ref.removeprefix("./")
        full_path = os.path.join(project_root, ref_clean)
        if os.path.exists(full_path):
            continue
        if (
            "/" not in ref_clean
            and "\\" not in ref_clean
            and _find_bare_name_in_project(ref_clean, project_root)
        ):
            continue
        dead.append(ref)
    return dead


def _find_bare_name_in_project(name: str, project_root: str) -> bool:
    """Check if a bare filename exists anywhere under common source dirs."""
    search_roots = [project_root]
    for subdir in ("src", "lib", "app", "pkg"):
        candidate = os.path.join(project_root, subdir)
        if os.path.isdir(candidate):
            search_roots.append(candidate)

    for root in search_roots:
        for dirpath, _dirnames, filenames in os.walk(root):
            depth = dirpath[len(root) :].count(os.sep)
            if depth > 3:
                continue
            if name in filenames:
                return True
    return False
