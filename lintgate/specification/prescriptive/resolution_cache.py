"""Resolution cache — spec_hash → verified implementation.

Cache key is SHA-256 of the spec's behavioral contract (invariants,
forbidden behaviors, algebraic laws, generation constraints, interface).
Excludes timestamps and IDs — only hashes the parts that determine
what code should be generated.

Storage: .lintgate/resolution_cache/{key}.json
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from .spec import PrescriptiveSpec

_CACHE_DIR = ".lintgate/resolution_cache"


def _spec_content_hash(spec_dict: dict[str, Any]) -> str:
    """Hash the behavioral contract portion of a spec."""
    contract = {
        "target_key": spec_dict.get("target_key", ""),
        "problem_class": spec_dict.get("problem_class", ""),
        "parameters": spec_dict.get("parameters", []),
        "return_type": spec_dict.get("return_type", ""),
        "invariants": spec_dict.get("invariants", []),
        "forbidden_behaviors": spec_dict.get("forbidden_behaviors", []),
        "algebraic_laws": spec_dict.get("algebraic_laws", []),
        "generation_constraints": spec_dict.get("generation_constraints", []),
        "state_variables": spec_dict.get("state_variables", []),
        "allowed_transitions": spec_dict.get("allowed_transitions", []),
    }
    content = json.dumps(contract, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(content.encode()).hexdigest()[:16]


@dataclass
class CachedResolution:
    """A verified implementation stored on disk."""

    body: str
    method: str  # "synthesis_gate" | "constrained_llm"
    confidence: float
    tokens_used: int
    verification: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "body": self.body,
            "method": self.method,
            "confidence": self.confidence,
            "tokens_used": self.tokens_used,
            "verification": self.verification,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CachedResolution:
        return cls(
            body=data.get("body", ""),
            method=data.get("method", ""),
            confidence=float(data.get("confidence", 0.0)),
            tokens_used=int(data.get("tokens_used", 0)),
            verification=data.get("verification", {}),
            created_at=float(data.get("created_at", 0.0)),
        )


def spec_cache_key(spec: PrescriptiveSpec) -> str:
    """Compute cache key from a PrescriptiveSpec."""
    return _spec_content_hash(spec.to_dict())


def get_cached(project_root: str, key: str) -> CachedResolution | None:
    """Return cached resolution or None."""
    path = os.path.join(project_root, _CACHE_DIR, f"{key}.json")
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return CachedResolution.from_dict(data)
    except (OSError, ValueError):
        return None


def put_cached(
    project_root: str, key: str, resolution: CachedResolution
) -> None:
    """Store a verified resolution."""
    cache_dir = os.path.join(project_root, _CACHE_DIR)
    os.makedirs(cache_dir, exist_ok=True)
    path = os.path.join(cache_dir, f"{key}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(resolution.to_dict(), f, indent=2)
