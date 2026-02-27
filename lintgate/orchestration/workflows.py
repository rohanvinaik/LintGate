"""Intent-aware workflows for LintGate MCP."""

from typing import Any


def get_workflow_for_intent(intent: str | None) -> list[dict[str, Any]]:
    """Return a guided workflow for the specified intent."""
    if not intent:
        return []

    intents = {
        "implement_issue": [
            {
                "step": 1,
                "tool": "controlplane_run",
                "when": "Before starting",
                "args_hint": "scope='project'",
                "do_not": "Skip baseline check",
            },
            {
                "step": 2,
                "tool": "lint_files",
                "when": "After writing code",
                "args_hint": "pass specific files",
                "do_not": "Run on whole project",
            },
            {
                "step": 3,
                "tool": "controlplane_run",
                "when": "Before finish",
                "args_hint": "scope='changed'",
                "do_not": "Ship without checking behavior drift",
            },
        ],
        "fix_bug": [
            {
                "step": 1,
                "tool": "controlplane_run",
                "when": "Before starting",
                "args_hint": "channels='tests,behavior'",
                "do_not": "Ignore behavior priors",
            },
            {
                "step": 2,
                "tool": "lint_files",
                "when": "Iterating",
                "args_hint": "pass bug files",
                "do_not": "Forget tests",
            },
        ],
        "refactor": [
            {
                "step": 1,
                "tool": "controlplane_run",
                "when": "Start of refactor",
                "args_hint": "channels='structure,performance'",
                "do_not": "Start without structure baseline",
            },
            {
                "step": 2,
                "tool": "lint_project",
                "when": "Post-refactor",
                "args_hint": "",
                "do_not": "Assume global safety without verify",
            },
        ],
        "explore": [
            {
                "step": 1,
                "tool": "controlplane_run",
                "when": "Once",
                "args_hint": "strictness='relaxed'",
                "do_not": "Worry about warnings",
            }
        ],
        "review": [
            {
                "step": 1,
                "tool": "controlplane_run",
                "when": "To review PR",
                "args_hint": "scope='changed', strictness='strict'",
                "do_not": "Miss security or git flags",
            }
        ],
    }

    return intents.get(intent, [])
