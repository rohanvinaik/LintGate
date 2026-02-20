"""Behavioral model calibration probe v2.

Replaces the v1 multiple-choice quiz with micro-task behavioral probes
that observe what a model DOES rather than what it SAYS it would do.

Each probe task presents a small coding scenario. The model's response
is scored by extracting structured behavioral features (tool calls,
action order, verification cadence, constraint references) — not by
matching prose or quiz answers. This measures revealed policy, not
stated policy.

Probe design principles:
- Action traces first, text second. tool_calls + order + retries +
  verification cadence is harder to game than prose.
- Deterministic scoring via extracted features, not regex-only.
- Task variants rotate on the same behavioral target (anti-gaming).
- Weak prior that decays fast as telemetry arrives (EMA cap).
- Structured response schema: optional tool-event trace fields
  supplement free text to avoid measuring stated policy.
- Fallback: incomplete/failed probes set neutral prior, never
  high-confidence risky prior.

Signal space (9 signals, same as v1):
- approach_cycling, failure_amnesia, serial_discovery,
  premature_action, verification_debt, stale_model,
  tool_repetition, brute_force_escalation, consecutive_failures
"""

from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .model_profiles import ModelProfile

# ── Version ──────────────────────────────────────────────────────────

PROBE_VERSION = 2
SUPPORTED_PROBE_SETS = {"quick"}

# Maximum initial confidence from probe alone (decays fast via EMA)
PROBE_MAX_CONFIDENCE = 0.60

# ── v1 Compatibility ────────────────────────────────────────────────

V1_PROBE_VERSION = 1


# ── Data Structures ─────────────────────────────────────────────────


@dataclass
class TaskVariant:
    """One surface-level variant of a probe task.

    Multiple variants target the same behavioral signals but use
    different wording/context to prevent overfitting.
    """

    variant_id: str
    context: str  # Scenario description
    instruction: str  # What to do
    setup_files: dict[str, str] = field(default_factory=dict)  # filename -> content


@dataclass
class BehavioralFeature:
    """An extracted feature from model response, used for scoring.

    Features are computed from the structured response, not matched
    via raw regex. Each feature contributes a delta to a signal.
    """

    name: str  # e.g. "read_before_edit", "exact_retry_count"
    signal: str  # Which signal this feature affects
    delta_if_present: float  # Delta when feature IS detected
    delta_if_absent: float  # Delta when feature is NOT detected
    weight: float = 1.0  # Relative importance (0.0-1.0)


@dataclass
class ProbeTask:
    """A behavioral probe task with variants and scoring features.

    Each task targets 1-3 behavioral signals. Variants rotate to
    prevent surface-level gaming. Scoring is from extracted features.
    """

    id: str
    target_signals: list[str]  # Primary signals this task measures
    description: str  # Internal description (not shown to model)
    variants: list[TaskVariant]
    features: list[BehavioralFeature]


# ── Probe Task Bank ─────────────────────────────────────────────────

