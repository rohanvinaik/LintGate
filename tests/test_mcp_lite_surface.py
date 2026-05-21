"""Tests for the lite MCP server surface — tool count, next_actions filtering."""
from __future__ import annotations

import json

from mcp_server_lite import _FULL_TO_LITE, _filter


class TestFilterNextActions:
    """_filter rewrites next_actions to only reference lite tools."""

    def test_rewrites_full_name_to_lite(self) -> None:
        response = json.dumps({
            "summary": "ok",
            "next_actions": [{"tool": "controlplane_run", "args": {"path": "."}}],
        })
        result = json.loads(_filter(response))
        assert result["next_actions"][0]["tool"] == "check_project"

    def test_drops_unknown_tools(self) -> None:
        response = json.dumps({
            "summary": "ok",
            "next_actions": [
                {"tool": "some_internal_tool", "args": {}},
                {"tool": "lint_fix", "args": {"path": "."}},
            ],
        })
        result = json.loads(_filter(response))
        assert len(result["next_actions"]) == 1
        assert result["next_actions"][0]["tool"] == "fix_lint"

    def test_passthrough_without_next_actions(self) -> None:
        response = json.dumps({"summary": "ok", "analysis_id": "abc"})
        assert _filter(response) == response

    def test_passthrough_empty_next_actions(self) -> None:
        response = json.dumps({"summary": "ok", "next_actions": []})
        result = json.loads(_filter(response))
        assert result["next_actions"] == []

    def test_passthrough_non_json(self) -> None:
        assert _filter("not json") == "not json"

    def test_preserves_other_fields(self) -> None:
        response = json.dumps({
            "analysis_id": "xyz",
            "summary": "ok",
            "file": "/tmp/analysis.json",
            "next_actions": [{"tool": "platonic_apply", "args": {"path": "."}}],
        })
        result = json.loads(_filter(response))
        assert result["analysis_id"] == "xyz"
        assert result["summary"] == "ok"
        assert result["file"] == "/tmp/analysis.json"
        assert result["next_actions"][0]["tool"] == "apply"

    def test_identity_mapped_tools_preserved(self) -> None:
        response = json.dumps({
            "next_actions": [{"tool": "after_edit", "args": {"path": "."}}],
        })
        result = json.loads(_filter(response))
        assert result["next_actions"][0]["tool"] == "after_edit"

    def test_platonic_continue_maps_to_converge(self) -> None:
        response = json.dumps({
            "next_actions": [{"tool": "platonic_continue", "args": {}}],
        })
        result = json.loads(_filter(response))
        assert result["next_actions"][0]["tool"] == "converge"

    def test_mutation_prescribe_maps_to_improve_tests(self) -> None:
        response = json.dumps({
            "next_actions": [{"tool": "mutation_prescribe", "args": {}}],
        })
        result = json.loads(_filter(response))
        assert result["next_actions"][0]["tool"] == "improve_tests"


class TestFullToLiteMapping:
    """The reverse mapping covers all expected full-server tool names."""

    def test_mapping_has_core_tools(self) -> None:
        expected = {
            "controlplane_run", "controlplane_get_details", "lint_fix",
            "controlplane_apply_repairs", "after_edit", "before_commit",
            "spec_file_analyze", "mutation_run_full", "mutation_prescribe",
            "platonic_converge", "platonic_apply", "mutation_decompose",
            "refactor_extract_method", "mutation_clear_state",
            "mutation_prescribe_tests",
        }
        assert expected.issubset(set(_FULL_TO_LITE.keys()))

    def test_all_lite_names_are_valid(self) -> None:
        """Every lite name in the mapping should be a real tool on the lite surface."""
        expected_lite_tools = {
            "getting_started", "check_project", "get_details", "fix_lint",
            "apply_repairs", "after_edit", "before_commit", "spec_analyze",
            "improve_tests", "generate_tests", "converge", "apply",
            "decompose", "simplify", "reset_state",
            "triage_tests", "compact_tests",
        }
        lite_names = set(_FULL_TO_LITE.values())
        assert lite_names.issubset(expected_lite_tools)
