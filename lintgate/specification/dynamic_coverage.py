"""Dynamic coverage bridge — per-test function linkage from coverage.py contexts.

Parses the `.coverage` SQLite database produced by `coverage.py` when
`dynamic_context = test_function` is enabled. Maps line-level per-test
coverage to function-level source→test linkage using AST function spans.

Confidence tiers:
  - dynamic: linkage confirmed by execution trace (coverage.py context)
  - static: linkage inferred from AST call-name matching (test_impact.py)
  - hybrid: both dynamic and static agree — highest confidence

Usage:
  1. Run pytest with: coverage run --dynamic-context=test_function -m pytest
  2. Call build_dynamic_linkage(project_root) to parse .coverage and produce linkage
  3. Wire into test_impact.py via merge_with_static() for unified lookups
"""

from __future__ import annotations

import ast
import json
import os
import sqlite3
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class LinkageEntry:
    """A single source_function → test_function linkage with confidence."""

    test_file: str
    test_function: str
    confidence: str  # "dynamic", "static", or "hybrid"

    def to_dict(self) -> dict[str, str]:
        return {
            "test_file": self.test_file,
            "test_function": self.test_function,
            "confidence": self.confidence,
        }

    @staticmethod
    def from_dict(d: dict[str, str]) -> LinkageEntry:
        return LinkageEntry(
            test_file=d["test_file"],
            test_function=d["test_function"],
            confidence=d.get("confidence", "static"),
        )


@dataclass
class FunctionLinkage:
    """All test linkages for a single source function."""

    tests: list[LinkageEntry] = field(default_factory=list)

    @property
    def best_confidence(self) -> str:
        """Highest confidence tier among all linkages."""
        tiers = {e.confidence for e in self.tests}
        if "hybrid" in tiers:
            return "hybrid"
        if "dynamic" in tiers:
            return "dynamic"
        return "static"

    def to_dict(self) -> dict[str, Any]:
        return {
            "tests": [t.to_dict() for t in self.tests],
            "best_confidence": self.best_confidence,
        }


@dataclass
class DynamicLinkageMap:
    """Project-wide source→test linkage from dynamic coverage contexts."""

    linkages: dict[str, FunctionLinkage] = field(default_factory=dict)
    coverage_db_mtime: float = 0.0
    cache_built_at: float = 0.0
    total_contexts: int = 0
    total_source_functions_linked: int = 0
    total_linkage_pairs: int = 0

    def tests_for(self, func_key: str) -> list[LinkageEntry]:
        """Get test linkages for a function key."""
        fl = self.linkages.get(func_key)
        return fl.tests if fl else []

    def has_dynamic(self, func_key: str) -> bool:
        """Check if a function has dynamic (execution-traced) linkage."""
        entries = self.tests_for(func_key)
        return any(e.confidence in ("dynamic", "hybrid") for e in entries)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": 1,
            "coverage_db_mtime": self.coverage_db_mtime,
            "cache_built_at": self.cache_built_at,
            "total_contexts": self.total_contexts,
            "total_source_functions_linked": self.total_source_functions_linked,
            "total_linkage_pairs": self.total_linkage_pairs,
            "linkages": {k: v.to_dict() for k, v in self.linkages.items()},
        }

    @staticmethod
    def from_dict(d: dict[str, Any]) -> DynamicLinkageMap:
        linkages: dict[str, FunctionLinkage] = {}
        for key, fl_data in d.get("linkages", {}).items():
            entries = [LinkageEntry.from_dict(e) for e in fl_data.get("tests", [])]
            linkages[key] = FunctionLinkage(tests=entries)
        dlm = DynamicLinkageMap(
            linkages=linkages,
            coverage_db_mtime=d.get("coverage_db_mtime", 0.0),
            cache_built_at=d.get("cache_built_at", 0.0),
            total_contexts=d.get("total_contexts", 0),
            total_source_functions_linked=len(linkages),
            total_linkage_pairs=sum(len(fl.tests) for fl in linkages.values()),
        )
        return dlm


# ── Core: parse .coverage SQLite for dynamic contexts ─────────────