PROBE_TASKS: list[ProbeTask] = [
    ProbeTask(
        id="t1_error_reading",
        target_signals=["failure_amnesia", "premature_action"],
        description="Does the model read errors carefully or pattern-match?",
        variants=[
            TaskVariant(
                variant_id="t1_v1",
                context=(
                    "A test is failing. The error message says 'TypeError: expected str, "
                    "got int' on line 15. But the actual bug is on line 8: a variable is "
                    "shadowed by a loop variable with the same name."
                ),
                instruction=(
                    "Fix the failing test. Describe your approach: what would you "
                    "read first, what tool calls would you make, and what fix would "
                    "you apply?"
                ),
                setup_files={
                    "utils.py": (
                        "def format_items(items):\n"
                        "    result = []\n"
                        "    for item in items:\n"
                        "        label = item['name']\n"
                        "        for label in item.get('tags', []):\n"
                        "            pass  # Bug: shadows 'label' from line 4\n"
                        "        result.append(f'{label}: {item[\"count\"]}')\n"
                        "    return result\n"
                    ),
                    "test_utils.py": (
                        "def test_format_items():\n"
                        "    items = [{'name': 'Widget', 'count': 5, 'tags': ['sale']}]\n"
                        "    result = format_items(items)\n"
                        "    assert result == ['Widget: 5']  # Fails: gets 'sale: 5'\n"
                    ),
                    "error_output": (
                        "FAILED test_utils.py::test_format_items\n"
                        "AssertionError: assert ['sale: 5'] == ['Widget: 5']\n"
                        "  At index 0 diff: 'sale: 5' != 'Widget: 5'\n"
                    ),
                },
            ),
            TaskVariant(
                variant_id="t1_v2",
                context=(
                    "A function returns wrong results. The error trace points to "
                    "a string comparison on line 22, but the root cause is a "
                    "missing .strip() on line 10 where user input is read."
                ),
                instruction=(
                    "Diagnose and fix the bug. Describe your approach: what would "
                    "you examine first, what tool calls would you make, and what "
                    "fix would you apply?"
                ),
                setup_files={
                    "parser.py": (
                        "def parse_config(lines):\n"
                        "    config = {}\n"
                        "    for line in lines:\n"
                        "        key, _, value = line.partition('=')\n"
                        "        config[key] = value  # Bug: no .strip()\n"
                        "    return config\n"
                        "\n"
                        "def get_setting(config, key):\n"
                        "    return config.get(key)  # Returns ' value' not 'value'\n"
                    ),
                    "test_parser.py": (
                        "def test_parse_config():\n"
                        "    lines = ['debug = true', 'port = 8080']\n"
                        "    config = parse_config(lines)\n"
                        "    assert config['debug'] == 'true'  # Fails: ' true' != 'true'\n"
                    ),
                    "error_output": (
                        "FAILED test_parser.py::test_parse_config\n"
                        "AssertionError: assert ' true' == 'true'\n"
                        "  - ' true'\n"
                        "  + 'true'\n"
                    ),
                },
            ),
        ],
        features=[
            BehavioralFeature(
                name="read_before_edit",
                signal="premature_action",
                delta_if_present=-0.25,
                delta_if_absent=0.30,
            ),
            BehavioralFeature(
                name="identifies_root_cause",
                signal="failure_amnesia",
                delta_if_present=-0.20,
                delta_if_absent=0.35,
            ),
            BehavioralFeature(
                name="follows_misleading_error",
                signal="failure_amnesia",
                delta_if_present=0.35,
                delta_if_absent=-0.10,
            ),
            BehavioralFeature(
                name="mentions_verification",
                signal="verification_debt",
                delta_if_present=-0.15,
                delta_if_absent=0.15,
            ),
        ],
    ),
    ProbeTask(
        id="t2_retry_behavior",
        target_signals=["tool_repetition", "approach_cycling"],
        description="Does the model retry blindly or change approach?",
        variants=[
            TaskVariant(
                variant_id="t2_v1",
                context=(
                    "You ran `pytest tests/test_api.py -v` and it failed with "
                    "'ModuleNotFoundError: No module named requests'. You've "
                    "already seen this error."
                ),
                instruction=(
                    "The command failed. Describe exactly what you would do next. "
                    "Include specific tool calls and their order."
                ),
                setup_files={
                    "previous_attempt": "pytest tests/test_api.py -v → ModuleNotFoundError",
                    "project_state": "pyproject.toml exists, .venv/ exists, requests not installed",
                },
            ),
            TaskVariant(
                variant_id="t2_v2",
                context=(
                    "You ran `make build` and it failed with 'gcc: error: "
                    "libfoo.h: No such file or directory'. You've seen this "
                    "exact error twice already."
                ),
                instruction=(
                    "This is the third time you've seen this error. Describe "
                    "exactly what you would do differently this time. Include "
                    "specific tool calls and their order."
                ),
                setup_files={
                    "previous_attempts": (
                        "Attempt 1: make build → libfoo.h not found\n"
                        "Attempt 2: make build CFLAGS=-I/usr/local/include → libfoo.h not found"
                    ),
                },
            ),
        ],
        features=[
            BehavioralFeature(
                name="exact_retry",
                signal="tool_repetition",
                delta_if_present=0.40,
                delta_if_absent=-0.10,
            ),
            BehavioralFeature(
                name="minor_variant_only",
                signal="approach_cycling",
                delta_if_present=0.30,
                delta_if_absent=-0.10,
            ),
            BehavioralFeature(
                name="reads_error_first",
                signal="premature_action",
                delta_if_present=-0.20,
                delta_if_absent=0.15,
            ),
            BehavioralFeature(
                name="references_previous_attempts",
                signal="failure_amnesia",
                delta_if_present=-0.25,
                delta_if_absent=0.20,
            ),
        ],
    ),
    ProbeTask(
        id="t3_verification_cadence",
        target_signals=["verification_debt"],
        description="When does the model verify during a multi-step fix?",
        variants=[
            TaskVariant(
                variant_id="t3_v1",
                context=(
                    "A Python file has 3 independent bugs:\n"
                    "1. Line 5: off-by-one in range(len(items)-1) should be range(len(items))\n"
                    "2. Line 12: missing 'self.' prefix on instance variable\n"
                    "3. Line 20: wrong comparison operator (== instead of !=)"
                ),
                instruction=(
                    "Fix all 3 bugs. For each fix, describe: the edit you'd make, "
                    "and whether you would run tests before moving to the next fix."
                ),
                setup_files={
                    "buggy.py": (
                        "class Processor:\n"
                        "    def __init__(self, items):\n"
                        "        self.items = items\n"
                        "        self.processed = []\n"
                        "    \n"
                        "    def run(self):\n"
                        "        for i in range(len(self.items) - 1):  # Bug 1\n"
                        "            item = self.items[i]\n"
                        "            if item.get('active'):\n"
                        "                processed.append(item)  # Bug 2: missing self.\n"
                        "        return self.processed\n"
                        "    \n"
                        "    def filter_done(self):\n"
                        "        return [i for i in self.processed if i['status'] == 'done']  # Bug 3: should be !=\n"
                    ),
                },
            ),
            TaskVariant(
                variant_id="t3_v2",
                context=(
                    "A configuration module has 3 issues:\n"
                    "1. Default value is mutable (empty list as default arg)\n"
                    "2. Missing type check on input parameter\n"
                    "3. Return value not consistent (sometimes None, sometimes dict)"
                ),
                instruction=(
                    "Fix all 3 issues. For each fix, state: what you'd change, "
                    "and whether you would verify before moving to the next fix."
                ),
                setup_files={
                    "config.py": (
                        "def load_config(path, defaults=[]):\n"  # Bug 1
                        "    with open(path) as f:\n"
                        "        data = json.load(f)\n"
                        "    if not data:\n"  # Bug 3: returns None implicitly
                        "        return\n"
                        "    for key in data:  # Bug 2: data might not be dict\n"
                        "        if key not in defaults:\n"
                        "            defaults.append(key)\n"
                        "    return data\n"
                    ),
                },
            ),
        ],
        features=[
            BehavioralFeature(
                name="verifies_after_each",
                signal="verification_debt",
                delta_if_present=-0.35,
                delta_if_absent=0.10,
            ),
            BehavioralFeature(
                name="verifies_after_some",
                signal="verification_debt",
                delta_if_present=-0.15,
                delta_if_absent=0.10,
            ),
            BehavioralFeature(
                name="no_verification_mentioned",
                signal="verification_debt",
                delta_if_present=0.40,
                delta_if_absent=-0.10,
            ),
            BehavioralFeature(
                name="batch_all_then_verify",
                signal="verification_debt",
                delta_if_present=0.20,
                delta_if_absent=-0.05,
            ),
        ],
    ),
    ProbeTask(
        id="t4_constraint_discovery",
        target_signals=["serial_discovery", "brute_force_escalation"],
        description="Does the model read context before acting?",
        variants=[
            TaskVariant(
                variant_id="t4_v1",
                context=(
                    "You're asked to add a new CLI command to an unfamiliar Python "
                    "project. The project uses Click and has existing commands in "
                    "src/cli/commands/. There's a pyproject.toml with entry points "
                    "and a CONTRIBUTING.md with conventions."
                ),
                instruction=(
                    "Describe your first 5 actions (tool calls) before writing "
                    "any code for the new command. Be specific about what you'd "
                    "read, search, or check."
                ),
                setup_files={
                    "project_structure": (
                        "src/cli/commands/list_cmd.py\n"
                        "src/cli/commands/add_cmd.py\n"
                        "src/cli/main.py\n"
                        "pyproject.toml\n"
                        "CONTRIBUTING.md\n"
                        "tests/test_cli.py"
                    ),
                },
            ),
            TaskVariant(
                variant_id="t4_v2",
                context=(
                    "You need to fix a build failure in a project you've never "
                    "seen before. The project has a Makefile, a pyproject.toml, "
                    "a .github/workflows/ci.yml, and a docs/build-guide.md."
                ),
                instruction=(
                    "Describe your first 5 actions before attempting any fix. "
                    "Be specific about what you'd read or investigate."
                ),
                setup_files={
                    "build_error": "make: *** [build] Error 2",
                    "available_files": (
                        "Makefile\n"
                        "pyproject.toml\n"
                        ".github/workflows/ci.yml\n"
                        "docs/build-guide.md\n"
                        "src/"
                    ),
                },
            ),
        ],
        features=[
            BehavioralFeature(
                name="reads_docs_first",
                signal="serial_discovery",
                delta_if_present=-0.30,
                delta_if_absent=0.25,
            ),
            BehavioralFeature(
                name="reads_config_first",
                signal="serial_discovery",
                delta_if_present=-0.20,
                delta_if_absent=0.15,
            ),
            BehavioralFeature(
                name="reads_existing_code",
                signal="premature_action",
                delta_if_present=-0.20,
                delta_if_absent=0.20,
            ),
            BehavioralFeature(
                name="jumps_to_fix",
                signal="brute_force_escalation",
                delta_if_present=0.35,
                delta_if_absent=-0.10,
            ),
        ],
    ),
    ProbeTask(
        id="t5_model_updating",
        target_signals=["stale_model", "approach_cycling"],
        description="Does the model learn from prior failures?",
        variants=[
            TaskVariant(
                variant_id="t5_v1",
                context=(
                    "You've tried 2 approaches to fix a failing database migration:\n\n"
                    "Attempt 1: Added a nullable column with ALTER TABLE → failed because "
                    "SQLite doesn't support ALTER TABLE ADD COLUMN with defaults.\n\n"
                    "Attempt 2: Created a new table with the column and copied data → "
                    "failed because a foreign key constraint on another table references "
                    "the old table name."
                ),
                instruction=(
                    "Propose a third approach. Explain how your approach accounts "
                    "for what you learned from attempts 1 and 2."
                ),
                setup_files={
                    "attempt1_error": "OperationalError: Cannot add a column with non-constant default",
                    "attempt2_error": (
                        "IntegrityError: FOREIGN KEY constraint failed "
                        "(table 'orders' references 'products')"
                    ),
                },
            ),
            TaskVariant(
                variant_id="t5_v2",
                context=(
                    "You've tried 2 approaches to fix a Docker build:\n\n"
                    "Attempt 1: Changed base image to python:3.12 → failed because "
                    "a C extension needs gcc which isn't in the slim image.\n\n"
                    "Attempt 2: Added 'RUN apt-get install gcc' → failed because "
                    "the package also needs libpq-dev for psycopg2."
                ),
                instruction=(
                    "Propose a third approach. Explain what constraints from "
                    "attempts 1 and 2 your new approach satisfies."
                ),
                setup_files={
                    "attempt1_error": "error: command 'gcc' failed: No such file or directory",
                    "attempt2_error": "Error: pg_config executable not found. (libpq-dev needed)",
                },
            ),
        ],
        features=[
            BehavioralFeature(
                name="references_both_failures",
                signal="stale_model",
                delta_if_present=-0.35,
                delta_if_absent=0.30,
            ),
            BehavioralFeature(
                name="addresses_known_constraints",
                signal="approach_cycling",
                delta_if_present=-0.25,
                delta_if_absent=0.25,
            ),
            BehavioralFeature(
                name="ignores_previous_errors",
                signal="failure_amnesia",
                delta_if_present=0.30,
                delta_if_absent=-0.10,
            ),
            BehavioralFeature(
                name="minor_variant_of_attempt2",
                signal="approach_cycling",
                delta_if_present=0.30,
                delta_if_absent=-0.10,
            ),
        ],
    ),
]


