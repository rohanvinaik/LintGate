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
    mean_reconciled_spec_level: float = 0.0
    mapping_coverage: float = 0.0
    regime_distribution: dict[str, int] = field(default_factory=dict)
    risk_distribution: dict[str, int] = field(default_factory=dict)
    phase_distribution: dict[str, int] = field(default_factory=dict)
    reconciliation_distribution: dict[str, int] = field(default_factory=dict)
    gap_class_distribution: dict[str, int] = field(default_factory=dict)
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
            "mean_reconciled_spec_level": round(self.mean_reconciled_spec_level, 3),
            "mapping_coverage": round(self.mapping_coverage, 3),
            "regime_distribution": self.regime_distribution,
            "risk_distribution": self.risk_distribution,
            "phase_distribution": self.phase_distribution,
            "reconciliation_distribution": self.reconciliation_distribution,
            "gap_class_distribution": self.gap_class_distribution,
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
    _aggregate(rollup, file_results, project_root=project_root)
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


def _load_mutation_cache(project_root: str) -> dict[str, dict] | None:
    """Load all mutation cache entries for a project."""
    import json as _json
    from pathlib import Path as _Path

    cache_dir = _Path(project_root) / ".lintgate" / "mutation"
    if not cache_dir.exists():
        return None

    cache: dict[str, dict] = {}
    for cache_file in cache_dir.glob("*.json"):
        if cache_file.name == "scheduler_state.json":
            continue
        try:
            with open(cache_file, encoding="utf-8") as f:
                data = _json.load(f)
        except (OSError, ValueError):
            continue
        func_key = data.get("function_key", "")
        if func_key:
            cache[func_key] = data
    return cache if cache else None


def _reconciliation_priority(
    func_data: dict,
    overlay_status: str,
    overlay_confidence: float,
    survival_rate: float,
    static_spec_level: float,
    sigma: int,
) -> tuple[float, str]:
    """Score a function for hotspot priority using reconciliation state.

    Returns (score, reason).
    """
    composition_gamma = float(func_data.get("composition_gamma", 0.0) or 0.0)
    structural_signal = bool(func_data)
    sigma_weight = min(max(float(sigma), 0.0) / 30.0, 1.0) * 0.2 if structural_signal else 0.0
    composition_weight = min(max(composition_gamma, 0.0), 2.0) * 0.4 if structural_signal else 0.0
    structural_boost = sigma_weight + composition_weight

    # Static low + empirical bad: genuine high-priority gap.
    if static_spec_level < 0.3 and survival_rate > 0.5:
        if overlay_status == "CONTRADICTS" and overlay_confidence >= 0.7:
            return (2.5 + structural_boost, "under_specified_contradiction")
        return (3.0 + structural_boost, "both_under_specified")

    # Static low + empirical good under a contradiction: likely measurement artifact.
    if (
        overlay_status == "CONTRADICTS"
        and overlay_confidence >= 0.7
        and survival_rate < 0.2
        and static_spec_level < 0.3
    ):
        return (0.5 + sigma_weight, "measurement_artifact")

    # No empirical data — unknown, needs profiling.
    if overlay_status == "NO_EMPIRICAL_DATA" and static_spec_level < 0.3:
        return (1.5 + structural_boost, "needs_profiling")

    # Low spec without strong empirical disagreement.
    if static_spec_level < 0.3:
        return (2.0 + structural_boost, "genuinely_under_specified")

    if composition_gamma >= 0.5:
        return (1.2 + composition_weight + sigma_weight, "composition_gap")

    return (1.0 + sigma_weight, "default")


