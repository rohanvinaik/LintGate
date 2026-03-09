"""Guidance extraction from LLM context files (AGENTS.md / CLAUDE.md)."""

from __future__ import annotations

import fnmatch
import os
import re
from pathlib import Path
from typing import Any

_CONTEXT_FILENAMES = ("AGENTS.md", "CLAUDE.md")
_CONTEXT_DIRS = ("", ".claude")
_BULLET_PREFIX_RE = re.compile(r"^\s*[-*]\s+")
_BACKTICK_RE = re.compile(r"`([^`]+)`")
_RULE_PREFIX = "LINTGATE_RULE:"
_FORBID_PREFIX = "LINTGATE_FORBID_REGEX:"
_REQUIRE_PREFIX = "LINTGATE_REQUIRE_REGEX:"


def build_context_guidance(
    project_root: str,
    files: list[str] | None = None,
) -> dict[str, Any]:
    """Build a structured summary of context guidance for a project."""
    context_files = discover_context_files(project_root)
    parsed = [_parse_context_file(path) for path in context_files]

    directives = {
        "critical": _dedupe_text(_flatten(parsed, "critical")),
        "must": _dedupe_text(_flatten(parsed, "must")),
        "do": _dedupe_text(_flatten(parsed, "do")),
        "do_not": _dedupe_text(_flatten(parsed, "do_not")),
    }
    path_hints = sorted({hint for item in parsed for hint in item.get("path_hints", [])})
    rules = collect_context_rules(project_root)

    resolved_files = _resolve_files(files or [], project_root)
    relevant = {}
    if resolved_files:
        relevant = {
            path: relevant_guidance_for_file(path, project_root, directives, path_hints)
            for path in resolved_files
        }

    return {
        "project": project_root,
        "context_files": [
            {
                "path": item["path"],
                "name": os.path.basename(item["path"]),
                "modified_ts": item["modified_ts"],
            }
            for item in parsed
        ],
        "directives": directives,
        "path_hints": path_hints,
        "rules": rules,
        "relevant_for_files": relevant,
    }


def summarize_context_guidance(guidance: dict[str, Any]) -> dict[str, Any]:
    """Return a compact context summary suitable for status payloads."""
    directives = guidance.get("directives", {})
    return {
        "context_file_count": len(guidance.get("context_files", [])),
        "context_files": [f.get("name") for f in guidance.get("context_files", [])],
        "directive_counts": {
            "critical": len(directives.get("critical", [])),
            "must": len(directives.get("must", [])),
            "do": len(directives.get("do", [])),
            "do_not": len(directives.get("do_not", [])),
        },
        "rule_count": len(guidance.get("rules", [])),
    }


def discover_context_files(project_root: str) -> list[str]:
    """Find AGENTS.md / CLAUDE.md files in the project context roots."""
    root = Path(project_root)
    found: list[str] = []
    for rel_dir in _CONTEXT_DIRS:
        base = root / rel_dir if rel_dir else root
        for filename in _CONTEXT_FILENAMES:
            candidate = base / filename
            if candidate.exists() and candidate.is_file():
                found.append(str(candidate))
    return found


def collect_context_rules(project_root: str) -> list[dict[str, Any]]:
    """Collect machine-usable context rules from context files."""
    context_files = discover_context_files(project_root)
    parsed = [_parse_context_file(path) for path in context_files]
    rules = [rule for item in parsed for rule in item.get("rules", [])]
    inferred = _infer_rules_from_directives(parsed)
    return rules + inferred


def relevant_guidance_for_file(
    file_path: str,
    project_root: str,
    directives: dict[str, list[str]],
    path_hints: list[str],
) -> list[str]:
    """Select guidance likely relevant to a specific file path."""
    rel = _safe_relpath(file_path, project_root)
    matched_hints = [hint for hint in path_hints if _path_hint_matches(rel, hint)]
    relevant: list[str] = []

    # Always include critical/must/do_not guidance.
    relevant.extend(directives.get("critical", []))
    relevant.extend(directives.get("must", []))
    relevant.extend(directives.get("do_not", []))

    # Include "do" directives if path hints appear in the directive text.
    for directive in directives.get("do", []):
        hint_tokens = _extract_path_hints(directive)
        if not hint_tokens:
            continue
        if any(_path_hint_matches(rel, hint) for hint in hint_tokens):
            relevant.append(directive)

    # Include any directive lines explicitly referencing matched hints.
    if matched_hints:
        for category in ("critical", "must", "do", "do_not"):
            for directive in directives.get(category, []):
                if any(hint in directive for hint in matched_hints):
                    relevant.append(directive)

    return _dedupe_text(relevant)


def _classify_directive(
    cleaned: str,
    directives: dict[str, list[str]],
) -> None:
    """Classify a cleaned line into directive categories."""
    upper = cleaned.upper()
    if "CRITICAL" in upper:
        directives["critical"].append(cleaned)
    if "DO NOT" in upper:
        directives["do_not"].append(cleaned)
    elif upper.startswith("DO ") or upper.startswith("DO:"):
        directives["do"].append(cleaned)
    if "MUST" in upper:
        directives["must"].append(cleaned)


def _is_skippable_line(stripped: str, raw: str, in_code_block: bool) -> bool | str:
    """Check if a line should be skipped. Returns 'toggle' for fence boundaries."""
    if not stripped:
        return True
    if stripped.startswith("```"):
        return "toggle"
    if in_code_block:
        return True
    return bool(re.match(r"^\s*\|.*\|", raw))


