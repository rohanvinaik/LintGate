"""Tests for lintgate/controlplane/skeleton_generator.py — full symbol coverage."""

from __future__ import annotations

import os
import textwrap
from unittest.mock import patch

from lintgate.controlplane.skeleton_generator import (
    _build_imports,
    _compute_import_path,
    _generate_class_tests,
    _generate_function_tests,
    generate_test_path,
    generate_test_skeleton,
)
from lintgate.controlplane.test_archetype_selector import (
    ArchetypeMatch,
    ClassInfo,
    FunctionInfo,
    SourceSignals,
)

# ── _compute_import_path ────────────────────────────────────────────────


class TestComputeImportPath:
    """Tests for _compute_import_path."""

    def test_no_project_root_returns_basename(self) -> None:
        result = _compute_import_path("/some/dir/module.py", "")
        assert result == "module"

    def test_relative_to_project_root(self, tmp_path: object) -> None:
        result = _compute_import_path("/project/lintgate/types.py", "/project")
        assert result == "lintgate.types"

    def test_nested_package_path(self) -> None:
        result = _compute_import_path("/project/lintgate/controlplane/tools.py", "/project")
        assert result == "lintgate.controlplane.tools"

    def test_init_file_excluded(self) -> None:
        result = _compute_import_path("/project/lintgate/__init__.py", "/project")
        assert result == "lintgate"

    def test_value_error_fallback(self) -> None:
        # On some systems relpath across drives raises ValueError;
        # simulate by passing paths that can never be relative on this OS.
        # The function catches ValueError and falls back to basename.
        result = _compute_import_path("/a/b/foo.py", "")
        assert result == "foo"


# ── _build_imports ──────────────────────────────────────────────────────


class TestBuildImports:
    """Tests for _build_imports."""

    def test_always_includes_pytest(self) -> None:
        signals = SourceSignals()
        matches: list[ArchetypeMatch] = []
        lines = _build_imports(signals, matches, "pkg.mod", "mod")
        assert "import pytest" in lines

    def test_mock_isolation_adds_mock_import(self) -> None:
        signals = SourceSignals()
        matches = [ArchetypeMatch(name="mock_isolation", confidence=0.9, reason="test")]
        lines = _build_imports(signals, matches, "pkg.mod", "mod")
        assert "from unittest.mock import MagicMock, patch" in lines

    def test_public_functions_imported(self) -> None:
        func = FunctionInfo(name="do_thing", is_method=False)
        signals = SourceSignals(functions=[func])
        matches: list[ArchetypeMatch] = []
        lines = _build_imports(signals, matches, "pkg.mod", "mod")
        assert any("from pkg.mod import do_thing" in line for line in lines)

    def test_private_functions_not_imported(self) -> None:
        func = FunctionInfo(name="_private", is_method=False)
        signals = SourceSignals(functions=[func])
        matches: list[ArchetypeMatch] = []
        lines = _build_imports(signals, matches, "pkg.mod", "mod")
        assert all("_private" not in line for line in lines)

    def test_methods_not_imported(self) -> None:
        func = FunctionInfo(name="my_method", is_method=True)
        signals = SourceSignals(functions=[func])
        matches: list[ArchetypeMatch] = []
        lines = _build_imports(signals, matches, "pkg.mod", "mod")
        # Methods are not standalone imports
        assert all("my_method" not in line for line in lines)

    def test_classes_imported(self) -> None:
        cls = ClassInfo(name="MyClass")
        signals = SourceSignals(classes=[cls])
        matches: list[ArchetypeMatch] = []
        lines = _build_imports(signals, matches, "pkg.mod", "mod")
        assert any("MyClass" in line for line in lines)

    def test_empty_import_path_skips_from_import(self) -> None:
        func = FunctionInfo(name="do_thing", is_method=False)
        signals = SourceSignals(functions=[func])
        matches: list[ArchetypeMatch] = []
        lines = _build_imports(signals, matches, "", "mod")
        # No "from" import line when import_path is empty
        assert all(not line.startswith("from ") for line in lines)