def parse_coverage_contexts(
    coverage_db: str,
) -> dict[str, dict[int, list[str]]]:
    """Parse .coverage SQLite database for per-line dynamic contexts.

    Returns {abs_filepath: {line_no: [context_strings]}}.

    The .coverage SQLite schema (coverage.py 7.x):
      - file: id, path
      - context: id, context
      - line_bits: file_id, context_id, numbits (bitmap of covered lines)

    Context strings from dynamic_context=test_function look like:
      "TestClass.test_method|run"  or  "test_function_name|run"
    """
    if not os.path.isfile(coverage_db):
        return {}

    try:
        conn = sqlite3.connect(coverage_db)
        conn.row_factory = sqlite3.Row
    except sqlite3.Error:
        return {}

    try:
        return _extract_contexts(conn)
    except sqlite3.Error:
        return {}
    finally:
        conn.close()


def _extract_contexts(conn: sqlite3.Connection) -> dict[str, dict[int, list[str]]]:
    """Extract per-file, per-line context lists from the coverage database."""
    # Check if the database has the expected tables
    tables = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    if not {"file", "context", "line_bits"}.issubset(tables):
        return {}

    # Load file and context lookup tables
    files = {row["id"]: row["path"] for row in conn.execute("SELECT id, path FROM file")}
    contexts = {
        row["id"]: row["context"] for row in conn.execute("SELECT id, context FROM context")
    }

    # Filter to non-empty contexts (the empty string is the "no context" default)
    test_contexts = {cid: ctx for cid, ctx in contexts.items() if ctx}

    if not test_contexts:
        return {}

    result: dict[str, dict[int, list[str]]] = {}

    for row in conn.execute("SELECT file_id, context_id, numbits FROM line_bits"):
        file_id = row["file_id"]
        context_id = row["context_id"]

        filepath = files.get(file_id)
        context_str = test_contexts.get(context_id)
        if not filepath or not context_str:
            continue

        # Decode the numbits bitmap to line numbers
        lines = _numbits_to_lines(row["numbits"])
        if not lines:
            continue

        file_contexts = result.setdefault(filepath, {})
        for line in lines:
            file_contexts.setdefault(line, []).append(context_str)

    return result


def _numbits_to_lines(numbits: bytes) -> list[int]:
    """Decode coverage.py's numbits bitmap to a list of line numbers.

    numbits is a variable-length byte string where bit N (0-indexed) means
    line N+1 is covered. Byte 0 holds bits 0-7, byte 1 holds bits 8-15, etc.
    """
    lines: list[int] = []
    for byte_idx, byte_val in enumerate(numbits):
        if byte_val == 0:
            continue
        for bit in range(8):
            if byte_val & (1 << bit):
                lines.append(byte_idx * 8 + bit + 1)
    return lines


# ── Map line-level contexts to function-level linkage ─────────────


def _get_function_spans(filepath: str) -> list[tuple[str, int, int]]:
    """Parse a Python file and return [(qualname, start_line, end_line)] for all functions.

    Walks module-level and class-level functions, building qualified names.
    """
    try:
        with open(filepath, encoding="utf-8") as f:
            source = f.read()
        tree = ast.parse(source, filename=filepath)
    except (OSError, SyntaxError):
        return []

    spans: list[tuple[str, int, int]] = []
    _walk_spans(tree, "", spans)
    return spans


def _walk_spans(scope: Any, prefix: str, out: list[tuple[str, int, int]]) -> None:
    """Recursively walk AST, collecting function spans."""
    for node in getattr(scope, "body", []):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            qualname = f"{prefix}{node.name}" if prefix else node.name
            end_line = getattr(node, "end_lineno", node.lineno)
            out.append((qualname, node.lineno, end_line))
            # Recurse for nested/inner functions
            inner_prefix = f"{qualname}.<locals>."
            _walk_spans(node, inner_prefix, out)
        elif isinstance(node, ast.ClassDef):
            class_prefix = f"{prefix}{node.name}." if prefix else f"{node.name}."
            _walk_spans(node, class_prefix, out)


