"""Tests for safe module refactoring engine (refactor_move)."""

from __future__ import annotations

import os


class TestScanAllImports:
    """Test AST-based import scanning."""

    def _write_file(self, tmp_path, rel_path, content):
        full = tmp_path / rel_path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content)
        return str(full)

    def test_finds_import_statement(self, tmp_path):
        from lintgate.refactor_move import scan_all_imports

        self._write_file(tmp_path, "app.py", "import lintgate.old_module\n")
        refs = scan_all_imports(str(tmp_path), "lintgate.old_module")
        assert len(refs) == 1
        assert refs[0].kind == "import"
        assert "import lintgate.old_module" in refs[0].old_text

    def test_finds_from_import(self, tmp_path):
        from lintgate.refactor_move import scan_all_imports

        self._write_file(tmp_path, "app.py", "from lintgate.old_module import Foo\n")
        refs = scan_all_imports(str(tmp_path), "lintgate.old_module")
        assert len(refs) == 1
        assert refs[0].kind == "from_import"

    def test_finds_submodule_import(self, tmp_path):
        from lintgate.refactor_move import scan_all_imports

        self._write_file(tmp_path, "app.py", "from lintgate.old_module.sub import bar\n")
        refs = scan_all_imports(str(tmp_path), "lintgate.old_module")
        assert len(refs) == 1
        assert refs[0].kind == "from_import"

    def test_finds_mock_patch_string(self, tmp_path):
        from lintgate.refactor_move import scan_all_imports

        self._write_file(
            tmp_path,
            "test_app.py",
            'from unittest.mock import patch\npatch("lintgate.old_module.func")\n',
        )
        refs = scan_all_imports(str(tmp_path), "lintgate.old_module")
        assert any(r.kind == "string_ref" and r.context == "mock.patch" for r in refs)

    def test_finds_importlib_import_module_string(self, tmp_path):
        from lintgate.refactor_move import scan_all_imports

        self._write_file(
            tmp_path,
            "app.py",
            'import importlib\nimportlib.import_module("lintgate.old_module")\n',
        )
        refs = scan_all_imports(str(tmp_path), "lintgate.old_module")
        assert any(r.kind == "string_ref" and r.context == "importlib.import_module" for r in refs)

    def test_ignores_unrelated_strings(self, tmp_path):
        from lintgate.refactor_move import scan_all_imports

        self._write_file(
            tmp_path,
            "app.py",
            'x = "lintgate.old_module"\nprint(x)\n',
        )
        refs = scan_all_imports(str(tmp_path), "lintgate.old_module")
        # Should NOT find arbitrary string literals
        assert not any(r.kind == "string_ref" for r in refs)

    def test_skips_hidden_directories(self, tmp_path):
        from lintgate.refactor_move import scan_all_imports

        self._write_file(tmp_path, ".hidden/app.py", "import lintgate.old_module\n")
        self._write_file(tmp_path, "visible/app.py", "import lintgate.old_module\n")
        refs = scan_all_imports(str(tmp_path), "lintgate.old_module")
        assert len(refs) == 1
        assert ".hidden" not in refs[0].file

    def test_handles_syntax_error(self, tmp_path):
        from lintgate.refactor_move import scan_all_imports

        self._write_file(tmp_path, "bad.py", "def foo(:\n    pass\n")
        refs = scan_all_imports(str(tmp_path), "lintgate.old_module")
        assert refs == []

    def test_no_false_positives_for_partial_names(self, tmp_path):
        from lintgate.refactor_move import scan_all_imports

        self._write_file(tmp_path, "app.py", "import lintgate.old_module_extra\n")
        refs = scan_all_imports(str(tmp_path), "lintgate.old_module")
        # Should NOT match partial names (old_module_extra != old_module)
        assert len(refs) == 0


class TestComputeRewrites:
    """Test rewrite computation."""

    def test_rewrites_import(self):
        from lintgate.refactor_move import ImportReference, _compute_rewrites

        refs = [
            ImportReference(
                file="app.py",
                line=1,
                kind="import",
                old_text="import lintgate.old_module",
                new_text="",
            )
        ]
        _compute_rewrites(refs, "lintgate.old_module", "lintgate.new_module")
        assert refs[0].new_text == "import lintgate.new_module"

    def test_rewrites_from_import(self):
        from lintgate.refactor_move import ImportReference, _compute_rewrites

        refs = [
            ImportReference(
                file="app.py",
                line=1,
                kind="from_import",
                old_text="from lintgate.old_module import Foo",
                new_text="",
            )
        ]
        _compute_rewrites(refs, "lintgate.old_module", "lintgate.new_module")
        assert refs[0].new_text == "from lintgate.new_module import Foo"

    def test_rewrites_string_ref(self):
        from lintgate.refactor_move import ImportReference, _compute_rewrites

        refs = [
            ImportReference(
                file="test.py",
                line=5,
                kind="string_ref",
                old_text="lintgate.old_module.func",
                new_text="",
                context="mock.patch",
            )
        ]
        _compute_rewrites(refs, "lintgate.old_module", "lintgate.new_module")
        assert refs[0].new_text == "lintgate.new_module.func"


