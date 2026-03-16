"""Tests for dynamic coverage bridge — per-test function linkage."""

from __future__ import annotations

import sqlite3
import textwrap
import time

from lintgate.specification.dynamic_coverage import (
    DynamicLinkageMap,
    FunctionLinkage,
    LinkageEntry,
    _any_py_file_newer,
    _build_test_func_index,
    _extract_contexts,
    _find_file_contexts,
    _is_test_path,
    _numbits_to_lines,
    _parse_test_context,
    _relpath_to_dotted,
    _test_func_matches,
    build_dynamic_linkage,
    build_or_load_linkage,
    load_linkage_cache,
    merge_with_static,
    parse_coverage_contexts,
    save_linkage_cache,
)

# ── numbits decoding ──────────────────────────────────────────────


class TestNumBitsToLines:
    def test_empty_bytes(self):
        assert _numbits_to_lines(b"") == []

    def test_single_bit_line_1(self):
        # Bit 0 of byte 0 → line 1
        assert _numbits_to_lines(bytes([0b00000001])) == [1]

    def test_single_bit_line_8(self):
        # Bit 7 of byte 0 → line 8
        assert _numbits_to_lines(bytes([0b10000000])) == [8]

    def test_multiple_bits_same_byte(self):
        # Bits 0,2,4 of byte 0 → lines 1,3,5
        assert _numbits_to_lines(bytes([0b00010101])) == [1, 3, 5]

    def test_second_byte(self):
        # Bit 0 of byte 1 → line 9
        assert _numbits_to_lines(bytes([0, 0b00000001])) == [9]

    def test_zero_byte_skipped(self):
        # Byte 0 all zeros, byte 1 has bit 0 → only line 9
        assert _numbits_to_lines(bytes([0, 1])) == [9]

    def test_all_bits_set(self):
        assert _numbits_to_lines(bytes([0xFF])) == [1, 2, 3, 4, 5, 6, 7, 8]


# ── context string parsing ────────────────────────────────────────


class TestParseTestContext:
    def test_simple_function(self):
        _, func = _parse_test_context("test_add|run")
        assert func == "test_add"

    def test_class_method(self):
        _, func = _parse_test_context("TestFoo.test_bar|run")
        assert func == "TestFoo.test_bar"

    def test_no_run_suffix(self):
        _, func = _parse_test_context("test_plain")
        assert func == "test_plain"

    def test_pytest_cov_format(self):
        file_hint, func = _parse_test_context("tests/test_foo.py::TestClass.test_method")
        assert file_hint == "tests/test_foo.py"
        assert func == "TestClass.test_method"

    def test_empty_context(self):
        _, func = _parse_test_context("")
        assert func == ""


# ── SQLite .coverage parsing ──────────────────────────────────────


def _create_coverage_db(db_path: str, files: dict, contexts: dict, line_bits: list) -> None:
    """Create a minimal .coverage SQLite database for testing."""
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE file (id INTEGER PRIMARY KEY, path TEXT)")
    conn.execute("CREATE TABLE context (id INTEGER PRIMARY KEY, context TEXT)")
    conn.execute("CREATE TABLE line_bits (file_id INTEGER, context_id INTEGER, numbits BLOB)")
    for fid, path in files.items():
        conn.execute("INSERT INTO file VALUES (?, ?)", (fid, path))
    for cid, ctx in contexts.items():
        conn.execute("INSERT INTO context VALUES (?, ?)", (cid, ctx))
    for file_id, context_id, numbits in line_bits:
        conn.execute("INSERT INTO line_bits VALUES (?, ?, ?)", (file_id, context_id, numbits))
    conn.commit()
    conn.close()


class TestParseCoverageContexts:
    def test_missing_db(self, tmp_path):
        result = parse_coverage_contexts(str(tmp_path / "nonexistent.db"))
        assert result == {}

    def test_empty_db(self, tmp_path):
        db = str(tmp_path / ".coverage")
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE other (id INTEGER)")
        conn.commit()
        conn.close()
        assert parse_coverage_contexts(db) == {}

    def test_basic_context_extraction(self, tmp_path):
        db = str(tmp_path / ".coverage")
        _create_coverage_db(
            db,
            files={1: "/src/module.py"},
            contexts={1: "", 2: "test_foo|run"},
            line_bits=[
                # test_foo covers lines 1-3 of module.py
                (1, 2, bytes([0b00000111])),  # bits 0,1,2 → lines 1,2,3
            ],
        )
        result = parse_coverage_contexts(db)
        assert "/src/module.py" in result
        ctxs = result["/src/module.py"]
        assert ctxs[1] == ["test_foo|run"]
        assert ctxs[2] == ["test_foo|run"]
        assert ctxs[3] == ["test_foo|run"]

    def test_multiple_contexts_same_line(self, tmp_path):
        db = str(tmp_path / ".coverage")
        _create_coverage_db(
            db,
            files={1: "/src/module.py"},
            contexts={1: "test_a|run", 2: "test_b|run"},
            line_bits=[
                (1, 1, bytes([0b00000001])),  # test_a covers line 1
                (1, 2, bytes([0b00000001])),  # test_b covers line 1
            ],
        )
        result = parse_coverage_contexts(db)
        ctxs = result["/src/module.py"]
        assert sorted(ctxs[1]) == ["test_a|run", "test_b|run"]

    def test_empty_context_filtered(self, tmp_path):
        """Empty context string (no dynamic_context) should be skipped."""
        db = str(tmp_path / ".coverage")
        _create_coverage_db(
            db,
            files={1: "/src/module.py"},
            contexts={1: ""},  # empty = no context
            line_bits=[(1, 1, bytes([0b00000001]))],
        )
        result = parse_coverage_contexts(db)
        assert result == {}


