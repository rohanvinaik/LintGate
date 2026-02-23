"""Tests for mcp_tools/performance_tools.py helper functions."""

from __future__ import annotations

from lintgate.linters.performance_checks.algebra_types import (
    AlgebraicProperty,
    FunctionProperties,
    PropertyKind,
    PurityResult,
)
from lintgate.linters.performance_checks.manifest import PropertyManifest
from mcp_tools.performance_tools import (
    _build_manifest_summary,
    _filter_manifest,
    _matches_filter,
    _select_property_candidates,
)


def _make_purity(name: str, *, is_pure: bool = True) -> PurityResult:
    return PurityResult(
        function_name=name,
        qualified_name=name,
        line=1,
        is_pure=is_pure,
        confidence=0.9,
        side_effects=(),
        parameter_count=1,
        return_annotation=None,
    )


def _make_func(
    name: str,
    *,
    is_pure: bool = True,
    source: str | None = None,
    hints: tuple[str, ...] = (),
    props: tuple[AlgebraicProperty, ...] = (),
) -> FunctionProperties:
    return FunctionProperties(
        purity=_make_purity(name, is_pure=is_pure),
        properties=props,
        optimization_hints=hints,
        source_file=source,
    )


def _build_test_manifest() -> PropertyManifest:
    m = PropertyManifest()
    m.functions["pure_cached"] = _make_func(
        "pure_cached", is_pure=True, source="/a.py", hints=("cacheable",)
    )
    m.functions["pure_parallel"] = _make_func(
        "pure_parallel", is_pure=True, source="/a.py", hints=("parallelizable",)
    )
    m.functions["impure_fn"] = _make_func(
        "impure_fn", is_pure=False, source="/b.py"
    )
    m.update_metrics()
    return m


# ── _build_manifest_summary ─────────────────────────────────────────


class TestBuildManifestSummary:
    def test_basic_structure(self):
        m = _build_test_manifest()
        summary = _build_manifest_summary(m, "/project")
        assert "summary" in summary
        assert "files" in summary
        assert summary["summary"]["total_functions"] == 3
        assert summary["summary"]["pure_functions"] == 2
        assert summary["summary"]["impure_functions"] == 1

    def test_purity_ratio(self):
        m = _build_test_manifest()
        summary = _build_manifest_summary(m, "/project")
        assert summary["summary"]["purity_ratio"] == round(2 / 3, 3)

    def test_files_grouped_by_source(self):
        m = _build_test_manifest()
        summary = _build_manifest_summary(m, "/project")
        assert "/a.py" in summary["files"]
        assert "/b.py" in summary["files"]
        assert len(summary["files"]["/a.py"]) == 2

    def test_hints_included(self):
        m = _build_test_manifest()
        summary = _build_manifest_summary(m, "/project")
        a_funcs = summary["files"]["/a.py"]
        hints_found = [f.get("hints") for f in a_funcs if f.get("hints")]
        assert len(hints_found) > 0

    def test_empty_manifest(self):
        m = PropertyManifest()
        m.update_metrics()
        summary = _build_manifest_summary(m, "/project")
        assert summary["summary"]["total_functions"] == 0
        assert summary["files"] == {}


# ── _matches_filter ──────────────────────────────────────────────────


class TestMatchesFilter:
    def test_pure_filter(self):
        entry = {"name": "fn", "is_pure": True, "hints": []}
        assert _matches_filter(entry, "pure", None) is True
        assert _matches_filter(entry, "impure", None) is False

    def test_cacheable_filter(self):
        entry = {"name": "fn", "is_pure": True, "hints": ["cacheable"]}
        assert _matches_filter(entry, "cacheable", None) is True

    def test_parallelizable_filter(self):
        entry = {"name": "fn", "is_pure": True, "hints": ["parallelizable"]}
        assert _matches_filter(entry, "parallelizable", None) is True
        assert _matches_filter(entry, "cacheable", None) is False

    def test_name_filter(self):
        entry = {"name": "compute_hash", "is_pure": True, "hints": []}
        assert _matches_filter(entry, None, "compute") is True
        assert _matches_filter(entry, None, "COMPUTE") is True  # case-insensitive
        assert _matches_filter(entry, None, "missing") is False

    def test_combined_filter(self):
        entry = {"name": "compute_hash", "is_pure": True, "hints": ["cacheable"]}
        assert _matches_filter(entry, "cacheable", "compute") is True
        assert _matches_filter(entry, "cacheable", "missing") is False
        assert _matches_filter(entry, "impure", "compute") is False

    def test_no_filter(self):
        entry = {"name": "fn", "is_pure": False, "hints": []}
        assert _matches_filter(entry, None, None) is True

    def test_unknown_filter_type_passes(self):
        entry = {"name": "fn", "is_pure": True, "hints": []}
        assert _matches_filter(entry, "unknown_type", None) is True