# ── Signal-to-Anti-Pattern Mapping (unchanged from v1) ──────────────

_SIGNAL_ANTI_PATTERN_MAP: dict[str, str] = {
    "approach_cycling": (
        "Do not try a 4th approach without first enumerating all known constraints "
        "and verifying which ones the new approach actually addresses."
    ),
    "failure_amnesia": (
        "Do not re-attempt an approach that already failed unless the conditions "
        "that caused the failure have changed."
    ),
    "serial_discovery": (
        "Do not discover constraints one-at-a-time through failure — enumerate "
        "the full constraint space upfront by reading before acting."
    ),
    "premature_action": (
        "Do not act before understanding — convert unbounded aesthetic tasks "
        "into bounded checklists before beginning work."
    ),
    "verification_debt": (
        "Do not defer verification — run tests after every 2-3 edits, not "
        "only at the end of a long editing sequence."
    ),
    "stale_model": (
        "Do not keep using the same mental model after it has been falsified — "
        "update your hypothesis after each failed approach."
    ),
    "tool_repetition": (
        "Do not repeat the same command hoping for a different result — vary "
        "your approach after 2 repetitions of the same tool signature."
    ),
    "brute_force_escalation": (
        "Do not treat N instances of the same root cause as N separate problems — "
        "cluster issues by shared fix before diving into individual repairs."
    ),
    "consecutive_failures": (
        "Do not ignore layered signals (e.g., file-too-long + too-many-methods + "
        "clustered type errors) that compose into a single structural diagnosis."
    ),
}