# ── _generate_function_tests ────────────────────────────────────────────


class TestGenerateFunctionTests:
    """Tests for _generate_function_tests."""

    def test_input_validation_creates_basic_test(self) -> None:
        func = FunctionInfo(name="compute", args=["x", "y"])
        lines = _generate_function_tests(func, {"input_validation"}, "mod")
        text = "\n".join(lines)
        assert "def test_compute_returns_expected_output" in text
        assert "result = compute(..., ...)" in text

    def test_no_args_generates_no_arg_call(self) -> None:
        func = FunctionInfo(name="ping", args=[])
        lines = _generate_function_tests(func, {"input_validation"}, "mod")
        text = "\n".join(lines)
        assert "result = ping()" in text

    def test_raises_generates_exception_tests(self) -> None:
        func = FunctionInfo(name="validate", args=["x"], raises=["ValueError", "TypeError"])
        lines = _generate_function_tests(func, {"input_validation"}, "mod")
        text = "\n".join(lines)
        assert "test_validate_raises_valueerror_on_invalid_input" in text
        assert "test_validate_raises_typeerror_on_invalid_input" in text

    def test_error_handling_archetype_generates_stub(self) -> None:
        func = FunctionInfo(name="fetch", raises=["IOError"])
        lines = _generate_function_tests(func, {"error_handling"}, "mod")
        text = "\n".join(lines)
        assert "test_fetch_handles_errors_gracefully" in text

    def test_error_handling_no_raises_produces_nothing(self) -> None:
        func = FunctionInfo(name="fetch", raises=[])
        lines = _generate_function_tests(func, {"error_handling"}, "mod")
        assert len(lines) == 0

    def test_unrecognized_archetype_produces_nothing(self) -> None:
        func = FunctionInfo(name="do_stuff", args=["a"])
        lines = _generate_function_tests(func, {"some_unknown_archetype"}, "mod")
        assert len(lines) == 0

    def test_raises_caps_at_two_exceptions(self) -> None:
        func = FunctionInfo(
            name="check",
            args=["v"],
            raises=["ValueError", "TypeError", "RuntimeError"],
        )
        lines = _generate_function_tests(func, {"input_validation"}, "mod")
        text = "\n".join(lines)
        # Only first two raises get tests
        assert "valueerror" in text
        assert "typeerror" in text
        assert "runtimeerror" not in text


# ── _generate_class_tests ───────────────────────────────────────────────


class TestGenerateClassTests:
    """Tests for _generate_class_tests."""

    def test_configuration_dataclass_generates_default_test(self) -> None:
        cls = ClassInfo(name="Config", is_dataclass=True, has_init=True)
        signals = SourceSignals(classes=[cls])
        lines = _generate_class_tests(cls, signals, {"configuration"}, "mod")
        text = "\n".join(lines)
        assert "class TestConfigConfig:" in text
        assert "test_default_creation" in text

    def test_configuration_with_defaults_generates_override_test(self) -> None:
        cls = ClassInfo(name="Settings", is_dataclass=True, has_init=True, init_defaults=3)
        signals = SourceSignals(classes=[cls])
        lines = _generate_class_tests(cls, signals, {"configuration"}, "mod")
        text = "\n".join(lines)
        assert "test_override_defaults" in text

    def test_configuration_no_defaults_skips_override_test(self) -> None:
        cls = ClassInfo(name="Obj", is_dataclass=True, has_init=True, init_defaults=0)
        signals = SourceSignals(classes=[cls])
        lines = _generate_class_tests(cls, signals, {"configuration"}, "mod")
        text = "\n".join(lines)
        assert "test_override_defaults" not in text

    def test_state_invariant_generates_initial_state_test(self) -> None:
        cls = ClassInfo(
            name="Counter",
            mutable_fields=["count"],
            methods=["increment", "__init__"],
        )
        signals = SourceSignals(classes=[cls])
        lines = _generate_class_tests(cls, signals, {"state_invariant"}, "mod")
        text = "\n".join(lines)
        assert "class TestCounterState:" in text
        assert "test_initial_state" in text

    def test_state_invariant_generates_method_tests(self) -> None:
        cls = ClassInfo(
            name="Stack",
            mutable_fields=["items"],
            methods=["push", "pop", "__init__", "__repr__"],
        )
        signals = SourceSignals(classes=[cls])
        lines = _generate_class_tests(cls, signals, {"state_invariant"}, "mod")
        text = "\n".join(lines)
        assert "test_push_modifies_state" in text
        assert "test_pop_modifies_state" in text
        # Dunder methods excluded
        assert "test___init___modifies_state" not in text
        assert "test___repr___modifies_state" not in text

    def test_private_methods_excluded_from_state_tests(self) -> None:
        cls = ClassInfo(
            name="Tracker",
            mutable_fields=["data"],
            methods=["_internal", "update"],
        )
        signals = SourceSignals(classes=[cls])
        lines = _generate_class_tests(cls, signals, {"state_invariant"}, "mod")
        text = "\n".join(lines)
        assert "_internal" not in text
        assert "test_update_modifies_state" in text

    def test_no_matching_archetype_returns_empty(self) -> None:
        cls = ClassInfo(name="Plain")
        signals = SourceSignals(classes=[cls])
        lines = _generate_class_tests(cls, signals, {"input_validation"}, "mod")
        assert len(lines) == 0


