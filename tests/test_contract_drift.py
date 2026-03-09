"""Tests for contract drift detector — TEFF010.

Verifies detection of return arity changes, parameter changes,
and affected test call site identification.
"""

from __future__ import annotations

import textwrap

from lintgate.channels.contract_drift_detector import (
    AffectedTestSite,
    SignatureChange,
    _build_advisory,
    _extract_function_params,
    _extract_function_return_arities,
    analyze_contract_drift,
    detect_param_changes,
    detect_return_arity_change,
    find_affected_test_sites,
)


class TestExtractFunctionReturnArities:
    def test_tuple_return(self):
        source = textwrap.dedent("""\
            def func():
                return a, b, c
        """)
        import ast

        tree = ast.parse(source)
        arities = _extract_function_return_arities(tree)
        assert arities["func"] == 3

    def test_annotated_tuple_return(self):
        source = textwrap.dedent("""\
            def func() -> tuple[int, str, float]:
                return 1, "x", 0.5
        """)
        import ast

        tree = ast.parse(source)
        arities = _extract_function_return_arities(tree)
        assert arities["func"] == 3

    def test_scalar_return_excluded(self):
        source = textwrap.dedent("""\
            def func():
                return 42
        """)
        import ast

        tree = ast.parse(source)
        arities = _extract_function_return_arities(tree)
        assert "func" not in arities

    def test_no_return_excluded(self):
        source = textwrap.dedent("""\
            def func():
                pass
        """)
        import ast

        tree = ast.parse(source)
        arities = _extract_function_return_arities(tree)
        assert "func" not in arities

    def test_annotation_takes_precedence(self):
        source = textwrap.dedent("""\
            def func() -> tuple[int, str]:
                return 1, "x", 0.5
        """)
        import ast

        tree = ast.parse(source)
        arities = _extract_function_return_arities(tree)
        # Annotation says 2, even though return has 3 elements
        assert arities["func"] == 2


class TestExtractFunctionParams:
    def test_basic_params(self):
        source = textwrap.dedent("""\
            def func(a, b, c):
                pass
        """)
        import ast

        tree = ast.parse(source)
        params = _extract_function_params(tree)
        assert params["func"] == {"a", "b", "c"}

    def test_self_excluded(self):
        source = textwrap.dedent("""\
            class Foo:
                def method(self, x, y):
                    pass
        """)
        import ast

        tree = ast.parse(source)
        params = _extract_function_params(tree)
        assert params["method"] == {"x", "y"}

    def test_kwargs(self):
        source = textwrap.dedent("""\
            def func(a, *args, **kwargs):
                pass
        """)
        import ast

        tree = ast.parse(source)
        params = _extract_function_params(tree)
        assert params["func"] == {"a", "*args", "**kwargs"}


class TestDetectReturnArityChange:
    def test_arity_increase(self, tmp_path):
        old = textwrap.dedent("""\
            def finalize():
                return findings, metrics, score
        """)
        new = textwrap.dedent("""\
            def finalize():
                return findings, metrics, score, count
        """)
        filepath = str(tmp_path / "module.py")
        changes = detect_return_arity_change(filepath, old, new)
        assert len(changes) == 1
        assert changes[0].function == "finalize"
        assert changes[0].change_type == "return_arity"
        assert changes[0].old_value == 3
        assert changes[0].new_value == 4

    def test_arity_decrease(self, tmp_path):
        old = textwrap.dedent("""\
            def func():
                return a, b, c
        """)
        new = textwrap.dedent("""\
            def func():
                return a, b
        """)
        filepath = str(tmp_path / "module.py")
        changes = detect_return_arity_change(filepath, old, new)
        assert len(changes) == 1
        assert changes[0].old_value == 3
        assert changes[0].new_value == 2

    def test_no_change(self, tmp_path):
        source = textwrap.dedent("""\
            def func():
                return a, b, c
        """)
        filepath = str(tmp_path / "module.py")
        changes = detect_return_arity_change(filepath, source, source)
        assert len(changes) == 0

    def test_annotation_change(self, tmp_path):
        old = textwrap.dedent("""\
            def func() -> tuple[int, str]:
                return 1, "x"
        """)
        new = textwrap.dedent("""\
            def func() -> tuple[int, str, float]:
                return 1, "x", 0.5
        """)
        filepath = str(tmp_path / "module.py")
        changes = detect_return_arity_change(filepath, old, new)
        assert len(changes) == 1
        assert changes[0].old_value == 2
        assert changes[0].new_value == 3


