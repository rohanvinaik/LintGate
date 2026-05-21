"""Generation helpers for the platonic workflow."""

from __future__ import annotations

import ast
import hashlib
import os
from typing import Any


def _has_nontrivial_tests(file_path: str) -> bool:
    """Check whether a file contains runnable tests with nontrivial bodies.

    Returns True if at least one test function contains a real assertion
    (not just ``assert True``), a ``pytest.raises`` call, or a helper
    assertion call (function starting with ``assert_``).
    """
    if not os.path.isfile(file_path):
        return False
    try:
        with open(file_path, encoding="utf-8") as f:
            tree = ast.parse(f.read())
    except (OSError, SyntaxError):
        return False

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not node.name.startswith("test_"):
            continue
        for child in ast.walk(node):
            # Real assert (excluding `assert True`)
            if isinstance(child, ast.Assert):
                test_val = child.test
                if isinstance(test_val, ast.Constant) and test_val.value is True:
                    continue
                return True
            # pytest.raises or helper assert_ calls
            if isinstance(child, ast.Call):
                func = child.func
                if isinstance(func, ast.Attribute) and func.attr == "raises":
                    return True
                if isinstance(func, ast.Name) and (
                    func.id == "raises" or func.id.startswith("assert_")
                ):
                    return True
    return False


def generate_tests(
    project_root: str,
    file_path: str,
    auto_targets: list[Any],
    *,
    staging_dir: str | None = None,
) -> dict[str, Any]:
    """Generate tests using the batch regenerator.

    When *staging_dir* is provided, writes to the staging directory instead
    of the canonical target path.  Before writing, checks whether the
    canonical target already contains nontrivial tests — if so, skips
    generation to avoid destructive overwrite.
    """
    try:
        from lintgate.testing.batch_regenerator import BatchRegenerator

        regenerator = BatchRegenerator(project_root)
        func_dicts = [
            {
                "function_key": target.evidence.function_key,
                "target_test_file": target.target_test_file,
            }
            for target in auto_targets
        ]

        result = regenerator.generate_for_file(file_path, func_dicts)
        if result is None:
            return {"files_written": 0}

        canonical_path = os.path.join(project_root, result.target_test_file)

        # Existing-file policy: skip if canonical target has real tests
        if staging_dir is not None and _has_nontrivial_tests(canonical_path):
            return {
                "files_written": 0,
                "skipped_reason": "existing_tests_adequate",
                "generated_path": result.target_test_file,
                "canonical_path": canonical_path,
            }

        # Determine write destination
        if staging_dir is not None:
            target_path = os.path.join(staging_dir, os.path.basename(canonical_path))
        else:
            target_path = canonical_path

        os.makedirs(os.path.dirname(target_path), exist_ok=True)
        with open(target_path, "w", encoding="utf-8") as handle:
            handle.write(result.content)

        out: dict[str, Any] = {
            "files_written": 1,
            "target": result.target_test_file,
            "functions_covered": result.functions_covered,
            "sources": result.enrichment_sources,
            "manual_contract_candidates": result.manual_contract_candidates,
            "generated_path": result.target_test_file,
            "oracle_requests": [
                r.to_dict() for r in getattr(result, "oracle_requests", [])
            ],
        }

        if staging_dir is not None:
            content_hash = hashlib.sha256(result.content.encode()).hexdigest()
            out["staging_path"] = target_path
            out["content_hash"] = content_hash

        return out
    except (ImportError, AttributeError) as exc:
        return {"files_written": 0, "error": f"generation_failed: {exc}"}
    except OSError as exc:
        return {"files_written": 0, "error": f"write_failed: {exc}"}