def _parse_test_context(context_str: str) -> tuple[str, str]:
    """Parse a coverage.py dynamic context string into (test_file_hint, test_function).

    Context strings look like:
      "TestClass.test_method|run"
      "test_function_name|run"
      "test_module.py::TestClass.test_method"  (pytest-cov format)

    Returns ("", test_function_name) — test_file is resolved separately
    since coverage.py contexts don't reliably include file paths.
    """
    # Strip the "|run" suffix that coverage.py appends
    ctx = context_str.split("|")[0].strip()

    # Handle pytest-cov format: "path::name"
    if "::" in ctx:
        parts = ctx.split("::", 1)
        return parts[0], parts[1]

    return "", ctx


def build_dynamic_linkage(
    project_root: str,
    coverage_db: str | None = None,
    source_files: list[str] | None = None,
) -> DynamicLinkageMap:
    """Build function-level source→test linkage from dynamic coverage data.

    Args:
        project_root: Absolute path to the project root.
        coverage_db: Path to .coverage SQLite database.
            Defaults to {project_root}/.coverage.
        source_files: Optional list of source files to map.
            If None, maps all files found in the coverage data.

    Returns:
        DynamicLinkageMap with per-function linkage entries.
    """
    from lintgate.keys import canonical_function_key, canonical_relpath

    if coverage_db is None:
        coverage_db = os.path.join(project_root, ".coverage")

    dlm = DynamicLinkageMap()

    if not os.path.isfile(coverage_db):
        return dlm

    dlm.coverage_db_mtime = os.path.getmtime(coverage_db)

    # Parse per-line contexts from the coverage database
    file_contexts = parse_coverage_contexts(coverage_db)
    if not file_contexts:
        return dlm

    # Count total unique contexts across all files
    all_contexts: set[str] = set()
    for line_ctxs in file_contexts.values():
        for ctxs in line_ctxs.values():
            all_contexts.update(ctxs)
    dlm.total_contexts = len(all_contexts)

    # Resolve test context strings → test file paths
    # We need to map context names back to test files. Build a reverse
    # index from test function names to their files using discovered test files.
    test_func_to_file = _build_test_func_index(project_root, file_contexts)

    # Determine which source files to process
    if source_files:
        files_to_process = source_files
    else:
        # Process all non-test Python files found in coverage data
        files_to_process = [
            fp for fp in file_contexts if fp.endswith(".py") and not _is_test_path(fp)
        ]

    for abs_path in files_to_process:
        # Normalize path for lookup
        norm_path = os.path.normpath(abs_path)
        line_ctxs = _find_file_contexts(norm_path, file_contexts, project_root)
        if not line_ctxs:
            continue

        # Get function spans from AST
        actual_path = abs_path if os.path.isabs(abs_path) else os.path.join(project_root, abs_path)
        if not os.path.isfile(actual_path):
            continue
        spans = _get_function_spans(actual_path)
        if not spans:
            continue

        rel_path = canonical_relpath(actual_path, project_root)

        # Map each function to its covering test contexts
        for qualname, start_line, end_line in spans:
            # Collect all test contexts that cover any line in this function
            covering_tests: set[tuple[str, str]] = set()  # (test_file, test_func)
            for line_no in range(start_line, end_line + 1):
                for ctx in line_ctxs.get(line_no, []):
                    _file_hint, test_func = _parse_test_context(ctx)
                    # Resolve test file from our index
                    test_file = (
                        _file_hint
                        or test_func_to_file.get(test_func, "")
                        or test_func_to_file.get(test_func.split(".")[-1], "")
                    )
                    if test_func:
                        covering_tests.add((test_file, test_func))

            if covering_tests:
                func_key = canonical_function_key(rel_path, qualname)
                entries = [
                    LinkageEntry(
                        test_file=tf,
                        test_function=tfunc,
                        confidence="dynamic",
                    )
                    for tf, tfunc in sorted(covering_tests)
                ]
                dlm.linkages[func_key] = FunctionLinkage(tests=entries)

    dlm.total_source_functions_linked = len(dlm.linkages)
    dlm.total_linkage_pairs = sum(len(fl.tests) for fl in dlm.linkages.values())
    dlm.cache_built_at = time.time()
    return dlm


