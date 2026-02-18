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
import os
from dataclasses import dataclass, field
from typing import Any


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
        return [ArchetypeMatch(
            name="input_validation",
            confidence=0.3,
            reason="Could not read file; default archetype",
        )]

    try:
        tree = ast.parse(source, filename=source_file)
    except SyntaxError:
        return [ArchetypeMatch(
            name="input_validation",
            confidence=0.3,
            reason="Syntax error; default archetype",
        )]

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


# ── AST Signal Extraction ────────────────────────────────────────────────


def _extract_signals(tree: ast.Module, source: str) -> SourceSignals:
    """Extract all signals from an AST tree."""
    signals = SourceSignals()
    visitor = _SignalVisitor(signals)
    visitor.visit(tree)

    # Post-processing: derive composite signals
    _derive_composite_signals(signals)

    return signals


class _SignalVisitor(ast.NodeVisitor):
    """AST visitor that extracts signals for archetype matching."""

    def __init__(self, signals: SourceSignals) -> None:
        self.signals = signals
        self._current_class: str | None = None

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            module = alias.name
            self.signals.imports.add(module)
            self.signals.import_modules.add(module.split(".")[0])
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module:
            self.signals.imports.add(node.module)
            self.signals.import_modules.add(node.module.split(".")[0])
            for alias in (node.names or []):
                self.signals.imports.add(f"{node.module}.{alias.name}")
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        bases = []
        for base in node.bases:
            if isinstance(base, ast.Name):
                bases.append(base.id)
            elif isinstance(base, ast.Attribute):
                bases.append(f"{_attr_name(base)}")

        is_dataclass = any(
            _decorator_name(d) in ("dataclass", "dataclasses.dataclass")
            for d in node.decorator_list
        )

        class_info = ClassInfo(
            name=node.name,
            bases=bases,
            is_dataclass=is_dataclass,
        )

        # Check for __init__ and mutable state
        for item in node.body:
            if isinstance(item, ast.FunctionDef):
                class_info.methods.append(item.name)
                if item.name == "__init__":
                    class_info.has_init = True
                    class_info.init_defaults = len(item.args.defaults) + len(item.args.kw_defaults)

        # Detect mutable fields (assignments in methods)
        for item in ast.walk(node):
            if isinstance(item, ast.Attribute) and isinstance(item.ctx, ast.Store):
                if isinstance(item.value, ast.Name) and item.value.id == "self":
                    class_info.mutable_fields.append(item.attr)

        self.signals.classes.append(class_info)

        # Visit methods within class context
        old_class = self._current_class
        self._current_class = node.name
        self.generic_visit(node)
        self._current_class = old_class

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        func_info = FunctionInfo(
            name=node.name,
            is_method=self._current_class is not None,
            class_name=self._current_class,
        )

        # Arguments
        for arg in node.args.args:
            if arg.arg != "self":
                func_info.args.append(arg.arg)
            if arg.annotation:
                func_info.has_type_annotations = True

        # Return type
        if node.returns:
            func_info.has_return_type = True
            func_info.has_type_annotations = True

        # Docstring
        if (node.body and isinstance(node.body[0], ast.Expr) and
                isinstance(node.body[0].value, (ast.Constant, ast.Str))):
            func_info.has_docstring = True

        # Decorators
        for d in node.decorator_list:
            func_info.decorators.append(_decorator_name(d))

        # Raises
        for child in ast.walk(node):
            if isinstance(child, ast.Raise) and child.exc:
                if isinstance(child.exc, ast.Call) and isinstance(child.exc.func, ast.Name):
                    func_info.raises.append(child.exc.func.id)
                elif isinstance(child.exc, ast.Name):
                    func_info.raises.append(child.exc.id)

        self.signals.functions.append(func_info)
        self.generic_visit(node)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Try(self, node: ast.Try) -> None:
        self.signals.has_try_except = True
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        func_name = _call_name(node)
        if func_name:
            # File I/O detection
            if func_name in ("open", "Path.read_text", "Path.write_text",
                             "Path.read_bytes", "Path.write_bytes"):
                self.signals.has_file_io = True

            # Subprocess detection
            if func_name.startswith("subprocess.") or func_name in ("Popen", "run", "call", "check_output"):
                self.signals.has_subprocess = True

            # JSON patterns
            if func_name in ("json.dump", "json.dumps", "json.load", "json.loads"):
                self.signals.has_json_dump_load = True

        self.generic_visit(node)

    def visit_If(self, node: ast.If) -> None:
        # Guard clause detection: if not X: raise ...
        if (isinstance(node.test, ast.UnaryOp) and isinstance(node.test.op, ast.Not)):
            for child in node.body:
                if isinstance(child, ast.Raise):
                    self.signals.has_guard_clauses = True
                    break
        # Also detect: if X is None: raise ...
        if isinstance(node.test, ast.Compare):
            for child in node.body:
                if isinstance(child, ast.Raise):
                    self.signals.has_guard_clauses = True
                    break

        # Complex conditional detection
        if isinstance(node.test, ast.BoolOp):
            if len(node.test.values) >= 3:
                self.signals.has_complex_conditionals = True

        self.generic_visit(node)


