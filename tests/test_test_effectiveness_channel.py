"""Tests for test_effectiveness_channel — channel protocol + findings."""

from __future__ import annotations

import os
import tempfile

from lintgate.channels.test_effectiveness_channel import (
    TestEffectivenessChannel,
    _teff001_low_semantic_ratio,
    _teff002_untested_functions,
    _teff003_structural_only,
    _teff004_high_vulnerability,
)
from lintgate.controlplane.types import ControlPlaneConfig, SupervisionEvent
from lintgate.linters.test_effectiveness.types import (
    AssertionInfo,
    AssertionKind,
    FunctionEffectiveness,
    TestEffectivenessManifest,
)


def _make_manifest(
    functions: dict[str, FunctionEffectiveness],
) -> TestEffectivenessManifest:
    """Helper to build a manifest with computed metrics."""
    m = TestEffectivenessManifest(functions=functions)
    m.update_metrics()
    return m


class TestChannelProtocol:
    """Verify channel follows the Channel protocol."""

    def test_channel_name(self):
        ch = TestEffectivenessChannel()
        assert ch.name == "test_effectiveness"

    def test_channel_timeout(self):
        ch = TestEffectivenessChannel()
        assert ch.timeout_ms == 12000

    def test_channel_not_blocking(self):
        ch = TestEffectivenessChannel()
        assert ch.blocking_capable is False

    def test_should_run_with_project(self):
        ch = TestEffectivenessChannel()
        event = SupervisionEvent(project_root="/some/project")
        config = ControlPlaneConfig(enabled=True)
        assert ch.should_run(event, config) is True

    def test_should_not_run_without_project(self):
        ch = TestEffectivenessChannel()
        event = SupervisionEvent(project_root="")
        config = ControlPlaneConfig(enabled=True)
        assert ch.should_run(event, config) is False


class TestTEFF001:
    """Low semantic assertion ratio."""

    def test_low_ratio_fires(self):
        fe = FunctionEffectiveness(
            function_name="foo",
            test_count=1,
            assertions=[
                AssertionInfo(kind=AssertionKind.IS_TRUE, line=1, strength=0.2),
                AssertionInfo(kind=AssertionKind.IS_NOT_NONE, line=2, strength=0.3),
            ],
        )
        fe.compute_scores()
        manifest = _make_manifest({"foo": fe})
        findings = _teff001_low_semantic_ratio(manifest, "/project")
        assert len(findings) == 1
        assert findings[0].kind == "TEFF001"

    def test_high_ratio_no_finding(self):
        fe = FunctionEffectiveness(
            function_name="foo",
            test_count=1,
            assertions=[
                AssertionInfo(kind=AssertionKind.EQUALITY, line=1, strength=0.9),
                AssertionInfo(kind=AssertionKind.LENGTH_CHECK, line=2, strength=0.8),
            ],
        )
        fe.compute_scores()
        manifest = _make_manifest({"foo": fe})
        findings = _teff001_low_semantic_ratio(manifest, "/project")
        assert findings == []


class TestTEFF002:
    """Untested public functions."""

    def test_untested_fires(self):
        fe = FunctionEffectiveness(function_name="untested_func", test_count=0)
        fe.compute_scores()
        manifest = _make_manifest({"untested_func": fe})
        findings = _teff002_untested_functions(manifest, "/project")
        assert len(findings) == 1
        assert findings[0].kind == "TEFF002"

    def test_tested_no_finding(self):
        fe = FunctionEffectiveness(
            function_name="tested_func",
            test_count=1,
            assertions=[
                AssertionInfo(kind=AssertionKind.EQUALITY, line=1, strength=0.9)
            ],
        )
        fe.compute_scores()
        manifest = _make_manifest({"tested_func": fe})
        findings = _teff002_untested_functions(manifest, "/project")
        assert findings == []

    def test_private_skipped(self):
        fe = FunctionEffectiveness(function_name="_private", test_count=0)
        fe.compute_scores()
        manifest = _make_manifest({"_private": fe})
        findings = _teff002_untested_functions(manifest, "/project")
        assert findings == []