# ── _filter_manifest ────────────────────────────────────────────────


class TestFilterManifest:
    def test_no_filters_returns_unchanged(self):
        data = {"files": {"/a.py": [{"name": "fn", "is_pure": True, "hints": []}]}}
        result = _filter_manifest(data, None, None)
        assert result is data  # Same object, no copy

    def test_filter_removes_non_matching(self):
        data = {
            "files": {
                "/a.py": [
                    {"name": "pure_fn", "is_pure": True, "hints": []},
                    {"name": "impure_fn", "is_pure": False, "hints": []},
                ]
            }
        }
        result = _filter_manifest(data, "pure", None)
        assert len(result["files"]["/a.py"]) == 1
        assert result["files"]["/a.py"][0]["name"] == "pure_fn"
        assert result["filter_applied"] == {"type": "pure", "name": None}

    def test_filter_removes_empty_files(self):
        data = {
            "files": {
                "/a.py": [{"name": "impure_fn", "is_pure": False, "hints": []}],
            }
        }
        result = _filter_manifest(data, "pure", None)
        assert "/a.py" not in result["files"]


# ── _select_property_candidates ──────────────────────────────────────


class TestSelectPropertyCandidates:
    def _manifest_with_properties(self) -> PropertyManifest:
        bounded = AlgebraicProperty(
            kind=PropertyKind.BOUNDED, confidence=0.8, evidence="clamp"
        )
        commutative = AlgebraicProperty(
            kind=PropertyKind.COMMUTATIVE, confidence=0.7, evidence="arg swap"
        )
        m = PropertyManifest()
        m.functions["one_prop"] = _make_func("one_prop", props=(bounded,))
        m.functions["two_props"] = _make_func("two_props", props=(bounded, commutative))
        m.functions["pure_only"] = _make_func(
            "pure_only",
            props=(AlgebraicProperty(kind=PropertyKind.PURE, confidence=0.9, evidence="no side effects"),),
        )
        m.functions["impure"] = _make_func("impure", is_pure=False)
        return m

    def test_selects_by_property_count(self):
        m = self._manifest_with_properties()
        candidates = _select_property_candidates(m, None, 10)
        # two_props has 2 non-PURE properties, one_prop has 1
        assert len(candidates) == 2
        assert candidates[0][0] == "two_props"
        assert candidates[1][0] == "one_prop"

    def test_excludes_pure_only(self):
        m = self._manifest_with_properties()
        candidates = _select_property_candidates(m, None, 10)
        names = [c[0] for c in candidates]
        assert "pure_only" not in names

    def test_excludes_impure(self):
        m = self._manifest_with_properties()
        candidates = _select_property_candidates(m, None, 10)
        names = [c[0] for c in candidates]
        assert "impure" not in names

    def test_respects_max_functions(self):
        m = self._manifest_with_properties()
        candidates = _select_property_candidates(m, None, 1)
        assert len(candidates) == 1

    def test_function_filter(self):
        m = self._manifest_with_properties()
        candidates = _select_property_candidates(m, "one", 10)
        assert len(candidates) == 1
        assert candidates[0][0] == "one_prop"

    def test_empty_manifest(self):
        m = PropertyManifest()
        candidates = _select_property_candidates(m, None, 10)
        assert candidates == []