# ── End-to-end linkage building ───────────────────────────────────


class TestBuildDynamicLinkage:
    def test_no_coverage_db(self, tmp_path):
        result = build_dynamic_linkage(str(tmp_path))
        assert result.linkages == {}
        assert result.total_source_functions_linked == 0

    def test_basic_linkage(self, tmp_path):
        """Source function covered by a test → dynamic linkage."""
        # Create source file
        src = tmp_path / "calc.py"
        src.write_text(
            textwrap.dedent("""\
            def add(a, b):
                return a + b

            def subtract(a, b):
                return a - b
        """)
        )

        # Create test file
        test_dir = tmp_path / "tests"
        test_dir.mkdir()
        test_file = test_dir / "test_calc.py"
        test_file.write_text(
            textwrap.dedent("""\
            from calc import add
            def test_add():
                assert add(1, 2) == 3
        """)
        )

        # Create .coverage with dynamic contexts
        db = str(tmp_path / ".coverage")
        _create_coverage_db(
            db,
            files={1: str(src), 2: str(test_file)},
            contexts={1: "test_add|run"},
            line_bits=[
                # test_add covers lines 1-2 of calc.py (the add function)
                (1, 1, bytes([0b00000011])),
            ],
        )

        result = build_dynamic_linkage(str(tmp_path), coverage_db=db)
        assert result.total_source_functions_linked >= 1

        # Find the add function linkage
        add_key = None
        for key in result.linkages:
            if "add" in key and "subtract" not in key:
                add_key = key
                break

        assert add_key is not None, (
            f"Expected add linkage, got keys: {list(result.linkages.keys())}"
        )
        entries = result.tests_for(add_key)
        assert len(entries) >= 1
        assert entries[0].confidence == "dynamic"
        assert entries[0].test_function == "test_add"

    def test_uncovered_function_not_linked(self, tmp_path):
        """Functions without coverage contexts should not appear in linkage."""
        src = tmp_path / "calc.py"
        src.write_text(
            textwrap.dedent("""\
            def add(a, b):
                return a + b

            def subtract(a, b):
                return a - b
        """)
        )

        db = str(tmp_path / ".coverage")
        _create_coverage_db(
            db,
            files={1: str(src)},
            contexts={1: "test_add|run"},
            line_bits=[
                # Only covers lines 1-2 (add function), not lines 4-5 (subtract)
                (1, 1, bytes([0b00000011])),
            ],
        )

        result = build_dynamic_linkage(str(tmp_path), coverage_db=db)

        # subtract should not be linked
        for key in result.linkages:
            assert "subtract" not in key, f"subtract should not be linked, found in {key}"

    def test_class_method_linkage(self, tmp_path):
        """Class methods should produce qualified keys."""
        src = tmp_path / "svc.py"
        src.write_text(
            textwrap.dedent("""\
            class Service:
                def process(self, data):
                    return data.upper()
        """)
        )

        db = str(tmp_path / ".coverage")
        _create_coverage_db(
            db,
            files={1: str(src)},
            contexts={1: "TestService.test_process|run"},
            line_bits=[
                (1, 1, bytes([0b00000111])),  # covers lines 1-3
            ],
        )

        result = build_dynamic_linkage(str(tmp_path), coverage_db=db)
        # Should have a key like "svc.py::Service.process"
        process_keys = [k for k in result.linkages if "Service.process" in k]
        assert len(process_keys) == 1


# ── Linkage confidence tiers ──────────────────────────────────────


class TestFunctionLinkage:
    def test_best_confidence_dynamic(self):
        fl = FunctionLinkage(tests=[LinkageEntry("t.py", "test_a", "dynamic")])
        assert fl.best_confidence == "dynamic"

    def test_best_confidence_hybrid(self):
        fl = FunctionLinkage(
            tests=[
                LinkageEntry("t.py", "test_a", "dynamic"),
                LinkageEntry("t.py", "test_b", "hybrid"),
            ]
        )
        assert fl.best_confidence == "hybrid"

    def test_best_confidence_static_only(self):
        fl = FunctionLinkage(tests=[LinkageEntry("t.py", "test_a", "static")])
        assert fl.best_confidence == "static"

    def test_empty_tests(self):
        fl = FunctionLinkage(tests=[])
        assert fl.best_confidence == "static"


# ── Merge with static impact map ──────────────────────────────────


class _FakeRef:
    def __init__(self, test_file: str, test_function: str):
        self.test_file = test_file
        self.test_function = test_function


class _FakeImpactMap:
    def __init__(self, mappings: dict[str, list[_FakeRef]]):
        self.function_to_tests = mappings