def _derive_composite_signals(signals: SourceSignals) -> None:
    """Derive higher-level signals from extracted data."""
    # HTTP imports
    http_modules = {"requests", "httpx", "urllib", "aiohttp", "http"}
    signals.has_http_imports = bool(signals.import_modules & http_modules)

    # Database ops
    db_modules = {"sqlite3", "sqlalchemy", "psycopg2", "pymongo", "motor", "redis"}
    signals.has_database_ops = bool(signals.import_modules & db_modules)

    # Config loading
    config_modules = {"yaml", "toml", "tomli", "tomllib", "configparser"}
    signals.has_yaml_toml_json = bool(signals.import_modules & config_modules)

    # Subprocess
    if "subprocess" in signals.import_modules:
        signals.has_subprocess = True

    # Dataclass detection
    if any(c.is_dataclass for c in signals.classes):
        signals.has_dataclasses = True

    # Typed functions
    if any(f.has_type_annotations for f in signals.functions):
        signals.has_typed_functions = True

    # ValueError/TypeError raises
    error_types = {"ValueError", "TypeError", "KeyError", "AttributeError"}
    for f in signals.functions:
        if set(f.raises) & error_types:
            signals.has_value_errors = True
            break

    # Stateful classes (classes with mutable fields)
    for c in signals.classes:
        if c.mutable_fields:
            signals.has_stateful_classes = True
            # Check for collection mutations
            # (this is a rough heuristic — could be refined)
            signals.has_mutable_collections = True
            break

    # Encode/decode patterns
    encode_decode_names = {"encode", "decode", "encrypt", "decrypt", "compress", "decompress"}
    for f in signals.functions:
        if any(name in f.name.lower() for name in encode_decode_names):
            signals.has_encode_decode = True
            break

    # to_/from_ pairs
    to_funcs = {f.name for f in signals.functions if f.name.startswith("to_")}
    from_funcs = {f.name for f in signals.functions if f.name.startswith("from_")}
    if to_funcs and from_funcs:
        signals.has_to_from_pairs = True

    # Serialize patterns
    serialize_names = {"serialize", "deserialize", "marshal", "unmarshal", "to_dict", "from_dict",
                       "to_json", "from_json", "dump", "load"}
    for f in signals.functions:
        if any(name in f.name.lower() for name in serialize_names):
            signals.has_serialize_patterns = True
            break


# ── Archetype Matching ───────────────────────────────────────────────────


