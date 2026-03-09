"""Structure pattern detection — STRUCT005 (package candidates) and STRUCT006 (cross-file patterns).

STRUCT005: Detect flat files that should become a Python package based on
shared name prefixes and import relationships.

STRUCT006: Detect repeated structural patterns across files using
AST-fingerprinted pattern catalogs.

No LLM calls. Fully deterministic. AST-based.
"""

from __future__ import annotations

import ast
import os
from collections import defaultdict

from lintgate.types import LintIssue

# ── STRUCT005: Package Candidate Detection ──────────────────────────────


def _extract_prefix_groups(files: list[str]) -> dict[str, list[str]]:
    """Group files by their name prefix (text before the first '_')."""
    prefix_groups: dict[str, list[str]] = defaultdict(list)
    for filepath in files:
        stem = os.path.splitext(os.path.basename(filepath))[0]
        if stem.startswith("__") or stem.startswith("test_"):
            continue
        parts = stem.split("_")
        if len(parts) < 2:
            continue
        prefix_groups[parts[0]].append(filepath)
    return dict(prefix_groups)


def _resolve_group_modules(
    group_files: list[str],
    file_map: dict[str, str],
) -> set[str]:
    """Map filepaths to module names using the file_map."""
    group_modules: set[str] = set()
    for fp in group_files:
        abs_fp = os.path.abspath(fp)
        for mod, mod_fp in file_map.items():
            if os.path.abspath(mod_fp) == abs_fp:
                group_modules.add(mod)
                break
    return group_modules


def _count_intra_group_imports(
    group_modules: set[str],
    import_graph: dict[str, set[str]],
) -> int:
    """Count import edges between modules within the group."""
    count = 0
    for mod in group_modules:
        for imp in import_graph.get(mod, set()):
            if imp in group_modules and imp != mod:
                count += 1
    return count


def _build_package_candidate_finding(
    prefix: str,
    group_files: list[str],
    import_edges: int,
    project_root: str,
) -> LintIssue:
    """Build a STRUCT005 LintIssue for a package candidate."""
    rel_files = [os.path.relpath(f, project_root) for f in group_files]
    names = [os.path.splitext(os.path.basename(f))[0] for f in group_files]
    name_list = ", ".join(names[:4])
    suffix = f" (+{len(names) - 4} more)" if len(names) > 4 else ""
    return LintIssue(
        linter="structure_channel",
        kind="STRUCT005",
        message=(
            f"Files {name_list}{suffix} share prefix '{prefix}' "
            f"and have import relationships. "
            f"Consider creating a '{prefix}/' package."
        ),
        file=group_files[0],
        severity="informational",
        confidence=0.6,
        evidence={
            "code": "STRUCT005",
            "prefix": prefix,
            "files": rel_files,
            "import_edges": import_edges,
        },
        suggestions=[
            f"Create {prefix}/ directory with __init__.py",
            f"Move {prefix}_*.py files into the package",
            "Update import paths in dependent modules",
        ],
    )


def check_package_candidates(
    py_files: list[str],
    import_graph: dict[str, set[str]],
    file_map: dict[str, str],
    project_root: str,
    *,
    min_files: int = 3,
) -> list[LintIssue]:
    """Detect flat files that should become a Python package.

    Algorithm:
    1. Group files by directory
    2. Within each directory, extract name prefix (before first '_')
    3. Groups with ≥min_files files sharing prefix → candidate
    4. Verify ≥1 intra-group import edge exists
    5. Verify no __init__.py / subdirectory already exists for that prefix

    Emits STRUCT005 (informational, confidence 0.6).
    """
    findings: list[LintIssue] = []

    # Group files by directory
    dir_files: dict[str, list[str]] = defaultdict(list)
    for filepath in py_files:
        dir_files[os.path.dirname(filepath)].append(filepath)

    for dirpath, files in dir_files.items():
        prefix_groups = _extract_prefix_groups(files)

        for prefix, group_files in prefix_groups.items():
            if len(group_files) < min_files:
                continue

            # Check that no package directory already exists
            package_dir = os.path.join(dirpath, prefix)
            if os.path.isdir(package_dir) and os.path.isfile(
                os.path.join(package_dir, "__init__.py")
            ):
                continue

            group_modules = _resolve_group_modules(group_files, file_map)
            import_edges = _count_intra_group_imports(group_modules, import_graph)
            if import_edges == 0:
                continue

            findings.append(
                _build_package_candidate_finding(prefix, group_files, import_edges, project_root)
            )

    return findings


# ── STRUCT006: Cross-File Pattern Detection ──────────────────────────────


