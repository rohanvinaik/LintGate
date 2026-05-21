"""Test suite optimizer — minimum killing set extraction and file compaction.

Uses mutation convergence analysis to identify the smallest subset of tests
that achieves the same specification coverage as the full suite, then
AST-extracts those tests into a compacted replacement file.

Two-phase design:
  1. Triage: load mutation convergence → extract minimum killing set
  2. Compact: AST-parse test file → filter to killing set → compose replacement
"""

from __future__ import annotations

import ast
import glob
import json
import os
from dataclasses import dataclass, field
from typing import Union

_FuncDef = Union[ast.FunctionDef, ast.AsyncFunctionDef]


# ── Data Types ────────────────────────────────────────────────────


@dataclass
class FunctionTriage:
    """Per-function triage result."""

    function_key: str
    sigma: int
    total_mutants: int
    killed: int
    killing_tests: list[str]  # minimum set from convergence


@dataclass
class TriageResult:
    """Result of analyzing a source file's test redundancy."""

    source_file: str
    analysis_id: str
    functions: list[FunctionTriage]
    killing_set: set[str]  # union of all killing test names
    total_tests_mapped: int  # total tests mapped to this source file
    kill_rate: float

    def summary(self) -> str:
        redundant = self.total_tests_mapped - len(self.killing_set)
        return (
            f"{self.source_file}: {self.total_tests_mapped} tests mapped, "
            f"{len(self.killing_set)} in minimum killing set, "
            f"{redundant} redundant ({self.kill_rate:.0%} kill rate)"
        )


@dataclass
class ParsedTestModule:
    """Structured representation of a parsed test file."""

    path: str
    source: str
    source_lines: list[str]
    tree: ast.Module
    docstring: str | None
    imports: list[ast.stmt]  # all import nodes (future + regular)
    constants: list[ast.stmt]  # module-level assignments
    fixtures: dict[str, _FuncDef]
    helpers: dict[str, _FuncDef]
    test_functions: dict[str, _FuncDef]
    test_classes: dict[str, ast.ClassDef]
    # Flattened index: test method name → (class_name, method_node)
    class_test_methods: dict[str, tuple[str, ast.FunctionDef]] = field(default_factory=dict)

    def all_test_names(self) -> set[str]:
        """All test function/method names across module-level and classes."""
        return set(self.test_functions.keys()) | set(self.class_test_methods.keys())


@dataclass
class CompactResult:
    """Result of test file compaction."""

    source_file: str
    test_file: str
    original_test_count: int
    original_lines: int
    compacted_test_count: int
    compacted_lines: int
    content: str
    skipped_class_tests: list[str] = field(default_factory=list)
    original_content: str = ""


# ── Triage ────────────────────────────────────────────────────────


def find_latest_analysis(project_root: str, source_file: str) -> str | None:
    """Find the most recent mutation_run_full analysis for a source file."""
    analysis_dir = os.path.join(project_root, ".lintgate", "analysis", "mutation_run_full")
    if not os.path.isdir(analysis_dir):
        return None

    candidates = []
    for f in os.listdir(analysis_dir):
        if not f.endswith(".json"):
            continue
        path = os.path.join(analysis_dir, f)
        try:
            with open(path) as fh:
                data = json.load(fh)
            if data.get("file", "") == source_file:
                mtime = os.path.getmtime(path)
                candidates.append((mtime, path, data))
        except (json.JSONDecodeError, OSError):
            continue

    if not candidates:
        return None
    # Return path of most recent
    candidates.sort(key=lambda x: -x[0])
    return candidates[0][1]


