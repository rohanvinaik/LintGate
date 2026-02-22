"""PreToolUse hook — compass-aware advisories + quality gate before tool execution.

Theory mode: advisory on write operations (suggests reading more first).
Normal/Habit mode: alignment check against toward/away/forbidden directives.
Quality gate: blocks git push until quality checks pass, advises on git commit.

Protocol: stdin JSON → stdout JSON, exit 0 always.
"""

from __future__ import annotations

import json
import re
import sys
import time
from dataclasses import dataclass, field
from typing import Any

_WRITE_TOOLS = frozenset({"Write", "Edit", "MultiEdit", "NotebookEdit"})

# Git command patterns — push checked first so "git commit && git push" classifies as push
_PUSH_RE = re.compile(r"\bgit\s+push\b")
_COMMIT_RE = re.compile(r"\bgit\s+commit\b")


def _load_mode(project_root: str) -> str:
    """Load current cognitive mode from session memory."""
    try:
        from lintgate.controlplane.session_memory import get_or_create_session

        session = get_or_create_session(project_root)
        mode = session.behavior_compass.get("mode_state", {}).get("current", "normal")
        return str(mode or "normal")
    except Exception:
        return "normal"


def _check_theory_mode(mode: str, tool_name: str) -> str:
    """Return advisory if theory mode is active and tool is a write."""
    if mode == "theory" and tool_name in _WRITE_TOOLS:
        return (
            "[Compass] Theory mode active — consider reading more"
            " before writing. Use `theory_mode_freeze` when ready."
        )
    return ""


def _extract_bash_command(data: dict[str, Any]) -> str:
    """Extract the command string from a Bash tool_input."""
    tool_input = data.get("tool_input", data.get("input", {}))
    if isinstance(tool_input, dict):
        return str(tool_input.get("command", ""))
    if isinstance(tool_input, str):
        return tool_input
    return ""


def _check_bash_alignment(data: dict[str, Any], exec_compass: Any) -> str:
    """Check Bash command alignment against compass directives."""
    command = _extract_bash_command(data)
    if not command:
        return ""

    result = exec_compass.check_alignment(command)
    if result.get("violations"):
        return f"[Compass] Alignment violation: {result['violations'][0]}"
    if result.get("warnings"):
        return f"[Compass] Alignment warning: {result['warnings'][0]}"
    return ""


def _classify_git_command(command: str) -> str | None:
    """Classify a command as 'push', 'commit', or None.

    Push is checked first so chained commands like
    ``git commit && git push`` classify as 'push'.
    """
    if _PUSH_RE.search(command):
        return "push"
    if _COMMIT_RE.search(command):
        return "commit"
    return None


@dataclass
class QualityGateResult:
    """Result of the quality gate check."""

    should_block: bool = False
    messages: list[str] = field(default_factory=list)


def load_controlplane_config(cwd: str):
    """Lazy wrapper for config loading — patchable at module level."""
    from lintgate.config import load_controlplane_config as _load

    return _load(cwd)


def load_runtime_state(project_root: str):
    """Lazy wrapper for runtime state loading — patchable at module level."""
    from lintgate.runtime_state import load_runtime_state as _load

    return _load(project_root)


def _check_diff_secrets(project_root: str):
    """Lazy wrapper for secrets scanning — patchable at module level."""
    from lintgate.channels.git_channel import _check_diff_secrets as _check

    return _check(project_root)


def load_compass(project_root: str):
    """Lazy wrapper for compass loading — patchable at module level."""
    from lintgate.compass_io import load_compass as _load

    return _load(project_root)


