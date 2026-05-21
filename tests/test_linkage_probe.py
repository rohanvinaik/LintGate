"""Tests for the in-process runtime linkage probe."""

from __future__ import annotations

from lintgate.specification.linkage_probe import (
    probe_fallback_callables,
    resolve_target_code,
)


def _write_module(tmp_path, body: str) -> str:
    path = tmp_path / "target_mod.py"
    path.write_text(body)
    return str(path)


class TestResolveTargetCode:
    def test_missing_file_returns_none(self, tmp_path):
        assert resolve_target_code(str(tmp_path / "nope.py"), "foo") is None

    def test_missing_symbol_returns_none(self, tmp_path):
        path = _write_module(tmp_path, "def bar():\n    return 1\n")
        assert resolve_target_code(path, "not_here") is None

    def test_resolves_function_code(self, tmp_path):
        path = _write_module(tmp_path, "def foo():\n    return 42\n")
        code = resolve_target_code(path, "foo")
        assert code is not None
        assert code.co_name == "foo"

    def test_resolves_qualified_method(self, tmp_path):
        path = _write_module(
            tmp_path,
            "class Foo:\n    def bar(self):\n        return 1\n",
        )
        code = resolve_target_code(path, "Foo.bar")
        assert code is not None
        assert code.co_name == "bar"

    def test_non_callable_symbol_returns_none(self, tmp_path):
        path = _write_module(tmp_path, "VALUE = 42\n")
        assert resolve_target_code(path, "VALUE") is None


class TestProbeFallbackCallables:
    def test_empty_callables_returns_empty(self, tmp_path):
        path = _write_module(tmp_path, "def foo():\n    return 1\n")
        verified, probed = probe_fallback_callables(path, "foo", [])
        assert verified == []
        assert probed == 0

    def test_unresolvable_target_passes_through(self, tmp_path):
        path = _write_module(tmp_path, "def bar():\n    return 1\n")
        fns = [lambda: None]
        verified, probed = probe_fallback_callables(path, "nope", fns)
        assert verified == fns
        assert probed == 0

    def test_filters_non_hitting_callables(self, tmp_path):
        path = _write_module(tmp_path, "def target():\n    return 42\n")
        # Test that doesn't call target is not verified
        non_hitter_ran = [False]

        def non_hitter():
            non_hitter_ran[0] = True  # executed but doesn't touch target

        # Test that imports and calls target IS verified
        import importlib.util

        spec = importlib.util.spec_from_file_location("probe_mod", path)
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        def hitter():
            mod.target()

        verified, probed = probe_fallback_callables(path, "target", [non_hitter, hitter])
        assert probed == 2
        assert hitter in verified
        assert non_hitter not in verified
        assert non_hitter_ran[0] is True  # probe ran the non-hitter too

    def test_bounded_by_max_probe(self, tmp_path):
        path = _write_module(tmp_path, "def target():\n    return 0\n")
        fns = [lambda i=i: None for i in range(10)]
        verified, probed = probe_fallback_callables(path, "target", fns, max_probe=3)
        assert probed == 3
        # Tail (indices 3..9, seven callables) is passed through unfiltered
        assert len(verified) >= 7

    def test_swallows_test_exceptions(self, tmp_path):
        path = _write_module(tmp_path, "def target():\n    return 0\n")

        def raising_test():
            raise AssertionError("this is fine")

        # Probe should not propagate the exception
        verified, probed = probe_fallback_callables(path, "target", [raising_test])
        assert probed == 1
        # Didn't enter target — not verified
        assert verified == []
