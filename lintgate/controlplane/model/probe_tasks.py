"""Probe task bank and signal mapping for model calibration.

Contains data structures, the 5 probe tasks with variants,
and signal-to-anti-pattern/disposition maps.

Extracted from model_probe.py for module size compliance.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# ── Data Structures ─────────────────────────────────────────────────


@dataclass
class TaskVariant:
    """One surface-level variant of a probe task."""

    variant_id: str
    context: str
    instruction: str
    setup_files: dict[str, str] = field(default_factory=dict)


@dataclass
class BehavioralFeature:
    """An extracted feature from model response, used for scoring."""

    name: str
    signal: str
    delta_if_present: float
    delta_if_absent: float
    weight: float = 1.0


@dataclass
class ProbeTask:
    """A behavioral probe task with variants and scoring features."""

    id: str
    target_signals: list[str]
    description: str
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


# ── Signal-to-Anti-Pattern Mapping ──────────────────────────────────

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
