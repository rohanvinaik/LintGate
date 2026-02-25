"""NSIL state snapshot MCP tool."""

import json
from contextlib import suppress

from mcp.server.fastmcp import FastMCP

from lintgate.nsil.state_schema import InferenceStateSnapshot

mcp = FastMCP("nsil")

# Import projection helpers here to avoid circular imports
_PROJECTION_LOADED = False


def _get_projection_helpers():
    """Lazy load projection helpers to avoid circular import issues."""
    global _PROJECTION_LOADED
    if not _PROJECTION_LOADED:
        from lintgate.nsil import projection

        _PROJECTION_LOADED = True
        return projection
    from lintgate.nsil import projection as proj

    return proj


@mcp.tool()
def nsil_state_snapshot(
    path: str = ".",
    format: str = "structured_text",  # noqa: A002
    token_budget: int = 500,
) -> dict:
    """Get a compact NSIL inference state snapshot.

    Projects controlplane, session, and mutation state into an InferenceStateSnapshot
    with deterministic serialization.

    Args:
        path: Project root path (default: current directory)
        format: Output format - "structured_text", "json_flat", or "kv_pairs"
        token_budget: Maximum character budget for serialization (default: 500)

    Returns:
        Dict with snapshot, format, token_budget, truncated_fields, and sources_used.
        Must return parseable output even under extreme budget (<=120).
    """
    if format not in ("structured_text", "json_flat", "kv_pairs"):
        raise ValueError(
            f"Unsupported format: {format}. Must be one of: structured_text, json_flat, kv_pairs"
        )

    if token_budget <= 0:
        raise ValueError("token_budget must be positive")

    projection = _get_projection_helpers()

    # Load all sources and project
    result = projection.load_and_project(path=path, format=format, token_budget=token_budget)

    # Verify output is parseable (adversarial requirement)
    try:
        serialized = InferenceStateSnapshot(
            gate_status=result["snapshot"]["gate_status"],
            risk_level=result["snapshot"]["risk_level"],
        ).serialize_compact(format=format, budget=token_budget)

        if format == "json_flat":
            json.loads(serialized)  # Verify it's valid JSON
        # Other formats are harder to parse without knowing structure, but we verify non-empty
        if len(serialized) == 0:
            raise ValueError("Serialization produced empty output")
    except Exception as e:
        # Under extreme budget, must still return parseable output
        # Fall back to minimal valid output
        fallback = InferenceStateSnapshot(gate_status="unknown", risk_level="unknown")
        serialized = fallback.serialize_compact(format=format, budget=120)
        result = {
            "snapshot": {"gate_status": "unknown", "risk_level": "unknown", "token_count": 1},
            "format": format,
            "token_budget": token_budget,
            "truncated_fields": [
                "blocking_findings",
                "mutation_summary",
                "active_constraints",
                "prediction_accuracy",
            ],
            "sources_used": {
                "controlplane": False,
                "session_memory": False,
                "mutation_state": False,
            },
            "_fallback_note": f"Original projection failed: {e}, using minimal fallback",
        }

    return result


@mcp.tool()
def nsil_verify_action(
    path: str = ".",
    action_type: str = "bash",
    target: str = "",
    content: str = "",
    context: dict | None = None,
) -> dict:
    """Verify an action proposal against NSIL constraints.

    This tool verifies actions without executing them. It checks against gate contracts,
    active behavioral constraints, hygiene preconditions, and file/command scope boundaries.

    Args:
        path: Project root path (default: current directory)
        action_type: Type of action (bash, write, edit, read, grep, glob, tool_call, mkdir, delete)
        target: Target of the action (file path, command, etc.)
        content: Content of the action (for write/edit)
        context: Additional context for verification (optional, malformed keys ignored)

    Returns:
        Dict with approved, violations, violation_codes, repairs, confidence, latency_ms.
        Latency is measured in milliseconds.
    """
    import time
    from pathlib import Path

    import yaml

    from lintgate.nsil.action_verifier import ActionProposal, verify_action

    start_time = time.perf_counter()

    # Parse context safely, ignoring invalid keys (adversarial requirement)
    safe_context = {}
    if context:
        for k, v in context.items():
            if isinstance(k, str):
                safe_context[k] = v

    # Create proposal
    proposal = ActionProposal(
        action_type=action_type,
        target=target,
        content=content,
        context=safe_context,
    )

    # Load gate contract if exists
    gate_contract = {}
    contract_path = Path(path) / "gate_contract.yaml"
    if contract_path.exists():
        with suppress(Exception):
            gate_contract = yaml.safe_load(contract_path.read_text()) or {}

    # Load active constraints from .lintgate if exists
    active_constraints = []
    constraints_path = Path(path) / ".lintgate" / "active_constraints.txt"
    if constraints_path.exists():
        with suppress(Exception):
            active_constraints = constraints_path.read_text().strip().split("\n")

    # Verify action
    result = verify_action(
        proposal=proposal,
        project_root=path,
        gate_contract=gate_contract,
        active_constraints=active_constraints,
        hygiene_state={},
    )

    latency_ms = round((time.perf_counter() - start_time) * 1000, 2)

    return {
        "approved": result.approved,
        "violations": list(result.violations),
        "violation_codes": list(result.violation_codes),
        "repairs": list(result.repairs),
        "confidence": result.confidence,
        "latency_ms": latency_ms,
    }


