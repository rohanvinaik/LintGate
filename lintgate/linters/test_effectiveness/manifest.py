"""Test effectiveness manifest builder with incremental caching.

Follows the same caching pattern as performance_checks/manifest.py:
- MD5 hash per file for incremental rebuild
- JSON serialization to PERF_CACHE_DIR / "test_effectiveness_cache.json"
- update_metrics() recalculates aggregate scores
"""

from __future__ import annotations

import datetime
import hashlib
import json
from typing import Any

from lintgate.state import PERF_CACHE_DIR

from .test_analyzer import (
    _discover_source_files,
    _discover_test_files,
    analyze_effectiveness,
)
from .types import TestEffectivenessManifest

# Increment this constant whenever the cache schema changes in a backward-
# incompatible way (e.g. new required keys, changed field semantics).
# Caches written with an older version are silently discarded and rebuilt.
TEFF_CACHE_SCHEMA_VERSION = "1"


def _compute_file_hash(filepath: str) -> str:
    """Compute MD5 hash of a file's contents."""
    with open(filepath, "rb") as f:
        return hashlib.md5(f.read(), usedforsecurity=False).hexdigest()


def _load_manifest_cache(
    cache_path: Any,
    expected_scope_fingerprint: str | None = None,
) -> tuple[TestEffectivenessManifest, dict[str, dict[str, Any]]]:
    """Load cached manifest and metadata from disk.

    Fails open (returns empty manifest) on:
    - Missing file
    - JSON decode error or OS error
    - schema_version mismatch
    - scope_fingerprint mismatch (prevents scope leakage #79)
    """
    if not cache_path.exists():
        return TestEffectivenessManifest(), {}
    try:
        with open(cache_path) as f:
            cached_data = json.load(f)

        # (#68) Validate schema version
        if cached_data.get("schema_version") != TEFF_CACHE_SCHEMA_VERSION:
            return TestEffectivenessManifest(), {}

        # (#79) Validate scope fingerprint to prevent contamination from broader runs
        if (
            expected_scope_fingerprint
            and cached_data.get("scope_fingerprint") != expected_scope_fingerprint
        ):
            return TestEffectivenessManifest(), {}

        return (
            TestEffectivenessManifest.from_dict(cached_data.get("manifest", {})),
            cached_data.get("metadata", {}),
        )
    except (json.JSONDecodeError, OSError, KeyError):
        return TestEffectivenessManifest(), {}


def _save_manifest_cache(
    cache_path: Any,
    manifest: TestEffectivenessManifest,
    metadata: dict[str, dict[str, Any]],
    scope_fingerprint: str | None = None,
) -> None:
    """Persist manifest and per-file metadata to disk cache."""
    try:
        envelope = {
            "schema_version": TEFF_CACHE_SCHEMA_VERSION,
            "written_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "scope_fingerprint": scope_fingerprint,
            "manifest": manifest.to_dict(),
            "metadata": metadata,
        }
        with open(cache_path, "w") as f:
            json.dump(envelope, f)
    except OSError:
        pass


def build_test_effectiveness_manifest(
    project_root: str,
    python_files: list[str] | None = None,
    test_files: list[str] | None = None,
) -> TestEffectivenessManifest:
    """Build a TestEffectivenessManifest with incremental caching.

    Args:
        project_root: Project root path.
        python_files: Source files to analyze. Auto-discovered if None.
        test_files: Test files to analyze. Auto-discovered if None.

    Returns:
        Populated TestEffectivenessManifest with per-function effectiveness data.
    """
    PERF_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    project_hash = hashlib.sha256(project_root.encode()).hexdigest()[:16]
    cache_path = PERF_CACHE_DIR / f"teff_{project_hash}.json"

    if python_files is None:
        python_files = _discover_source_files(project_root)
    if test_files is None:
        test_files = _discover_test_files(project_root)

    if not python_files or not test_files:
        return TestEffectivenessManifest()

    # (#79) Compute scope fingerprint to prevent leakage
    scope_payload = ",".join(sorted(python_files)) + ":" + ",".join(sorted(test_files))
    scope_fingerprint = hashlib.sha256(scope_payload.encode()).hexdigest()

    # Check if cache is still valid (all files unchanged AND scope matches)
    cached_manifest, cache_metadata = _load_manifest_cache(
        cache_path, expected_scope_fingerprint=scope_fingerprint
    )

    all_files = python_files + test_files
    any_changed = False
    new_metadata: dict[str, dict[str, Any]] = {}

    for filepath in all_files:
        try:
            file_hash = _compute_file_hash(filepath)
        except OSError:
            any_changed = True
            continue

        cached_entry = cache_metadata.get(filepath)
        if not cached_entry or cached_entry.get("hash") != file_hash:
            any_changed = True

        new_metadata[filepath] = {"hash": file_hash}

    # If nothing changed and cache has data, return cached
    if not any_changed and cached_manifest.functions:
        return cached_manifest

    # Rebuild from scratch (test mapping is holistic, not per-file incremental)
    effectiveness, diagnostics = analyze_effectiveness(project_root, python_files, test_files)

    manifest = TestEffectivenessManifest(functions=effectiveness, diagnostics=diagnostics)

    manifest.file_scores = {}

    manifest.update_metrics()
    _save_manifest_cache(cache_path, manifest, new_metadata, scope_fingerprint=scope_fingerprint)

    return manifest
