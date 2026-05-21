"""Lightweight within-session ROI tracker.

Tracks tool invocations, failures, retry loops, and findings deltas
within a single agent session.  Produces a structured summary that
can feed automatic retrospective generation.

Pure data — no I/O, no side effects.  The MCP layer is responsible
for calling ``record_*`` methods and saving the final summary to disk.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolCall:
    """Single tool invocation record."""

    tool_name: str
    timestamp: float
    success: bool
    error_message: str = ""


@dataclass
class FindingsSnapshot:
    """Controlplane findings at a point in time."""

    blocking: int = 0
    warnings: int = 0
    informational: int = 0
    timestamp: float = 0.0


@dataclass
class SessionTracker:
    """Accumulates within-session signals for ROI analysis."""

    start_time: float = field(default_factory=time.monotonic)
    tool_calls: list[ToolCall] = field(default_factory=list)
    initial_findings: FindingsSnapshot | None = None
    final_findings: FindingsSnapshot | None = None

    # ── Recording API ────────────────────────────────────────────

    def record_tool_call(
        self, tool_name: str, success: bool, error_message: str = "",
    ) -> None:
        """Record a single tool invocation."""
        self.tool_calls.append(ToolCall(
            tool_name=tool_name,
            timestamp=time.monotonic(),
            success=success,
            error_message=error_message,
        ))

    def record_findings(
        self,
        blocking: int = 0,
        warnings: int = 0,
        informational: int = 0,
    ) -> None:
        """Record a controlplane findings snapshot.

        The first call sets ``initial_findings``; subsequent calls
        update ``final_findings``.
        """
        snap = FindingsSnapshot(
            blocking=blocking,
            warnings=warnings,
            informational=informational,
            timestamp=time.monotonic(),
        )
        if self.initial_findings is None:
            self.initial_findings = snap
        self.final_findings = snap

    # ── Analysis API ─────────────────────────────────────────────

    def tool_counts(self) -> dict[str, int]:
        """Count invocations per tool."""
        counts: dict[str, int] = {}
        for tc in self.tool_calls:
            counts[tc.tool_name] = counts.get(tc.tool_name, 0) + 1
        return counts

    def failure_counts(self) -> dict[str, int]:
        """Count failures per tool."""
        counts: dict[str, int] = {}
        for tc in self.tool_calls:
            if not tc.success:
                counts[tc.tool_name] = counts.get(tc.tool_name, 0) + 1
        return counts

    def retry_loops(self) -> list[dict[str, Any]]:
        """Detect tools that failed 2+ times consecutively (retry loops).

        Returns a list of {tool, consecutive_failures, first_error}.
        """
        loops: list[dict[str, Any]] = []
        if not self.tool_calls:
            return loops

        streak_tool = self.tool_calls[0].tool_name
        streak_count = 0 if self.tool_calls[0].success else 1
        first_error = self.tool_calls[0].error_message if not self.tool_calls[0].success else ""

        for tc in self.tool_calls[1:]:
            if tc.tool_name == streak_tool and not tc.success:
                streak_count += 1
                if not first_error:
                    first_error = tc.error_message
            else:
                if streak_count >= 2:
                    loops.append({
                        "tool": streak_tool,
                        "consecutive_failures": streak_count,
                        "first_error": first_error,
                    })
                streak_tool = tc.tool_name
                streak_count = 0 if tc.success else 1
                first_error = tc.error_message if not tc.success else ""

        if streak_count >= 2:
            loops.append({
                "tool": streak_tool,
                "consecutive_failures": streak_count,
                "first_error": first_error,
            })
        return loops

    def findings_delta(self) -> dict[str, int] | None:
        """Compute findings change from initial to final snapshot.

        Returns None if fewer than 2 snapshots recorded.
        """
        if self.initial_findings is None or self.final_findings is None:
            return None
        if self.initial_findings is self.final_findings:
            return None
        return {
            "blocking_delta": self.final_findings.blocking - self.initial_findings.blocking,
            "warnings_delta": self.final_findings.warnings - self.initial_findings.warnings,
            "informational_delta": self.final_findings.informational - self.initial_findings.informational,
        }

    def summary(self) -> dict[str, Any]:
        """Produce the full session ROI summary."""
        elapsed = time.monotonic() - self.start_time
        total = len(self.tool_calls)
        failures = sum(1 for tc in self.tool_calls if not tc.success)

        result: dict[str, Any] = {
            "elapsed_s": round(elapsed, 1),
            "total_tool_calls": total,
            "total_failures": failures,
            "success_rate": round((total - failures) / total, 3) if total else 0.0,
            "tool_counts": self.tool_counts(),
            "failure_counts": self.failure_counts(),
            "retry_loops": self.retry_loops(),
        }

        if self.initial_findings is not None:
            result["initial_findings"] = {
                "blocking": self.initial_findings.blocking,
                "warnings": self.initial_findings.warnings,
                "informational": self.initial_findings.informational,
            }
        if self.final_findings is not None:
            result["final_findings"] = {
                "blocking": self.final_findings.blocking,
                "warnings": self.final_findings.warnings,
                "informational": self.final_findings.informational,
            }

        delta = self.findings_delta()
        if delta is not None:
            result["findings_delta"] = delta

        return result
