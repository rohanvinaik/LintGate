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
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SUCCESS_CONCLUSIONS = {"success", "neutral", "skipped"}

# Classification constants for telemetry
# transport_flake: likely transient network/infra failure that may pass on rerun
# code_failure: likely real regression requiring code changes
# unknown: cannot determine from available signals
CLASSIFICATION_TRANSPORT_FLAKE = "transport_flake"
CLASSIFICATION_CODE_FAILURE = "code_failure"
CLASSIFICATION_UNKNOWN = "unknown"

# Known transient failure signatures (transport/infra layer)
# These patterns suggest flaky failures that might pass on rerun
TRANSIENT_FAILURE_SIGNATURES = [
    "connection",
    "timeout",
    "network",
    "dns",
    "ssl",
    "tls",
    "certificate",
    "500",
    "502",
    "503",
    "504",
    "temporary",
    "transient",
    "unavailable",
    "rate limit",
    "rate_limit",
    "quota",
    "api",
]


def _classify_failure(
    check_name: str,
    conclusion: str | None,
    error_message: str | None = None,
) -> str:
    """Classify a check failure as transport_flake, code_failure, or unknown.

    Classification rules:
    - If failure message contains transient error signatures -> transport_flake
    - If check is known to be flaky (e.g., qlty with retry logic) -> transport_flake
    - If failure is clear code/test issue (pytest failures, lint errors) -> code_failure
    - Otherwise -> unknown
    """
    # Check combined string for transient signatures (these can appear in check_name)
    combined = " ".join(s.lower() for s in [check_name, conclusion or "", error_message or ""] if s)

    # Check for transient signatures
    for signature in TRANSIENT_FAILURE_SIGNATURES:
        if signature in combined:
            return CLASSIFICATION_TRANSPORT_FLAKE

    # For code failure, only check conclusion (not check_name to avoid false positives)
    conclusion_lower = (conclusion or "").lower()

    # Known code failure patterns - only look at conclusion
    code_failure_patterns = [
        "failed",
        "error",
        "failure",
    ]

    for pattern in code_failure_patterns:
        if pattern in conclusion_lower:
            return CLASSIFICATION_CODE_FAILURE

    return CLASSIFICATION_UNKNOWN


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
                "quality infrastructure" in stdout_lower or "quality infrastructure" in stderr_lower
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
    telemetry_output: dict[str, Any] | None = None,
) -> None:
    """Watch required check status with optional telemetry tracking.

    Args:
        repo_root: Repository root path.
        repo_slug: Repository slug (owner/name).
        sha: Git SHA to check.
        required_checks: List of required check names.
        wait_seconds: Polling interval.
        timeout_seconds: Max wait time.
        telemetry_output: Optional dict to collect telemetry data.
    """
    deadline = time.time() + timeout_seconds
    print("[ship] Required checks:")
    for check in required_checks:
        print(f"  - {check}")

    # Initialize telemetry tracking
    if telemetry_output is not None:
        telemetry_output["checks"] = {}
        for check in required_checks:
            telemetry_output["checks"][check] = {
                "transitions": 0,
                "first_failure_signature": None,
                "final_status": "unknown",
                "classification": CLASSIFICATION_UNKNOWN,
            }

    while True:
        runs = _read_check_runs(repo_root, repo_slug, sha)
        pending: list[str] = []
        failed: list[str] = []

        for check in required_checks:
            status, conclusion = runs.get(check, ("missing", None))

            # Track telemetry
            if telemetry_output is not None:
                check_telemetry = telemetry_output["checks"][check]
                check_telemetry["transitions"] += 1
                check_telemetry["_last_status"] = status

                # Track first failure signature
                if (
                    status == "completed"
                    and conclusion not in SUCCESS_CONCLUSIONS
                    and check_telemetry["first_failure_signature"] is None
                ):
                    check_telemetry["first_failure_signature"] = conclusion
                    check_telemetry["classification"] = _classify_failure(check, conclusion)

            if status != "completed":
                pending.append(f"{check} [{status}]")
                continue
            if conclusion not in SUCCESS_CONCLUSIONS:
                failed.append(f"{check} [{conclusion or 'none'}]")

        if failed:
            # Update final status in telemetry
            if telemetry_output is not None:
                for check in failed:
                    check_name = check.split(" [")[0]
                    conclusion = check.split(" [")[1].rstrip("]") if " [" in check else None
                    if check_name in telemetry_output["checks"]:
                        telemetry_output["checks"][check_name]["final_status"] = (
                            conclusion or "failed"
                        )
                        if (
                            telemetry_output["checks"][check_name]["classification"]
                            == CLASSIFICATION_UNKNOWN
                        ):
                            telemetry_output["checks"][check_name]["classification"] = (
                                _classify_failure(check_name, conclusion)
                            )

            raise RuntimeError(
                "Required checks failed:\n" + "\n".join(f"  - {item}" for item in failed)
            )

        if not pending:
            # Update final status for all checks in telemetry
            if telemetry_output is not None:
                for check in required_checks:
                    check_telemetry = telemetry_output["checks"][check]
                    check_telemetry["final_status"] = "passed"
                    if check_telemetry["classification"] == CLASSIFICATION_UNKNOWN:
                        check_telemetry["classification"] = (
                            CLASSIFICATION_CODE_FAILURE  # Passed = not a permanent failure
                        )

            print("[ship] All required checks passed")
            return

        if time.time() > deadline:
            # Update final status for pending checks
            if telemetry_output is not None:
                for check in pending:
                    check_name = check.split(" [")[0]
                    if check_name in telemetry_output["checks"]:
                        telemetry_output["checks"][check_name]["final_status"] = "timeout"

            raise RuntimeError(
                "Timed out waiting for required checks:\n"
                + "\n".join(f"  - {item}" for item in pending)
            )

        print("[ship] Waiting on required checks:")
        for item in pending:
            print(f"  - {item}")
        time.sleep(wait_seconds)