def _match_archetypes(signals: SourceSignals) -> list[ArchetypeMatch]:
    """Match extracted signals against archetype definitions."""
    matches: list[ArchetypeMatch] = []

    # 1. Input Validation (always applicable at some confidence)
    iv_confidence, iv_reasons = _score_input_validation(signals)
    if iv_confidence > 0:
        matches.append(ArchetypeMatch(
            name="input_validation",
            confidence=iv_confidence,
            reason="; ".join(iv_reasons),
            relevant_functions=[f.name for f in signals.functions
                                if f.has_type_annotations or f.raises],
        ))

    # 2. Error Handling
    eh_confidence, eh_reasons = _score_error_handling(signals)
    if eh_confidence > 0:
        matches.append(ArchetypeMatch(
            name="error_handling",
            confidence=eh_confidence,
            reason="; ".join(eh_reasons),
            relevant_functions=[f.name for f in signals.functions if f.raises],
        ))

    # 3. Configuration
    cfg_confidence, cfg_reasons = _score_configuration(signals)
    if cfg_confidence > 0:
        matches.append(ArchetypeMatch(
            name="configuration",
            confidence=cfg_confidence,
            reason="; ".join(cfg_reasons),
            relevant_classes=[c.name for c in signals.classes
                              if c.is_dataclass or c.has_init],
        ))

    # 4. State Invariant
    si_confidence, si_reasons = _score_state_invariant(signals)
    if si_confidence > 0:
        matches.append(ArchetypeMatch(
            name="state_invariant",
            confidence=si_confidence,
            reason="; ".join(si_reasons),
            relevant_classes=[c.name for c in signals.classes if c.mutable_fields],
        ))

    # 5. Mock Isolation
    mi_confidence, mi_reasons = _score_mock_isolation(signals)
    if mi_confidence > 0:
        matches.append(ArchetypeMatch(
            name="mock_isolation",
            confidence=mi_confidence,
            reason="; ".join(mi_reasons),
        ))

    # 6. Regression (hard to auto-detect; low confidence)
    reg_confidence, reg_reasons = _score_regression(signals)
    if reg_confidence > 0:
        matches.append(ArchetypeMatch(
            name="regression",
            confidence=reg_confidence,
            reason="; ".join(reg_reasons),
        ))

    # 7. Round Trip
    rt_confidence, rt_reasons = _score_round_trip(signals)
    if rt_confidence > 0:
        matches.append(ArchetypeMatch(
            name="round_trip",
            confidence=rt_confidence,
            reason="; ".join(rt_reasons),
        ))

    return matches


def _score_input_validation(signals: SourceSignals) -> tuple[float, list[str]]:
    """Score for input_validation archetype."""
    confidence = 0.3  # Always applicable at baseline
    reasons = ["Baseline: universal archetype"]

    if signals.has_typed_functions:
        confidence = max(confidence, 0.6)
        reasons.append("Has typed functions")
    if signals.has_guard_clauses:
        confidence = max(confidence, 0.8)
        reasons.append("Has guard clauses")
    if signals.has_value_errors:
        confidence = max(confidence, 0.7)
        reasons.append("Raises ValueError/TypeError")

    return confidence, reasons


def _score_error_handling(signals: SourceSignals) -> tuple[float, list[str]]:
    """Score for error_handling archetype."""
    confidence = 0.0
    reasons: list[str] = []

    if signals.has_try_except:
        confidence = max(confidence, 0.8)
        reasons.append("Has try/except blocks")
    if signals.has_http_imports:
        confidence = max(confidence, 0.7)
        reasons.append("Has HTTP library imports")
    if signals.has_subprocess:
        confidence = max(confidence, 0.8)
        reasons.append("Uses subprocess")
    if signals.has_file_io:
        confidence = max(confidence, 0.6)
        reasons.append("Has file I/O operations")

    # Boost if functions explicitly raise exceptions
    raising_funcs = [f for f in signals.functions if f.raises]
    if raising_funcs:
        confidence = max(confidence, 0.7)
        reasons.append(f"{len(raising_funcs)} functions raise exceptions")

    return confidence, reasons


