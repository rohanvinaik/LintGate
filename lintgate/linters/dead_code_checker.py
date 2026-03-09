"""Dead code detection — vulture integration with AST fallback.

Tier 3 — runs on architectural changes. Uses vulture for comprehensive
dead code detection when installed; falls back to a lightweight AST-based
checker when it's not.

Dead code is a particularly important signal for LLM-driven development:
- LLM agents often generate functions that go unused
- Refactoring sessions leave behind orphaned helpers
- Copy-paste-modify patterns leave dead code debris
- Context window churn means the agent forgets what it already wrote

Vulture finds: unused functions, classes, variables, imports, attributes.
AST fallback finds: unused module-level functions and classes (simpler but
catches the most common LLM anti-pattern: generating a function that
nothing calls).

Severity: informational by default (dead code is technical debt, not an error),
but configurable to "warning" via lintgate.yaml for teams that want to
enforce cleanup.
"""

from __future__ import annotations

import ast
import os
import re
from typing import TYPE_CHECKING

from ..types import LinterContext, LintIssue
from .base import BaseLinter

if TYPE_CHECKING:
    from collections.abc import Iterable


class DeadCodeChecker(BaseLinter):
    """Dead code detector — finds unused definitions.

    Strategy:
    1. If vulture is installed → use it (comprehensive, handles cross-file refs)
    2. If not → AST-based fallback (single-file, catches common patterns)

    Both modes emit informational issues by default.
    """

    name = "dead_code_checker"
    tier = 3
    timeout_ms = 8000
    required_tool = None  # Falls back to AST if vulture unavailable

    def available(self) -> bool:
        """Always available — AST fallback doesn't need external tools."""
        return True

    def run(self, ctx: LinterContext) -> Iterable[LintIssue]:
        """Run dead code detection."""
        severity_default = ctx.config.get("severity", "informational")
        min_confidence = ctx.config.get("min_confidence", 60)

        # Try vulture first (comprehensive)
        if self._vulture_available():
            yield from self._run_vulture(ctx, severity_default, min_confidence)
        else:
            # AST fallback (per-file, less comprehensive)
            yield from self._run_ast_fallback(ctx, severity_default)

    # ─── Vulture mode ────────────────────────────────────────────────

    def _vulture_available(self) -> bool:
        """Check if vulture is installed."""
        import shutil

        return shutil.which("vulture") is not None

    def _run_vulture(
        self,
        ctx: LinterContext,
        severity_default: str,
        min_confidence: int,
    ) -> Iterable[LintIssue]:
        """Run vulture for comprehensive dead code detection."""
        cmd = [
            "vulture",
            "--min-confidence",
            str(min_confidence),
        ]

        # Add whitelist files from config
        whitelist = ctx.config.get("whitelist", [])
        for wl in whitelist:
            cmd.extend(["--ignore-names", wl])

        # Ignore decorators that mark intentional "unused" patterns
        ignore_decorators = ctx.config.get(
            "ignore_decorators",
            ["@property", "@abstractmethod", "@overload", "@staticmethod"],
        )
        for dec in ignore_decorators:
            cmd.extend(["--ignore-decorators", dec.lstrip("@")])

        cmd.extend(ctx.files)

        result = self.run_command(cmd, ctx.project_root)

        # Vulture outputs one line per finding: filepath:line: message (confidence%)
        if result.stdout:
            yield from _parse_vulture_output(
                result.stdout,
                severity_default,
            )

    # ─── AST fallback mode ───────────────────────────────────────────

    def _run_ast_fallback(
        self,
        ctx: LinterContext,
        severity_default: str,
    ) -> Iterable[LintIssue]:
        """Per-file AST-based dead code detection (simple but catches common patterns).

        For each file, finds:
        - Module-level functions that are defined but never called within the file
        - Module-level classes that are defined but never referenced within the file
        - Imports that are never used (basic, ruff catches these too)

        Limitations vs vulture:
        - Only single-file analysis (can't detect cross-file usage)
        - Doesn't track attribute access on classes
        - Skipped for __init__.py (re-exports are expected)
        """
        for filepath in ctx.files:
            # Skip __init__.py — re-exports are normal there
            if os.path.basename(filepath) == "__init__.py":
                continue

            yield from _ast_dead_code_check(filepath, severity_default)


# ─── Vulture output parser ───────────────────────────────────────────────

# Vulture output format: filepath:line: unused TYPE 'NAME' (confidence%)
_VULTURE_RE = re.compile(r"^(.+?):(\d+):\s+(.+?)\s+\((\d+)%\s+confidence\)\s*$")


def _parse_vulture_output(
    output: str,
    severity_default: str,
) -> Iterable[LintIssue]:
    """Parse vulture's text output into LintIssues."""
    for line in output.strip().splitlines():
        match = _VULTURE_RE.match(line)
        if not match:
            continue

        filepath = match.group(1)
        lineno = int(match.group(2))
        description = match.group(3)
        confidence = int(match.group(4))

        # Classify the kind of dead code
        kind = _classify_vulture_finding(description)

        yield LintIssue(
            linter="dead_code",
            kind=kind,
            message=description,
            file=filepath,
            line=lineno,
            severity=severity_default,
            confidence=confidence / 100.0,
            evidence={"vulture_confidence": confidence},
            suggestions=_suggestions_for_kind(kind),
        )