def run_triage(project_root: str, source_file: str, analysis_id: str = "") -> TriageResult | None:
    """Extract minimum killing set from cached mutation convergence data.

    Args:
        project_root: Project root directory.
        source_file: Relative path to the source file being triaged.
        analysis_id: Optional specific analysis ID. Finds latest if empty.

    Returns:
        TriageResult with per-function breakdown and minimum killing set,
        or None if no mutation analysis is available.
    """
    if analysis_id:
        path = os.path.join(
            project_root, ".lintgate", "analysis", "mutation_run_full", f"{analysis_id}.json"
        )
        if not os.path.isfile(path):
            return None
    else:
        path = find_latest_analysis(project_root, source_file)
        if not path:
            return None

    with open(path) as f:
        data = json.load(f)

    convergence = data.get("analysis", {}).get("convergence", [])
    results = data.get("results", [])
    aid = data.get("_meta", {}).get("analysis_id", os.path.basename(path).removesuffix(".json"))
    if not aid:
        aid = os.path.basename(path).removesuffix(".json")

    # Build kill rate from results
    total_mutants = sum(r.get("total_mutants", 0) for r in results)
    total_killed = sum(r.get("total_killed", 0) for r in results)
    kill_rate = total_killed / total_mutants if total_mutants > 0 else 0.0

    # Extract minimum killing set from convergence steps
    killing_set: set[str] = set()
    functions: list[FunctionTriage] = []

    for conv in convergence:
        func_key = conv.get("function_key", "")
        sigma = conv.get("sigma", 0)
        steps = conv.get("steps", [])

        func_kills: list[str] = []
        for step in steps:
            test_name = step.get("test_name", "")
            if test_name:
                killing_set.add(test_name)
                func_kills.append(test_name)

        # Match to results for mutant counts
        matching_result = next(
            (r for r in results if r.get("function_key") == func_key), None
        )
        total_m = matching_result.get("total_mutants", 0) if matching_result else 0
        killed_m = matching_result.get("total_killed", 0) if matching_result else 0

        functions.append(
            FunctionTriage(
                function_key=func_key,
                sigma=sigma,
                total_mutants=total_m,
                killed=killed_m,
                killing_tests=func_kills,
            )
        )

    # Count total tests mapped to this source file via test-impact
    total_mapped = _count_mapped_tests(project_root, source_file)

    return TriageResult(
        source_file=source_file,
        analysis_id=aid,
        functions=functions,
        killing_set=killing_set,
        total_tests_mapped=total_mapped,
        kill_rate=kill_rate,
    )


def _count_mapped_tests(project_root: str, source_file: str) -> int:
    """Count total test functions mapped to functions in source_file."""
    test_files = sorted(glob.glob(os.path.join(project_root, "tests", "test_*.py")))
    if not test_files:
        return 0

    try:
        from lintgate.specification.test_impact import build_test_impact_map

        impact = build_test_impact_map(test_files)
    except Exception:
        return 0

    # Get function names from the source file
    source_path = os.path.join(project_root, source_file)
    if not os.path.isfile(source_path):
        return 0

    try:
        with open(source_path) as f:
            tree = ast.parse(f.read())
    except (SyntaxError, OSError):
        return 0

    func_names = {
        n.name
        for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }

    total = 0
    mappings = impact.to_dict().get("mappings", {})
    for name in func_names:
        refs = mappings.get(name, [])
        total += len(refs)
    return total


# ── Test File Parsing ─────────────────────────────────────────────


