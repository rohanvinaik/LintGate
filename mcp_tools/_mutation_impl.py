"""Mutation tools implementation — helpers, context, and analysis logic.

Extracted from mutation_tools.py to stay under the 400-line file limit.
The public API is mutation_tools.register(); this module is private.
"""

from __future__ import annotations

import ast
import contextlib
import inspect
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_MUTATION_CACHE_DIR = ".lintgate/mutation"


def detect_delegation(file_path: str) -> str | None:
    """Detect if a file is a thin subprocess wrapper delegating to a script.

    Returns the delegated script path if detected, None otherwise.
    A file is a delegation wrapper if it:
    - Imports subprocess
    - Calls subprocess.run with a script path argument
    - Has no own branching logic beyond the wrapper pattern
    """
    try:
        with open(file_path, encoding="utf-8") as f:
            source = f.read()
        tree = ast.parse(source)
    except (OSError, SyntaxError):
        return None

    has_subprocess_import = False
    script_target: str | None = None
    own_function_count = 0

    for node in ast.walk(tree):
        # Check for subprocess import
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "subprocess":
                    has_subprocess_import = True
        elif isinstance(node, ast.ImportFrom):
            if node.module == "subprocess":
                has_subprocess_import = True

        # Count own function definitions (excluding __main__ guard)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            own_function_count += 1

        # Find subprocess.run calls with script path args
        elif isinstance(node, ast.Call):
            func = node.func
            is_subprocess_run = False
            if isinstance(func, ast.Attribute) and func.attr == "run":
                if isinstance(func.value, ast.Name) and func.value.id == "subprocess":
                    is_subprocess_run = True
            if is_subprocess_run and node.args:
                first_arg = node.args[0]
                # Look for ["python", ..., "scripts/X.py", ...] pattern
                if isinstance(first_arg, ast.List):
                    for elt in first_arg.elts:
                        if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                            val = elt.value
                            if "scripts/" in val and val.endswith(".py"):
                                script_target = val

    if not has_subprocess_import or script_target is None:
        return None

    # Heuristic: thin wrappers have few functions (mostly just the @mcp.tool wrappers)
    # and delegate all compute to the script
    return script_target


@dataclass
class DiscoveryDiagnostics:
    """Tracks why test discovery produced the results it did."""

    test_files_found: int = 0
    impact_map_refs: int = 0
    ast_test_callables: int = 0
    import_successes: int = 0
    import_failures: list[str] = field(default_factory=list)
    import_error_details: list[dict[str, str]] = field(default_factory=list)
    class_instantiation_failures: list[str] = field(default_factory=list)
    callables_loaded: int = 0
    fallback_used: bool = False
    weak_linkage_suspected: bool = False
    linkage_source: str = ""  # "dynamic", "static", "semantic", or "" (fallback)
    semantic_matches: int = 0
    semantic_available: bool = False
    semantic_scores: list[float] = field(default_factory=list)
    proven_linkage_count: int = 0
    static_refs_count: int = 0
    dynamic_refs_count: int = 0
    linkage_divergence: str = ""  # "aligned", "static_missing", "dynamic_missing", "disjoint"

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "test_files_found": self.test_files_found,
            "callables_loaded": self.callables_loaded,
        }
        if self.impact_map_refs:
            d["impact_map_refs"] = self.impact_map_refs
        if self.ast_test_callables:
            d["ast_test_callables"] = self.ast_test_callables
        if self.import_failures:
            d["import_failures"] = self.import_failures[:5]
        if self.import_error_details:
            d["import_error_details"] = self.import_error_details[:5]
        if self.class_instantiation_failures:
            d["class_instantiation_failures"] = self.class_instantiation_failures[:5]
        if self.linkage_source:
            d["linkage_source"] = self.linkage_source
        if self.semantic_matches:
            d["semantic_matches"] = self.semantic_matches
        if self.semantic_available:
            d["semantic_available"] = self.semantic_available
        if self.semantic_scores:
            d["semantic_scores"] = self.semantic_scores
        if self.fallback_used:
            d["fallback_used"] = True
        if self.proven_linkage_count:
            d["proven_linkage_count"] = self.proven_linkage_count
        if self.linkage_divergence:
            d["linkage_divergence"] = self.linkage_divergence
            d["static_refs_count"] = self.static_refs_count
            d["dynamic_refs_count"] = self.dynamic_refs_count
        if self.weak_linkage_suspected:
            d["weak_linkage_suspected"] = True
            d["sanity_warning"] = (
                "Callable loading appears incomplete for the discovered test files. "
                "Treat mutation survival as a discovery artifact."
            )
        if self.import_error_details:
            import sys as _sys

            d["interpreter_path"] = _sys.executable
        if self.callables_loaded == 0:
            reasons: list[str] = []
            if self.test_files_found == 0:
                reasons.append("no_test_files_discovered")
            elif self.import_failures and self.import_successes == 0:
                reasons.append("all_imports_failed")
            else:
                reasons.append("no_test_functions_found_in_modules")
            d["failure_reasons"] = reasons
        return d


@dataclass
class MutationContext:
    """Bundled context for mutation analysis runs."""

    full_path: str
    rel_path: str
    cache_dir: Path
    purity_map: dict[str, bool] = field(default_factory=dict)
    test_files: list[str] = field(default_factory=list)
    project_root: str = ""


# ── File/function resolution ──────────────────────────────────────


def _find_qualified_method(
    tree: ast.Module,
    qualified_name: str,
) -> tuple[ast.FunctionDef | ast.AsyncFunctionDef | None, str | None]:
    """Walk class hierarchy for a dotted name like 'Class.method'.

    Returns (func_node, error_message). One of them is always None.
    """
    parts = qualified_name.split(".")
    method_name = parts[-1]
    scope: ast.AST = tree
    for class_name in parts[:-1]:
        cls_match = next(
            (
                c
                for c in getattr(scope, "body", [])
                if isinstance(c, ast.ClassDef) and c.name == class_name
            ),
            None,
        )
        if cls_match is None:
            return None, f"Class '{class_name}' not found"
        scope = cls_match
    func_match = next(
        (
            c
            for c in getattr(scope, "body", [])
            if isinstance(c, (ast.FunctionDef, ast.AsyncFunctionDef)) and c.name == method_name
        ),
        None,
    )
    if func_match is not None:
        return func_match, None
    return None, f"Method '{method_name}' not found in class chain"


def _find_toplevel_function(
    tree: ast.Module,
    name: str,
) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    """Find a function/async function by name anywhere in the AST."""
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    return None


