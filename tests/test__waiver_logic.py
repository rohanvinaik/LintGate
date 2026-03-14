"""Tests for lintgate/channels/_waiver_logic.py — waiver parsing and application."""

from __future__ import annotations

from datetime import date

from lintgate.channels._symbol_types import SymbolCoverageWaiver, SymbolSpan
from lintgate.channels._waiver_logic import (
    _match_target_waiver,
    _partition_waivers,
    apply_waivers,
    parse_waivers,
)

# ── parse_waivers ─────────────────────────────────────────────────


def test_parse_waivers_valid() -> None:
    raw = [
        {"symbol": "module.py::func", "reason": "Tested elsewhere"},
        {"symbol": "module.py::Class.*", "reason": "Abstract base", "expires": "2030-01-01"},
    ]
    result = parse_waivers(raw)
    assert len(result) == 2
    assert result[0].symbol == "module.py::func"
    assert result[1].expires == "2030-01-01"


def test_parse_waivers_not_a_list() -> None:
    assert parse_waivers("not a list") == []
    assert parse_waivers(None) == []
    assert parse_waivers(42) == []


def test_parse_waivers_skips_invalid_entries() -> None:
    raw = [
        {"symbol": "", "reason": "empty symbol"},  # empty symbol
        {"symbol": "valid", "reason": ""},  # empty reason
        "not a dict",
        {"symbol": "ok.py::func", "reason": "valid"},
    ]
    result = parse_waivers(raw)
    assert len(result) == 1
    assert result[0].symbol == "ok.py::func"


# ── _partition_waivers ────────────────────────────────────────────


def test_partition_waivers_separates_exact_and_glob() -> None:
    waivers = [
        SymbolCoverageWaiver(symbol="mod.py::func", reason="exact"),
        SymbolCoverageWaiver(symbol="mod.py::Class.*", reason="glob"),
    ]
    exact, glob, expired = _partition_waivers(waivers, date(2025, 6, 1))
    assert "mod.py::func" in exact
    assert len(glob) == 1
    assert len(expired) == 0


def test_partition_waivers_detects_expired() -> None:
    waivers = [
        SymbolCoverageWaiver(symbol="mod.py::func", reason="old", expires="2020-01-01"),
        SymbolCoverageWaiver(symbol="mod.py::func2", reason="fresh", expires="2030-01-01"),
    ]
    exact, glob, expired = _partition_waivers(waivers, date(2025, 6, 1))
    assert len(expired) == 1
    assert expired[0].symbol == "mod.py::func"
    assert "mod.py::func2" in exact


def test_partition_waivers_invalid_date_skipped() -> None:
    waivers = [
        SymbolCoverageWaiver(symbol="mod.py::func", reason="bad date", expires="not-a-date"),
    ]
    exact, glob, expired = _partition_waivers(waivers, date(2025, 6, 1))
    assert len(exact) == 0
    assert len(glob) == 0
    assert len(expired) == 0


# ── _match_target_waiver ─────────────────────────────────────────


def _make_span(symbol_key: str) -> SymbolSpan:
    return SymbolSpan(
        file="/tmp/mod.py",
        symbol_key=symbol_key,
        name=symbol_key.split("::")[-1],
        start_line=1,
        end_line=10,
        is_method=False,
        class_name=None,
    )


def test_match_exact() -> None:
    exact = {"mod.py::func": SymbolCoverageWaiver(symbol="mod.py::func", reason="exact")}
    target = _make_span("mod.py::func")
    result = _match_target_waiver(target, exact, [])
    assert result is not None
    assert result.symbol == "mod.py::func"


def test_match_glob() -> None:
    glob = [SymbolCoverageWaiver(symbol="mod.py::Class.*", reason="glob")]
    target = _make_span("mod.py::Class.method")
    result = _match_target_waiver(target, {}, glob)
    assert result is not None


def test_no_match() -> None:
    target = _make_span("mod.py::unmatched")
    result = _match_target_waiver(target, {}, [])
    assert result is None


# ── apply_waivers ─────────────────────────────────────────────────


def test_apply_waivers_filters_targets() -> None:
    targets = [_make_span("mod.py::func"), _make_span("mod.py::other")]
    waivers = [SymbolCoverageWaiver(symbol="mod.py::func", reason="waived")]
    filtered, applied, expired = apply_waivers(targets, waivers, date(2025, 6, 1))
    assert len(filtered) == 1
    assert filtered[0].symbol_key == "mod.py::other"
    assert len(applied) == 1


def test_apply_waivers_reports_expired() -> None:
    targets = [_make_span("mod.py::func")]
    waivers = [
        SymbolCoverageWaiver(symbol="mod.py::func", reason="old", expires="2020-01-01"),
    ]
    filtered, applied, expired = apply_waivers(targets, waivers, date(2025, 6, 1))
    assert len(filtered) == 1  # Not waived because expired
    assert len(expired) == 1
    assert len(applied) == 0
