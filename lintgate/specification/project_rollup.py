"""Project-wide specification rollup with file-level caching.

Iterates all Python files via canonical discovery, analyzes each with
file_analyzer, caches results by content hash, and aggregates into
a project-wide rollup.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

from lintgate.discovery import discover_project_files
from lintgate.state import SPEC_CACHE_DIR

from .file_analyzer import FileSpecResult, analyze_file

_CACHE_SUBDIR = "files"
_MAX_HOTSPOT_FILES = 10
_TEST_PATTERNS = (
    re.compile(r"(?:^|/)tests?/"),
    re.compile(r"(?:^|/)test_[^/]+\.py$"),
    re.compile(r"(?:^|/)[^/]+_test\.py$"),
)


@dataclass
class ProjectRollup:
    """Aggregated specification state across all files in a project."""

    project_root: str = ""
    total_files: int = 0
    total_functions: int = 0
    total_sigma: int = 0
    mean_spec_level: float = 0.0
    regime_distribution: dict[str, int] = field(default_factory=dict)
    risk_distribution: dict[str, int] = field(default_factory=dict)
    phase_distribution: dict[str, int] = field(default_factory=dict)
    hotspot_files: list[dict[str, Any]] = field(default_factory=list)
    cache_hits: int = 0
    cache_misses: int = 0
    include_tests: bool = False
    skipped_test_files: int = 0
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_root": self.project_root,
            "total_files": self.total_files,
            "total_functions": self.total_functions,
            "total_sigma": self.total_sigma,
            "mean_spec_level": round(self.mean_spec_level, 3),
            "regime_distribution": self.regime_distribution,
            "risk_distribution": self.risk_distribution,
            "phase_distribution": self.phase_distribution,
            "hotspot_files": self.hotspot_files,
            "cache_hits": self.cache_hits,
            "cache_misses": self.cache_misses,
            "include_tests": self.include_tests,
            "skipped_test_files": self.skipped_test_files,
            "error_count": len(self.errors),
        }


def rollup_project(
    project_root: str,
    use_cache: bool = True,
    analyze_uncached: bool = False,
    include_tests: bool = False,
) -> ProjectRollup:
    """Aggregate specification data across all Python files.

    Default mode is cache-read-only: reads existing cache entries and
    skips files without cached results. This is O(files) with no
    heavy recomputation.

    Set analyze_uncached=True to analyze cache misses live (slower,
    builds manifests + call graph per uncached file).

    Args:
        project_root: Absolute path to the project root.
        use_cache: Whether to use file-level content-hash caching.
        analyze_uncached: If True, analyze files with no cache entry.
            If False (default), skip uncached files for fast rollup.
        include_tests: If True, include test files (``tests/``, ``test_*.py``,
            ``*_test.py``). Defaults to False so hotspots and rollup metrics
            focus on production code.

    Returns:
        ProjectRollup with aggregated specification data.
    """
    rollup = ProjectRollup(project_root=project_root)
    rollup.include_tests = include_tests
    py_files = discover_project_files(project_root, extra_exclude_dirs=frozenset({"archive"}))

    if not py_files:
        return rollup

    cache_dir = SPEC_CACHE_DIR / _CACHE_SUBDIR if use_cache else None
    if cache_dir:
        cache_dir.mkdir(parents=True, exist_ok=True)

    # Per-file analysis: cache-read-only by default
    file_results: list[FileSpecResult] = []
    for fpath in py_files:
        rel_path = os.path.relpath(fpath, project_root)
        if not include_tests and _is_test_file(rel_path):
            rollup.skipped_test_files += 1
            continue
        result = _analyze_with_cache(fpath, project_root, cache_dir, analyze_uncached)
        if result is None:
            # Cache miss in read-only mode — skip
            rollup.cache_misses += 1
            continue
        if result.error:
            rollup.errors.append(f"{result.file}: {result.error}")
            continue
        if result.functions:
            file_results.append(result)
            rollup.cache_hits += 1

    # Aggregate
    _aggregate(rollup, file_results)
    return rollup


def _is_test_file(filepath: str) -> bool:
    """Return True if *filepath* looks like a Python test file."""
    normalized = filepath.replace("\\", "/")
    return any(pat.search(normalized) for pat in _TEST_PATTERNS)


def _analyze_with_cache(
    file_path: str,
    project_root: str,
    cache_dir: Path | None,
    analyze_uncached: bool,
) -> FileSpecResult | None:
    """Load from cache or optionally analyze live.

    Returns None when cache_dir is set, no cache entry exists,
    and analyze_uncached is False (cache-read-only mode).
    """
    if cache_dir:
        cached = _load_file_cache(file_path, cache_dir)
        if cached is not None:
            return cached
        if not analyze_uncached:
            return None

    result = analyze_file(file_path, project_root)

    if cache_dir and result.error is None and result.functions:
        _save_file_cache(file_path, cache_dir, result)

    return result


def _cache_key(file_path: str) -> str:
    """Compute cache key from file path and content hash.

    The key incorporates both the file path and the content so that
    different files with identical content get separate cache entries.
    This matters because analyze_file results depend on context
    (imports, module position) not just content.
    """
    try:
        h = hashlib.sha256(file_path.encode("utf-8"))
        with open(file_path, "rb") as f:
            h.update(f.read())
        return h.hexdigest()[:16]
    except OSError:
        return ""


def _cache_hit(file_path: str, cache_dir: Path) -> bool:
    """Check if a valid cache entry exists for this file."""
    key = _cache_key(file_path)
    if not key:
        return False
    return (cache_dir / f"{key}.json").exists()


def _load_file_cache(file_path: str, cache_dir: Path) -> FileSpecResult | None:
    """Load cached FileSpecResult if content hash matches."""
    key = _cache_key(file_path)
    if not key:
        return None
    cache_file = cache_dir / f"{key}.json"
    if not cache_file.exists():
        return None
    try:
        with open(cache_file) as f:
            data = json.load(f)
        return _deserialize_file_result(data)
    except (OSError, json.JSONDecodeError, KeyError):
        return None


def _save_file_cache(file_path: str, cache_dir: Path, result: FileSpecResult) -> None:
    """Save FileSpecResult to cache keyed by content hash."""
    key = _cache_key(file_path)
    if not key:
        return
    cache_file = cache_dir / f"{key}.json"
    try:
        with open(cache_file, "w") as f:
            json.dump(result.to_dict(), f)
    except OSError:
        pass


def _deserialize_file_result(data: dict[str, Any]) -> FileSpecResult:
    """Reconstruct FileSpecResult from cached JSON."""
    return FileSpecResult(
        file=data["file"],
        project_root=data.get("project_root", ""),
        functions=data.get("functions", {}),
        total_sigma=data.get("total_sigma", 0),
        mean_spec_level=data.get("mean_spec_level", 0.0),
        regime_distribution=data.get("regime_distribution", {}),
        risk_distribution=data.get("risk_distribution", {}),
        error=data.get("error"),
    )


def _aggregate(rollup: ProjectRollup, results: list[FileSpecResult]) -> None:
    """Aggregate per-file results into the project rollup."""
    total_spec = 0.0
    total_funcs = 0
    file_sigma_pairs: list[tuple[str, int]] = []

    for r in results:
        rollup.total_files += 1
        n_funcs = len(r.functions)
        total_funcs += n_funcs
        rollup.total_sigma += r.total_sigma
        total_spec += r.mean_spec_level * n_funcs

        file_sigma_pairs.append((r.file, r.total_sigma))

        # Merge distributions
        for regime, count in r.regime_distribution.items():
            rollup.regime_distribution[regime] = rollup.regime_distribution.get(regime, 0) + count
        for band, count in r.risk_distribution.items():
            rollup.risk_distribution[band] = rollup.risk_distribution.get(band, 0) + count

        # Phase distribution from per-function data
        for func_data in r.functions.values():
            phase = func_data.get("phase", "bulk") if isinstance(func_data, dict) else "bulk"
            rollup.phase_distribution[phase] = rollup.phase_distribution.get(phase, 0) + 1

    rollup.total_functions = total_funcs
    rollup.mean_spec_level = total_spec / total_funcs if total_funcs > 0 else 0.0

    # Top-N hotspot files by sigma
    file_sigma_pairs.sort(key=lambda x: x[1], reverse=True)
    rollup.hotspot_files = [
        {"file": f, "sigma": s} for f, s in file_sigma_pairs[:_MAX_HOTSPOT_FILES]
    ]
