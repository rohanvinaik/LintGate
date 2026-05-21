"""Tests for the kill-set-as-ground-truth linkage cache."""

from __future__ import annotations

import json
import os

from lintgate.specification.proven_linkage import (
    ProvenEntry,
    cache_path,
    killed_pairs_from_result,
    load_proven_entries,
    record_kills,
)


class TestLoadProvenEntries:
    def test_missing_cache_returns_empty(self, tmp_path):
        assert load_proven_entries(str(tmp_path), "anything::here") == []

    def test_corrupt_cache_returns_empty(self, tmp_path):
        path = cache_path(str(tmp_path))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write("not-json{{{")
        assert load_proven_entries(str(tmp_path), "fn") == []

    def test_round_trip_single_kill(self, tmp_path):
        record_kills(
            str(tmp_path),
            "mod.py::foo",
            [("/abs/tests/test_foo.py", "test_foo")],
        )
        entries = load_proven_entries(str(tmp_path), "mod.py::foo")
        assert len(entries) == 1
        assert entries[0].test_file == "/abs/tests/test_foo.py"
        assert entries[0].test_function == "test_foo"
        assert entries[0].killed_mutants == 1
        assert entries[0].last_proven > 0


class TestRecordKills:
    def test_no_op_when_pairs_empty(self, tmp_path):
        record_kills(str(tmp_path), "fn", [])
        assert not os.path.isfile(cache_path(str(tmp_path)))

    def test_increments_killed_mutants_on_repeat(self, tmp_path):
        record_kills(str(tmp_path), "fn", [("/t.py", "test_a")])
        record_kills(str(tmp_path), "fn", [("/t.py", "test_a")])
        entries = load_proven_entries(str(tmp_path), "fn")
        assert len(entries) == 1
        assert entries[0].killed_mutants == 2

    def test_distinct_pairs_accumulate(self, tmp_path):
        record_kills(
            str(tmp_path),
            "fn",
            [("/t.py", "test_a"), ("/t.py", "test_b")],
        )
        entries = load_proven_entries(str(tmp_path), "fn")
        assert {e.test_function for e in entries} == {"test_a", "test_b"}

    def test_separate_functions_isolated(self, tmp_path):
        record_kills(str(tmp_path), "fn1", [("/t.py", "test_1")])
        record_kills(str(tmp_path), "fn2", [("/t.py", "test_2")])
        assert [e.test_function for e in load_proven_entries(str(tmp_path), "fn1")] == ["test_1"]
        assert [e.test_function for e in load_proven_entries(str(tmp_path), "fn2")] == ["test_2"]

    def test_cache_file_shape(self, tmp_path):
        record_kills(str(tmp_path), "fn", [("/t.py", "test_a")])
        with open(cache_path(str(tmp_path)), encoding="utf-8") as f:
            data = json.load(f)
        assert "fn" in data
        assert "entries" in data["fn"]
        assert "updated" in data["fn"]


class TestKilledPairsFromResult:
    def test_empty_when_no_records(self):
        assert killed_pairs_from_result([], {}) == []

    def test_drops_entries_without_file_resolution(self):
        records = [{"killed_by_test": "test_orphan"}]
        assert killed_pairs_from_result(records, {}) == []

    def test_resolves_by_bare_name(self):
        records = [{"killed_by_test": "test_foo"}]
        pairs = killed_pairs_from_result(records, {"test_foo": "/t.py"})
        assert pairs == [("/t.py", "test_foo")]

    def test_resolves_qualified_name(self):
        records = [{"killed_by_test": "TestFoo.test_bar"}]
        pairs = killed_pairs_from_result(
            records,
            {"TestFoo.test_bar": "/t.py"},
        )
        assert pairs == [("/t.py", "TestFoo.test_bar")]

    def test_falls_back_to_bare_when_qualified_missing(self):
        records = [{"killed_by_test": "TestFoo.test_bar"}]
        pairs = killed_pairs_from_result(records, {"test_bar": "/t.py"})
        assert pairs == [("/t.py", "TestFoo.test_bar")]

    def test_dedups_within_single_call(self):
        records = [
            {"killed_by_test": "test_foo"},
            {"killed_by_test": "test_foo"},
        ]
        pairs = killed_pairs_from_result(records, {"test_foo": "/t.py"})
        assert pairs == [("/t.py", "test_foo")]


class TestProvenEntry:
    def test_defaults(self):
        entry = ProvenEntry(test_file="/t.py", test_function="test_x")
        assert entry.killed_mutants == 1
        assert entry.last_proven == 0
