"""Test-topology analysis — detect mock-boundary opacity in test suites.

Identifies when mutation survival is dominated by mocked call paths rather
than genuine specification gaps. Scans test files for patch/monkeypatch
usage and compares patched symbols against the target function's outbound
calls to determine topology state.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from enum import Enum


class TopologyState(str, Enum):
    """Classification of test topology for a function under mutation."""

    NORMAL = "NORMAL"
    MOCK_BOUNDARY_DOMINANT = "MOCK_BOUNDARY_DOMINANT"
    PATCHED_INTERNAL_CALLS = "PATCHED_INTERNAL_CALLS"
    TOPOLOGY_UNKNOWN = "TOPOLOGY_UNKNOWN"


class SurvivalInterpretation(str, Enum):
    """How to interpret mutation survival given discovery + topology state."""

    MEANINGFUL = "MEANINGFUL"
    DISCOVERY_ARTIFACT = "DISCOVERY_ARTIFACT"
    MOCK_BOUNDARY_ARTIFACT = "MOCK_BOUNDARY_ARTIFACT"
    LOW_CONFIDENCE = "LOW_CONFIDENCE"


class DiscoveryState(str, Enum):
    """Classification of test discovery outcome."""

    NO_TEST_FILES = "NO_TEST_FILES"
    TEST_FILES_FOUND_NONE_LINKED = "TEST_FILES_FOUND_NONE_LINKED"
    DISCOVERY_WEAK_LINKAGE = "DISCOVERY_WEAK_LINKAGE"
    TESTS_LINKED_ZERO_KILLS = "TESTS_LINKED_ZERO_KILLS"
    DISCOVERY_IMPORT_FAILED = "DISCOVERY_IMPORT_FAILED"
    SEMANTIC_LINKAGE = "SEMANTIC_LINKAGE"
    DISCOVERY_OK = "DISCOVERY_OK"


@dataclass
class TopologyResult:
    """Result of topology analysis for a function."""

    topology_state: TopologyState = TopologyState.TOPOLOGY_UNKNOWN
    outbound_calls: list[str] = field(default_factory=list)
    patched_symbols: list[str] = field(default_factory=list)
    mocked_call_sites: list[str] = field(default_factory=list)
    patched_symbol_count: int = 0
    topology_confidence: float = 0.5

    def to_dict(self) -> dict:
        d: dict = {
            "topology_state": self.topology_state.value,
            "patched_symbol_count": self.patched_symbol_count,
            "topology_confidence": round(self.topology_confidence, 2),
        }
        if self.patched_symbols:
            d["patched_symbols"] = self.patched_symbols[:10]
        if self.mocked_call_sites:
            d["mocked_call_sites"] = self.mocked_call_sites[:10]
        return d


def analyze_topology(
    func_node: ast.FunctionDef | ast.AsyncFunctionDef,
    test_file_paths: list[str],
) -> TopologyResult:
    """Analyze test topology for a function.

    Compares the function's outbound calls against patched symbols in
    linked test files to determine if mutation survival is likely a
    mock-boundary artifact.
    """
    outbound = _extract_outbound_calls(func_node)
    if not outbound:
        return TopologyResult(
            topology_state=TopologyState.NORMAL,
            outbound_calls=[],
            topology_confidence=0.8,
        )

    all_patched: list[str] = []
    for tf in test_file_paths:
        all_patched.extend(_extract_patched_symbols(tf))

    if not all_patched:
        return TopologyResult(
            topology_state=TopologyState.NORMAL,
            outbound_calls=outbound,
            topology_confidence=0.8,
        )

    # Compare: which outbound calls are also patched?
    outbound_set = set(outbound)
    patched_set = set(all_patched)
    overlap = outbound_set & patched_set
    mocked_sites = sorted(overlap)

    patched_ratio = len(overlap) / len(outbound_set) if outbound_set else 0.0

    if patched_ratio >= 0.5:
        state = TopologyState.MOCK_BOUNDARY_DOMINANT
        confidence = min(0.9, 0.6 + patched_ratio * 0.3)
    elif overlap:
        state = TopologyState.PATCHED_INTERNAL_CALLS
        confidence = 0.7
    else:
        state = TopologyState.NORMAL
        confidence = 0.8

    return TopologyResult(
        topology_state=state,
        outbound_calls=outbound,
        patched_symbols=sorted(patched_set)[:20],
        mocked_call_sites=mocked_sites,
        patched_symbol_count=len(patched_set),
        topology_confidence=confidence,
    )


def classify_discovery_state(
    test_files_found: int,
    callables_loaded: int,
    import_failures: int,
    fallback_used: bool,
    weak_linkage_suspected: bool,
    total_killed: int,
    *,
    linkage_source: str = "",
) -> DiscoveryState:
    """Classify the discovery outcome from diagnostics."""
    # If callables were loaded (e.g. via dynamic coverage linkage),
    # skip the NO_TEST_FILES check — tests exist even if conventional
    # filename-based discovery didn't find them.
    if test_files_found == 0 and callables_loaded == 0:
        return DiscoveryState.NO_TEST_FILES
    if import_failures > 0 and callables_loaded == 0:
        return DiscoveryState.DISCOVERY_IMPORT_FAILED
    if callables_loaded == 0:
        return DiscoveryState.TEST_FILES_FOUND_NONE_LINKED
    if weak_linkage_suspected and fallback_used:
        return DiscoveryState.DISCOVERY_WEAK_LINKAGE
    if linkage_source == "semantic":
        return DiscoveryState.SEMANTIC_LINKAGE
    if callables_loaded > 0 and total_killed == 0:
        return DiscoveryState.TESTS_LINKED_ZERO_KILLS
    return DiscoveryState.DISCOVERY_OK


def interpret_survival(
    discovery_state: DiscoveryState,
    topology_state: TopologyState,
    survival_rate: float,
    *,
    weak_linkage_suspected: bool = False,
) -> SurvivalInterpretation:
    """Determine how to interpret mutation survival."""
    if discovery_state in (
        DiscoveryState.NO_TEST_FILES,
        DiscoveryState.TEST_FILES_FOUND_NONE_LINKED,
        DiscoveryState.DISCOVERY_IMPORT_FAILED,
        DiscoveryState.DISCOVERY_WEAK_LINKAGE,
    ):
        return SurvivalInterpretation.DISCOVERY_ARTIFACT
    if discovery_state == DiscoveryState.SEMANTIC_LINKAGE:
        return SurvivalInterpretation.LOW_CONFIDENCE
    if weak_linkage_suspected:
        return SurvivalInterpretation.DISCOVERY_ARTIFACT

    if topology_state == TopologyState.MOCK_BOUNDARY_DOMINANT:
        return SurvivalInterpretation.MOCK_BOUNDARY_ARTIFACT

    if discovery_state == DiscoveryState.TESTS_LINKED_ZERO_KILLS and survival_rate >= 0.9:
        return SurvivalInterpretation.LOW_CONFIDENCE

    return SurvivalInterpretation.MEANINGFUL


# ── AST extraction helpers ────────────────────────────────────────


def _extract_outbound_calls(
    func_node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> list[str]:
    """Extract names of functions called from within a function body."""
    calls: list[str] = []
    for node in ast.walk(func_node):
        if not isinstance(node, ast.Call):
            continue
        name = _call_target_name(node)
        if name and name not in calls:
            calls.append(name)
    return calls


def _call_target_name(node: ast.Call) -> str | None:
    """Extract the bare function name from a Call node."""
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def _extract_patched_symbols(test_file_path: str) -> list[str]:
    """Extract symbols that are patched/mocked in a test file.

    Detects:
    - @patch("module.symbol") and @patch.object(cls, "method")
    - with patch("module.symbol"):
    - monkeypatch.setattr(obj, "name", ...)
    """
    try:
        with open(test_file_path, encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=test_file_path)
    except (OSError, SyntaxError):
        return []

    symbols: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            _collect_patch_targets(node, symbols)
    return symbols


def _collect_patch_targets(node: ast.Call, symbols: list[str]) -> None:
    """Collect patched symbol names from a Call node."""
    # @patch("some.module.symbol") or patch("some.module.symbol")
    if _is_patch_call(node) and node.args:
        arg = node.args[0]
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            # Extract the last component: "some.module.symbol" → "symbol"
            parts = arg.value.rsplit(".", 1)
            symbols.append(parts[-1])

    # patch.object(cls, "method_name")
    if _is_patch_object_call(node) and len(node.args) >= 2:
        arg = node.args[1]
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            symbols.append(arg.value)

    # monkeypatch.setattr(obj, "name", ...)
    if _is_monkeypatch_setattr(node) and len(node.args) >= 2:
        arg = node.args[1]
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            symbols.append(arg.value)


def _is_patch_call(node: ast.Call) -> bool:
    """Check if a Call is unittest.mock.patch(...)."""
    func = node.func
    if isinstance(func, ast.Name) and func.id == "patch":
        return True
    return isinstance(func, ast.Attribute) and func.attr == "patch"


def _is_patch_object_call(node: ast.Call) -> bool:
    """Check if a Call is patch.object(...)."""
    func = node.func
    if isinstance(func, ast.Attribute) and func.attr == "object":
        value = func.value
        if isinstance(value, ast.Name) and value.id == "patch":
            return True
        if isinstance(value, ast.Attribute) and value.attr == "patch":
            return True
    return False


def _is_monkeypatch_setattr(node: ast.Call) -> bool:
    """Check if a Call is monkeypatch.setattr(...)."""
    func = node.func
    return isinstance(func, ast.Attribute) and func.attr == "setattr"