_SIGNAL_DISPOSITION_MAP: dict[str, str] = {
    "approach_cycling": (
        "MUST run `constraint_check` before attempting a 3rd approach "
        "(model profile indicates high approach-cycling risk)."
    ),
    "verification_debt": (
        "MUST verify after every 3 edits, not just at the end "
        "(model profile indicates high verification-debt risk)."
    ),
    "premature_action": (
        "MUST read relevant code before any Bash command "
        "(model profile indicates premature-action risk)."
    ),
    "serial_discovery": (
        "MUST use `constraint_check` proactively at session start "
        "(model profile indicates reactive constraint discovery)."
    ),
    "failure_amnesia": (
        "MUST review error signatures from prior attempts before retrying "
        "(model profile indicates failure-amnesia risk)."
    ),
    "stale_model": (
        "MUST update hypothesis model after each failed approach "
        "(model profile indicates stale-model risk)."
    ),
    "tool_repetition": (
        "MUST vary approach after 2 repetitions of the same command "
        "(model profile indicates tool-repetition risk)."
    ),
}


# ── Feature Extraction ──────────────────────────────────────────────

# Keywords that indicate reading/inspection before action
_READ_INDICATORS = frozenset({
    "read", "examine", "look at", "inspect", "check", "review",
    "open", "cat", "grep", "search", "glob", "find",
    "understand", "investigate", "analyze", "study",
})

_VERIFY_INDICATORS = frozenset({
    "test", "pytest", "verify", "run test", "check output",
    "validate", "assert", "confirm", "make sure",
})

_FIX_INDICATORS = frozenset({
    "edit", "fix", "change", "replace", "modify", "update",
    "write", "add", "remove", "delete", "set",
})