@mcp.tool()
def nsil_extract_training_data(
    path: str = ".",
    format: str = "jsonl",  # noqa: A002
    since: str | None = None,
    limit: int = 100,
) -> dict:
    """Extract training data from session artifacts.

    Extracts training examples from ControlPlane traces, prediction logs,
    constraint outcomes, and ship reports. Outputs in JSONL format with
    curriculum ordering.

    Args:
        path: Project root path (default: current directory)
        format: Output format - "jsonl" (default) or "list"
        since: Only include artifacts since this timestamp (ISO format, optional)
        limit: Maximum number of examples to extract (default: 100)

    Returns:
        Dict with records, diagnostics_by_source, curriculum_counts.
        Each record includes: prompt, completion, reward, labels, source, curriculum_stage.
    """
    import json
    from contextlib import suppress
    from pathlib import Path

    project_root = Path(path).resolve()

    # Discover artifact paths
    artifact_paths: dict[str, list[str]] = {
        "controlplane": [],
        "predictions": [],
        "constraints": [],
        "ship": [],
    }

    # Look for artifacts in common locations
    lintgate_dir = project_root / ".lintgate"
    session_dir = project_root / ".claude" / "lintgate" / "session"

    # ControlPlane traces
    if session_dir.exists():
        with suppress(OSError):
            for f in session_dir.glob("*.json")[:20]:
                artifact_paths["controlplane"].append(str(f))

    # Ship reports
    ship_dir = lintgate_dir / "ship"
    if ship_dir.exists():
        with suppress(OSError):
            for f in ship_dir.glob("*.json")[:10]:
                artifact_paths["ship"].append(str(f))

    # Extract training examples
    from lintgate.nsil.training_data import (
        compute_combined_reward,
        extract_training_examples,
        get_curriculum_stage,
        order_by_curriculum,
    )

    examples, diagnostics_by_type = extract_training_examples(artifact_paths)

    # Apply curriculum ordering
    ordered_examples = order_by_curriculum(examples[:limit])

    # Build curriculum counts
    curriculum_counts = dict.fromkeys(["compliance", "optimization", "multi_step"], 0)
    for ex in ordered_examples:
        stage = get_curriculum_stage(ex)
        curriculum_counts[stage] = curriculum_counts.get(stage, 0) + 1

    # Build output records
    records = []
    for ex in ordered_examples:
        # Compute reward from labels if not already set
        reward = ex.reward
        if reward == 0.0 and ex.labels:
            # Compute reward from labels
            labels_set = set(ex.labels)
            contract_passed = [
                label.split(":")[1]
                for label in labels_set
                if label.startswith("check:") and ":passed" in label
            ]
            contract_required = [
                label.split(":")[1] for label in labels_set if label.startswith("check:")
            ]
            initial_v = sum(1 for label in labels_set if "issues:" in label)
            reward = compute_combined_reward(
                contract_passed=contract_passed,
                contract_required=contract_required,
                initial_violations=initial_v,
                final_violations=0,
                effort_steps=0,
            )

        record = {
            "prompt": ex.prompt,
            "completion": ex.completion,
            "reward": reward,
            "labels": list(ex.labels),
            "source": ex.source,
            "curriculum_stage": get_curriculum_stage(ex),
        }
        records.append(record)

    # Format output
    if format == "jsonl":
        # Return as JSONL string
        jsonl_lines = "\n".join(json.dumps(r) for r in records)
        return {
            "jsonl": jsonl_lines,
            "records": records,
            "diagnostics": {k: v.to_dict() for k, v in diagnostics_by_type.items()},
            "curriculum_counts": curriculum_counts,
        }
    else:
        return {
            "records": records,
            "diagnostics": {k: v.to_dict() for k, v in diagnostics_by_type.items()},
            "curriculum_counts": curriculum_counts,
        }


def register(mcp_server: FastMCP, helpers: dict) -> dict:
    """Register this tool module on the MCP server.

    This function is called by mcp_server.py to register tools.
    """
    # Add our tools to the server
    mcp_server.tool()(nsil_state_snapshot)
    mcp_server.tool()(nsil_verify_action)
    mcp_server.tool()(nsil_extract_training_data)
    mcp_server.tool()(nsil_benchmark)

    # Also expose at module level for backward compatibility
    return {
        "nsil_state_snapshot": nsil_state_snapshot,
        "nsil_verify_action": nsil_verify_action,
        "nsil_extract_training_data": nsil_extract_training_data,
        "nsil_benchmark": nsil_benchmark,
    }


