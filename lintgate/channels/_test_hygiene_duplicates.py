"""Duplicate and subsumption detection for test hygiene (THYGIENE003, THYGIENE005).

Extracted from test_hygiene_channel.py to keep the main module under 400 lines.
"""

from __future__ import annotations

import hashlib
import os
from collections import defaultdict
from typing import Any

from lintgate.controlplane.types import RepairAction
from lintgate.types import LintIssue

from ._test_hygiene_ast import (
    _extract_test_functions,
    _function_body_ast_hash,
    _function_body_source,
    _function_context_hash,
    _parse_file,
    _read_source,
)

_TOP_N_FINDINGS = 5


def _build_test_fingerprints(
    test_files: list[str],
) -> list[dict[str, Any]]:
    """Build fingerprints for all test functions across all files.

    Returns list of dicts with keys:
        file, name, class_name, line, body_hash, ast_hash
    """
    fingerprints: list[dict[str, Any]] = []

    for filepath in test_files:
        source = _read_source(filepath)
        if source is None:
            continue
        tree = _parse_file(filepath)
        if tree is None:
            continue

        for name, node, class_name in _extract_test_functions(tree):
            body_src = _function_body_source(source, node)
            body_hash = hashlib.sha256(body_src.encode()).hexdigest()[:16]
            ast_hash = _function_body_ast_hash(node)
            ctx_hash = _function_context_hash(node)

            fingerprints.append(
                {
                    "file": filepath,
                    "name": name,
                    "class_name": class_name,
                    "line": node.lineno,
                    "body_hash": body_hash,
                    "ast_hash": ast_hash,
                    "ctx_hash": ctx_hash,
                    "body_source": body_src,
                }
            )

    return fingerprints


def _find_cross_file_duplicates(
    fingerprints: list[dict],
    hash_field: str,
    project_root: str,
    seen_dupes: set[str],
    *,
    duplicate_type: str,
    severity: str,
    confidence: float,
    message_verb: str,
) -> list[LintIssue]:
    """Find cross-file duplicate test functions by a given hash field.

    Groups fingerprints by name + hash_field + ctx_hash, then emits findings
    for duplicates across different files. Mutates seen_dupes to deduplicate
    across calls.
    """
    grouped: dict[str, list[dict]] = defaultdict(list)
    for fp in fingerprints:
        key = f"{fp['name']}:{fp[hash_field]}:{fp['ctx_hash']}"
        grouped[key].append(fp)

    findings: list[LintIssue] = []
    for group in grouped.values():
        if len(group) < 2:
            continue
        files = {fp["file"] for fp in group}
        if len(files) < 2:
            continue

        sorted_group = sorted(group, key=lambda fp: fp["file"])
        keeper = sorted_group[0]
        keeper_rel = os.path.relpath(keeper["file"], project_root)

        for dup in sorted_group[1:]:
            dup_key = f"{dup['file']}:{dup['name']}"
            if dup_key in seen_dupes:
                continue
            seen_dupes.add(dup_key)

            dup_rel = os.path.relpath(dup["file"], project_root)
            display = f"{dup['class_name']}.{dup['name']}" if dup["class_name"] else dup["name"]
            findings.append(
                LintIssue(
                    linter="test_hygiene",
                    kind="THYGIENE003",
                    message=(
                        f"'{display}' in {dup_rel} is {message_verb} to "
                        f"{keeper_rel}:{keeper['name']}. "
                        f"{'Safe to remove.' if severity == 'warning' else 'Review before removing.'}"
                    ),
                    file=dup["file"],
                    line=dup["line"],
                    severity=severity,
                    confidence=confidence,
                    evidence={
                        "code": "THYGIENE003",
                        "function": dup["name"],
                        "duplicate_type": duplicate_type,
                        "keeper_file": keeper_rel,
                        "keeper_function": keeper["name"],
                        hash_field: dup[hash_field],
                    },
                )
            )
    return findings


def _thygiene003_duplicates(
    test_files: list[str],
    project_root: str,
) -> tuple[list[LintIssue], list[RepairAction]]:
    """THYGIENE003 -- Duplicate test functions.

    Returns (findings, repair_actions).
    """
    fingerprints = _build_test_fingerprints(test_files)
    repairs: list[RepairAction] = []
    seen_dupes: set[str] = set()

    # Pass 1: byte-identical duplicates (high confidence)
    byte_findings = _find_cross_file_duplicates(
        fingerprints,
        "body_hash",
        project_root,
        seen_dupes,
        duplicate_type="byte_identical",
        severity="warning",
        confidence=0.95,
        message_verb="byte-identical",
    )

    # Pass 2: AST-equivalent duplicates (lower confidence, skips already-seen)
    ast_findings = _find_cross_file_duplicates(
        fingerprints,
        "ast_hash",
        project_root,
        seen_dupes,
        duplicate_type="ast_equivalent",
        severity="informational",
        confidence=0.75,
        message_verb="AST-equivalent",
    )

    findings = byte_findings + ast_findings

    # Check for fully subsumed files (THYGIENE005)
    _add_subsumption_findings(fingerprints, test_files, project_root, findings, repairs)

    return findings[: _TOP_N_FINDINGS * 3], repairs


def _add_subsumption_findings(
    fingerprints: list[dict],
    test_files: list[str],
    project_root: str,
    findings: list[LintIssue],
    repairs: list[RepairAction],
) -> None:
    """Detect files that are fully subsumed by another file."""
    # Build per-file fingerprint sets (name + body + context)
    file_hashes: dict[str, set[str]] = defaultdict(set)
    file_test_count: dict[str, int] = defaultdict(int)
    for fp in fingerprints:
        file_hashes[fp["file"]].add(f"{fp['name']}:{fp['body_hash']}:{fp['ctx_hash']}")
        file_test_count[fp["file"]] += 1

    for filepath in test_files:
        if filepath not in file_hashes or file_test_count[filepath] == 0:
            continue
        my_hashes = file_hashes[filepath]

        for other_file in test_files:
            if other_file == filepath:
                continue
            if other_file not in file_hashes:
                continue
            if file_test_count[other_file] <= file_test_count[filepath]:
                continue  # Only check if other has MORE tests
            other_hashes = file_hashes[other_file]
            if my_hashes <= other_hashes:
                # All tests in filepath exist in other_file
                rel_path = os.path.relpath(filepath, project_root)
                other_rel = os.path.relpath(other_file, project_root)
                findings.append(
                    LintIssue(
                        linter="test_hygiene",
                        kind="THYGIENE005",
                        message=(
                            f"All {file_test_count[filepath]} tests in {rel_path} "
                            f"are byte-identical duplicates of tests in {other_rel}. "
                            f"Safe to delete entire file."
                        ),
                        file=filepath,
                        severity="warning",
                        confidence=0.95,
                        evidence={
                            "code": "THYGIENE005",
                            "subsumed_file": rel_path,
                            "superset_file": other_rel,
                            "test_count": file_test_count[filepath],
                        },
                    )
                )
                repairs.append(
                    RepairAction(
                        channel="test_hygiene",
                        kind="safe_delete",
                        summary=f"Delete {rel_path} (fully subsumed by {other_rel})",
                        payload={
                            "action": "delete_file",
                            "target_path": filepath,
                            "reason": f"All {file_test_count[filepath]} tests are byte-identical duplicates of {other_rel}",
                        },
                        safe=True,
                    )
                )
                break  # Only need one superset