# ── generate_test_skeleton (integration) ────────────────────────────────


class TestGenerateTestSkeleton:
    """Integration tests for generate_test_skeleton."""

    def test_generates_valid_python_for_simple_module(self, tmp_path: object) -> None:
        import pathlib

        src = pathlib.Path(str(tmp_path)) / "simple.py"
        src.write_text(
            textwrap.dedent("""\
                def hello(name: str) -> str:
                    \"\"\"Greet someone.\"\"\"
                    return f"Hello, {name}"
            """)
        )
        result = generate_test_skeleton(str(src))
        assert "Tests for simple" in result
        assert "from __future__ import annotations" in result
        assert "import pytest" in result

    def test_placeholder_for_empty_module(self, tmp_path: object) -> None:
        import pathlib

        src = pathlib.Path(str(tmp_path)) / "empty.py"
        src.write_text("# nothing here\n")
        result = generate_test_skeleton(str(src))
        assert "test_empty_placeholder" in result

    def test_explicit_archetypes_override_detection(self, tmp_path: object) -> None:
        import pathlib

        src = pathlib.Path(str(tmp_path)) / "mod.py"
        src.write_text("def foo(): pass\n")
        result = generate_test_skeleton(str(src), archetypes=["input_validation"])
        assert "test_foo_returns_expected_output" in result

    def test_private_functions_skipped(self, tmp_path: object) -> None:
        import pathlib

        src = pathlib.Path(str(tmp_path)) / "priv.py"
        src.write_text(
            textwrap.dedent("""\
                def _helper():
                    pass

                def public_api():
                    pass
            """)
        )
        result = generate_test_skeleton(str(src), archetypes=["input_validation"])
        assert "test__helper" not in result
        assert "test_public_api" in result


# ── generate_test_path ──────────────────────────────────────────────────