class TestDetectParamChanges:
    def test_param_added(self, tmp_path):
        old = "def func(a, b): pass"
        new = "def func(a, b, c): pass"
        filepath = str(tmp_path / "module.py")
        changes = detect_param_changes(filepath, old, new)
        assert len(changes) == 1
        assert changes[0].change_type == "param_added"

    def test_param_removed(self, tmp_path):
        old = "def func(a, b, c): pass"
        new = "def func(a, b): pass"
        filepath = str(tmp_path / "module.py")
        changes = detect_param_changes(filepath, old, new)
        assert len(changes) == 1
        assert changes[0].change_type == "param_removed"

    def test_no_change(self, tmp_path):
        source = "def func(a, b): pass"
        filepath = str(tmp_path / "module.py")
        changes = detect_param_changes(filepath, source, source)
        assert len(changes) == 0


class TestFindAffectedTestSites:
    def test_finds_unpack_mismatch(self, tmp_path):
        test_file = tmp_path / "test_example.py"
        test_file.write_text(
            textwrap.dedent("""\
            def test_it():
                a, b, c = obj.finalize()
                assert a == 1
        """)
        )

        change = SignatureChange(
            module="mymod",
            function="finalize",
            file="mymod.py",
            change_type="return_arity",
            old_value=3,
            new_value=4,
        )

        sites = find_affected_test_sites(change, [str(test_file)])
        assert len(sites) == 1
        assert sites[0].unpacking_arity == 3
        assert sites[0].line == 2

    def test_matching_arity_not_flagged(self, tmp_path):
        test_file = tmp_path / "test_example.py"
        test_file.write_text(
            textwrap.dedent("""\
            def test_it():
                a, b, c, d = obj.finalize()
        """)
        )

        change = SignatureChange(
            module="mymod",
            function="finalize",
            file="mymod.py",
            change_type="return_arity",
            old_value=3,
            new_value=4,
        )

        # Arity 4 matches new, not old — should not be flagged
        sites = find_affected_test_sites(change, [str(test_file)])
        assert len(sites) == 0

    def test_finds_call_sites_for_param_change(self, tmp_path):
        test_file = tmp_path / "test_example.py"
        test_file.write_text(
            textwrap.dedent("""\
            def test_it():
                result = compute(1, 2)
                other = compute(3, 4)
        """)
        )

        change = SignatureChange(
            module="mymod",
            function="compute",
            file="mymod.py",
            change_type="param_added",
            old_value=["a", "b"],
            new_value=["a", "b", "c"],
        )

        sites = find_affected_test_sites(change, [str(test_file)])
        assert len(sites) == 2

    def test_different_function_not_flagged(self, tmp_path):
        test_file = tmp_path / "test_example.py"
        test_file.write_text(
            textwrap.dedent("""\
            def test_it():
                a, b, c = other_func()
        """)
        )

        change = SignatureChange(
            module="mymod",
            function="finalize",
            file="mymod.py",
            change_type="return_arity",
            old_value=3,
            new_value=4,
        )

        sites = find_affected_test_sites(change, [str(test_file)])
        assert len(sites) == 0


class TestAnalyzeContractDrift:
    def test_full_analysis(self, tmp_path):
        old = textwrap.dedent("""\
            def finalize():
                return findings, metrics, score
        """)
        new = textwrap.dedent("""\
            def finalize():
                return findings, metrics, score, count
        """)

        test_file = tmp_path / "test_mod.py"
        test_file.write_text(
            textwrap.dedent("""\
            def test_it():
                a, b, c = coord.finalize()
        """)
        )

        filepath = str(tmp_path / "module.py")
        results = analyze_contract_drift(filepath, old, new, [str(test_file)])
        assert len(results) == 1
        assert results[0].change.function == "finalize"
        assert len(results[0].affected_sites) == 1
        assert results[0].advisory  # non-empty advisory string

    def test_no_affected_sites(self, tmp_path):
        old = "def func(): return a, b"
        new = "def func(): return a, b, c"

        test_file = tmp_path / "test_mod.py"
        test_file.write_text("def test_it(): pass\n")

        filepath = str(tmp_path / "module.py")
        results = analyze_contract_drift(filepath, old, new, [str(test_file)])
        # Change detected but no affected sites
        assert len(results) == 1
        assert len(results[0].affected_sites) == 0