def _find_file_contexts(
    norm_path: str,
    file_contexts: dict[str, dict[int, list[str]]],
    project_root: str,
) -> dict[int, list[str]] | None:
    """Find context data for a file, trying multiple path formats."""
    if norm_path in file_contexts:
        return file_contexts[norm_path]

    # Try relative path
    try:
        rel = os.path.relpath(norm_path, project_root)
        if rel in file_contexts:
            return file_contexts[rel]
    except ValueError:
        pass

    # Try matching by normpath
    for key, ctxs in file_contexts.items():
        if os.path.normpath(key) == norm_path:
            return ctxs
        try:
            if os.path.normpath(os.path.join(project_root, key)) == norm_path:
                return ctxs
        except (ValueError, TypeError):
            pass

    return None


def _build_test_func_index(
    project_root: str,
    file_contexts: dict[str, dict[int, list[str]]],
) -> dict[str, str]:
    """Build a reverse index: test_function_name → test_file_relpath.

    Scans two sources:
    1. Test files found in coverage data (may include in-tree test helpers)
    2. Standard test directories (tests/, test/) which may not be in coverage
       when .coveragerc only instruments source dirs

    Context strings from dynamic_context=test_function use the form
    "module_name.ClassName.test_method", where module_name is the test
    module (e.g. "test_controlplane_runtime"). We index by:
    - Full qualified name: "TestClass.test_method" → file
    - Bare function name: "test_method" → file
    - Module-qualified: "test_module.TestClass.test_method" → file
    """
    index: dict[str, str] = {}

    # Source 1: files in coverage data
    for filepath in file_contexts:
        if not _is_test_path(filepath):
            continue
        _index_file(filepath, project_root, index)

    # Source 2: standard test directories (critical when .coveragerc
    # only instruments source dirs, not tests/)
    for test_dir in ("tests", "test"):
        test_root = os.path.join(project_root, test_dir)
        if not os.path.isdir(test_root):
            continue
        for dirpath, dirnames, filenames in os.walk(test_root):
            dirnames[:] = [d for d in dirnames if not d.startswith(".") and d != "__pycache__"]
            for fname in filenames:
                if fname.endswith(".py") and _is_test_path(fname):
                    _index_file(os.path.join(dirpath, fname), project_root, index)

    return index


def _index_file(filepath: str, project_root: str, index: dict[str, str]) -> None:
    """Index a single test file's functions into the reverse lookup."""
    abs_path = filepath if os.path.isabs(filepath) else os.path.join(project_root, filepath)
    if not os.path.isfile(abs_path):
        return

    try:
        rel = os.path.relpath(abs_path, project_root)
    except ValueError:
        rel = filepath

    try:
        with open(abs_path, encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=abs_path)
    except (OSError, SyntaxError):
        return

    # Derive module name from filename (test_foo.py → test_foo)
    basename_module = os.path.basename(abs_path).removesuffix(".py")

    # Derive full dotted package path from relative path so that
    # package-qualified contexts like "tests.a.test_api.TestCalc.test_add"
    # resolve to the correct file when duplicate basenames exist
    # (e.g. tests/a/test_api.py vs tests/b/test_api.py).
    package_module = _relpath_to_dotted(rel)

    _index_test_funcs(
        tree.body,
        rel,
        "",
        index,
        module_name=basename_module,
        package_module=package_module,
    )


def _relpath_to_dotted(rel_path: str) -> str:
    """Convert a relative file path to a dotted module path.

    "tests/a/test_api.py" → "tests.a.test_api"
    """
    normed = rel_path.replace(os.sep, "/")
    if normed.endswith(".py"):
        normed = normed[:-3]
    return normed.replace("/", ".")


def _index_test_funcs(
    body: list[ast.stmt],
    test_file: str,
    prefix: str,
    index: dict[str, str],
    module_name: str = "",
    package_module: str = "",
) -> None:
    """Index test functions by name for reverse lookup.

    Indexes each test function under multiple keys to handle the different
    name formats used by coverage.py dynamic contexts:
    - Bare: "test_method"
    - Class-qualified: "TestClass.test_method"
    - Module-qualified (basename): "test_module.TestClass.test_method"
    - Package-qualified: "tests.a.test_module.TestClass.test_method"

    The package_module key disambiguates duplicate basenames across packages.
    """
    for node in body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name.startswith("test_"):
                qualified = f"{prefix}{node.name}" if prefix else node.name
                index[qualified] = test_file
                # Bare function name for unqualified lookups
                if prefix:
                    index[node.name] = test_file
                # Module-qualified name (basename) for coverage.py context matching
                if module_name:
                    mod_qualified = f"{module_name}.{qualified}"
                    index[mod_qualified] = test_file
                # Package-qualified name to disambiguate duplicate basenames
                # e.g. "tests.a.test_api.TestCalc.test_add" vs "tests.b.test_api.TestCalc.test_add"
                if package_module and package_module != module_name:
                    pkg_qualified = f"{package_module}.{qualified}"
                    index[pkg_qualified] = test_file
        elif isinstance(node, ast.ClassDef):
            class_prefix = f"{prefix}{node.name}." if prefix else f"{node.name}."
            _index_test_funcs(
                node.body,
                test_file,
                class_prefix,
                index,
                module_name=module_name,
                package_module=package_module,
            )