def resolve_function(
    project_root: str,
    file: str,
    function: str | None,
) -> tuple[str, ast.FunctionDef | ast.AsyncFunctionDef | None, str | None]:
    """Resolve file and optionally find a function node.

    Returns (full_path, func_node_or_None, error_or_None).
    """
    full = os.path.join(project_root, file) if not os.path.isabs(file) else file
    if not os.path.isfile(full):
        return full, None, f"File not found: {file}"

    try:
        with open(full, encoding="utf-8") as fh:
            tree = ast.parse(fh.read(), filename=full)
    except (OSError, SyntaxError) as e:
        return full, None, f"Parse error: {e}"

    if not function:
        return full, None, None

    if "." in function:
        node, err = _find_qualified_method(tree, function)
        if err:
            return full, None, f"{err} in {file}"
        setattr(node, "_lintgate_qualname", function)  # noqa: B010
        return full, node, None

    node = _find_toplevel_function(tree, function)
    if node is not None:
        for qualname, candidate in walk_functions(tree):
            if candidate is node:
                setattr(node, "_lintgate_qualname", qualname)  # noqa: B010
                break
        return full, node, None
    return full, None, f"Function '{function}' not found in {file}"


def walk_functions(tree: ast.Module) -> list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]]:
    """Walk AST yielding (qualname, node) for each function."""
    results: list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]] = []

    def _walk(scope: ast.AST, prefix: str) -> None:
        for node in getattr(scope, "body", []):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = f"{prefix}{node.name}" if prefix else node.name
                results.append((name, node))
            elif isinstance(node, ast.ClassDef):
                cp = f"{prefix}{node.name}." if prefix else f"{node.name}."
                _walk(node, cp)

    _walk(tree, "")
    return results


def parse_file(full_path: str) -> ast.Module | None:
    """Parse a Python file, returning None on error."""
    try:
        with open(full_path, encoding="utf-8") as fh:
            return ast.parse(fh.read(), filename=full_path)
    except (OSError, SyntaxError):
        return None


# ── Cache helpers ─────────────────────────────────────────────────


def get_cache_dir(project_root: str) -> Path:
    return Path(project_root) / _MUTATION_CACHE_DIR


def load_cached_state(cache_dir: Path, func_key: str) -> dict | None:
    safe_key = func_key.replace("::", "__").replace("/", "_")
    cache_file = cache_dir / f"{safe_key}.json"
    if not cache_file.exists():
        return None
    try:
        with open(cache_file, encoding="utf-8") as f:
            result: dict | None = json.load(f)
            return result
    except (OSError, json.JSONDecodeError):
        return None


def compute_test_hash(test_files: list[str]) -> str:
    """Compute a content hash of test files for cache invalidation."""
    import hashlib

    h = hashlib.sha256()
    for tf in sorted(test_files):
        try:
            with open(tf, "rb") as f:
                h.update(f.read())
        except OSError:
            continue
    return h.hexdigest()[:16]


def save_cached_state(
    cache_dir: Path,
    func_key: str,
    data: dict,
    *,
    test_files: list[str] | None = None,
) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    safe_key = func_key.replace("::", "__").replace("/", "_")
    cache_file = cache_dir / f"{safe_key}.json"
    if test_files:
        data["_test_content_hash"] = compute_test_hash(test_files)
    try:
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(data, f, default=str)
    except (OSError, TypeError):
        # Remove truncated file if write failed mid-stream
        cache_file.unlink(missing_ok=True)


