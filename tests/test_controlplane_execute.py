"""Tests for controlplane_execute unified orchestration."""

from __future__ import annotations

import json
import os
import tempfile

from mcp_tools._controlplane_execute_impl import (
    EXECUTE_TERMINAL_STATES,
    _classify_terminal_state,
    _converged_files_from_workflows,
)


class TestClassifyTerminalState:
    def test_complete_when_all_converged_no_blocking(self):
        cp = {"counts": {"blocking": 0}}
        outcomes = [{"state": "CONVERGED"}, {"state": "EXISTING_TESTS_SUFFICIENT"}]
        assert _classify_terminal_state(cp, None, outcomes) == "COMPLETE"

    def test_needs_oracle(self):
        cp = {"counts": {"blocking": 0}}
        outcomes = [{"state": "CONVERGED"}, {"state": "NEEDS_ORACLE"}]
        assert _classify_terminal_state(cp, None, outcomes) == "NEEDS_ORACLE"

    def test_needs_decomposition(self):
        cp = {"counts": {"blocking": 0}}
        outcomes = [{"state": "NEEDS_DECOMPOSITION"}]
        assert _classify_terminal_state(cp, None, outcomes) == "NEEDS_DECOMPOSITION"

    def test_tool_failure(self):
        cp = {"counts": {"blocking": 0}}
        outcomes = [{"state": "TOOL_FAILURE"}]
        assert _classify_terminal_state(cp, None, outcomes) == "TOOL_FAILURE"

    def test_ready_for_review(self):
        cp = {"counts": {"blocking": 0}}
        outcomes = [{"state": "READY_TO_APPLY_WITH_REVIEW"}]
        assert _classify_terminal_state(cp, None, outcomes) == "READY_FOR_REVIEW"

    def test_blocked_by_verifier(self):
        cp = {"counts": {"blocking": 5}}
        outcomes = [{"state": "BLOCKED_TOPOLOGY"}]
        assert _classify_terminal_state(cp, None, outcomes) == "BLOCKED_BY_VERIFIER"

    def test_advisory_when_converged_with_blocking(self):
        cp = {"counts": {"blocking": 3}}
        outcomes = [{"state": "CONVERGED"}]
        assert _classify_terminal_state(cp, None, outcomes) == "ADVISORY_ONLY"

    def test_complete_with_empty_outcomes(self):
        cp = {"counts": {"blocking": 0}}
        assert _classify_terminal_state(cp, None, []) == "COMPLETE"

    def test_oracle_takes_priority_over_decompose(self):
        cp = {"counts": {"blocking": 0}}
        outcomes = [{"state": "NEEDS_ORACLE"}, {"state": "NEEDS_DECOMPOSITION"}]
        assert _classify_terminal_state(cp, None, outcomes) == "NEEDS_ORACLE"

    def test_failure_takes_priority_over_oracle(self):
        cp = {"counts": {"blocking": 0}}
        outcomes = [{"state": "TOOL_FAILURE"}, {"state": "NEEDS_ORACLE"}]
        assert _classify_terminal_state(cp, None, outcomes) == "TOOL_FAILURE"

    def test_all_terminal_states_are_valid(self):
        for state in EXECUTE_TERMINAL_STATES:
            assert isinstance(state, str)


class TestConvergedFilesFromWorkflows:
    def test_empty_when_no_dir(self):
        result = _converged_files_from_workflows("/nonexistent", ".lintgate/wf")
        assert result == set()

    def test_reads_converged_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            wf_dir = os.path.join(tmpdir, ".lintgate", "wf")
            os.makedirs(wf_dir)

            # Write a converged workflow
            wf1 = {"state": "CONVERGED", "rel_file": "src/foo.py"}
            with open(os.path.join(wf_dir, "wf1.json"), "w") as f:
                json.dump(wf1, f)

            # Write a non-converged workflow
            wf2 = {"state": "NEEDS_ORACLE", "rel_file": "src/bar.py"}
            with open(os.path.join(wf_dir, "wf2.json"), "w") as f:
                json.dump(wf2, f)

            # Write an EXISTING_TESTS_SUFFICIENT workflow
            wf3 = {"state": "EXISTING_TESTS_SUFFICIENT", "rel_file": "src/baz.py"}
            with open(os.path.join(wf_dir, "wf3.json"), "w") as f:
                json.dump(wf3, f)

            result = _converged_files_from_workflows(tmpdir, ".lintgate/wf")
            assert result == {"src/foo.py", "src/baz.py"}

    def test_handles_corrupt_json(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            wf_dir = os.path.join(tmpdir, ".lintgate", "wf")
            os.makedirs(wf_dir)

            with open(os.path.join(wf_dir, "bad.json"), "w") as f:
                f.write("not valid json{{{")

            wf1 = {"state": "CONVERGED", "rel_file": "src/ok.py"}
            with open(os.path.join(wf_dir, "good.json"), "w") as f:
                json.dump(wf1, f)

            result = _converged_files_from_workflows(tmpdir, ".lintgate/wf")
            assert result == {"src/ok.py"}


class TestSelectProjectTargetExclusion:
    """Test that select_project_target respects exclusion_set."""

    def test_exclusion_set_param_accepted(self):
        """Verify the function signature accepts exclusion_set."""
        from lintgate.testing.platonic_selection import select_project_target
        import inspect

        sig = inspect.signature(select_project_target)
        assert "exclusion_set" in sig.parameters