def _test_func_matches(dynamic_name: str, static_names: set[str]) -> bool:
    """Check if a dynamic test function name matches any static name.

    Dynamic names are module-qualified ("mod.TestClass.test_method"),
    static names are class-qualified ("TestClass.test_method") or bare.
    Uses suffix matching: if the dynamic name ends with a static name, it matches.
    """
    if dynamic_name in static_names:
        return True
    for static in static_names:
        if dynamic_name.endswith("." + static) or dynamic_name.endswith(static):
            return True
    return False


def _is_test_path(filepath: str) -> bool:
    """Check if a filepath looks like a test file."""
    basename = os.path.basename(filepath)
    return basename.startswith("test_") or basename.endswith("_test.py")


# ── Merge dynamic + static linkage ────────────────────────────────


def merge_with_static(
    dynamic: DynamicLinkageMap,
    static_impact: Any,  # TestImpactMap from test_impact.py
) -> DynamicLinkageMap:
    """Merge dynamic coverage linkage with static AST-based impact map.

    - Entries found in both → confidence="hybrid"
    - Entries found only in dynamic → confidence="dynamic"
    - Entries found only in static → confidence="static"

    Returns a new DynamicLinkageMap with merged linkages.
    """
    merged = DynamicLinkageMap(
        coverage_db_mtime=dynamic.coverage_db_mtime,
        total_contexts=dynamic.total_contexts,
    )

    # Add static keys (static uses bare function names, not canonical keys)
    # We need to iterate static entries and match them
    static_by_func: dict[str, set[tuple[str, str]]] = {}
    if hasattr(static_impact, "function_to_tests"):
        for bare_name, refs in static_impact.function_to_tests.items():
            pairs = set()
            for ref in refs:
                pairs.add((ref.test_file, ref.test_function))
            static_by_func[bare_name] = pairs

    # Process dynamic entries, upgrading to hybrid where static agrees
    for func_key, fl in dynamic.linkages.items():
        # Extract bare function name from canonical key for static lookup
        bare_name = func_key.rsplit("::", 1)[-1] if "::" in func_key else func_key
        # Also try the leaf name (after last dot)
        leaf_name = bare_name.rsplit(".", 1)[-1] if "." in bare_name else bare_name

        static_pairs = static_by_func.get(bare_name, set()) | static_by_func.get(leaf_name, set())

        entries: list[LinkageEntry] = []
        dynamic_pairs: set[tuple[str, str]] = set()

        for entry in fl.tests:
            pair = (entry.test_file, entry.test_function)
            dynamic_pairs.add(pair)
            # Check if static also found this link.
            # Dynamic names are module-qualified ("mod.TestClass.test_method"),
            # static names are class-qualified ("TestClass.test_method").
            # Use suffix matching to bridge the gap.
            static_match = pair in static_pairs or _test_func_matches(
                entry.test_function, {sp[1] for sp in static_pairs}
            )
            entries.append(
                LinkageEntry(
                    test_file=entry.test_file,
                    test_function=entry.test_function,
                    confidence="hybrid" if static_match else "dynamic",
                )
            )

        # Add static-only entries
        dynamic_test_names = {e.test_function for e in entries}
        for test_file, test_func in static_pairs:
            if (test_file, test_func) not in dynamic_pairs and not _test_func_matches(
                test_func, dynamic_test_names
            ):
                entries.append(
                    LinkageEntry(
                        test_file=test_file,
                        test_function=test_func,
                        confidence="static",
                    )
                )

        merged.linkages[func_key] = FunctionLinkage(tests=entries)

    # Add static-only function keys not in dynamic
    # This requires knowing canonical keys for static entries,
    # which we don't have. Static entries use bare names.
    # These will be picked up by the existing test_impact fallback path.

    merged.total_source_functions_linked = len(merged.linkages)
    merged.total_linkage_pairs = sum(len(fl.tests) for fl in merged.linkages.values())
    return merged


