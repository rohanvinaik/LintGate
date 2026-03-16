"""Test archetype selector — matches source files to test patterns.

Uses lightweight AST analysis + import signal detection to determine
which test archetypes are relevant for a given source file.

This is NOT a test generator — it produces structured ArchetypeMatch
objects that describe WHAT to test, not HOW. The skeleton_generator
(Phase 3B) uses these matches to produce actual test stubs.

Design: AST-based, no LLM calls, deterministic. Runs in <50ms per file.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field

# ── Types ─────────────────────────────────────────────────────────


@dataclass
class FunctionInfo:
    """Extracted info about a function/method."""

    name: str
    args: list[str] = field(default_factory=list)
    has_type_annotations: bool = False
    has_return_type: bool = False
    has_docstring: bool = False
    raises: list[str] = field(default_factory=list)
    decorators: list[str] = field(default_factory=list)
    is_method: bool = False
    class_name: str | None = None


@dataclass
class ClassInfo:
    """Extracted info about a class."""

    name: str
    bases: list[str] = field(default_factory=list)
    has_init: bool = False
    init_defaults: int = 0
    methods: list[str] = field(default_factory=list)
    is_dataclass: bool = False
    mutable_fields: list[str] = field(default_factory=list)


@dataclass
class SourceSignals:
    """Signals extracted from AST analysis of a source file."""

    functions: list[FunctionInfo] = field(default_factory=list)
    classes: list[ClassInfo] = field(default_factory=list)
    imports: set[str] = field(default_factory=set)
    import_modules: set[str] = field(default_factory=set)

    # Boolean signals
    has_try_except: bool = False
    has_file_io: bool = False
    has_subprocess: bool = False
    has_http_imports: bool = False
    has_database_ops: bool = False
    has_dataclasses: bool = False
    has_yaml_toml_json: bool = False
    has_encode_decode: bool = False
    has_to_from_pairs: bool = False
    has_guard_clauses: bool = False
    has_typed_functions: bool = False
    has_value_errors: bool = False
    has_stateful_classes: bool = False
    has_complex_conditionals: bool = False
    has_mutable_collections: bool = False
    has_serialize_patterns: bool = False
    has_json_dump_load: bool = False


@dataclass
class ArchetypeMatch:
    """Result of matching a source file to a test archetype."""

    name: str
    confidence: float  # 0.0 - 1.0
    reason: str
    relevant_functions: list[str] = field(default_factory=list)
    relevant_classes: list[str] = field(default_factory=list)


# ── Public API ───────────────────────────────────────────────────────────


def select_archetypes(
    source_file: str,
    project_root: str = "",
) -> list[ArchetypeMatch]:
    """Select applicable test archetypes for a source file.

    Reads the source file, extracts AST signals, and matches them
    against the 7 v1 archetypes. Returns a ranked list sorted by
    confidence (highest first).

    Args:
        source_file: Path to the Python source file to analyze.
        project_root: Project root (used for import resolution, optional).

    Returns:
        List of ArchetypeMatch objects with confidence > 0.3,
        sorted by confidence descending.
    """
    try:
        with open(source_file) as f:
            source = f.read()
    except (OSError, UnicodeDecodeError):
        return [
            ArchetypeMatch(
                name="input_validation",
                confidence=0.3,
                reason="Could not read file; default archetype",
            )
        ]

    try:
        tree = ast.parse(source, filename=source_file)
    except SyntaxError:
        return [
            ArchetypeMatch(
                name="input_validation",
                confidence=0.3,
                reason="Syntax error; default archetype",
            )
        ]

    signals = _extract_signals(tree, source)
    matches = _match_archetypes(signals)

    # Sort by confidence descending
    matches.sort(key=lambda m: -m.confidence)

    # Filter to confidence >= 0.3 (input_validation baseline is 0.3)
    return [m for m in matches if m.confidence >= 0.3]


def extract_signals(source_file: str) -> SourceSignals:
    """Extract AST signals from a source file (public for testing)."""
    try:
        with open(source_file) as f:
            source = f.read()
        tree = ast.parse(source, filename=source_file)
        return _extract_signals(tree, source)
    except (OSError, SyntaxError):
        return SourceSignals()


from .test_archetype_impl import (  # noqa: F401, E402
    _attr_name,
    _call_name,
    _decorator_name,
    _derive_composite_signals,
    _extract_signals,
    _match_archetypes,
    _score_configuration,
    _score_error_handling,
    _score_input_validation,
    _score_mock_isolation,
    _score_regression,
    _score_round_trip,
    _score_state_invariant,
    _SignalVisitor,
)
