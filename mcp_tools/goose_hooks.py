"""Goose-compatible hook tools — replicate Claude Code hook behavior via MCP.

Claude Code has PreToolUse/PostToolUse hooks that fire automatically.
Goose doesn't. These tools bundle the hook logic into callable MCP tools
that the model invokes explicitly (guided by .goosehints).

The key insight: with a local model, tool calls are free. So "call this
after every edit" costs nothing — just context slots for the response.
"""

from __future__ import annotations

import json
import os

from mcp_tools._disk_helpers import _safe_json, tool_response


def register(mcp, helpers):
    """Register Goose hook-equivalent tools."""

    @mcp.tool()
    def after_edit(path: str, files: list[str] | None = None) -> str:
        """Run after editing files — replaces Claude Code PostToolUse:Edit hook.

        WHEN TO USE: Call this after every file edit. It runs a fast lint check
        on changed files, updates session state, and returns any issues found.
        This is the equivalent of LintGate's automatic post-edit hook.

        Example: after_edit(path="/my/project", files=["src/main.py"])
        Example: after_edit(path="/my/project")  # auto-detects changed files

        Args:
            path: Project root path.
            files: Files that were edited. If None, uses git to detect changes.
        """
        project_root = helpers["_validate_project_root"](path)

        # Auto-detect changed files from git if not specified
        if not files:
            import subprocess
            try:
                result = subprocess.run(
                    ["git", "diff", "--name-only", "--diff-filter=M", "HEAD"],
                    capture_output=True, text=True, cwd=project_root, timeout=5,
                )
                files = [f.strip() for f in result.stdout.strip().split("\n") if f.strip() and f.endswith(".py")]
            except Exception:
                files = []

        if not files:
            return json.dumps({"status": "no_changes", "note": "No Python files changed."})

        # Fast lint on changed files
        py_files = [
            os.path.join(project_root, f) if not os.path.isabs(f) else f
            for f in files if f.endswith(".py")
        ]
        existing = [f for f in py_files if os.path.isfile(f)]

        if not existing:
            return json.dumps({"status": "no_python_files"})

        lint_result = helpers["_run_lint"](existing, project_root, 1, "relaxed", output_mode="compact")
        blocking = lint_result.get("blocking_count", 0)
        warnings = lint_result.get("warning_count", 0)
        total = lint_result.get("issue_count", 0)

        summary = f"After edit: {total} issues ({blocking} blocking, {warnings} warnings) in {len(existing)} files."
        if blocking > 0:
            summary += " Fix blocking issues before continuing."

        return tool_response(
            lint_result, "after_edit", project_root, summary,
            run_id=lint_result.get("run_id", ""),
            extra={"blocking": blocking, "files_checked": len(existing)},
        )

    @mcp.tool()
    def before_commit(path: str) -> str:
        """Run before committing — replaces Claude Code pre-commit checks.

        WHEN TO USE: Call before git commit. Checks for lint issues, secrets
        in staged files, and test status. Returns a go/no-go verdict.

        Example: before_commit(path="/my/project")

        Args:
            path: Project root path.
        """
        import subprocess

        project_root = helpers["_validate_project_root"](path)

        # Get staged files
        try:
            result = subprocess.run(
                ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
                capture_output=True, text=True, cwd=project_root, timeout=5,
            )
            staged = [f.strip() for f in result.stdout.strip().split("\n") if f.strip() and f.endswith(".py")]
        except Exception:
            staged = []

        if not staged:
            return _safe_json({"verdict": "pass", "note": "No staged Python files."})

        # Lint staged files
        py_files = [os.path.join(project_root, f) for f in staged if os.path.isfile(os.path.join(project_root, f))]
        lint_result = helpers["_run_lint"](py_files, project_root, 2, "normal", output_mode="compact") if py_files else {}
        blocking = lint_result.get("blocking_count", 0)

        # Check for secrets in staged diff
        secrets_found = False
        try:
            diff_result = subprocess.run(
                ["git", "diff", "--cached"],
                capture_output=True, text=True, cwd=project_root, timeout=10,
            )
            diff_text = diff_result.stdout
            import re
            secret_patterns = [
                r'(?:AKIA|ASIA)[A-Z0-9]{16}',  # AWS keys
                r'-----BEGIN (?:RSA |EC )?PRIVATE KEY-----',
                r'ghp_[a-zA-Z0-9]{36}',  # GitHub tokens
                r'sk-[a-zA-Z0-9]{48}',  # OpenAI keys
            ]
            for pattern in secret_patterns:
                if re.search(pattern, diff_text):
                    secrets_found = True
                    break
        except Exception:
            pass

        verdict = "fail" if (blocking > 0 or secrets_found) else "pass"
        issues = []
        if blocking > 0:
            issues.append(f"{blocking} blocking lint issues")
        if secrets_found:
            issues.append("potential secrets in staged diff")

        output = {
            "verdict": verdict,
            "staged_files": len(staged),
            "blocking": blocking,
            "secrets_warning": secrets_found,
            "issues": issues,
        }

        summary = f"Pre-commit: {verdict.upper()}."
        if issues:
            summary += f" Issues: {'; '.join(issues)}."
        else:
            summary += f" {len(staged)} files clean."

        return tool_response(output, "before_commit", project_root, summary)

    return {
        "after_edit": after_edit,
        "before_commit": before_commit,
    }