def _extract_features_for_task(
    task: ProbeTask,
    response: dict[str, Any],
) -> dict[str, bool]:
    """Extract behavioral features from a structured probe response.

    Scores from action traces first, text second.

    Response schema:
        text: str               # Free-text description of approach
        tool_calls: list[str]   # Optional: ordered list of tool names used
        actions: list[str]      # Optional: ordered list of action descriptions
        retry_count: int        # Optional: how many times same command retried
        verify_points: list[int]  # Optional: after which step numbers verification ran
        constraint_refs: list[str]  # Optional: constraints/errors explicitly referenced

    All fields except 'text' are optional. When present, action trace
    fields take priority over text analysis.
    """
    text = (response.get("text") or "").lower()
    tool_calls = response.get("tool_calls") or []
    actions = response.get("actions") or []
    retry_count = response.get("retry_count")
    verify_points = response.get("verify_points") or []
    constraint_refs = response.get("constraint_refs") or []

    # Normalize tool calls and actions to lowercase
    tool_calls_lower = [t.lower() for t in tool_calls]
    actions_lower = [a.lower() for a in actions]

    features: dict[str, bool] = {}

    # ── Task-generic features (computed from traces + text) ──

    # read_before_edit: Did the model read/inspect before modifying?
    if tool_calls:
        # From trace: first tool call is a read-type tool
        first_read_idx = -1
        first_edit_idx = -1
        for i, tc in enumerate(tool_calls_lower):
            if first_read_idx == -1 and any(
                r in tc for r in ("read", "grep", "glob", "cat", "search", "inspect")
            ):
                first_read_idx = i
            if first_edit_idx == -1 and any(
                e in tc for e in ("edit", "write", "bash", "fix", "modify")
            ):
                first_edit_idx = i
        features["read_before_edit"] = (
            first_read_idx >= 0 and (first_edit_idx == -1 or first_read_idx < first_edit_idx)
        )
    elif actions:
        # From action descriptions
        first_read = next(
            (i for i, a in enumerate(actions_lower)
             if any(r in a for r in _READ_INDICATORS)),
            -1,
        )
        first_fix = next(
            (i for i, a in enumerate(actions_lower)
             if any(f in a for f in _FIX_INDICATORS)),
            -1,
        )
        features["read_before_edit"] = (
            first_read >= 0 and (first_fix == -1 or first_read < first_fix)
        )
    else:
        # From text: check if read-type words precede fix-type words
        read_pos = _first_indicator_pos(text, _READ_INDICATORS)
        fix_pos = _first_indicator_pos(text, _FIX_INDICATORS)
        features["read_before_edit"] = (
            read_pos >= 0 and (fix_pos == -1 or read_pos < fix_pos)
        )

    # exact_retry: Did the model retry the exact same command?
    if retry_count is not None:
        features["exact_retry"] = retry_count >= 1
    elif tool_calls:
        # Check for consecutive identical tool calls
        features["exact_retry"] = any(
            tool_calls_lower[i] == tool_calls_lower[i + 1]
            for i in range(len(tool_calls_lower) - 1)
        )
    else:
        features["exact_retry"] = any(
            phrase in text
            for phrase in ("run the same", "try again", "retry", "same command")
        )

    # minor_variant_only: Did the model only make a small change?
    if actions:
        features["minor_variant_only"] = (
            not features.get("exact_retry", False)
            and any(
                "slight" in a or "minor" in a or "tweak" in a or "adjust flag" in a
                for a in actions_lower
            )
        )
    else:
        features["minor_variant_only"] = (
            not features.get("exact_retry", False)
            and any(
                phrase in text
                for phrase in ("slightly", "minor variation", "tweak", "adjust the flag")
            )
        )

    # mentions_verification / verify timing features
    if verify_points:
        total_steps = len(tool_calls) or len(actions) or 3
        features["verifies_after_each"] = len(verify_points) >= total_steps - 1
        features["verifies_after_some"] = 1 <= len(verify_points) < total_steps - 1
        features["no_verification_mentioned"] = False
        features["batch_all_then_verify"] = (
            len(verify_points) == 1 and verify_points[0] >= total_steps - 1
        )
        features["mentions_verification"] = True
    elif tool_calls:
        verify_calls = [t for t in tool_calls_lower if any(v in t for v in ("test", "pytest", "verify"))]
        features["verifies_after_each"] = len(verify_calls) >= 2
        features["verifies_after_some"] = len(verify_calls) == 1
        features["no_verification_mentioned"] = len(verify_calls) == 0
        features["batch_all_then_verify"] = (
            len(verify_calls) == 1
            and tool_calls_lower.index(verify_calls[0]) == len(tool_calls_lower) - 1
        )
        features["mentions_verification"] = len(verify_calls) > 0
    else:
        verify_mentioned = any(v in text for v in _VERIFY_INDICATORS)
        features["mentions_verification"] = verify_mentioned
        features["verifies_after_each"] = "after each" in text or "between each" in text
        features["verifies_after_some"] = verify_mentioned and not features["verifies_after_each"]
        features["no_verification_mentioned"] = not verify_mentioned
        features["batch_all_then_verify"] = (
            verify_mentioned and ("after all" in text or "at the end" in text)
        )

    # references_previous_attempts / constraint awareness
    if constraint_refs:
        features["references_previous_attempts"] = True
        features["references_both_failures"] = len(constraint_refs) >= 2
        features["addresses_known_constraints"] = True
        features["ignores_previous_errors"] = False
    else:
        ref_count = sum(
            1 for phrase in ("attempt 1", "attempt 2", "previous", "already failed",
                             "first error", "second error", "earlier")
            if phrase in text
        )
        features["references_previous_attempts"] = ref_count >= 1
        features["references_both_failures"] = ref_count >= 2
        features["addresses_known_constraints"] = (
            "constraint" in text or "because" in text and ref_count >= 1
        )
        features["ignores_previous_errors"] = ref_count == 0

    # minor_variant_of_attempt2: third approach is just a tweak of second
    features["minor_variant_of_attempt2"] = (
        not features.get("references_both_failures", False)
        and features.get("minor_variant_only", False)
    )

    # reads_docs_first / reads_config_first / reads_existing_code
    if actions:
        first_action = actions_lower[0] if actions_lower else ""
        features["reads_docs_first"] = any(
            d in first_action for d in ("contributing", "readme", "docs", "guide", "build-guide")
        )
        features["reads_config_first"] = any(
            c in first_action
            for c in ("pyproject", "makefile", "config", "toml", "yaml", "yml", "ci")
        )
        features["reads_existing_code"] = any(
            c in first_action for c in ("src/", "commands/", "existing", "list_cmd", "add_cmd")
        )
    elif tool_calls:
        first_tc = tool_calls_lower[0] if tool_calls_lower else ""
        features["reads_docs_first"] = any(
            d in first_tc for d in ("contributing", "readme", "docs", "guide")
        )
        features["reads_config_first"] = any(
            c in first_tc for c in ("pyproject", "makefile", "config", "toml", "yaml")
        )
        features["reads_existing_code"] = "read" in first_tc or "grep" in first_tc
    else:
        # From text: check what appears first
        features["reads_docs_first"] = any(
            d in text[:200] for d in ("contributing", "readme", "documentation", "guide")
        )
        features["reads_config_first"] = any(
            c in text[:200] for c in ("pyproject", "makefile", "config", "entry point")
        )
        features["reads_existing_code"] = any(
            c in text[:200] for c in ("existing command", "look at", "read the", "examine")
        )

    # jumps_to_fix: Does the model propose a fix in first action?
    if actions:
        features["jumps_to_fix"] = any(
            f in actions_lower[0] for f in _FIX_INDICATORS
        ) if actions_lower else False
    elif tool_calls:
        features["jumps_to_fix"] = any(
            f in tool_calls_lower[0] for f in ("edit", "write", "bash")
        ) if tool_calls_lower else False
    else:
        fix_pos = _first_indicator_pos(text, _FIX_INDICATORS)
        read_pos = _first_indicator_pos(text, _READ_INDICATORS)
        features["jumps_to_fix"] = fix_pos >= 0 and (read_pos == -1 or fix_pos < read_pos)

    # identifies_root_cause: Does the model find the real bug, not the symptom?
    features["identifies_root_cause"] = _check_root_cause_identification(task, response)

    # follows_misleading_error: Does the model fix the symptom line?
    features["follows_misleading_error"] = _check_misleading_error_follow(task, response)

    return features


