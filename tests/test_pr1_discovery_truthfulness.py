"""PR1: Discovery truthfulness and test-topology diagnostics tests.

Validates that mutation outputs are honest: discovery_state, topology_state,
and survival_interpretation fields correctly classify the quality of
mutation results.
"""

from __future__ import annotations

import ast
import textwrap

from lintgate.specification.test_topology import (
    DiscoveryState,
    SurvivalInterpretation,
    TopologyResult,
    TopologyState,
    analyze_topology,
    classify_discovery_state,
    interpret_survival,
)


def _parse_func(source: str, name: str | None = None) -> ast.FunctionDef:
    tree = ast.parse(textwrap.dedent(source))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and (name is None or node.name == name):
            return node
    msg = f"No function {name!r} found"
    raise ValueError(msg)


# ── DiscoveryState classification ────────────────────────────────


class TestClassifyDiscoveryState:
    def test_no_test_files(self):
        state = classify_discovery_state(
            test_files_found=0,
            callables_loaded=0,
            import_failures=0,
            fallback_used=False,
            total_killed=0,
        )
        assert state == DiscoveryState.NO_TEST_FILES

    def test_import_failed(self):
        state = classify_discovery_state(
            test_files_found=3,
            callables_loaded=0,
            import_failures=3,
            fallback_used=True,
            total_killed=0,
        )
        assert state == DiscoveryState.DISCOVERY_IMPORT_FAILED

    def test_files_found_none_linked(self):
        state = classify_discovery_state(
            test_files_found=2,
            callables_loaded=0,
            import_failures=0,
            fallback_used=True,
            total_killed=0,
        )
        assert state == DiscoveryState.TEST_FILES_FOUND_NONE_LINKED

    def test_linked_zero_kills(self):
        state = classify_discovery_state(
            test_files_found=2,
            callables_loaded=5,
            import_failures=0,
            fallback_used=False,
            total_killed=0,
        )
        assert state == DiscoveryState.TESTS_LINKED_ZERO_KILLS

    def test_discovery_ok(self):
        state = classify_discovery_state(
            test_files_found=2,
            callables_loaded=5,
            import_failures=0,
            fallback_used=False,
            total_killed=3,
        )
        assert state == DiscoveryState.DISCOVERY_OK


# ── SurvivalInterpretation ───────────────────────────────────────


class TestInterpretSurvival:
    def test_no_test_files_is_artifact(self):
        interp = interpret_survival(
            DiscoveryState.NO_TEST_FILES,
            TopologyState.NORMAL,
            1.0,
        )
        assert interp == SurvivalInterpretation.DISCOVERY_ARTIFACT

    def test_import_failed_is_artifact(self):
        interp = interpret_survival(
            DiscoveryState.DISCOVERY_IMPORT_FAILED,
            TopologyState.NORMAL,
            1.0,
        )
        assert interp == SurvivalInterpretation.DISCOVERY_ARTIFACT

    def test_none_linked_is_artifact(self):
        interp = interpret_survival(
            DiscoveryState.TEST_FILES_FOUND_NONE_LINKED,
            TopologyState.NORMAL,
            1.0,
        )
        assert interp == SurvivalInterpretation.DISCOVERY_ARTIFACT

    def test_mock_dominant_is_mock_artifact(self):
        interp = interpret_survival(
            DiscoveryState.DISCOVERY_OK,
            TopologyState.MOCK_BOUNDARY_DOMINANT,
            0.8,
        )
        assert interp == SurvivalInterpretation.MOCK_BOUNDARY_ARTIFACT

    def test_zero_kills_high_survival_is_low_confidence(self):
        interp = interpret_survival(
            DiscoveryState.TESTS_LINKED_ZERO_KILLS,
            TopologyState.NORMAL,
            1.0,
        )
        assert interp == SurvivalInterpretation.LOW_CONFIDENCE

    def test_ok_normal_is_meaningful(self):
        interp = interpret_survival(
            DiscoveryState.DISCOVERY_OK,
            TopologyState.NORMAL,
            0.5,
        )
        assert interp == SurvivalInterpretation.MEANINGFUL

    def test_ok_patched_internal_still_meaningful(self):
        """Partial patching doesn't invalidate results."""
        interp = interpret_survival(
            DiscoveryState.DISCOVERY_OK,
            TopologyState.PATCHED_INTERNAL_CALLS,
            0.3,
        )
        assert interp == SurvivalInterpretation.MEANINGFUL