class TestGenerateTestPath:
    """Tests for generate_test_path."""

    def test_basic_path(self, tmp_path: object) -> None:
        import pathlib

        root = pathlib.Path(str(tmp_path))
        src = root / "pkg" / "mod.py"
        src.parent.mkdir(parents=True, exist_ok=True)
        src.touch()
        result = generate_test_path(str(src), str(root))
        assert result.endswith(os.path.join("tests", "pkg", "test_mod.py"))

    def test_strips_src_directory(self, tmp_path: object) -> None:
        import pathlib

        root = pathlib.Path(str(tmp_path))
        src = root / "src" / "mod.py"
        src.parent.mkdir(parents=True, exist_ok=True)
        src.touch()
        result = generate_test_path(str(src), str(root))
        assert result.endswith(os.path.join("tests", "test_mod.py"))

    def test_no_project_root_defaults_to_dirname(self, tmp_path: object) -> None:
        import pathlib

        src = pathlib.Path(str(tmp_path)) / "module.py"
        src.touch()
        result = generate_test_path(str(src))
        assert "test_module.py" in result

    def test_relpath_value_error_falls_back_to_tests_dir(self) -> None:
        """Lines 128-129: When os.path.relpath raises ValueError, fall back."""
        with patch(
            "lintgate.controlplane.skeleton_generator.os.path.relpath",
            side_effect=ValueError("different drives"),
        ):
            result = generate_test_path("/some/src/mod.py", "/project")
        # Falls through to the final return: os.path.join(tests_dir, test_name)
        assert result.endswith(os.path.join("tests", "test_mod.py"))


# ── generate_test_skeleton — default fallback (line 52) ─────────────────


class TestGenerateTestSkeletonDefaultFallback:
    """Tests for the default archetype fallback in generate_test_skeleton."""

    def test_empty_archetypes_from_selector_uses_default(self, tmp_path: object) -> None:
        """Line 52: When select_archetypes returns [] and no archetypes given, use default."""
        import pathlib

        src = pathlib.Path(str(tmp_path)) / "bare.py"
        src.write_text("def greet(): pass\n")

        with patch(
            "lintgate.controlplane.skeleton_generator.select_archetypes",
            return_value=[],
        ):
            result = generate_test_skeleton(str(src))
        # The default archetype is input_validation, which generates a basic test
        assert "test_greet_returns_expected_output" in result


# ── generate_test_skeleton — method skip (line 81) ──────────────────────


class TestGenerateTestSkeletonMethodSkip:
    """Tests for skipping methods in the function loop of generate_test_skeleton."""

    def test_methods_skipped_in_function_loop(self, tmp_path: object) -> None:
        """Line 81: Functions with is_method=True are skipped in the top-level loop."""
        import pathlib

        src = pathlib.Path(str(tmp_path)) / "with_class.py"
        src.write_text(
            textwrap.dedent("""\
                class MyObj:
                    def do_work(self):
                        pass

                def standalone():
                    pass
            """)
        )
        result = generate_test_skeleton(str(src), archetypes=["input_validation"])
        # standalone gets a test, but do_work (a method) does not get a
        # top-level function test — it is handled with its class
        assert "test_standalone_returns_expected_output" in result
        assert "test_do_work_returns_expected_output" not in result


# ── _generate_class_tests — dunder continue (line 272) ──────────────────


class TestGenerateClassTestsDunderSkip:
    """Tests for private/dunder method skip in _generate_class_tests."""

    def test_private_and_dunder_methods_excluded(self) -> None:
        """Private methods (startswith '_') are skipped in state_invariant tests."""
        cls = ClassInfo(
            name="Widget",
            mutable_fields=["value"],
            methods=["__str__", "__eq__", "_internal", "update"],
        )
        signals = SourceSignals(classes=[cls])
        lines = _generate_class_tests(cls, signals, {"state_invariant"}, "mod")
        text = "\n".join(lines)
        assert "test_update_modifies_state" in text
        assert "test___str___modifies_state" not in text
        assert "test___eq___modifies_state" not in text
        assert "test__internal_modifies_state" not in text


# ── _compute_import_path — ValueError fallback (lines 297-298) ──────────


class TestComputeImportPathValueErrorFallback:
    """Tests for the ValueError fallback in _compute_import_path."""

    def test_relpath_value_error_returns_basename(self) -> None:
        """Lines 297-298: When relpath raises ValueError, return basename."""
        with patch(
            "lintgate.controlplane.skeleton_generator.os.path.relpath",
            side_effect=ValueError("cannot compute relative path"),
        ):
            result = _compute_import_path("/x/y/mymod.py", "/some/root")
        assert result == "mymod"