def _check_quality_gate(command: str, project_root: str) -> QualityGateResult:
    """Check whether a git command should be blocked or advised.

    Fail-open on infrastructure errors (missing config, crashed secrets check).
    Fail-closed for git push when no quality run exists.
    """
    git_action = _classify_git_command(command)
    if git_action is None:
        return QualityGateResult()

    # Load config — fail-open if missing
    try:
        cp_config = load_controlplane_config(project_root)
    except Exception:
        return QualityGateResult()

    if cp_config is None or not cp_config.quality_gate.enabled:
        return QualityGateResult()

    qg = cp_config.quality_gate

    # Skip if the specific action type is disabled
    if git_action == "push" and not qg.block_push:
        return QualityGateResult()
    if git_action == "commit" and not qg.advise_commit:
        return QualityGateResult()

    failures: list[str] = []

    # Load runtime state
    try:
        state = load_runtime_state(project_root)
    except Exception:
        state = None

    if state is None:
        if git_action == "push":
            # No quality run has ever happened — block push
            failures.append("No quality run found. Run `controlplane_run` first.")
        # For commit, missing state is not actionable — skip
        if not failures:
            return QualityGateResult()
        return QualityGateResult(
            should_block=True,
            messages=[f"[QualityGate] BLOCKED: {f}" for f in failures],
        )

    # Check staleness
    age_s = time.time() - state.timestamp
    if age_s > qg.staleness_threshold_s:
        mins = int(age_s // 60)
        failures.append(
            f"Last quality run is {mins}min old"
            f" (threshold: {int(qg.staleness_threshold_s // 60)}min)."
        )

    # Check blocking issues
    if state.blocking_issues > 0:
        failures.append(f"{state.blocking_issues} blocking issue(s) remain.")

    # Check test status (empty is OK — means no test data yet)
    if state.last_test_status == "fail":
        failures.append("Tests are failing.")

    # Check secrets — fail-open if crashes
    if qg.check_secrets:
        try:
            secret_findings = _check_diff_secrets(project_root)
            if secret_findings:
                failures.append(
                    f"{len(secret_findings)} secret(s) detected in staged diff."
                )
        except Exception:
            pass  # fail-open on secrets check crash

    if not failures:
        return QualityGateResult()

    is_push = git_action == "push"
    prefix = "BLOCKED" if is_push else "Advisory"
    return QualityGateResult(
        should_block=is_push,
        messages=[f"[QualityGate] {prefix}: {f}" for f in failures],
    )


def handle(data: dict[str, Any]) -> dict[str, Any]:
    """Process PreToolUse event."""
    tool_name = data.get("tool_name", data.get("toolName", ""))
    project_root = data.get("cwd", ".")
    command = _extract_bash_command(data) if tool_name == "Bash" else ""

    # Quality gate: block git push immediately if checks fail
    gate_result = QualityGateResult()
    if command:
        gate_result = _check_quality_gate(command, project_root)
        if gate_result.should_block:
            return {
                "continue": False,
                "systemMessage": " | ".join(gate_result.messages),
            }

    mode = _load_mode(project_root)

    try:
        from lintgate.modes.execution_compass import ExecutionCompass
    except ImportError:
        if gate_result.messages:
            return {
                "continue": True,
                "systemMessage": " | ".join(gate_result.messages),
            }
        return {"continue": True}

    compass = load_compass(project_root)  # uses module-level lazy wrapper
    if compass is None and not (mode == "theory" and tool_name in _WRITE_TOOLS):
        if gate_result.messages:
            return {
                "continue": True,
                "systemMessage": " | ".join(gate_result.messages),
            }
        return {"continue": True}

    exec_compass = ExecutionCompass.from_compass_state(compass) if compass is not None else None
    messages: list[str] = list(gate_result.messages)

    # Add compass messages
    theory_msg = _check_theory_mode(mode, tool_name)
    if theory_msg:
        messages.append(theory_msg)

    if tool_name == "Bash" and exec_compass:
        alignment_msg = _check_bash_alignment(data, exec_compass)
        if alignment_msg:
            messages.append(alignment_msg)

    return {
        "continue": True,
        "systemMessage": " | ".join(messages) if messages else "",
    }


def main() -> None:
    data = json.loads(sys.stdin.read())
    result = handle(data)
    print(json.dumps(result))
    sys.exit(0)


if __name__ == "__main__":
    main()
