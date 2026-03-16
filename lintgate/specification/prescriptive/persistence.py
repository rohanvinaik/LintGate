"""Persistence layer for prescriptive specs and workflow records.

Save/load/index specs and workflow records to disk.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from typing import Any

from .spec import PrescriptiveSpec

# ── Persistence ───────────────────────────────────────────────────────

_SPEC_DIR = ".lintgate/prescriptive_specs"


def _target_hash(target_key: str) -> str:
    return hashlib.sha256(target_key.encode()).hexdigest()[:16]


@dataclass
class PrescriptiveWorkflowRecord:
    """Stable handle for a prescriptive spec workflow across tool calls.

    Persisted alongside the spec so every tool in the chain reads/writes
    the same record using target_key as the primary identity.
    """

    spec_id: str
    target_key: str
    state: str = (
        "composed"  # composed|compiled|materialized|implementing|verifying|converging|complete
    )

    # Accumulated artifacts
    projected_claims: list[dict] = field(default_factory=list)
    compiled_targets_path: str = ""
    materialized_test_path: str = ""
    expected_kill_set: dict[str, bool] = field(default_factory=dict)

    # Evidence state
    structural_evidence: list[dict] = field(default_factory=list)
    behavioral_evidence: list[dict] = field(default_factory=list)
    convergence_signal: dict = field(default_factory=dict)

    # Generation tracking
    generation_mode: str = (
        ""  # symbolic_only|symbolic_then_verify|symbolic_then_llm_repair|llm_direct|manual_contract
    )

    # Routing
    recommended_next_action: str = ""
    recommended_next_args: dict = field(default_factory=dict)

    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "spec_id": self.spec_id,
            "target_key": self.target_key,
            "state": self.state,
            "projected_claims": self.projected_claims,
            "compiled_targets_path": self.compiled_targets_path,
            "materialized_test_path": self.materialized_test_path,
            "expected_kill_set": self.expected_kill_set,
            "structural_evidence": self.structural_evidence,
            "behavioral_evidence": self.behavioral_evidence,
            "convergence_signal": self.convergence_signal,
            "generation_mode": self.generation_mode,
            "recommended_next_action": self.recommended_next_action,
            "recommended_next_args": self.recommended_next_args,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PrescriptiveWorkflowRecord:
        return cls(
            spec_id=str(data.get("spec_id", "")),
            target_key=str(data.get("target_key", "")),
            state=str(data.get("state", "composed")),
            projected_claims=data.get("projected_claims", []),
            compiled_targets_path=str(data.get("compiled_targets_path", "")),
            materialized_test_path=str(data.get("materialized_test_path", "")),
            expected_kill_set=data.get("expected_kill_set", {}),
            structural_evidence=data.get("structural_evidence", []),
            behavioral_evidence=data.get("behavioral_evidence", []),
            convergence_signal=data.get("convergence_signal", {}),
            generation_mode=str(data.get("generation_mode", "")),
            recommended_next_action=str(data.get("recommended_next_action", "")),
            recommended_next_args=data.get("recommended_next_args", {}),
            created_at=float(data.get("created_at", 0.0)),
            updated_at=float(data.get("updated_at", 0.0)),
        )


def save_workflow_record(project_root: str, record: PrescriptiveWorkflowRecord) -> None:
    """Save a PrescriptiveWorkflowRecord alongside its spec."""
    spec_dir = os.path.join(project_root, _SPEC_DIR)
    os.makedirs(spec_dir, exist_ok=True)
    h = _target_hash(record.target_key)
    record.updated_at = time.time()
    path = os.path.join(spec_dir, f"{h}_workflow.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(record.to_dict(), f, indent=2)


def load_workflow_record(project_root: str, target_key: str) -> PrescriptiveWorkflowRecord | None:
    """Load a PrescriptiveWorkflowRecord by target_key."""
    spec_dir = os.path.join(project_root, _SPEC_DIR)
    h = _target_hash(target_key)
    path = os.path.join(spec_dir, f"{h}_workflow.json")
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return PrescriptiveWorkflowRecord.from_dict(data)
    except (OSError, ValueError):
        return None


def save_spec(project_root: str, spec: PrescriptiveSpec) -> None:
    """Save a PrescriptiveSpec to disk and update the index."""
    spec_dir = os.path.join(project_root, _SPEC_DIR)
    os.makedirs(spec_dir, exist_ok=True)

    h = _target_hash(spec.target_key)
    spec_path = os.path.join(spec_dir, f"{h}.json")
    with open(spec_path, "w", encoding="utf-8") as f:
        json.dump(spec.to_dict(), f, indent=2)

    # Update index
    index_path = os.path.join(spec_dir, "index.json")
    index = _load_index(index_path)
    index[spec.target_key] = spec.spec_id
    with open(index_path, "w", encoding="utf-8") as f:
        json.dump(index, f, indent=2)


def load_spec(project_root: str, target_key: str) -> PrescriptiveSpec | None:
    """Load a PrescriptiveSpec by target_key."""
    spec_dir = os.path.join(project_root, _SPEC_DIR)
    h = _target_hash(target_key)
    spec_path = os.path.join(spec_dir, f"{h}.json")
    if not os.path.isfile(spec_path):
        return None
    with open(spec_path, encoding="utf-8") as f:
        data = json.load(f)
    return PrescriptiveSpec.from_dict(data)


def load_all_specs(project_root: str) -> dict[str, PrescriptiveSpec]:
    """Load all PrescriptiveSpecs from disk."""
    spec_dir = os.path.join(project_root, _SPEC_DIR)
    if not os.path.isdir(spec_dir):
        return {}
    result: dict[str, PrescriptiveSpec] = {}
    for fname in os.listdir(spec_dir):
        if fname == "index.json" or not fname.endswith(".json"):
            continue
        fpath = os.path.join(spec_dir, fname)
        try:
            with open(fpath, encoding="utf-8") as f:
                data = json.load(f)
            spec = PrescriptiveSpec.from_dict(data)
            result[spec.target_key] = spec
        except (OSError, ValueError, KeyError):
            continue
    return result


def load_spec_index(project_root: str) -> dict[str, str]:
    """Load spec index (target_key → spec_id) — fast, no full spec load."""
    index_path = os.path.join(project_root, _SPEC_DIR, "index.json")
    return _load_index(index_path)


def spec_coverage(project_root: str, function_keys: list[str]) -> dict[str, Any]:
    """Compute prescriptive spec coverage over a set of function keys."""
    index = load_spec_index(project_root)
    covered = [k for k in function_keys if k in index]
    total = len(function_keys)
    return {
        "total_functions": total,
        "covered": len(covered),
        "coverage_ratio": len(covered) / total if total else 0.0,
        "uncovered": [k for k in function_keys if k not in index],
    }


def _load_index(index_path: str) -> dict[str, str]:
    if not os.path.isfile(index_path):
        return {}
    try:
        with open(index_path, encoding="utf-8") as f:
            result: dict[str, str] = json.load(f)
            return result
    except (OSError, ValueError):
        return {}