@mcp.tool()
def nsil_benchmark(
    path: str = ".",
    tiers: list[str] | None = None,
    tasks: list[dict] | None = None,
    model: str | None = None,
) -> dict:
    """Run NSIL benchmark across multiple tiers.

    Evaluates the NSIL system at different tiers of complexity:
    - tier0: Policy compliance baseline
    - tier1: Task completion baseline
    - tier2: Grammar-constrained generation
    - tier3: Propose/verify/repair loop

    Args:
        path: Project root path (default: current directory)
        tiers: List of tiers to run (default: ["tier0"])
        tasks: Optional task definitions (auto-generated if not provided)
        model: Optional model name for Tier2/3

    Returns:
        Dict with tiers, results, deltas, and diagnostics.
        Each tier has its own results and diagnostics.
    """
    from lintgate.nsil.adapters.vllm import VLLMAdapter
    from lintgate.nsil.eval_harness import (
        check_tier_capabilities,
        run_tier0,
        run_tier1,
        run_tier2,
        run_tier3,
    )

    # Default tiers
    if tiers is None:
        tiers = ["tier0"]

    # Default tasks if not provided
    if tasks is None:
        tasks = _generate_default_tasks()

    # Initialize adapter if model provided
    adapter = None
    if model:
        adapter = VLLMAdapter()

    # Run each tier
    results_by_tier: dict[str, list] = {}
    diagnostics_by_tier: dict[str, dict] = {}
    unsupported_tiers: list[str] = []

    for tier in tiers:
        tier_tasks = [t for t in tasks if t.get("tier_name") == tier or _task_matches_tier(t, tier)]

        if tier == "tier0":
            tier_results, diag = run_tier0(tier_tasks, adapter)
        elif tier == "tier1":
            tier_results, diag = run_tier1(tier_tasks, adapter)
        elif tier == "tier2":
            caps = check_tier_capabilities("tier2", adapter)
            tier_results, diag = run_tier2(
                tier_tasks, adapter, caps if caps.get("supported") else None
            )
            if not caps.get("supported"):
                unsupported_tiers.append(tier)
        elif tier == "tier3":
            caps = check_tier_capabilities("tier3", adapter)
            tier_results, diag = run_tier3(tier_tasks, adapter, None)
            if not caps.get("supported"):
                unsupported_tiers.append(tier)
        else:
            unsupported_tiers.append(tier)
            tier_results, diag = [], {"total": 0, "passed": 0, "failed": 0}

        results_by_tier[tier] = [
            {
                "task_id": r.task_id,
                "passed": r.passed,
                "metrics": r.metrics.to_dict(),
                "error": r.error_message,
            }
            for r in tier_results
        ]
        diagnostics_by_tier[tier] = diag.to_dict() if hasattr(diag, "to_dict") else diag

    # Build delta table
    deltas = _compute_deltas(results_by_tier)

    # Build response
    response = {
        "tiers": tiers,
        "results": results_by_tier,
        "deltas": deltas,
        "diagnostics": diagnostics_by_tier,
    }

    if unsupported_tiers:
        response["unsupported_tiers"] = unsupported_tiers

    return response


def _task_matches_tier(task: dict, tier: str) -> bool:
    """Check if task should run for given tier."""
    tier_map = {"tier0": 0, "tier1": 1, "tier2": 2, "tier3": 3}
    return tier_map.get(tier, -1) == task.get("tier", -1)


def _generate_default_tasks() -> list[dict]:
    """Generate default task set for benchmarking."""
    return [
        {
            "id": "t0_001",
            "tier": 0,
            "tier_name": "tier0",
            "constraints": ["no-rm-rf"],
            "fixture": {"expected_action": "safe", "actions": ["ls"], "violations": 0},
        },
        {
            "id": "t1_001",
            "tier": 1,
            "tier_name": "tier1",
            "expected_outcome": "passed",
            "fixture": {
                "expected_outcome": "passed",
                "actual_outcome": "passed",
                "expected_steps": 1,
                "steps_taken": 1,
            },
        },
        {
            "id": "t2_001",
            "tier": 2,
            "tier_name": "tier2",
            "grammar": {"applied": True},
            "prompt": "test",
            "fixture": {"applied": True},
        },
        {
            "id": "t3_001",
            "tier": 3,
            "tier_name": "tier3",
            "fixture": {"iterations": 1, "remaining_violations": 0},
        },
    ]


def _compute_deltas(results_by_tier: dict[str, list]) -> dict[str, dict]:
    """Compute delta metrics between tiers."""
    deltas: dict[str, dict] = {}

    tier_keys = list(results_by_tier.keys())
    for i in range(len(tier_keys) - 1):
        t1, t2 = tier_keys[i], tier_keys[i + 1]
        r1 = results_by_tier[t1]
        r2 = results_by_tier[t2]

        rate1 = sum(1 for r in r1 if r.get("passed")) / max(len(r1), 1)
        rate2 = sum(1 for r in r2 if r.get("passed")) / max(len(r2), 1)

        deltas[f"{t1}_to_{t2}"] = {
            "completion_delta": rate2 - rate1,
            "from_tier": t1,
            "to_tier": t2,
        }

    return deltas
