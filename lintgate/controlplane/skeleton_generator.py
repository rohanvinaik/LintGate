"""Test skeleton generator — produces pytest stubs from archetypes.

Given a source file and its matched archetypes, generates a test
skeleton with correct imports, class structure, and test method stubs.

This is a pure function that returns a string — it does NOT write files.
File creation is an explicit repair action that must be approved.

Design: AST-based function/class extraction + template expansion.
No Jinja2 dependency — uses simple string templates.
"""

from __future__ import annotations

import os

from .test_archetype_selector import (
    ArchetypeMatch,
    ClassInfo,
    FunctionInfo,
    SourceSignals,
    extract_signals,
    select_archetypes,
)


def generate_test_skeleton(
    source_file: str,
    archetypes: list[str] | None = None,
    project_root: str = "",
) -> str:
    """Generate a test skeleton for a source file.

    Args:
        source_file: Path to the Python source file.
        archetypes: List of archetype names to use. If None, auto-detect.
        project_root: Project root for import resolution.

    Returns:
        String content of the generated test file.
    """
    # Extract signals and get archetype matches
    signals = extract_signals(source_file)

    if archetypes:
        # Use requested archetypes
        matches = [
            ArchetypeMatch(name=a, confidence=1.0, reason="requested")
            for a in archetypes
        ]
    else:
        matches = select_archetypes(source_file, project_root)

    if not matches:
        matches = [
            ArchetypeMatch(name="input_validation", confidence=0.3, reason="default")
        ]

    # Determine the module import path
    module_name = os.path.splitext(os.path.basename(source_file))[0]
    import_path = _compute_import_path(source_file, project_root)

    # Build the skeleton
    parts: list[str] = []

    # Header
    parts.append(f'"""Tests for {module_name}."""')
    parts.append("")
    parts.append("from __future__ import annotations")
    parts.append("")

    # Imports
    imports = _build_imports(signals, matches, import_path, module_name)
    parts.extend(imports)
    parts.append("")
    parts.append("")

    # Generate test stubs per archetype
    archetype_names = {m.name for m in matches}

    # Generate function-level tests
    for func in signals.functions:
        if func.name.startswith("_") and not func.name.startswith("__"):
            continue  # Skip private functions
        if func.is_method:
            continue  # Methods handled with their classes

        func_tests = _generate_function_tests(func, archetype_names, module_name)
        if func_tests:
            parts.extend(func_tests)
            parts.append("")
            parts.append("")

    # Generate class-level tests
    for cls in signals.classes:
        cls_tests = _generate_class_tests(cls, signals, archetype_names, module_name)
        if cls_tests:
            parts.extend(cls_tests)
            parts.append("")
            parts.append("")

    # If no functions or classes, add a placeholder
    if not signals.functions and not signals.classes:
        parts.append(f"def test_{module_name}_placeholder() -> None:")
        parts.append(f'    """TODO: Add tests for {module_name}."""')
        parts.append("    pass")
        parts.append("")

    return "\n".join(parts) + "\n"


def generate_test_path(source_file: str, project_root: str = "") -> str:
    """Compute the expected test file path for a source file.

    Convention: tests/test_<module>.py or tests/<package>/test_<module>.py
    """
    src_path = os.path.abspath(source_file)
    root = os.path.abspath(project_root) if project_root else os.path.dirname(src_path)

    basename = os.path.splitext(os.path.basename(source_file))[0]
    test_name = f"test_{basename}.py"

    # Try to put it in tests/ directory
    tests_dir = os.path.join(root, "tests")

    try:
        rel = os.path.relpath(source_file, root)
        parts = os.path.dirname(rel).split(os.sep)
        # Strip common source directories
        parts = [p for p in parts if p not in ("src", "lib", ".")]
        if parts:
            return os.path.join(tests_dir, *parts, test_name)
    except ValueError:
        pass

    return os.path.join(tests_dir, test_name)


# ── Internal generators ──────────────────────────────────────────────────


def _build_imports(
    signals: SourceSignals,
    matches: list[ArchetypeMatch],
    import_path: str,
    module_name: str,
) -> list[str]:
    """Build import section for the test file."""
    imports: list[str] = ["import pytest"]

    # Add mock imports if needed
    archetype_names = {m.name for m in matches}
    if "mock_isolation" in archetype_names:
        imports.append("from unittest.mock import MagicMock, patch")

    # Import from the source module
    public_names: list[str] = []
    for func in signals.functions:
        if not func.name.startswith("_") and not func.is_method:
            public_names.append(func.name)
    for cls in signals.classes:
        public_names.append(cls.name)

    if public_names and import_path:
        names_str = ", ".join(sorted(public_names))
        imports.append(f"from {import_path} import {names_str}")

    return imports