class TestMergeWithStatic:
    def test_dynamic_only_stays_dynamic(self):
        dynamic = DynamicLinkageMap(
            linkages={
                "mod.py::func_a": FunctionLinkage(tests=[LinkageEntry("t.py", "test_a", "dynamic")])
            }
        )
        static = _FakeImpactMap({})
        merged = merge_with_static(dynamic, static)
        entries = merged.tests_for("mod.py::func_a")
        assert len(entries) == 1
        assert entries[0].confidence == "dynamic"

    def test_both_agree_becomes_hybrid(self):
        dynamic = DynamicLinkageMap(
            linkages={
                "mod.py::func_a": FunctionLinkage(tests=[LinkageEntry("t.py", "test_a", "dynamic")])
            }
        )
        static = _FakeImpactMap({"func_a": [_FakeRef("t.py", "test_a")]})
        merged = merge_with_static(dynamic, static)
        entries = merged.tests_for("mod.py::func_a")
        assert any(e.confidence == "hybrid" for e in entries)

    def test_static_only_added(self):
        dynamic = DynamicLinkageMap(
            linkages={
                "mod.py::func_a": FunctionLinkage(tests=[LinkageEntry("t.py", "test_a", "dynamic")])
            }
        )
        static = _FakeImpactMap(
            {"func_a": [_FakeRef("t.py", "test_a"), _FakeRef("t2.py", "test_b")]}
        )
        merged = merge_with_static(dynamic, static)
        entries = merged.tests_for("mod.py::func_a")
        funcs = {e.test_function for e in entries}
        assert "test_b" in funcs
        static_entries = [e for e in entries if e.test_function == "test_b"]
        assert static_entries[0].confidence == "static"


# ── Serialization round-trip ──────────────────────────────────────


class TestSerialization:
    def test_linkage_entry_round_trip(self):
        entry = LinkageEntry("tests/test_foo.py", "test_bar", "hybrid")
        d = entry.to_dict()
        restored = LinkageEntry.from_dict(d)
        assert restored.test_file == entry.test_file
        assert restored.test_function == entry.test_function
        assert restored.confidence == entry.confidence

    def test_dynamic_linkage_map_round_trip(self):
        dlm = DynamicLinkageMap(
            linkages={
                "mod.py::func_a": FunctionLinkage(
                    tests=[
                        LinkageEntry("t.py", "test_a", "dynamic"),
                        LinkageEntry("t.py", "test_b", "static"),
                    ]
                )
            },
            coverage_db_mtime=1234567.0,
            total_contexts=5,
            total_source_functions_linked=1,
            total_linkage_pairs=2,
        )
        d = dlm.to_dict()
        restored = DynamicLinkageMap.from_dict(d)
        assert restored.coverage_db_mtime == 1234567.0
        assert restored.total_contexts == 5
        assert "mod.py::func_a" in restored.linkages
        entries = restored.tests_for("mod.py::func_a")
        assert len(entries) == 2
        assert entries[0].confidence == "dynamic"

    def test_has_dynamic(self):
        dlm = DynamicLinkageMap(
            linkages={
                "a.py::f": FunctionLinkage(tests=[LinkageEntry("t.py", "test_f", "dynamic")]),
                "b.py::g": FunctionLinkage(tests=[LinkageEntry("t.py", "test_g", "static")]),
                "d.py::j": FunctionLinkage(tests=[LinkageEntry("t.py", "test_j", "hybrid")]),
            }
        )
        assert dlm.has_dynamic("a.py::f") is True
        assert dlm.has_dynamic("b.py::g") is False
        assert dlm.has_dynamic("c.py::h") is False
        assert dlm.has_dynamic("d.py::j") is True


# ── Cache persistence ─────────────────────────────────────────────


class TestLinkageCache:
    def test_save_and_load(self, tmp_path):
        dlm = DynamicLinkageMap(
            linkages={
                "mod.py::func": FunctionLinkage(tests=[LinkageEntry("t.py", "test_f", "dynamic")])
            },
            coverage_db_mtime=100.0,
            total_contexts=1,
            total_source_functions_linked=1,
            total_linkage_pairs=1,
        )
        save_linkage_cache(str(tmp_path), dlm)
        loaded = load_linkage_cache(str(tmp_path))
        assert loaded is not None
        assert "mod.py::func" in loaded.linkages
        assert loaded.coverage_db_mtime == 100.0

    def test_stale_cache_invalidated(self, tmp_path):
        """Cache should be invalidated when .coverage is newer."""
        dlm = DynamicLinkageMap(coverage_db_mtime=100.0)
        save_linkage_cache(str(tmp_path), dlm)

        # Create a .coverage file with a newer mtime
        cov_file = tmp_path / ".coverage"
        cov_file.write_text("")
        # mtime will be > 100.0

        loaded = load_linkage_cache(str(tmp_path))
        assert loaded is None  # stale

    def test_missing_cache_returns_none(self, tmp_path):
        assert load_linkage_cache(str(tmp_path)) is None


class TestBuildOrLoad:
    def test_builds_when_no_cache(self, tmp_path):
        # No .coverage file → empty linkage
        result = build_or_load_linkage(str(tmp_path))
        assert result.linkages == {}

    def test_uses_cache_when_fresh(self, tmp_path):
        # Pre-populate cache with known data
        dlm = DynamicLinkageMap(
            linkages={
                "cached.py::func": FunctionLinkage(
                    tests=[LinkageEntry("t.py", "test_f", "dynamic")]
                )
            },
            coverage_db_mtime=float("inf"),  # always "fresh"
            total_contexts=1,
            total_source_functions_linked=1,
            total_linkage_pairs=1,
        )
        save_linkage_cache(str(tmp_path), dlm)

        result = build_or_load_linkage(str(tmp_path))
        assert "cached.py::func" in result.linkages

    def test_force_rebuild_ignores_cache(self, tmp_path):
        dlm = DynamicLinkageMap(
            linkages={
                "cached.py::func": FunctionLinkage(
                    tests=[LinkageEntry("t.py", "test_f", "dynamic")]
                )
            },
            coverage_db_mtime=float("inf"),
            total_contexts=1,
            total_source_functions_linked=1,
            total_linkage_pairs=1,
        )
        save_linkage_cache(str(tmp_path), dlm)

        # Force rebuild with no .coverage → empty result
        result = build_or_load_linkage(str(tmp_path), force_rebuild=True)
        assert result.linkages == {}


