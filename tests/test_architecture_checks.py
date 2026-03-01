"""Per-check tests for architecture checker sub-modules.

Covers: _helpers, layer_contracts, circular_imports, module_exports,
and the ArchitectureChecker thin-orchestrator integration.
"""

from __future__ import annotations

import os
import textwrap

from lintgate.linters.architecture_checker import ArchitectureChecker
from lintgate.linters.architecture_checks._helpers import (
    build_layer_map,
    extract_imports,
    filepath_to_module,
    find_layer,
    is_project_local,
    module_matches_prefix,
)
from lintgate.linters.architecture_checks.circular_imports import check_circular_imports
from lintgate.linters.architecture_checks.layer_contracts import check_layer_contracts
from lintgate.linters.architecture_checks.module_exports import check_module_exports
from lintgate.types import LinterContext

# ─── Helpers ──────────────────────────────────────────────────────────


class TestExtractImports:
    def test_import_statement(self, tmp_path):
        f = tmp_path / "mod.py"
        f.write_text("import os\nimport sys\n")
        imports = extract_imports(str(f))
        modules = [m for m, _ in imports]
        assert "os" in modules
        assert "sys" in modules

    def test_from_import(self, tmp_path):
        f = tmp_path / "mod.py"
        f.write_text("from pathlib import Path\n")
        imports = extract_imports(str(f))
        assert ("pathlib", 1) in imports

    def test_relative_import_skipped(self, tmp_path):
        f = tmp_path / "mod.py"
        f.write_text("from . import sibling\n")
        imports = extract_imports(str(f))
        assert len(imports) == 0

    def test_syntax_error_returns_empty(self, tmp_path):
        f = tmp_path / "bad.py"
        f.write_text("def broken(:\n")
        imports = extract_imports(str(f))
        assert imports == []

    def test_missing_file_returns_empty(self):
        imports = extract_imports("/nonexistent/path.py")
        assert imports == []


class TestFilepathToModule:
    def test_regular_file(self, tmp_path):
        result = filepath_to_module(str(tmp_path / "app" / "views.py"), str(tmp_path))
        assert result == "app.views"

    def test_init_file(self, tmp_path):
        result = filepath_to_module(
            str(tmp_path / "app" / "__init__.py"), str(tmp_path)
        )
        assert result == "app"

    def test_src_prefix_stripped(self, tmp_path):
        result = filepath_to_module(
            str(tmp_path / "src" / "app" / "models.py"), str(tmp_path)
        )
        assert result == "app.models"

    def test_non_python_returns_none(self, tmp_path):
        result = filepath_to_module(str(tmp_path / "data.json"), str(tmp_path))
        assert result is None


class TestIsProjectLocal:
    def test_local_module(self, tmp_path):
        (tmp_path / "mymod.py").write_text("")
        assert is_project_local("mymod", str(tmp_path)) is True

    def test_local_package(self, tmp_path):
        pkg = tmp_path / "mypkg"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("")
        assert is_project_local("mypkg.sub", str(tmp_path)) is True

    def test_external_module(self, tmp_path):
        assert is_project_local("requests", str(tmp_path)) is False


class TestModuleMatchesPrefix:
    def test_exact_match(self):
        assert module_matches_prefix("app.models", "app.models") is True

    def test_child_match(self):
        assert module_matches_prefix("app.models.user", "app.models") is True

    def test_no_match(self):
        assert module_matches_prefix("app.views", "app.models") is False

    def test_partial_name_no_match(self):
        assert module_matches_prefix("app.models_v2", "app.models") is False


class TestBuildLayerMapAndFindLayer:
    def test_build_and_find(self):
        layers = [
            {"name": "core", "modules": ["app.core"]},
            {"name": "api", "modules": ["app.api"]},
        ]
        layer_map = build_layer_map(layers)
        assert "app.core" in layer_map
        assert "app.api" in layer_map

        found = find_layer("app.core.utils", layer_map)
        assert found is not None
        assert found["name"] == "core"

    def test_find_longest_prefix(self):
        layers = [
            {"name": "broad", "modules": ["app"]},
            {"name": "specific", "modules": ["app.core"]},
        ]
        layer_map = build_layer_map(layers)
        found = find_layer("app.core.models", layer_map)
        assert found["name"] == "specific"

    def test_find_no_match(self):
        layer_map = build_layer_map([{"name": "core", "modules": ["app.core"]}])
        assert find_layer("other.module", layer_map) is None