def _aggregate(
    rollup: ProjectRollup,
    results: list[FileSpecResult],
    project_root: str = "",
) -> None:
    """Aggregate per-file results into the project rollup."""
    from .gap_classifier import classify_from_func_data
    from .static_empirical_reconciliation import build_overlay, reconcile_spec_level

    total_spec = 0.0
    total_reconciled_spec = 0.0
    total_funcs = 0
    mapped_count = 0
    mutation_cache = _load_mutation_cache(project_root) if project_root else None
    file_hotspot_data: list[tuple[str, int, float, str]] = []  # file, sigma, priority, reason

    for r in results:
        rollup.total_files += 1
        n_funcs = len(r.functions)
        total_funcs += n_funcs
        rollup.total_sigma += r.total_sigma
        total_spec += r.mean_spec_level * n_funcs

        file_priority_sum = 0.0
        file_priority_reason = "default"
        best_reason_score = float("-inf")

        # Merge distributions
        for regime, count in r.regime_distribution.items():
            rollup.regime_distribution[regime] = rollup.regime_distribution.get(regime, 0) + count
        for band, count in r.risk_distribution.items():
            rollup.risk_distribution[band] = rollup.risk_distribution.get(band, 0) + count

        # Phase distribution + reconciliation from per-function data
        for func_key, func_data in r.functions.items():
            if not isinstance(func_data, dict):
                continue
            phase = func_data.get("phase", "bulk")
            rollup.phase_distribution[phase] = rollup.phase_distribution.get(phase, 0) + 1

            # Track test mapping coverage
            # A function is "mapped" if it has empirical mutation data (test linkage exists)
            if mutation_cache and func_key in mutation_cache:
                mapped_count += 1

            # Build overlay for reconciliation distribution
            sigma = func_data.get("sigma", func_data.get("estimated_sigma", 0)) or 0
            regime = func_data.get("regime", "A")
            overlay = build_overlay(func_key, int(sigma), regime, phase, mutation_cache)
            status_key = overlay.status.value
            rollup.reconciliation_distribution[status_key] = (
                rollup.reconciliation_distribution.get(status_key, 0) + 1
            )

            # Gap classification — trust persisted gap_class from file_analyzer
            # when available (it has function_name context for serializer heuristics).
            # Only recompute for old cached entries that lack the field.
            gap_class = func_data.get("gap_class")
            if not gap_class:
                mutation_entry = mutation_cache.get(func_key) if mutation_cache else None
                gap_class = classify_from_func_data(func_data, mutation_entry).value
            rollup.gap_class_distribution[gap_class] = (
                rollup.gap_class_distribution.get(gap_class, 0) + 1
            )

            # Reconciled spec_level
            static_spec = func_data.get("specification_level", 0.0)
            reconciled_val, _src = reconcile_spec_level(static_spec, overlay)
            total_reconciled_spec += reconciled_val

            # Priority scoring
            score, reason = _reconciliation_priority(
                func_data,
                status_key,
                overlay.overlay_confidence,
                overlay.empirical_survival_rate,
                static_spec,
                int(sigma),
            )
            file_priority_sum += score
            if reason != "default" and score > best_reason_score:
                best_reason_score = score
                file_priority_reason = reason

        avg_priority = file_priority_sum / n_funcs if n_funcs > 0 else 0.0
        final_priority = avg_priority
        file_hotspot_data.append((r.file, r.total_sigma, final_priority, file_priority_reason))

    rollup.total_functions = total_funcs
    rollup.mean_spec_level = total_spec / total_funcs if total_funcs > 0 else 0.0
    rollup.mean_reconciled_spec_level = (
        total_reconciled_spec / total_funcs if total_funcs > 0 else 0.0
    )
    rollup.mapping_coverage = mapped_count / total_funcs if total_funcs > 0 else 0.0

    # Top-N hotspot files by reconciliation-aware priority score
    file_hotspot_data.sort(key=lambda x: x[2], reverse=True)
    rollup.hotspot_files = [
        {"file": f, "sigma": s, "priority_score": round(p, 3), "priority_reason": reason}
        for f, s, p, reason in file_hotspot_data[:_MAX_HOTSPOT_FILES]
    ]