# ── P1-b: Package-qualified context resolution ───────────────────


class TestRelPathToDotted:
    def test_simple_path(self):
        assert _relpath_to_dotted("tests/test_foo.py") == "tests.test_foo"

    def test_nested_path(self):
        assert _relpath_to_dotted("tests/a/test_api.py") == "tests.a.test_api"

    def test_no_extension(self):
        assert _relpath_to_dotted("tests/test_foo") == "tests.test_foo"


class TestPackageQualifiedResolution:
    def test_duplicate_basenames_resolved_correctly(self, tmp_path):
        """Package-qualified contexts with duplicate basenames must resolve
        to the correct file, not the last one indexed."""
        # Create two test files with the same basename in different packages
        pkg_a = tmp_path / "tests" / "a"
        pkg_a.mkdir(parents=True)
        (pkg_a / "__init__.py").touch()
        test_a = pkg_a / "test_api.py"
        test_a.write_text(
            textwrap.dedent("""\
            class TestCalc:
                def test_add(self):
                    pass
        """)
        )

        pkg_b = tmp_path / "tests" / "b"
        pkg_b.mkdir(parents=True)
        (pkg_b / "__init__.py").touch()
        test_b = pkg_b / "test_api.py"
        test_b.write_text(
            textwrap.dedent("""\
            class TestCalc:
                def test_subtract(self):
                    pass
        """)
        )
        (tmp_path / "tests" / "__init__.py").touch()

        # Create a source file
        src = tmp_path / "calc.py"
        src.write_text(
            textwrap.dedent("""\
            def add(a, b):
                return a + b
        """)
        )

        # Coverage context uses package-qualified name: tests.a.test_api.TestCalc.test_add
        db = str(tmp_path / ".coverage")
        _create_coverage_db(
            db,
            files={1: str(src)},
            contexts={1: "tests.a.test_api.TestCalc.test_add|run"},
            line_bits=[(1, 1, bytes([0b00000011]))],
        )

        result = build_dynamic_linkage(str(tmp_path), coverage_db=db)
        # Find the add function
        add_keys = [k for k in result.linkages if "add" in k]
        assert add_keys, f"Expected add linkage, got: {list(result.linkages.keys())}"

        entries = result.tests_for(add_keys[0])
        assert len(entries) >= 1
        # The resolved test file should be tests/a/test_api.py, NOT tests/b/test_api.py
        resolved_files = {e.test_file for e in entries}
        assert any("a" in f for f in resolved_files), (
            f"Expected tests/a/test_api.py, got: {resolved_files}"
        )


# ── P2: Cache staleness on source/test file changes ─────────────


class TestCacheStalenessOnFileChanges:
    def test_cache_stale_when_test_file_modified(self, tmp_path):
        """Cache should be invalidated when a test file is modified after build."""
        # Create test dir with a test file
        test_dir = tmp_path / "tests"
        test_dir.mkdir()
        test_file = test_dir / "test_foo.py"
        test_file.write_text("def test_a(): pass\n")

        # Build cache with a timestamp in the past
        dlm = DynamicLinkageMap(
            linkages={
                "mod.py::func": FunctionLinkage(
                    tests=[LinkageEntry("tests/test_foo.py", "test_a", "dynamic")]
                )
            },
            coverage_db_mtime=float("inf"),  # .coverage check passes
            cache_built_at=time.time() - 10,  # built 10s ago
            total_contexts=1,
            total_source_functions_linked=1,
            total_linkage_pairs=1,
        )
        save_linkage_cache(str(tmp_path), dlm)

        # Modify test file (mtime will be now, after cache_built_at)
        test_file.write_text("def test_a(): assert True\ndef test_b(): pass\n")

        loaded = load_linkage_cache(str(tmp_path))
        assert loaded is None  # stale due to test file change

    def test_cache_fresh_when_no_files_changed(self, tmp_path):
        """Cache should be valid when no files changed since build."""
        # Create test dir with a test file
        test_dir = tmp_path / "tests"
        test_dir.mkdir()
        test_file = test_dir / "test_foo.py"
        test_file.write_text("def test_a(): pass\n")

        # Build cache with a future timestamp (no files can be newer)
        dlm = DynamicLinkageMap(
            linkages={
                "mod.py::func": FunctionLinkage(
                    tests=[LinkageEntry("tests/test_foo.py", "test_a", "dynamic")]
                )
            },
            coverage_db_mtime=float("inf"),
            cache_built_at=time.time() + 100,  # future — nothing newer
            total_contexts=1,
            total_source_functions_linked=1,
            total_linkage_pairs=1,
        )
        save_linkage_cache(str(tmp_path), dlm)

        loaded = load_linkage_cache(str(tmp_path))
        assert loaded is not None

    def test_any_py_file_newer_detects_source_changes(self, tmp_path):
        """_any_py_file_newer should detect changes in source packages."""
        # Create a source package
        pkg = tmp_path / "mypackage"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("")
        src = pkg / "core.py"
        src.write_text("def f(): pass\n")

        threshold = time.time() - 10  # 10s ago
        assert _any_py_file_newer(str(tmp_path), threshold) is True

    def test_any_py_file_newer_false_when_all_old(self, tmp_path):
        """_any_py_file_newer should return False when all files are older."""
        test_dir = tmp_path / "tests"
        test_dir.mkdir()
        (test_dir / "test_old.py").write_text("def test_x(): pass\n")

        threshold = time.time() + 100  # far future
        assert _any_py_file_newer(str(tmp_path), threshold) is False