# ─── Layer Contracts ──────────────────────────────────────────────────


class TestLayerContracts:
    def _make_project(self, tmp_path, files: dict[str, str]):
        """Create a project with given file paths and contents."""
        for rel_path, content in files.items():
            full = tmp_path / rel_path
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_text(textwrap.dedent(content))
        return str(tmp_path)

    def test_forbidden_import_detected(self, tmp_path):
        root = self._make_project(
            tmp_path,
            {
                "app/__init__.py": "",
                "app/core/__init__.py": "",
                "app/core/models.py": "import app.api\n",
                "app/api/__init__.py": "",
            },
        )
        layers = [
            {
                "name": "core",
                "modules": ["app.core"],
                "must_not_import": ["app.api"],
                "may_import": [],
            },
            {
                "name": "api",
                "modules": ["app.api"],
                "must_not_import": [],
                "may_import": [],
            },
        ]
        ctx = LinterContext(
            files=[os.path.join(root, "app/core/models.py")],
            project_root=root,
            config={},
        )
        issues = list(check_layer_contracts(ctx.files, layers, ctx))
        assert len(issues) >= 1
        assert any(
            i.kind == "layer-violation" and i.severity == "blocking" for i in issues
        )

    def test_allowed_import_no_issue(self, tmp_path):
        root = self._make_project(
            tmp_path,
            {
                "app/__init__.py": "",
                "app/core/__init__.py": "",
                "app/core/models.py": "import app.utils\n",
                "app/utils/__init__.py": "",
            },
        )
        layers = [
            {
                "name": "core",
                "modules": ["app.core"],
                "must_not_import": [],
                "may_import": ["app.utils"],
            },
        ]
        ctx = LinterContext(
            files=[os.path.join(root, "app/core/models.py")],
            project_root=root,
            config={},
        )
        issues = list(check_layer_contracts(ctx.files, layers, ctx))
        forbidden = [i for i in issues if i.severity == "blocking"]
        assert len(forbidden) == 0

    def test_no_layers_no_issues(self, tmp_path):
        root = self._make_project(
            tmp_path,
            {
                "app.py": "import os\n",
            },
        )
        ctx = LinterContext(
            files=[os.path.join(root, "app.py")],
            project_root=root,
            config={},
        )
        issues = list(check_layer_contracts(ctx.files, [], ctx))
        assert len(issues) == 0


# ─── Circular Imports ─────────────────────────────────────────────────


class TestCircularImports:
    def test_cycle_detected(self, tmp_path):
        """Two files importing each other should trigger circular-import."""
        (tmp_path / "a.py").write_text("import b\n")
        (tmp_path / "b.py").write_text("import a\n")

        ctx = LinterContext(
            files=[str(tmp_path / "a.py"), str(tmp_path / "b.py")],
            project_root=str(tmp_path),
            config={},
        )
        issues = list(check_circular_imports(ctx.files, ctx))
        assert len(issues) >= 1
        assert any(i.kind == "circular-import" for i in issues)

    def test_no_cycle(self, tmp_path):
        """One-way dependency should not trigger circular-import."""
        (tmp_path / "a.py").write_text("import b\n")
        (tmp_path / "b.py").write_text("x = 1\n")

        ctx = LinterContext(
            files=[str(tmp_path / "a.py"), str(tmp_path / "b.py")],
            project_root=str(tmp_path),
            config={},
        )
        issues = list(check_circular_imports(ctx.files, ctx))
        circular = [i for i in issues if i.kind == "circular-import"]
        assert len(circular) == 0

    def test_external_import_ignored(self, tmp_path):
        """Importing an external package should not be flagged."""
        (tmp_path / "a.py").write_text("import requests\n")

        ctx = LinterContext(
            files=[str(tmp_path / "a.py")],
            project_root=str(tmp_path),
            config={},
        )
        issues = list(check_circular_imports(ctx.files, ctx))
        assert len(issues) == 0


