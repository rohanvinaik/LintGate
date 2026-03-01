"""Channel protocol for ControlPlane supervision mesh.

Each supervision channel is an independent, orthogonal measurement dimension.
A channel:
- Decides whether to run based on the event profile (should_run)
- Executes its analysis and returns structured findings (execute)
- Reports its findings through the common ChannelResult type

Design: Modeled after lintgate.linters.base.Linter protocol, but at a
higher level of abstraction. Each channel may internally use multiple
linters, test runners, or analysis tools.

The channel protocol enables:
- Parallel execution (channels are independent)
- Orthogonal measurement (each channel measures one dimension)
- Silent = informative (a channel returning "pass" provides confident exclusion)
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from .types import ChannelResult, ControlPlaneConfig, SupervisionEvent


@runtime_checkable
class Channel(Protocol):
    """Protocol for supervision channels.

    Each channel is an independent measurement dimension in the
    supervision mesh. Channels run in parallel and their combined
    signals are analyzed by the coherence engine.

    Attributes:
        name: Unique channel identifier (e.g., "lint", "tests", "deps", "git")
        timeout_ms: Default per-channel timeout in milliseconds
        blocking_capable: Whether this channel CAN produce blocking findings
            (actual blocking behavior is controlled by config)
    """

    name: str
    timeout_ms: int
    blocking_capable: bool

    def should_run(self, event: SupervisionEvent, config: ControlPlaneConfig) -> bool:
        """Decide whether this channel should activate for this event.

        Quick check — must be fast (<1ms). Used to skip channels that
        are irrelevant to the current event (e.g., dep channel doesn't
        run on logic-only edits).
        """
        ...

    def execute(
        self, event: SupervisionEvent, config: ControlPlaneConfig
    ) -> ChannelResult:
        """Run the channel's analysis and return structured results.

        This is the main analysis method. It should:
        1. Perform domain-specific checks
        2. Collect findings as LintIssue objects
        3. Propose RepairAction objects for fixable issues
        4. Return a ChannelResult with status, severity, findings, repairs

        Must handle its own errors gracefully — return ChannelResult
        with status="error" rather than raising.
        """
        ...
