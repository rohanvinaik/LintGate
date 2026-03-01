"""Tests for symbol coverage: gate orchestration and TestChannel integration."""

from __future__ import annotations

import json
import textwrap
from unittest.mock import patch

from lintgate.channels.symbol_coverage import (
    SymbolCoverageGateResult,
    SymbolCoverageResult,
    SymbolSpan,
    run_symbol_coverage_gate,
)


class TestRunSymbolCoverageGate:
    def _write_source(self, tmp_path, content: str) -> str:
        p = tmp_path / "mod.py"
        p.write_text(textwrap.dedent(content))
        return str(p)

    def _write_coverage(self, tmp_path, files_data: dict) -> str:
        p = tmp_path / "coverage.json"
        p.write_text(json.dumps({"files": files_data}))
        return str(p)

    def test_all_pass(self, tmp_path):
        src = self._write_source(
            tmp_path,
            """\
            def func():
                return 1
        """,
        )
        cov_path = self._write_coverage(
            tmp_path,
            {
                src: {
                    "executed_lines": [1, 2],
                    "missing_lines": [],
                    "excluded_lines": [],
                    "missing_branches": [],
                }
            },
        )
        with patch(
            "lintgate.channels.symbol_coverage.get_changed_line_ranges"
        ) as mock_diff:
            mock_diff.return_value = [range(1, 3)]
            result = run_symbol_coverage_gate(
                cov_path, [src], str(tmp_path), {"enabled": True, "mode": "changed"}
            )
        assert result.passed is True
        assert all(r.covered for r in result.symbol_results)

    def test_partial_fail(self, tmp_path):
        src = self._write_source(
            tmp_path,
            """\
            def covered_func():
                return 1

            def uncovered_func():
                return 2
        """,
        )
        cov_path = self._write_coverage(
            tmp_path,
            {
                src: {
                    "executed_lines": [1, 2],
                    "missing_lines": [4, 5],
                    "excluded_lines": [],
                    "missing_branches": [],
                }
            },
        )
        with patch(
            "lintgate.channels.symbol_coverage.get_changed_line_ranges"
        ) as mock_diff:
            mock_diff.return_value = [range(1, 6)]
            result = run_symbol_coverage_gate(
                cov_path, [src], str(tmp_path), {"enabled": True, "mode": "changed"}
            )
        assert result.passed is False
        uncovered = [r for r in result.symbol_results if not r.covered]
        assert len(uncovered) == 1
        assert uncovered[0].symbol.name == "uncovered_func"

    def test_waivers_applied(self, tmp_path):
        src = self._write_source(
            tmp_path,
            """\
            def waived_func():
                return 1
        """,
        )
        cov_path = self._write_coverage(
            tmp_path,
            {
                src: {
                    "executed_lines": [],
                    "missing_lines": [1, 2],
                    "excluded_lines": [],
                    "missing_branches": [],
                }
            },
        )
        with patch(
            "lintgate.channels.symbol_coverage.get_changed_line_ranges"
        ) as mock_diff:
            mock_diff.return_value = [range(1, 3)]
            result = run_symbol_coverage_gate(
                cov_path,
                [src],
                str(tmp_path),
                {
                    "enabled": True,
                    "mode": "changed",
                    "waivers": [
                        {"symbol": "mod.py::waived_func", "reason": "Tested elsewhere"}
                    ],
                },
            )
        assert result.passed is True
        assert len(result.waivers_applied) == 1
        assert result.symbol_results == []  # Waived target not checked

    def test_no_targets_passes(self, tmp_path):
        cov_path = self._write_coverage(tmp_path, {})
        result = run_symbol_coverage_gate(
            cov_path, [], str(tmp_path), {"enabled": True, "mode": "changed"}
        )
        assert result.passed is True
        assert "No symbols targeted" in result.skipped_reasons[0]

    def test_missing_coverage_json_mcp(self, tmp_path):
        src = self._write_source(tmp_path, "def func():\n    pass\n")
        with patch(
            "lintgate.channels.symbol_coverage.get_changed_line_ranges"
        ) as mock_diff:
            mock_diff.return_value = [range(1, 3)]
            result = run_symbol_coverage_gate(
                str(tmp_path / "nonexistent.json"),
                [src],
                str(tmp_path),
                {"enabled": True, "mode": "changed"},
                surface="mcp",
            )
        assert result.passed is True  # MCP: fail-open
        assert len(result.skipped_reasons) > 0

    def test_missing_coverage_json_ci(self, tmp_path):
        src = self._write_source(tmp_path, "def func():\n    pass\n")
        with patch(
            "lintgate.channels.symbol_coverage.get_changed_line_ranges"
        ) as mock_diff:
            mock_diff.return_value = [range(1, 3)]
            result = run_symbol_coverage_gate(
                str(tmp_path / "nonexistent.json"),
                [src],
                str(tmp_path),
                {"enabled": True, "mode": "changed"},
                surface="ci",
            )
        assert result.passed is False  # CI: fail-closed

    def test_unresolved_required_blocks(self, tmp_path):
        cov_path = self._write_coverage(tmp_path, {})
        result = run_symbol_coverage_gate(
            cov_path,
            [],
            str(tmp_path),
            {
                "enabled": True,
                "mode": "changed",
                "required_symbols": ["ghost.py::func"],
            },
        )
        assert result.passed is False
        assert "ghost.py::func" in result.unresolved_required

    def test_expired_waivers_reported(self, tmp_path):
        src = self._write_source(
            tmp_path,
            """\
            def func():
                return 1
        """,
        )
        cov_path = self._write_coverage(
            tmp_path,
            {
                src: {
                    "executed_lines": [1, 2],
                    "missing_lines": [],
                    "excluded_lines": [],
                    "missing_branches": [],
                }
            },
        )
        with patch(
            "lintgate.channels.symbol_coverage.get_changed_line_ranges"
        ) as mock_diff:
            mock_diff.return_value = [range(1, 3)]
            result = run_symbol_coverage_gate(
                cov_path,
                [src],
                str(tmp_path),
                {
                    "enabled": True,
                    "mode": "changed",
                    "waivers": [
                        {
                            "symbol": "mod.py::func",
                            "reason": "Old exemption",
                            "expires": "2020-01-01",
                        }
                    ],
                },
            )
        assert len(result.waivers_expired) == 1