def _merge_pr(repo_root: str, pr_number: int) -> None:
    print(f"[ship] Enabling GitHub auto-merge for PR #{pr_number} (squash + branch deletion)")
    _run(
        [
            "gh",
            "pr",
            "merge",
            str(pr_number),
            "--auto",
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
    parser = argparse.ArgumentParser(
        description="Ship current branch to main with strict gate parity"
    )
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
    parser.add_argument(
        "--preflight",
        action="store_true",
        help="Run strict gate parity check locally without modifying git state. Exits cleanly upon completion.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output machine-readable JSON (Only valid with --preflight)",
    )
    parser.add_argument(
        "--telemetry",
        action="store_true",
        help="Output structured telemetry about check runs (flaky vs code failure classification)",
    )
    parser.add_argument(
        "--telemetry-path",
        type=str,
        default=None,
        help="Path to write telemetry JSON file (default: stdout)",
    )
    args = parser.parse_args()

    repo_root = _git_output(os.getcwd(), "rev-parse", "--show-toplevel")

    if args.preflight:
        # Preflight strictly guarantees NO SIDE EFFECTS: no auth, no branching, no pushing
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

    _run_local_gate_stack(repo_root)
    _push_branch(repo_root, args.remote, branch)

    pr_number, pr_url = _resolve_pr(repo_root, branch, args.base)
    print(f"[ship] PR URL: {pr_url}")

    repo_slug = _repo_slug(repo_root)
    required_by_protection = _required_checks_from_branch_protection(
        repo_root, repo_slug, args.base
    )
    required_by_contract = _required_checks_from_contract(repo_root)
    required_checks = _union_checks(required_by_protection, required_by_contract)
    if not required_checks:
        raise RuntimeError("No required checks resolved from branch protection or gate contract")

    sha = _git_output(repo_root, "rev-parse", "HEAD")

    # Initialize telemetry if requested
    telemetry_output: dict[str, Any] | None = None
    if args.telemetry:
        telemetry_output = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "repo_slug": repo_slug,
            "sha": sha,
            "branch": branch,
            "checks": {},
        }

    try:
        _watch_required_checks(
            repo_root,
            repo_slug,
            sha,
            required_checks,
            wait_seconds=args.wait_seconds,
            timeout_seconds=args.timeout_seconds,
            telemetry_output=telemetry_output,
        )
        watch_result = "success"
    except RuntimeError:
        watch_result = "failure"
        raise
    finally:
        # Output telemetry if requested
        if telemetry_output is not None:
            telemetry_output["result"] = watch_result

            # Calculate summary statistics
            checks_data = telemetry_output.get("checks", {})
            transport_flake_count = sum(
                1
                for c in checks_data.values()
                if c.get("classification") == CLASSIFICATION_TRANSPORT_FLAKE
            )
            code_failure_count = sum(
                1
                for c in checks_data.values()
                if c.get("classification") == CLASSIFICATION_CODE_FAILURE
            )
            unknown_count = sum(
                1 for c in checks_data.values() if c.get("classification") == CLASSIFICATION_UNKNOWN
            )

            telemetry_output["summary"] = {
                "transport_flake_count": transport_flake_count,
                "code_failure_count": code_failure_count,
                "unknown_count": unknown_count,
            }

            # Write telemetry output
            telemetry_json = json.dumps(telemetry_output, indent=2)
            if args.telemetry_path:
                Path(args.telemetry_path).write_text(telemetry_json)
                print(f"[ship] Telemetry written to {args.telemetry_path}")
            else:
                print("[ship] Telemetry:")
                print(telemetry_json)

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