# ── P2: cache_built_at serialization round-trip ──────────────────


class TestCacheBuiltAtRoundTrip:
    def test_cache_built_at_preserved(self):
        dlm = DynamicLinkageMap(cache_built_at=12345.678)
        d = dlm.to_dict()
        restored = DynamicLinkageMap.from_dict(d)
        assert restored.cache_built_at == 12345.678

    def test_cache_built_at_defaults_zero(self):
        """Old caches without cache_built_at should default to 0."""
        restored = DynamicLinkageMap.from_dict({"version": 1})
        assert restored.cache_built_at == 0.0


# ── Direct tests for _any_py_file_newer ──────────────────────────


class TestAnyPyFileNewerDirect:
    """Direct tests for _any_py_file_newer covering nested packages,
    __pycache__ exclusion, .git exclusion, and source-only / test-only dirs."""

    def test_nested_test_package_detected(self, tmp_path):
        """Nested test directories (tests/sub/deep/) should be scanned."""
        deep = tmp_path / "tests" / "sub" / "deep"
        deep.mkdir(parents=True)
        (deep / "test_nested.py").write_text("def test_x(): pass\n")

        threshold = time.time() - 10
        assert _any_py_file_newer(str(tmp_path), threshold) is True

    def test_pycache_excluded(self, tmp_path):
        """__pycache__ directories must be skipped even if they contain .py files."""
        test_dir = tmp_path / "tests"
        test_dir.mkdir()
        cache_dir = test_dir / "__pycache__"
        cache_dir.mkdir()
        (cache_dir / "test_cached.py").write_text("# cached\n")

        threshold = time.time() - 10
        # __pycache__ is the only dir with .py files, so result should be False
        assert _any_py_file_newer(str(tmp_path), threshold) is False

    def test_dot_dirs_excluded(self, tmp_path):
        """Hidden directories (starting with .) should be skipped."""
        test_dir = tmp_path / "tests"
        test_dir.mkdir()
        hidden = test_dir / ".hidden"
        hidden.mkdir()
        (hidden / "test_secret.py").write_text("def test_h(): pass\n")

        threshold = time.time() - 10
        assert _any_py_file_newer(str(tmp_path), threshold) is False

    def test_git_dir_excluded_from_source_scan(self, tmp_path):
        """The .git directory should not be scanned as a source package."""
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        (git_dir / "__init__.py").write_text("")
        (git_dir / "config.py").write_text("x = 1\n")

        threshold = time.time() - 10
        assert _any_py_file_newer(str(tmp_path), threshold) is False

    def test_only_test_dirs_present(self, tmp_path):
        """Should detect changes when only tests/ exists (no source packages)."""
        test_dir = tmp_path / "tests"
        test_dir.mkdir()
        (test_dir / "test_only.py").write_text("def test_y(): pass\n")

        threshold = time.time() - 10
        assert _any_py_file_newer(str(tmp_path), threshold) is True

    def test_only_source_package_present(self, tmp_path):
        """Should detect changes when only source packages exist (no tests/)."""
        pkg = tmp_path / "mypkg"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("")
        (pkg / "module.py").write_text("def f(): return 1\n")

        threshold = time.time() - 10
        assert _any_py_file_newer(str(tmp_path), threshold) is True

    def test_source_package_without_init_ignored(self, tmp_path):
        """Directories without __init__.py are not source packages."""
        notpkg = tmp_path / "scripts"
        notpkg.mkdir()
        (notpkg / "run.py").write_text("print('hi')\n")

        threshold = time.time() - 10
        assert _any_py_file_newer(str(tmp_path), threshold) is False

    def test_both_test_dirs_scanned(self, tmp_path):
        """Both tests/ and test/ directories should be scanned."""
        for dirname in ("tests", "test"):
            d = tmp_path / dirname
            d.mkdir()
            (d / f"test_{dirname}_file.py").write_text("def test_z(): pass\n")

        threshold = time.time() - 10
        assert _any_py_file_newer(str(tmp_path), threshold) is True

    def test_nested_source_packages(self, tmp_path):
        """Nested source packages (mylib/sub/) should be scanned."""
        sub = tmp_path / "mylib" / "sub"
        sub.mkdir(parents=True)
        (tmp_path / "mylib" / "__init__.py").write_text("")
        (sub / "__init__.py").write_text("")
        (sub / "deep_module.py").write_text("x = 42\n")

        threshold = time.time() - 10
        assert _any_py_file_newer(str(tmp_path), threshold) is True

    def test_no_dirs_returns_false(self, tmp_path):
        """Empty project root (no tests/ or source packages) returns False."""
        threshold = time.time() - 10
        assert _any_py_file_newer(str(tmp_path), threshold) is False

    def test_non_py_files_ignored(self, tmp_path):
        """Non-Python files in test dirs should not trigger staleness."""
        test_dir = tmp_path / "tests"
        test_dir.mkdir()
        (test_dir / "data.json").write_text("{}")
        (test_dir / "README.md").write_text("# readme")

        threshold = time.time() - 10
        assert _any_py_file_newer(str(tmp_path), threshold) is False

    def test_node_modules_excluded(self, tmp_path):
        """node_modules should not be treated as a source package."""
        nm = tmp_path / "node_modules"
        nm.mkdir()
        (nm / "__init__.py").write_text("")
        (nm / "surprise.py").write_text("x = 1\n")

        threshold = time.time() - 10
        assert _any_py_file_newer(str(tmp_path), threshold) is False


