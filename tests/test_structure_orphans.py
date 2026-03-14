"""Tests for lintgate/channels/structure/orphans.py.

Covers orphan detection, re-export parsing, exclusion rules,
and classification logic.
"""

from __future__ import annotations

import ast
from typing import cast

from lintgate.channels.structure.orphans import (
    _check_orphans,
    _classify_orphan,
    _detect_reexports,
    _has_entrypoint_marker,
    _is_dynamic_import_call,
    _is_in_excluded_dir,
    _is_orphan_excluded,
    _parse_all_assignment,
    _parse_import_from_reexport,
)

# ── _parse_import_from_reexport ──────────────────────────────────


class TestParseImportFromReexport:
    def test_named_import(self):
        node = cast("ast.ImportFrom", ast.parse("from .sub import Foo").body[0])
        reexports: dict[str, str] = {}
        _parse_import_from_reexport(node, reexports)
        assert reexports["sub"] == "definite"

    def test_star_import(self):
        node = cast("ast.ImportFrom", ast.parse("from .sub import *").body[0])
        reexports: dict[str, str] = {}
        _parse_import_from_reexport(node, reexports)
        assert reexports["sub"] == "unknown"

    def test_star_does_not_overwrite_definite(self):
        node_named = cast("ast.ImportFrom", ast.parse("from .sub import Foo").body[0])
        node_star = cast("ast.ImportFrom", ast.parse("from .sub import *").body[0])
        reexports: dict[str, str] = {}
        _parse_import_from_reexport(node_named, reexports)
        _parse_import_from_reexport(node_star, reexports)
        assert reexports["sub"] == "definite"

    def test_absolute_import_ignored(self):
        node = cast("ast.ImportFrom", ast.parse("from os.path import join").body[0])
        reexports: dict[str, str] = {}
        _parse_import_from_reexport(node, reexports)
        assert reexports == {}

    def test_no_module_ignored(self):
        # `from . import something` has module=None
        node = cast("ast.ImportFrom", ast.parse("from . import something").body[0])
        reexports: dict[str, str] = {}
        _parse_import_from_reexport(node, reexports)
        assert reexports == {}


# ── _parse_all_assignment ────────────────────────────────────────


class TestParseAllAssignment:
    def test_list_assignment(self):
        node = cast("ast.Assign", ast.parse('__all__ = ["Foo", "Bar"]').body[0])
        reexports: dict[str, str] = {}
        _parse_all_assignment(node, reexports)
        assert reexports == {"Foo": "definite", "Bar": "definite"}

    def test_tuple_assignment(self):
        node = cast("ast.Assign", ast.parse('__all__ = ("Foo",)').body[0])
        reexports: dict[str, str] = {}
        _parse_all_assignment(node, reexports)
        assert reexports == {"Foo": "definite"}

    def test_non_all_ignored(self):
        node = cast("ast.Assign", ast.parse('exports = ["Foo"]').body[0])
        reexports: dict[str, str] = {}
        _parse_all_assignment(node, reexports)
        assert reexports == {}

    def test_non_list_value_ignored(self):
        node = cast("ast.Assign", ast.parse("__all__ = get_exports()").body[0])
        reexports: dict[str, str] = {}
        _parse_all_assignment(node, reexports)
        assert reexports == {}


# ── _is_dynamic_import_call ──────────────────────────────────────


class TestIsDynamicImportCall:
    def test_importlib_import_module(self):
        node = cast(
            "ast.Call", cast("ast.Expr", ast.parse('importlib.import_module("foo")').body[0]).value
        )
        assert _is_dynamic_import_call(node) is True

    def test_dunder_import(self):
        node = cast("ast.Call", cast("ast.Expr", ast.parse('__import__("foo")').body[0]).value)
        assert _is_dynamic_import_call(node) is True

    def test_regular_call(self):
        node = cast("ast.Call", cast("ast.Expr", ast.parse('print("hello")').body[0]).value)
        assert _is_dynamic_import_call(node) is False


# ── _is_orphan_excluded ──────────────────────────────────────────


class TestIsOrphanExcluded:
    def test_init_file_excluded(self):
        assert _is_orphan_excluded("/p/__init__.py", "pkg", "/p") is True

    def test_main_excluded(self):
        assert _is_orphan_excluded("/p/main.py", "pkg.main", "/p") is True

    def test_conftest_excluded(self):
        assert _is_orphan_excluded("/p/conftest.py", "pkg.conftest", "/p") is True

    def test_top_level_excluded(self):
        # No dot in module = top-level script
        assert _is_orphan_excluded("/p/script.py", "script", "/p") is True

    def test_test_prefix_excluded(self):
        assert _is_orphan_excluded("/p/pkg/test_foo.py", "pkg.test_foo", "/p") is True

    def test_test_suffix_excluded(self):
        assert _is_orphan_excluded("/p/pkg/foo_test.py", "pkg.foo_test", "/p") is True

    def test_test_dir_excluded(self):
        assert _is_orphan_excluded("/p/tests/foo.py", "tests.foo", "/p") is True

    def test_regular_module_not_excluded(self):
        assert _is_orphan_excluded("/p/pkg/utils.py", "pkg.utils", "/p") is False


# ── _is_in_excluded_dir ──────────────────────────────────────────


