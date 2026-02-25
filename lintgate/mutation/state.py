"""State persistence model for mutation testing coverage depth.

Maintains the state of which functions have been mutated to what level of depth,
using source and test hashes to determine when runs are stale and should be
invalidated or re-executed.
"""

from __future__ import annotations

import hashlib
import json
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


class SignalQuality(str, Enum):
    """Quality tier for mutation signal based on depth/coverage.

    Maps deterministically from CoverageDepth for backward compatibility.
    """

    NONE = "none"  # CoverageDepth.NONE -> no signal
    SAMPLED_LOW = "sampled_low"  # CoverageDepth.SAMPLED -> low confidence sample
    SAMPLED_HIGH = "sampled_high"  # High-confidence sampled run (future)
    PROFILED = "profiled"  # CoverageDepth.PROFILED -> full coverage

    @classmethod
    def from_depth(cls, depth: CoverageDepth) -> SignalQuality:
        """Map CoverageDepth to SignalQuality deterministically."""
        if depth == CoverageDepth.NONE:
            return cls.NONE
        elif depth == CoverageDepth.SAMPLED:
            return cls.SAMPLED_LOW
        elif depth == CoverageDepth.PROFILED:
            return cls.PROFILED
        return cls.NONE


class ConfidenceLevel(str, Enum):
    """Confidence in the mutation signal."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass
class SurvivorSite:
    """Location-aware survivor record for detailed mutation analysis.

    Tracks the specific line/column where surviving mutants were found,
    enabling targeted test generation and function decomposition.
    """

    line: int
    column: int
    category: str
    mutant_id: str
    operator: str = "unknown"  # Mutation operator name (e.g., 'add', 'sub', 'conditional')

    def to_dict(self) -> dict[str, Any]:
        """Serialize for storage."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SurvivorSite | None:
        """Deserialize from storage. Returns None if data is invalid or empty."""
        if not isinstance(data, dict) or not data:
            return None

        try:
            line = int(data.get("line", 0))
            column = int(data.get("column", 0))
            category = str(data.get("category", ""))
            mutant_id = str(data.get("mutant_id", ""))
            operator = str(data.get("operator", "unknown"))

            # Require at least one meaningful field to consider it valid
            if line <= 0 and column <= 0 and not category and not mutant_id:
                return None

            return cls(
                line=line, column=column, category=category, mutant_id=mutant_id, operator=operator
            )
        except (TypeError, ValueError):
            return None