class TestBuildAdvisory:
    def test_return_arity_advisory(self):
        change = SignatureChange(
            module="mod",
            function="func",
            file="mod.py",
            change_type="return_arity",
            old_value=3,
            new_value=4,
        )
        sites = [AffectedTestSite(test_file="test_x.py", line=10, unpacking_arity=3)]
        advisory = _build_advisory(change, sites)
        assert "func()" in advisory
        assert "3" in advisory
        assert "4" in advisory
        assert "1 test site" in advisory

    def test_param_added_advisory(self):
        change = SignatureChange(
            module="mod",
            function="func",
            file="mod.py",
            change_type="param_added",
            old_value=["a", "b"],
            new_value=["a", "b", "c"],
        )
        sites = [AffectedTestSite(test_file="test_x.py", line=5)]
        advisory = _build_advisory(change, sites)
        assert "gained parameter" in advisory
        assert "c" in advisory

    def test_empty_advisory_for_no_sites(self):
        change = SignatureChange(
            module="mod",
            function="func",
            file="mod.py",
            change_type="return_arity",
            old_value=2,
            new_value=3,
        )
        advisory = _build_advisory(change, [])
        assert advisory == ""


# ── SPEC010: find_affected_test_sites specification ───────────────────


class TestFindAffectedTestSitesSpec:
    """Specify exact behavioral contract for find_affected_test_sites."""

    def test_nonexistent_file_skipped_gracefully(self, tmp_path):
        change = SignatureChange(
            module="mod",
            function="func",
            file="mod.py",
            change_type="return_arity",
            old_value=2,
            new_value=3,
        )
        sites = find_affected_test_sites(change, [str(tmp_path / "nonexistent.py")])
        assert sites == []

    def test_syntax_error_file_skipped_gracefully(self, tmp_path):
        bad_file = tmp_path / "test_bad.py"
        bad_file.write_text("def broken(:\n", encoding="utf-8")
        change = SignatureChange(
            module="mod",
            function="func",
            file="mod.py",
            change_type="return_arity",
            old_value=2,
            new_value=3,
        )
        sites = find_affected_test_sites(change, [str(bad_file)])
        assert sites == []

    def test_param_removed_finds_call_sites(self, tmp_path):
        test_file = tmp_path / "test_example.py"
        test_file.write_text(
            textwrap.dedent("""\
            def test_it():
                result = compute(1, 2, 3)
        """),
            encoding="utf-8",
        )
        change = SignatureChange(
            module="mod",
            function="compute",
            file="mod.py",
            change_type="param_removed",
            old_value=["a", "b", "c"],
            new_value=["a", "b"],
        )
        sites = find_affected_test_sites(change, [str(test_file)])
        assert len(sites) == 1
        assert sites[0].test_file == str(test_file)
        assert sites[0].line == 2
        assert sites[0].call_expression == "compute"

    def test_multiple_files_aggregated(self, tmp_path):
        f1 = tmp_path / "test_a.py"
        f2 = tmp_path / "test_b.py"
        f1.write_text("def test_a():\n    x = compute(1)\n", encoding="utf-8")
        f2.write_text("def test_b():\n    y = compute(2)\n", encoding="utf-8")
        change = SignatureChange(
            module="mod",
            function="compute",
            file="mod.py",
            change_type="param_added",
            old_value=["a"],
            new_value=["a", "b"],
        )
        sites = find_affected_test_sites(change, [str(f1), str(f2)])
        assert len(sites) == 2
        test_files = {s.test_file for s in sites}
        assert str(f1) in test_files
        assert str(f2) in test_files

    def test_return_arity_exact_site_attributes(self, tmp_path):
        test_file = tmp_path / "test_example.py"
        test_file.write_text(
            textwrap.dedent("""\
            def test_it():
                a, b = obj.finalize()
        """),
            encoding="utf-8",
        )
        change = SignatureChange(
            module="mod",
            function="finalize",
            file="mod.py",
            change_type="return_arity",
            old_value=2,
            new_value=3,
        )
        sites = find_affected_test_sites(change, [str(test_file)])
        assert len(sites) == 1
        site = sites[0]
        assert site.test_file == str(test_file)
        assert site.line == 2
        assert site.unpacking_arity == 2
        assert site.call_expression == "obj.finalize"