# ── Direct tests for _extract_contexts ───────────────────────────


class TestExtractContextsDirect:
    """Direct tests for _extract_contexts with controlled SQLite connections."""

    def _make_db(self, tmp_path, files, contexts, line_bits):
        db = str(tmp_path / ".coverage")
        _create_coverage_db(db, files, contexts, line_bits)
        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        return conn

    def test_missing_file_table(self, tmp_path):
        """Should return {} when 'file' table is missing."""
        db = str(tmp_path / "partial.db")
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE context (id INTEGER PRIMARY KEY, context TEXT)")
        conn.execute("CREATE TABLE line_bits (file_id INTEGER, context_id INTEGER, numbits BLOB)")
        conn.row_factory = sqlite3.Row
        result = _extract_contexts(conn)
        conn.close()
        assert result == {}

    def test_missing_context_table(self, tmp_path):
        """Should return {} when 'context' table is missing."""
        db = str(tmp_path / "partial.db")
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE file (id INTEGER PRIMARY KEY, path TEXT)")
        conn.execute("CREATE TABLE line_bits (file_id INTEGER, context_id INTEGER, numbits BLOB)")
        conn.row_factory = sqlite3.Row
        result = _extract_contexts(conn)
        conn.close()
        assert result == {}

    def test_missing_line_bits_table(self, tmp_path):
        """Should return {} when 'line_bits' table is missing."""
        db = str(tmp_path / "partial.db")
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE file (id INTEGER PRIMARY KEY, path TEXT)")
        conn.execute("CREATE TABLE context (id INTEGER PRIMARY KEY, context TEXT)")
        conn.row_factory = sqlite3.Row
        result = _extract_contexts(conn)
        conn.close()
        assert result == {}

    def test_multiple_files_multiple_contexts(self, tmp_path):
        """Multiple files with multiple contexts should all be extracted."""
        conn = self._make_db(
            tmp_path,
            files={1: "/src/a.py", 2: "/src/b.py"},
            contexts={1: "test_a|run", 2: "test_b|run", 3: "test_c|run"},
            line_bits=[
                (1, 1, bytes([0b00000001])),  # a.py, line 1, test_a
                (1, 2, bytes([0b00000010])),  # a.py, line 2, test_b
                (2, 3, bytes([0b00000001])),  # b.py, line 1, test_c
            ],
        )
        result = _extract_contexts(conn)
        conn.close()

        assert "/src/a.py" in result
        assert "/src/b.py" in result
        assert result["/src/a.py"][1] == ["test_a|run"]
        assert result["/src/a.py"][2] == ["test_b|run"]
        assert result["/src/b.py"][1] == ["test_c|run"]

    def test_empty_context_strings_filtered(self, tmp_path):
        """Empty context strings should be excluded from the results."""
        conn = self._make_db(
            tmp_path,
            files={1: "/src/mod.py"},
            contexts={1: "", 2: "test_real|run"},
            line_bits=[
                (1, 1, bytes([0b00000001])),  # empty context
                (1, 2, bytes([0b00000001])),  # real context
            ],
        )
        result = _extract_contexts(conn)
        conn.close()

        # Only the non-empty context should appear
        assert "/src/mod.py" in result
        assert result["/src/mod.py"][1] == ["test_real|run"]

    def test_only_empty_contexts_returns_empty(self, tmp_path):
        """If all contexts are empty strings, result should be {}."""
        conn = self._make_db(
            tmp_path,
            files={1: "/src/mod.py"},
            contexts={1: ""},
            line_bits=[(1, 1, bytes([0b00000001]))],
        )
        result = _extract_contexts(conn)
        conn.close()
        assert result == {}

    def test_unknown_file_id_skipped(self, tmp_path):
        """line_bits referencing a non-existent file_id should be skipped."""
        conn = self._make_db(
            tmp_path,
            files={1: "/src/known.py"},
            contexts={1: "test_x|run"},
            line_bits=[
                (999, 1, bytes([0b00000001])),  # unknown file_id
            ],
        )
        result = _extract_contexts(conn)
        conn.close()
        assert result == {}

    def test_unknown_context_id_skipped(self, tmp_path):
        """line_bits referencing a non-existent context_id should be skipped."""
        conn = self._make_db(
            tmp_path,
            files={1: "/src/known.py"},
            contexts={1: "test_x|run"},
            line_bits=[
                (1, 999, bytes([0b00000001])),  # unknown context_id
            ],
        )
        result = _extract_contexts(conn)
        conn.close()
        assert result == {}

    def test_empty_numbits_produces_no_lines(self, tmp_path):
        """Empty numbits blob should yield no lines and not contribute."""
        conn = self._make_db(
            tmp_path,
            files={1: "/src/mod.py"},
            contexts={1: "test_x|run"},
            line_bits=[(1, 1, b"")],  # empty bitmap
        )
        result = _extract_contexts(conn)
        conn.close()
        assert result == {}


# ── Direct tests for _build_test_func_index ──────────────────────