# ── Topology analysis ────────────────────────────────────────────


class TestAnalyzeTopology:
    def test_no_outbound_calls_is_normal(self):
        func = _parse_func("def f(x): return x + 1")
        result = analyze_topology(func, [])
        assert result.topology_state == TopologyState.NORMAL

    def test_no_test_files_is_normal(self):
        func = _parse_func("def f(x): return helper(x)")
        result = analyze_topology(func, [])
        assert result.topology_state == TopologyState.NORMAL

    def test_mock_dominant_when_most_calls_patched(self, tmp_path):
        """When >50% of outbound calls are patched, mark mock-dominant."""
        func = _parse_func("""
            def orchestrate(x):
                a = fetch_data(x)
                b = transform(a)
                c = validate(b)
                return save(c)
        """)

        test_code = textwrap.dedent("""\
            from unittest.mock import patch

            @patch("mod.fetch_data")
            @patch("mod.transform")
            @patch("mod.validate")
            def test_orchestrate(mock_v, mock_t, mock_f):
                pass
        """)
        test_file = tmp_path / "test_orch.py"
        test_file.write_text(test_code)

        result = analyze_topology(func, [str(test_file)])
        assert result.topology_state == TopologyState.MOCK_BOUNDARY_DOMINANT
        assert "fetch_data" in result.mocked_call_sites
        assert result.patched_symbol_count >= 3

    def test_patched_internal_when_some_calls_patched(self, tmp_path):
        """When some but not most outbound calls are patched."""
        func = _parse_func("""
            def process(x):
                a = step_one(x)
                b = step_two(a)
                c = step_three(b)
                d = step_four(c)
                return step_five(d)
        """)

        test_code = textwrap.dedent("""\
            from unittest.mock import patch

            @patch("mod.step_one")
            def test_process(mock_s1):
                pass
        """)
        test_file = tmp_path / "test_proc.py"
        test_file.write_text(test_code)

        result = analyze_topology(func, [str(test_file)])
        assert result.topology_state == TopologyState.PATCHED_INTERNAL_CALLS

    def test_normal_when_no_patching(self, tmp_path):
        func = _parse_func("def f(x): return helper(x)")

        test_code = textwrap.dedent("""\
            def test_f():
                assert f(1) == 1
        """)
        test_file = tmp_path / "test_f.py"
        test_file.write_text(test_code)

        result = analyze_topology(func, [str(test_file)])
        assert result.topology_state == TopologyState.NORMAL

    def test_monkeypatch_detection(self, tmp_path):
        """Detect monkeypatch.setattr patterns."""
        func = _parse_func("""
            def f(x):
                return fetch(x) + compute(x)
        """)

        test_code = textwrap.dedent("""\
            def test_f(monkeypatch):
                monkeypatch.setattr("mod", "fetch", lambda x: 0)
                monkeypatch.setattr("mod", "compute", lambda x: 0)
        """)
        test_file = tmp_path / "test_f.py"
        test_file.write_text(test_code)

        result = analyze_topology(func, [str(test_file)])
        assert result.patched_symbol_count >= 2


class TestTopologyResultToDict:
    def test_minimal(self):
        result = TopologyResult()
        d = result.to_dict()
        assert "topology_state" in d
        assert "topology_confidence" in d

    def test_with_patches(self):
        result = TopologyResult(
            topology_state=TopologyState.MOCK_BOUNDARY_DOMINANT,
            patched_symbols=["foo", "bar"],
            mocked_call_sites=["foo"],
            patched_symbol_count=2,
            topology_confidence=0.85,
        )
        d = result.to_dict()
        assert d["topology_state"] == "MOCK_BOUNDARY_DOMINANT"
        assert d["patched_symbol_count"] == 2
        assert "foo" in d["patched_symbols"]


