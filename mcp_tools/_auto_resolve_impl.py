"""Implementation for auto-resolve and auto-sweep MCP tools.

Two-phase design: the MCP tool never makes API calls. The calling LLM
IS the intelligence — the tool handles cache, synthesis gate, and
deterministic verification. When those are exhausted, it returns a
constrained generation prompt for the caller to fill.
"""

from __future__ import annotations

from typing import Any

from lintgate.next_action import NextAction, serialize_next_actions


def impl_auto_resolve(
    path: str,
    target: str,
    helpers: dict[str, Any],
    proposed_body: str = "",
    verify_with_pytest: bool = False,
    write_to_file: bool = True,
) -> dict[str, Any]:
    """Resolve a single function autonomously.

    Phase 1 (no proposed_body): cache → synthesis gate → return prompt.
    Phase 2 (with proposed_body): verify → accept or return failures.
    """
    from lintgate.specification.prescriptive.auto_resolve import (
        ResolveConfig,
        resolve_one,
    )
    from lintgate.specification.prescriptive.backends import select_backend
    from lintgate.specification.prescriptive.persistence import load_spec

    project_root = helpers["_validate_project_root"](path)
    spec = load_spec(project_root, target)

    if spec is None:
        return {
            "error": f"No spec for '{target}'. Run prescriptive_spec_compose first.",
            "next_actions": serialize_next_actions([
                NextAction(
                    tool="prescriptive_spec_compose",
                    args={"path": path, "target": target},
                    reason="Compose a spec before resolving",
                ),
            ]),
        }

    config = ResolveConfig(
        verify_with_pytest=verify_with_pytest,
        write_to_file=write_to_file,
    )
    backend = select_backend(spec)
    targets = backend.compile(spec)
    result = resolve_one(spec, targets, project_root, config, proposed_body=proposed_body)

    response = result.to_dict()
    next_actions: list[NextAction] = []

    if result.status in ("resolved", "cached"):
        next_actions.append(NextAction(
            tool="prescriptive_spec_verify",
            args={"path": path, "target": target},
            reason="Verify resolved code against full spec",
        ))
    elif result.status == "needs_generation":
        # Caller should generate code using the prompt, then re-call with proposed_body
        next_actions.append(NextAction(
            tool="auto_resolve",
            args={"path": path, "target": target, "proposed_body": "<your generated body>"},
            reason="Generate the function body from the prompt above, then pass it back for verification",
        ))
    elif result.status == "failed":
        # Verification failed — caller can retry with updated body
        next_actions.append(NextAction(
            tool="auto_resolve",
            args={"path": path, "target": target, "proposed_body": "<corrected body>"},
            reason="Fix the issues listed in retry_constraints and resubmit",
        ))

    response["next_actions"] = serialize_next_actions(next_actions)
    return response


def impl_auto_sweep(
    path: str,
    helpers: dict[str, Any],
    scope: str = "all",
    max_targets: int = 50,
    verify_with_pytest: bool = False,
) -> dict[str, Any]:
    """Sweep project — resolve deterministic cases, return prompts for the rest."""
    from lintgate.specification.prescriptive.auto_resolve import ResolveConfig
    from lintgate.specification.prescriptive.auto_sweep import sweep

    project_root = helpers["_validate_project_root"](path)
    config = ResolveConfig(
        verify_with_pytest=verify_with_pytest,
        write_to_file=True,
    )
    manifest = sweep(project_root, scope=scope, max_targets=max_targets, config=config)
    response = manifest.to_dict()

    next_actions: list[NextAction] = []

    # Collect targets that need caller generation
    needs_gen = [
        r for r in manifest.results
        if isinstance(r, dict) and r.get("status") == "needs_generation"
    ]
    if needs_gen:
        next_actions.append(NextAction(
            tool="auto_resolve",
            args={"path": path, "target": needs_gen[0].get("target_key", "")},
            reason=f"{len(needs_gen)} specs need caller generation — resolve one at a time",
        ))

    if manifest.failed > 0:
        next_actions.append(NextAction(
            tool="prescriptive_spec_status",
            args={"path": path},
            reason=f"{manifest.failed} specs failed verification — review status",
        ))
    if manifest.resolved > 0:
        next_actions.append(NextAction(
            tool="prescriptive_spec_verify",
            args={"path": path, "target": ""},
            reason="Verify all resolved code",
        ))

    response["next_actions"] = serialize_next_actions(next_actions)
    return response
