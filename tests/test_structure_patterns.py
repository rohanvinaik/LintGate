"""Tests for structure pattern detection — STRUCT005 + STRUCT006 (Deliverable B).

Covers: package candidate detection, cross-file pattern detection,
pattern fingerprinting, and performance guardrails.
"""

from __future__ import annotations

import textwrap

from lintgate.channels.structure_patterns import (
    _fingerprint_function,
    _get_call_name_simple,
    check_cross_file_patterns,
    check_package_candidates,
)

# ── STRUCT005: Package Candidate Detection ────────────────────────────


class TestPackageCandidateBasic:
    """3 files with shared prefix + import edges → emit STRUCT005."""

    def test_three_files_shared_prefix_with_imports(self, tmp_path) -> None:
        """Core scenario: 3 auth_*.py files with import edges → STRUCT005."""
        d = tmp_path / "src"
        d.mkdir()
        (d / "auth_login.py").write_text("import auth_session\n")
        (d / "auth_session.py").write_text("pass\n")
        (d / "auth_register.py").write_text("import auth_login\n")

        py_files = [str(d / f) for f in ["auth_login.py", "auth_session.py", "auth_register.py"]]
        # Simplified import graph: module names match stems
        import_graph = {
            "auth_login": {"auth_session"},
            "auth_session": set(),
            "auth_register": {"auth_login"},
        }
        file_map = {
            "auth_login": str(d / "auth_login.py"),
            "auth_session": str(d / "auth_session.py"),
            "auth_register": str(d / "auth_register.py"),
        }

        findings = check_package_candidates(py_files, import_graph, file_map, str(tmp_path))
        assert len(findings) == 1
        f = findings[0]
        assert f.kind == "STRUCT005"
        assert f.severity == "informational"
        assert f.confidence == 0.6
        assert f.evidence["prefix"] == "auth"
        assert f.evidence["import_edges"] >= 1
        assert len(f.evidence["files"]) == 3


class TestPackageCandidateNoImports:
    """Shared prefix but no edges → no emit."""

    def test_shared_prefix_no_import_edges(self, tmp_path) -> None:
        d = tmp_path / "src"
        d.mkdir()
        (d / "data_clean.py").write_text("pass\n")
        (d / "data_load.py").write_text("pass\n")
        (d / "data_transform.py").write_text("pass\n")

        py_files = [str(d / f) for f in ["data_clean.py", "data_load.py", "data_transform.py"]]
        import_graph = {
            "data_clean": set(),
            "data_load": set(),
            "data_transform": set(),
        }
        file_map = {
            "data_clean": str(d / "data_clean.py"),
            "data_load": str(d / "data_load.py"),
            "data_transform": str(d / "data_transform.py"),
        }

        findings = check_package_candidates(py_files, import_graph, file_map, str(tmp_path))
        assert len(findings) == 0


class TestPackageCandidateTooFew:
    """2 files → below min_files threshold → no emit."""

    def test_two_files_below_minimum(self, tmp_path) -> None:
        d = tmp_path / "src"
        d.mkdir()
        (d / "util_a.py").write_text("import util_b\n")
        (d / "util_b.py").write_text("pass\n")

        py_files = [str(d / f) for f in ["util_a.py", "util_b.py"]]
        import_graph = {"util_a": {"util_b"}, "util_b": set()}
        file_map = {
            "util_a": str(d / "util_a.py"),
            "util_b": str(d / "util_b.py"),
        }

        findings = check_package_candidates(py_files, import_graph, file_map, str(tmp_path))
        assert len(findings) == 0


class TestPackageCandidateAlreadyExists:
    """Subdirectory with __init__.py already exists → no emit."""

    def test_existing_package_suppresses(self, tmp_path) -> None:
        d = tmp_path / "src"
        d.mkdir()
        (d / "cache_store.py").write_text("import cache_policy\n")
        (d / "cache_policy.py").write_text("pass\n")
        (d / "cache_evict.py").write_text("import cache_store\n")
        # Package already exists
        pkg = d / "cache"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("")

        py_files = [str(d / f) for f in ["cache_store.py", "cache_policy.py", "cache_evict.py"]]
        import_graph = {
            "cache_store": {"cache_policy"},
            "cache_policy": set(),
            "cache_evict": {"cache_store"},
        }
        file_map = {
            "cache_store": str(d / "cache_store.py"),
            "cache_policy": str(d / "cache_policy.py"),
            "cache_evict": str(d / "cache_evict.py"),
        }

        findings = check_package_candidates(py_files, import_graph, file_map, str(tmp_path))
        assert len(findings) == 0


