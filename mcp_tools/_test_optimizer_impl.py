"""Test optimizer MCP implementation — triage and compact."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time

from ._disk_helpers import tool_response


def impl_test_triage(path: str, file: str) -> str:
    """Run test triage: extract minimum killing set from mutation data."""
    from lintgate.testing.test_optimizer import run_triage

    project_root = os.path.abspath(path)
    triage = run_triage(project_root, file)
    if triage is None:
        return tool_response(
            {"status": "no_data", "note": "No mutation analysis found. Run improve_tests first."},
            "test_triage",
            project_root,
            summary=f"No mutation data for {file}. Run improve_tests(path, file) first.",
            next_actions=[{"tool": "mutation_run_full", "args": {"path": path, "file": file},
                           "reason": "Need mutation convergence data", "priority": "required"}],
        )

    data = {
        "source_file": triage.source_file,
        "analysis_id": triage.analysis_id,
        "total_tests_mapped": triage.total_tests_mapped,
        "killing_set_size": len(triage.killing_set),
        "redundant_count": triage.total_tests_mapped - len(triage.killing_set),
        "kill_rate": triage.kill_rate,
        "killing_set": sorted(triage.killing_set),
        "functions": [
            {
                "function_key": f.function_key,
                "sigma": f.sigma,
                "total_mutants": f.total_mutants,
                "killed": f.killed,
                "killing_tests": f.killing_tests,
            }
            for f in triage.functions
        ],
    }

    redundant = triage.total_tests_mapped - len(triage.killing_set)
    summary = (
        f"{file}: {triage.total_tests_mapped} tests, "
        f"{len(triage.killing_set)} needed, {redundant} redundant "
        f"({triage.kill_rate:.0%} kill rate)"
    )

    return tool_response(
        data, "test_triage", project_root, summary=summary,
        next_actions=[{"tool": "test_compact", "args": {"path": path, "file": file},
                       "reason": "Compact test file to minimum killing set", "priority": "suggested"}],
    )


def impl_test_compact(path: str, file: str, dry_run: bool = True) -> str:
    """Compact test file to minimum killing set."""
    from lintgate.testing.test_optimizer import run_compact

    project_root = os.path.abspath(path)
    result = run_compact(project_root, file)
    if result is None:
        return tool_response(
            {"status": "no_data", "note": "Cannot compact. Run improve_tests first."},
            "test_compact",
            project_root,
            summary=f"No compaction possible for {file}. Run improve_tests first.",
            next_actions=[
                {
                    "tool": "improve_tests",
                    "args": {"path": path, "file": file},
                    "reason": "Mutation profile required before compaction",
                    "priority": "required",
                },
            ],
        )

    data = {
        "source_file": result.source_file,
        "test_file": result.test_file,
        "original_test_count": result.original_test_count,
        "original_lines": result.original_lines,
        "compacted_test_count": result.compacted_test_count,
        "compacted_lines": result.compacted_lines,
        "reduction_pct": round((1 - result.compacted_lines / max(result.original_lines, 1)) * 100),
        "dry_run": dry_run,
    }

    if dry_run:
        data["preview_content"] = result.content
        summary = (
            f"[DRY RUN] {os.path.basename(result.test_file)}: "
            f"{result.original_test_count}→{result.compacted_test_count} tests, "
            f"{result.original_lines}→{result.compacted_lines} lines "
            f"({data['reduction_pct']}% reduction)"
        )
    else:
        # Save backup before overwriting
        backup_dir = os.path.join(project_root, ".lintgate", "backups", "compact")
        os.makedirs(backup_dir, exist_ok=True)
        backup_name = f"{os.path.basename(result.test_file)}_{int(time.time())}.py"
        backup_path = os.path.join(backup_dir, backup_name)
        original = result.original_content
        if not original and os.path.isfile(result.test_file):
            with open(result.test_file, encoding="utf-8") as f:
                original = f.read()
        if original:
            with open(backup_path, "w", encoding="utf-8") as f:
                f.write(original)
            data["backup_path"] = backup_path

        # Write the compacted file
        with open(result.test_file, "w", encoding="utf-8") as f:
            f.write(result.content)

        # Post-write validation: pytest --collect-only
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "pytest", "--collect-only", "-q", result.test_file],
                capture_output=True,
                text=True,
                timeout=30,
                cwd=project_root,
            )
            if proc.returncode != 0:
                # Collection failed — restore from backup
                if original:
                    with open(result.test_file, "w", encoding="utf-8") as f:
                        f.write(original)
                error_detail = (proc.stderr or proc.stdout or "unknown error")[:500]
                data["validation"] = "COLLECTION_FAILED"
                data["collection_error"] = error_detail
                summary = (
                    f"REVERTED: {os.path.basename(result.test_file)}: "
                    f"compacted file failed pytest --collect-only. Original restored."
                )
                return tool_response(data, "test_compact", project_root, summary=summary)

            # Parse collected test count
            collected = 0
            match = re.search(r"(\d+) tests? collected", proc.stdout or "")
            if match:
                collected = int(match.group(1))
            data["validation"] = "PASSED"
            data["tests_collected"] = collected
        except (subprocess.TimeoutExpired, OSError):
            # Validation inconclusive — keep the write but warn
            data["validation"] = "SKIPPED"
            data["validation_note"] = "pytest --collect-only timed out or failed to run"

        summary = (
            f"APPLIED: {os.path.basename(result.test_file)}: "
            f"{result.original_test_count}→{result.compacted_test_count} tests, "
            f"{result.original_lines}→{result.compacted_lines} lines "
            f"({data['reduction_pct']}% reduction)"
        )
        if data.get("validation") == "PASSED":
            summary += f" [{data['tests_collected']} collected]"

    return tool_response(data, "test_compact", project_root, summary=summary)