def _parse_context_file(path: str) -> dict[str, Any]:
    """Parse a single context file."""
    try:
        text = Path(path).read_text()
    except OSError:
        text = ""

    lines = text.splitlines()
    directives: dict[str, list[str]] = {
        "critical": [],
        "must": [],
        "do": [],
        "do_not": [],
    }
    rules: list[dict[str, Any]] = []
    path_hints: set[str] = set()

    in_code_block = False
    for line_no, raw in enumerate(lines, 1):
        stripped = raw.strip()
        skip = _is_skippable_line(stripped, raw, in_code_block)
        if skip == "toggle":
            in_code_block = not in_code_block
            continue
        if skip:
            continue

        cleaned = _clean_line(stripped)
        path_hints.update(_extract_path_hints(cleaned))
        rule = _parse_rule_line(cleaned, source_path=path, line_no=line_no)
        if rule:
            rules.append(rule)
        _classify_directive(cleaned, directives)

    modified_ts = Path(path).stat().st_mtime if os.path.exists(path) else None
    return {
        "path": path,
        "modified_ts": modified_ts,
        "directives": directives,
        "rules": rules,
        "path_hints": sorted(path_hints),
    }


def _flatten(items: list[dict[str, Any]], key: str) -> list[str]:
    out: list[str] = []
    for item in items:
        directives = item.get("directives", {})
        out.extend(directives.get(key, []))
    return out


def _clean_line(line: str) -> str:
    """Remove markdown heading markers, bullets, and emphasis wrappers."""
    # Strip leading markdown heading markers (# / ## / ###)
    line = re.sub(r"^#+\s*", "", line).strip()
    line = _BULLET_PREFIX_RE.sub("", line).strip()
    line = line.strip("*").strip()
    return line


def _extract_path_hints(text: str) -> list[str]:
    """Extract path-like snippets from text/backticks."""
    hints: set[str] = set()
    candidates = _BACKTICK_RE.findall(text)
    candidates.append(text)
    for candidate in candidates:
        for token in re.split(r"\s+", candidate):
            token = token.strip(" ,:()[]\"'")
            if not token:
                continue
            if "/" in token or token.endswith((".py", ".md", ".yaml", ".yml", ".toml")):
                hints.add(token.lstrip("./"))
    return sorted(hints)


def _parse_rule_line(
    cleaned: str,
    source_path: str,
    line_no: int,
) -> dict[str, Any] | None:
    """Parse explicit machine-usable rule lines from context files."""
    source = f"{source_path}:{line_no}"

    if cleaned.startswith(_FORBID_PREFIX):
        pattern = cleaned[len(_FORBID_PREFIX) :].strip()
        if pattern:
            return {
                "kind": "forbid_regex",
                "pattern": pattern,
                "severity": "blocking",
                "message": "Matched forbidden context pattern",
                "source": source,
            }

    if cleaned.startswith(_REQUIRE_PREFIX):
        pattern = cleaned[len(_REQUIRE_PREFIX) :].strip()
        if pattern:
            return {
                "kind": "require_regex",
                "pattern": pattern,
                "severity": "warning",
                "message": "Missing required context pattern",
                "source": source,
            }

    if not cleaned.startswith(_RULE_PREFIX):
        return None

    body = cleaned[len(_RULE_PREFIX) :].strip()
    parts = [p.strip() for p in body.split(";") if p.strip()]
    attrs: dict[str, str] = {}
    for part in parts:
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        attrs[key.strip().lower()] = value.strip()

    rule_pattern: str | None = attrs.get("forbid_regex") or attrs.get("require_regex")
    if not rule_pattern:
        return None
    kind = "forbid_regex" if "forbid_regex" in attrs else "require_regex"
    return {
        "kind": kind,
        "pattern": rule_pattern,
        "severity": attrs.get("severity", "warning"),
        "message": attrs.get(
            "message",
            "Context rule violation"
            if kind == "forbid_regex"
            else "Missing required context pattern",
        ),
        "path_glob": attrs.get("path"),
        "source": source,
    }


def _infer_rules_from_directives(parsed: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Infer practical anti-drift rules from natural-language directives."""
    combined_do_not = " ".join(
        line for item in parsed for line in item.get("directives", {}).get("do_not", [])
    ).lower()

    inferred: list[dict[str, Any]] = []
    if "solve_task_" in combined_do_not:
        inferred.append(
            {
                "kind": "forbid_regex",
                "pattern": r"def\s+solve_task_[A-Za-z0-9_]*\s*\(",
                "severity": "blocking",
                "message": (
                    "Task-specific solver function detected (solve_task_*). "
                    "Context guidance requires compositional primitive-based programs."
                ),
                "source": "inferred:do_not_solve_task_prefix",
            }
        )

    return inferred


def rule_applies_to_path(rule: dict[str, Any], rel_path: str) -> bool:
    """Check whether a rule applies to a given relative path."""
    path_glob = rule.get("path_glob")
    if not path_glob:
        return True
    return fnmatch.fnmatch(rel_path, path_glob)


def _resolve_files(files: list[str], project_root: str) -> list[str]:
    resolved = []
    for path in files:
        if os.path.isabs(path):
            resolved.append(path)
        else:
            resolved.append(os.path.normpath(os.path.join(project_root, path)))
    return resolved


def _safe_relpath(path: str, root: str) -> str:
    try:
        return os.path.relpath(path, root)
    except ValueError:
        return path


def _path_hint_matches(rel_path: str, hint: str) -> bool:
    normalized_hint = hint.lstrip("./")
    if normalized_hint.endswith("/"):
        return rel_path.startswith(normalized_hint)
    return rel_path == normalized_hint or rel_path.startswith(f"{normalized_hint}/")


def _dedupe_text(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        key = value.strip()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out
