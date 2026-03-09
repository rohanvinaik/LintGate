"""C4: Bootstrap state — persistent, resumable state for the bootstrap pipeline.

Execution model:
- Idempotent: re-running skips completed phases per ``files_processed``
- Resumable: state persists to disk; interrupted runs continue from last phase
- Single-run: lock file prevents concurrent pipelines on the same project
- Heartbeat: long phases call ``heartbeat()`` to prevent stale lock detection

Storage: ``~/.claude/lintgate/bootstrap/{project_hash}.json``
Lock:    ``~/.claude/lintgate/bootstrap/{project_hash}.lock``
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

BOOTSTRAP_DIR = Path.home() / ".claude" / "lintgate" / "bootstrap"

# Ordered pipeline phases — used for phase comparison
PHASES = (
    "not_started",
    "algebra",
    "skeletons",
    "properties",
    "contracts",
    "complete",
)


@dataclass
class BootstrapArtifacts:
    """Artifacts produced by the bootstrap pipeline."""

    generated_test_dir: str = "tests/generated"
    proposal_output_path: str | None = None
    test_files: list[str] = field(default_factory=list)


@dataclass
class BootstrapState:
    """Persistent, resumable state for the bootstrap pipeline."""

    run_id: str = ""
    project_root: str = ""
    status: str = "idle"  # "idle" | "queued" | "running" | "failed" | "complete"
    phase: str = "not_started"
    started_at: float | None = None
    last_heartbeat: float | None = None
    error: str | None = None
    files_processed: dict[str, str] = field(default_factory=dict)  # file → last completed phase
    tests_generated: int = 0
    artifacts: BootstrapArtifacts = field(default_factory=BootstrapArtifacts)
    last_updated: float = 0.0

    # ── Persistence ──────────────────────────────────────────────────

    @classmethod
    def load(cls, project_root: str) -> BootstrapState:
        """Load state from disk, returning fresh state if none exists."""
        state_path = _state_path(project_root)
        if not state_path.exists():
            return cls(project_root=project_root)
        try:
            with open(state_path) as f:
                data = json.load(f)
            artifacts_data = data.pop("artifacts", {})
            # Backward compatibility: drop archived mutation artifacts/state.
            artifacts_data.pop("mutation_output_path", None)

            if data.get("phase") == "mutation":
                data["phase"] = "contracts"

            files_processed = data.get("files_processed")
            if isinstance(files_processed, dict):
                data["files_processed"] = {
                    k: ("contracts" if v == "mutation" else v) for k, v in files_processed.items()
                }

            state = cls(**{k: v for k, v in data.items() if k != "artifacts"})
            state.artifacts = BootstrapArtifacts(**artifacts_data)
            return state
        except (json.JSONDecodeError, OSError, TypeError):
            return cls(project_root=project_root)

    def save(self) -> None:
        """Persist state to disk."""
        BOOTSTRAP_DIR.mkdir(parents=True, exist_ok=True)
        state_path = _state_path(self.project_root)
        self.last_updated = time.time()
        data = asdict(self)
        try:
            with open(state_path, "w") as f:
                json.dump(data, f, indent=2)
        except OSError:
            pass

    # ── Locking ──────────────────────────────────────────────────────

    def acquire_lock(self) -> bool:
        """Acquire an exclusive lock for this project's bootstrap pipeline.

        Returns True if lock acquired, False if another pipeline is running.
        Stale locks (PID dead or heartbeat >120s old) are automatically recovered.
        """
        lock_path = _lock_path(self.project_root)
        BOOTSTRAP_DIR.mkdir(parents=True, exist_ok=True)

        if lock_path.exists():
            if not _is_stale_lock(lock_path):
                return False
            # Stale lock — remove and proceed
            lock_path.unlink(missing_ok=True)

        # Write lock file with PID and timestamp
        try:
            with open(lock_path, "w") as f:
                json.dump({"pid": os.getpid(), "timestamp": time.time()}, f)
            return True
        except OSError:
            return False

    def release_lock(self) -> None:
        """Release the lock for this project."""
        lock_path = _lock_path(self.project_root)
        lock_path.unlink(missing_ok=True)

    def heartbeat(self) -> None:
        """Update heartbeat timestamp to prevent stale lock detection."""
        self.last_heartbeat = time.time()
        lock_path = _lock_path(self.project_root)
        if lock_path.exists():
            try:
                with open(lock_path, "w") as f:
                    json.dump({"pid": os.getpid(), "timestamp": time.time()}, f)
            except OSError:
                pass

    # ── Phase management ─────────────────────────────────────────────

    def phase_completed(self, phase: str) -> bool:
        """Check if a phase has already been completed."""
        if phase not in PHASES or self.phase not in PHASES:
            return False
        return PHASES.index(self.phase) >= PHASES.index(phase)

    def advance_phase(self, phase: str) -> None:
        """Advance to the next phase and save state."""
        self.phase = phase
        self.save()

    def to_summary(self) -> dict:
        """Return a summary suitable for MCP tool output."""
        return {
            "run_id": self.run_id,
            "status": self.status,
            "phase": self.phase,
            "phase_index": PHASES.index(self.phase) if self.phase in PHASES else -1,
            "total_phases": len(PHASES) - 1,  # exclude "not_started"
            "files_processed": len(self.files_processed),
            "tests_generated": self.tests_generated,
            "test_files": self.artifacts.test_files,
            "error": self.error,
            "started_at": self.started_at,
            "last_updated": self.last_updated,
        }


# ── Internal helpers ─────────────────────────────────────────────────


def _project_hash(project_root: str) -> str:
    """Compute a stable hash of the project root path."""
    return hashlib.sha256(project_root.encode()).hexdigest()[:16]


def _state_path(project_root: str) -> Path:
    return BOOTSTRAP_DIR / f"{_project_hash(project_root)}.json"


def _lock_path(project_root: str) -> Path:
    return BOOTSTRAP_DIR / f"{_project_hash(project_root)}.lock"


def _is_stale_lock(lock_path: Path) -> bool:
    """Check if a lock file is stale (PID dead or heartbeat >120s old)."""
    try:
        with open(lock_path) as f:
            data = json.load(f)
        pid = data.get("pid")
        timestamp = data.get("timestamp", 0)

        # Check if PID is still alive
        if pid is not None:
            try:
                os.kill(pid, 0)  # Signal 0 = check existence only
            except ProcessLookupError:
                return True  # PID is dead — stale lock
            except PermissionError:
                pass  # Process exists but owned by another user — not stale

        # Check heartbeat age
        age = time.time() - float(timestamp)
        return age > 120.0

    except (json.JSONDecodeError, OSError):
        return True  # Corrupt lock file — treat as stale
