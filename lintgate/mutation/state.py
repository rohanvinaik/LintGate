"""State persistence model for mutation testing coverage depth.

Maintains the state of which functions have been mutated to what level of depth,
using source and test hashes to determine when runs are stale and should be
invalidated or re-executed.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

try:
    from filelock import FileLock
except ImportError:

    class FileLock:
        def __init__(self, *args, **kwargs):
            # No-op fallback when optional `filelock` dependency is unavailable.
            self._noop = True

        def __enter__(self):
            return self

        def __exit__(self, *args):
            # Keep context-manager semantics without side effects.
            return None


class CoverageDepth(str, Enum):
    """The depth of mutation testing executed against a target."""

    NONE = "none"
    SAMPLED = "sampled"  # Quick inline run with limited budget
    PROFILED = "profiled"  # Exhaustive background run


class ConfidenceLevel(str, Enum):
    """Confidence in the mutation signal."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass
class FunctionMutationState:
    """Persistent state for a single function's mutation coverage."""

    function_name: str
    file_path: str
    code_hash: str
    test_hash: str

    depth: CoverageDepth = CoverageDepth.NONE
    confidence: ConfidenceLevel = ConfidenceLevel.LOW

    last_run_ts: float = field(default_factory=time.time)
    source: str = "unknown"

    # Snapshot of the latest metrics for this function to avoid re-parsing massive outputs
    killed: int = 0
    survived: int = 0
    timeout: int = 0
    total: int = 0

    # Tier 2 breakdown (Item 1A)
    killed_by_assertion: int = 0
    killed_by_crash: int = 0

    # Detailed breakdown (v2)
    survived_by_category: dict[str, int] = field(default_factory=dict)

    @property
    def survival_rate(self) -> float:
        """0.0 = fully specified, 1.0 = no specification."""
        if self.total == 0:
            return 1.0
        return self.survived / self.total

    @property
    def specification_strength(self) -> float:
        """Ratio of assertion-kills to total kills.

        0.0 = all crash-kills (proves crash-freedom only).
        1.0 = all assertion-kills (proves specification completeness).
        """
        total_killed = self.killed_by_assertion + self.killed_by_crash
        if total_killed == 0:
            return 0.0
        return self.killed_by_assertion / total_killed

    @property
    def is_gateable(self) -> bool:
        """Whether this state has sufficient authority to gate optimization hints.

        Full-depth profiled data always gates. Sampled data gates only with HIGH confidence.
        """
        if self.depth == CoverageDepth.PROFILED:
            return True
        return bool(self.depth == CoverageDepth.SAMPLED and self.confidence == ConfidenceLevel.HIGH)

    def to_dict(self) -> dict[str, Any]:
        """Serialize for storage."""
        d = asdict(self)
        d["depth"] = self.depth.value
        d["confidence"] = self.confidence.value
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FunctionMutationState:
        """Deserialize from storage."""
        # Handle enums
        if "depth" in data:
            data["depth"] = CoverageDepth(data["depth"])
        if "confidence" in data:
            data["confidence"] = ConfidenceLevel(data["confidence"])

        return cls(**data)


