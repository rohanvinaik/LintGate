#!/usr/bin/env python3
"""Automated local->main ship pipeline with required-check parity.

This is the central pipeline driver:
1. Run strict local gates (.githooks/pre-push)
2. Push current branch
3. Create/update PR to main
4. Watch required checks (branch protection + gate contract)
5. Merge when all required checks are green

It resolves split-brain by making the merge decision from the same
required check set that branch protection enforces.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any

SUCCESS_CONCLUSIONS = {"success", "neutral", "skipped"}


def _run(
    cmd: list[str],
    *,
    cwd: str,
    check: bool = True,
    capture: bool = False,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=cwd,
        check=check,
        text=True,
        capture_output=capture,
    )


def _require_tool(name: str, repo_root: str) -> None:
    if shutil.which(name):
        return
    raise RuntimeError(f"Missing required tool: {name}")


def _git_output(repo_root: str, *args: str) -> str:
    result = _run(["git", *args], cwd=repo_root, capture=True)
    return result.stdout.strip()


def _gh_json(repo_root: str, *args: str) -> Any:
    result = _run(["gh", *args], cwd=repo_root, capture=True)
    return json.loads(result.stdout)


def _require_clean_worktree(repo_root: str) -> None:
    tracked = _git_output(repo_root, "status", "--porcelain", "--untracked-files=no")
    if tracked:
        raise RuntimeError(
            "Tracked changes detected. Commit or stash tracked files before ship_main."
        )


def _ensure_branch(repo_root: str, base_branch: str) -> str:
    branch = _git_output(repo_root, "branch", "--show-current")
    if not branch:
        raise RuntimeError("Detached HEAD is not supported for ship_main.")
    if branch != base_branch:
        return branch

    ts = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    branch = f"codex/ship-{ts}"
    _run(["git", "switch", "-c", branch], cwd=repo_root)
    print(f"[ship] Created ephemeral branch from {base_branch}: {branch}")
    return branch


def _run_local_gate_stack(repo_root: str) -> None:
    hook_path = Path(repo_root) / ".githooks" / "pre-push"
    if not hook_path.exists():
        raise RuntimeError("Missing .githooks/pre-push")
    if not os.access(hook_path, os.X_OK):
        raise RuntimeError(".githooks/pre-push is not executable")

    print("[ship] Running strict local gate stack (.githooks/pre-push)")
    _run([str(hook_path)], cwd=repo_root)


def _push_branch(repo_root: str, remote: str, branch: str) -> None:
    print(f"[ship] Pushing {branch} -> {remote}/{branch}")
    _run(["git", "push", "-u", remote, branch], cwd=repo_root)


def _resolve_pr(repo_root: str, branch: str, base_branch: str) -> tuple[int, str]:
    prs = _gh_json(
        repo_root,
        "pr",
        "list",
        "--head",
        branch,
        "--base",
        base_branch,
        "--state",
        "open",
        "--json",
        "number,url",
    )
    if prs:
        pr = prs[0]
        print(f"[ship] Reusing open PR #{pr['number']}: {pr['url']}")
        return int(pr["number"]), str(pr["url"])

    title = f"chore: ship {branch} to {base_branch}"
    body = (
        "Automated ship via scripts/ship_main.py\n\n"
        "- Runs local strict gate stack\n"
        "- Watches required checks until all pass\n"
        "- Merges with branch cleanup"
    )
    _run(
        [
            "gh",
            "pr",
            "create",
            "--base",
            base_branch,
            "--head",
            branch,
            "--title",
            title,
            "--body",
            body,
        ],
        cwd=repo_root,
    )

    pr = _gh_json(repo_root, "pr", "view", "--json", "number,url")
    print(f"[ship] Created PR #{pr['number']}: {pr['url']}")
    return int(pr["number"]), str(pr["url"])


def _repo_slug(repo_root: str) -> str:
    payload = _gh_json(repo_root, "repo", "view", "--json", "nameWithOwner")
    slug = payload.get("nameWithOwner")
    if not isinstance(slug, str) or not slug:
        raise RuntimeError("Unable to resolve repository slug via gh repo view")
    return slug


def _required_checks_from_branch_protection(
    repo_root: str,
    repo_slug: str,
    base_branch: str,
) -> list[str]:
    payload = _gh_json(
        repo_root,
        "api",
        f"repos/{repo_slug}/branches/{base_branch}/protection/required_status_checks",
    )

    checks_raw = payload.get("checks")
    if isinstance(checks_raw, list) and checks_raw:
        out: list[str] = []
        for entry in checks_raw:
            if isinstance(entry, dict):
                context = entry.get("context")
                if isinstance(context, str) and context.strip():
                    out.append(context.strip())
        if out:
            return out

    contexts_raw = payload.get("contexts")
    out = []
    if isinstance(contexts_raw, list):
        for item in contexts_raw:
            if isinstance(item, str) and item.strip():
                out.append(item.strip())
    return out


def _required_checks_from_contract(repo_root: str) -> list[str]:
    contract_path = Path(repo_root) / "gate_contract.yaml"
    if not contract_path.exists():
        return []

    try:
        import yaml

        content = yaml.safe_load(contract_path.read_text())
    except Exception:
        return []

    if not isinstance(content, dict):
        return []

    checks = content.get("required_checks", [])
    out: list[str] = []
    if isinstance(checks, list):
        for item in checks:
            if isinstance(item, str) and item.strip():
                out.append(item.strip())
    return out


def _union_checks(primary: list[str], secondary: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for check in [*primary, *secondary]:
        if check in seen:
            continue
        seen.add(check)
        out.append(check)
    return out


def _read_check_runs(repo_root: str, repo_slug: str, sha: str) -> dict[str, tuple[str, str | None]]:
    payload = _gh_json(repo_root, "api", f"repos/{repo_slug}/commits/{sha}/check-runs")
    runs = payload.get("check_runs", [])
    out: dict[str, tuple[str, str | None]] = {}
    if not isinstance(runs, list):
        return out
    for run in runs:
        if not isinstance(run, dict):
            continue
        name = run.get("name")
        status = run.get("status")
        if not isinstance(name, str) or not isinstance(status, str):
            continue
        conclusion = run.get("conclusion")
        out[name] = (status, conclusion if isinstance(conclusion, str) else None)
    return out


def _watch_required_checks(
    repo_root: str,
    repo_slug: str,
    sha: str,
    required_checks: list[str],
    *,
    wait_seconds: int,
    timeout_seconds: int,
) -> None:
    deadline = time.time() + timeout_seconds
    print("[ship] Required checks:")
    for check in required_checks:
        print(f"  - {check}")

    while True:
        runs = _read_check_runs(repo_root, repo_slug, sha)
        pending: list[str] = []
        failed: list[str] = []

        for check in required_checks:
            status, conclusion = runs.get(check, ("missing", None))
            if status != "completed":
                pending.append(f"{check} [{status}]")
                continue
            if conclusion not in SUCCESS_CONCLUSIONS:
                failed.append(f"{check} [{conclusion or 'none'}]")

        if failed:
            raise RuntimeError(
                "Required checks failed:\n" + "\n".join(f"  - {item}" for item in failed)
            )

        if not pending:
            print("[ship] All required checks passed")
            return

        if time.time() > deadline:
            raise RuntimeError(
                "Timed out waiting for required checks:\n"
                + "\n".join(f"  - {item}" for item in pending)
            )

        print("[ship] Waiting on required checks:")
        for item in pending:
            print(f"  - {item}")
        time.sleep(wait_seconds)


def _merge_pr(repo_root: str, pr_number: int) -> None:
    print(f"[ship] Merging PR #{pr_number} with squash + branch deletion")
    _run(
        [
            "gh",
            "pr",
            "merge",
            str(pr_number),
            "--squash",
            "--delete-branch",
        ],
        cwd=repo_root,
    )


def _prune_merged_local_branches(repo_root: str, base_branch: str, current_branch: str) -> None:
    protected = {base_branch, current_branch}
    out = _git_output(repo_root, "for-each-ref", "refs/heads", "--format=%(refname:short)")
    branches = [line.strip() for line in out.splitlines() if line.strip()]

    for branch in branches:
        if branch in protected:
            continue
        if not branch.startswith(("feat/", "fix/", "quality/", "codex/")):
            continue
        merged = subprocess.run(
            ["git", "merge-base", "--is-ancestor", branch, base_branch],
            cwd=repo_root,
            text=True,
            capture_output=True,
        )
        if merged.returncode == 0:
            subprocess.run(["git", "branch", "-d", branch], cwd=repo_root, check=False)


def main() -> int:
    parser = argparse.ArgumentParser(description="Ship current branch to main with strict gate parity")
    parser.add_argument("--base", default="main", help="Base branch to merge into (default: main)")
    parser.add_argument("--remote", default="origin", help="Git remote (default: origin)")
    parser.add_argument("--wait-seconds", type=int, default=20, help="Polling interval")
    parser.add_argument("--timeout-seconds", type=int, default=3600, help="Max wait for checks")
    parser.add_argument("--no-merge", action="store_true", help="Do not auto-merge")
    parser.add_argument(
        "--prune-merged",
        action="store_true",
        help="Delete merged local side branches after merge",
    )
    args = parser.parse_args()

    repo_root = _git_output(os.getcwd(), "rev-parse", "--show-toplevel")

    _require_tool("git", repo_root)
    _require_tool("gh", repo_root)

    auth = subprocess.run(
        ["gh", "auth", "status", "-h", "github.com"],
        cwd=repo_root,
        text=True,
        capture_output=True,
    )
    if auth.returncode != 0:
        raise RuntimeError("gh auth is not configured for github.com")

    _require_clean_worktree(repo_root)

    branch = _ensure_branch(repo_root, args.base)

    print(f"[ship] Fetching {args.remote}/{args.base}")
    _run(["git", "fetch", args.remote, args.base], cwd=repo_root)

    _run_local_gate_stack(repo_root)
    _push_branch(repo_root, args.remote, branch)

    pr_number, pr_url = _resolve_pr(repo_root, branch, args.base)
    print(f"[ship] PR URL: {pr_url}")

    repo_slug = _repo_slug(repo_root)
    required_by_protection = _required_checks_from_branch_protection(repo_root, repo_slug, args.base)
    required_by_contract = _required_checks_from_contract(repo_root)
    required_checks = _union_checks(required_by_protection, required_by_contract)
    if not required_checks:
        raise RuntimeError("No required checks resolved from branch protection or gate contract")

    sha = _git_output(repo_root, "rev-parse", "HEAD")
    _watch_required_checks(
        repo_root,
        repo_slug,
        sha,
        required_checks,
        wait_seconds=args.wait_seconds,
        timeout_seconds=args.timeout_seconds,
    )

    if not args.no_merge:
        _merge_pr(repo_root, pr_number)
        if args.prune_merged:
            _run(["git", "fetch", "--prune", args.remote], cwd=repo_root)
            _prune_merged_local_branches(repo_root, args.base, branch)

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(f"[ship] ERROR: {exc}")
        raise SystemExit(1) from exc