def _generate_function_tests(
    func: FunctionInfo,
    archetype_names: set[str],
    module_name: str,
) -> list[str]:
    """Generate test stubs for a single function."""
    lines: list[str] = []
    fn = func.name

    # Input validation tests
    if "input_validation" in archetype_names:
        lines.append(f"def test_{fn}_returns_expected_output() -> None:")
        lines.append(f'    """Test {fn} with valid input."""')
        if func.args:
            args_placeholder = ", ".join("..." for _ in func.args)
            lines.append(f"    result = {fn}({args_placeholder})")
        else:
            lines.append(f"    result = {fn}()")
        lines.append(
            "    assert result == EXPECTED  # TODO: Replace EXPECTED with the actual expected value"
        )
        lines.append("")

        # Boundary test stubs for functions with arguments
        if func.args:
            lines.append(f"def test_{fn}_boundary_values() -> None:")
            lines.append(
                f'    """Test {fn} with edge cases that mutation testing targets."""'
            )
            for arg in func.args[:3]:
                lines.append(f"    # Boundary: {arg}")
            lines.append(
                "    # TODO: Test with boundary inputs (0, -1, empty string, None)"
            )
            lines.append(f"    result = {fn}({', '.join('...' for _ in func.args)})")
            lines.append(
                "    assert result == EXPECTED  # TODO: Replace with actual expected value"
            )
            lines.append("")

        if func.raises:
            for exc_type in func.raises[:2]:
                lines.append(
                    f"def test_{fn}_raises_{exc_type.lower()}_on_invalid_input() -> None:"
                )
                lines.append(f'    """Test {fn} raises {exc_type} on invalid input."""')
                lines.append(f"    with pytest.raises({exc_type}):")
                lines.append(f"        {fn}(None)")
                lines.append("")

    # Error handling tests
    if "error_handling" in archetype_names and func.raises:
        lines.append(f"def test_{fn}_handles_errors_gracefully() -> None:")
        lines.append(f'    """Test {fn} error handling."""')
        lines.append("    # TODO: Trigger error condition and verify handling")
        lines.append("    pass")
        lines.append("")

    return lines


def _generate_class_tests(
    cls: ClassInfo,
    signals: SourceSignals,
    archetype_names: set[str],
    module_name: str,
) -> list[str]:
    """Generate test stubs for a class."""
    lines: list[str] = []
    cn = cls.name

    # Configuration tests (dataclasses, classes with defaults)
    if "configuration" in archetype_names and (cls.is_dataclass or cls.has_init):
        lines.append(f"class Test{cn}Config:")
        lines.append(f'    """Configuration tests for {cn}."""')
        lines.append("")
        lines.append("    def test_default_creation(self) -> None:")
        lines.append(f'        """Test {cn} can be created with defaults."""')
        lines.append(f"        obj = {cn}()")
        lines.append("        # TODO: Assert specific field values, not just existence")
        lines.append(
            "        assert obj == EXPECTED  # TODO: Replace with expected instance or field checks"
        )
        lines.append("")

        if cls.init_defaults > 0:
            lines.append("    def test_override_defaults(self) -> None:")
            lines.append(f'        """Test {cn} with overridden defaults."""')
            lines.append(
                "        # TODO: Provide override values and assert exact field values"
            )
            lines.append(f"        obj = {cn}()")
            lines.append(
                "        assert obj == EXPECTED  # TODO: Replace with expected instance"
            )
            lines.append("")

    # State invariant tests
    if "state_invariant" in archetype_names and cls.mutable_fields:
        lines.append(f"class Test{cn}State:")
        lines.append(f'    """State invariant tests for {cn}."""')
        lines.append("")
        lines.append("    def test_initial_state(self) -> None:")
        lines.append(f'        """Test {cn} initial state after creation."""')
        lines.append(f"        obj = {cn}()")
        lines.append("        # TODO: Assert exact initial field values")
        lines.append(
            "        assert obj.field == EXPECTED  # TODO: Replace with actual field checks"
        )
        lines.append("")

        # Generate tests for methods that likely modify state
        for method in cls.methods:
            if method.startswith("_"):
                continue
            lines.append(f"    def test_{method}_modifies_state(self) -> None:")
            lines.append(f'        """Test {cn}.{method} state changes."""')
            lines.append(f"        obj = {cn}()")
            lines.append(f"        obj.{method}()  # TODO: Provide arguments")
            lines.append("        # TODO: Assert state changed correctly")
            lines.append("")

    return lines


def _compute_import_path(source_file: str, project_root: str) -> str:
    """Compute the Python import path for a source file.

    e.g., /project/lintgate/types.py → lintgate.types
    """
    if not project_root:
        return os.path.splitext(os.path.basename(source_file))[0]

    try:
        rel = os.path.relpath(source_file, project_root)
        parts = os.path.splitext(rel)[0].split(os.sep)
        # Remove __init__ from the path
        parts = [p for p in parts if p != "__init__"]
        return ".".join(parts)
    except ValueError:
        return os.path.splitext(os.path.basename(source_file))[0]
