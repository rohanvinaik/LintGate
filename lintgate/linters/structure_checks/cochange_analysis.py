"""F: Co-change coupling analysis — git log-based file co-change patterns.

Analyzes git history to detect which files tend to change together.
Used to **annotate** split proposals from cohesion analysis, NOT as
standalone findings.

Heuristics:
- "structurally separable, historically low co-change" → high confidence split
- "structurally separable, but historically coupled" → annotate with caution

No LLM calls. Deterministic from git log output.
"""

from __future__ import annotations

import contextlib
import os
import subprocess
from collections import Counter
from dataclasses import dataclass, field


@dataclass
class CoChangePair:
    """Two files that frequently change together."""

    file_a: str
    file_b: str
    cochange_count: int
    total_commits_a: int
    total_commits_b: int

    @property
    def coupling_strength(self) -> float:
        """Jaccard-like coupling: co-changes / union of changes."""
        union = self.total_commits_a + self.total_commits_b - self.cochange_count
        return self.cochange_count / union if union > 0 else 0.0


@dataclass
class CoChangeCoupling:
    """Result of co-change coupling analysis for a project."""

    pairs: list[CoChangePair] = field(default_factory=list)
    file_commit_counts: dict[str, int] = field(default_factory=dict)
    total_commits_analyzed: int = 0

    def coupling_for(self, file_a: str, file_b: str) -> float:
        """Get coupling strength between two specific files."""
        for pair in self.pairs:
            if (pair.file_a == file_a and pair.file_b == file_b) or (
                pair.file_a == file_b and pair.file_b == file_a
            ):
                return pair.coupling_strength
        return 0.0

    def top_coupled_with(self, filepath: str, limit: int = 5) -> list[CoChangePair]:
        """Get top co-change partners for a given file."""
        matches = [p for p in self.pairs if p.file_a == filepath or p.file_b == filepath]
        return sorted(matches, key=lambda p: p.cochange_count, reverse=True)[:limit]


def compute_cochange_coupling(
    project_root: str,
    days: int = 30,
    min_cochanges: int = 3,
) -> CoChangeCoupling:
    """Analyze git log for file co-change patterns.

    Args:
        project_root: Project root path (must be a git repo).
        days: Number of days of history to analyze.
        min_cochanges: Minimum co-change count to include a pair.

    Returns:
        CoChangeCoupling with co-change pairs and per-file commit counts.
    """
    # Get git log: one commit per line, files changed per commit
    try:
        result = subprocess.run(
            [
                "git",
                "log",
                f"--since={days} days ago",
                "--name-only",
                "--pretty=format:COMMIT",
                "--diff-filter=ACMR",  # Added, Copied, Modified, Renamed
            ],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return CoChangeCoupling()

    if result.returncode != 0:
        return CoChangeCoupling()

    # Parse git log output into per-commit file lists
    commits = _parse_git_log(result.stdout, project_root)

    # Count per-file commits
    file_commit_counts: Counter[str] = Counter()
    for files in commits:
        for f in files:
            file_commit_counts[f] += 1

    # Count co-changes: for each commit, all file pairs co-changed
    cochange_counts: Counter[tuple[str, str]] = Counter()
    for files in commits:
        py_files = sorted(f for f in files if f.endswith(".py"))
        for i, a in enumerate(py_files):
            for b in py_files[i + 1 :]:
                cochange_counts[(a, b)] += 1

    # Build pairs above threshold
    pairs = []
    for (file_a, file_b), count in cochange_counts.items():
        if count >= min_cochanges:
            pairs.append(
                CoChangePair(
                    file_a=file_a,
                    file_b=file_b,
                    cochange_count=count,
                    total_commits_a=file_commit_counts[file_a],
                    total_commits_b=file_commit_counts[file_b],
                )
            )

    pairs.sort(key=lambda p: p.cochange_count, reverse=True)

    return CoChangeCoupling(
        pairs=pairs,
        file_commit_counts=dict(file_commit_counts),
        total_commits_analyzed=len(commits),
    )


def annotate_split_proposals(
    split_proposals: list[dict],
    cochange: CoChangeCoupling,
    filepath: str,
) -> list[dict]:
    """Annotate split proposals with co-change coupling data.

    For each split proposal, check if the proposed destination files
    are historically coupled with the source file. High coupling
    suggests caution; low coupling corroborates the split.

    Args:
        split_proposals: List of split proposal dicts (from cohesion_analysis).
        cochange: CoChangeCoupling data from compute_cochange_coupling().
        filepath: The file being analyzed for splitting.

    Returns:
        Annotated split proposals with co-change evidence.
    """
    if not split_proposals:
        return split_proposals

    top_coupled = cochange.top_coupled_with(filepath, limit=10)

    for proposal in split_proposals:
        # Check if any of the proposed split targets are historically coupled
        coupled_files = [
            {
                "file": p.file_b if p.file_a == filepath else p.file_a,
                "cochange_count": p.cochange_count,
                "coupling_strength": round(p.coupling_strength, 3),
            }
            for p in top_coupled
        ]

        if coupled_files:
            coupling_values = [
                float(c["coupling_strength"])  # type: ignore[arg-type]
                for c in coupled_files
            ]
            max_coupling = max(coupling_values)

            if max_coupling > 0.6:
                proposal["cochange_annotation"] = {
                    "status": "caution",
                    "reason": (
                        "Structurally separable but historically coupled. "
                        "Split may increase cross-file change frequency."
                    ),
                    "coupled_files": coupled_files[:5],
                    "max_coupling_strength": max_coupling,
                }
            elif max_coupling < 0.2:  # noqa: PLR2004
                proposal["cochange_annotation"] = {
                    "status": "corroborated",
                    "reason": (
                        "Structurally separable and historically independent. "
                        "Split is well-supported by change history."
                    ),
                    "max_coupling_strength": max_coupling,
                }
            else:
                proposal["cochange_annotation"] = {
                    "status": "neutral",
                    "reason": "Moderate historical coupling — split may be fine.",
                    "coupled_files": coupled_files[:3],
                    "max_coupling_strength": max_coupling,
                }
        else:
            proposal["cochange_annotation"] = {
                "status": "no_data",
                "reason": "No co-change data available for this file.",
            }

    return split_proposals


# ── Internal helpers ─────────────────────────────────────────────────────


def _parse_git_log(stdout: str, project_root: str) -> list[set[str]]:
    """Parse git log --name-only output into per-commit file sets."""
    commits: list[set[str]] = []
    current_files: set[str] = set()

    for line in stdout.splitlines():
        line = line.strip()
        if line == "COMMIT":
            if current_files:
                commits.append(current_files)
            current_files = set()
        elif line:
            # Normalize to relative path
            if os.path.isabs(line):
                with contextlib.suppress(ValueError):
                    line = os.path.relpath(line, project_root)
            current_files.add(line)

    # Don't forget the last commit
    if current_files:
        commits.append(current_files)

    return commits