class TestGenerateShim:
    """Test backward-compatibility shim generation."""

    def test_generates_shim(self, tmp_path):
        from lintgate.refactor_move import generate_shim

        # Create destination module
        new_dir = tmp_path / "lintgate"
        new_dir.mkdir()
        (new_dir / "new_module.py").write_text(
            "def public_func():\n    pass\n\nclass PublicClass:\n    pass\n"
        )

        shim_path = generate_shim(str(tmp_path), "lintgate.old_module", "lintgate.new_module")
        assert os.path.exists(shim_path)

        with open(shim_path) as f:
            content = f.read()
        assert "from lintgate.new_module import *" in content
        assert "moved to lintgate.new_module" in content

    def test_shim_includes_public_names(self, tmp_path):
        from lintgate.refactor_move import generate_shim

        new_dir = tmp_path / "pkg"
        new_dir.mkdir()
        (new_dir / "new.py").write_text(
            "__all__ = ['exported_func', 'ExportedClass']\n"
            "def exported_func(): pass\n"
            "class ExportedClass: pass\n"
        )
        shim_path = generate_shim(str(tmp_path), "pkg.old", "pkg.new")
        with open(shim_path) as f:
            content = f.read()
        assert "__all__" in content
        assert "exported_func" in content


class TestSmokeTestImport:
    """Test subprocess-based import smoke gate."""

    def test_importable_module(self):
        from lintgate.refactor_move import smoke_test_import

        passed, error = smoke_test_import("os", "/tmp")
        assert passed is True
        assert error == ""

    def test_nonexistent_module(self):
        from lintgate.refactor_move import smoke_test_import

        passed, error = smoke_test_import("nonexistent_module_xyz_12345", "/tmp")
        assert passed is False
        assert error  # Should have error message


class TestMoveResult:
    """Test MoveResult serialization."""

    def test_to_dict_dry_run(self):
        from lintgate.refactor_move import ImportReference, MoveResult

        result = MoveResult(
            source="old.mod",
            destination="new.mod",
            dry_run=True,
            references_found=[
                ImportReference("a.py", 1, "import", "import old.mod", "import new.mod")
            ],
        )
        d = result.to_dict()
        assert d["source"] == "old.mod"
        assert d["destination"] == "new.mod"
        assert d["dry_run"] is True
        assert d["references_found"] == 1
        assert "references" in d

    def test_to_dict_degraded(self):
        from lintgate.refactor_move import MoveResult

        result = MoveResult(source="a", destination="b", dry_run=True, degraded=True)
        d = result.to_dict()
        assert d["degraded"] is True

    def test_to_dict_no_degraded_key_when_false(self):
        from lintgate.refactor_move import MoveResult

        result = MoveResult(source="a", destination="b", dry_run=True, degraded=False)
        d = result.to_dict()
        assert "degraded" not in d


class TestRefactorMove:
    """Test end-to-end refactor_move orchestration."""

    def _setup_project(self, tmp_path):
        """Create a minimal project structure."""
        pkg = tmp_path / "mypkg"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("")
        (pkg / "old_module.py").write_text(
            "def hello():\n    return 'world'\n\nclass MyClass:\n    pass\n"
        )
        (tmp_path / "app.py").write_text("from mypkg.old_module import hello\n\nprint(hello())\n")
        return str(tmp_path)

    def test_dry_run_finds_references(self, tmp_path):
        from lintgate.refactor_move import refactor_move

        project = self._setup_project(tmp_path)
        result = refactor_move(project, "mypkg.old_module", "mypkg.new_module", dry_run=True)
        assert result.dry_run is True
        assert len(result.references_found) >= 1
        assert not result.errors

    def test_missing_source_returns_error(self, tmp_path):
        from lintgate.refactor_move import refactor_move

        result = refactor_move(str(tmp_path), "nonexistent.module", "new.module", dry_run=True)
        assert len(result.errors) >= 1
        assert "not found" in result.errors[0].lower()

    def test_dry_run_no_file_changes(self, tmp_path):
        from lintgate.refactor_move import refactor_move

        project = self._setup_project(tmp_path)
        # Record original content
        app_content = (tmp_path / "app.py").read_text()
        refactor_move(project, "mypkg.old_module", "mypkg.new_module", dry_run=True)
        # Verify no changes
        assert (tmp_path / "app.py").read_text() == app_content
        assert (tmp_path / "mypkg" / "old_module.py").exists()