# Pattern fingerprint definitions
_PATTERN_CATALOG = {
    "config_loading": {
        "description": "Config loading pattern (path expansion + file read + error handling)",
        "required_calls": {"expanduser", "Path", "json.load", "yaml.load", "toml.load"},
        "required_structures": {"try_except"},
        "min_calls": 2,  # At least 2 of the required calls
    },
    "subprocess_wrapper": {
        "description": "Subprocess wrapper pattern (run + returncode/stdout check)",
        "required_calls": {
            "subprocess.run",
            "subprocess.check_output",
            "subprocess.call",
            "subprocess.check_call",
            "subprocess.Popen",
        },
        "required_structures": set(),
        "min_calls": 1,
    },
    "retry_loop": {
        "description": "Retry loop pattern (loop + try/except + counter/sleep)",
        "required_calls": {"time.sleep", "sleep"},
        "required_structures": {"for_try_except", "while_try_except"},
        "min_calls": 1,
    },
}


def check_cross_file_patterns(
    py_files: list[str],
    project_root: str,
    *,
    max_file_loc: int = 1000,
    max_files: int = 100,
) -> list[LintIssue]:
    """Detect repeated structural patterns across files.

    Algorithm:
    1. Per file, per function: compute pattern fingerprint
    2. Group by fingerprint
    3. Groups with ≥3 functions across ≥2 files → emit finding

    Performance: skip files >max_file_loc LOC, cap at max_files files.
    Emits STRUCT006 (informational, confidence 0.5).
    """
    findings: list[LintIssue] = []

    # Collect pattern matches: pattern_name → list of locations
    pattern_matches: dict[str, list[dict]] = defaultdict(list)

    files_analyzed = 0
    for filepath in py_files:
        if files_analyzed >= max_files:
            break

        # Skip large files
        try:
            with open(filepath, encoding="utf-8", errors="ignore") as f:
                source = f.read()
            if source.count("\n") > max_file_loc:
                continue
        except OSError:
            continue

        try:
            tree = ast.parse(source, filename=filepath)
        except SyntaxError:
            continue

        files_analyzed += 1
        rel_path = os.path.relpath(filepath, project_root)

        # Analyze each top-level function
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue

            func_patterns = _fingerprint_function(node)
            for pattern_name in func_patterns:
                pattern_matches[pattern_name].append(
                    {
                        "file": rel_path,
                        "function": node.name,
                        "line": node.lineno,
                    }
                )

    # Emit findings for patterns with ≥3 matches across ≥2 files
    for pattern_name, locations in pattern_matches.items():
        if len(locations) < 3:
            continue

        unique_files = {loc["file"] for loc in locations}
        if len(unique_files) < 2:
            continue

        catalog_entry = _PATTERN_CATALOG.get(pattern_name, {})
        description = (
            catalog_entry.get("description", pattern_name) if catalog_entry else pattern_name
        )

        findings.append(
            LintIssue(
                linter="structure_channel",
                kind="STRUCT006",
                message=(
                    f"Repeated pattern '{description}' found in {len(locations)} functions "
                    f"across {len(unique_files)} files. Consider extracting a shared utility."
                ),
                file=locations[0]["file"],
                severity="informational",
                confidence=0.5,
                evidence={
                    "code": "STRUCT006",
                    "pattern": pattern_name,
                    "locations": locations[:10],  # Cap for output size
                    "count": len(locations),
                    "file_count": len(unique_files),
                },
                suggestions=[
                    f"Extract the '{pattern_name}' pattern into a shared utility module",
                    "Reduces duplication and makes the pattern testable in one place",
                ],
            )
        )

    return findings


def _collect_structural_features(
    func: ast.FunctionDef | ast.AsyncFunctionDef,
) -> tuple[set[str], set[str]]:
    """Walk the AST of a function and collect call names and structural features.

    Returns (call_names, structures) where structures contains tags like
    'try_except', 'for_try_except', 'while_try_except'.
    """
    call_names: set[str] = set()
    structures: set[str] = set()

    for node in ast.walk(func):
        if isinstance(node, ast.Call):
            name = _get_call_name_simple(node)
            if name:
                call_names.add(name)

        if isinstance(node, ast.ExceptHandler):
            structures.add("try_except")

        if isinstance(node, (ast.For, ast.While)):
            for child in ast.walk(node):
                if isinstance(child, ast.ExceptHandler):
                    tag = "for_try_except" if isinstance(node, ast.For) else "while_try_except"
                    structures.add(tag)

    return call_names, structures


def _fingerprint_function(func: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    """Compute pattern fingerprints for a function.

    Returns list of matching pattern names from _PATTERN_CATALOG.
    """
    call_names, structures = _collect_structural_features(func)

    matches: list[str] = []
    for pattern_name, pattern_def in _PATTERN_CATALOG.items():
        req_calls = set(pattern_def["required_calls"])  # type: ignore[arg-type]
        matching_calls = call_names & req_calls
        min_calls: int = int(pattern_def["min_calls"])
        if len(matching_calls) < min_calls:
            continue
        req_structs = set(pattern_def["required_structures"])  # type: ignore[arg-type]
        if req_structs and not (structures & req_structs):
            continue
        matches.append(pattern_name)

    return matches


def _get_call_name_simple(node: ast.Call) -> str | None:
    """Extract a simple call name for pattern matching."""
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        if isinstance(node.func.value, ast.Name):
            return f"{node.func.value.id}.{node.func.attr}"
        return node.func.attr
    return None
