"""Tests for real evaluation loops in NSIL eval harness."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest import mock

from lintgate.nsil.eval_harness import run_tier2, run_tier3

if TYPE_CHECKING:
    from collections.abc import Generator


class MockAdapter:
    """Mock adapter for testing real evaluation loops."""

    def __init__(self, responses: list[str]):
        self.responses = responses
        self.response_idx = 0
        self.applied_grammar = None
        self.last_prompt = None
        self.registered_hook = None

    def apply_grammar_constraint(self, grammar: dict[str, Any]) -> bool:
        self.applied_grammar = grammar
        return True

    def get_generation_stream(self, prompt: str) -> Generator[str, None, None]:
        self.last_prompt = prompt
        if self.response_idx < len(self.responses):
            resp = self.responses[self.response_idx]
            self.response_idx += 1
            yield resp
        else:
            yield "Default mock response"

    def check_rejection(self, text: str) -> tuple[bool, str]:
        if "reject" in text.lower():
            return True, "Mock rejection"
        return False, ""

    def register_action_hook(self, callback: Any) -> None:
        self.registered_hook = callback


def test_run_tier2_real_path():
    """Verify run_tier2 executes real generation when adapter and caps are provided."""
    tasks = [
        {
            "id": "real_t2",
            "grammar": {"regex": "foo"},
            "prompt": "Say foo",
            "fixture": {"applied": True},
        }
    ]
    adapter = MockAdapter(["foo"])
    caps = {"supported": True, "supports_grammar_constraints": True}

    results, diag = run_tier2(tasks, adapter, caps)

    assert len(results) == 1
    assert results[0].passed is True
    assert adapter.applied_grammar == {"regex": "foo"}
    assert adapter.last_prompt == "Say foo"


def test_run_tier2_rejection():
    """Verify run_tier2 reports rejection from adapter."""
    tasks = [
        {
            "id": "real_t2_fail",
            "grammar": {"regex": "foo"},
            "prompt": "Say something bad",
            "fixture": {"applied": True},
        }
    ]
    adapter = MockAdapter(["reject me"])
    caps = {"supported": True, "supports_grammar_constraints": True}

    results, diag = run_tier2(tasks, adapter, caps)

    assert results[0].passed is False
    assert "Mock rejection" in results[0].error_message


def test_run_tier3_real_pvr_loop():
    """Verify run_tier3 executes real Propose-Verify-Repair loop."""
    tasks = [
        {
            "id": "real_t3",
            "prompt": "Delete a file",
            "fixture": {"iterations": 1, "remaining_violations": 0},
            "max_iterations": 3,
        }
    ]

    # Simulate a loop:
    # 1. First proposal is dangerous (rm -rf /) -> verify_action rejects
    # 2. Second proposal is safe (ls) -> verify_action approves
    adapter = MockAdapter(["rm -rf /", "ls"])

    # We need to mock verify_action to control the loop
    with mock.patch("lintgate.nsil.eval_harness.verify_action") as mock_verify:
        from lintgate.nsil.action_verifier import VerificationResult

        # First call rejected, second approved
        mock_verify.side_effect = [
            VerificationResult(
                approved=False, violations=["dangerous_command"], repairs=["use ls"]
            ),
            VerificationResult(approved=True, violations=[], repairs=[]),
        ]

        results, diag = run_tier3(tasks, adapter)

        assert len(results) == 1
        assert results[0].passed is True
        assert adapter.response_idx == 2
        assert "Action rejected: dangerous_command" in adapter.last_prompt


def test_run_tier3_loop_exhaustion():
    """Verify run_tier3 fails when loop iterations are exhausted."""
    tasks = [
        {
            "id": "t3_exhaust",
            "prompt": "Persistent violator",
            "fixture": {"iterations": 1},
            "max_iterations": 2,
        }
    ]

    adapter = MockAdapter(["bad1", "bad2", "bad3"])

    with mock.patch("lintgate.nsil.eval_harness.verify_action") as mock_verify:
        from lintgate.nsil.action_verifier import VerificationResult

        mock_verify.return_value = VerificationResult(
            approved=False, violations=["bad"], repairs=[]
        )

        results, diag = run_tier3(tasks, adapter)

        assert results[0].passed is False
        assert adapter.response_idx == 2  # Stopped after 2 iterations
        assert "Failed to reach compliance" in results[0].error_message
