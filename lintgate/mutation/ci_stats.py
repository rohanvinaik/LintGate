"""Mutation CI stats — single source of truth for parsing mutmut output.

Used by both the CI workflow (via scripts/parse_mutation_stats.py) and
MCP tools (analyze_test_strength, inspect_test_assertions).
"""

from __future__ import annotations

import ast
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class MutationCIStats:
    """Parsed mutation testing statistics from a mutmut CI run."""

    killed: int = 0
    survived: int = 0
    timeout: int = 0
    suspicious: int = 0
    no_tests: int = 0
    skipped: int = 0
    equivalent_suspect: int = 0
    skipped_equivalent_policy: int = 0
    effective_total_for_score: int = 0
    total: int = 0
    score: float = 0.0
    run_state: str = "missing"  # "valid" | "invalid" | "missing"
    source: str = "missing"  # "local_file" | "ci_artifact" | "missing"

    @classmethod
    def from_json_path(cls, path: str, source: str = "local_file") -> MutationCIStats:
        """Load stats from a mutmut-cicd-stats.json file.

        Args:
            path: Filesystem path to mutmut-cicd-stats.json.
            source: Provenance tag — "local_file" for dev, "ci_artifact" for CI.
        """
        if not os.path.exists(path):
            return cls.missing()

        try:
            raw = json.loads(Path(path).read_text())
        except (json.JSONDecodeError, OSError):
            return cls(run_state="invalid", source=source)

        killed = raw.get("killed", 0)
        survived = raw.get("survived", 0)
        timeout = raw.get("timeout", 0)
        suspicious = raw.get("suspicious", 0)
        no_tests = raw.get("no_tests", 0)
        skipped = raw.get("skipped", 0)
        equivalent_suspect = raw.get("equivalent_suspect", 0)
        skipped_equivalent_policy = raw.get("skipped_equivalent_policy", 0)

        # Prefer mutmut's own total when present; fall back to computed sum.
        computed_sum = (
            killed
            + survived
            + timeout
            + suspicious
            + no_tests
            + skipped
            + equivalent_suspect
        )
        total = raw.get("total", computed_sum)

        effective_total = total - equivalent_suspect - skipped_equivalent_policy
        effective_total_for_score = raw.get(
            "effective_total_for_score", effective_total if effective_total > 0 else 0
        )

        if total == 0:
            return cls(
                killed=killed,
                survived=survived,
                timeout=timeout,
                suspicious=suspicious,
                no_tests=no_tests,
                skipped=skipped,
                equivalent_suspect=equivalent_suspect,
                skipped_equivalent_policy=skipped_equivalent_policy,
                effective_total_for_score=effective_total_for_score,
                total=0,
                score=0.0,
                run_state="invalid",
                source=source,
            )

        score = (
            round(killed / effective_total_for_score * 100, 1)
            if effective_total_for_score > 0
            else 0.0
        )

        return cls(
            killed=killed,
            survived=survived,
            timeout=timeout,
            suspicious=suspicious,
            no_tests=no_tests,
            skipped=skipped,
            equivalent_suspect=equivalent_suspect,
            skipped_equivalent_policy=skipped_equivalent_policy,
            effective_total_for_score=effective_total_for_score,
            total=total,
            score=score,
            run_state="valid",
            source=source,
        )

    @classmethod
    def missing(cls) -> MutationCIStats:
        """Factory for when no stats file exists."""
        return cls(run_state="missing", source="missing")

    def is_valid(self) -> bool:
        """True when total > 0 and run_state == 'valid'."""
        return self.run_state == "valid" and self.total > 0

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a dict suitable for JSON output."""
        return asdict(self)


def compute_badge_color(score: float) -> str:
    """Map mutation score to badge color.

    >=80 → brightgreen, >=60 → yellow, else → red.
    """
    if score >= 80:
        return "brightgreen"
    elif score >= 60:
        return "yellow"
    return "red"


def parse_stats_for_ci(stats_path: str, github_output: str) -> int:
    """CI entry point: parse stats, write GITHUB_OUTPUT vars, return exit code.

    Returns 0 on valid run, 1 on invalid/missing/zero-total.

    Args:
        stats_path: Path to mutmut-cicd-stats.json.
        github_output: Path to $GITHUB_OUTPUT file (empty string = skip writing).
    """
    stats = MutationCIStats.from_json_path(stats_path, source="ci_artifact")

    if stats.run_state == "missing":
        print("::error::No mutmut stats found — failing workflow for integrity")
        _write_github_output(
            github_output, {"skip": "true", "mutation_integrity": "fail"}
        )
        return 1  # Integrity lock: missing stats fails the pipeline

    if not stats.is_valid():
        print(
            f"::error::Mutation stats invalid — total={stats.total}, run_state={stats.run_state}"
        )
        _write_github_output(
            github_output, {"skip": "true", "mutation_integrity": "fail"}
        )
        return 1

    color = compute_badge_color(stats.score)

    _write_github_output(
        github_output,
        {
            "skip": "false",
            "score": str(stats.score),
            "color": color,
            "killed": str(stats.killed),
            "survived": str(stats.survived),
            "total": str(stats.total),
            "mutation_integrity": "pass",
            "mutation_quality": str(stats.score),
        },
    )

    print("## Results")
    print()
    print(f"Mutation score: {stats.score}%")
    print(f"  Killed (detected):   {stats.killed}")
    print(f"  Survived (missed):   {stats.survived}")
    print(f"  Timeout:             {stats.timeout}")
    print(f"  Suspicious:          {stats.suspicious}")
    print(f"  No tests:            {stats.no_tests}")
    print(f"  Skipped:             {stats.skipped}")
    if stats.equivalent_suspect > 0 or stats.skipped_equivalent_policy > 0:
        print(f"  Equivalent (suspect): {stats.equivalent_suspect}")
        print(f"  Skipped (policy):    {stats.skipped_equivalent_policy}")
        print(f"  Effective Total:     {stats.effective_total_for_score}")
    print(f"  Total mutants:       {stats.total}")

    return 0


def _write_github_output(github_output: str, pairs: dict[str, str]) -> None:
    """Append key=value pairs to the $GITHUB_OUTPUT file."""
    if not github_output:
        return
    with open(github_output, "a") as fh:
        for key, value in pairs.items():
            fh.write(f"{key}={value}\n")


def load_mutation_hotspots(survivors_path: str) -> list[dict[str, Any]]:
    """Load survivor detail from a mutmut survivors export.

    Expects a JSON file with a list of dicts or a newline-delimited text format
    from ``mutmut results --all true``. Returns a list of hotspot dicts with
    keys: file, line, function, operator (all optional except file).

    Returns an empty list when the file is missing or unparseable.
    """
    if not os.path.exists(survivors_path):
        return []

    try:
        text = Path(survivors_path).read_text().strip()
    except OSError:
        return []

    if not text:
        return []

    # Try JSON format first
    try:
        data = json.loads(text)
        if isinstance(data, list):
            return [
                _normalize_hotspot(entry) for entry in data if isinstance(entry, dict)
            ]
        if isinstance(data, dict) and "mutants" in data:
            return [
                _normalize_hotspot(m) for m in data["mutants"] if isinstance(m, dict)
            ]
    except json.JSONDecodeError:
        pass

    # Fall back to line-based parsing (mutmut results output)
    hotspots: list[dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        hotspot = _parse_survivor_line(line)
        if hotspot:
            hotspots.append(hotspot)

    _enrich_function_names(survivors_path, hotspots)

    return hotspots


def _normalize_hotspot(entry: dict[str, Any]) -> dict[str, Any]:
    """Normalize a raw hotspot dict to the canonical schema."""
    return {
        "run_id": entry.get("run_id", "default"),
        "file": entry.get("file", entry.get("filename", "")),
        "line": entry.get("line", entry.get("lineno", 0)),
        "function": entry.get("function", entry.get("func", "")),
        "operator": entry.get("operator", entry.get("mutation_type", "")),
        "status": entry.get("status", "survived"),
        "category": entry.get("category", "unknown"),
        "test_ids": entry.get("test_ids", []),
        "mutation_id": str(entry.get("mutation_id", entry.get("id", ""))),
        "confidence": entry.get("confidence", "low"),
    }


def _parse_survivor_line(line: str) -> dict[str, Any] | None:
    """Parse a single mutmut results line into a hotspot dict.

    Expected format: ``mutant_id  file:line  status``
    """
    parts = line.split()
    if len(parts) < 2:
        return None

    mutation_id = parts[0]

    # Second token is typically file:line
    location = parts[1] if len(parts) >= 2 else ""
    if ":" in location:
        file_part, _, line_part = location.rpartition(":")
        try:
            line_num = int(line_part)
        except ValueError:
            file_part = location
            line_num = 0
    else:
        file_part = location
        line_num = 0

    status = parts[2] if len(parts) >= 3 else "survived"

    return {
        "run_id": "default",
        "file": file_part,
        "line": line_num,
        "function": "",
        "operator": "",
        "status": status,
        "category": "unknown",
        "mutation_id": mutation_id,
        "test_ids": [],
        "confidence": "low",
    }


def _resolve_project_root(survivors_path: str) -> Path:
    """Resolve project root from the survivors file path.

    Assumes survivors file lives in a ``mutants/`` subdirectory of the project.
    Falls back to the parent directory for temporary/test directories.
    """
    try:
        candidate = Path(survivors_path).parent.parent
        if (candidate / ".git").exists() or (candidate / "src").exists():
            return candidate
        return Path(survivors_path).parent
    except Exception:
        return Path(survivors_path).parent


def _build_function_ranges(file_path: Path) -> list[tuple[int, int, str]]:
    """Parse *file_path* and return ``(start, end, name)`` for each function."""
    source = file_path.read_text("utf-8")
    tree = ast.parse(source, filename=str(file_path))
    return [
        (node.lineno, node.end_lineno or node.lineno, node.name)
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]


def _assign_function_names(
    items: list[dict[str, Any]], funcs: list[tuple[int, int, str]]
) -> None:
    """Set ``item["function"]`` for each hotspot whose line falls inside a function range."""
    for item in items:
        line = item["line"]
        for start, end, name in funcs:
            if start <= line <= end:
                item["function"] = name
                break


def _group_hotspots_by_file(
    hotspots: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """Group hotspots that need function-name enrichment by their file path."""
    file_hotspots: dict[str, list[dict[str, Any]]] = {}
    for h in hotspots:
        if not h.get("function") and h.get("file") and h.get("line"):
            file_hotspots.setdefault(h["file"], []).append(h)
    return file_hotspots


def _enrich_function_names(survivors_path: str, hotspots: list[dict[str, Any]]) -> None:
    """Enrich hotspots with function names by mapping lines to AST nodes."""
    project_root = _resolve_project_root(survivors_path)
    file_hotspots = _group_hotspots_by_file(hotspots)

    for rel_path, items in file_hotspots.items():
        abs_path = project_root / rel_path
        if not abs_path.exists():
            continue
        try:
            funcs = _build_function_ranges(abs_path)
        except (OSError, SyntaxError):
            continue
        _assign_function_names(items, funcs)
