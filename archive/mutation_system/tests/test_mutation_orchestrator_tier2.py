"""Tests for Tier 2 mutation orchestrator queue, guards, and channel integration."""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock, patch

from lintgate.mutation.automation import (
    MutationOrchestrator,
    Tier2Priority,
    Tier2QueueItem,
)

# ---------------------------------------------------------------------------
# Helpers — bypass singleton, same pattern as test_mutation_source_protection.py
# ---------------------------------------------------------------------------


def _make_orchestrator() -> MutationOrchestrator:
    """Create a bare orchestrator without starting the worker thread."""
    orch = MutationOrchestrator.__new__(MutationOrchestrator)
    orch._lock = threading.Lock()
    # Tier 1 state
    orch._queued_files = set()
    orch._last_run = {}
    orch._debounce_seconds = 30.0
    orch._project_root = None
    # Tier 2 state
    orch._tier2_queue = []
    orch._tier2_in_progress = None
    orch._tier2_session_count = 0
    orch._tier2_max_per_session = 3
    orch._tier2_timeout_s = 300
    orch._tier2_debounce_seconds = 120.0
    orch._tier2_last_run = {}
    return orch


# ---------------------------------------------------------------------------
# Priority ordering
# ---------------------------------------------------------------------------


class TestPriorityOrdering:
    def test_priority_ordering(self):
        """Queue items sort by (priority, time) — lowest priority value first."""
        t = time.time()
        no_data = Tier2QueueItem(Tier2Priority.NO_DATA, t + 1, "a.py")
        gap = Tier2QueueItem(Tier2Priority.CONFIRMED_GAP, t, "b.py")
        stale = Tier2QueueItem(Tier2Priority.STALE, t, "c.py")
        refresh = Tier2QueueItem(Tier2Priority.REFRESH, t, "d.py")

        items = sorted([refresh, stale, gap, no_data])
        assert [i.priority for i in items] == [
            Tier2Priority.NO_DATA,
            Tier2Priority.CONFIRMED_GAP,
            Tier2Priority.STALE,
            Tier2Priority.REFRESH,
        ]

    def test_fifo_within_priority(self):
        """Same-priority items maintain FIFO by enqueue_time."""
        t = time.time()
        first = Tier2QueueItem(Tier2Priority.STALE, t, "a.py")
        second = Tier2QueueItem(Tier2Priority.STALE, t + 1, "b.py")

        items = sorted([second, first])
        assert items[0].file_path == "a.py"
        assert items[1].file_path == "b.py"


# ---------------------------------------------------------------------------
# enqueue_tier2 guards
# ---------------------------------------------------------------------------


class TestEnqueueTier2Guards:
    def test_rejects_mutant_path(self):
        orch = _make_orchestrator()
        result = orch.enqueue_tier2("mutants/lintgate/foo.py", Tier2Priority.NO_DATA)
        assert result is False
        assert len(orch._tier2_queue) == 0

    def test_respects_session_cap(self, tmp_path):
        orch = _make_orchestrator()
        orch._tier2_max_per_session = 2
        orch._tier2_session_count = 2

        f = tmp_path / "a.py"
        f.write_text("x = 1")
        result = orch.enqueue_tier2(str(f), Tier2Priority.NO_DATA)
        assert result is False

    def test_debounces_recent(self, tmp_path):
        orch = _make_orchestrator()
        f = tmp_path / "a.py"
        f.write_text("x = 1")
        path = str(f)

        orch._tier2_last_run[path] = time.time()  # Just ran
        result = orch.enqueue_tier2(path, Tier2Priority.STALE)
        assert result is False

    def test_deduplicates_in_queue(self, tmp_path):
        orch = _make_orchestrator()
        f = tmp_path / "a.py"
        f.write_text("x = 1")
        path = str(f)

        assert orch.enqueue_tier2(path, Tier2Priority.NO_DATA) is True
        assert orch.enqueue_tier2(path, Tier2Priority.CONFIRMED_GAP) is False
        assert len(orch._tier2_queue) == 1

    def test_deduplicates_in_progress(self, tmp_path):
        orch = _make_orchestrator()
        f = tmp_path / "a.py"
        f.write_text("x = 1")
        path = str(f)

        orch._tier2_in_progress = path
        result = orch.enqueue_tier2(path, Tier2Priority.NO_DATA)
        assert result is False

    def test_rejects_nonexistent_file(self):
        orch = _make_orchestrator()
        result = orch.enqueue_tier2("/nonexistent/file.py", Tier2Priority.NO_DATA)
        assert result is False

    def test_accepts_valid_enqueue(self, tmp_path):
        orch = _make_orchestrator()
        f = tmp_path / "a.py"
        f.write_text("x = 1")
        path = str(f)

        result = orch.enqueue_tier2(
            path, Tier2Priority.CONFIRMED_GAP, reason="survival gaps"
        )
        assert result is True
        assert len(orch._tier2_queue) == 1
        assert orch._tier2_queue[0].file_path == path
        assert orch._tier2_queue[0].priority == Tier2Priority.CONFIRMED_GAP


# ---------------------------------------------------------------------------
# configure_tier2
# ---------------------------------------------------------------------------


