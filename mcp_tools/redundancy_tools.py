"""Project-wide mutation redundancy analysis.

Aggregates profiled kill matrices across cached mutation runs to identify:
- THYGIENE004: Tests with zero unique mutant kills
- Minimal covering set (greedy set cover)
- File-level subsumption candidates

Only uses profiled (exhaustive) mutation data for deletion-grade confidence.
"""

from __future__ import annotations

import json
from collections import defaultdict
from typing import Any


from mcp_tools._disk_helpers import tool_response

def register(mcp: Any, helpers: Any) -> dict[str, Any]:
    """Register redundancy analysis tools on the shared MCP instance."""

    @mcp.tool()
    def test_redundancy_project(
        path: str,
        top_n: int = 200,
    ) -> str:
        """Project-wide mutation redundancy analysis from cached kill matrices.

        WHEN TO USE: After running mutation_run_full on key functions. Identifies
        tests with zero unique kills (safe to remove) and computes the minimal
        covering set.

        Only uses profiled (exhaustive) mutation data for deletion-grade confidence.

        Args:
            path: Project root path.
            top_n: Maximum number of redundancy findings to return.
        """
        import json as _json
        project_root = helpers["_validate_project_root"](path)
        result_json = _impl_redundancy_project(project_root, top_n)
        result = _json.loads(result_json)
        n_tests = result.get("total_tests_in_matrix", 0)
        redundant = result.get("redundant_test_count", 0)
        summary = f"Redundancy: {n_tests} tests. {redundant} redundant."
        return tool_response(
            result, "test_redundancy_project", project_root, summary,
            next_actions=result.get("next_actions"),
        )

    return {"test_redundancy_project": test_redundancy_project}


def _impl_redundancy_project(project_root: str, top_n: int) -> str:
    """Core implementation of project-wide redundancy analysis."""
    from mcp_tools._mutation_impl import get_cache_dir, iter_cached_states

    cache_dir = get_cache_dir(project_root)
    all_states = iter_cached_states(cache_dir)

    # Only use profiled data (exhaustive mutation runs with kill matrices)
    profiled = [s for s in all_states if s.get("coverage_depth") == "profiled"]

    if not profiled:
        return json.dumps(
            {
                "status": "no_profiled_data",
                "message": "No profiled mutation data found. Run mutation_run_full on key functions first.",
                "next_actions": [
                    {
                        "tool": "mutation_run_full",
                        "args": {"path": project_root},
                        "reason": "Generate exhaustive kill matrices for redundancy analysis",
                        "priority": 1,
                    }
                ],
            },
            indent=2,
        )

    # Build global kill matrix: test_name → set of mutants killed
    test_kills: dict[str, set[str]] = defaultdict(set)
    all_mutants: set[str] = set()
    function_count = 0

    for state in profiled:
        func_key = state.get("function_key", "")
        kill_matrix = state.get("kill_matrix", {})
        function_count += 1

        for mutant_desc, killing_tests in kill_matrix.items():
            # Namespace mutants by function to avoid collisions
            namespaced = f"{func_key}::{mutant_desc}"
            all_mutants.add(namespaced)
            for test_name in killing_tests:
                test_kills[test_name].add(namespaced)

    if not all_mutants:
        return json.dumps(
            {
                "status": "no_killable_mutants",
                "profiled_functions": function_count,
                "message": "No killable mutants found in profiled data.",
            },
            indent=2,
        )

    # Build per-test category coverage map
    test_categories: dict[str, set[str]] = defaultdict(set)
    for test_name, kills in test_kills.items():
        for mutant_id in kills:
            # mutant_id format: "func_key::CATEGORY:location"
            cat_part = mutant_id.split("::")[-1] if "::" in mutant_id else mutant_id
            category = cat_part.split(":")[0] if ":" in cat_part else "UNKNOWN"
            test_categories[test_name].add(category)

    # Compute unique kills per test
    unique_kills = _compute_unique_kills(test_kills)

    # Identify zero-unique-kill tests (THYGIENE004 candidates)
    zero_unique = [
        {
            "test": t,
            "total_kills": len(test_kills[t]),
            "categories_covered": sorted(test_categories.get(t, set())),
        }
        for t, u in unique_kills.items()
        if u == 0
    ]
    zero_unique.sort(key=lambda x: str(x["test"]))

    # Compute minimal covering set (greedy set cover)
    covering_set = _greedy_covering_set(test_kills, all_mutants)

    # Tests not in covering set are redundant
    redundant_tests = sorted(set(test_kills.keys()) - set(covering_set))

    output: dict[str, Any] = {
        "status": "analyzed",
        "profiled_functions": function_count,
        "total_mutants": len(all_mutants),
        "total_tests_in_matrix": len(test_kills),
        "zero_unique_kill_tests": zero_unique[:top_n],
        "zero_unique_kill_count": len(zero_unique),
        "minimal_covering_set_size": len(covering_set),
        "redundant_test_count": len(redundant_tests),
        "redundant_tests": redundant_tests[:top_n],
        "covering_set": covering_set[:50],  # Cap for readability
    }

    output["next_actions"] = _build_next_actions(output, project_root)
    return json.dumps(output, indent=2)


def _compute_unique_kills(test_kills: dict[str, set[str]]) -> dict[str, int]:
    """Compute number of unique kills per test (mutants killed by no other test)."""
    # For each mutant, count how many tests kill it
    mutant_killers: dict[str, int] = defaultdict(int)
    for kills in test_kills.values():
        for m in kills:
            mutant_killers[m] += 1

    # A test has a unique kill if it kills a mutant that only 1 test kills
    unique_counts: dict[str, int] = {}
    for test_name, kills in test_kills.items():
        unique = sum(1 for m in kills if mutant_killers[m] == 1)
        unique_counts[test_name] = unique

    return unique_counts


def _greedy_covering_set(test_kills: dict[str, set[str]], all_mutants: set[str]) -> list[str]:
    """Greedy set cover: select tests that kill the most uncovered mutants."""
    uncovered = set(all_mutants)
    covering: list[str] = []
    remaining = dict(test_kills)

    while uncovered and remaining:
        # Pick the test that covers the most uncovered mutants
        def _key_fn(t: str, _r: dict[str, set[str]] = remaining, _u: set[str] = uncovered) -> int:  # noqa: B006
            return len(_r[t] & _u)

        best_test = max(remaining, key=_key_fn)
        covered = remaining[best_test] & uncovered
        if not covered:
            break
        covering.append(best_test)
        uncovered -= covered
        del remaining[best_test]

    return covering


def _build_next_actions(output: dict[str, Any], project_root: str) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []

    if output["zero_unique_kill_count"] > 0:
        actions.append(
            {
                "tool": "test_hygiene_scan",
                "args": {"path": project_root},
                "reason": f"{output['zero_unique_kill_count']} tests have zero unique kills — cross-reference with hygiene findings",
                "priority": 1,
            }
        )

    if output["redundant_test_count"] > 0:
        actions.append(
            {
                "tool": "controlplane_apply_repairs",
                "args": {"path": project_root},
                "reason": f"{output['redundant_test_count']} redundant tests identified for review",
                "priority": 2,
            }
        )

    return actions
