"""Tests for test-impact mapping from coverage.py SQLite databases."""

import sqlite3

from lintgate.mutation.test_impact import (
    _is_test_file,
    _strip_context_suffix,
    get_tests_for_file,
    load_test_impact_mapping,
)


def _create_coverage_db(path, rows):
    """Create a minimal coverage.py SQLite DB with the given (file, context) rows."""
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE file (id INTEGER PRIMARY KEY, path TEXT)")
    conn.execute("CREATE TABLE context (id INTEGER PRIMARY KEY, context TEXT)")
    conn.execute(
        "CREATE TABLE line_bits (file_id INTEGER, context_id INTEGER, "
        "numbits BLOB, FOREIGN KEY(file_id) REFERENCES file(id), "
        "FOREIGN KEY(context_id) REFERENCES context(id))"
    )

    file_ids = {}
    ctx_ids = {}
    file_counter = 0
    ctx_counter = 0

    for file_path, context in rows:
        if file_path not in file_ids:
            file_counter += 1
            file_ids[file_path] = file_counter
            conn.execute("INSERT INTO file VALUES (?, ?)", (file_counter, file_path))
        if context not in ctx_ids:
            ctx_counter += 1
            ctx_ids[context] = ctx_counter
            conn.execute("INSERT INTO context VALUES (?, ?)", (ctx_counter, context))
        conn.execute(
            "INSERT INTO line_bits VALUES (?, ?, ?)",
            (file_ids[file_path], ctx_ids[context], b"\x01"),
        )

    conn.commit()
    conn.close()


class TestLoadTestImpactMapping:
    def test_no_coverage_file(self, tmp_path):
        result = load_test_impact_mapping(str(tmp_path))
        assert result is None

    def test_valid_coverage_db(self, tmp_path):
        root = str(tmp_path.resolve())
        src = tmp_path / "lintgate" / "core.py"
        src.parent.mkdir(parents=True)
        src.write_text("pass")

        _create_coverage_db(
            tmp_path / ".coverage",
            [
                (str(src.resolve()), "tests/test_core.py::test_one|run"),
                (str(src.resolve()), "tests/test_core.py::test_two|run"),
            ],
        )

        mapping = load_test_impact_mapping(root)
        assert mapping is not None
        assert "lintgate/core.py" in mapping
        assert "tests/test_core.py::test_one" in mapping["lintgate/core.py"]
        assert "tests/test_core.py::test_two" in mapping["lintgate/core.py"]

    def test_corrupt_file_returns_none(self, tmp_path):
        cov = tmp_path / ".coverage"
        cov.write_text("not a sqlite database")
        result = load_test_impact_mapping(str(tmp_path))
        assert result is None

    def test_empty_db_returns_none(self, tmp_path):
        _create_coverage_db(tmp_path / ".coverage", [])
        result = load_test_impact_mapping(str(tmp_path))
        assert result is None

    def test_missing_tables_returns_none(self, tmp_path):
        cov = tmp_path / ".coverage"
        conn = sqlite3.connect(str(cov))
        conn.execute("CREATE TABLE other (id INTEGER)")
        conn.commit()
        conn.close()
        result = load_test_impact_mapping(str(tmp_path))
        assert result is None

    def test_test_files_excluded_as_keys(self, tmp_path):
        root = str(tmp_path.resolve())
        src = tmp_path / "lintgate" / "core.py"
        src.parent.mkdir(parents=True)
        src.write_text("pass")
        test_file = tmp_path / "tests" / "test_core.py"
        test_file.parent.mkdir(parents=True)
        test_file.write_text("pass")

        _create_coverage_db(
            tmp_path / ".coverage",
            [
                (str(src.resolve()), "tests/test_core.py::test_one|run"),
                (str(test_file.resolve()), "tests/test_core.py::test_one|run"),
            ],
        )

        mapping = load_test_impact_mapping(root)
        assert mapping is not None
        # Source file present, test file excluded as key
        assert "lintgate/core.py" in mapping
        assert not any("test_" in k for k in mapping)

    def test_deduplicates_test_ids(self, tmp_path):
        root = str(tmp_path.resolve())
        src = tmp_path / "mod.py"
        src.write_text("pass")

        _create_coverage_db(
            tmp_path / ".coverage",
            [
                (str(src.resolve()), "tests/test_mod.py::test_a|run"),
                (str(src.resolve()), "tests/test_mod.py::test_a|run"),
            ],
        )

        mapping = load_test_impact_mapping(root)
        assert mapping is not None
        assert len(mapping["mod.py"]) == 1

    def test_empty_context_excluded(self, tmp_path):
        root = str(tmp_path.resolve())
        src = tmp_path / "mod.py"
        src.write_text("pass")

        _create_coverage_db(
            tmp_path / ".coverage",
            [
                (str(src.resolve()), ""),
                (str(src.resolve()), "tests/test_mod.py::test_a|run"),
            ],
        )

        mapping = load_test_impact_mapping(root)
        assert mapping is not None
        assert len(mapping["mod.py"]) == 1


class TestGetTestsForFile:
    def test_existing_key(self):
        mapping = {"core.py": ["test_a", "test_b"]}
        assert get_tests_for_file(mapping, "core.py") == ["test_a", "test_b"]

    def test_missing_key(self):
        mapping = {"core.py": ["test_a"]}
        assert get_tests_for_file(mapping, "other.py") == []


class TestStripContextSuffix:
    def test_strip_run(self):
        assert _strip_context_suffix("test::foo|run") == "test::foo"

    def test_strip_setup(self):
        assert _strip_context_suffix("test::foo|setup") == "test::foo"

    def test_no_suffix(self):
        assert _strip_context_suffix("test::foo") == "test::foo"


class TestIsTestFile:
    def test_prefix(self):
        assert _is_test_file("tests/test_core.py") is True

    def test_suffix(self):
        assert _is_test_file("tests/core_test.py") is True

    def test_source_file(self):
        assert _is_test_file("lintgate/core.py") is False