class TestIsInExcludedDir:
    def test_migrations_excluded(self):
        assert _is_in_excluded_dir(("migrations",), None) is True

    def test_plugins_excluded(self):
        assert _is_in_excluded_dir(("linters",), None) is True

    def test_extra_exclude(self):
        assert _is_in_excluded_dir(("custom",), frozenset({"custom"})) is True

    def test_normal_dir_not_excluded(self):
        assert _is_in_excluded_dir(("src", "lib"), None) is False


# ── _has_entrypoint_marker ───────────────────────────────────────


class TestHasEntrypointMarker:
    def test_shebang(self, tmp_path):
        f = tmp_path / "script.py"
        f.write_text("#!/usr/bin/env python\nprint('hi')")
        assert _has_entrypoint_marker(str(f)) is True

    def test_main_guard(self, tmp_path):
        f = tmp_path / "app.py"
        f.write_text('if __name__ == "__main__":\n    main()')
        assert _has_entrypoint_marker(str(f)) is True

    def test_normal_module(self, tmp_path):
        f = tmp_path / "utils.py"
        f.write_text("def helper(): pass")
        assert _has_entrypoint_marker(str(f)) is False

    def test_nonexistent_file(self):
        assert _has_entrypoint_marker("/nonexistent/file.py") is False


# ── _detect_reexports ────────────────────────────────────────────


class TestDetectReexports:
    def test_named_imports(self, tmp_path):
        init = tmp_path / "__init__.py"
        init.write_text("from .sub import Foo\nfrom .other import Bar")
        result = _detect_reexports(str(init), str(tmp_path))
        assert result["sub"] == "definite"
        assert result["other"] == "definite"

    def test_star_import(self, tmp_path):
        init = tmp_path / "__init__.py"
        init.write_text("from .sub import *")
        result = _detect_reexports(str(init), str(tmp_path))
        assert result["sub"] == "unknown"

    def test_all_assignment(self, tmp_path):
        init = tmp_path / "__init__.py"
        init.write_text('__all__ = ["Foo", "Bar"]')
        result = _detect_reexports(str(init), str(tmp_path))
        assert result == {"Foo": "definite", "Bar": "definite"}

    def test_dynamic_import(self, tmp_path):
        init = tmp_path / "__init__.py"
        init.write_text('importlib.import_module("sub")')
        result = _detect_reexports(str(init), str(tmp_path))
        assert "*" in result

    def test_syntax_error_returns_empty(self, tmp_path):
        init = tmp_path / "__init__.py"
        init.write_text("def (broken")
        result = _detect_reexports(str(init), str(tmp_path))
        assert result == {}

    def test_nonexistent_file(self):
        result = _detect_reexports("/no/such/file.py", "/no")
        assert result == {}


# ── _classify_orphan ─────────────────────────────────────────────


class TestClassifyOrphan:
    def test_definite_reexport_returns_none(self, tmp_path):
        f = tmp_path / "sub.py"
        f.write_text("x = 1")
        reexport_map = {str(tmp_path): {"sub": "definite"}}
        result = _classify_orphan("pkg.sub", str(f), str(tmp_path), reexport_map)
        assert result is None

    def test_unknown_reexport_low_confidence(self, tmp_path):
        f = tmp_path / "sub.py"
        f.write_text("x = 1")
        reexport_map = {str(tmp_path): {"sub": "unknown"}}
        result = _classify_orphan("pkg.sub", str(f), str(tmp_path), reexport_map)
        assert result is not None
        assert result.confidence == 0.3
        assert result.evidence["reexport_status"] == "unknown"

    def test_true_orphan_higher_confidence(self, tmp_path):
        f = tmp_path / "orphan.py"
        f.write_text("x = 1")
        result = _classify_orphan("pkg.orphan", str(f), str(tmp_path), {})
        assert result is not None
        assert result.confidence == 0.6
        assert result.kind == "STRUCT003"

    def test_wildcard_reexport_inherits_unknown(self, tmp_path):
        f = tmp_path / "mod.py"
        f.write_text("x = 1")
        reexport_map = {str(tmp_path): {"*": "unknown"}}
        result = _classify_orphan("pkg.mod", str(f), str(tmp_path), reexport_map)
        assert result is not None
        assert result.confidence == 0.3


# ── _check_orphans ───────────────────────────────────────────────


class TestCheckOrphans:
    def test_imported_module_not_orphan(self, tmp_path):
        f = tmp_path / "used.py"
        f.write_text("x = 1")
        import_graph = {"main": {"pkg.used"}}
        file_map = {"pkg.used": str(f)}
        result = _check_orphans([str(f)], import_graph, file_map, str(tmp_path))
        assert result == []

    def test_unimported_module_is_orphan(self, tmp_path):
        f = tmp_path / "pkg" / "unused.py"
        f.parent.mkdir()
        f.write_text("x = 1")
        import_graph: dict[str, set[str]] = {}
        file_map = {"pkg.unused": str(f)}
        result = _check_orphans([str(f)], import_graph, file_map, str(tmp_path))
        assert len(result) == 1
        assert result[0].kind == "STRUCT003"

    def test_parent_package_counts_as_referenced(self, tmp_path):
        """If pkg.sub.mod is imported, pkg.sub is considered referenced."""
        f = tmp_path / "pkg" / "sub.py"
        f.parent.mkdir(parents=True)
        f.write_text("x = 1")
        import_graph = {"main": {"pkg.sub.mod"}}
        file_map = {"pkg.sub": str(f)}
        result = _check_orphans([str(f)], import_graph, file_map, str(tmp_path))
        assert result == []
