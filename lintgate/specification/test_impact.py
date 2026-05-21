"""Test-impact mapping — skip irrelevant tests during mutation evaluation.

Phase 1 (static): Parse test file imports and AST, map test functions
to source functions they reference. Reuses the same heuristic as
ledger.py:_build_test_coverage_map but returns structured pairs.

Phase 2 (qualified): Import-aware resolution produces both bare and
qualified call names so ``utils.helper()`` maps to ``"utils.helper"``
not just ``"helper"``. Supports ``# lintgate: covers`` directives.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field

# Bare names that are too common to be useful as fallback lookup keys.
# When only a bare-name entry exists, tests_for() will NOT return it
# for these names — a qualified match is required.
_AMBIGUOUS_BARE_NAMES = frozenset({
    "from_dict", "to_dict", "__init__", "__repr__", "__str__",
    "__eq__", "__hash__", "setup", "teardown", "run", "execute",
    "process", "validate", "parse", "load", "save", "get", "set",
})


@dataclass
class TestImpactMap:
    """Mapping from source functions to their covering test functions."""

    function_to_tests: dict[str, list[TestReference]] = field(default_factory=dict)
    total_test_functions: int = 0
    total_source_functions_covered: int = 0

    def tests_for(self, func_name: str) -> list[TestReference]:
        """Get test references for a function name.

        Primary: exact key lookup (works for both bare and qualified).
        Fallback: if *func_name* contains a dot, try the bare tail.
        Bare-name fallback is suppressed for names in _AMBIGUOUS_BARE_NAMES.
        """
        direct = self.function_to_tests.get(func_name)
        if direct is not None:
            return direct

        # Bare-name fallback for qualified lookups
        if "." in func_name:
            bare = func_name.rsplit(".", 1)[-1]
            if bare not in _AMBIGUOUS_BARE_NAMES:
                return self.function_to_tests.get(bare, [])

        return []

    def to_dict(self) -> dict:
        return {
            "total_test_functions": self.total_test_functions,
            "total_source_functions_covered": self.total_source_functions_covered,
            "mappings": {k: [t.to_dict() for t in v] for k, v in self.function_to_tests.items()},
        }


@dataclass
class TestReference:
    """A reference from a test function to a source function."""

    test_file: str
    test_function: str

    def to_dict(self) -> dict:
        return {"test_file": self.test_file, "test_function": self.test_function}


def build_test_impact_map(test_files: list[str]) -> TestImpactMap:
    """Build a test impact map from test file ASTs.

    Scans test functions for calls to source functions and builds
    a mapping from source function name -> list of (test_file, test_func).
    """
    impact = TestImpactMap()
    total_tests = 0

    for test_file in test_files:
        tests_in_file = _scan_test_file(test_file)
        total_tests += len(tests_in_file)

        for test_func_name, called_functions in tests_in_file.items():
            seen: set[tuple[str, str, str]] = set()
            for called in called_functions:
                triple = (test_file, test_func_name, called)
                if triple in seen:
                    continue
                seen.add(triple)
                ref = TestReference(test_file=test_file, test_function=test_func_name)
                impact.function_to_tests.setdefault(called, []).append(ref)

    impact.total_test_functions = total_tests
    impact.total_source_functions_covered = len(impact.function_to_tests)
    return impact


# ── Import extraction ─────────────────────────────────────────────


def _extract_imports(tree: ast.Module) -> dict[str, str]:
    """Walk Import/ImportFrom nodes, return {local_name: qualified_name}.

    Examples:
        import os            -> {"os": "os"}
        import os as myos    -> {"myos": "os"}
        from os.path import join       -> {"join": "os.path.join"}
        from os.path import join as j  -> {"j": "os.path.join"}
    """
    imports: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                local = alias.asname if alias.asname else alias.name
                imports[local] = alias.name
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for alias in node.names:
                local = alias.asname if alias.asname else alias.name
                qualified = f"{module}.{alias.name}" if module else alias.name
                imports[local] = qualified
    return imports


# ── Covers directives ─────────────────────────────────────────────

_COVERS_RE = re.compile(
    r"#\s*lintgate:\s*covers\s+([\w.]+)::([\w.]+)"
)


def _extract_covers_directives(
    source: str,
    func_lines: dict[str, int],
) -> dict[str, list[str]]:
    """Scan source for ``# lintgate: covers pkg.mod::func`` comments.

    *func_lines* maps test function names to their start line numbers.
    Each directive is associated with the test function whose start line
    is the earliest line >= the directive's line number (i.e., the next
    test function at or after the comment).

    Returns {test_func_name: [qualified_target, ...]}.
    """
    if not func_lines:
        return {}

    # Sort test functions by start line for association
    sorted_funcs = sorted(func_lines.items(), key=lambda x: x[1])

    result: dict[str, list[str]] = {}
    for lineno, line in enumerate(source.splitlines(), 1):
        m = _COVERS_RE.search(line)
        if not m:
            continue
        module_part = m.group(1)
        func_part = m.group(2)
        target = "::".join((module_part, func_part))

        # Find the first test function that starts at or after this line
        owner: str | None = None
        for fname, fline in sorted_funcs:
            if fline >= lineno:
                owner = fname
                break
        if owner:
            result.setdefault(owner, []).append(target)

    return result


# ── Qualified call name resolution ────────────────────────────────


def _call_name_qualified(
    node: ast.Call,
    imports: dict[str, str],
) -> tuple[str | None, str | None]:
    """Return (bare_name, qualified_name) for a Call node.

    - ast.Name call: bare = id, qualified = imports.get(id, id)
    - ast.Attribute call: bare = attr, qualified resolves obj through imports
    """
    if isinstance(node.func, ast.Name):
        bare = node.func.id
        qualified = imports.get(bare, bare)
        return (bare, qualified)

    if isinstance(node.func, ast.Attribute):
        bare = node.func.attr
        # Try to resolve the object (e.g. ``utils.helper()`` -> ``utils.helper``)
        if isinstance(node.func.value, ast.Name):
            obj_name = node.func.value.id
            resolved_obj = imports.get(obj_name, obj_name)
            qualified = f"{resolved_obj}.{bare}"
            return (bare, qualified)
        return (bare, None)

    return (None, None)


def _call_name(node: ast.Call) -> str | None:
    """Extract call name from a Call node (backward-compatible bare-name version).

    For attribute calls (obj.method), returns the bare method name.
    The TestImpactMap.tests_for lookup uses bare names, so callers
    should also look up by bare name for consistency.
    """
    bare, _ = _call_name_qualified(node, {})
    return bare


def _scan_test_file(filepath: str) -> dict[str, list[str]]:
    """Scan a test file, return {test_func_name: [called_function_names]}."""
    try:
        with open(filepath, encoding="utf-8") as f:
            source = f.read()
        tree = ast.parse(source, filename=filepath)
    except (OSError, SyntaxError):
        return {}

    imports = _extract_imports(tree)

    # Build func_lines for covers directive association
    func_lines: dict[str, int] = {}
    _collect_func_lines(tree.body, func_lines)

    # Collect same-file non-test helpers so tests that call wrappers still
    # link to the underlying source function (1-hop follow).
    helpers: dict[str, list[str]] = {}
    _collect_helpers(tree.body, helpers, imports=imports)

    result: dict[str, list[str]] = {}
    _collect_tests(tree.body, result, imports=imports)

    # 1-hop helper follow: when a test calls a local helper, merge the
    # helper's outbound calls into the test's call-set.
    for test_func, calls in list(result.items()):
        _expand_with_helper_calls(calls, helpers)

    # Naming-strategy synthetic targets: link tests by naming convention
    # even when the call-graph doesn't reveal the source target (e.g. the
    # target is reached through a wrapper defined outside this file).
    for test_func, calls in list(result.items()):
        for synthetic in _name_synthetic_targets(test_func):
            if synthetic not in calls:
                calls.append(synthetic)

    # Merge covers directives
    covers = _extract_covers_directives(source, func_lines)
    for test_func, targets in covers.items():
        result.setdefault(test_func, []).extend(targets)

    return result


def _collect_func_lines(
    body: list[ast.stmt],
    func_lines: dict[str, int],
    prefix: str = "",
) -> None:
    """Collect {qualified_test_name: start_lineno} for covers association."""
    for node in body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith(
            "test_"
        ):
            qualified = f"{prefix}{node.name}" if prefix else node.name
            func_lines[qualified] = node.lineno
        elif isinstance(node, ast.ClassDef):
            class_prefix = f"{prefix}{node.name}."
            _collect_func_lines(node.body, func_lines, class_prefix)


def _collect_tests(
    body: list[ast.stmt],
    result: dict[str, list[str]],
    prefix: str = "",
    imports: dict[str, str] | None = None,
) -> None:
    """Collect test functions and methods with qualified class context."""
    for node in body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith(
            "test_"
        ):
            qualified = f"{prefix}{node.name}" if prefix else node.name
            result[qualified] = _extract_calls(node, imports=imports)
        elif isinstance(node, ast.ClassDef):
            class_prefix = f"{prefix}{node.name}."
            _collect_tests(node.body, result, class_prefix, imports=imports)


def _extract_calls(
    test_func: ast.FunctionDef | ast.AsyncFunctionDef,
    imports: dict[str, str] | None = None,
) -> list[str]:
    """Extract function call names from a test function body.

    When *imports* is provided, produces both qualified and bare names
    for each call (qualified as primary, bare as secondary).
    """
    names: list[str] = []
    seen: set[str] = set()
    _imports = imports or {}

    for node in ast.walk(test_func):
        if not isinstance(node, ast.Call):
            continue
        bare, qualified = _call_name_qualified(node, _imports)
        if bare and bare.startswith("test_"):
            continue
        # Add qualified first (primary), then bare (secondary)
        if qualified and qualified not in seen:
            seen.add(qualified)
            names.append(qualified)
        if bare and bare not in seen and bare != qualified:
            seen.add(bare)
            names.append(bare)

    return names


# ── 1-hop helper follow + naming-strategy synthetic targets ───────


def _collect_helpers(
    body: list[ast.stmt],
    helpers: dict[str, list[str]],
    imports: dict[str, str] | None = None,
) -> None:
    """Collect module-level non-test helper functions and their outbound calls.

    Nested defs, class methods, and test_* functions are excluded. Used
    by the 1-hop follow so tests calling a local wrapper still resolve
    to what the wrapper actually calls.
    """
    for node in body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith(
            "test_"
        ):
            helpers[node.name] = _extract_calls(node, imports=imports)


def _expand_with_helper_calls(
    calls: list[str],
    helpers: dict[str, list[str]],
) -> None:
    """1-hop: if *calls* references a local helper, inline the helper's calls.

    In-place mutation — *calls* grows with deduped additions. Only one
    hop is followed (no recursion) so the expansion is bounded by the
    number of same-file helpers.
    """
    seen = set(calls)
    for call in list(calls):
        bare = call.rsplit(".", 1)[-1] if "." in call else call
        helper_calls = helpers.get(bare)
        if not helper_calls:
            continue
        for hc in helper_calls:
            if hc not in seen:
                seen.add(hc)
                calls.append(hc)


def _camel_to_snake(name: str) -> str:
    """CamelCase → snake_case. ComputeScore → compute_score."""
    if not name:
        return ""
    s1 = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name)
    return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s1).lower()


def _name_synthetic_targets(qualified_test_name: str) -> list[str]:
    """Derive synthetic source targets from a test's qualified name.

    Mirrors the naming strategy from source_mapper_mapping but injected
    into the impact map so the mutation engine — which uses call-graph
    only — also benefits from convention-based linkage.

    Examples:
        TestComputeScore.test_foo  → [foo, _foo, ComputeScore, compute_score,
                                      _compute_score, ComputeScore.foo,
                                      compute_score.foo]
        test_detect_lockfile       → [detect_lockfile, _detect_lockfile]
    """
    targets: list[str] = []
    seen: set[str] = set()

    if "." in qualified_test_name:
        class_name, method_name = qualified_test_name.rsplit(".", 1)
    else:
        class_name, method_name = "", qualified_test_name

    if not method_name.startswith("test_"):
        return []
    stripped = method_name[len("test_") :]

    def _add(t: str) -> None:
        if t and t not in seen:
            seen.add(t)
            targets.append(t)

    if stripped:
        _add(stripped)
        _add(f"_{stripped}")

    if class_name.startswith("Test"):
        source_cls = class_name[len("Test") :]
        if source_cls:
            snake = _camel_to_snake(source_cls)
            _add(source_cls)
            _add(snake)
            _add(f"_{snake}")
            if stripped:
                _add(f"{source_cls}.{stripped}")
                if snake:
                    _add(f"{snake}.{stripped}")

    return targets
