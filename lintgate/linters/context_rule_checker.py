"""Context-guidance rule enforcement (AGENTS.md / CLAUDE.md)."""

from __future__ import annotations

import functools
import os
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterator

from ..context_guidance import collect_context_rules, rule_applies_to_path
from ..types import LinterContext, LintIssue
from .base import BaseLinter

_VALID_SEVERITIES = {"blocking", "warning", "informational"}


class ContextRuleChecker(BaseLinter):
    """Enforce explicit/inferred style and anti-drift context rules."""

    name = "context_rule_checker"
    tier = 1
    timeout_ms = 2500
    required_tool = None

    def run(self, ctx: LinterContext) -> Iterator[LintIssue]:
        rules = collect_context_rules(ctx.project_root)
        if not rules:
            return

        for file_path in sorted(set(ctx.files)):
            rel_path = _safe_relpath(file_path, ctx.project_root)
            source_text = _read_text(file_path)
            if source_text is None:
                continue

            for rule in rules:
                if not rule_applies_to_path(rule, rel_path):
                    continue
                issue_kind = str(rule.get("kind", "forbid_regex"))
                if issue_kind == "forbid_regex":
                    yield from _run_forbid_rule(rule, file_path, rel_path, source_text)
                elif issue_kind == "require_regex":
                    issue = _run_require_rule(rule, file_path, rel_path, source_text)
                    if issue:
                        yield issue


def _run_forbid_rule(
    rule: dict[str, Any],
    file_path: str,
    rel_path: str,
    source_text: str,
) -> Iterator[LintIssue]:
    pattern = str(rule.get("pattern", "")).strip()
    if not pattern:
        return
    try:
        compiled = re.compile(pattern, re.MULTILINE)
    except re.error:
        return

    for match in compiled.finditer(source_text):
        line = _line_number(source_text, match.start())
        snippet = match.group(0).strip().splitlines()[0][:120]
        yield _build_issue(
            rule=rule,
            file_path=file_path,
            rel_path=rel_path,
            line=line,
            kind_suffix="forbid",
            default_message=f"Forbidden context pattern matched: {pattern}",
            evidence={
                "pattern": pattern,
                "matched_text": snippet,
                "rule_source": rule.get("source"),
            },
        )


def _run_require_rule(
    rule: dict[str, Any],
    file_path: str,
    rel_path: str,
    source_text: str,
) -> LintIssue | None:
    pattern = str(rule.get("pattern", "")).strip()
    if not pattern:
        return None
    try:
        compiled = re.compile(pattern, re.MULTILINE)
    except re.error:
        return None

    if compiled.search(source_text):
        return None

    return _build_issue(
        rule=rule,
        file_path=file_path,
        rel_path=rel_path,
        line=1,
        kind_suffix="require",
        default_message=f"Required context pattern missing: {pattern}",
        evidence={
            "pattern": pattern,
            "rule_source": rule.get("source"),
        },
    )


def _build_issue(
    rule: dict[str, Any],
    file_path: str,
    rel_path: str,
    line: int | None,
    kind_suffix: str,
    default_message: str,
    evidence: dict[str, Any],
) -> LintIssue:
    severity = str(rule.get("severity", "warning")).lower()
    if severity not in _VALID_SEVERITIES:
        severity = "warning"

    message = str(rule.get("message", "")).strip() or default_message
    suggestion = (
        "Review AGENTS.md/CLAUDE.md guidance and refactor this file to comply "
        "with project constraints."
    )
    return LintIssue(
        linter="context_rule_checker",
        kind=f"context-{kind_suffix}",
        message=message,
        file=file_path,
        line=line,
        severity=severity,
        confidence=1.0,
        evidence={
            "rel_path": rel_path,
            **evidence,
        },
        suggestions=[suggestion],
    )


@functools.lru_cache(maxsize=None)
def _line_number(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _safe_relpath(file_path: str, root: str) -> str:
    try:
        return os.path.relpath(file_path, root)
    except ValueError:
        return file_path


def _read_text(path: str) -> str | None:
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return None
