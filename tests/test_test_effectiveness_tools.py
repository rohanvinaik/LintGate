"""Tests for test_effectiveness_tools — MCP tool responses."""

from __future__ import annotations

import json
import os
import tempfile

import pytest


@pytest.fixture()
def temp_project():
    """Create a minimal project with source and test files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Source file
        src_dir = os.path.join(tmpdir, "src")
        os.makedirs(src_dir)
        with open(os.path.join(src_dir, "calculator.py"), "w") as f:
            f.write(
                "def add(a, b):\n    return a + b\n\ndef subtract(a, b):\n    return a - b\n"
            )

        # Test file
        test_dir = os.path.join(tmpdir, "tests")
        os.makedirs(test_dir)
        with open(os.path.join(test_dir, "test_calculator.py"), "w") as f:
            f.write(
                "from src.calculator import add, subtract\n"
                "\n"
                "def test_add():\n"
                "    result = add(1, 2)\n"
                "    assert result == 3\n"
                "\n"
                "def test_subtract():\n"
                "    result = subtract(5, 3)\n"
                "    assert result is not None\n"
            )

        yield tmpdir


@pytest.fixture()
def helpers():
    """Minimal helpers dict matching MCP tool expectations."""

    def _validate_project_root(path):
        return os.path.abspath(path)

    def _json_dumps(data, output_mode=None):
        return json.dumps(data, indent=2)

    return {
        "_validate_project_root": _validate_project_root,
        "_json_dumps": _json_dumps,
    }


class TestAnalyzeTestStrength:
    """Tests for analyze_test_strength MCP tool."""

    def test_returns_json(self, temp_project, helpers):
        from mcp_tools.test_effectiveness_tools import register

        class FakeMCP:
            def tool(self):
                def decorator(func):
                    return func

                return decorator

        mcp = FakeMCP()
        tools = register(mcp, helpers)
        result_str = tools["analyze_test_strength"](path=temp_project)
        result = json.loads(result_str)

        assert "summary" in result or "error" in result or "note" in result
        if "error" not in result and "note" not in result:
            assert "mutation_ci_context" in result
            assert "mutation_hotspots" in result

    def test_with_function_filter(self, temp_project, helpers):
        from mcp_tools.test_effectiveness_tools import register

        class FakeMCP:
            def tool(self):
                def decorator(func):
                    return func

                return decorator

        mcp = FakeMCP()
        tools = register(mcp, helpers)
        result_str = tools["analyze_test_strength"](
            path=temp_project, function_filter="add"
        )
        result = json.loads(result_str)
        assert "error" not in result

    def test_zero_mapping_hints(self, helpers):
        from mcp_tools.test_effectiveness_tools import register

        class FakeMCP:
            def tool(self):
                def decorator(func):
                    return func

                return decorator

        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            src_dir = os.path.join(tmpdir, "src")
            os.makedirs(src_dir)
            with open(os.path.join(src_dir, "lib.py"), "w") as f:
                f.write("# empty, no source functions\n")

            test_dir = os.path.join(tmpdir, "tests")
            os.makedirs(test_dir)
            with open(os.path.join(test_dir, "test_lib.py"), "w") as f:
                # Calls fake function, should be unmapped (no candidate)
                f.write("def test_missing():\n    fake_func()\n")

            mcp = FakeMCP()
            tools = register(mcp, helpers)
            result_str = tools["analyze_test_strength"](path=tmpdir)
            result = json.loads(result_str)

            assert "note" in result
            assert "No mapped functions analyzed" in result["note"]
            assert "diagnostics" in result
            assert result["diagnostics"]["attempted"] > 0
            assert "Check your module configuration or project root" in result["hint"]

    def test_file_filter_narrows_results(self, temp_project, helpers):
        from mcp_tools.test_effectiveness_tools import register

        class FakeMCP:
            def tool(self):
                return lambda func: func

        mcp = FakeMCP()
        tools = register(mcp, helpers)
        result_str = tools["analyze_test_strength"](
            path=temp_project, file_filter="calculator"
        )
        result = json.loads(result_str)
        assert "error" not in result
        assert "calculator.py" in str(result)
        # Verify filtering actually runs
        names = [v["function"] for v in result.get("top_vulnerable", [])] + result.get(
            "untested_functions", []
        )
        assert all("calculator" in n.lower() for n in names)

    def test_file_filter_metadata_emitted(self, temp_project, helpers):
        from mcp_tools.test_effectiveness_tools import register

        class FakeMCP:
            def tool(self):
                return lambda func: func

        mcp = FakeMCP()
        tools = register(mcp, helpers)
        result_str = tools["analyze_test_strength"](
            path=temp_project, file_filter="calc"
        )
        result = json.loads(result_str)
        assert "filter_applied" in result
        assert result["filter_applied"]["file"] == "calc"

    def test_file_filter_and_function_filter_compose(self, temp_project, helpers):
        from mcp_tools.test_effectiveness_tools import register

        class FakeMCP:
            def tool(self):
                return lambda func: func

        mcp = FakeMCP()
        tools = register(mcp, helpers)
        result_str = tools["analyze_test_strength"](
            path=temp_project, file_filter="calc", function_filter="add"
        )
        result = json.loads(result_str)
        assert "filter_applied" in result
        assert result["filter_applied"]["file"] == "calc"
        assert result["filter_applied"]["function"] == "add"

        names = [v["function"] for v in result.get("top_vulnerable", [])] + result.get(
            "untested_functions", []
        )
        assert all("calc" in n.lower() and "add" in n.lower() for n in names)

    def test_file_filter_no_matches_empty_results(self, temp_project, helpers):
        from mcp_tools.test_effectiveness_tools import register

        class FakeMCP:
            def tool(self):
                return lambda func: func

        mcp = FakeMCP()
        tools = register(mcp, helpers)
        result_str = tools["analyze_test_strength"](
            path=temp_project, file_filter="nonexistent_file"
        )
        result = json.loads(result_str)

        assert "error" not in result
        assert "note" not in result
        assert len(result.get("top_vulnerable", [])) == 0
        assert len(result.get("untested_functions", [])) == 0


class TestInspectTestAssertions:
    """Tests for inspect_test_assertions MCP tool."""

    def test_returns_classified_assertions(self, temp_project, helpers):
        from mcp_tools.test_effectiveness_tools import register

        class FakeMCP:
            def tool(self):
                def decorator(func):
                    return func

                return decorator

        mcp = FakeMCP()
        tools = register(mcp, helpers)
        test_file = os.path.join(temp_project, "tests", "test_calculator.py")
        result_str = tools["inspect_test_assertions"](
            path=temp_project, test_file=test_file
        )
        result = json.loads(result_str)

        assert "test_functions" in result
        assert "summary" in result
        assert result["summary"]["total_assertions"] >= 1
        # New fields emitted by batch-mode refactor (#85, #84)
        assert "sentinel_ratio" in result["summary"]
        assert "analyzed_file_count" in result["summary"]
        assert "total_file_count" in result["summary"]
        # Phase 2 Additive Fields
        assert "mutation_hotspots" in result

    def test_missing_file_returns_error(self, temp_project, helpers):
        from mcp_tools.test_effectiveness_tools import register

        class FakeMCP:
            def tool(self):
                def decorator(func):
                    return func

                return decorator

        mcp = FakeMCP()
        tools = register(mcp, helpers)
        result_str = tools["inspect_test_assertions"](
            path=temp_project, test_file="nonexistent.py"
        )
        result = json.loads(result_str)
        assert "error" in result

    def test_isolated_sentinel_emits_followup_hint(self, helpers):
        """An isolated is_not_none guard emits missing_followup_pattern (#84)."""
        from mcp_tools.test_effectiveness_tools import _inspect_test_assertions_impl

        root = "/tmp/test_sentinel_hint_root"
        import os
        import shutil

        if os.path.exists(root):
            shutil.rmtree(root)
        os.makedirs(root)
        test_file = os.path.join(root, "test_sentinel.py")
        with open(test_file, "w") as f:
            f.write(
                "def test_existence_only():\n    result = fetch()\n    assert result is not None\n"
            )

        result = json.loads(_inspect_test_assertions_impl(root, test_file, helpers))
        func = result["test_functions"].get("test_existence_only", {})
        warnings = func.get("warnings", [])
        sentinels = [w for w in warnings if w["kind"] == "isolated_sentinel"]
        # Should have exactly 1 isolated sentinel warning
        assert len(sentinels) == 1
        assert "missing_followup_pattern" in sentinels[0]
        assert "expected_followup" in sentinels[0]["missing_followup_pattern"]
        assert func.get("has_isolated_sentinel") is True

        # File-level sentinel_ratio should be 1.0 (1 of 1 function)
        assert result["summary"]["sentinel_ratio"] == 1.0

    def test_file_error_is_structured(self, helpers):
        """file_errors emits structured {error_kind, message, file} dicts (#85)."""
        import os
        import shutil

        from mcp_tools.test_effectiveness_tools import _inspect_test_assertions_impl

        root = "/tmp/test_file_error_root"
        if os.path.exists(root):
            shutil.rmtree(root)
        os.makedirs(root)

        # Good file
        good = os.path.join(root, "test_good.py")
        with open(good, "w") as f:
            f.write("def test_ok():\n    assert 1 == 1\n")

        result_json = _inspect_test_assertions_impl(root, "", helpers)
        result = json.loads(result_json)

        # Even without errors, these fields should be present
        assert "file_errors" in result
        assert "analyzed_file_count" in result["summary"]
        assert "total_file_count" in result["summary"]
        assert (
            result["summary"]["analyzed_file_count"]
            == result["summary"]["total_file_count"]
        )


class TestAssertionAnnotation:
    """Tests for new classifier behavior: lgignore annotation and BOOLEAN_CONTRACT_CALL."""

    def test_lgignore_sentinel_annotation(self):
        """# lgignore: sentinel on a line overrides kind to SENTINEL_CHECK (#81)."""
        from lintgate.linters.test_effectiveness.assertion_classifier import (
            classify_test_file,
        )
        from lintgate.linters.test_effectiveness.types import AssertionKind

        source = (
            "def test_annotated():\n"
            "    result = fetch()\n"
            "    assert result is None  # lgignore: sentinel\n"
        )
        assertions = classify_test_file(source)["test_annotated"]
        assert len(assertions) == 1
        a = assertions[0]
        assert a.kind == AssertionKind.SENTINEL_CHECK
        assert a.confidence == "annotated"

    def test_bare_call_classified_as_boolean_contract(self):
        """bare assert fn(args) → BOOLEAN_CONTRACT_CALL with heuristic confidence (#80)."""
        from lintgate.linters.test_effectiveness.assertion_classifier import (
            classify_test_file,
        )
        from lintgate.linters.test_effectiveness.types import AssertionKind

        source = "def test_predicate():\n    assert compute_ratio(data)\n"
        assertions = classify_test_file(source)["test_predicate"]
        assert len(assertions) == 1
        a = assertions[0]
        assert a.kind == AssertionKind.BOOLEAN_CONTRACT_CALL
        assert a.confidence == "heuristic"
