"""Integration tests for the end-to-end orchestration chain.

Verifies the next_actions chain:
  test_hygiene_scan → test_triage → test_infer_inputs → test_characterize
  → mutation_run_sampling → mutation_prescribe → mutation_prescribe_tests
  → mutation_validate_tests → test_hygiene_scan (re-scan)
"""

from __future__ import annotations

import json
import textwrap


def _load_tool_result(json_str):
    import json
    import os
    r = json.loads(json_str)
    if isinstance(r, dict) and "file" in r and "analysis_id" in r and os.path.isfile(r.get("file","")):
        with open(r["file"]) as f:

            return json.loads(f.read())
    return r



def _build_mini_project(tmp_path):
    """Create a minimal project with source and test files."""
    src = tmp_path / "mylib"
    src.mkdir()
    (src / "__init__.py").touch()
    (src / "core.py").write_text(
        textwrap.dedent("""\
        def compute(x: int, y: int) -> int:
            if x > y:
                return x - y
            return x + y

        def helper():
            return 42
        """)
    )

    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "__init__.py").touch()
    (tests / "test_core.py").write_text(
        textwrap.dedent("""\
        from mylib.core import helper

        def test_helper():
            assert helper() == 42

        def test_stub():
            pass
        """)
    )
    return tmp_path


class TestOrchestrationChain:
    """Test that next_actions chain forms a complete loop."""

    def test_hygiene_scan_suggests_triage(self, tmp_path):
        """test_hygiene_scan → test_triage (via THYGIENE001 stubs)."""
        project = _build_mini_project(tmp_path)

        from lintgate.channels.test_hygiene_channel import TestHygieneChannel
        from lintgate.controlplane.types import ControlPlaneConfig, SupervisionEvent

        ch = TestHygieneChannel()
        event = SupervisionEvent(surface="mcp", project_root=str(project))
        config = ControlPlaneConfig(enabled=True)
        result = ch.execute(event, config)

        # Should find the stub
        assert any(f.kind == "THYGIENE001" for f in result.findings)

    def test_triage_suggests_infer_inputs(self, tmp_path):
        """test_triage → test_infer_inputs (first untested function)."""
        project = _build_mini_project(tmp_path)

        class FakeMCP:
            def tool(self):
                def dec(fn):
                    return fn

                return dec

        from mcp_tools.cold_start_tools import register

        tools = register(FakeMCP(), {"_validate_project_root": lambda p: p})
        result = _load_tool_result(tools["test_triage"](path=str(project)))

        assert result["total_untested"] >= 1
        assert len(result["next_actions"]) >= 1
        assert result["next_actions"][0]["tool"] == "test_infer_inputs"

    def test_infer_inputs_suggests_characterize(self, tmp_path):
        """test_infer_inputs → test_characterize."""
        project = _build_mini_project(tmp_path)

        class FakeMCP:
            def tool(self):
                def dec(fn):
                    return fn

                return dec

        from mcp_tools.cold_start_tools import register

        tools = register(FakeMCP(), {"_validate_project_root": lambda p: p})
        result = _load_tool_result(tools["test_infer_inputs"](path=str(project), file="mylib/core.py", function="compute")
        )

        assert len(result["next_actions"]) >= 1
        assert result["next_actions"][0]["tool"] == "test_characterize"

    def test_characterize_suggests_mutation(self, tmp_path):
        """test_characterize → mutation_run_sampling."""
        project = _build_mini_project(tmp_path)

        class FakeMCP:
            def tool(self):
                def dec(fn):
                    return fn

                return dec

        from mcp_tools.cold_start_tools import register

        tools = register(FakeMCP(), {"_validate_project_root": lambda p: p})
        result = _load_tool_result(tools["test_characterize"](path=str(project), file="mylib/core.py", function="compute")
        )

        assert result["maturity"] == "unchecked"
        assert any(a["tool"] == "mutation_run_sampling" for a in result["next_actions"])

    def test_hygiene_tool_suggests_triage_for_stubs(self, tmp_path):
        """MCP test_hygiene_scan next_actions includes mutation_prescribe_tests for stubs."""
        project = _build_mini_project(tmp_path)

        class FakeMCP:
            def tool(self):
                def dec(fn):
                    self._fn = fn
                    return fn

                return dec

        from mcp_tools.test_hygiene_tools import register

        tools = register(FakeMCP(), {"_validate_project_root": lambda p: p})
        result = _load_tool_result(tools["test_hygiene_scan"](path=str(project)))

        # Should have findings
        assert len(result["findings"]) >= 1
        # next_actions should include mutation_prescribe_tests for THYGIENE001
        action_tools = {a["tool"] for a in result["next_actions"]}
        assert "mutation_prescribe_tests" in action_tools

    def test_redundancy_suggests_hygiene(self, tmp_path):
        """test_redundancy_project → test_hygiene_scan."""
        # Create fake profiled data with redundant tests
        cache_dir = tmp_path / ".lintgate" / "mutation"
        cache_dir.mkdir(parents=True)
        state = {
            "function_key": "mod.py::func",
            "coverage_depth": "profiled",
            "kill_matrix": {
                "VALUE:line1": ["test_a", "test_b"],
            },
        }
        (cache_dir / "mod_py__func.json").write_text(json.dumps(state))

        class FakeMCP:
            def tool(self):
                def dec(fn):
                    return fn

                return dec

        from mcp_tools.redundancy_tools import register

        tools = register(FakeMCP(), {"_validate_project_root": lambda p: p})
        result = _load_tool_result(tools["test_redundancy_project"](path=str(tmp_path)))

        # Both tests have zero unique kills
        assert result["zero_unique_kill_count"] == 2
        action_tools = {a["tool"] for a in result["next_actions"]}
        assert "test_hygiene_scan" in action_tools


class TestFullChainNextActions:
    """Verify that every tool in the chain produces valid next_actions."""

    def test_next_actions_have_required_fields(self, tmp_path):
        """All next_actions must have tool, args, reason, priority."""
        project = _build_mini_project(tmp_path)

        class FakeMCP:
            def tool(self):
                def dec(fn):
                    return fn

                return dec

        from mcp_tools.cold_start_tools import register as reg_cold
        from mcp_tools.test_hygiene_tools import register as reg_hygiene

        hygiene = reg_hygiene(FakeMCP(), {"_validate_project_root": lambda p: p})
        cold = reg_cold(FakeMCP(), {"_validate_project_root": lambda p: p})

        # Collect all next_actions from tools
        results = [
            _load_tool_result(hygiene["test_hygiene_scan"](path=str(project))),
            _load_tool_result(cold["test_triage"](path=str(project))),
            _load_tool_result(
                cold["test_infer_inputs"](
                    path=str(project), file="mylib/core.py", function="compute"
                )
            ),
            _load_tool_result(
                cold["test_characterize"](
                    path=str(project), file="mylib/core.py", function="compute"
                )
            ),
        ]

        for result in results:
            for action in result.get("next_actions", []):
                assert "tool" in action, f"Missing 'tool' in next_action: {action}"
                assert "reason" in action, f"Missing 'reason' in next_action: {action}"
                assert "priority" in action, f"Missing 'priority' in next_action: {action}"
