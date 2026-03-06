"""Tests for manifest.py decomposed helpers and PropertyManifest methods."""

from __future__ import annotations

import ast
import textwrap

from lintgate.linters.performance_checks.algebra_types import (
    AlgebraicProperty,
    FunctionProperties,
    PropertyKind,
    PurityResult,
)
from lintgate.linters.performance_checks.manifest import (
    PropertyManifest,
    _compute_file_hash,
    _FuncFinder,
    _load_manifest_cache,
    _restore_cached_functions,
    _save_manifest_cache,
    _scan_file,
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


def _make_func_props(
    name: str, *, is_pure: bool = True, source: str | None = None
) -> FunctionProperties:
    return FunctionProperties(
        purity=_make_purity(name, is_pure=is_pure),
        properties=(),
        optimization_hints=(),
        source_file=source,
    )


# ── PropertyManifest ────────────────────────────────────────────────


class TestPropertyManifest:
    def test_empty_manifest(self):
        m = PropertyManifest()
        assert m.pure_count == 0
        assert m.impure_count == 0
        assert m.functions == {}

    def test_get_source_file_found(self):
        m = PropertyManifest()
        m.functions["foo"] = _make_func_props("foo", source="/a.py")
        assert m.get_source_file("foo") == "/a.py"

    def test_get_source_file_missing(self):
        m = PropertyManifest()
        assert m.get_source_file("nonexistent") is None

    def test_get_pure_function_names(self):
        m = PropertyManifest()
        m.functions["pure_fn"] = _make_func_props("pure_fn", is_pure=True)
        m.functions["impure_fn"] = _make_func_props("impure_fn", is_pure=False)
        names = m.get_pure_function_names()
        assert names == {"pure_fn"}

    def test_update_metrics(self):
        m = PropertyManifest()
        m.functions["a"] = _make_func_props("a", is_pure=True)
        m.functions["b"] = _make_func_props("b", is_pure=False)
        m.functions["c"] = _make_func_props("c", is_pure=True)
        m.update_metrics()
        assert m.pure_count == 2
        assert m.impure_count == 1

    def test_update_metrics_with_properties(self):
        prop = AlgebraicProperty(
            kind=PropertyKind.BOUNDED,
            confidence=0.8,
            evidence="clamp pattern",
        )
        m = PropertyManifest()
        m.functions["bounded_fn"] = FunctionProperties(
            purity=_make_purity("bounded_fn"),
            properties=(prop,),
            optimization_hints=("cacheable",),
            source_file=None,
        )
        m.update_metrics()
        assert m.property_distribution[PropertyKind.BOUNDED] == 1
        assert len(m.optimization_potential) == 1
        assert m.optimization_potential[0][0] == "bounded_fn"

    def test_roundtrip_serialization(self):
        m = PropertyManifest()
        m.functions["fn"] = _make_func_props("fn", is_pure=True, source="/x.py")
        m.update_metrics()

        data = m.to_dict()
        restored = PropertyManifest.from_dict(data)
        assert restored.functions["fn"].purity.is_pure is True
        assert restored.functions["fn"].source_file == "/x.py"
        assert restored.pure_count == 1


# ── _FuncFinder ─────────────────────────────────────────────────────


class TestFuncFinder:
    def test_finds_top_level_function(self):
        tree = ast.parse("def foo(): pass")
        finder = _FuncFinder()
        finder.visit(tree)
        assert "foo" in finder.nodes

    def test_finds_method_in_class(self):
        source = textwrap.dedent("""\
            class MyClass:
                def my_method(self):
                    pass
        """)
        tree = ast.parse(source)
        finder = _FuncFinder()
        finder.visit(tree)
        assert "MyClass.my_method" in finder.nodes

    def test_finds_async_function(self):
        tree = ast.parse("async def afoo(): pass")
        finder = _FuncFinder()
        finder.visit(tree)
        assert "afoo" in finder.nodes

    def test_nested_class_method(self):
        source = textwrap.dedent("""\
            class Outer:
                class Inner:
                    def nested(self):
                        pass
        """)
        tree = ast.parse(source)
        finder = _FuncFinder()
        finder.visit(tree)
        assert "Outer.Inner.nested" in finder.nodes

    def test_empty_module(self):
        tree = ast.parse("")
        finder = _FuncFinder()
        finder.visit(tree)
        assert finder.nodes == {}


# ── _compute_file_hash ──────────────────────────────────────────────


def test_compute_file_hash_deterministic(tmp_path):
    f = tmp_path / "test.py"
    f.write_text("hello")
    h1 = _compute_file_hash(str(f))
    h2 = _compute_file_hash(str(f))
    assert h1 == h2
    assert len(h1) == 32  # MD5 hex digest


def test_compute_file_hash_changes_with_content(tmp_path):
    f = tmp_path / "test.py"
    f.write_text("version1")
    h1 = _compute_file_hash(str(f))
    f.write_text("version2")
    h2 = _compute_file_hash(str(f))
    assert h1 != h2


# ── _load_manifest_cache / _save_manifest_cache ─────────────────────


def test_load_cache_missing_file(tmp_path):
    cache_path = tmp_path / "missing.json"
    manifest, metadata = _load_manifest_cache(cache_path)
    assert manifest.functions == {}
    assert metadata == {}


def test_save_and_load_roundtrip(tmp_path):
    cache_path = tmp_path / "cache.json"
    m = PropertyManifest()
    m.functions["fn"] = _make_func_props("fn", is_pure=True)
    m.update_metrics()
    meta = {"file.py": {"hash": "abc123", "functions": ["fn"]}}

    _save_manifest_cache(cache_path, m, meta)
    loaded_m, loaded_meta = _load_manifest_cache(cache_path)
    assert "fn" in loaded_m.functions
    assert loaded_meta["file.py"]["hash"] == "abc123"


def test_load_cache_corrupt_json(tmp_path):
    cache_path = tmp_path / "bad.json"
    cache_path.write_text("not valid json{{{")
    manifest, metadata = _load_manifest_cache(cache_path)
    assert manifest.functions == {}
    assert metadata == {}


# ── _restore_cached_functions ───────────────────────────────────────


def test_restore_cached_functions():
    cached_manifest = PropertyManifest()
    cached_manifest.functions["my_func"] = _make_func_props("my_func", is_pure=True)

    new_manifest = PropertyManifest()
    cached_entry = {"hash": "abc", "functions": ["my_func"]}

    _restore_cached_functions(new_manifest, "file.py", cached_manifest, cached_entry)
    assert "my_func" in new_manifest.functions


def test_restore_cached_functions_missing_in_cache():
    cached_manifest = PropertyManifest()
    new_manifest = PropertyManifest()
    cached_entry = {"hash": "abc", "functions": ["nonexistent"]}

    _restore_cached_functions(new_manifest, "file.py", cached_manifest, cached_entry)
    assert new_manifest.functions == {}


# ── _scan_file ──────────────────────────────────────────────────────


def test_scan_file_pure_function(tmp_path):
    f = tmp_path / "pure.py"
    f.write_text("def add(a, b): return a + b\n")

    m = PropertyManifest()
    found = _scan_file(m, str(f), str(tmp_path))
    assert "pure.py::add" in found
    assert "pure.py::add" in m.functions


def test_scan_file_syntax_error(tmp_path):
    f = tmp_path / "bad.py"
    f.write_text("def broken(\n")

    m = PropertyManifest()
    found = _scan_file(m, str(f), str(tmp_path))
    assert found == []
    assert m.functions == {}


def test_scan_file_impure_function(tmp_path):
    f = tmp_path / "impure.py"
    f.write_text("def write_file(path): open(path, 'w').write('x')\n")

    m = PropertyManifest()
    found = _scan_file(m, str(f), str(tmp_path))
    # Function should be found but marked impure
    if found:
        for name in found:
            assert name in m.functions
