"""Tests for mutation_decompose performance bridge (#312)."""

import ast
from unittest.mock import patch

from mcp_tools._mutation_tools_impl import (
    _build_performance_unlocks,
    _CATEGORY_PERFORMANCE_MAP,
)


class TestCategoryPerformanceMap:
    def test_all_categories_have_entries(self):
        """All 5 mutation categories are mapped."""
        for cat in ("BOUNDARY", "SWAP", "VALUE", "STATE", "TYPE"):
            assert cat in _CATEGORY_PERFORMANCE_MAP

    def test_entries_have_required_keys(self):
        for cat, mapping in _CATEGORY_PERFORMANCE_MAP.items():
            assert "unlock" in mapping
            assert "description" in mapping
            assert "performance_actions" in mapping
            assert "cacheable_subunit" in mapping
            assert "parallelizable_subunit" in mapping
            assert "jit_eligible" in mapping

    def test_swap_is_cacheable_and_parallelizable(self):
        m = _CATEGORY_PERFORMANCE_MAP["SWAP"]
        assert m["cacheable_subunit"] is True
        assert m["parallelizable_subunit"] is True

    def test_value_is_cacheable_not_parallel(self):
        m = _CATEGORY_PERFORMANCE_MAP["VALUE"]
        assert m["cacheable_subunit"] is True
        assert m["parallelizable_subunit"] is False

    def test_boundary_not_cacheable(self):
        m = _CATEGORY_PERFORMANCE_MAP["BOUNDARY"]
        assert m["cacheable_subunit"] is False

    def test_type_is_jit_eligible(self):
        m = _CATEGORY_PERFORMANCE_MAP["TYPE"]
        assert m["jit_eligible"] is True


class TestBuildPerformanceUnlocks:
    def test_returns_unlocks_for_each_surviving_category(self):
        cats = ["BOUNDARY", "SWAP"]
        per_cat = [
            {"category": "BOUNDARY", "survived": 3, "total": 5},
            {"category": "SWAP", "survived": 2, "total": 4},
        ]
        unlocks = _build_performance_unlocks(cats, per_cat)
        assert len(unlocks) == 2
        unlock_types = [u["unlock_type"] for u in unlocks]
        assert "predicate_extraction" in unlock_types
        assert "strategy_seam" in unlock_types

    def test_confidence_scales_with_survival_rate(self):
        cats = ["VALUE"]
        per_cat_high = [{"category": "VALUE", "survived": 9, "total": 10}]
        per_cat_low = [{"category": "VALUE", "survived": 1, "total": 10}]

        high = _build_performance_unlocks(cats, per_cat_high)
        low = _build_performance_unlocks(cats, per_cat_low)
        assert high[0]["confidence"] > low[0]["confidence"]

    def test_unknown_category_skipped(self):
        cats = ["UNKNOWN"]
        per_cat = [{"category": "UNKNOWN", "survived": 1, "total": 1}]
        unlocks = _build_performance_unlocks(cats, per_cat)
        assert len(unlocks) == 0

    def test_each_unlock_has_required_fields(self):
        cats = ["VALUE", "STATE"]
        per_cat = [
            {"category": "VALUE", "survived": 2, "total": 5},
            {"category": "STATE", "survived": 1, "total": 3},
        ]
        unlocks = _build_performance_unlocks(cats, per_cat)
        for u in unlocks:
            assert "category" in u
            assert "unlock_type" in u
            assert "description" in u
            assert "performance_actions" in u
            assert "predicted_subunits" in u
            assert "confidence" in u
            assert "survival_rate" in u

    def test_predicted_subunits_structure(self):
        cats = ["SWAP"]
        per_cat = [{"category": "SWAP", "survived": 1, "total": 2}]
        unlocks = _build_performance_unlocks(cats, per_cat)
        subunits = unlocks[0]["predicted_subunits"]
        assert "cacheable" in subunits
        assert "parallelizable" in subunits
        assert "jit_eligible" in subunits

    def test_empty_categories_returns_empty(self):
        assert _build_performance_unlocks([], []) == []