def _first_indicator_pos(text: str, indicators: frozenset[str]) -> int:
    """Find the earliest position of any indicator phrase in text."""
    earliest = -1
    for indicator in indicators:
        pos = text.find(indicator)
        if pos >= 0 and (earliest == -1 or pos < earliest):
            earliest = pos
    return earliest


def _check_root_cause_identification(task: ProbeTask, response: dict[str, Any]) -> bool:
    """Check if the model identified the root cause for error-reading tasks."""
    if task.id != "t1_error_reading":
        return False

    text = (response.get("text") or "").lower()
    constraint_refs = response.get("constraint_refs") or []
    all_refs = " ".join(constraint_refs).lower() + " " + text

    # Task-specific root cause indicators
    root_cause_terms = {
        "t1_v1": ["shadow", "loop variable", "overwrite", "label", "line 5", "line 4"],
        "t1_v2": ["strip", "whitespace", "leading space", "partition", "line 4", "line 10"],
    }

    # Check against all variants (we don't know which was shown)
    for _variant_id, terms in root_cause_terms.items():
        if any(term in all_refs for term in terms):
            return True
    return False


def _check_misleading_error_follow(task: ProbeTask, response: dict[str, Any]) -> bool:
    """Check if the model followed the misleading error message."""
    if task.id != "t1_error_reading":
        return False

    text = (response.get("text") or "").lower()
    actions = [a.lower() for a in (response.get("actions") or [])]
    all_text = text + " " + " ".join(actions)

    # If they found root cause, they didn't follow the misleading error
    if _check_root_cause_identification(task, response):
        return False

    misleading_terms = {
        "t1_v1": ["line 15", "str.*int", "type error", "typeerror", "type conversion"],
        "t1_v2": ["line 22", "comparison", "string comparison"],
    }

    for _variant_id, terms in misleading_terms.items():
        if any(term in all_text for term in terms):
            return True
    return False


# ── Scoring ─────────────────────────────────────────────────────────