class TestBuildTestFuncIndexDirect:
    """Direct tests for _build_test_func_index covering all key lookup modes."""

    def test_bare_function_name_lookup(self, tmp_path):
        """A top-level test function should be indexed by bare name."""
        test_dir = tmp_path / "tests"
        test_dir.mkdir()
        (test_dir / "test_simple.py").write_text("def test_alpha(): pass\n")

        index = _build_test_func_index(str(tmp_path), {})
        assert "test_alpha" in index

    def test_class_qualified_lookup(self, tmp_path):
        """A class method should be indexed as 'ClassName.test_method'."""
        test_dir = tmp_path / "tests"
        test_dir.mkdir()
        (test_dir / "test_cls.py").write_text(
            textwrap.dedent("""\
            class TestMath:
                def test_add(self):
                    pass
        """)
        )

        index = _build_test_func_index(str(tmp_path), {})
        assert "TestMath.test_add" in index
        # Also bare name
        assert "test_add" in index

    def test_module_qualified_lookup(self, tmp_path):
        """Functions should be indexed as 'test_module.test_func'."""
        test_dir = tmp_path / "tests"
        test_dir.mkdir()
        (test_dir / "test_core.py").write_text("def test_beta(): pass\n")

        index = _build_test_func_index(str(tmp_path), {})
        assert "test_core.test_beta" in index

    def test_package_qualified_lookup(self, tmp_path):
        """Functions should be indexed as 'tests.sub.test_module.test_func'."""
        sub = tmp_path / "tests" / "sub"
        sub.mkdir(parents=True)
        (tmp_path / "tests" / "__init__.py").touch()
        (sub / "__init__.py").touch()
        (sub / "test_deep.py").write_text("def test_gamma(): pass\n")

        index = _build_test_func_index(str(tmp_path), {})
        assert "tests.sub.test_deep.test_gamma" in index

    def test_coverage_data_test_files_indexed(self, tmp_path):
        """Test files found in coverage data should be indexed too."""
        test_dir = tmp_path / "tests"
        test_dir.mkdir()
        test_file = test_dir / "test_cov.py"
        test_file.write_text("def test_from_cov(): pass\n")

        file_contexts = {str(test_file): {1: ["some_ctx"]}}
        index = _build_test_func_index(str(tmp_path), file_contexts)
        assert "test_from_cov" in index

    def test_non_test_files_in_coverage_skipped(self, tmp_path):
        """Source files in coverage data should not be indexed."""
        src = tmp_path / "module.py"
        src.write_text("def helper(): pass\n")

        file_contexts = {str(src): {1: ["some_ctx"]}}
        index = _build_test_func_index(str(tmp_path), file_contexts)
        assert "helper" not in index

    def test_both_test_and_tests_dirs_scanned(self, tmp_path):
        """Both tests/ and test/ directories should be scanned."""
        for dirname, func in [("tests", "test_from_tests"), ("test", "test_from_test")]:
            d = tmp_path / dirname
            d.mkdir()
            (d / f"test_{dirname}.py").write_text(f"def {func}(): pass\n")

        index = _build_test_func_index(str(tmp_path), {})
        assert "test_from_tests" in index
        assert "test_from_test" in index

    def test_class_with_module_qualified(self, tmp_path):
        """Class methods should have module-qualified keys too."""
        test_dir = tmp_path / "tests"
        test_dir.mkdir()
        (test_dir / "test_svc.py").write_text(
            textwrap.dedent("""\
            class TestService:
                def test_process(self):
                    pass
        """)
        )

        index = _build_test_func_index(str(tmp_path), {})
        assert "test_svc.TestService.test_process" in index

    def test_non_test_functions_not_indexed(self, tmp_path):
        """Functions not starting with 'test_' should not be indexed."""
        test_dir = tmp_path / "tests"
        test_dir.mkdir()
        (test_dir / "test_helpers.py").write_text(
            textwrap.dedent("""\
            def helper_setup():
                pass

            def test_real():
                pass
        """)
        )

        index = _build_test_func_index(str(tmp_path), {})
        assert "helper_setup" not in index
        assert "test_real" in index

    def test_pycache_excluded_during_walk(self, tmp_path):
        """__pycache__ directories in tests/ should be skipped."""
        test_dir = tmp_path / "tests"
        test_dir.mkdir()
        cache = test_dir / "__pycache__"
        cache.mkdir()
        (cache / "test_cached.py").write_text("def test_stale(): pass\n")

        index = _build_test_func_index(str(tmp_path), {})
        assert "test_stale" not in index

    def test_duplicate_basenames_both_indexed(self, tmp_path):
        """Two test files with the same basename in different packages
        should both have package-qualified keys."""
        for subdir, func in [("a", "test_foo"), ("b", "test_bar")]:
            d = tmp_path / "tests" / subdir
            d.mkdir(parents=True)
            (d / "__init__.py").touch()
            (d / "test_api.py").write_text(f"def {func}(): pass\n")
        (tmp_path / "tests" / "__init__.py").touch()

        index = _build_test_func_index(str(tmp_path), {})
        assert "tests.a.test_api.test_foo" in index
        assert "tests.b.test_api.test_bar" in index


# ── Direct tests for _find_file_contexts ─────────────────────────