def parse_test_module(path: str) -> ParsedTestModule | None:
    """Parse a test file into structured components."""
    try:
        with open(path) as f:
            source = f.read()
        tree = ast.parse(source)
    except (SyntaxError, OSError):
        return None

    source_lines = source.splitlines()
    docstring = None
    imports: list[ast.stmt] = []
    constants: list[ast.stmt] = []
    fixtures: dict[str, _FuncDef] = {}
    helpers: dict[str, _FuncDef] = {}
    test_functions: dict[str, _FuncDef] = {}
    test_classes: dict[str, ast.ClassDef] = {}

    for node in tree.body:
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            if docstring is None and tree.body.index(node) == 0:
                docstring = node.value.value
                continue

        if isinstance(node, (ast.Import, ast.ImportFrom)):
            imports.append(node)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            constants.append(node)
        elif isinstance(node, ast.FunctionDef):
            if node.name.startswith("test_"):
                test_functions[node.name] = node
            elif _is_fixture(node):
                fixtures[node.name] = node
            else:
                helpers[node.name] = node
        elif isinstance(node, ast.AsyncFunctionDef):
            if node.name.startswith("test_"):
                test_functions[node.name] = node
            elif _is_fixture(node):
                fixtures[node.name] = node
            else:
                helpers[node.name] = node
        elif isinstance(node, ast.ClassDef):
            if node.name.startswith("Test") or any(
                n.name.startswith("test_")
                for n in ast.walk(node)
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            ):
                test_classes[node.name] = node
            else:
                # Treat as a constant/helper class
                constants.append(node)

    # Build flattened class method index
    class_test_methods: dict[str, tuple[str, ast.FunctionDef]] = {}
    for cls_name, cls_node in test_classes.items():
        for child in ast.walk(cls_node):
            if isinstance(child, ast.FunctionDef) and child.name.startswith("test_"):
                class_test_methods[child.name] = (cls_name, child)

    return ParsedTestModule(
        path=path,
        source=source,
        source_lines=source_lines,
        tree=tree,
        docstring=docstring,
        imports=imports,
        constants=constants,
        fixtures=fixtures,
        helpers=helpers,
        test_functions=test_functions,
        test_classes=test_classes,
        class_test_methods=class_test_methods,
    )