class TestPackageCandidateDifferentDirs:
    """Same prefix in different directories → independent groups, no false positive."""

    def test_different_directories_not_grouped(self, tmp_path) -> None:
        dir_a = tmp_path / "pkg_a"
        dir_b = tmp_path / "pkg_b"
        dir_a.mkdir()
        dir_b.mkdir()

        # Only 1 file per dir per prefix — not enough
        (dir_a / "net_client.py").write_text("pass\n")
        (dir_a / "net_server.py").write_text("pass\n")
        (dir_b / "net_proxy.py").write_text("pass\n")

        py_files = [
            str(dir_a / "net_client.py"),
            str(dir_a / "net_server.py"),
            str(dir_b / "net_proxy.py"),
        ]
        import_graph = {
            "net_client": {"net_server"},
            "net_server": set(),
            "net_proxy": set(),
        }
        file_map = {
            "net_client": str(dir_a / "net_client.py"),
            "net_server": str(dir_a / "net_server.py"),
            "net_proxy": str(dir_b / "net_proxy.py"),
        }

        findings = check_package_candidates(py_files, import_graph, file_map, str(tmp_path))
        # dir_a has only 2 net_* files (below 3), dir_b has only 1
        assert len(findings) == 0


class TestPackageCandidateMinFilesConfig:
    """Custom min_files parameter is respected."""

    def test_custom_min_files(self, tmp_path) -> None:
        d = tmp_path / "src"
        d.mkdir()
        (d / "api_routes.py").write_text("import api_auth\n")
        (d / "api_auth.py").write_text("pass\n")

        py_files = [str(d / f) for f in ["api_routes.py", "api_auth.py"]]
        import_graph = {"api_routes": {"api_auth"}, "api_auth": set()}
        file_map = {
            "api_routes": str(d / "api_routes.py"),
            "api_auth": str(d / "api_auth.py"),
        }

        # Default min_files=3 → no findings
        assert len(check_package_candidates(py_files, import_graph, file_map, str(tmp_path))) == 0

        # min_files=2 → should emit
        findings = check_package_candidates(
            py_files, import_graph, file_map, str(tmp_path), min_files=2
        )
        assert len(findings) == 1
        assert findings[0].evidence["prefix"] == "api"


# ── STRUCT006: Cross-File Pattern Detection ──────────────────────────


class TestCrossFilePatternConfigLoading:
    """3 files with expanduser+json.load+try/except → emit STRUCT006."""

    def _write_config_loading_function(self, filepath, func_name: str = "load_config") -> None:
        code = textwrap.dedent(f"""\
            import json
            import os

            def {func_name}():
                try:
                    path = os.path.expanduser("~/.config")
                    with open(path) as f:
                        return json.load(f)
                except FileNotFoundError:
                    return {{}}
        """)
        filepath.write_text(code)

    def test_config_loading_across_files(self, tmp_path) -> None:
        for i in range(3):
            d = tmp_path / f"mod_{i}.py"
            self._write_config_loading_function(d, f"load_config_{i}")

        py_files = [str(tmp_path / f"mod_{i}.py") for i in range(3)]
        findings = check_cross_file_patterns(py_files, str(tmp_path))

        config_findings = [f for f in findings if f.evidence.get("pattern") == "config_loading"]
        assert len(config_findings) == 1
        f = config_findings[0]
        assert f.kind == "STRUCT006"
        assert f.severity == "informational"
        assert f.confidence == 0.5
        assert f.evidence["count"] >= 3
        assert f.evidence["file_count"] >= 2


class TestCrossFilePatternSubprocess:
    """3 files with subprocess.run → emit STRUCT006."""

    def _write_subprocess_function(self, filepath, func_name: str = "run_cmd") -> None:
        code = textwrap.dedent(f"""\
            import subprocess

            def {func_name}(cmd):
                result = subprocess.run(cmd, capture_output=True)
                if result.returncode != 0:
                    raise RuntimeError(result.stderr)
                return result.stdout
        """)
        filepath.write_text(code)

    def test_subprocess_pattern_across_files(self, tmp_path) -> None:
        for i in range(3):
            d = tmp_path / f"runner_{i}.py"
            self._write_subprocess_function(d, f"run_cmd_{i}")

        py_files = [str(tmp_path / f"runner_{i}.py") for i in range(3)]
        findings = check_cross_file_patterns(py_files, str(tmp_path))

        subprocess_findings = [
            f for f in findings if f.evidence.get("pattern") == "subprocess_wrapper"
        ]
        assert len(subprocess_findings) == 1
        assert subprocess_findings[0].evidence["count"] >= 3


class TestCrossFilePatternBelowThreshold:
    """2 files → below threshold → no emit."""

    def test_two_files_no_emission(self, tmp_path) -> None:
        for i in range(2):
            d = tmp_path / f"mod_{i}.py"
            d.write_text(
                textwrap.dedent(f"""\
                import subprocess
                def run_cmd_{i}(cmd):
                    result = subprocess.run(cmd)
                    return result.returncode
            """)
            )

        py_files = [str(tmp_path / f"mod_{i}.py") for i in range(2)]
        findings = check_cross_file_patterns(py_files, str(tmp_path))

        subprocess_findings = [
            f for f in findings if f.evidence.get("pattern") == "subprocess_wrapper"
        ]
        assert len(subprocess_findings) == 0