class TestConfigureTier2:
    def test_configure_updates_params(self):
        orch = _make_orchestrator()
        orch.configure_tier2(max_per_session=10, timeout_s=600, debounce_s=60.0)
        assert orch._tier2_max_per_session == 10
        assert orch._tier2_timeout_s == 600
        assert orch._tier2_debounce_seconds == 60.0

    def test_configure_partial_update(self):
        orch = _make_orchestrator()
        orch.configure_tier2(max_per_session=5)
        assert orch._tier2_max_per_session == 5
        # Others unchanged
        assert orch._tier2_timeout_s == 300
        assert orch._tier2_debounce_seconds == 120.0


# ---------------------------------------------------------------------------
# Channel integration — _maybe_enqueue_tier2
# ---------------------------------------------------------------------------


class TestChannelEnqueueTier2:
    def _make_config(self, *, tier2_auto_schedule: bool = False, **extra):
        """Build a minimal ControlPlaneConfig with mutation channel settings."""
        from lintgate.controlplane.types import ChannelConfig, ControlPlaneConfig

        settings = {"tier2_auto_schedule": tier2_auto_schedule, **extra}
        ch = ChannelConfig(settings=settings)
        return ControlPlaneConfig(channels={"mutation": ch})

    def _make_event(self, project_root: str = "/project"):
        from lintgate.controlplane.types import SupervisionEvent

        return SupervisionEvent(
            tool_name="Edit",
            project_root=project_root,
        )

    def test_channel_disabled_by_default(self):
        """_maybe_enqueue_tier2 is a no-op without tier2_auto_schedule."""
        from lintgate.channels.mutation_channel import MutationChannel
        from lintgate.types import LintIssue

        channel = MutationChannel()
        config = self._make_config(tier2_auto_schedule=False)
        event = self._make_event()

        finding = LintIssue(
            linter="mutation",
            kind="MUTCH008",
            message="test",
            file="a.py",
            severity="informational",
        )

        with patch(
            "lintgate.mutation.automation.global_orchestrator"
        ) as mock_orch:
            channel._maybe_enqueue_tier2([finding], event, config)
            mock_orch.enqueue_tier2.assert_not_called()

    def test_channel_enqueues_on_mutch008(self, tmp_path):
        """MUTCH008 findings trigger NO_DATA priority enqueue."""
        from lintgate.channels.mutation_channel import MutationChannel
        from lintgate.types import LintIssue

        channel = MutationChannel()
        config = self._make_config(tier2_auto_schedule=True)
        event = self._make_event()

        f = tmp_path / "a.py"
        f.write_text("x = 1")

        finding = LintIssue(
            linter="mutation",
            kind="MUTCH008",
            message="no data",
            file=str(f),
            severity="informational",
        )

        with patch(
            "lintgate.mutation.automation.global_orchestrator"
        ) as mock_orch:
            mock_orch.configure_tier2 = MagicMock()
            mock_orch.enqueue_tier2 = MagicMock(return_value=True)
            channel._maybe_enqueue_tier2([finding], event, config)

            mock_orch.enqueue_tier2.assert_called_once_with(
                file_path=str(f),
                priority=Tier2Priority.NO_DATA,
                reason="pure function with no mutation data",
                project_root="/project",
            )

    def test_channel_enqueues_on_mut003(self, tmp_path):
        """MUT003 findings trigger CONFIRMED_GAP priority enqueue."""
        from lintgate.channels.mutation_channel import MutationChannel
        from lintgate.types import LintIssue

        channel = MutationChannel()
        config = self._make_config(tier2_auto_schedule=True)
        event = self._make_event()

        f = tmp_path / "b.py"
        f.write_text("x = 1")

        finding = LintIssue(
            linter="mutation",
            kind="MUT003",
            message="needs full profiling",
            file=str(f),
            severity="informational",
        )

        with patch(
            "lintgate.mutation.automation.global_orchestrator"
        ) as mock_orch:
            mock_orch.configure_tier2 = MagicMock()
            mock_orch.enqueue_tier2 = MagicMock(return_value=True)
            channel._maybe_enqueue_tier2([finding], event, config)

            mock_orch.enqueue_tier2.assert_called_once_with(
                file_path=str(f),
                priority=Tier2Priority.CONFIRMED_GAP,
                reason="sampled data shows survival gaps",
                project_root="/project",
            )

    def test_channel_keeps_highest_priority_per_file(self, tmp_path):
        """Multiple findings on same file keep the highest (lowest enum) priority."""
        from lintgate.channels.mutation_channel import MutationChannel
        from lintgate.types import LintIssue

        channel = MutationChannel()
        config = self._make_config(tier2_auto_schedule=True)
        event = self._make_event()

        f = tmp_path / "c.py"
        f.write_text("x = 1")
        path = str(f)

        findings = [
            LintIssue(
                linter="mutation",
                kind="MUTCH005",
                message="stale",
                file=path,
                severity="informational",
            ),
            LintIssue(
                linter="mutation",
                kind="MUTCH008",
                message="no data",
                file=path,
                severity="informational",
            ),
        ]

        with patch(
            "lintgate.mutation.automation.global_orchestrator"
        ) as mock_orch:
            mock_orch.configure_tier2 = MagicMock()
            mock_orch.enqueue_tier2 = MagicMock(return_value=True)
            channel._maybe_enqueue_tier2(findings, event, config)

            # Should enqueue once with NO_DATA (0), not STALE (2)
            mock_orch.enqueue_tier2.assert_called_once()
            call_kwargs = mock_orch.enqueue_tier2.call_args[1]
            assert call_kwargs["priority"] == Tier2Priority.NO_DATA