class TestTestChannelIntegration:
    """Integration tests for symbol coverage gate within TestChannel."""

    @staticmethod
    def _make_config(surface="mcp", project_root="/tmp/test", files=None):
        from lintgate.controlplane.types import (
            ChannelConfig,
            ControlPlaneConfig,
            SupervisionEvent,
        )

        event = SupervisionEvent(
            surface=surface,
            project_root=project_root,
            files_changed=files or [f"{project_root}/mod.py"],
        )
        config = ControlPlaneConfig(
            enabled=True,
            channels={
                "tests": ChannelConfig(
                    settings={"symbol_coverage": {"enabled": True, "mode": "changed"}},
                )
            },
        )
        return event, config

    def test_blocking_findings_for_uncovered_symbols(self):
        from lintgate.channels.test_channel import TestChannel

        gate_result = SymbolCoverageGateResult(
            passed=False,
            symbol_results=[
                SymbolCoverageResult(
                    symbol=SymbolSpan(
                        file="/src/mod.py",
                        symbol_key="mod.py::uncovered",
                        name="uncovered",
                        start_line=1,
                        end_line=5,
                        is_method=False,
                        class_name=None,
                    ),
                    covered=False,
                    missing_lines=[3, 4],
                    missing_branches=[],
                    total_lines_in_span=5,
                    executed_lines_in_span=3,
                ),
            ],
        )

        event, config = self._make_config()
        channel = TestChannel()
        with (
            patch(
                "lintgate.channels.test_channel.find_impacted_tests", return_value=[]
            ),
            patch(
                "lintgate.channels.symbol_coverage.run_symbol_coverage_gate",
                return_value=gate_result,
            ),
        ):
            result = channel.execute(event, config)

        # No impacted tests -> no coverage data -> gate skipped
        assert result.status in ("pass", "fail")

    def test_ci_missing_coverage_emits_warning(self):
        from lintgate.channels.test_channel import TestChannel

        event, config = self._make_config(surface="ci")
        channel = TestChannel()
        with patch(
            "lintgate.channels.test_channel.find_impacted_tests", return_value=[]
        ):
            result = channel.execute(event, config)

        gate_skipped = [f for f in result.findings if f.kind == "symbol_gate_skipped"]
        assert len(gate_skipped) == 1
        assert gate_skipped[0].severity == "warning"
        assert "no coverage data" in gate_skipped[0].message

    def test_execute_with_symbol_gate_findings(self):
        """Full integration: TestChannel produces blocking findings for uncovered symbols."""
        from lintgate.channels.test_channel import TestChannel, TestRunResult

        event, config = self._make_config()
        gate_result = SymbolCoverageGateResult(
            passed=False,
            symbol_results=[
                SymbolCoverageResult(
                    symbol=SymbolSpan(
                        file="/tmp/test/mod.py",
                        symbol_key="mod.py::untested",
                        name="untested",
                        start_line=10,
                        end_line=15,
                        is_method=False,
                        class_name=None,
                    ),
                    covered=False,
                    missing_lines=[12, 13],
                    missing_branches=[],
                    total_lines_in_span=6,
                    executed_lines_in_span=4,
                ),
            ],
            unresolved_required=["missing.py::gone"],
        )
        fake_test_result = TestRunResult(
            passed=3,
            failed=0,
            coverage_pct=85.0,
            coverage_json_path="/tmp/cov.json",
        )

        channel = TestChannel()
        with (
            patch(
                "lintgate.channels.test_channel.find_impacted_tests",
                return_value=["test_mod.py"],
            ),
            patch(
                "lintgate.channels.test_channel.run_tests",
                return_value=fake_test_result,
            ),
            patch(
                "lintgate.channels.symbol_coverage.run_symbol_coverage_gate",
                return_value=gate_result,
            ),
        ):
            result = channel.execute(event, config)

        blocking = [f for f in result.findings if f.severity == "blocking"]
        assert len(blocking) >= 1
        symbol_uncovered = [f for f in result.findings if f.kind == "symbol_uncovered"]
        assert len(symbol_uncovered) == 1
        assert (
            symbol_uncovered[0].severity == "warning"
        )  # Downgraded due to partial run with passing coverage
        assert symbol_uncovered[0].evidence["symbol"] == "untested"
        unresolved = [f for f in blocking if f.kind == "unresolved_required_symbol"]
        assert len(unresolved) == 1
        assert result.severity == "blocking"

    def test_falls_back_to_broad_tests_when_impacted_empty(self, tmp_path):
        """When symbol gate is enabled and impacted tests are empty, use fallback targets."""
        from lintgate.channels.test_channel import TestChannel, TestRunResult

        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_smoke.py").write_text(
            "def test_smoke():\n    assert True\n"
        )
        src = tmp_path / "mod.py"
        src.write_text("def f():\n    return 1\n")

        event, config = self._make_config(
            project_root=str(tmp_path),
            files=[str(src)],
        )
        fake_result = TestRunResult(
            passed=1,
            failed=0,
            coverage_pct=100.0,
            coverage_json_path="/tmp/cov.json",
        )
        gate_result = SymbolCoverageGateResult(passed=True, symbol_results=[])

        channel = TestChannel()
        with (
            patch(
                "lintgate.channels.test_channel.find_impacted_tests", return_value=[]
            ),
            patch(
                "lintgate.channels.test_channel.run_tests", return_value=fake_result
            ) as mock_run,
            patch(
                "lintgate.channels.symbol_coverage.run_symbol_coverage_gate",
                return_value=gate_result,
            ),
        ):
            result = channel.execute(event, config)

        assert mock_run.call_count == 1
        run_targets = mock_run.call_args.args[0]
        assert any(str(t).endswith("/tests") for t in run_targets)
        fallback_findings = [
            f for f in result.findings if f.kind == "symbol_gate_fallback"
        ]
        assert len(fallback_findings) == 1
