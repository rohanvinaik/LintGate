"""Data types and I/O for the test regeneration strategy system.

Split from test_regeneration_strategy.py to stay under file-length limits.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


# ── Strategy enum ────────────────────────────────────────────────────


class Strategy(str, Enum):
    """Regeneration strategy for a function's tests."""

    EXCLUDE_MUTATION = "exclude_mutation"
    PRESERVE_SYSTEM = "preserve_system"
    MANUAL_CONTRACT = "manual_contract"
    AUTO_GENERATE_UNIT = "auto_generate_unit"


class ExistingTestAction(str, Enum):
    """What to do with existing tests for a function."""

    PRESERVE = "preserve"
    QUARANTINE_REPLACE = "quarantine_replace"
    QUARANTINE_ONLY = "quarantine_only"
    DELETE = "delete"


# ── Evidence sub-dataclasses ─────────────────────────────────────────


@dataclass
class SpecEvidence:
    """Specification analysis evidence for a function."""

    specification_level: float = 0.0
    sigma_upper_bound: int = 0
    regime: str = "unknown"
    phase: str = "bulk"
    is_pure: bool = False
    is_stateful: bool = False
    has_side_effects: bool = False
    testability_score: float = 1.0


@dataclass
class MutationEvidence:
    """Mutation profiling evidence for a function."""

    discovery_state: str = ""
    topology_state: str = ""
    survival_interpretation: str = ""
    survival_rate: float = 1.0
    tests_loaded: int = 0


# ── Composite evidence ───────────────────────────────────────────────


@dataclass
class FunctionEvidence:
    """Evidence used to classify a function's strategy.

    Composes SpecEvidence and MutationEvidence sub-dataclasses
    to keep attribute count manageable.
    """

    function_key: str = ""
    source_file: str = ""
    spec: SpecEvidence = field(default_factory=SpecEvidence)
    mutation: MutationEvidence = field(default_factory=MutationEvidence)
    covering_tests: list[str] = field(default_factory=list)
    assertion_count: int = 0

    # ── Convenience accessors (flat access for classifier) ───────

    @property
    def specification_level(self) -> float:
        return self.spec.specification_level

    @property
    def sigma_upper_bound(self) -> int:
        return self.spec.sigma_upper_bound

    @property
    def regime(self) -> str:
        return self.spec.regime

    @property
    def phase(self) -> str:
        return self.spec.phase

    @property
    def is_pure(self) -> bool:
        return self.spec.is_pure

    @property
    def is_stateful(self) -> bool:
        return self.spec.is_stateful

    @property
    def has_side_effects(self) -> bool:
        return self.spec.has_side_effects

    @property
    def testability_score(self) -> float:
        return self.spec.testability_score

    @property
    def discovery_state(self) -> str:
        return self.mutation.discovery_state

    @property
    def topology_state(self) -> str:
        return self.mutation.topology_state

    @property
    def survival_interpretation(self) -> str:
        return self.mutation.survival_interpretation

    @property
    def survival_rate(self) -> float:
        return self.mutation.survival_rate

    @property
    def tests_loaded(self) -> int:
        return self.mutation.tests_loaded

    def to_dict(self) -> dict:
        return {
            "specification_level": round(self.spec.specification_level, 3),
            "sigma_upper_bound": self.spec.sigma_upper_bound,
            "regime": self.spec.regime,
            "phase": self.spec.phase,
            "discovery_state": self.mutation.discovery_state,
            "topology_state": self.mutation.topology_state,
            "survival_interpretation": self.mutation.survival_interpretation,
            "purity": self.spec.is_pure,
        }


# ── Classification result ────────────────────────────────────────────


@dataclass
class ClassificationResult:
    """Result of classifying a single function."""

    function_key: str
    strategy: Strategy
    existing_test_action: ExistingTestAction
    target_test_file: str
    confidence: float
    reason_codes: list[str]
    evidence: FunctionEvidence
    generation_mode: str = ""
    manual_review_required: bool = False

    def to_dict(self) -> dict:
        return {
            "function_key": self.function_key,
            "strategy": self.strategy.value,
            "existing_test_action": self.existing_test_action.value,
            "target_test_file": self.target_test_file,
            "confidence": round(self.confidence, 3),
            "reason_codes": self.reason_codes,
            "evidence": self.evidence.to_dict(),
            "generation_mode": self.generation_mode,
            "manual_review_required": self.manual_review_required,
        }


