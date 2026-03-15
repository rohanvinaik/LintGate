"""Mutation-targeted tests for delta.py zero-kill functions."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from lintgate.controlplane.reporter.delta import (
    _apply_resurface_cadence,
    _build_delta_quota,
    _select_by_quota,
    compute_finding_fingerprint,
    filter_display_findings,
)
from lintgate.controlplane.types import ChannelResult, MeshResult


@dataclass
class FakeFinding:
    kind: str = "E501"
    message: str = "line too long"
    severity: str = "warning"
    file: str | None = "src/core.py"
    line: int | None = 10
    evidence: dict[str, Any] = field(default_factory=dict)

    def short_location(self) -> str:
        return self.file or ""


# ── _build_delta_quota ───────────────────────────────────────────────


class TestBuildDeltaQuota:
    def test_builds_quota_from_new(self):
        delta = {"new": [{"fingerprint": "fp1", "count": 3}]}
        quota = _build_delta_quota(delta)
        assert quota["fp1"] == 3

    def test_builds_quota_from_escalated(self):
        delta = {"escalated": [{"fingerprint": "fp2", "count": 2}]}
        quota = _build_delta_quota(delta)
        assert quota["fp2"] == 2

    def test_combines_new_and_escalated(self):
        delta = {
            "new": [{"fingerprint": "fp1", "count": 2}],
            "escalated": [{"fingerprint": "fp1", "count": 1}],
        }
        quota = _build_delta_quota(delta)
        assert quota["fp1"] == 3

    def test_empty_delta(self):
        quota = _build_delta_quota({})
        assert quota == {}

    def test_skips_empty_fingerprint(self):
        delta = {"new": [{"fingerprint": "", "count": 1}]}
        quota = _build_delta_quota(delta)
        assert quota == {}

    def test_min_count_is_one(self):
        """count=0 in delta still produces quota of 1 (max(1, 0))."""
        delta = {"new": [{"fingerprint": "fp1", "count": 0}]}
        quota = _build_delta_quota(delta)
        assert quota["fp1"] == 1


# ── _apply_resurface_cadence ─────────────────────────────────────────


class TestApplyResurfaceCadence:
    def test_resurfaces_on_10th_snapshot(self):
        quota: dict[str, int] = {}
        current = {"fp1": {"severity": "blocking"}}
        previous = {"fp1": {"severity": "blocking"}}
        count = _apply_resurface_cadence(quota, current, previous, 10)
        assert count == 1
        assert quota["fp1"] == 1

    def test_no_resurface_on_non_10th(self):
        quota: dict[str, int] = {}
        current = {"fp1": {"severity": "blocking"}}
        previous = {"fp1": {"severity": "blocking"}}
        count = _apply_resurface_cadence(quota, current, previous, 7)
        assert count == 0
        assert quota == {}

    def test_no_resurface_when_zero_snapshots(self):
        quota: dict[str, int] = {}
        count = _apply_resurface_cadence(quota, {}, {}, 0)
        assert count == 0

    def test_no_resurface_without_previous_index(self):
        quota: dict[str, int] = {}
        current = {"fp1": {"severity": "blocking"}}
        count = _apply_resurface_cadence(quota, current, None, 10)
        assert count == 0

    def test_skips_non_blocking(self):
        quota: dict[str, int] = {}
        current = {"fp1": {"severity": "warning"}}
        previous = {"fp1": {"severity": "warning"}}
        count = _apply_resurface_cadence(quota, current, previous, 10)
        assert count == 0

    def test_skips_already_in_quota(self):
        quota: dict[str, int] = {"fp1": 2}
        current = {"fp1": {"severity": "blocking"}}
        previous = {"fp1": {"severity": "blocking"}}
        count = _apply_resurface_cadence(quota, current, previous, 10)
        assert count == 0

    def test_skips_new_findings_not_in_previous(self):
        quota: dict[str, int] = {}
        current = {"fp1": {"severity": "blocking"}}
        previous: dict[str, dict[str, Any]] = {}
        count = _apply_resurface_cadence(quota, current, previous, 10)
        assert count == 0

    def test_20th_snapshot(self):
        """Also resurfaces on 20th (any multiple of 10)."""
        quota: dict[str, int] = {}
        current = {"fp1": {"severity": "blocking"}}
        previous = {"fp1": {"severity": "blocking"}}
        count = _apply_resurface_cadence(quota, current, previous, 20)
        assert count == 1


# ── _select_by_quota ─────────────────────────────────────────────────


class TestSelectByQuota:
    def test_selects_findings_by_quota(self):
        f1 = FakeFinding(kind="E501", message="line too long")
        cr = ChannelResult(channel="lint", status="fail", findings=[f1])
        mesh = MeshResult(channel_results=[cr])
        fp = compute_finding_fingerprint(f1, "lint")
        quota = {fp: 1}
        result = _select_by_quota(mesh, quota)
        assert len(result) == 1

    def test_empty_quota_returns_empty(self):
        cr = ChannelResult(channel="lint", status="fail", findings=[FakeFinding()])
        mesh = MeshResult(channel_results=[cr])
        result = _select_by_quota(mesh, {})
        assert result == []

    def test_respects_quota_limit(self):
        f1 = FakeFinding(kind="E501", message="line too long")
        f2 = FakeFinding(kind="E501", message="line too long")
        cr = ChannelResult(channel="lint", status="fail", findings=[f1, f2])
        mesh = MeshResult(channel_results=[cr])
        fp = compute_finding_fingerprint(f1, "lint")
        quota = {fp: 1}  # Only allow 1
        result = _select_by_quota(mesh, quota)
        assert len(result) == 1

    def test_unmatched_findings_excluded(self):
        f1 = FakeFinding(kind="E501", message="line too long")
        cr = ChannelResult(channel="lint", status="fail", findings=[f1])
        mesh = MeshResult(channel_results=[cr])
        quota = {"unrelated_fp": 5}
        result = _select_by_quota(mesh, quota)
        assert len(result) == 0


# ── filter_display_findings ──────────────────────────────────────────


class TestFilterDisplayFindings:
    def test_no_delta_returns_all(self):
        findings = [FakeFinding(), FakeFinding()]
        mesh = MeshResult(channel_results=[])
        result = filter_display_findings(
            all_findings=findings,
            delta=None,
            mesh_result=mesh,
            current_index=None,
            previous_finding_index=None,
            snapshot_count=0,
        )
        assert len(result.display_findings) == 2
        assert result.resurfaced_count == 0

    def test_with_delta_filters_by_quota(self):
        f1 = FakeFinding(kind="NEW01", message="new issue")
        cr = ChannelResult(channel="lint", status="fail", findings=[f1])
        mesh = MeshResult(channel_results=[cr])
        fp = compute_finding_fingerprint(f1, "lint")
        delta = {"new": [{"fingerprint": fp, "count": 1}]}
        result = filter_display_findings(
            all_findings=[f1],
            delta=delta,
            mesh_result=mesh,
            current_index=None,
            previous_finding_index=None,
            snapshot_count=0,
        )
        assert len(result.display_findings) == 1

    def test_empty_delta_shows_nothing(self):
        f1 = FakeFinding()
        cr = ChannelResult(channel="lint", status="fail", findings=[f1])
        mesh = MeshResult(channel_results=[cr])
        delta: dict[str, Any] = {"new": [], "escalated": []}
        result = filter_display_findings(
            all_findings=[f1],
            delta=delta,
            mesh_result=mesh,
            current_index=None,
            previous_finding_index=None,
            snapshot_count=0,
        )
        assert len(result.display_findings) == 0
