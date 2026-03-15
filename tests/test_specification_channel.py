"""Tests for the specification channel — protocol compliance, finding emission."""

from __future__ import annotations

from lintgate.channels.specification_channel import SpecificationChannel, _emit_findings
from lintgate.controlplane.types import ChannelResult, ControlPlaneConfig, SupervisionEvent
from lintgate.specification.types import (
    FunctionSpecification,
    RiskProfile,
    SpecCore,
    SpecificationLedger,
    TestabilityProfile,
    Traceability,
)


class TestChannelProtocol:
    def test_name(self):
        ch = SpecificationChannel()
        assert ch.name == "specification"

    def test_not_blocking(self):
        ch = SpecificationChannel()
        assert ch.blocking_capable is False

    def test_should_run_with_project_root(self):
        ch = SpecificationChannel()
        event = SupervisionEvent(project_root="/some/path")
        config = ControlPlaneConfig()
        assert ch.should_run(event, config) is True

    def test_should_not_run_without_project_root(self):
        ch = SpecificationChannel()
        event = SupervisionEvent(project_root="")
        config = ControlPlaneConfig()
        assert ch.should_run(event, config) is False


class TestMissingManifests:
    def test_skip_when_no_manifests(self):
        ch = SpecificationChannel()
        event = SupervisionEvent(
            project_root="/some/path",
            context={},
        )
        config = ControlPlaneConfig()
        result = ch.execute(event, config)
        assert result.status == "skip"
        assert result.channel == "specification"

    def test_skip_when_partial_manifests(self):
        ch = SpecificationChannel()
        event = SupervisionEvent(
            project_root="/some/path",
            context={"property_manifest": object()},
        )
        config = ControlPlaneConfig()
        result = ch.execute(event, config)
        assert result.status == "skip"


class TestChannelResult:
    def test_result_has_correct_channel(self):
        ch = SpecificationChannel()
        event = SupervisionEvent(
            project_root="/some/path",
            context={},
        )
        config = ControlPlaneConfig()
        result = ch.execute(event, config)
        assert isinstance(result, ChannelResult)
        assert result.channel == "specification"
        assert result.duration_ms >= 0


# ── _emit_findings mutation targets ───────────────────────────────────


def _make_fs(
    key: str = "mod::func",
    sigma: int = 10,
    assertions: int = 2,
    is_pure: bool = False,
    spec_level: float = 0.0,
    priority_band: str = "P2",
) -> FunctionSpecification:
    return FunctionSpecification(
        function_key=key,
        source_file="mod.py",
        core=SpecCore(estimated_sigma=sigma, specification_level=spec_level, is_pure=is_pure),
        testability=TestabilityProfile(),
        traceability=Traceability(assertion_count=assertions),
        risk=RiskProfile(priority_band=priority_band),
    )


def _make_ledger(*funcs: FunctionSpecification) -> SpecificationLedger:
    ledger = SpecificationLedger()
    for fs in funcs:
        ledger.functions[fs.function_key] = fs
    return ledger


class TestEmitFindings:
    def test_spec001_underspecified(self):
        fs = _make_fs(sigma=10, assertions=2)
        findings = _emit_findings(_make_ledger(fs), "/tmp")
        assert any(f.kind == "SPEC001" for f in findings)

    def test_spec001_not_when_specified(self):
        fs = _make_fs(sigma=5, assertions=10)
        findings = _emit_findings(_make_ledger(fs), "/tmp")
        assert not any(f.kind == "SPEC001" for f in findings)

    def test_spec006_pure_underspecified(self):
        fs = _make_fs(is_pure=True, spec_level=0.2, sigma=5, assertions=5)
        findings = _emit_findings(_make_ledger(fs), "/tmp")
        assert any(f.kind == "SPEC006" for f in findings)

    def test_spec010_risk_critical(self):
        fs = _make_fs(priority_band="P0", spec_level=0.3, sigma=5, assertions=5)
        findings = _emit_findings(_make_ledger(fs), "/tmp")
        assert any(f.kind == "SPEC010" for f in findings)

    def test_empty_ledger(self):
        findings = _emit_findings(_make_ledger(), "/tmp")
        assert len(findings) == 0

    def test_finding_count_capped(self):
        funcs = [_make_fs(key=f"mod::f{i}", sigma=10, assertions=1) for i in range(10)]
        findings = _emit_findings(_make_ledger(*funcs), "/tmp")
        spec001 = [f for f in findings if f.kind == "SPEC001"]
        assert len(spec001) <= 5
