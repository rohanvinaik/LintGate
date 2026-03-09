"""Implementation for project_organize_audit and project_organize_apply.

Extracted from gh_tools.py to keep the register() module under 400 lines.
High-CC functions are decomposed into per-check helpers.
"""

from __future__ import annotations

import contextlib
import json
import re
from pathlib import Path
from typing import Any

from lintgate.next_action import NextAction, serialize_next_actions
from mcp_tools._gh_helpers import _repo_full_name, _run_gh

# ── Result-parsing helpers ────────────────────────────────────────────────


def _parse_label_names(labels_result: Any) -> list[str]:
    """Extract label name strings from a ``gh label list`` result."""
    if isinstance(labels_result, list):
        return [lb.get("name", "") for lb in labels_result]
    if isinstance(labels_result, dict) and not labels_result.get("error"):
        raw = labels_result.get("raw", "")
        if raw:
            with contextlib.suppress(json.JSONDecodeError):
                parsed = json.loads(raw)
                if isinstance(parsed, list):
                    return [lb.get("name", "") for lb in parsed]
    return []


def _parse_issues_list(issues_result: Any) -> list[dict]:
    """Extract issue dicts from a ``gh issue list`` result."""
    if isinstance(issues_result, list):
        return issues_result
    if isinstance(issues_result, dict):
        raw = issues_result.get("raw", "")
        if raw:
            with contextlib.suppress(json.JSONDecodeError):
                parsed = json.loads(raw)
                if isinstance(parsed, list):
                    return parsed
    return []


# ── Per-check helpers for organize_audit ──────────────────────────────────


def _check_issue_templates(path: str) -> list[dict[str, Any]]:
    """ORG001: Check for .github/ISSUE_TEMPLATE/ directory."""
    template_dir = Path(path) / ".github" / "ISSUE_TEMPLATE"
    if not template_dir.is_dir():
        return [
            {
                "code": "ORG001",
                "message": "No .github/ISSUE_TEMPLATE/ directory",
                "severity": "medium",
            }
        ]
    return []


def _check_priority_labels(label_names: list[str]) -> list[dict[str, Any]]:
    """ORG002: Check for P0-P3 priority labels."""
    priority_labels = [n for n in label_names if re.match(r"^P[0-3]$", n)]
    if len(priority_labels) < 4:
        missing = [f"P{i}" for i in range(4) if f"P{i}" not in label_names]
        return [
            {
                "code": "ORG002",
                "message": f"Missing priority labels: {', '.join(missing)}",
                "severity": "low",
            }
        ]
    return []


def _check_type_labels(label_names: list[str]) -> list[dict[str, Any]]:
    """ORG003: Check for type: prefix labels."""
    type_labels = [n for n in label_names if n.startswith("type:")]
    if not type_labels:
        return [
            {
                "code": "ORG003",
                "message": "No type: prefix labels defined",
                "severity": "low",
            }
        ]
    return []


def _check_unlabeled_issues(issues_list: list[dict]) -> list[dict[str, Any]]:
    """ORG004: Issues with no labels."""
    unlabeled = [iss["number"] for iss in issues_list if not iss.get("labels")]
    if unlabeled:
        return [
            {
                "code": "ORG004",
                "message": f"{len(unlabeled)} issues have no labels",
                "severity": "medium",
                "issues": unlabeled[:20],
            }
        ]
    return []


def _check_milestones(
    repo_full: str,
    issue_count: int,
) -> list[dict[str, Any]]:
    """ORG005: No milestones when >10 open issues."""
    if issue_count <= 10:
        return []
    milestones_result = _run_gh(
        ["api", f"repos/{repo_full}/milestones", "--jq", "length"],
    )
    ms_count = 0
    if isinstance(milestones_result, dict):
        raw = milestones_result.get("raw", "0")
        with contextlib.suppress(ValueError, TypeError):
            ms_count = int(raw)
    if ms_count == 0:
        return [
            {
                "code": "ORG005",
                "message": f"No milestones defined (repo has {issue_count}+ open issues)",
                "severity": "low",
            }
        ]
    return []


def _check_wiki_enabled(repo_full: str) -> list[dict[str, Any]]:
    """ORG006: Wiki disabled."""
    repo_info = _run_gh(
        ["api", f"repos/{repo_full}", "--jq", ".has_wiki"],
    )
    if isinstance(repo_info, dict):
        raw = repo_info.get("raw", "")
        if raw.strip().lower() == "false":
            return [
                {
                    "code": "ORG006",
                    "message": "Wiki is disabled on this repository",
                    "severity": "low",
                }
            ]
    return []


def _check_issues_missing_priority(
    issues_list: list[dict],
    label_names: list[str],
) -> list[dict[str, Any]]:
    """ORG007: Issues missing priority labels."""
    priority_labels = [n for n in label_names if re.match(r"^P[0-3]$", n)]
    if not priority_labels or not issues_list:
        return []
    no_priority = [
        iss["number"]
        for iss in issues_list
        if not any(
            lb.get("name", "").startswith("P") and len(lb.get("name", "")) == 2
            for lb in (iss.get("labels") or [])
        )
    ]
    if no_priority:
        return [
            {
                "code": "ORG007",
                "message": f"{len(no_priority)} issues missing priority labels",
                "severity": "low",
                "issues": no_priority[:20],
            }
        ]
    return []