# ── Persistence ───────────────────────────────────────────────────

_CACHE_FILE = ".lintgate/linkage_cache.json"


def save_linkage_cache(project_root: str, linkage: DynamicLinkageMap) -> str:
    """Persist linkage map to the project's .lintgate directory.

    Returns the absolute path of the cache file.
    """
    cache_path = os.path.join(project_root, _CACHE_FILE)
    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(linkage.to_dict(), f, indent=2)
    return cache_path


def load_linkage_cache(project_root: str) -> DynamicLinkageMap | None:
    """Load persisted linkage map, or None if not found / stale.

    Staleness checks:
      1. .coverage database modified since cache was built
      2. Any source or test .py file modified since cache was built
    """
    cache_path = os.path.join(project_root, _CACHE_FILE)
    if not os.path.isfile(cache_path):
        return None

    try:
        with open(cache_path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None

    dlm = DynamicLinkageMap.from_dict(data)

    # Staleness check 1: .coverage newer than cached mtime
    coverage_db = os.path.join(project_root, ".coverage")
    if os.path.isfile(coverage_db):
        current_mtime = os.path.getmtime(coverage_db)
        if current_mtime > dlm.coverage_db_mtime:
            return None  # Stale

    # Staleness check 2: source/test files modified after cache was built
    if dlm.cache_built_at > 0 and _any_py_file_newer(project_root, dlm.cache_built_at):
        return None  # Stale

    return dlm


def _any_py_file_newer(project_root: str, threshold: float) -> bool:
    """Check if any .py file in source or test dirs is newer than threshold.

    Scans standard directories (tests/, test/, and the project's own package
    dirs) for files modified after the cache was built. Short-circuits on
    first match for speed.
    """
    # Check test directories
    for test_dir in ("tests", "test"):
        test_root = os.path.join(project_root, test_dir)
        if not os.path.isdir(test_root):
            continue
        for dirpath, dirnames, filenames in os.walk(test_root):
            dirnames[:] = [d for d in dirnames if not d.startswith(".") and d != "__pycache__"]
            for fname in filenames:
                if fname.endswith(".py"):
                    fpath = os.path.join(dirpath, fname)
                    try:
                        if os.path.getmtime(fpath) > threshold:
                            return True
                    except OSError:
                        continue

    # Check source directories (top-level packages with __init__.py)
    try:
        entries = os.listdir(project_root)
    except OSError:
        return False
    for entry in entries:
        entry_path = os.path.join(project_root, entry)
        if (
            os.path.isdir(entry_path)
            and not entry.startswith(".")
            and entry not in ("tests", "test", "__pycache__", "node_modules", ".git")
            and os.path.isfile(os.path.join(entry_path, "__init__.py"))
        ):
            for dirpath, dirnames, filenames in os.walk(entry_path):
                dirnames[:] = [d for d in dirnames if not d.startswith(".") and d != "__pycache__"]
                for fname in filenames:
                    if fname.endswith(".py"):
                        fpath = os.path.join(dirpath, fname)
                        try:
                            if os.path.getmtime(fpath) > threshold:
                                return True
                        except OSError:
                            continue

    return False


def build_or_load_linkage(
    project_root: str,
    coverage_db: str | None = None,
    force_rebuild: bool = False,
) -> DynamicLinkageMap:
    """Build dynamic linkage, using cache when available.

    This is the main entry point for consumers. Returns cached linkage
    if the .coverage database hasn't changed, otherwise rebuilds and
    persists a fresh map.
    """
    if not force_rebuild:
        cached = load_linkage_cache(project_root)
        if cached is not None:
            return cached

    dlm = build_dynamic_linkage(project_root, coverage_db=coverage_db)

    if dlm.linkages:
        save_linkage_cache(project_root, dlm)

    return dlm
