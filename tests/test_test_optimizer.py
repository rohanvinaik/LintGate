"""Tests for lintgate/testing/test_optimizer.py — triage and compact."""

from __future__ import annotations

import ast
import textwrap

from lintgate.testing.test_optimizer import (
    CompactResult,
    FunctionTriage,
    TriageResult,
    _is_fixture,
    _referenced_names,
    _trace_needed_fixtures,
    compose_compacted_file,
    parse_test_module,
    run_compact,
    run_triage,
)


# ── ParsedTestModule ─────────────────────────────────────────────


def test_parse_module_level_tests(tmp_path):
    """Module-level test functions are captured."""
    f = tmp_path / "test_example.py"
    f.write_text(textwrap.dedent('''\
        """Test module."""
        import pytest

        def helper():
            return 42

        def test_basic():
            assert helper() == 42

        def test_advanced():
            assert True
    '''))
    module = parse_test_module(str(f))
    assert module is not None
    assert len(module.test_functions) == 2
    assert "test_basic" in module.test_functions
    assert "test_advanced" in module.test_functions
    assert "helper" in module.helpers
    assert module.docstring == "Test module."


def test_parse_class_based_tests(tmp_path):
    """Class-based test methods are indexed in class_test_methods."""
    f = tmp_path / "test_cls.py"
    f.write_text(textwrap.dedent('''\
        class TestFoo:
            def test_alpha(self):
                assert True

            def test_beta(self):
                assert True

            def helper(self):
                pass
    '''))
    module = parse_test_module(str(f))
    assert module is not None
    assert len(module.test_classes) == 1
    assert "TestFoo" in module.test_classes
    assert len(module.class_test_methods) == 2
    assert "test_alpha" in module.class_test_methods
    assert "test_beta" in module.class_test_methods


def test_parse_fixtures(tmp_path):
    """Fixtures are identified by decorator."""
    f = tmp_path / "test_fix.py"
    f.write_text(textwrap.dedent('''\
        import pytest

        @pytest.fixture
        def my_fixture():
            return 42

        def test_uses_fixture(my_fixture):
            assert my_fixture == 42
    '''))
    module = parse_test_module(str(f))
    assert module is not None
    assert "my_fixture" in module.fixtures
    assert "test_uses_fixture" in module.test_functions


def test_all_test_names_combines_both(tmp_path):
    """all_test_names() includes both module-level and class methods."""
    f = tmp_path / "test_combined.py"
    f.write_text(textwrap.dedent('''\
        def test_standalone():
            pass

        class TestGroup:
            def test_method(self):
                pass
    '''))
    module = parse_test_module(str(f))
    assert module is not None
    names = module.all_test_names()
    assert "test_standalone" in names
    assert "test_method" in names
    assert len(names) == 2


# ── _is_fixture ──────────────────────────────────────────────────


def test_is_fixture_decorator():
    code = "import pytest\n@pytest.fixture\ndef f(): pass"
    tree = ast.parse(code)
    func = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)][0]
    assert _is_fixture(func) is True


def test_is_fixture_call_decorator():
    code = "import pytest\n@pytest.fixture(scope='module')\ndef f(): pass"
    tree = ast.parse(code)
    func = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)][0]
    assert _is_fixture(func) is True


def test_is_not_fixture():
    code = "def f(): pass"
    tree = ast.parse(code)
    func = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)][0]
    assert _is_fixture(func) is False


# ── _referenced_names ────────────────────────────────────────────


def test_referenced_names_simple():
    code = "x + y"
    tree = ast.parse(code, mode="eval")
    names = _referenced_names(tree)
    assert "x" in names
    assert "y" in names


def test_referenced_names_attribute():
    code = "foo.bar.baz"
    tree = ast.parse(code, mode="eval")
    names = _referenced_names(tree)
    assert "foo" in names


# ── _trace_needed_fixtures ───────────────────────────────────────


def test_trace_fixtures_transitive(tmp_path):
    """Fixtures used by other fixtures are included transitively."""
    f = tmp_path / "test_trans.py"
    f.write_text(textwrap.dedent('''\
        import pytest

        @pytest.fixture
        def base():
            return 1

        @pytest.fixture
        def derived(base):
            return base + 1

        def test_uses_derived(derived):
            assert derived == 2
    '''))
    module = parse_test_module(str(f))
    assert module is not None
    needed = _trace_needed_fixtures({"test_uses_derived"}, module)
    assert "derived" in needed
    assert "base" in needed


# ── compose_compacted_file ───────────────────────────────────────