class TestFindFileContextsDirect:
    """Direct tests for _find_file_contexts covering all path matching modes."""

    def test_exact_normpath_match(self, tmp_path):
        """Should match when norm_path is an exact key in file_contexts."""
        contexts = {"/src/module.py": {1: ["ctx"]}}
        result = _find_file_contexts("/src/module.py", contexts, str(tmp_path))
        assert result is not None
        assert result[1] == ["ctx"]

    def test_relative_path_match(self, tmp_path):
        """Should match via relative path when absolute path is given."""
        project = str(tmp_path)
        rel_key = "src/module.py"
        abs_path = str(tmp_path / "src" / "module.py")
        contexts = {rel_key: {5: ["test_x"]}}

        result = _find_file_contexts(abs_path, contexts, project)
        assert result is not None
        assert result[5] == ["test_x"]

    def test_normpath_normalization_match(self, tmp_path):
        """Should match when keys differ only by normpath (e.g. extra slashes)."""
        project = str(tmp_path)
        # Key with redundant separators
        key_with_extra = "src//module.py"
        contexts = {key_with_extra: {3: ["test_y"]}}

        result = _find_file_contexts(str(tmp_path / "src" / "module.py"), contexts, project)
        assert result is not None
        assert result[3] == ["test_y"]

    def test_join_normpath_match(self, tmp_path):
        """Should match when project_root + key normalizes to norm_path."""
        project = str(tmp_path)
        contexts = {"lib/core.py": {10: ["ctx_z"]}}
        abs_norm = str(tmp_path / "lib" / "core.py")

        result = _find_file_contexts(abs_norm, contexts, project)
        assert result is not None
        assert result[10] == ["ctx_z"]

    def test_no_match_returns_none(self, tmp_path):
        """Should return None when no path variant matches."""
        contexts = {"/other/path.py": {1: ["ctx"]}}
        result = _find_file_contexts("/no/match.py", contexts, str(tmp_path))
        assert result is None

    def test_empty_file_contexts(self, tmp_path):
        """Should return None when file_contexts is empty."""
        result = _find_file_contexts("/any/path.py", {}, str(tmp_path))
        assert result is None

    def test_multiple_candidates_first_normpath_match_wins(self, tmp_path):
        """When multiple keys normalize to the same path, any match is acceptable."""
        project = str(tmp_path)
        contexts = {
            "src/../src/mod.py": {1: ["ctx_first"]},
            "src/mod.py": {2: ["ctx_second"]},
        }
        abs_path = str(tmp_path / "src" / "mod.py")
        result = _find_file_contexts(abs_path, contexts, project)
        assert result is not None
        # Should find something (either match is valid)
        assert 1 in result or 2 in result


# ── Direct tests for _relpath_to_dotted (additional) ─────────────


class TestRelPathToDottedExtra:
    """Additional tests for _relpath_to_dotted beyond the existing 3."""

    def test_single_component_with_py(self):
        assert _relpath_to_dotted("test_foo.py") == "test_foo"

    def test_single_component_no_extension(self):
        assert _relpath_to_dotted("conftest") == "conftest"

    def test_deeply_nested(self):
        assert _relpath_to_dotted("a/b/c/d/test_deep.py") == "a.b.c.d.test_deep"

    def test_empty_string(self):
        assert _relpath_to_dotted("") == ""

    def test_trailing_separator(self):
        """A path ending with / (no filename) should still convert correctly."""
        result = _relpath_to_dotted("tests/sub/")
        assert result == "tests.sub."


# ── Direct tests for _test_func_matches ──────────────────────────


class TestTestFuncMatchesDirect:
    """Direct tests for _test_func_matches covering all match modes."""

    def test_exact_match(self):
        assert _test_func_matches("test_add", {"test_add", "test_sub"}) is True

    def test_suffix_match_with_dot(self):
        assert (
            _test_func_matches(
                "test_module.TestCalc.test_add",
                {"TestCalc.test_add"},
            )
            is True
        )

    def test_no_match(self):
        assert _test_func_matches("test_other", {"test_add", "test_sub"}) is False

    def test_empty_static_names(self):
        assert _test_func_matches("test_add", set()) is False

    def test_partial_name_not_matched(self):
        """'test_add' should not match 'test_addition' via suffix."""
        assert _test_func_matches("test_add", {"test_addition"}) is False

    def test_bare_suffix_match(self):
        """Dynamic name ending with static name (no dot) should match
        via the endswith(static) branch."""
        # "mod.test_add" ends with "test_add" (suffix without dot separator)
        assert _test_func_matches("mod.test_add", {"test_add"}) is True

    def test_module_class_method_suffix(self):
        """Module-qualified name should match class-qualified static name."""
        assert (
            _test_func_matches(
                "tests.test_svc.TestService.test_process",
                {"TestService.test_process"},
            )
            is True
        )

    def test_single_element_set(self):
        assert _test_func_matches("x.test_one", {"test_one"}) is True
        assert _test_func_matches("x.test_one", {"test_two"}) is False


# ── Direct tests for _is_test_path ───────────────────────────────


class TestIsTestPath:
    """Direct tests for _is_test_path covering edge cases."""

    def test_test_prefix(self):
        assert _is_test_path("test_foo.py") is True

    def test_test_suffix(self):
        assert _is_test_path("foo_test.py") is True

    def test_not_test(self):
        assert _is_test_path("helper.py") is False

    def test_full_path_with_test_prefix(self):
        assert _is_test_path("/abs/path/tests/test_core.py") is True

    def test_full_path_without_test(self):
        assert _is_test_path("/abs/path/src/core.py") is False

    def test_conftest_not_test(self):
        assert _is_test_path("conftest.py") is False
