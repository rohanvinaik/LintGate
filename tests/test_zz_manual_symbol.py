from lintgate.channels.symbol_coverage import _collect_changed_symbols
from lintgate.linters.performance_checks.manifest import build_manifest


def test_collect_changed_symbols():
    # Pass empty lists to hit the lines
    result = _collect_changed_symbols([], "dummy_root", "HEAD", [], set())
    assert result is None


def test_build_manifest():
    manifest = build_manifest(".", [])
    assert manifest is not None
    assert manifest.pure_count == 0