# ── Fetching helpers ──────────────────────────────────────────────────────


def _fetch_labels(repo_full: str) -> list[str]:
    """Fetch label names from GitHub for *repo_full*."""
    labels_result = _run_gh(
        ["label", "list", "--repo", repo_full, "--json", "name", "--limit", "200"],
    )
    return _parse_label_names(labels_result)


def _fetch_issues(repo_full: str) -> list[dict]:
    """Fetch open issues (number + labels) from GitHub."""
    issues_result = _run_gh(
        [
            "issue",
            "list",
            "--repo",
            repo_full,
            "--json",
            "number,labels",
            "--state",
            "open",
            "--limit",
            "500",
        ],
    )
    return _parse_issues_list(issues_result)


# ── Label creation helpers ────────────────────────────────────────────────


def _plan_missing_labels(
    existing: list[str],
    repo_full: str,
    write: bool,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return (planned, applied) for priority + type labels."""
    planned: list[dict[str, Any]] = []
    applied: list[dict[str, Any]] = []

    priority_colors = {"P0": "B60205", "P1": "D93F0B", "P2": "FBCA04", "P3": "0E8A16"}
    type_labels = {
        "type:research": "1D76DB",
        "type:architecture": "5319E7",
        "type:bug": "B60205",
        "type:refactor": "FBCA04",
    }

    all_labels = {**priority_colors, **type_labels}
    for label, color in all_labels.items():
        if label in existing:
            continue
        desc_parts = ["--description", f"Priority {label}"] if label.startswith("P") else []
        action: dict[str, Any] = {"type": "create_label", "name": label, "color": color}
        planned.append(action)
        if write:
            result = _run_gh(
                ["label", "create", label, "--repo", repo_full, "--color", color, *desc_parts],
            )
            action["result"] = result
            applied.append(action)

    return planned, applied


# ── Top-level implementations ─────────────────────────────────────────────


def impl_project_organize_audit(
    path: str,
    helpers: Any,
) -> dict[str, Any]:
    """Scan GitHub project state and report organizational gaps."""
    helpers["_validate_project_root"](path)
    repo_full = _repo_full_name(path)

    gaps: list[dict[str, Any]] = _check_issue_templates(path)

    if not repo_full:
        return {
            "project": path,
            "repo": None,
            "gaps": gaps,
            "error": "Could not detect GitHub remote. Skipping API-based checks.",
            "next_actions": serialize_next_actions(
                [
                    NextAction(
                        tool="project_organize_apply",
                        args={"path": path, "write": False},
                        reason="Preview organization changes.",
                        priority=3,
                    ),
                ]
            ),
        }

    label_names = _fetch_labels(repo_full)
    issues_list = _fetch_issues(repo_full)

    gaps.extend(_check_priority_labels(label_names))
    gaps.extend(_check_type_labels(label_names))
    gaps.extend(_check_unlabeled_issues(issues_list))
    gaps.extend(_check_milestones(repo_full, len(issues_list)))
    gaps.extend(_check_wiki_enabled(repo_full))
    gaps.extend(_check_issues_missing_priority(issues_list, label_names))

    return {
        "project": path,
        "repo": repo_full,
        "total_gaps": len(gaps),
        "gaps": gaps,
        "labels_found": len(label_names),
        "open_issues": len(issues_list),
        "next_actions": serialize_next_actions(
            [
                NextAction(
                    tool="project_organize_apply",
                    args={"path": path, "write": False},
                    reason="Preview fixes for detected gaps.",
                    priority=2,
                    condition="gaps detected",
                ),
                NextAction(
                    tool="project_wiki_sync",
                    args={"path": path, "scope": "all", "write": False},
                    reason="Preview wiki page generation.",
                    priority=4,
                    condition="wiki infrastructure desired",
                ),
            ]
        ),
    }


def impl_project_organize_apply(
    path: str,
    actions: list[str] | None,
    write: bool,
    helpers: Any,
) -> dict[str, Any]:
    """Apply organization changes. Dry-run by default."""
    helpers["_validate_project_root"](path)
    repo_full = _repo_full_name(path)
    if not repo_full:
        return {"error": "Could not detect GitHub remote."}

    all_actions = actions or ["labels", "milestones"]
    planned: list[dict[str, Any]] = []
    applied: list[dict[str, Any]] = []

    if "labels" in all_actions:
        existing = _fetch_labels(repo_full)
        label_planned, label_applied = _plan_missing_labels(existing, repo_full, write)
        planned.extend(label_planned)
        applied.extend(label_applied)

    return {
        "project": path,
        "repo": repo_full,
        "write": write,
        "planned_actions": len(planned),
        "applied_actions": len(applied),
        "actions": planned if not write else applied,
        "next_actions": serialize_next_actions(
            [
                NextAction(
                    tool="project_organize_apply",
                    args={"path": path, "actions": all_actions, "write": True},
                    reason="Apply planned changes.",
                    priority=2,
                    condition="dry-run looks good and write=False was used",
                ),
                NextAction(
                    tool="project_organize_audit",
                    args={"path": path},
                    reason="Re-audit after applying changes.",
                    priority=3,
                    condition="after applying changes",
                ),
            ]
        ),
    }
