"""Tests for automatic decomposition routing (M5)."""

from __future__ import annotations

from unittest.mock import patch

from mcp_tools._platonic_impl import _attempt_auto_decomposition


class TestAttemptAutoDecomposition:
    def test_returns_none_on_exception(self):
        result = _attempt_auto_decomposition("/nonexistent", [])
        assert result is None

    @patch("mcp_tools.convergence_tools._impl_extraction_plan")
    def test_returns_none_when_no_steps(self, mock_plan):
        mock_plan.return_value = {"steps": []}

        class FakeTarget:
            function_key = "mod::func"

        result = _attempt_auto_decomposition("/proj", [FakeTarget()])
        assert result is None

    @patch("mcp_tools.convergence_tools._impl_extraction_plan")
    def test_routes_create_module_to_refactor_move(self, mock_plan):
        mock_plan.return_value = {
            "steps": [
                {
                    "step_type": "create_module",
                    "source_file": "src/big.py",
                    "target_module": "src/extracted.py",
                    "symbols": ["helper_a", "helper_b"],
                }
            ]
        }

        class FakeTarget:
            function_key = "src.big::helper_a"

        result = _attempt_auto_decomposition("/proj", [FakeTarget()])
        assert result is not None
        assert result["verified"] is True
        assert result["tool"] == "refactor_move"
        assert result["args"]["dry_run"] is True
        assert result["args"]["source"] == "src/big.py"

    @patch("mcp_tools.convergence_tools._impl_extraction_plan")
    def test_routes_extract_body_to_refactor_extract_method(self, mock_plan):
        mock_plan.return_value = {
            "steps": [
                {
                    "step_type": "extract_body",
                    "source_file": "src/big.py",
                }
            ]
        }

        class FakeTarget:
            function_key = "src.big::complex_func"

        result = _attempt_auto_decomposition("/proj", [FakeTarget()])
        assert result is not None
        assert result["verified"] is True
        assert result["tool"] == "refactor_extract_method"
        assert result["args"]["dry_run"] is True

    @patch("mcp_tools.convergence_tools._impl_extraction_plan")
    def test_returns_none_for_unknown_step_type(self, mock_plan):
        mock_plan.return_value = {
            "steps": [{"step_type": "unknown_action"}]
        }

        class FakeTarget:
            function_key = "mod::func"

        result = _attempt_auto_decomposition("/proj", [FakeTarget()])
        assert result is None

    @patch("mcp_tools.convergence_tools._impl_extraction_plan")
    def test_handles_string_target(self, mock_plan):
        """When decompose_targets contains strings instead of objects."""
        mock_plan.return_value = {
            "steps": [
                {
                    "step_type": "create_function",
                    "source_file": "src/big.py",
                }
            ]
        }
        result = _attempt_auto_decomposition("/proj", ["mod::func"])
        assert result is not None
        assert result["tool"] == "refactor_extract_method"
