"""Project-wide sweep orchestrator.

Discovers all composed specs, triages by σ (simplest first — most likely
to resolve deterministically), resolves each via the auto_resolve pipeline,
and writes a manifest of results to disk.

All resolution is CPU-based. The tool never makes API calls.
- Synthesis gate hits resolve deterministically (pure CPU/AST).
- Cache hits return previously verified implementations (disk read).
- Non-deterministic specs return generation prompts for the calling LLM.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Any

from .auto_resolve import ResolveConfig, resolve_one
from .backends import select_backend
from .persistence import load_all_specs


@dataclass
class SweepManifest:
    """Result of a project-wide autonomous resolution sweep."""

    project_root: str
    scope: str
    total_specs: int = 0
    resolved: int = 0
    cached: int = 0
    synthesized: int = 0
    needs_generation: int = 0
    failed: int = 0
    skipped: int = 0
    total_elapsed_ms: int = 0
    results: list[dict[str, Any]] = field(default_factory=list)
    manifest_path: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_root": self.project_root,
            "scope": self.scope,
            "total_specs": self.total_specs,
            "resolved": self.resolved,
            "cached": self.cached,
            "synthesized": self.synthesized,
            "needs_generation": self.needs_generation,
            "failed": self.failed,
            "skipped": self.skipped,
            "total_elapsed_ms": self.total_elapsed_ms,
            "results": self.results,
            "manifest_path": self.manifest_path,
        }


def sweep(
    project_root: str,
    scope: str = "all",
    max_targets: int = 50,
    config: ResolveConfig | None = None,
) -> SweepManifest:
    """Resolve all composed specs in σ-ascending order.

    Args:
        project_root: Absolute path to project root.
        scope: "all" or module prefix filter (e.g. "lintgate.core").
        max_targets: Maximum specs to attempt in one sweep.
        config: Resolution configuration.
    """
    if config is None:
        config = ResolveConfig()

    t0 = time.monotonic_ns()
    manifest = SweepManifest(project_root=project_root, scope=scope)

    # ── Discover ─────────────────────────────────────────────────
    all_specs = load_all_specs(project_root)
    if not all_specs:
        manifest.total_elapsed_ms = _elapsed(t0)
        return manifest

    # Filter by scope
    if scope and scope != "all":
        all_specs = {
            k: v for k, v in all_specs.items() if scope.lower() in k.lower()
        }

    # ── Triage: sort by σ ascending ──────────────────────────────
    sorted_specs = sorted(all_specs.values(), key=lambda s: s.prescriptive_sigma)
    sorted_specs = sorted_specs[:max_targets]
    manifest.total_specs = len(sorted_specs)

    # ── Resolve each ─────────────────────────────────────────────
    for spec in sorted_specs:
        # Skip specs with no parameters and no invariants (empty shells)
        if not spec.parameters and not spec.invariants:
            manifest.skipped += 1
            manifest.results.append({
                "target_key": spec.target_key,
                "status": "skipped",
                "reason": "empty spec (no parameters or invariants)",
            })
            continue

        backend = select_backend(spec)
        targets = backend.compile(spec)
        result = resolve_one(spec, targets, project_root, config)

        manifest.results.append(result.to_dict())

        if result.status == "cached":
            manifest.cached += 1
            manifest.resolved += 1
        elif result.status == "resolved":
            manifest.resolved += 1
            if result.method == "synthesis_gate":
                manifest.synthesized += 1
        elif result.status == "needs_generation":
            manifest.needs_generation += 1
        else:
            manifest.failed += 1

    manifest.total_elapsed_ms = _elapsed(t0)

    # ── Persist manifest ─────────────────────────────────────────
    manifest_dir = os.path.join(project_root, ".lintgate", "analysis", "auto_sweep")
    os.makedirs(manifest_dir, exist_ok=True)
    manifest_path = os.path.join(manifest_dir, f"sweep_{int(time.time())}.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest.to_dict(), f, indent=2)
    manifest.manifest_path = manifest_path

    return manifest


def _elapsed(t0_ns: int) -> int:
    return int((time.monotonic_ns() - t0_ns) / 1_000_000)