class TestCrossFilePatternSameFile:
    """3 functions in the same file → < 2 files → no emit."""

    def test_single_file_no_emission(self, tmp_path) -> None:
        code = textwrap.dedent("""\
            import subprocess

            def run_a(cmd):
                result = subprocess.run(cmd)
                return result.returncode

            def run_b(cmd):
                result = subprocess.run(cmd)
                return result.returncode

            def run_c(cmd):
                result = subprocess.run(cmd)
                return result.returncode
        """)
        f = tmp_path / "single.py"
        f.write_text(code)

        findings = check_cross_file_patterns([str(f)], str(tmp_path))
        subprocess_findings = [
            fi for fi in findings if fi.evidence.get("pattern") == "subprocess_wrapper"
        ]
        assert len(subprocess_findings) == 0


class TestCrossFilePatternLargeFileSkipped:
    """File with >max_file_loc LOC is skipped."""

    def test_large_file_skipped(self, tmp_path) -> None:
        # Create a file with >50 lines of LOC (set max_file_loc=50 for test)
        large_code = (
            "import subprocess\n"
            + "\n" * 60
            + textwrap.dedent("""\
            def run_cmd(cmd):
                result = subprocess.run(cmd)
                return result.returncode
        """)
        )
        (tmp_path / "large.py").write_text(large_code)

        # Create 2 more small files with the pattern
        for i in range(2):
            (tmp_path / f"small_{i}.py").write_text(
                textwrap.dedent(f"""\
                import subprocess
                def run_{i}(cmd):
                    result = subprocess.run(cmd)
                    return result.returncode
            """)
            )

        py_files = [
            str(tmp_path / "large.py"),
            str(tmp_path / "small_0.py"),
            str(tmp_path / "small_1.py"),
        ]
        # With max_file_loc=50, the large file is skipped
        # Only 2 small files have the pattern → below 3 threshold → no emit
        findings = check_cross_file_patterns(py_files, str(tmp_path), max_file_loc=50)
        subprocess_findings = [
            f for f in findings if f.evidence.get("pattern") == "subprocess_wrapper"
        ]
        assert len(subprocess_findings) == 0


class TestCrossFilePatternMaxFilesCap:
    """max_files cap is respected."""

    def test_max_files_cap(self, tmp_path) -> None:
        # Create 5 files with subprocess pattern
        for i in range(5):
            (tmp_path / f"mod_{i}.py").write_text(
                textwrap.dedent(f"""\
                import subprocess
                def run_{i}(cmd):
                    result = subprocess.run(cmd)
                    return result.returncode
            """)
            )

        py_files = [str(tmp_path / f"mod_{i}.py") for i in range(5)]

        # With max_files=2, only first 2 files analyzed → below 3 threshold
        findings = check_cross_file_patterns(py_files, str(tmp_path), max_files=2)
        subprocess_findings = [
            f for f in findings if f.evidence.get("pattern") == "subprocess_wrapper"
        ]
        assert len(subprocess_findings) == 0

        # With max_files=5 (default), all files analyzed → emit
        findings = check_cross_file_patterns(py_files, str(tmp_path), max_files=5)
        subprocess_findings = [
            f for f in findings if f.evidence.get("pattern") == "subprocess_wrapper"
        ]
        assert len(subprocess_findings) == 1


# ── Helper tests ─────────────────────────────────────────────────────


class TestFingerprintFunction:
    """Test _fingerprint_function helper."""

    def _parse_func(self, code: str):
        import ast

        tree = ast.parse(textwrap.dedent(code))
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                return node
        raise ValueError("No function found")

    def test_config_pattern_match(self) -> None:
        func = self._parse_func("""\
            def load():
                try:
                    p = os.path.expanduser("~")
                    with open(p) as f:
                        return json.load(f)
                except Exception:
                    pass
        """)
        patterns = _fingerprint_function(func)
        assert "config_loading" in patterns

    def test_subprocess_pattern_match(self) -> None:
        func = self._parse_func("""\
            def run(cmd):
                result = subprocess.run(cmd)
                return result.returncode
        """)
        patterns = _fingerprint_function(func)
        assert "subprocess_wrapper" in patterns

    def test_retry_pattern_match(self) -> None:
        func = self._parse_func("""\
            def retry():
                for i in range(3):
                    try:
                        do_thing()
                    except Exception:
                        time.sleep(1)
        """)
        patterns = _fingerprint_function(func)
        assert "retry_loop" in patterns

    def test_no_pattern_match(self) -> None:
        func = self._parse_func("""\
            def simple():
                return 42
        """)
        patterns = _fingerprint_function(func)
        assert patterns == []


class TestGetCallNameSimple:
    """Test _get_call_name_simple helper."""

    def test_simple_name(self) -> None:
        import ast

        node = ast.parse("foo()").body[0].value
        assert _get_call_name_simple(node) == "foo"

    def test_attribute_name(self) -> None:
        import ast

        node = ast.parse("os.path.expanduser('~')").body[0].value
        # os.path is not a simple Name, so returns just the attr
        assert _get_call_name_simple(node) == "expanduser"

    def test_dotted_name(self) -> None:
        import ast

        node = ast.parse("subprocess.run(cmd)").body[0].value
        assert _get_call_name_simple(node) == "subprocess.run"