def iter_cached_states(
    cache_dir: Path,
    file: str | None = None,
    function: str | None = None,
) -> list[dict]:
    """Load all cached mutation states, optionally filtered by file/function."""
    if not cache_dir.exists():
        return []
    states: list[dict] = []
    for cache_file in sorted(cache_dir.glob("*.json")):
        if cache_file.name == "scheduler_state.json":
            continue
        try:
            with open(cache_file, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        func_key = data.get("function_key", "")
        if file and file not in func_key:
            continue
        if function and function not in func_key:
            continue
        states.append(data)
    return states


# ── Test discovery + loading ──────────────────────────────────────


def discover_test_files(project_root: str, source_file: str) -> list[str]:
    """Find test files relevant to a source file.

    Uses multiple strategies:
    1. Exact match: test_{basename}.py
    2. Prefix variants: test_{basename}_*.py and {basename}_test*.py
    3. Package-aware: for files in subpackages like performance_checks/,
       also search for test_{package_name}*.py
    4. Recursive directory walk under tests/ and test/ (nested layouts)
    """
    raw_base = os.path.splitext(os.path.basename(source_file))[0]
    base_candidates = {raw_base}
    stripped_base = raw_base.lstrip("_")
    if stripped_base:
        base_candidates.add(stripped_base)
    test_dirs = ["tests", "test"]

    # Build candidate patterns: exact match + parent package match
    exact_candidates = {
        candidate
        for base in base_candidates
        for candidate in (f"test_{base}.py", f"{base}_test.py", f"test_char_{base}.py")
    }
    generated_candidate = _generated_test_candidate(project_root, source_file)
    if generated_candidate:
        exact_candidates.add(generated_candidate)

    # For files in subpackages, also try the parent package name
    # e.g., lintgate/linters/performance_checks/perf001.py → test_performance_checks*.py
    parent_dir = os.path.basename(os.path.dirname(source_file))
    package_candidates = []
    if parent_dir and parent_dir not in ("lintgate", "src", "lib"):
        package_candidates.append(f"test_{parent_dir}")

    found: list[str] = []
    seen: set[str] = set()
    for td in test_dirs:
        test_dir = os.path.join(project_root, td)
        if not os.path.isdir(test_dir):
            continue
        for dirpath, dirnames, filenames in os.walk(test_dir):
            dirnames[:] = [d for d in dirnames if not d.startswith(".") and d != "__pycache__"]
            for entry in filenames:
                if not entry.endswith(".py"):
                    continue
                match = (
                    entry in exact_candidates
                    or any(entry.startswith(f"test_{base}_") for base in base_candidates)
                    or any(entry.startswith(f"{base}_test") for base in base_candidates)
                    or any(entry.startswith(f"test_char_{base}_") for base in base_candidates)
                    or any(
                        entry.startswith(prefix) and entry.endswith(".py")
                        for prefix in package_candidates
                    )
                )

                if match:
                    full = os.path.join(dirpath, entry)
                    if full not in seen:
                        found.append(full)
                        seen.add(full)
    return found


def _generated_test_candidate(project_root: str, source_file: str) -> str | None:
    """Compute the path-safe generated test basename for a source file."""
    try:
        rel = os.path.relpath(source_file, project_root)
    except ValueError:
        return None
    if rel.startswith(".."):
        return None
    stem = rel.replace(os.sep, "/")
    if stem.endswith(".py"):
        stem = stem[:-3]
    safe = stem.replace("/", "_").replace(".", "_")
    return f"test_{safe}.py"


def load_test_callables(
    test_files: list[str],
    func_name: str,
    *,
    project_root: str = "",
    func_key: str = "",
    source_file: str = "",
) -> tuple[list[Any], DiscoveryDiagnostics]:
    """Discover and import test callables for a function.

    Resolution order (highest to lowest confidence):
    0. Proven kill-set linkage (tests that killed mutants — ground truth)
    1. Dynamic coverage linkage (execution-traced, from .coverage contexts)
    2. Static AST-based impact map (call-name matching)
    3. Fallback: all test functions from discovered test files

    Falls back to loading all test functions from the discovered test files
    when neither dynamic nor static mapping finds references to func_name.
    This handles indirect calls (fixtures, parametrize, helper wrappers)
    that static name matching misses.

    Returns (callables, diagnostics) — diagnostics explain why discovery
    produced the result it did, especially when callables is empty.
    """
    from lintgate.specification.test_impact import build_test_impact_map

    diag = DiscoveryDiagnostics(test_files_found=len(test_files))
    diag.ast_test_callables = _count_ast_test_callables(test_files)

    # ── Layer 0: proven kill-set linkage (ground truth) ────────────
    proven_imported: list[Any] = []
    if project_root and func_key:
        proven_refs = _resolve_proven_linkage(project_root, func_key)
        if proven_refs:
            proven_imported = _import_test_functions(proven_refs, diag)
            if proven_imported:
                diag.proven_linkage_count = len(proven_imported)

    # ── Layer 1: dynamic coverage linkage (highest confidence) ─────
    dynamic_refs: list[Any] = []
    if project_root and func_key:
        dynamic_refs = _resolve_dynamic_linkage(project_root, func_key)
    diag.dynamic_refs_count = len(dynamic_refs)

    # ── Layer 2: static AST-based impact map ───────────────────────
    impact = build_test_impact_map(test_files)
    static_refs = impact.tests_for(func_name)
    diag.static_refs_count = len(static_refs) if static_refs else 0

    # Divergence diagnostic: when both layers ran, compare.
    _classify_linkage_divergence(diag, dynamic_refs, static_refs, project_root)

    if dynamic_refs:
        diag.impact_map_refs = len(dynamic_refs)
        diag.linkage_source = "dynamic"
        imported = _import_test_functions(dynamic_refs, diag)
        merged = _merge_callables(proven_imported, imported)
        if merged:
            diag.callables_loaded = len(merged)
            diag.weak_linkage_suspected = _suspect_weak_linkage(diag)
            return merged, diag

    if static_refs:
        diag.impact_map_refs = len(static_refs)
        imported = _import_test_functions(static_refs, diag)
        merged = _merge_callables(proven_imported, imported)
        if merged:
            diag.callables_loaded = len(merged)
            diag.weak_linkage_suspected = _suspect_weak_linkage(diag)
            return merged, diag

    # ── Layer 1.5: semantic discovery fallback ────────────────────
    # Consulted only when both dynamic (Layer 1) and static (Layer 2)
    # yield zero linkage for this function. Discovers additional test
    # files via TF-IDF fingerprint similarity, loads their callables,
    # and tags the linkage as low-confidence "semantic".
    if project_root and source_file:
        semantic_files = _discover_semantic_fallback(project_root, source_file, test_files, diag)
        if semantic_files:
            diag.linkage_source = "semantic"
            sem_callables = _load_all_tests_from_files(semantic_files, diag)
            merged = _merge_callables(proven_imported, sem_callables)
            if merged:
                diag.callables_loaded = len(merged)
                diag.weak_linkage_suspected = _suspect_weak_linkage(diag)
                return merged, diag

    # ── Layer 3: fallback — all test functions from discovered files ─
    # The test files were already scoped by filename convention in
    # discover_test_files, so this is bounded and relevant. Proven
    # linkage is still merged so kill-proven tests always run.
    diag.fallback_used = True
    callables = _load_all_tests_from_files(test_files, diag)

    # ── Layer 3.5: in-process runtime probe (authoritative filter) ─
    # When we've fallen all the way through, the fallback set is noisy
    # (every test in every filename-matched file). A sys.setprofile probe
    # narrows this to tests that actually enter the target's code object.
    # Ground truth by construction; bounded by max_probe.
    probed_count = _probe_fallback_with_runtime(source_file, func_name, callables, diag)
    if probed_count:
        diag.linkage_source = "probe"

    merged = _merge_callables(proven_imported, callables)
    diag.callables_loaded = len(merged)
    diag.weak_linkage_suspected = _suspect_weak_linkage(diag)
    return merged, diag


def _probe_fallback_with_runtime(
    source_file: str,
    func_name: str,
    callables: list[Any],
    diag: DiscoveryDiagnostics,
) -> int:
    """Filter *callables* in-place to runtime-probe-verified entries.

    Returns the number of callables probed. Mutates *callables* in-place
    when the probe produced a narrower set than the input — otherwise
    leaves it untouched. Safe fallback when the target can't be resolved.
    """
    if not source_file or not func_name or not callables:
        return 0
    try:
        from lintgate.specification.linkage_probe import probe_fallback_callables
    except Exception:
        return 0
    try:
        verified, probed = probe_fallback_callables(source_file, func_name, callables)
    except Exception:
        return 0
    if probed and len(verified) < len(callables):
        callables[:] = verified
    return probed


def _persist_proven_linkage(
    project_root: str,
    func_key: str,
    tests: list[Any],
    result_dict: dict[str, Any],
) -> None:
    """Record tests that killed mutants as proven linkage for *func_key*.

    No-op when project_root or func_key are missing, or when no mutants
    were killed. Otherwise appends to .lintgate/mutation/linkage_proven.json
    so future discovery layers can consult this as ground truth.
    """
    if not project_root or not func_key:
        return
    killed_records = result_dict.get("killed_records") or []
    if not killed_records:
        return

    from lintgate.specification.proven_linkage import killed_pairs_from_result, record_kills

    name_to_file: dict[str, str] = {}
    for fn in tests:
        underlying = getattr(fn, "__func__", fn)
        try:
            path = inspect.getsourcefile(underlying) or inspect.getfile(underlying)
        except TypeError:
            path = ""
        if not path:
            continue
        name = getattr(underlying, "__name__", "")
        qualname = getattr(underlying, "__qualname__", name)
        if name:
            name_to_file[name] = path
        if qualname and qualname != name:
            name_to_file[qualname] = path

    pairs = killed_pairs_from_result(killed_records, name_to_file)
    if pairs:
        record_kills(project_root, func_key, pairs)


def _resolve_proven_linkage(project_root: str, func_key: str) -> list[Any]:
    """Load kill-proven TestReference entries for *func_key*.

    Returns [] when the proven-linkage cache is missing or has no
    entries for this function. Proven entries are the strongest
    linkage signal — execution that changed outcome is definitional.
    """
    from lintgate.specification.proven_linkage import load_proven_entries
    from lintgate.specification.test_impact import TestReference

    entries = load_proven_entries(project_root, func_key)
    return [
        TestReference(test_file=e.test_file, test_function=e.test_function) for e in entries
    ]


def _merge_callables(first: list[Any], second: list[Any]) -> list[Any]:
    """Concatenate callable lists, deduping by (module, qualname)."""
    if not first:
        return list(second)
    if not second:
        return list(first)
    seen: set[tuple[str, str]] = set()
    merged: list[Any] = []
    for fn in list(first) + list(second):
        underlying = getattr(fn, "__func__", fn)
        key = (
            getattr(underlying, "__module__", ""),
            getattr(underlying, "__qualname__", getattr(underlying, "__name__", "")),
        )
        if key in seen:
            continue
        seen.add(key)
        merged.append(fn)
    return merged


def _classify_linkage_divergence(
    diag: DiscoveryDiagnostics,
    dynamic_refs: list[Any],
    static_refs: list[Any] | None,
    project_root: str,
) -> None:
    """Compare static and dynamic linkage; record divergence for diagnostics.

    Only meaningful when both layers could run — otherwise this stays
    silent. Divergence signals: "aligned" (sets overlap substantially),
    "static_missing" (dynamic found refs static didn't), "dynamic_missing"
    (the reverse), "disjoint" (both found refs but no overlap).
    """
    static_refs = static_refs or []
    if not dynamic_refs and not static_refs:
        return
    if not dynamic_refs or not static_refs:
        # Only one layer produced refs — no divergence to classify.
        return

    def _key(ref: Any) -> tuple[str, str]:
        tf = getattr(ref, "test_file", "") or ""
        if tf and project_root and os.path.isabs(tf):
            with contextlib.suppress(ValueError):
                tf = os.path.relpath(tf, project_root)
        return (tf, getattr(ref, "test_function", "") or "")

    dyn_keys = {_key(r) for r in dynamic_refs}
    stat_keys = {_key(r) for r in static_refs}
    overlap = dyn_keys & stat_keys
    if not overlap:
        diag.linkage_divergence = "disjoint"
    elif dyn_keys - stat_keys and not (stat_keys - dyn_keys):
        diag.linkage_divergence = "static_missing"
    elif stat_keys - dyn_keys and not (dyn_keys - stat_keys):
        diag.linkage_divergence = "dynamic_missing"
    elif overlap == dyn_keys == stat_keys:
        diag.linkage_divergence = "aligned"
    else:
        diag.linkage_divergence = "partial_overlap"


def _resolve_dynamic_linkage(
    project_root: str,
    func_key: str,
) -> list[Any]:
    """Resolve dynamic linkage for a function, building the cache if needed.

    Returns a list of TestReference-compatible objects if dynamic
    linkage exists, empty list otherwise.  Uses build_or_load_linkage()
    so the cache is auto-built from .coverage when missing or stale.
    """
    from lintgate.specification.dynamic_coverage import build_or_load_linkage
    from lintgate.specification.test_impact import TestReference

    dlm = build_or_load_linkage(project_root)
    if not dlm.linkages:
        return []

    entries = dlm.tests_for(func_key)
    if not entries:
        return []

    # Convert LinkageEntry to TestReference for _import_test_functions.
    # Resolve relative test_file paths to absolute for module import.
    # Strip module prefix from test_function names — coverage.py contexts
    # use "module.Class.method" but _resolve_test_reference expects
    # "Class.method" relative to the imported module.
    refs: list[TestReference] = []
    for entry in entries:
        if entry.confidence in ("dynamic", "hybrid"):
            test_file = entry.test_file
            if test_file and not os.path.isabs(test_file):
                test_file = os.path.join(project_root, test_file)
            test_func = _strip_module_prefix(entry.test_function, test_file)
            refs.append(
                TestReference(
                    test_file=test_file,
                    test_function=test_func,
                )
            )
    return refs


def _strip_module_prefix(test_function: str, test_file: str) -> str:
    """Strip module name prefix from a coverage.py context function name.

    Coverage.py dynamic contexts produce names like:
      "test_controlplane_runtime.TestClass.test_method"
    but _resolve_test_reference needs:
      "TestClass.test_method"

    Derives the module name from the test file path and strips it.
    """
    if not test_file or "." not in test_function:
        return test_function
    module_name = os.path.basename(test_file).removesuffix(".py")
    prefix = module_name + "."
    if test_function.startswith(prefix):
        return test_function[len(prefix) :]
    return test_function


def _count_ast_test_callables(test_files: list[str]) -> int:
    """Count test_* callables visible in AST across the discovered test files."""
    total = 0
    for tf in test_files:
        try:
            with open(tf, encoding="utf-8") as f:
                tree = ast.parse(f.read(), filename=tf)
        except (OSError, SyntaxError):
            continue
        for node in getattr(tree, "body", []):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith(
                "test_"
            ):
                total += 1
            elif isinstance(node, ast.ClassDef):
                for child in node.body:
                    if isinstance(
                        child, (ast.FunctionDef, ast.AsyncFunctionDef)
                    ) and child.name.startswith("test_"):
                        total += 1
    return total


def _suspect_weak_linkage(diag: DiscoveryDiagnostics) -> bool:
    """Detect implausibly small callable sets relative to discovered test bodies.

    Three cases — any one triggers:
      1. Zero-linkage: fallback reached, no impact-map refs, yet tests exist.
         Means static + dynamic both failed to link any test to this function.
      2. Near-empty: 3+ tests in the file but 1 or fewer loaded via fallback.
      3. Ratio: 8+ tests but fewer than a quarter loaded.
    """
    if not diag.fallback_used or diag.callables_loaded <= 0 or diag.ast_test_callables <= 0:
        return False
    # Zero-linkage: tests were scanned but nothing linked to this function.
    # Survival under this condition is a discovery artifact, not a test gap.
    if diag.impact_map_refs == 0:
        return True
    if diag.ast_test_callables >= 3 and diag.callables_loaded <= 1:
        return True
    return diag.ast_test_callables >= 8 and diag.callables_loaded * 4 < diag.ast_test_callables


def _discover_semantic_fallback(
    project_root: str,
    source_file: str,
    already_found: list[str],
    diag: DiscoveryDiagnostics,
) -> list[str]:
    """Try semantic discovery when dynamic + static linkage both fail.

    Returns additional test files found via TF-IDF fingerprint similarity,
    excluding any already in the discovered set. Updates diag with
    semantic_available/semantic_matches/semantic_scores.
    """
    try:
        from lintgate.specification.semantic_discovery import discover_semantic_test_files

        diag.semantic_available = True
    except Exception:
        return []

    try:
        results = discover_semantic_test_files(project_root, source_file)
    except Exception:
        return []

    if not results:
        return []

    seen = set(already_found)
    new_files: list[str] = []
    scores: list[float] = []
    for tf, score in results:
        if tf not in seen:
            new_files.append(tf)
            scores.append(score)
            seen.add(tf)

    diag.semantic_matches = len(new_files)
    diag.semantic_scores = scores
    return new_files


def _extract_test_callables_from_module(
    mod: Any,
    tf: str,
    diag: DiscoveryDiagnostics | None,
) -> list[Any]:
    """Extract test functions and class-based test methods from a loaded module."""
    callables: list[Any] = []
    for name in dir(mod):
        obj = getattr(mod, name, None)
        if obj is None:
            continue
        if name.startswith("test_") and callable(obj):
            callables.append(obj)
        elif isinstance(obj, type) and getattr(obj, "__module__", None) == getattr(
            mod, "__name__", ""
        ):
            callables.extend(_extract_class_test_methods(obj, name, tf, diag))
    return callables


def _extract_class_test_methods(
    cls: type,
    cls_name: str,
    tf: str,
    diag: DiscoveryDiagnostics | None,
) -> list[Any]:
    """Extract test_* bound methods from a test class via fresh instance."""
    method_names = [m for m in dir(cls) if m.startswith("test_")]
    if not method_names:
        return []
    try:
        instance = cls()
    except Exception:
        if diag is not None:
            diag.class_instantiation_failures.append(f"{cls_name} in {os.path.basename(tf)}")
        return []
    return [m for name in method_names if callable(m := getattr(instance, name, None))]


def _load_all_tests_from_files(
    test_files: list[str],
    diag: DiscoveryDiagnostics | None = None,
) -> list[Any]:
    """Import all test_ functions from the given test files.

    Handles both module-level test functions and class-based test methods
    (e.g., class TestFoo with test_bar methods). For class methods, yields
    bound methods on fresh instances so they can be called zero-arg.
    """
    callables: list[Any] = []
    for tf in test_files:
        mod, err_detail = _try_import_module(tf)
        if mod is None:
            if diag is not None:
                basename = os.path.basename(tf)
                diag.import_failures.append(basename)
                if err_detail:
                    diag.import_error_details.append({"file": basename, "error": err_detail})
            continue
        if diag is not None:
            diag.import_successes += 1
        callables.extend(_extract_test_callables_from_module(mod, tf, diag))
    return callables


def _import_test_functions(
    refs: list[Any],
    diag: DiscoveryDiagnostics | None = None,
) -> list[Any]:
    """Import test functions from TestReference objects.

    Handles both module-level test functions and class-based test methods.
    For class methods, searches Test* classes for the method name and yields
    a bound method on a fresh instance.
    """
    callables: list[Any] = []
    loaded_modules: dict[str, Any] = {}
    for ref in refs:
        if ref.test_file not in loaded_modules:
            mod, err_detail = _try_import_module(ref.test_file)
            loaded_modules[ref.test_file] = mod
            if mod is None and diag is not None:
                basename = os.path.basename(ref.test_file)
                diag.import_failures.append(basename)
                if err_detail:
                    diag.import_error_details.append({"file": basename, "error": err_detail})
            elif mod is not None and diag is not None:
                diag.import_successes += 1
        mod = loaded_modules[ref.test_file]
        if mod is None:
            continue
        found = _resolve_test_reference(mod, ref.test_function)
        if found is not None:
            callables.append(found)
    return callables


def _resolve_test_reference(mod: Any, test_function: str) -> Any:
    """Resolve a test reference, preserving class-qualified method identity."""
    test_fn = getattr(mod, test_function, None)
    if test_fn is not None and callable(test_fn):
        return test_fn
    if "." in test_function:
        return _find_qualified_test_method(mod, test_function)
    return _find_method_in_test_classes(mod, test_function)


def _find_qualified_test_method(mod: Any, qualified_name: str) -> Any:
    """Resolve Class.method or Nested.Class.method against a test module."""
    parts = qualified_name.split(".")
    scope: Any = mod
    for class_name in parts[:-1]:
        scope = getattr(scope, class_name, None)
        if not isinstance(scope, type):
            return None
    method_name = parts[-1]
    try:
        instance = scope()
    except Exception:
        return None
    method = getattr(instance, method_name, None)
    return method if callable(method) else None


def _find_method_in_test_classes(mod: Any, method_name: str) -> Any:
    """Search Test* classes in a module for a method by name.

    Returns a bound method on a fresh instance, or None.
    """
    for name in dir(mod):
        cls = getattr(mod, name, None)
        if not isinstance(cls, type):
            continue
        if getattr(cls, "__module__", None) != getattr(mod, "__name__", ""):
            continue
        if hasattr(cls, method_name):
            try:
                instance = cls()
                method = getattr(instance, method_name, None)
                if callable(method):
                    return method
            except Exception:
                continue
    return None


def _try_import_module(filepath: str) -> tuple[Any, str]:
    """Try to dynamically import a Python file as a module.

    Establishes project import context by adding the project root
    (directory containing tests/) to sys.path before import.

    Returns (module, error_detail) — module is None on failure,
    error_detail is a short string describing why (empty on success).
    """
    import importlib.util
    import sys

    # Find project root: walk up from filepath to find a directory containing
    # a tests/ dir or pyproject.toml, ensuring package imports resolve.
    project_root = _infer_project_root(filepath)
    path_added = False
    if project_root and project_root not in sys.path:
        sys.path.insert(0, project_root)
        path_added = True
    # Support src-layout projects (e.g. src/prism/) where the package
    # isn't importable from the project root alone.
    if project_root:
        src_dir = os.path.join(project_root, "src")
        if os.path.isdir(src_dir) and src_dir not in sys.path:
            sys.path.insert(0, src_dir)

    mod_name = f"_mutation_test_{os.path.basename(filepath).replace('.py', '')}"
    spec = importlib.util.spec_from_file_location(mod_name, filepath)
    if spec is None or spec.loader is None:
        if path_added and project_root is not None:
            sys.path.remove(project_root)
        return None, "spec_from_file_location returned None"
    try:
        mod = importlib.util.module_from_spec(spec)
        # Register in sys.modules BEFORE exec so dataclass decorators,
        # __module__ introspection, and intra-test imports resolve.
        sys.modules[mod_name] = mod
        spec.loader.exec_module(mod)
        return mod, ""
    except Exception as exc:
        sys.modules.pop(mod_name, None)
        detail = f"{type(exc).__name__}: {str(exc)[:200]} (interpreter: {sys.executable})"
        return None, detail
    finally:
        # Leave project_root in sys.path — other test modules may need it
        pass


def _infer_project_root(filepath: str) -> str | None:
    """Walk up from filepath to find project root (contains pyproject.toml or tests/)."""
    current = os.path.dirname(os.path.abspath(filepath))
    for _ in range(10):  # max 10 levels up
        if os.path.exists(os.path.join(current, "pyproject.toml")):
            return current
        if os.path.isdir(os.path.join(current, "tests")):
            return current
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    return None


# ── Purity detection ──────────────────────────────────────────────


def detect_purity(full_path: str, func_name: str) -> bool:
    """Fast purity check for a single function using the algebra pipeline."""
    try:
        from lintgate.linters.performance_checks.purity import analyze_purity

        tree = parse_file(full_path)
        if tree is None:
            return False
        purity_map = analyze_purity(tree)
        for key, result in purity_map.items():
            if key == func_name or key.endswith(f".{func_name}"):
                return result.is_pure
    except Exception:
        pass
    return False


def detect_purity_map(full_path: str) -> dict[str, bool]:
    """Build function_name -> is_pure map for all functions in a file."""
    try:
        from lintgate.linters.performance_checks.purity import analyze_purity

        tree = parse_file(full_path)
        if tree is None:
            return {}
        purity_map = analyze_purity(tree)
        return {k: v.is_pure for k, v in purity_map.items()}
    except Exception:
        return {}


def lookup_purity(purity_map: dict[str, bool], func_name: str) -> bool:
    """Look up purity for a function, trying both bare and qualified names."""
    if func_name in purity_map:
        return purity_map[func_name]
    bare = func_name.split(".")[-1]
    for key, is_pure in purity_map.items():
        if key == bare or key.endswith(f".{bare}"):
            return is_pure
    return False


# ── Mutation execution ────────────────────────────────────────────


def run_on_functions_with_tests(
    ctx: MutationContext,
    func_node: Any,
    function: str | None,
    runner: Any,
    filter_fn: Any,
    key_fn: Any,
) -> list[dict] | str:
    """Run mutation analysis with real test selection and purity-aware filtering."""
    results: list[dict] = []
    if func_node and function:
        # Explicit function target — always profile, skip triviality filter
        results.append(
            _run_single(ctx, func_node, function, runner, filter_fn, key_fn, skip_triviality=True)
        )
    else:
        tree = parse_file(ctx.full_path)
        if tree is None:
            return json.dumps({"error": f"Parse error: {ctx.full_path}"})
        for qualname, node in walk_functions(tree):
            results.append(_run_single(ctx, node, qualname, runner, filter_fn, key_fn))
    return results


def _run_single(
    ctx: MutationContext,
    node: Any,
    func_name: str,
    runner: Any,
    filter_fn: Any,
    key_fn: Any,
    *,
    skip_triviality: bool = False,
) -> dict:
    """Run mutation analysis on a single function with tests and purity."""
    qualname = getattr(node, "_lintgate_qualname", func_name)
    func_key = key_fn(ctx.rel_path, qualname)

    # ── Triviality pre-filter: skip profiling for structurally trivial functions ─
    # Disabled when a specific function is explicitly targeted (skip_triviality=True).
    triviality = _classify_triviality(node, qualname)
    if not skip_triviality and triviality != "nontrivial":
        result_dict = _trivial_result(func_key, node, triviality)
        save_cached_state(ctx.cache_dir, func_key, result_dict)
        return result_dict

    is_pure = lookup_purity(ctx.purity_map, qualname)
    cats = filter_fn(node, is_pure=is_pure)
    bare_name = qualname.split(".")[-1]
    tests, discovery_diag = load_test_callables(
        ctx.test_files,
        bare_name,
        project_root=ctx.project_root,
        func_key=func_key,
        source_file=ctx.full_path,
    )
    sr = runner(node, func_key, cats, tests, lambda *_a: None)
    result_dict = sr.to_dict()
    result_dict["tests_loaded"] = len(tests)
    result_dict["is_pure"] = is_pure
    result_dict["parameter_count"] = len(getattr(node, "args", _EMPTY_ARGS).args)

    _enrich_mutation_result(result_dict, node, tests, discovery_diag)

    _persist_proven_linkage(ctx.project_root, func_key, tests, result_dict)

    save_cached_state(ctx.cache_dir, func_key, result_dict, test_files=ctx.test_files)
    ret: dict = result_dict
    return ret


def _classify_triviality(node: Any, qualname: str) -> str:
    """Classify function triviality, returning the TrivialityClass value string."""
    from lintgate.specification.triviality_filter import classify_triviality

    return classify_triviality(node, function_name=qualname).value


def _trivial_result(func_key: str, node: Any, triviality_class: str) -> dict:
    """Build a synthetic mutation result for a trivial function.

    Trivial functions are tagged as EQUIVALENT_OR_UNINTERESTING without
    running any mutations — they would only produce equivalent mutants.
    """
    return {
        "function_key": func_key,
        "categories_tested": 0,
        "total_mutants": 0,
        "total_killed": 0,
        "total_survived": 0,
        "survival_rate": 0.0,
        "coverage_depth": "skipped",
        "budget_exhausted": False,
        "elapsed_ms": 0.0,
        "per_category": [],
        "tests_loaded": 0,
        "is_pure": False,
        "parameter_count": len(getattr(node, "args", _EMPTY_ARGS).args),
        "kill_rate": 1.0,
        "discovery_state": "SKIPPED_TRIVIAL",
        "topology_state": "TOPOLOGY_UNKNOWN",
        "survival_interpretation": "EQUIVALENT_OR_UNINTERESTING",
        "last_updated": int(time.time()),
        "mutation_truth_label": "EQUIVALENT_OR_UNINTERESTING",
        "discovery_failed": False,
        "triviality_class": triviality_class,
    }


class _EmptyArgs:
    args: list = []  # noqa: RUF012


_EMPTY_ARGS = _EmptyArgs()


def _enrich_mutation_result(
    result_dict: dict[str, Any],
    node: Any,
    tests: list[Any],
    discovery_diag: DiscoveryDiagnostics,
) -> None:
    """Attach topology/truth metadata required by downstream workflow logic."""
    from lintgate.specification.test_topology import (
        TopologyState,
        analyze_topology,
        classify_discovery_state,
        interpret_survival,
    )

    linked_test_files = _test_files_for_callables(tests)
    topology = analyze_topology(node, linked_test_files) if tests else None

    discovery_state = classify_discovery_state(
        test_files_found=discovery_diag.test_files_found,
        callables_loaded=discovery_diag.callables_loaded,
        import_failures=len(discovery_diag.import_failures),
        fallback_used=discovery_diag.fallback_used,
        weak_linkage_suspected=discovery_diag.weak_linkage_suspected,
        total_killed=result_dict.get("total_killed", 0),
        linkage_source=discovery_diag.linkage_source,
    )
    topology_state = topology.topology_state if topology else TopologyState.TOPOLOGY_UNKNOWN
    survival_rate = float(result_dict.get("survival_rate", 1.0))
    survival_interp = interpret_survival(
        discovery_state,
        topology_state,
        survival_rate,
        weak_linkage_suspected=discovery_diag.weak_linkage_suspected,
    )

    result_dict["kill_rate"] = round(1.0 - survival_rate, 3)
    result_dict["discovery_state"] = discovery_state.value
    result_dict["topology_state"] = topology_state.value
    result_dict["survival_interpretation"] = survival_interp.value
    result_dict["last_updated"] = int(time.time())
    result_dict["mutation_truth_label"] = _truth_label(
        result_dict,
        survival_interp.value,
    )
    result_dict["discovery_failed"] = survival_interp.value == "DISCOVERY_ARTIFACT"
    result_dict["discovery_diagnostics"] = discovery_diag.to_dict()
    if discovery_diag.weak_linkage_suspected:
        result_dict["discovery_artifact_reason"] = (
            "fallback callable loading captured too few tests for the discovered files"
        )
    if topology is not None:
        result_dict["topology_details"] = topology.to_dict()

    # Classify side-effect-only survivors as equivalent
    _mark_side_effect_equivalent(result_dict, node)

    # Flag property violation candidates on pure functions
    if result_dict.get("is_pure") and result_dict.get("discovery_diagnostics"):
        _flag_property_violation_candidates(result_dict)


# Known side-effect-only function names — mutations in their arguments
# don't affect return values or state.
_SIDE_EFFECT_SINKS = frozenset({
    "print", "pprint", "logging", "logger",
    "debug", "info", "warning", "error", "critical", "exception",
    "warn",
})


def _mark_side_effect_equivalent(
    result_dict: dict[str, Any],
    node: Any,
) -> None:
    """Mark surviving mutants in side-effect-only positions as equivalent."""
    survivors = result_dict.get("survivor_records", [])
    if not survivors or node is None:
        return

    # Build a set of line numbers that are inside side-effect-only calls
    side_effect_lines: set[int] = set()
    for child in ast.walk(node):
        if not isinstance(child, ast.Expr):
            continue
        call = child.value
        if not isinstance(call, ast.Call):
            continue
        func = call.func
        func_name = ""
        if isinstance(func, ast.Name):
            func_name = func.id
        elif isinstance(func, ast.Attribute):
            func_name = func.attr
            # Also check the object: logging.info, logger.debug, etc.
            if isinstance(func.value, ast.Name) and func.value.id in _SIDE_EFFECT_SINKS:
                func_name = func.value.id
        if func_name in _SIDE_EFFECT_SINKS:
            for line in range(child.lineno, (child.end_lineno or child.lineno) + 1):
                side_effect_lines.add(line)

    if not side_effect_lines:
        return

    equivalent_count = 0
    for survivor in survivors:
        diff = survivor.get("diff_summary", "")
        # Extract line number from diff: "- line N:" or lineno field
        lineno = survivor.get("lineno", 0)
        if not lineno and diff:
            # Try to extract from "line N" pattern in diff
            import re

            m = re.search(r"line\s+(\d+)", diff)
            if m:
                lineno = int(m.group(1))
        if lineno and lineno in side_effect_lines:
            survivor["equivalent_reason"] = "side_effect_only"
            equivalent_count += 1

    if equivalent_count:
        result_dict["side_effect_equivalent_count"] = equivalent_count


_PROPERTY_TEST_PATTERNS = {"property", "hypothesis", "invariant", "monoton", "idempoten"}


def _flag_property_violation_candidates(result_dict: dict[str, Any]) -> None:
    """Flag when a property-style test fails on a pure function.

    This might indicate a real code bug rather than a wrong test oracle.
    """
    diag = result_dict.get("discovery_diagnostics", {})
    # Check if any test function names suggest property-based testing
    failures = diag.get("import_error_details", [])
    # Also check survivor records for test-name patterns
    survivors = result_dict.get("survivor_records", [])
    for survivor in survivors:
        test_name = survivor.get("killing_test", "") or survivor.get("test_name", "")
        if any(pat in test_name.lower() for pat in _PROPERTY_TEST_PATTERNS):
            survivor["property_violation_candidate"] = True
            result_dict.setdefault("property_violation_candidates", []).append(
                survivor.get("category", "unknown")
            )
    # Also check for @given decorator usage in test files
    test_files = diag.get("test_files", [])
    if not test_files and not failures:
        return


def _truth_label(result_dict: dict[str, Any], survival_interpretation: str) -> str:
    """Normalize the top-level mutation truth label for downstream routing."""
    if result_dict.get("budget_exhausted"):
        return "BUDGET_INSTABILITY"
    if survival_interpretation == "DISCOVERY_ARTIFACT":
        return "DISCOVERY_ARTIFACT"
    if survival_interpretation == "MOCK_BOUNDARY_ARTIFACT":
        return "MOCK_BOUNDARY_ARTIFACT"
    if result_dict.get("total_mutants", 0) <= 0:
        return "EQUIVALENT_OR_UNINTERESTING"
    return "MEANINGFUL"


def _test_files_for_callables(test_callables: list[Any]) -> list[str]:
    """Map loaded test callables back to the concrete test files that were used."""
    files: list[str] = []
    seen: set[str] = set()
    for fn in test_callables:
        underlying = getattr(fn, "__func__", fn)
        path = inspect.getsourcefile(underlying) or inspect.getfile(underlying)
        if not path or path in seen:
            continue
        seen.add(path)
        files.append(path)
    return files


# ── Post-profiling analysis ───────────────────────────────────────


def run_post_profiling_analysis(
    results: list[dict],
    purity_map: dict[str, bool],
) -> dict:
    """Run convergence and symmetry analysis on profiling results.

    sigma is approximated by total_mutants (the empirical mutation population).
    This is an upper bound on true σ — convergence/symmetry analysis uses it
    to measure kill coverage against the generated mutant set.
    """
    from lintgate.specification.greedy_convergence import analyze_convergence
    from lintgate.specification.symmetry_classifier import classify_regime_from_mutations

    convergence_results: list[dict] = []
    symmetry_results: list[dict] = []

    for result_dict in results:
        pr = reconstruct_profiling_result(result_dict)
        if pr is None:
            continue

        # total_mutants as empirical sigma (upper bound on true σ)
        sigma = result_dict.get("total_mutants", 0)
        is_pure = result_dict.get("is_pure", False)
        param_count = result_dict.get("parameter_count", 0)

        conv = analyze_convergence(pr, sigma)
        convergence_results.append(conv.to_dict())

        sym = classify_regime_from_mutations(pr, sigma, is_pure, param_count)
        symmetry_results.append(sym.to_dict())

    return {"convergence": convergence_results, "symmetry": symmetry_results}


def reconstruct_profiling_result(result_dict: dict) -> Any:
    """Reconstruct a ProfilingResult from cached dict for analysis."""
    from Wesker.engine import (
        CategoryResult,
        MutationCategory,
        ProfilingResult,
    )

    if result_dict.get("coverage_depth") != "profiled":
        return None

    per_cat = []
    for cd in result_dict.get("per_category", []):
        try:
            cat = MutationCategory(cd["category"])
        except (ValueError, KeyError):
            continue
        per_cat.append(
            CategoryResult(
                category=cat,
                total=cd.get("total", 0),
                killed=cd.get("killed", 0),
                survived=cd.get("survived", 0),
                killed_by_assertion=cd.get("killed_by_assertion", 0),
                killed_by_crash=cd.get("killed_by_crash", 0),
            )
        )

    return ProfilingResult(
        function_key=result_dict.get("function_key", ""),
        categories_tested=result_dict.get("categories_tested", 0),
        total_mutants=result_dict.get("total_mutants", 0),
        total_killed=result_dict.get("total_killed", 0),
        total_survived=result_dict.get("total_survived", 0),
        survival_rate=result_dict.get("survival_rate", 0.0),
        per_category=per_cat,
        kill_matrix=result_dict.get("kill_matrix", {}),
    )


# ── Prescription helpers ──────────────────────────────────────────


def prescription_for_category(category: str) -> str:
    """Map surviving category to actionable prescription."""
    prescriptions = {
        "VALUE": "Add exact-value assertions: assert f(input) == expected_output",
        "SWAP": "Add parameter-order tests: verify f(a, b) != f(b, a) where applicable",
        "BOUNDARY": "Add boundary-value tests: test at boundary-1, boundary, boundary+1",
        "STATE": "Add state-verification tests: check self.attr after method calls",
        "TYPE": "Add type-discrimination tests: verify isinstance checks affect behavior",
    }
    return prescriptions.get(category, f"Add tests targeting {category} mutations")


def generate_test_skeleton(func_key: str, category: str) -> dict:
    """Generate a pytest test skeleton for a surviving category."""
    short_name = func_key.split("::")[-1] if "::" in func_key else func_key
    safe_name = short_name.replace(".", "_")
    test_name = f"test_{safe_name}_{category.lower()}_mutation"

    # Detect methods (Class.method) vs bare functions
    if "." in short_name:
        parts = short_name.split(".")
        class_name = parts[0]
        method_name = parts[-1]
        templates = {
            "VALUE": f"def {test_name}():\n    obj = {class_name}()\n    result = obj.{method_name}(...)\n    assert result == EXPECTED_VALUE\n",
            "SWAP": f"def {test_name}():\n    obj = {class_name}()\n    assert obj.{method_name}(a, b) != obj.{method_name}(b, a)\n",
            "BOUNDARY": f"def {test_name}():\n    obj = {class_name}()\n    assert obj.{method_name}(boundary - 1) == BELOW_RESULT\n    assert obj.{method_name}(boundary + 1) == ABOVE_RESULT\n",
            "STATE": f"def {test_name}():\n    obj = {class_name}()\n    obj.{method_name}(input_val)\n    assert obj.state_attr == EXPECTED_STATE\n",
            "TYPE": f"def {test_name}():\n    obj = {class_name}()\n    assert obj.{method_name}(valid_type) != obj.{method_name}(invalid_type)\n",
        }
    else:
        templates = {
            "VALUE": f"def {test_name}():\n    result = {short_name}(...)\n    assert result == EXPECTED_VALUE\n",
            "SWAP": f"def {test_name}():\n    assert {short_name}(a, b) != {short_name}(b, a)\n",
            "BOUNDARY": f"def {test_name}():\n    assert {short_name}(boundary - 1) == BELOW_RESULT\n    assert {short_name}(boundary + 1) == ABOVE_RESULT\n",
            "STATE": f"def {test_name}():\n    result = {short_name}(input_val)\n    assert result == EXPECTED_STATE\n",
            "TYPE": f"def {test_name}():\n    assert {short_name}(valid_type) != {short_name}(invalid_type)\n",
        }
    return {
        "function": func_key,
        "category": category,
        "test_name": test_name,
        "skeleton": templates.get(category, f"def {test_name}(): pass"),
    }