def _score_configuration(signals: SourceSignals) -> tuple[float, list[str]]:
    """Score for configuration archetype."""
    confidence = 0.0
    reasons: list[str] = []

    if signals.has_dataclasses:
        confidence = max(confidence, 0.8)
        reasons.append("Has dataclass definitions")
    if signals.has_yaml_toml_json:
        confidence = max(confidence, 0.8)
        reasons.append("Imports config parsers (YAML/TOML/JSON)")

    # Classes with __init__ and defaults
    for c in signals.classes:
        if c.has_init and c.init_defaults > 0:
            confidence = max(confidence, 0.7)
            reasons.append(f"Class {c.name} has __init__ with {c.init_defaults} defaults")
            break

    return confidence, reasons


def _score_state_invariant(signals: SourceSignals) -> tuple[float, list[str]]:
    """Score for state_invariant archetype."""
    confidence = 0.0
    reasons: list[str] = []

    if signals.has_stateful_classes:
        confidence = max(confidence, 0.8)
        reasons.append("Has classes with mutable state")

    for c in signals.classes:
        if len(c.mutable_fields) > 2:
            confidence = max(confidence, 0.9)
            reasons.append(f"Class {c.name} has {len(c.mutable_fields)} mutable fields")
            break

    return confidence, reasons


def _score_mock_isolation(signals: SourceSignals) -> tuple[float, list[str]]:
    """Score for mock_isolation archetype."""
    confidence = 0.0
    reasons: list[str] = []

    if signals.has_http_imports:
        confidence = max(confidence, 0.9)
        reasons.append("Has HTTP library imports (mock API calls)")
    if signals.has_subprocess:
        confidence = max(confidence, 0.9)
        reasons.append("Uses subprocess (mock external commands)")
    if signals.has_database_ops:
        confidence = max(confidence, 0.8)
        reasons.append("Has database operations (mock DB)")
    if signals.has_file_io:
        confidence = max(confidence, 0.7)
        reasons.append("Has file I/O (mock filesystem)")

    return confidence, reasons


def _score_regression(signals: SourceSignals) -> tuple[float, list[str]]:
    """Score for regression archetype."""
    confidence = 0.0
    reasons: list[str] = []

    if signals.has_complex_conditionals:
        confidence = max(confidence, 0.4)
        reasons.append("Has complex conditional logic")

    # Check for edge-case-susceptible patterns
    if signals.has_guard_clauses and signals.has_value_errors:
        confidence = max(confidence, 0.5)
        reasons.append("Guard clauses + value errors suggest edge cases")

    # Low fallback
    if not reasons:
        confidence = 0.2
        reasons.append("Low default confidence")

    return confidence, reasons


def _score_round_trip(signals: SourceSignals) -> tuple[float, list[str]]:
    """Score for round_trip archetype."""
    confidence = 0.0
    reasons: list[str] = []

    if signals.has_encode_decode:
        confidence = max(confidence, 0.9)
        reasons.append("Has encode/decode functions")
    if signals.has_to_from_pairs:
        confidence = max(confidence, 0.9)
        reasons.append("Has to_/from_ function pairs")
    if signals.has_serialize_patterns:
        confidence = max(confidence, 0.8)
        reasons.append("Has serialize/deserialize patterns")
    if signals.has_json_dump_load:
        confidence = max(confidence, 0.7)
        reasons.append("Has JSON dump/load patterns")

    return confidence, reasons


# ── AST Helpers ──────────────────────────────────────────────────────────


def _decorator_name(node: ast.expr) -> str:
    """Extract decorator name from AST node."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return _attr_name(node)
    if isinstance(node, ast.Call):
        return _decorator_name(node.func)
    return ""


def _attr_name(node: ast.Attribute) -> str:
    """Extract dotted name from Attribute node."""
    parts = []
    current = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
    return ".".join(reversed(parts))


def _call_name(node: ast.Call) -> str:
    """Extract function name from a Call node."""
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return _attr_name(node.func)
    return ""
