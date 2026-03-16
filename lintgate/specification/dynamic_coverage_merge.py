"""Merge dynamic + static linkage and persistence for coverage data."""

from __future__ import annotations

import json
import os
from typing import Any

from .dynamic_coverage import (
    DynamicLinkageMap,
    FunctionLinkage,
    LinkageEntry,
    _test_func_matches,
    build_dynamic_linkage,
)

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