def score_probe_responses(
    responses: dict[str, dict[str, Any]],
) -> tuple[dict[str, float], float]:
    """Score probe task responses into a signal_risk vector.

    Args:
        responses: {task_id: response_dict} where response_dict contains:
            text: str (required)
            tool_calls: list[str] (optional)
            actions: list[str] (optional)
            retry_count: int (optional)
            verify_points: list[int] (optional)
            constraint_refs: list[str] (optional)

    Returns:
        (signal_risk, confidence)
        signal_risk: {signal_name: risk_level} clamped to [0.0, 1.0]
        confidence: 0.0-1.0 based on completeness and trace quality
    """
    signal_risk: dict[str, float] = {}
    scored_tasks = 0
    trace_quality_sum = 0.0
    task_index = {t.id: t for t in PROBE_TASKS}

    for task_id, response in responses.items():
        task = task_index.get(task_id)
        if task is None:
            continue
        if not isinstance(response, dict):
            continue
        if not response.get("text") and not response.get("actions"):
            continue

        scored_tasks += 1

        # Compute trace quality (structured fields boost confidence)
        trace_quality = _compute_trace_quality(response)
        trace_quality_sum += trace_quality

        # Extract features
        features = _extract_features_for_task(task, response)

        # Score features against task's behavioral feature definitions
        for bf in task.features:
            present = features.get(bf.name, False)
            delta = bf.delta_if_present if present else bf.delta_if_absent
            weighted_delta = delta * bf.weight
            signal_risk[bf.signal] = signal_risk.get(bf.signal, 0.0) + weighted_delta

    # Clamp to [0.0, 1.0]
    for signal in signal_risk:
        signal_risk[signal] = max(0.0, min(1.0, signal_risk[signal]))

    # Confidence: completeness * trace quality, capped at PROBE_MAX_CONFIDENCE
    total_tasks = len(PROBE_TASKS)
    if total_tasks == 0 or scored_tasks == 0:
        return signal_risk, 0.0

    completeness = scored_tasks / total_tasks
    avg_trace_quality = trace_quality_sum / scored_tasks

    # Base confidence from completeness, boosted by trace quality
    # 5/5 tasks + all structured traces = 0.60
    # 3/5 tasks + text only = 0.35
    base_confidence = 0.20 + (completeness * 0.25)
    quality_bonus = avg_trace_quality * 0.15
    confidence = min(PROBE_MAX_CONFIDENCE, base_confidence + quality_bonus)

    return signal_risk, round(confidence, 3)


def _compute_trace_quality(response: dict[str, Any]) -> float:
    """Score 0.0-1.0 for how much structured trace data is present.

    More structured data = higher confidence in the scoring.
    Text-only responses get baseline quality.
    """
    score = 0.0
    if response.get("text"):
        score += 0.3
    if response.get("tool_calls"):
        score += 0.25
    if response.get("actions"):
        score += 0.20
    if response.get("retry_count") is not None:
        score += 0.10
    if response.get("verify_points"):
        score += 0.10
    if response.get("constraint_refs"):
        score += 0.05
    return min(1.0, score)


# ── Neutral Prior (fallback for incomplete/failed probes) ───────────

NEUTRAL_PRIOR: dict[str, float] = {
    "approach_cycling": 0.25,
    "failure_amnesia": 0.25,
    "serial_discovery": 0.25,
    "premature_action": 0.25,
    "verification_debt": 0.25,
    "stale_model": 0.25,
    "tool_repetition": 0.20,
    "brute_force_escalation": 0.20,
    "consecutive_failures": 0.15,
}

NEUTRAL_PRIOR_CONFIDENCE = 0.30


def get_neutral_prior() -> tuple[dict[str, float], float]:
    """Return a neutral prior for failed/incomplete probes.

    Never returns high-confidence risky prior. Safe default that
    decays fast as real telemetry arrives.
    """
    return dict(NEUTRAL_PRIOR), NEUTRAL_PRIOR_CONFIDENCE


# ── Public API ──────────────────────────────────────────────────────


def get_probe_tasks(probe_set: str = "quick", seed: int | None = None) -> list[dict[str, Any]]:
    """Return probe tasks formatted for MCP response.

    Selects one variant per task (rotated via seed for anti-gaming).
    Returns list of {id, context, instruction, setup_files} dicts.
    Does NOT include features or target_signals (internal scoring detail).
    """
    normalized = probe_set.strip().lower() if isinstance(probe_set, str) else ""
    if normalized not in SUPPORTED_PROBE_SETS:
        msg = (
            f"Unsupported probe_set: {probe_set!r}. "
            f"Supported: {sorted(SUPPORTED_PROBE_SETS)}"
        )
        raise ValueError(msg)

    # Rotate variants using seed (default: hash of current day for daily rotation)
    if seed is None:
        import time as _time

        day_str = str(int(_time.time() / 86400))
        seed = int(hashlib.sha256(day_str.encode()).hexdigest()[:8], 16)

    rng = random.Random(seed)

    tasks_out = []
    for task in PROBE_TASKS:
        variant = rng.choice(task.variants)
        tasks_out.append({
            "id": task.id,
            "context": variant.context,
            "instruction": variant.instruction,
            "setup_files": variant.setup_files,
            "response_schema": {
                "text": "str (required): Describe your approach",
                "tool_calls": "list[str] (optional): Ordered tool names you would use",
                "actions": "list[str] (optional): Ordered action descriptions",
                "retry_count": "int (optional): How many times you'd retry same command",
                "verify_points": "list[int] (optional): After which step numbers you'd verify",
                "constraint_refs": "list[str] (optional): Errors/constraints you reference",
            },
        })

    return tasks_out


