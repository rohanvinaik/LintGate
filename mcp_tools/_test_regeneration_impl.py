"""Implementation for test regeneration MCP tools.

Extracted from test_regeneration_tools.py to stay under the
400-line file limit and reduce register() statement count.
"""

from __future__ import annotations

import os
from typing import Any

from lintgate.next_action import NextAction, serialize_next_actions


def _load_regen_config(project_root: str) -> Any:
    """Load TestRegenerationConfig from project config, with safe defaults."""
    try:
        from lintgate.config import load_controlplane_config

        cp = load_controlplane_config(project_root)
        if cp is not None:
            return cp.test_regeneration
    except Exception:
        pass
    from lintgate.controlplane.types import TestRegenerationConfig

    return TestRegenerationConfig()


def impl_rebuild_plan(
    helpers: dict[str, Any],
    path: str,
    file: str | None = None,
    write_manifest: bool = True,
    preserve_globs: list[str] | None = None,
) -> str:
    """Build a test rebuild manifest by classifying every function."""
    from lintgate.specification.file_analyzer import analyze_file
    from lintgate.specification.test_regeneration_strategy import (
        build_evidence,
        build_manifest,
        classify_function,
        write_manifest as write_manifest_fn,
    )

    from ._mutation_impl import get_cache_dir, iter_cached_states
    from ._specification_helpers import resolve_py_files

    project_root = helpers["_validate_project_root"](path)

    # Apply config defaults for preserve_globs
    if preserve_globs is None:
        cfg = _load_regen_config(project_root)
        if cfg.preserve_globs:
            preserve_globs = cfg.preserve_globs

    # Resolve source files
    if file:
        from ._specification_helpers import validate_file_in_project

        full = validate_file_in_project(project_root, file)
        source_files = [full]
    else:
        resolved = resolve_py_files(project_root, None)
        if isinstance(resolved, dict):
            return str(helpers["_json_dumps"](resolved, output_mode="compact"))
        source_files = resolved

    # Load mutation cache
    cache_dir = get_cache_dir(project_root)
    mutation_states = iter_cached_states(cache_dir)
    mutation_by_key: dict[str, dict] = {}
    for state in mutation_states:
        key = state.get("function_key", "")
        if key:
            mutation_by_key[key] = state

    # Classify each function
    classifications = []
    for source_file in source_files:
        rel = os.path.relpath(source_file, project_root)
        try:
            ledger = analyze_file(source_file, project_root, enrich=False)
        except Exception:
            continue

        for func_key, spec in ledger.functions.items():
            spec_data = spec.to_dict()
            mutation_data = mutation_by_key.get(func_key)
            evidence = build_evidence(func_key, rel, spec_data, mutation_data)
            result = classify_function(evidence)
            classifications.append(result)

    manifest = build_manifest(project_root, classifications, preserve_globs)

    manifest_path = ""
    if write_manifest:
        manifest_path = write_manifest_fn(manifest, project_root)

    output = manifest.summary()
    output["manifest_path"] = manifest_path
    output["next_actions"] = serialize_next_actions(
        [
            NextAction(
                tool="test_rebuild_generate",
                args={"path": path},
                reason="Generate tests for auto_generate_unit targets",
            ),
        ]
    )
    return str(helpers["_json_dumps"](output, output_mode="compact"))


def impl_rebuild_generate(
    helpers: dict[str, Any],
    path: str,
    write: bool = False,
    max_files: int = 50,
) -> str:
    """Generate tests for auto_generate_unit targets in the manifest."""
    from lintgate.specification.test_regeneration_strategy import (
        Strategy,
        load_manifest as load_manifest_fn,
    )

    project_root = helpers["_validate_project_root"](path)

    plan = load_manifest_fn(project_root)
    if plan is None:
        return str(
            helpers["_json_dumps"](
                {"error": "No manifest found. Run test_rebuild_plan first."},
                output_mode="compact",
            )
        )

    # Group auto_generate_unit targets by source file
    by_file: dict[str, list[dict]] = {}
    for func in plan.functions:
        if func.strategy != Strategy.AUTO_GENERATE_UNIT:
            continue
        sf = func.evidence.source_file
        by_file.setdefault(sf, []).append(func.to_dict())

    generated: list[dict] = []
    skeletons: dict[str, str] = {}
    files_processed = 0

    for source_file, funcs in sorted(by_file.items()):
        if files_processed >= max_files:
            break
        files_processed += 1

        full_path = os.path.join(project_root, source_file)
        if not os.path.isfile(full_path):
            continue

        skeleton = _generate_skeleton(full_path, project_root)
        if not skeleton:
            continue

        target_file = funcs[0].get("target_test_file", "")
        if not target_file:
            continue

        skeletons[target_file] = skeleton

        if write:
            out_path = os.path.join(project_root, target_file)
            os.makedirs(os.path.dirname(out_path), exist_ok=True)
            with open(out_path, "w", encoding="utf-8") as f:
                f.write(skeleton)

        generated.append({
            "source_file": source_file,
            "target_test_file": target_file,
            "functions": len(funcs),
            "written": write,
        })

    output: dict[str, Any] = {
        "files_generated": len(generated),
        "files_processed": files_processed,
        "total_auto_generate_targets": sum(len(v) for v in by_file.values()),
        "generated": generated,
    }

    if not write:
        output["skeletons"] = skeletons

    output["next_actions"] = serialize_next_actions(
        [
            NextAction(
                tool="test_rebuild_validate",
                args={"path": path},
                reason="Validate generated tests against quality gates",
            ),
        ]
    )
    return str(helpers["_json_dumps"](output, output_mode="compact"))


def _generate_skeleton(full_path: str, project_root: str) -> str:
    """Generate a test skeleton for a source file."""
    try:
        from lintgate.controlplane.skeleton_generator import generate_test_skeleton

        return generate_test_skeleton(
            full_path, archetypes=None, project_root=project_root
        )
    except Exception:
        return ""