def _is_fixture(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Check if a function is decorated with @pytest.fixture."""
    for dec in node.decorator_list:
        if isinstance(dec, ast.Name) and dec.id == "fixture":
            return True
        if isinstance(dec, ast.Attribute) and dec.attr == "fixture":
            return True
        if isinstance(dec, ast.Call):
            func = dec.func
            if isinstance(func, ast.Name) and func.id == "fixture":
                return True
            if isinstance(func, ast.Attribute) and func.attr == "fixture":
                return True
    return False


# ── Dependency Tracing ────────────────────────────────────────────


def _referenced_names(node: ast.AST) -> set[str]:
    """Collect all name references in an AST subtree."""
    names: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Name):
            names.add(child.id)
        elif isinstance(child, ast.Attribute):
            root = child
            while isinstance(root, ast.Attribute):
                root = root.value
            if isinstance(root, ast.Name):
                names.add(root.id)
    return names


def _trace_needed_fixtures(
    test_names: set[str],
    module: ParsedTestModule,
) -> set[str]:
    """Find all fixtures needed by the surviving tests (transitive)."""
    needed: set[str] = set()
    work = set(test_names)

    while work:
        name = work.pop()
        # Get the function node
        node = module.test_functions.get(name) or module.fixtures.get(name)
        if node is None:
            continue
        # Check parameters — fixture injection
        for arg in node.args.args:
            arg_name = arg.arg
            if arg_name in module.fixtures and arg_name not in needed:
                needed.add(arg_name)
                work.add(arg_name)  # fixture may depend on other fixtures
        # Check body for explicit fixture references
        body_refs = _referenced_names(node)
        for ref in body_refs:
            if ref in module.fixtures and ref not in needed:
                needed.add(ref)
                work.add(ref)

    return needed


def _trace_needed_helpers(
    test_names: set[str],
    fixture_names: set[str],
    module: ParsedTestModule,
) -> set[str]:
    """Find all helper functions needed by surviving tests and fixtures."""
    needed: set[str] = set()
    # Check all surviving tests + fixtures for helper references
    all_nodes = []
    for name in test_names:
        if name in module.test_functions:
            all_nodes.append(module.test_functions[name])
    for name in fixture_names:
        if name in module.fixtures:
            all_nodes.append(module.fixtures[name])

    for node in all_nodes:
        refs = _referenced_names(node)
        for ref in refs:
            if ref in module.helpers:
                needed.add(ref)

    # Transitive: helpers may call other helpers
    changed = True
    while changed:
        changed = False
        for name in list(needed):
            if name in module.helpers:
                refs = _referenced_names(module.helpers[name])
                for ref in refs:
                    if ref in module.helpers and ref not in needed:
                        needed.add(ref)
                        changed = True

    return needed


def _trace_needed_constants(
    test_names: set[str],
    fixture_names: set[str],
    helper_names: set[str],
    module: ParsedTestModule,
) -> list[ast.stmt]:
    """Find module-level constants/assignments referenced by surviving code."""
    # Collect all names defined by constants
    const_map: dict[str, ast.stmt] = {}
    for node in module.constants:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                for name_node in ast.walk(target):
                    if isinstance(name_node, ast.Name):
                        const_map[name_node.id] = node
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            const_map[node.target.id] = node
        elif isinstance(node, ast.ClassDef):
            const_map[node.name] = node

    # Collect all referenced names from surviving code
    all_refs: set[str] = set()
    for name in test_names:
        if name in module.test_functions:
            all_refs |= _referenced_names(module.test_functions[name])
    for name in fixture_names:
        if name in module.fixtures:
            all_refs |= _referenced_names(module.fixtures[name])
    for name in helper_names:
        if name in module.helpers:
            all_refs |= _referenced_names(module.helpers[name])

    needed = []
    seen_ids: set[int] = set()
    for ref_name in all_refs:
        if ref_name in const_map:
            node = const_map[ref_name]
            node_id = id(node)
            if node_id not in seen_ids:
                seen_ids.add(node_id)
                needed.append(node)
    return needed


# ── Source Extraction ─────────────────────────────────────────────


def _extract_node_source(source_lines: list[str], node: ast.stmt) -> str:
    """Extract source text for an AST node, preserving original formatting."""
    start = getattr(node, "lineno", 1) - 1  # 0-indexed
    end = getattr(node, "end_lineno", start + 1)  # exclusive for slice

    # Include decorators for function/class defs
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        if node.decorator_list:
            decorator_starts = [d.lineno for d in node.decorator_list]
            if decorator_starts:
                start = min(decorator_starts) - 1

    return "\n".join(source_lines[start:end])


def _extract_import_source(source_lines: list[str], node: ast.stmt) -> str:
    """Extract import statement source text."""
    start = node.lineno - 1
    end = node.end_lineno if node.end_lineno else node.lineno
    return "\n".join(source_lines[start:end])


# ── File Composition ──────────────────────────────────────────────


def compose_compacted_file(
    module: ParsedTestModule,
    surviving_tests: set[str],
) -> str:
    """Compose a new test file with only the surviving tests + dependencies."""
    # Trace transitive dependencies
    needed_fixtures = _trace_needed_fixtures(surviving_tests, module)
    needed_helpers = _trace_needed_helpers(surviving_tests, needed_fixtures, module)
    needed_constants = _trace_needed_constants(
        surviving_tests, needed_fixtures, needed_helpers, module
    )

    lines = module.source_lines
    parts: list[str] = []

    # 1. Module docstring
    if module.docstring:
        parts.append(f'"""Test suite for {module.path} — optimized by test_compact."""')
    else:
        parts.append(f'"""Test suite — optimized by test_compact."""')

    parts.append("")

    # 2. All imports (keep them all — ruff cleans unused)
    for node in module.imports:
        parts.append(_extract_import_source(lines, node))
    parts.append("")

    # 3. Needed constants (in original order)
    original_constants_ordered = [
        c for c in module.constants if c in needed_constants
    ]
    if original_constants_ordered:
        for node in original_constants_ordered:
            parts.append(_extract_node_source(lines, node))
        parts.append("")

    # 4. Needed fixtures (in original order)
    fixture_order = [
        name for name in module.fixtures if name in needed_fixtures
    ]
    if fixture_order:
        for name in fixture_order:
            parts.append(_extract_node_source(lines, module.fixtures[name]))
            parts.append("")

    # 5. Needed helpers (in original order)
    helper_order = [
        name for name in module.helpers if name in needed_helpers
    ]
    if helper_order:
        for name in helper_order:
            parts.append(_extract_node_source(lines, module.helpers[name]))
            parts.append("")

    # 6. Surviving module-level test functions (in original order)
    test_order = [
        name for name in module.test_functions if name in surviving_tests
    ]
    for name in test_order:
        parts.append(_extract_node_source(lines, module.test_functions[name]))
        parts.append("")

    # 7. Test classes — keep classes that contain surviving methods,
    #    trimming out non-surviving methods within each class
    for _cls_name, cls_node in module.test_classes.items():
        surviving_methods = [
            child
            for child in ast.walk(cls_node)
            if isinstance(child, ast.FunctionDef)
            and child.name.startswith("test_")
            and child.name in surviving_tests
        ]
        if not surviving_methods:
            continue  # No surviving methods — drop entire class
        # Check if ALL test methods survive — keep class as-is
        all_methods = [
            child
            for child in ast.walk(cls_node)
            if isinstance(child, ast.FunctionDef)
            and child.name.startswith("test_")
        ]
        if len(surviving_methods) == len(all_methods):
            # Keep entire class unchanged
            parts.append(_extract_node_source(lines, cls_node))
            parts.append("")
        else:
            # Rebuild class with only surviving methods + non-test methods
            parts.append(_compose_trimmed_class(lines, cls_node, surviving_tests))
            parts.append("")

    content = "\n".join(parts)
    # Ensure single trailing newline
    return content.rstrip("\n") + "\n"


def _compose_trimmed_class(
    source_lines: list[str],
    cls_node: ast.ClassDef,
    surviving_tests: set[str],
) -> str:
    """Rebuild a test class keeping only surviving test methods + setup/helpers."""
    # Extract class header (decorators + class line)
    cls_start = cls_node.lineno - 1
    if cls_node.decorator_list:
        cls_start = min(d.lineno for d in cls_node.decorator_list) - 1

    # Find the first child's line to determine where the class body starts
    first_body = cls_node.body[0] if cls_node.body else None
    if first_body is None:
        return _extract_node_source(source_lines, cls_node)

    # Class header = everything from class_start to just before first body element
    header_end = first_body.lineno - 1
    # Check for class docstring
    if (
        isinstance(first_body, ast.Expr)
        and isinstance(first_body.value, ast.Constant)
        and isinstance(first_body.value.value, str)
    ):
        header_end = first_body.end_lineno if first_body.end_lineno else first_body.lineno

    header = "\n".join(source_lines[cls_start:header_end])

    # Collect surviving members
    member_sources: list[str] = []
    for child in cls_node.body:
        # Skip docstring (already in header)
        if (
            child is first_body
            and isinstance(child, ast.Expr)
            and isinstance(child.value, ast.Constant)
            and isinstance(child.value.value, str)
        ):
            continue

        if isinstance(child, ast.FunctionDef):
            if child.name.startswith("test_"):
                if child.name in surviving_tests:
                    member_sources.append(_extract_node_source(source_lines, child))
            else:
                # Keep non-test methods (setUp, tearDown, helpers)
                member_sources.append(_extract_node_source(source_lines, child))
        else:
            # Keep non-function class members (class variables, etc.)
            member_sources.append(_extract_node_source(source_lines, child))

    if not member_sources:
        return header + "\n    pass"

    return header + "\n\n" + "\n\n".join(member_sources)


# ── Compact ───────────────────────────────────────────────────────


def find_test_files_for_source(project_root: str, source_file: str) -> list[str]:
    """Find test files that cover a given source file via test-impact mapping."""
    test_dir = os.path.join(project_root, "tests")
    test_files = sorted(glob.glob(os.path.join(test_dir, "test_*.py")))
    if not test_files:
        return []

    try:
        from lintgate.specification.test_impact import build_test_impact_map

        impact = build_test_impact_map(test_files)
    except Exception:
        return []

    # Get function names from source file
    source_path = os.path.join(project_root, source_file)
    if not os.path.isfile(source_path):
        return []

    try:
        with open(source_path) as f:
            tree = ast.parse(f.read())
    except (SyntaxError, OSError):
        return []

    func_names = {
        n.name
        for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }

    # Find which test files contain tests mapped to our source functions
    mappings = impact.to_dict().get("mappings", {})
    covering_files: set[str] = set()
    for name in func_names:
        for ref in mappings.get(name, []):
            covering_files.add(ref["test_file"])

    return sorted(covering_files)


def run_compact(
    project_root: str,
    source_file: str,
    triage: TriageResult | None = None,
    test_file: str = "",
) -> CompactResult | None:
    """Compact a test file to its minimum killing set.

    Args:
        project_root: Project root directory.
        source_file: Relative path to the source file.
        triage: Pre-computed triage result. Computed on-the-fly if None.
        test_file: Specific test file to compact. Auto-detected if empty.

    Returns:
        CompactResult with the compacted file content, or None on failure.
    """
    if triage is None:
        triage = run_triage(project_root, source_file)
    if triage is None:
        return None
    if not triage.killing_set:
        return None

    # Find the test file to compact
    if test_file:
        candidates = [os.path.join(project_root, test_file)]
    else:
        candidates = find_test_files_for_source(project_root, source_file)
        if not candidates:
            return None

    # Parse all candidates and find the best killing-set intersection.
    parsed_modules: list[ParsedTestModule] = []
    best_module: ParsedTestModule | None = None
    best_surviving: set[str] = set()
    for tf in candidates:
        parsed = parse_test_module(tf)
        if parsed is None:
            continue
        parsed_modules.append(parsed)
        overlap = triage.killing_set & parsed.all_test_names()
        if len(overlap) > len(best_surviving):
            best_module = parsed
            best_surviving = overlap

    if not parsed_modules:
        return None

    if not best_surviving:
        # Killing tests live in OTHER files — primary file is entirely redundant.
        # Pick the largest candidate as the file to report on.
        primary = None
        primary_count = -1
        for tf in candidates:
            parsed = parse_test_module(tf)
            if parsed and len(parsed.all_test_names()) > primary_count:
                primary_count = len(parsed.all_test_names())
                primary = parsed
        if primary is None:
            return None
        original_count = len(primary.test_functions) + sum(
            sum(1 for n in ast.walk(cls) if isinstance(n, ast.FunctionDef) and n.name.startswith("test_"))
            for cls in primary.test_classes.values()
        )
        stub = f'"""Tests for {source_file} — all specification coverage exists in other test files."""\n'
        return CompactResult(
            source_file=source_file,
            test_file=primary.path,
            original_test_count=original_count,
            original_lines=len(primary.source_lines),
            compacted_test_count=0,
            compacted_lines=1,
            content=stub,
            original_content="\n".join(primary.source_lines),
        )

    # At this point best_module is guaranteed non-None (set when overlap > 0)
    assert best_module is not None

    # Compose the compacted file
    content = compose_compacted_file(best_module, best_surviving)

    original_test_count = len(best_module.test_functions) + sum(
        sum(1 for n in ast.walk(cls) if isinstance(n, ast.FunctionDef) and n.name.startswith("test_"))
        for cls in best_module.test_classes.values()
    )

    return CompactResult(
        source_file=source_file,
        test_file=best_module.path,
        original_test_count=original_test_count,
        original_lines=len(best_module.source_lines),
        compacted_test_count=len(best_surviving),
        compacted_lines=content.count("\n") + 1,
        content=content,
        original_content="\n".join(best_module.source_lines),
    )
