"""SessionKnowledge — unified project-specific persistent memory.

Unifies Facts, Patterns, and Calibration into a single store that survives
session expiry. Handles asymmetric linear decay and legacy migration.

Storage: ~/.claude/lintgate/knowledge/knowledge_<project_hash>.json
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from lintgate.controlplane.behavior_types import BehaviorCompass

KNOWLEDGE_DIR = Path.home() / ".claude" / "lintgate" / "knowledge"
LEGACY_SESSION_DIR = Path.home() / ".claude" / "lintgate" / "session"


@dataclass
class SessionKnowledge:
    """Unified container for all cross-session project knowledge."""

    version: str = "1.0.0"
    project_root: str = ""
    last_active: float = field(default_factory=time.time)

    # Calibration (Behavioral Compass State)
    compass_state: dict[str, Any] = field(default_factory=dict)

    # Patterns (Resolution Repertoire)
    repertoire: list[dict[str, Any]] = field(default_factory=list)

    # Facts (Last session context / Transfer packets)
    facts: dict[str, Any] = field(default_factory=dict)

    # Tracking metrics for observability
    survival_ratio: float = 1.0
    knowledge_staleness_hrs: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SessionKnowledge:
        return cls(
            version=data.get("version", "1.0.0"),
            project_root=data.get("project_root", ""),
            last_active=data.get("last_active", time.time()),
            compass_state=data.get("compass_state", {}),
            repertoire=data.get("repertoire", []),
            facts=data.get("facts", {}),
            survival_ratio=data.get("survival_ratio", 1.0),
            knowledge_staleness_hrs=data.get("knowledge_staleness_hrs", 0.0),
        )


class KnowledgeManager:
    """Orchestrates loading, saving, decay, and migration of SessionKnowledge."""

    def __init__(self, project_root: str):
        self.project_root = project_root
        self.knowledge_path = self._get_path(project_root)

    def load(self) -> SessionKnowledge:
        """Load, migrate, and decay knowledge for the project."""
        knowledge = self._read_from_disk()

        if knowledge is None:
            knowledge = self._migrate_legacy()

        if knowledge is None:
            knowledge = SessionKnowledge(project_root=self.project_root)

        # Apply inter-session decay
        self.decay_knowledge(knowledge)
        return knowledge

    def save(self, knowledge: SessionKnowledge):
        """Persist knowledge to disk."""
        knowledge.last_active = time.time()
        KNOWLEDGE_DIR.mkdir(parents=True, exist_ok=True)
        try:
            with open(self.knowledge_path, "w") as f:
                json.dump(knowledge.to_dict(), f, indent=2)
        except OSError:
            pass

    def decay_knowledge(self, knowledge: SessionKnowledge):
        """Apply asymmetric linear decay based on elapsed time."""
        now = time.time()
        elapsed_hrs = (now - knowledge.last_active) / 3600.0
        knowledge.knowledge_staleness_hrs = elapsed_hrs

        if elapsed_hrs <= 0.01:
            return

        # 1. Decay Compass Hypotheses (Calibration)
        if knowledge.compass_state:
            compass = BehaviorCompass.from_dict(knowledge.compass_state)
            initial_count = len(
                [h for h in compass.hypotheses if h.status != "expired"]
            )

            for hyp in compass.hypotheses:
                if hyp.status == "expired":
                    continue

                # Asymmetric rates: confirmed decay slower
                is_confirmed = hyp.status == "confirmed" or hyp.confidence >= 0.7
                rate = 0.01 if is_confirmed else 0.08
                floor = 0.3 if is_confirmed else 0.0

                hyp.confidence = max(floor, hyp.confidence - rate * elapsed_hrs)

                if hyp.confidence < 0.1 and not is_confirmed:
                    hyp.status = "expired"

            # Decay Compliance Rate toward 1.0
            comp_rate = compass.nudges.compliance_rate
            if comp_rate < 1.0:
                compass.nudges.compliance_rate = min(
                    1.0, comp_rate + 0.005 * elapsed_hrs
                )
            elif comp_rate > 1.0:
                compass.nudges.compliance_rate = max(
                    1.0, comp_rate - 0.005 * elapsed_hrs
                )

            # Prune and re-serialize
            compass.hypotheses = [
                h for h in compass.hypotheses if h.status != "expired"
            ]
            knowledge.compass_state = compass.to_dict()

            final_count = len(compass.hypotheses)
            knowledge.survival_ratio = (
                final_count / initial_count if initial_count > 0 else 1.0
            )

    def _read_from_disk(self) -> SessionKnowledge | None:
        if not self.knowledge_path.exists():
            return None
        try:
            with open(self.knowledge_path) as f:
                data = json.load(f)
            return SessionKnowledge.from_dict(data)
        except (json.JSONDecodeError, OSError, KeyError):
            return None

    def _migrate_legacy(self) -> SessionKnowledge | None:
        """Attempt to migrate facts/patterns from legacy session files."""
        project_hash = hashlib.sha256(self.project_root.encode()).hexdigest()[:16]
        legacy_path = LEGACY_SESSION_DIR / f"{project_hash}.json"

        if not legacy_path.exists():
            return None

        try:
            with open(legacy_path) as f:
                data = json.load(f)

            return SessionKnowledge(
                project_root=self.project_root,
                last_active=data.get("last_active", time.time()),
                compass_state=data.get("behavior_compass", {}),
                repertoire=data.get("resolution_repertoire", []),
            )
        except Exception:
            return None

    def _get_path(self, project_root: str) -> Path:
        proj_hash = hashlib.sha256(project_root.encode()).hexdigest()[:16]
        return KNOWLEDGE_DIR / f"knowledge_{proj_hash}.json"