# ─── Module Exports ──────────────────────────────────────────────────


class TestModuleExports:
    def test_under_limit(self, tmp_path):
        f = tmp_path / "mod.py"
        f.write_text("def foo(): pass\ndef bar(): pass\n")
        issues = list(check_module_exports([str(f)], max_exports=15))
        assert len(issues) == 0

    def test_over_limit(self, tmp_path):
        funcs = "\n".join(f"def func_{i}(): pass" for i in range(20))
        f = tmp_path / "mod.py"
        f.write_text(funcs)
        issues = list(check_module_exports([str(f)], max_exports=15))
        assert len(issues) == 1
        assert issues[0].kind == "too-many-exports"

    def test_private_names_excluded(self, tmp_path):
        funcs = "\n".join(f"def _private_{i}(): pass" for i in range(20))
        f = tmp_path / "mod.py"
        f.write_text(funcs)
        issues = list(check_module_exports([str(f)], max_exports=15))
        assert len(issues) == 0

    def test_all_overrides(self, tmp_path):
        """__all__ should be used as the export count."""
        source = "__all__ = ['a', 'b', 'c']\n"
        source += "\n".join(f"def func_{i}(): pass" for i in range(20))
        f = tmp_path / "mod.py"
        f.write_text(source)
        issues = list(check_module_exports([str(f)], max_exports=15))
        assert len(issues) == 0  # __all__ has only 3 entries

    def test_syntax_error_file(self, tmp_path):
        f = tmp_path / "bad.py"
        f.write_text("def broken(:\n")
        issues = list(check_module_exports([str(f)], max_exports=15))
        assert len(issues) == 0  # gracefully returns 0


# ─── ArchitectureChecker Integration ─────────────────────────────────


class TestArchitectureCheckerIntegration:
    def test_runs_checks(self, tmp_path):
        """Verify the thin orchestrator delegates to sub-modules."""
        (tmp_path / "a.py").write_text("import b\n")
        (tmp_path / "b.py").write_text("import a\n")

        ctx = LinterContext(
            files=[str(tmp_path / "a.py"), str(tmp_path / "b.py")],
            project_root=str(tmp_path),
            config={},
        )
        checker = ArchitectureChecker()
        issues = list(checker.run(ctx))
        assert any(i.kind == "circular-import" for i in issues)

    def test_clean_project_no_issues(self, tmp_path):
        f = tmp_path / "clean.py"
        f.write_text("def hello(): return 'world'\n")

        ctx = LinterContext(
            files=[str(f)],
            project_root=str(tmp_path),
            config={},
        )
        checker = ArchitectureChecker()
        issues = list(checker.run(ctx))
        assert len(issues) == 0

    def test_layer_config_used(self, tmp_path):
        """When layers are configured, layer_contracts should run."""
        (tmp_path / "app").mkdir()
        (tmp_path / "app" / "__init__.py").write_text("")
        (tmp_path / "app" / "core").mkdir()
        (tmp_path / "app" / "core" / "__init__.py").write_text("")
        (tmp_path / "app" / "core" / "m.py").write_text("import app.api\n")
        (tmp_path / "app" / "api").mkdir()
        (tmp_path / "app" / "api" / "__init__.py").write_text("")

        ctx = LinterContext(
            files=[str(tmp_path / "app" / "core" / "m.py")],
            project_root=str(tmp_path),
            config={
                "layers": [
                    {
                        "name": "core",
                        "modules": ["app.core"],
                        "must_not_import": ["app.api"],
                        "may_import": [],
                    },
                    {
                        "name": "api",
                        "modules": ["app.api"],
                        "must_not_import": [],
                        "may_import": [],
                    },
                ],
            },
        )
        checker = ArchitectureChecker()
        issues = list(checker.run(ctx))
        assert any(i.kind == "layer-violation" for i in issues)