# ── Manifest ─────────────────────────────────────────────────────────


@dataclass
class RebuildManifest:
    """Project-wide test rebuild manifest."""

    version: int = 1
    project_root: str = ""
    generated_at: str = ""
    functions: list[ClassificationResult] = field(default_factory=list)
    preserve_test_files: list[str] = field(default_factory=list)
    quarantine_test_files: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "project_root": self.project_root,
            "generated_at": self.generated_at,
            "functions": [f.to_dict() for f in self.functions],
            "preserve_test_files": self.preserve_test_files,
            "quarantine_test_files": self.quarantine_test_files,
        }

    def summary(self) -> dict:
        """Compute strategy distribution and key metrics."""
        by_strategy: dict[str, int] = {}
        by_action: dict[str, int] = {}
        review_count = 0
        total_confidence = 0.0

        for f in self.functions:
            s = f.strategy.value
            by_strategy[s] = by_strategy.get(s, 0) + 1
            a = f.existing_test_action.value
            by_action[a] = by_action.get(a, 0) + 1
            if f.manual_review_required:
                review_count += 1
            total_confidence += f.confidence

        n = len(self.functions)
        return {
            "total_functions": n,
            "strategy_distribution": by_strategy,
            "action_distribution": by_action,
            "manual_review_required": review_count,
            "manual_review_share": round(review_count / n, 3) if n else 0.0,
            "mean_confidence": round(total_confidence / n, 3) if n else 0.0,
            "preserve_test_files": len(self.preserve_test_files),
            "quarantine_test_files": len(self.quarantine_test_files),
        }


def write_manifest(manifest: RebuildManifest, project_root: str) -> str:
    """Write manifest to .lintgate/test_rebuild_manifest.json."""
    lintgate_dir = Path(project_root) / ".lintgate"
    lintgate_dir.mkdir(exist_ok=True)
    manifest_path = lintgate_dir / "test_rebuild_manifest.json"
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest.to_dict(), f, indent=2)
    return str(manifest_path)


def load_manifest(project_root: str) -> RebuildManifest | None:
    """Load manifest from .lintgate/test_rebuild_manifest.json."""
    manifest_path = Path(project_root) / ".lintgate" / "test_rebuild_manifest.json"
    if not manifest_path.exists():
        return None
    try:
        with open(manifest_path, encoding="utf-8") as f:
            data: dict[str, Any] = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None

    functions: list[ClassificationResult] = []
    for fd in data.get("functions", []):
        ev_data = fd.get("evidence", {})
        ev = FunctionEvidence(
            function_key=fd.get("function_key", ""),
            source_file=ev_data.get("source_file", ""),
            spec=SpecEvidence(
                specification_level=ev_data.get("specification_level", 0.0),
                sigma_upper_bound=ev_data.get("sigma_upper_bound", 0),
                regime=ev_data.get("regime", "unknown"),
                phase=ev_data.get("phase", "bulk"),
                is_pure=ev_data.get("purity", False),
            ),
            mutation=MutationEvidence(
                discovery_state=ev_data.get("discovery_state", ""),
                topology_state=ev_data.get("topology_state", ""),
                survival_interpretation=ev_data.get(
                    "survival_interpretation", ""
                ),
            ),
        )
        functions.append(
            ClassificationResult(
                function_key=fd.get("function_key", ""),
                strategy=Strategy(fd.get("strategy", "manual_contract")),
                existing_test_action=ExistingTestAction(
                    fd.get("existing_test_action", "preserve")
                ),
                target_test_file=fd.get("target_test_file", ""),
                confidence=fd.get("confidence", 0.0),
                reason_codes=fd.get("reason_codes", []),
                evidence=ev,
                generation_mode=fd.get("generation_mode", ""),
                manual_review_required=fd.get("manual_review_required", False),
            )
        )

    return RebuildManifest(
        version=data.get("version", 1),
        project_root=data.get("project_root", project_root),
        generated_at=data.get("generated_at", ""),
        functions=functions,
        preserve_test_files=data.get("preserve_test_files", []),
        quarantine_test_files=data.get("quarantine_test_files", []),
    )
