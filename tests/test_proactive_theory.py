"""Tests for proactive theory extraction (#182).

Covers:
1. extract_docstring_claims — Python module-level docstring extraction
2. check_theory_staleness — theory coverage of uncommitted files
3. extract_theory with working_tree_files — integrated extraction
4. check_session_readiness with git_context — session gate enhancement
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

from lintgate.context_auditor import check_session_readiness
from lintgate.theory_extractor import (
    check_theory_staleness,
    extract_docstring_claims,
    extract_theory,
)

# ── extract_docstring_claims ──────────────────────────────────────────


class TestExtractDocstringClaims:
    def test_extracts_from_module_docstring(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            py_file = os.path.join(tmpdir, "engine.py")
            Path(py_file).write_text(
                '"""Authority escalation engine.\n\n'
                "This module implements the core escalation logic because\n"
                "deterministic authority resolution prevents cascading failures.\n"
                "The design rationale is that each finding should have exactly\n"
                "one authoritative source of truth.\n"
                '"""\n\ndef run(): pass\n'
            )
            sections = extract_docstring_claims(tmpdir, ["engine.py"])
            assert len(sections) == 1
            assert sections[0].heading == "Module: engine"
            assert "escalation" in sections[0].body.lower()

    def test_skips_short_docstrings(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            py_file = os.path.join(tmpdir, "tiny.py")
            Path(py_file).write_text('"""Short."""\n')
            sections = extract_docstring_claims(tmpdir, ["tiny.py"])
            assert len(sections) == 0

    def test_skips_no_docstring(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            py_file = os.path.join(tmpdir, "no_doc.py")
            Path(py_file).write_text("import os\nx = 1\n")
            sections = extract_docstring_claims(tmpdir, ["no_doc.py"])
            assert len(sections) == 0

    def test_skips_syntax_errors(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            py_file = os.path.join(tmpdir, "bad.py")
            Path(py_file).write_text("def f(\n  # unterminated\n")
            sections = extract_docstring_claims(tmpdir, ["bad.py"])
            assert len(sections) == 0

    def test_skips_nonexistent_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            sections = extract_docstring_claims(tmpdir, ["nonexistent.py"])
            assert len(sections) == 0

    def test_skips_non_python(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            md_file = os.path.join(tmpdir, "readme.md")
            Path(md_file).write_text(
                "# This is a markdown file with lots of content here for testing"
            )
            sections = extract_docstring_claims(tmpdir, ["readme.md"])
            assert len(sections) == 0

    def test_handles_absolute_paths(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            py_file = os.path.join(tmpdir, "module.py")
            Path(py_file).write_text(
                '"""Design intent: this module provides the foundation for\n'
                "compositional architecture because each layer builds on the\n"
                "previous layer's guarantees without violating encapsulation.\n"
                '"""\n'
            )
            sections = extract_docstring_claims(tmpdir, [py_file])
            assert len(sections) == 1

    def test_multiple_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            for name in ["a.py", "b.py", "c.py"]:
                Path(os.path.join(tmpdir, name)).write_text(
                    f'"""Module {name}: this implements a significant piece of\n'
                    f"the system architecture because it handles the core\n"
                    f"responsibility of {name} processing and validation.\n"
                    f'"""\n'
                )
            sections = extract_docstring_claims(tmpdir, ["a.py", "b.py", "c.py"])
            assert len(sections) == 3

    def test_empty_file_list(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            sections = extract_docstring_claims(tmpdir, [])
            assert len(sections) == 0


# ── check_theory_staleness ────────────────────────────────────────────


class TestCheckTheoryStaleness:
    def test_no_uncommitted_files(self):
        result = check_theory_staleness(
            "/project",
            theory_profile={"core_theory": []},
            git_context={"modified_files": [], "untracked_files": []},
        )
        assert not result["stale"]
        assert result["uncovered_files"] == []

    def test_no_theory_profile(self):
        result = check_theory_staleness(
            "/project",
            theory_profile=None,
            git_context={
                "modified_files": ["lintgate/engine.py"],
                "untracked_files": [],
            },
        )
        assert result["stale"]
        assert "No theory profile" in result["recommendation"]

    def test_uncovered_files_with_docstrings(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create an uncommitted file with a substantive docstring
            py_path = os.path.join(tmpdir, "lintgate", "new_module.py")
            os.makedirs(os.path.dirname(py_path), exist_ok=True)
            Path(py_path).write_text(
                '"""New module for authority escalation.\n\n'
                "This implements the core escalation logic because\n"
                "deterministic authority resolution prevents failures.\n"
                '"""\n'
            )

            profile = {
                "core_theory": [
                    {
                        "source": "docs/design.md:10",
                        "heading": "Design",
                        "claims": ["claim1"],
                    }
                ],
            }
            result = check_theory_staleness(
                tmpdir,
                theory_profile=profile,
                git_context={
                    "modified_files": [],
                    "untracked_files": ["lintgate/new_module.py"],
                },
            )
            assert result["stale"]
            assert "lintgate/new_module.py" in result["uncovered_files"]
            assert "build_theory_pack" in result["recommendation"]

    def test_covered_files_not_stale(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            py_path = os.path.join(tmpdir, "module.py")
            Path(py_path).write_text(
                '"""Module with design intent documentation here\n'
                "that explains the architectural rationale behind\n"
                "the implementation choices made in this module.\n"
                '"""\n'
            )

            profile = {
                "core_theory": [
                    {"source": "module.py:1", "heading": "Module", "claims": ["claim1"]}
                ],
            }
            result = check_theory_staleness(
                tmpdir,
                theory_profile=profile,
                git_context={
                    "modified_files": ["module.py"],
                    "untracked_files": [],
                },
            )
            assert not result["stale"]

    def test_filters_test_files(self):
        result = check_theory_staleness(
            "/project",
            theory_profile=None,
            git_context={
                "modified_files": ["tests/test_foo.py"],
                "untracked_files": ["test_bar.py"],
            },
        )
        assert not result["stale"]
        assert result["total_uncommitted_py"] == 0

    def test_filters_pycache(self):
        result = check_theory_staleness(
            "/project",
            theory_profile=None,
            git_context={
                "modified_files": ["__pycache__/foo.py"],
                "untracked_files": [],
            },
        )
        assert not result["stale"]

    def test_files_without_docstrings_not_flagged(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            py_path = os.path.join(tmpdir, "simple.py")
            Path(py_path).write_text("import os\nx = 1\n")

            profile: dict[str, Any] = {"core_theory": []}
            result = check_theory_staleness(
                tmpdir,
                theory_profile=profile,
                git_context={
                    "modified_files": ["simple.py"],
                    "untracked_files": [],
                },
            )
            assert not result["stale"]


# ── extract_theory with working_tree_files ────────────────────────────


class TestExtractTheoryWithWorkingTree:
    def test_includes_docstring_sources_count(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a minimal project
            Path(os.path.join(tmpdir, "README.md")).write_text("# Project\n")
            py_path = os.path.join(tmpdir, "engine.py")
            Path(py_path).write_text(
                '"""The core theory is that deterministic supervision\n'
                "prevents cascading failures because each finding has\n"
                "exactly one authoritative source rather than multiple\n"
                "competing resolution paths that cause confusion.\n"
                '"""\n'
            )

            result = extract_theory(tmpdir, working_tree_files=["engine.py"])
            assert "docstring_sources" in result
            assert result["docstring_sources"] >= 1

    def test_no_docstring_sources_without_working_tree(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            Path(os.path.join(tmpdir, "README.md")).write_text("# Project\n")
            result = extract_theory(tmpdir)
            assert "docstring_sources" not in result

    def test_backward_compatible_without_arg(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            Path(os.path.join(tmpdir, "README.md")).write_text("# Project\n")
            # Should work exactly like before
            result = extract_theory(tmpdir)
            assert "theory_profile" in result
            assert "docs_scanned" in result


# ── check_session_readiness with git_context ──────────────────────────


class TestSessionReadinessWithGitContext:
    def _make_profile(self):
        """Build a minimal valid theory profile."""
        return {
            "core_theory": [{"claims": ["claim1"], "source": "docs/design.md:1"}],
            "problem_solving": [{"claims": ["claim2"], "source": "docs/design.md:5"}],
            "alignment": [{"claims": ["claim3"], "source": "docs/design.md:10"}],
        }

    def test_ready_without_git_context(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create CLAUDE.md with enforceable rules
            Path(os.path.join(tmpdir, "CLAUDE.md")).write_text(
                "# Rules\n<!-- LINTGATE_FORBID_REGEX: foo -->\n"
            )
            result = check_session_readiness(tmpdir, theory_profile=self._make_profile())
            assert result.ready

    def test_stale_theory_with_uncommitted_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            Path(os.path.join(tmpdir, "CLAUDE.md")).write_text(
                "# Rules\n<!-- LINTGATE_FORBID_REGEX: foo -->\n"
            )
            # Create uncommitted file with docstring
            py_path = os.path.join(tmpdir, "new_engine.py")
            Path(py_path).write_text(
                '"""New engine module with significant design intent\n'
                "that explains why deterministic resolution is critical\n"
                "for preventing cascading authority failures.\n"
                '"""\n'
            )

            result = check_session_readiness(
                tmpdir,
                theory_profile=self._make_profile(),
                git_context={
                    "modified_files": [],
                    "untracked_files": ["new_engine.py"],
                },
            )
            assert not result.ready
            stale_items = [m for m in result.missing if m.startswith("theory_stale:")]
            assert len(stale_items) == 1
            assert "build_theory_pack" in result.recommendation

    def test_no_stale_when_no_uncommitted(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            Path(os.path.join(tmpdir, "CLAUDE.md")).write_text(
                "# Rules\n<!-- LINTGATE_FORBID_REGEX: foo -->\n"
            )
            result = check_session_readiness(
                tmpdir,
                theory_profile=self._make_profile(),
                git_context={
                    "modified_files": [],
                    "untracked_files": [],
                },
            )
            assert result.ready

    def test_backward_compatible_without_git_context(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            Path(os.path.join(tmpdir, "CLAUDE.md")).write_text(
                "# Rules\n<!-- LINTGATE_FORBID_REGEX: foo -->\n"
            )
            # Old call signature (no git_context kwarg) still works
            result = check_session_readiness(tmpdir, theory_profile=self._make_profile())
            assert result.ready
            # Backward compat: missing field should not appear
            assert not any("theory_stale" in m for m in result.missing)