def _classify_vulture_finding(description: str) -> str:
    """Classify a vulture finding into a more specific kind."""
    desc_lower = description.lower()
    if "import" in desc_lower:
        return "unused-import"
    if "function" in desc_lower:
        return "unused-function"
    if "class" in desc_lower:
        return "unused-class"
    if "variable" in desc_lower:
        return "unused-variable"
    if "attribute" in desc_lower:
        return "unused-attribute"
    if "property" in desc_lower:
        return "unused-property"
    return "dead-code"


def _suggestions_for_kind(kind: str) -> list[str]:
    """Get contextual suggestions for each kind of dead code."""
    suggestions_map = {
        "unused-import": [
            "Remove unused imports or add to __all__ if intentionally re-exported",
        ],
        "unused-function": [
            "Remove if no longer needed, or document why it's kept",
            "If used externally, add to a vulture whitelist",
        ],
        "unused-class": [
            "Remove if no longer needed",
            "If used as a base class externally, add to whitelist",
        ],
        "unused-variable": [
            "Use _ prefix for intentionally unused variables",
        ],
        "unused-attribute": [
            "Remove if no longer needed",
            "May indicate incomplete refactoring",
        ],
    }
    return suggestions_map.get(kind, ["Consider removing dead code to reduce maintenance burden"])


# ─── AST-based fallback ─────────────────────────────────────────────────


def _ast_dead_code_check(
    filepath: str,
    severity_default: str,
) -> Iterable[LintIssue]:
    """Simple AST-based dead code detection for a single file.

    Strategy:
    1. Collect all defined names (functions, classes) at module level
    2. Collect all referenced names in the entire file
    3. Report definitions that are never referenced

    Exclusions:
    - Names starting with _ (conventionally private/internal)
    - Names in __all__ (explicitly exported)
    - main() function (entry point convention)
    - Decorated functions (often used as callbacks, routes, etc.)
    """
    tree = _parse_file(filepath)
    if tree is None:
        return

    exported_names = _get_all_exports(tree)
    definitions = _collect_definitions(tree)
    referenced = _collect_references(tree)

    for name, lineno in definitions.items():
        if _should_skip_definition(name, exported_names, referenced, tree):
            continue

        kind = "unused-function" if _is_function(tree, name) else "unused-class"
        yield LintIssue(
            linter="dead_code",
            kind=kind,
            message=f"'{name}' appears to be unused within this module",
            file=filepath,
            line=lineno,
            severity=severity_default,
            confidence=0.7,  # Lower confidence — may be used externally
            suggestions=[
                "Remove if no longer needed",
                "If used by other modules, this is a false positive",
            ],
        )


def _parse_file(filepath: str) -> ast.Module | None:
    """Parse a file into an AST, returning None on error."""
    try:
        with open(filepath) as f:
            source = f.read()
    except OSError:
        return None

    try:
        return ast.parse(source, filename=filepath)
    except SyntaxError:
        return None


def _collect_definitions(tree: ast.Module) -> dict[str, int]:
    """Collect undecorated function/class definitions at module level."""
    definitions: dict[str, int] = {}
    for node in tree.body:
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and not node.decorator_list
        ):
            definitions[node.name] = node.lineno
    return definitions


def _collect_references(tree: ast.Module) -> set[str]:
    """Collect all referenced names in the entire file."""
    referenced: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            referenced.add(node.id)
        elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            referenced.add(node.value.id)
    return referenced


def _should_skip_definition(
    name: str,
    exported_names: set[str] | None,
    referenced: set[str],
    tree: ast.Module,
) -> bool:
    """Determine if a definition should be skipped (not reported as dead)."""
    if name.startswith("_"):
        return True
    if exported_names is not None and name in exported_names:
        return True
    if name == "main":
        return True
    return bool(name in referenced and _count_name_references(tree, name) > 0)


def _get_all_exports(tree: ast.Module) -> set[str] | None:
    """Get names from __all__ if defined, or None if not defined."""
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if (
                    isinstance(target, ast.Name)
                    and target.id == "__all__"
                    and isinstance(node.value, (ast.List, ast.Tuple))
                ):
                    names = set()
                    for elt in node.value.elts:
                        if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                            names.add(elt.value)
                    return names
    return None


def _count_name_references(tree: ast.Module, name: str) -> int:
    """Count how many times a name is referenced (not defined) in a module."""
    count = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == name and isinstance(node.ctx, ast.Load):
            # Skip if this is a function/class definition (not a reference)
            # We detect this by checking if the parent is a FunctionDef/ClassDef
            # Since ast.walk doesn't give parents, we use a simpler heuristic:
            # check if it's in a Load context
            count += 1
    return count


def _is_function(tree: ast.Module, name: str) -> bool:
    """Check if a name is defined as a function (vs class) at module level."""
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return True
    return False
