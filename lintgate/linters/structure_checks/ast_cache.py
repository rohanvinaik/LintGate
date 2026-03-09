"""G: AST function analysis cache — per-function caching keyed by source hash.

Avoids re-analyzing unchanged functions during incremental structure checks.
On edit: hash each function body, re-analyze only changed functions.
Import changes → invalidate all functions in the file.

Cache is session-scoped (in-memory). No persistence across sessions.
"""

from __future__ import annotations

import ast
import hashlib
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class CachedAnalysis:
    """Cached analysis result for a single function."""

    function_name: str
    file_path: str
    source_hash: str
    analysis: dict[str, Any]  # The cached result (CC, prescriptions, etc.)
    timestamp: float = 0.0

    def is_stale(self, max_age_seconds: float = 600.0) -> bool:
        """Check if this cache entry is too old."""
        return (time.time() - self.timestamp) > max_age_seconds


@dataclass
class FileCacheState:
    """Cache state for a single file."""

    file_path: str
    import_hash: str  # Hash of all imports — change invalidates entire file
    functions: dict[str, CachedAnalysis] = field(default_factory=dict)


class FunctionAnalysisCache:
    """Per-function analysis cache keyed by function source hash.

    Usage:
        cache = FunctionAnalysisCache()

        # Check cache before analysis
        cached = cache.get(file_path, func_name, func_hash)
        if cached:
            return cached.analysis

        # After analysis, store result
        cache.set(file_path, func_name, func_hash, analysis_result)

        # On file edit with import changes
        cache.invalidate_file(file_path)
    """

    def __init__(self, max_entries: int = 2000, max_age_seconds: float = 600.0):
        self._files: dict[str, FileCacheState] = {}
        self.max_entries = max_entries
        self.max_age_seconds = max_age_seconds
        self.hits = 0
        self.misses = 0

    def get(
        self,
        file_path: str,
        func_name: str,
        func_hash: str,
    ) -> CachedAnalysis | None:
        """Retrieve cached analysis for a function.

        Returns None if not cached, hash changed, or entry is stale.
        """
        file_state = self._files.get(file_path)
        if file_state is None:
            self.misses += 1
            return None

        cached = file_state.functions.get(func_name)
        if cached is None:
            self.misses += 1
            return None

        # Hash mismatch — function body changed
        if cached.source_hash != func_hash:
            self.misses += 1
            return None

        # Stale check
        if cached.is_stale(self.max_age_seconds):
            del file_state.functions[func_name]
            self.misses += 1
            return None

        self.hits += 1
        return cached

    def set(
        self,
        file_path: str,
        func_name: str,
        func_hash: str,
        analysis: dict[str, Any],
        import_hash: str = "",
    ) -> None:
        """Cache an analysis result for a function.

        If import_hash is provided and differs from the stored value,
        the entire file cache is invalidated first.
        """
        file_state = self._files.get(file_path)

        if file_state is None:
            file_state = FileCacheState(file_path=file_path, import_hash=import_hash)
            self._files[file_path] = file_state
        elif import_hash and file_state.import_hash != import_hash:
            # Import change — invalidate all functions in this file
            file_state.functions.clear()
            file_state.import_hash = import_hash

        file_state.functions[func_name] = CachedAnalysis(
            function_name=func_name,
            file_path=file_path,
            source_hash=func_hash,
            analysis=analysis,
            timestamp=time.time(),
        )

        # Evict if over capacity
        self._evict_if_needed()

    def invalidate_file(self, file_path: str) -> None:
        """Invalidate all cached entries for a file."""
        self._files.pop(file_path, None)

    def invalidate_all(self) -> None:
        """Clear the entire cache."""
        self._files.clear()
        self.hits = 0
        self.misses = 0

    @property
    def total_entries(self) -> int:
        """Total number of cached function analyses."""
        return sum(len(fs.functions) for fs in self._files.values())

    @property
    def hit_rate(self) -> float:
        """Cache hit rate as a fraction."""
        total = self.hits + self.misses
        return self.hits / total if total > 0 else 0.0

    def stats(self) -> dict[str, Any]:
        """Return cache statistics."""
        return {
            "files_cached": len(self._files),
            "functions_cached": self.total_entries,
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate": round(self.hit_rate, 3),
        }

    def _evict_if_needed(self) -> None:
        """Evict oldest entries if cache exceeds max_entries."""
        if self.total_entries <= self.max_entries:
            return

        # Collect all entries with timestamps
        all_entries: list[tuple[float, str, str]] = []
        for file_state in self._files.values():
            for func_name, cached in file_state.functions.items():
                all_entries.append((cached.timestamp, file_state.file_path, func_name))

        # Sort by timestamp (oldest first) and evict
        all_entries.sort()
        evict_count = self.total_entries - self.max_entries
        for _, fp, fn in all_entries[:evict_count]:
            evict_state = self._files.get(fp)
            if evict_state:
                evict_state.functions.pop(fn, None)
                if not evict_state.functions:
                    del self._files[fp]


# ── Hashing utilities ────────────────────────────────────────────────────


def hash_function_source(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """Compute a stable hash of a function's AST.

    Uses ast.dump for deterministic representation. This captures the
    structure of the function body, not formatting or comments.
    """
    dumped = ast.dump(node, annotate_fields=True, include_attributes=False)
    return hashlib.sha256(dumped.encode()).hexdigest()[:16]


def hash_file_imports(tree: ast.Module) -> str:
    """Compute a hash of all imports in a module.

    When this hash changes, all function caches for the file should be
    invalidated because import changes can affect function behavior.
    """
    import_nodes = [n for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))]
    if not import_nodes:
        return "no_imports"

    dumped = ";".join(
        ast.dump(n, annotate_fields=True, include_attributes=False) for n in import_nodes
    )
    return hashlib.sha256(dumped.encode()).hexdigest()[:16]
