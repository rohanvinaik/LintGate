#!/usr/bin/env python3
"""Automated local->main ship pipeline.

This is the local pipeline driver:
1. Validate worktree is clean
2. Run strict local gates (.githooks/pre-push)
3. Push current branch
4. Create/update PR to main

CI monitoring, gate enforcement, auto-merge, and branch cleanup are
handled by the lintgate[bot] GitHub App (Cloudflare Worker). The agent's
job ends at `git push`.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


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


def _read_contract_config(repo_root: str) -> dict[str, Any]:
    """Read gate_contract.yaml and return the full config dict."""
    contract_path = Path(repo_root) / "gate_contract.yaml"
    if not contract_path.exists():
        return {}
    try:
        import yaml

        content = yaml.safe_load(contract_path.read_text())
        return content if isinstance(content, dict) else {}
    except Exception:
        return {}


def _check_mergeability(
    repo_root: str, branch: str, base_branch: str, remote: str
) -> tuple[bool, str]:
    """Check if branch can merge cleanly into base using git merge-tree.

    Returns (mergeable, detail_message).
    """
    remote_base = f"{remote}/{base_branch}"

    # Ensure we have the latest remote base
    try:
        _run(["git", "fetch", remote, base_branch], cwd=repo_root, capture=True)
    except subprocess.CalledProcessError:
        return True, "Could not fetch remote base; skipping mergeability check"

    # Find merge base
    try:
        merge_base = _git_output(repo_root, "merge-base", remote_base, "HEAD")
    except subprocess.CalledProcessError:
        return True, "No common ancestor; skipping mergeability check"

    # git merge-tree (three-way) — available in git 2.38+
    result = subprocess.run(
        [
            "git",
            "merge-tree",
            "--write-tree",
            "--merge-base",
            merge_base,
            "HEAD",
            remote_base,
        ],
        cwd=repo_root,
        text=True,
        capture_output=True,
    )

    if result.returncode == 0:
        return True, "Branch merges cleanly into base"

    # Parse conflict info from stderr/stdout.
    # git merge-tree emits conflicts as "CONFLICT (type): ..." lines.
    # Use startswith("CONFLICT (") to avoid false-positive matches on lines
    # that merely contain the word "CONFLICT" (e.g., source code comments).
    conflicts = []
    for line in (result.stdout + result.stderr).splitlines():
        stripped = line.strip()
        if stripped.startswith("CONFLICT ("):
            conflicts.append(stripped)

    if conflicts:
        detail = "Merge conflicts detected:\n" + "\n".join(
            f"  - {c}" for c in conflicts[:10]
        )
    else:
        detail = f"Branch cannot merge cleanly (git merge-tree exit code: {result.returncode})"

    return False, detail


def _auto_sync_branch(repo_root: str, base_branch: str, remote: str) -> None:
    """Rebase current branch onto remote base to sync with latest changes."""
    remote_base = f"{remote}/{base_branch}"

    # Check how far behind we are
    behind = _git_output(repo_root, "rev-list", "--count", f"HEAD..{remote_base}")
    behind_count = int(behind) if behind.isdigit() else 0

    if behind_count == 0:
        print("[ship] Branch is up to date with remote base")
        return

    print(
        f"[ship] Branch is {behind_count} commit(s) behind {remote_base}, rebasing..."
    )
    _run(["git", "rebase", remote_base], cwd=repo_root)


def _ensure_branch(repo_root: str, base_branch: str) -> str:
    branch = _git_output(repo_root, "branch", "--show-current")
    if not branch:
        raise RuntimeError("Detached HEAD is not supported for ship_main.")
    if branch != base_branch:
        return branch

    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
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


def _run_preflight(repo_root: str, json_mode: bool) -> int:
    """Run precisely the pre-push hook without side effects."""
    hook_path = Path(repo_root) / ".githooks" / "pre-push"

    def emit_json(
        status: str, exit_code: int, failed_ids: list[str], err_msg: str | None = None
    ) -> None:
        payload = {
            "status": status,
            "exit_code": exit_code,
            "failed_gate_ids": failed_ids,
            "command": str(hook_path),
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }
        if err_msg:
            payload["error"] = err_msg

        # Safely determine if we should indent
        indent = 2 if getattr(sys.stdout, "isatty", lambda: False)() else None
        print(json.dumps(payload, indent=indent))

    if not hook_path.exists():
        if json_mode:
            emit_json("error", 1, [], "Missing .githooks/pre-push")
            return 1
        raise RuntimeError("Missing .githooks/pre-push")

    if not os.access(hook_path, os.X_OK):
        if json_mode:
            emit_json("error", 1, [], ".githooks/pre-push is not executable")
            return 1
        raise RuntimeError(".githooks/pre-push is not executable")

    if not json_mode:
        print("[ship] [PREFLIGHT] Running strict local gate stack (.githooks/pre-push)")

    proc = subprocess.run(
        [str(hook_path)],
        cwd=repo_root,
        check=False,
        text=True,
        capture_output=json_mode,
    )

    if json_mode:
        status = "pass" if proc.returncode == 0 else "fail"
        failed_gate_ids = []

        # Heuristic for failed blocks
        if status == "fail":
            stdout_lower = proc.stdout.lower()
            stderr_lower = proc.stderr.lower()
            if "blocked: secrets" in stdout_lower or "blocked: secrets" in stderr_lower:
                failed_gate_ids.append("secrets_scan")
            if (
                ("blocked" in stdout_lower or "blocked" in stderr_lower)
                and "symbol_gate" not in failed_gate_ids
                and ("symbol" in stdout_lower or "symbol" in stderr_lower)
            ):
                failed_gate_ids.append("symbol_gate")
            if ("incomplete" in stdout_lower or "incomplete" in stderr_lower) and (
                "quality infrastructure" in stdout_lower
                or "quality infrastructure" in stderr_lower
            ):
                failed_gate_ids.append("quality_infra")
            if "pytest" in stdout_lower and (
                proc.stdout.count("FAILED ") > 0 or proc.stdout.count("FAILURES ") > 0
            ):
                failed_gate_ids.append("pytest")
            if "sonar" in stdout_lower and "fail" in stdout_lower:
                failed_gate_ids.append("sonar")

            if not failed_gate_ids:
                failed_gate_ids.append("pre-push-hook")

        emit_json(status, proc.returncode, failed_gate_ids)
        return proc.returncode

    return proc.returncode


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
        "- lintgate[bot] handles CI monitoring, gate enforcement, and auto-merge"
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


def _prune_merged_local_branches(
    repo_root: str, base_branch: str, current_branch: str
) -> None:
    protected = {base_branch, current_branch}
    out = _git_output(
        repo_root, "for-each-ref", "refs/heads", "--format=%(refname:short)"
    )
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


def _post_merge_sync(repo_root: str, base_branch: str, shipped_branch: str) -> None:
    """Sync local state after a remote merge: checkout base, pull, delete shipped branch."""
    print(f"[ship] Post-merge sync: checking out {base_branch}")
    _run(["git", "checkout", base_branch], cwd=repo_root)
    _run(["git", "pull", "--ff-only"], cwd=repo_root)

    # Delete the shipped branch locally (safe — it was already merged remotely)
    result = subprocess.run(
        ["git", "branch", "-d", shipped_branch],
        cwd=repo_root,
        text=True,
        capture_output=True,
    )
    if result.returncode == 0:
        print(f"[ship] Deleted local branch: {shipped_branch}")
    else:
        print(f"[ship] Could not delete {shipped_branch}: {result.stderr.strip()}")


def _check_compliance(repo_root: str) -> None:
    contract = _read_contract_config(repo_root)
    if not contract:
        return

    comp_cfg = contract.get("compliance", {})
    min_rate = comp_cfg.get("min_rate", 0.0)
    block = comp_cfg.get("block_on_low_compliance", False)

    if min_rate <= 0:
        return

    # Dynamic import to avoid boatloads of dependencies if not needed
    try:
        from lintgate.controlplane.session_memory import load_session

        session = load_session(repo_root)
        if not session:
            return

        # Access compliance_rate from behavior_compass
        bc = session.behavior_compass
        rate = bc.get("compliance_rate", 1.0)

        print(f"[ship] Agent compliance rate: {rate:.2f} (required: {min_rate:.2f})")
        if rate < min_rate:
            msg = f"Agent compliance rate ({rate:.2f}) is below minimum required ({min_rate:.2f})."
            if block:
                raise RuntimeError(msg)
            else:
                print(f"[ship] WARNING: {msg}")
    except (ImportError, AttributeError):
        pass


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Ship current branch to main — local gates then push. "
        "CI monitoring, gate enforcement, and auto-merge are handled by lintgate[bot]."
    )
    parser.add_argument(
        "--base", default="main", help="Base branch to merge into (default: main)"
    )
    parser.add_argument(
        "--remote", default="origin", help="Git remote (default: origin)"
    )
    parser.add_argument(
        "--prune-merged",
        action="store_true",
        help="Delete merged local side branches before push",
    )
    parser.add_argument(
        "--preflight",
        action="store_true",
        help="Run strict gate check locally without modifying git state. Exits cleanly upon completion.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output machine-readable JSON (Only valid with --preflight)",
    )
    parser.add_argument(
        "--auto-sync",
        action="store_true",
        help="Rebase onto remote base branch before running gates (resolves behind-main drift)",
    )
    parser.add_argument(
        "--post-merge-sync",
        action="store_true",
        help="After push+PR, poll for merge then sync local (checkout base, pull, delete branch)",
    )
    args = parser.parse_args()

    repo_root = _git_output(os.getcwd(), "rev-parse", "--show-toplevel")

    if args.preflight:
        # Preflight strictly guarantees NO SIDE EFFECTS
        _check_compliance(repo_root)
        return _run_preflight(repo_root, args.json)

    if args.json and not args.preflight:
        raise RuntimeError("--json can only be used with --preflight")

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

    # Auto-sync: rebase onto remote base if behind
    if args.auto_sync:
        _auto_sync_branch(repo_root, args.base, args.remote)

    # Mergeability check: fail fast if branch has conflicts with base
    mergeable, merge_detail = _check_mergeability(
        repo_root, branch, args.base, args.remote
    )
    if not mergeable:
        raise RuntimeError(
            f"Branch '{branch}' cannot merge cleanly into '{args.base}'.\n"
            f"{merge_detail}\n"
            "Resolve conflicts first, or use --auto-sync to rebase."
        )
    print(f"[ship] Mergeability: {merge_detail}")

    # Prune merged local branches before push (Layer 3 cleanup)
    if args.prune_merged:
        _prune_merged_local_branches(repo_root, args.base, branch)

    _check_compliance(repo_root)
    _run_local_gate_stack(repo_root)
    _push_branch(repo_root, args.remote, branch)

    _resolve_pr(repo_root, branch, args.base)

    # ── Terminal action ──────────────────────────────────────────────
    # CI monitoring, gate contract enforcement, auto-merge, and branch
    # cleanup are handled by lintgate[bot] (Cloudflare Worker).
    print("[ship] Pushed. lintgate[bot] handles CI → merge.")

    if args.post_merge_sync:
        _post_merge_sync(repo_root, args.base, branch)

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(f"[ship] ERROR: {exc}")
        raise SystemExit(1) from exc