@dataclass(init=False)
class FunctionMutationState:
    """Persistent state for a single function's mutation coverage."""

    function_name: str
    file_path: str
    code_hash: str
    test_hash: str

    depth: CoverageDepth = CoverageDepth.NONE
    confidence: ConfidenceLevel = ConfidenceLevel.LOW
    # Internal fields for signal_quality handling
    # _signal_quality_set tracks whether it was explicitly provided
    _signal_quality_set: bool = field(default=False, repr=False)
    _signal_quality_value: SignalQuality = field(default=SignalQuality.NONE, repr=False)

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

    # Location-aware survivor records (v3)
    survivor_sites: list[SurvivorSite] = field(default_factory=list)

    def __init__(
        self,
        function_name: str,
        file_path: str,
        code_hash: str,
        test_hash: str,
        depth: CoverageDepth = CoverageDepth.NONE,
        confidence: ConfidenceLevel = ConfidenceLevel.LOW,
        signal_quality: SignalQuality | None = None,
        last_run_ts: float = 0.0,
        source: str = "unknown",
        killed: int = 0,
        survived: int = 0,
        timeout: int = 0,
        total: int = 0,
        killed_by_assertion: int = 0,
        killed_by_crash: int = 0,
        survived_by_category: dict | None = None,
        survivor_sites: list | None = None,
    ):
        self.function_name = function_name
        self.file_path = file_path
        self.code_hash = code_hash
        self.test_hash = test_hash
        self.depth = depth
        self.confidence = confidence

        # Handle signal_quality - derive from depth if not provided
        if signal_quality is not None:
            self._signal_quality_set = True
            self._signal_quality_value = signal_quality
        else:
            self._signal_quality_set = False
            self._signal_quality_value = SignalQuality.from_depth(depth)

        self.last_run_ts = last_run_ts if last_run_ts else time.time()
        self.source = source
        self.killed = killed
        self.survived = survived
        self.timeout = timeout
        self.total = total
        self.killed_by_assertion = killed_by_assertion
        self.killed_by_crash = killed_by_crash
        self.survived_by_category = survived_by_category if survived_by_category else {}
        self.survivor_sites = survivor_sites if survivor_sites else []

    @classmethod
    def create(
        cls,
        function_name: str,
        file_path: str,
        code_hash: str,
        test_hash: str,
        depth: CoverageDepth = CoverageDepth.NONE,
        confidence: ConfidenceLevel = ConfidenceLevel.LOW,
        signal_quality: SignalQuality | None = None,
    ) -> FunctionMutationState:
        """Factory method to create state with optional explicit signal_quality."""
        # Create instance with default values
        state = cls(
            function_name=function_name,
            file_path=file_path,
            code_hash=code_hash,
            test_hash=test_hash,
            depth=depth,
            confidence=confidence,
        )
        # If signal_quality was explicitly provided, set it
        if signal_quality is not None:
            state._signal_quality_value = signal_quality
            state._signal_quality_set = True
        return state

    @property
    def signal_quality(self) -> SignalQuality:
        """Get signal_quality, deriving from depth if not explicitly set."""
        if self._signal_quality_set:
            return self._signal_quality_value
        # Derive from depth if not explicitly set
        return SignalQuality.from_depth(self.depth)

    @signal_quality.setter
    def signal_quality(self, value: SignalQuality) -> None:
        self._signal_quality_value = value
        self._signal_quality_set = True

    @property
    def survival_rate(self) -> float:
        """0.0 = fully specified, 1.0 = no specification."""
        if self.total == 0:
            return 1.0
        return self.survived / self.total

    def to_dict(self) -> dict[str, Any]:
        """Serialize for storage."""
        d = asdict(self)
        d["depth"] = self.depth.value
        d["confidence"] = self.confidence.value
        # Use property getter to get signal_quality (handles derivation from depth)
        d["signal_quality"] = self.signal_quality.value
        # Remove internal fields from output
        d.pop("_signal_quality_set", None)
        d.pop("_signal_quality_value", None)
        # Serialize survivor_sites properly
        d["survivor_sites"] = [s.to_dict() for s in self.survivor_sites]
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FunctionMutationState:
        """Deserialize from storage."""
        # Handle enums
        if "depth" in data:
            data["depth"] = CoverageDepth(data["depth"])
        if "confidence" in data:
            data["confidence"] = ConfidenceLevel(data["confidence"])

        # Handle signal_quality with backward compatibility
        # Fail-closed: default to derived value from depth if missing or invalid
        signal_quality_set = False
        signal_quality_value = SignalQuality.NONE
        if "signal_quality" in data:
            try:
                signal_quality_value = SignalQuality(data["signal_quality"])
                signal_quality_set = True
            except (ValueError, KeyError):
                # Invalid value - will derive from depth via property
                pass

        # Remove signal_quality from data so dataclass doesn't process it
        data.pop("signal_quality", None)

        # Handle survivor_sites with backward compatibility
        # Fail-closed: drop invalid entries instead of crashing
        raw_sites = data.get("survivor_sites", [])
        if isinstance(raw_sites, list):
            survivor_sites = []
            for site_data in raw_sites:
                if isinstance(site_data, dict):
                    site = SurvivorSite.from_dict(site_data)
                    if site is not None:
                        survivor_sites.append(site)
            data["survivor_sites"] = survivor_sites
        else:
            # Backward compat: if field missing or not a list, default to empty
            data["survivor_sites"] = []

        # Create instance first
        state = cls(**data)

        # Then set internal signal_quality fields if explicit value was provided
        if signal_quality_set:
            state._signal_quality_value = signal_quality_value
            state._signal_quality_set = True

        return state


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

    def update_state(self, state: FunctionMutationState) -> None:
        """Update or insert a state record."""
        # Use fully qualified name as the identifier
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

        # If the target depth is strictly greater than the current depth, we must run.
        depth_order = {CoverageDepth.NONE: 0, CoverageDepth.SAMPLED: 1, CoverageDepth.PROFILED: 2}
        return depth_order.get(target_depth, 0) > depth_order.get(st.depth, 0)


def compute_content_hash(content: str) -> str:
    """Compute a stable hash for a block of code or test file."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()