def test_compose_keeps_surviving_tests(tmp_path):
    """Only surviving test functions appear in output."""
    f = tmp_path / "test_compose.py"
    f.write_text(textwrap.dedent('''\
        """Test module."""
        import pytest

        def test_keep():
            assert True

        def test_drop():
            assert False
    '''))
    module = parse_test_module(str(f))
    assert module is not None
    content = compose_compacted_file(module, {"test_keep"})
    assert "test_keep" in content
    assert "test_drop" not in content


def test_compose_preserves_fixture(tmp_path):
    """Fixtures used by surviving tests are preserved."""
    f = tmp_path / "test_fix_compose.py"
    f.write_text(textwrap.dedent('''\
        import pytest

        @pytest.fixture
        def data():
            return [1, 2, 3]

        def test_len(data):
            assert len(data) == 3

        def test_sum():
            assert 1 + 1 == 2
    '''))
    module = parse_test_module(str(f))
    assert module is not None
    content = compose_compacted_file(module, {"test_len"})
    assert "def data():" in content
    assert "test_len" in content
    assert "test_sum" not in content


def test_compose_trims_class_methods(tmp_path):
    """Only surviving methods within a class are kept."""
    f = tmp_path / "test_cls_compose.py"
    f.write_text(textwrap.dedent('''\
        class TestGroup:
            def test_keep(self):
                assert True

            def test_drop(self):
                assert False
    '''))
    module = parse_test_module(str(f))
    assert module is not None
    content = compose_compacted_file(module, {"test_keep"})
    assert "test_keep" in content
    assert "test_drop" not in content
    assert "class TestGroup" in content


def test_compose_keeps_whole_class_when_all_survive(tmp_path):
    """When all methods survive, the class is kept unchanged."""
    f = tmp_path / "test_cls_all.py"
    f.write_text(textwrap.dedent('''\
        class TestAll:
            def test_a(self):
                assert True

            def test_b(self):
                assert True
    '''))
    module = parse_test_module(str(f))
    assert module is not None
    content = compose_compacted_file(module, {"test_a", "test_b"})
    assert "test_a" in content
    assert "test_b" in content


def test_compose_preserves_helper(tmp_path):
    """Helper functions called by surviving tests are preserved."""
    f = tmp_path / "test_helper.py"
    f.write_text(textwrap.dedent('''\
        def make_data():
            return 42

        def test_uses_helper():
            assert make_data() == 42

        def test_no_helper():
            assert True
    '''))
    module = parse_test_module(str(f))
    assert module is not None
    content = compose_compacted_file(module, {"test_uses_helper"})
    assert "make_data" in content
    assert "test_uses_helper" in content
    assert "test_no_helper" not in content


# ── TriageResult ─────────────────────────────────────────────────


def test_triage_result_summary():
    triage = TriageResult(
        source_file="src/core.py",
        analysis_id="abc123",
        functions=[
            FunctionTriage("f1", sigma=5, total_mutants=5, killed=5, killing_tests=["t1"]),
        ],
        killing_set={"t1"},
        total_tests_mapped=10,
        kill_rate=1.0,
    )
    summary = triage.summary()
    assert "10 tests mapped" in summary
    assert "1 in minimum killing set" in summary
    assert "9 redundant" in summary


# ── run_triage ───────────────────────────────────────────────────


def test_run_triage_no_analysis(tmp_path):
    """Returns None when no mutation analysis exists."""
    result = run_triage(str(tmp_path), "nonexistent.py")
    assert result is None


# ── run_compact ──────────────────────────────────────────────────


def test_run_compact_no_triage(tmp_path):
    """Returns None when no triage data available."""
    result = run_compact(str(tmp_path), "nonexistent.py")
    assert result is None


def test_run_compact_empty_killing_set(tmp_path):
    """Returns None when killing set is empty."""
    triage = TriageResult(
        source_file="src.py",
        analysis_id="x",
        functions=[],
        killing_set=set(),
        total_tests_mapped=0,
        kill_rate=0.0,
    )
    result = run_compact(str(tmp_path), "src.py", triage=triage)
    assert result is None


# ── CompactResult ────────────────────────────────────────────────


def test_compact_result_fields():
    r = CompactResult(
        source_file="src.py",
        test_file="tests/test_src.py",
        original_test_count=50,
        original_lines=500,
        compacted_test_count=5,
        compacted_lines=60,
        content="# compacted",
    )
    assert r.original_test_count == 50
    assert r.compacted_test_count == 5
    assert r.content == "# compacted"