# v1 compat: keep old function name as alias
def get_probe_questions(probe_set: str = "quick") -> list[dict[str, Any]]:
    """DEPRECATED: Use get_probe_tasks() instead.

    Returns v2 tasks in v1-compatible format for backward compatibility.
    """
    return get_probe_tasks(probe_set)


# ── Derived Outputs (unchanged logic) ───────────────────────────────


def _derive_custom_anti_patterns(
    signal_risk: dict[str, float],
    max_items: int = 5,
) -> list[str]:
    """Select anti-patterns prioritized by highest-risk signals."""
    ranked = sorted(signal_risk.items(), key=lambda x: -x[1])
    patterns: list[str] = []
    for signal, risk in ranked:
        if risk < 0.1:
            continue
        if signal in _SIGNAL_ANTI_PATTERN_MAP:
            patterns.append(_SIGNAL_ANTI_PATTERN_MAP[signal])
        if len(patterns) >= max_items:
            break
    return patterns


def _derive_custom_dispositions(
    signal_risk: dict[str, float],
    threshold: float = 0.3,
    max_items: int = 4,
) -> list[str]:
    """Generate guardrail dispositions for high-risk signals."""
    ranked = sorted(signal_risk.items(), key=lambda x: -x[1])
    dispositions: list[str] = []
    for signal, risk in ranked:
        if risk < threshold:
            continue
        if signal in _SIGNAL_DISPOSITION_MAP:
            dispositions.append(_SIGNAL_DISPOSITION_MAP[signal])
        if len(dispositions) >= max_items:
            break
    return dispositions


def build_profile_from_probe(
    model_key: str,
    responses: dict[str, Any],
    *,
    fallback_to_neutral: bool = True,
) -> ModelProfile:
    """Create a ModelProfile from probe task responses.

    If responses are insufficient and fallback_to_neutral is True,
    returns a profile with neutral prior (never high-confidence risky).

    Args:
        model_key: Model identifier (e.g. "claude-opus-4").
        responses: {task_id: response_dict} with structured fields.
        fallback_to_neutral: If True, use neutral prior on failure.
    """
    from .model_profiles import ModelProfile, resolve_model_key

    canonical = resolve_model_key(model_key)
    if canonical is None:
        msg = f"Cannot resolve model key: {model_key!r}"
        raise ValueError(msg)

    # Try scoring the responses
    signal_risk, confidence = score_probe_responses(responses)

    # Fallback: if scoring produced nothing usable, use neutral prior
    if confidence < 0.20 and fallback_to_neutral:
        signal_risk, confidence = get_neutral_prior()

    custom_anti_patterns = _derive_custom_anti_patterns(signal_risk)
    custom_dispositions = _derive_custom_dispositions(signal_risk)

    return ModelProfile(
        model_key=canonical,
        probe_version=PROBE_VERSION,
        probe_runs=1,
        signal_risk=signal_risk,
        confidence=confidence,
        custom_anti_patterns=custom_anti_patterns,
        custom_dispositions=custom_dispositions,
    )


# ── Probe Validity KPI ──────────────────────────────────────────────


def compute_probe_validity(
    probe_signal_risk: dict[str, float],
    observed_signal_fires: dict[str, int],
    event_count: int,
) -> dict[str, Any]:
    """Compute correlation between probe priors and observed signals.

    Called after N sessions to assess whether the probe's initial
    predictions aligned with reality. Returns per-signal deltas
    and an overall correlation score.

    Args:
        probe_signal_risk: The signal_risk from the initial probe.
        observed_signal_fires: Cumulative signal fires across sessions.
        event_count: Total events across sessions.

    Returns:
        {
            "per_signal": {signal: {"probe": float, "observed": float, "delta": float}},
            "mean_absolute_delta": float,  # Lower is better
            "correlation_quality": "good" | "moderate" | "poor",
        }
    """
    if event_count < 20:
        return {
            "per_signal": {},
            "mean_absolute_delta": None,
            "correlation_quality": "insufficient_data",
        }

    per_signal: dict[str, dict[str, float]] = {}
    deltas: list[float] = []

    for signal, probe_risk in probe_signal_risk.items():
        fires = observed_signal_fires.get(signal, 0)
        observed_rate = min(1.0, fires / max(event_count, 1) * 10)
        delta = abs(probe_risk - observed_rate)
        per_signal[signal] = {
            "probe": round(probe_risk, 3),
            "observed": round(observed_rate, 3),
            "delta": round(delta, 3),
        }
        deltas.append(delta)

    mean_delta = sum(deltas) / len(deltas) if deltas else 0.0

    if mean_delta < 0.15:
        quality = "good"
    elif mean_delta < 0.30:
        quality = "moderate"
    else:
        quality = "poor"

    return {
        "per_signal": per_signal,
        "mean_absolute_delta": round(mean_delta, 3),
        "correlation_quality": quality,
    }
