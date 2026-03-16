"""Mutation-targeted tests for prescriptive_projection — decomposed functions."""

from __future__ import annotations

import tempfile

from lintgate.specification.prescriptive.projection import (
    FunctionProjection,
    _assemble_projection,
    _build_reverse_graph,
    build_projection_from_ledger,
    load_projection,
    save_projection,
)

# ── _build_reverse_graph ─────────────────────────────────────────────


class TestBuildReverseGraph:
    def test_empty_graph(self):
        assert _build_reverse_graph({}) == {}

    def test_single_edge(self):
        result = _build_reverse_graph({"a": ["b"]})
        assert result == {"b": ["a"]}

    def test_multiple_callers(self):
        result = _build_reverse_graph({"a": ["c"], "b": ["c"]})
        assert set(result["c"]) == {"a", "b"}

    def test_multiple_callees(self):
        result = _build_reverse_graph({"a": ["b", "c"]})
        assert result == {"b": ["a"], "c": ["a"]}

    def test_no_mutation_of_input(self):
        graph = {"a": ["b"]}
        original = {"a": ["b"]}
        _build_reverse_graph(graph)
        assert graph == original


# ── _assemble_projection (SWAP + VALUE) ──────────────────────────────


class TestAssembleProjection:
    def test_exact_values(self):
        fd = {
            "coupling_surface": 3,
            "covering_tests": ["t1", "t2"],
            "priority_band": "P1",
            "is_pure": True,
            "estimated_sigma": 7,
        }
        result = _assemble_projection("mod::func", fd, ["caller1"], ["callee1", "callee2"])
        assert result.function_key == "mod::func"
        assert result.fan_in == 1
        assert result.fan_out == 2
        assert result.coupling_surface == 3
        assert result.covering_tests == ["t1", "t2"]
        assert result.priority_band == "P1"
        assert result.is_pure is True
        assert result.estimated_sigma == 7

    def test_swap_fan_in_fan_out_differ(self):
        """SWAP: verify fan_in and fan_out are not interchangeable."""
        fd = {"coupling_surface": 0}
        r1 = _assemble_projection("f", fd, ["a", "b"], ["c"])
        assert r1.fan_in == 2
        assert r1.fan_out == 1
        # Swapped args → different result
        r2 = _assemble_projection("f", fd, ["c"], ["a", "b"])
        assert r2.fan_in == 1
        assert r2.fan_out == 2

    def test_defaults_for_missing_keys(self):
        result = _assemble_projection("f", {}, [], [])
        assert result.coupling_surface == 0
        assert result.covering_tests == []
        assert result.priority_band == "P2"
        assert result.is_pure is False
        assert result.estimated_sigma == 0

    def test_covering_tests_capped_at_10(self):
        fd = {"covering_tests": [f"t{i}" for i in range(20)]}
        result = _assemble_projection("f", fd, [], [])
        assert len(result.covering_tests) == 10

    def test_neighbors_capped_at_20(self):
        fan_in = [f"caller_{i}" for i in range(15)]
        fan_out = [f"callee_{i}" for i in range(15)]
        result = _assemble_projection("f", {}, fan_in, fan_out)
        assert len(result.neighbor_keys) <= 20


# ── build_projection_from_ledger (SWAP) ──────────────────────────────


class TestBuildProjectionFromLedger:
    def test_swap_ledger_and_graph_not_interchangeable(self):
        """SWAP: verify the two args aren't interchangeable."""
        ledger = {"a": {"is_pure": True}, "b": {"is_pure": False}}
        graph = {"a": ["b"]}
        result = build_projection_from_ledger(ledger, graph)
        assert result["a"].fan_out == 1
        assert result["a"].fan_in == 0
        assert result["b"].fan_in == 1
        assert result["b"].fan_out == 0

    def test_no_graph(self):
        ledger = {"f": {"coupling_surface": 5}}
        result = build_projection_from_ledger(ledger)
        assert result["f"].fan_in == 0
        assert result["f"].fan_out == 0
        assert result["f"].coupling_surface == 5

    def test_empty_ledger(self):
        assert build_projection_from_ledger({}) == {}


# ── save/load round-trip (VALUE) ─────────────────────────────────────


class TestSaveLoadProjection:
    def test_round_trip_exact_values(self):
        proj = FunctionProjection(
            function_key="mod::func",
            fan_in=2,
            fan_out=3,
            coupling_surface=5,
            covering_tests=["t1"],
            priority_band="P0",
            is_pure=True,
            estimated_sigma=10,
        )
        with tempfile.TemporaryDirectory() as tmp:
            save_projection(tmp, {"mod::func": proj})
            loaded = load_projection(tmp)
            assert "mod::func" in loaded
            r = loaded["mod::func"]
            assert r.fan_in == 2
            assert r.fan_out == 3
            assert r.coupling_surface == 5
            assert r.priority_band == "P0"
            assert r.is_pure is True
            assert r.estimated_sigma == 10

    def test_load_empty_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            assert load_projection(tmp) == {}