class MutationStateManager:
    """Manages the lifecycle and persistence of mutation state."""

    SCHEMA_VERSION = 2

    def __init__(self, storage_path: str | Path):
        self.storage_path = Path(storage_path)
        self.state: dict[str, FunctionMutationState] = {}
        self.last_updated: float = 0.0
        self.load()

    def load(self) -> None:
        """Load state from disk with schema-aware migration."""
        if not self.storage_path.exists():
            self.state = {}
            return

        try:
            lock_path = str(self.storage_path) + ".lock"
            with FileLock(lock_path, timeout=5):
                raw_content = self.storage_path.read_text("utf-8")
                if not raw_content.strip():
                    self.state = {}
                    return

                data = json.loads(raw_content)
        except (json.JSONDecodeError, OSError):
            self.state = {}
            return

        # v1 vs v2 discrimination
        if isinstance(data, dict) and "schema_version" in data:
            # v2+ schema
            self._load_v2(data)
        else:
            self._load_v1(data)
            # Auto-save migration
            self.save()

    def _load_v1(self, data: dict[str, Any]) -> None:
        """Migrate v1 data (Dict[str, state_dict]) to manager."""
        for k, v in data.items():
            try:
                self.state[k] = FunctionMutationState.from_dict(v)
            except (KeyError, TypeError, ValueError):
                continue
        self.last_updated = time.time()

    def _load_v2(self, data: dict[str, Any]) -> None:
        """Load v2 data (Dict with schema_version, states, etc)."""
        states_raw = data.get("states", {})
        for k, v in states_raw.items():
            try:
                self.state[k] = FunctionMutationState.from_dict(v)
            except (KeyError, TypeError, ValueError):
                continue
        self.last_updated = data.get("last_updated", time.time())

    def save(self) -> None:
        """Persist state to disk using latest schema (v2)."""
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        raw_states = {k: v.to_dict() for k, v in self.state.items()}

        payload = {
            "schema_version": self.SCHEMA_VERSION,
            "last_updated": time.time(),
            "states": raw_states,
        }

        try:
            lock_path = str(self.storage_path) + ".lock"
            with FileLock(lock_path, timeout=5):
                self.storage_path.write_text(json.dumps(payload, indent=2), "utf-8")
        except OSError:
            pass  # Fail gracefully if we can't write (e.g. strict CI env)

    def get_state(self, function_id: str) -> FunctionMutationState | None:
        """Retrieve state for a fully qualified function identifier."""
        return self.state.get(function_id)

    def update_state(
        self,
        state: FunctionMutationState,
        project_root: str | None = None,
    ) -> None:
        """Update or insert a state record.

        When *project_root* is supplied the key is produced via
        :func:`canonicalize_function_id` so that it matches the manifest's
        ``relpath::qualname`` convention.  Without it the legacy
        ``file_path::function_name`` key is used (backward-compatible).
        """
        if project_root is not None:
            func_id = canonicalize_function_id(state.file_path, state.function_name, project_root)
        else:
            func_id = f"{state.file_path}::{state.function_name}"
        self.state[func_id] = state

    def requires_run(
        self,
        function_id: str,
        current_code_hash: str,
        current_test_hash: str,
        target_depth: CoverageDepth,
    ) -> bool:
        """Determine if a function requires mutation execution.

        A run is required if:
        1. We have no prior state.
        2. The codebase has drifted (code_hash or test_hash changed).
        3. The target execution depth is strictly greater than the currently achieved depth.
        """
        st = self.get_state(function_id)
        if not st:
            return True

        if st.code_hash != current_code_hash or st.test_hash != current_test_hash:
            return True

        # If we want a deep profile but only have a sample, we must run.
        return target_depth == CoverageDepth.PROFILED and st.depth != CoverageDepth.PROFILED


def _path_to_mutmut_module(path: str, project_root: str | None = None) -> str:
    """Convert a file path to the module name mutmut v3 uses internally.

    Replicates mutmut v3's ``get_mutant_name()`` transformations
    (``mutmut/__main__.py:336-343``):

    1. Strip ``.py`` extension, replace path separators with dots.
    2. Strip leading ``src.`` prefix (src-layout convention).
    3. Collapse ``.__init__.`` to ``.`` / strip trailing ``.__init__``.

    When *project_root* is provided, the path is first relativized so that
    absolute paths (as stored in ``FunctionMutationState.file_path``) produce
    correct module names.

    Examples::

        >>> _path_to_mutmut_module("lintgate/mutation/engine.py")
        'lintgate.mutation.engine'
        >>> _path_to_mutmut_module("src/model_atlas/spreading.py")
        'model_atlas.spreading'
        >>> _path_to_mutmut_module("src/model_atlas/__init__.py")
        'model_atlas'
    """
    if project_root is not None:
        path = os.path.relpath(path, project_root)
    module_name = os.path.splitext(path)[0].replace(os.sep, ".").replace("/", ".")
    if module_name.startswith("."):
        module_name = module_name[1:]
    # mutmut v3: strip src. prefix
    if module_name.startswith("src."):
        module_name = module_name[4:]
    # mutmut v3: collapse .__init__. to . and strip trailing .__init__
    module_name = module_name.replace(".__init__.", ".")
    if module_name.endswith(".__init__"):
        module_name = module_name[: -len(".__init__")]
    return module_name


def canonicalize_function_id(
    file_path: str,
    function_name: str,
    project_root: str,
) -> str:
    """Produce a canonical function identifier: ``relpath::qualname``.

    This is the single source of truth for function identity across the
    manifest (``manifest.py``) and mutation state (``state.py``).  Both
    subsystems MUST use this helper so that lookups never silently miss.

    The path component is always ``os.path.relpath(file_path, project_root)``
    which matches the keying convention in ``manifest.py::_scan_file()``.
    The qualname component preserves class prefixes (e.g. ``Class.method``).

    Examples::

        canonicalize_function_id(
            "/home/user/proj/src/core.py", "Engine.run", "/home/user/proj"
        )
        # -> "src/core.py::Engine.run"

        canonicalize_function_id(
            "src/core.py", "compute", "/home/user/proj"
        )
        # -> "src/core.py::compute"
    """
    # Normalize to absolute so relpath works even when file_path is already relative
    abs_file = os.path.abspath(file_path)
    abs_root = os.path.abspath(project_root)
    relpath = os.path.relpath(abs_file, abs_root)
    return f"{relpath}::{function_name}"


def compute_content_hash(content: str) -> str:
    """Compute a stable hash for a block of code or test file."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()