class TestTEFF003:
    """Structural-only assertions."""

    def test_structural_only_fires(self):
        fe = FunctionEffectiveness(
            function_name="checked_func",
            test_count=1,
            assertions=[
                AssertionInfo(kind=AssertionKind.IS_NOT_NONE, line=1, strength=0.3),
                AssertionInfo(kind=AssertionKind.IS_TRUE, line=2, strength=0.2),
            ],
        )
        fe.compute_scores()
        manifest = _make_manifest({"checked_func": fe})
        findings = _teff003_structural_only(manifest, "/project")
        assert len(findings) == 1
        assert findings[0].kind == "TEFF003"
        assert findings[0].severity == "warning"

    def test_mixed_no_finding(self):
        fe = FunctionEffectiveness(
            function_name="mixed_func",
            test_count=1,
            assertions=[
                AssertionInfo(kind=AssertionKind.IS_NOT_NONE, line=1, strength=0.3),
                AssertionInfo(kind=AssertionKind.EQUALITY, line=2, strength=0.9),
            ],
        )
        fe.compute_scores()
        manifest = _make_manifest({"mixed_func": fe})
        findings = _teff003_structural_only(manifest, "/project")
        assert findings == []


class TestTEFF004:
    """High mutation vulnerability."""

    def test_high_vulnerability_fires(self):
        fe = FunctionEffectiveness(
            function_name="vulnerable",
            test_count=1,
            assertions=[
                AssertionInfo(kind=AssertionKind.IS_TRUE, line=1, strength=0.2),
            ],
        )
        fe.compute_scores()
        manifest = _make_manifest({"vulnerable": fe})
        findings = _teff004_high_vulnerability(manifest, "/project")
        assert len(findings) == 1
        assert findings[0].kind == "TEFF004"

    def test_low_vulnerability_no_finding(self):
        fe = FunctionEffectiveness(
            function_name="strong",
            test_count=1,
            assertions=[
                AssertionInfo(kind=AssertionKind.EQUALITY, line=1, strength=0.9),
            ],
        )
        fe.compute_scores()
        manifest = _make_manifest({"strong": fe})
        findings = _teff004_high_vulnerability(manifest, "/project")
        assert findings == []

    def test_untested_skipped(self):
        """Untested functions don't get TEFF004 (they get TEFF002 instead)."""
        fe = FunctionEffectiveness(function_name="no_tests", test_count=0)
        fe.compute_scores()
        manifest = _make_manifest({"no_tests": fe})
        findings = _teff004_high_vulnerability(manifest, "/project")
        assert findings == []


class TestChannelExecution:
    """Integration test for channel execute method."""

    def test_execute_skip_no_files(self):
        """Channel skips when no project root."""
        ch = TestEffectivenessChannel()
        event = SupervisionEvent(project_root="")
        config = ControlPlaneConfig(enabled=True)
        assert ch.should_run(event, config) is False

    def test_execute_produces_result(self):
        """Channel produces ChannelResult with correct structure."""
        # Create a minimal temp project
        with tempfile.TemporaryDirectory() as tmpdir:
            src_dir = os.path.join(tmpdir, "src")
            os.makedirs(src_dir)
            with open(os.path.join(src_dir, "module.py"), "w") as f:
                f.write("def compute(x):\n    return x + 1\n")

            test_dir = os.path.join(tmpdir, "tests")
            os.makedirs(test_dir)
            with open(os.path.join(test_dir, "test_module.py"), "w") as f:
                f.write(
                    "from src.module import compute\n\n"
                    "def test_compute():\n"
                    "    result = compute(1)\n"
                    "    assert result is not None\n"
                )

            ch = TestEffectivenessChannel()
            event = SupervisionEvent(project_root=tmpdir)
            config = ControlPlaneConfig(enabled=True)

            result = ch.execute(event, config)

            assert result.channel == "test_effectiveness"
            assert result.status in ("pass", "fail", "skip")
            assert result.duration_ms >= 0
            assert (
                "project_effectiveness_score" in result.metrics
                or "reason" in result.metrics
            )
