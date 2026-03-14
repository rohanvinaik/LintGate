"""Tests for PR1 discovery fixes — fail-closed guardrails, scoped prepass, canonical discovery.

Covers:
- specification_tools._resolve_py_files fail-closed behavior
- convergence_tools._discover_python_files canonical discovery
- runtime._scoped_discover prepass scoping with path sanitization
"""

from __future__ import annotations

import os

# ── _resolve_py_files guardrails ─────────────────────────────────────


class TestResolvePyFiles:
    """Fail-closed guardrails for specification tool file discovery."""

    def test_single_file_shortcircuit(self, tmp_path):
        from mcp_tools.specification_tools import _resolve_py_files

        src = tmp_path / "mod.py"
        src.write_text("x = 1\n")

        result = _resolve_py_files(str(tmp_path), "mod.py")
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0].endswith("mod.py")

    def test_single_file_not_found(self, tmp_path):
        from mcp_tools.specification_tools import _resolve_py_files

        result = _resolve_py_files(str(tmp_path), "nonexistent.py")
        assert isinstance(result, dict)
        assert "error" in result

    def test_canonical_discovery_excludes_hidden(self, tmp_path):
        from mcp_tools.specification_tools import _resolve_py_files

        (tmp_path / "visible.py").write_text("x = 1\n")
        hidden = tmp_path / ".hidden"
        hidden.mkdir()
        (hidden / "secret.py").write_text("x = 1\n")

        result = _resolve_py_files(str(tmp_path), None)
        assert isinstance(result, list)
        assert any("visible.py" in f for f in result)
        assert not any(".hidden" in f for f in result)

    def test_file_budget_exceeded(self, tmp_path, monkeypatch):
        from mcp_tools import _specification_helpers
        from mcp_tools.specification_tools import _resolve_py_files

        monkeypatch.setattr(_specification_helpers, "_MAX_FILES_PER_RUN", 2)

        for i in range(5):
            (tmp_path / f"mod{i}.py").write_text(f"x = {i}\n")

        result = _resolve_py_files(str(tmp_path), None)
        assert isinstance(result, dict)
        assert "error" in result
        assert "File budget exceeded" in result["error"]
        assert "file_budget" in result

    def test_line_budget_exceeded(self, tmp_path, monkeypatch):
        from mcp_tools import _specification_helpers
        from mcp_tools.specification_tools import _resolve_py_files

        monkeypatch.setattr(_specification_helpers, "_MAX_TOTAL_LINES", 5)

        (tmp_path / "big.py").write_text("\n".join(f"x{i} = {i}" for i in range(20)) + "\n")

        result = _resolve_py_files(str(tmp_path), None)
        assert isinstance(result, dict)
        assert "error" in result
        assert "Line budget exceeded" in result["error"]
        assert "line_budget" in result

    def test_absolute_file_path(self, tmp_path):
        from mcp_tools.specification_tools import _resolve_py_files

        src = tmp_path / "mod.py"
        src.write_text("x = 1\n")

        result = _resolve_py_files(str(tmp_path), str(src))
        assert isinstance(result, list)
        assert len(result) == 1


# ── convergence_tools canonical discovery ────────────────────────────


class TestConvergenceCanonicalDiscovery:
    """Verify convergence_tools uses canonical discovery (no raw os.walk)."""

    def test_excludes_archive_dir(self, tmp_path):
        from mcp_tools.convergence_tools import _discover_python_files

        (tmp_path / "main.py").write_text("x = 1\n")
        archive = tmp_path / "archive"
        archive.mkdir()
        (archive / "old.py").write_text("x = 1\n")

        files = _discover_python_files(str(tmp_path))
        basenames = [os.path.basename(f) for f in files]
        assert "main.py" in basenames
        assert "old.py" not in basenames

    def test_excludes_pycache(self, tmp_path):
        from mcp_tools.convergence_tools import _discover_python_files

        (tmp_path / "main.py").write_text("x = 1\n")
        cache = tmp_path / "__pycache__"
        cache.mkdir()
        # Create a .py file inside __pycache__/ so that discovery must
        # actually exclude the directory (a .pyc file would be ignored
        # by suffix filtering alone, not testing directory exclusion).
        (cache / "cached_mod.py").write_text("x = 1\n")

        files = _discover_python_files(str(tmp_path))
        assert not any("__pycache__" in f for f in files)


# ── _scoped_discover prepass scoping ─────────────────────────────────


class TestScopedDiscover:
    """Prepass scoping honors files_changed and sanitizes paths."""

    def test_scopes_to_changed_files(self, tmp_path):
        from lintgate.controlplane.runtime import _scoped_discover
        from lintgate.controlplane.types import SupervisionEvent

        (tmp_path / "a.py").write_text("x = 1\n")
        (tmp_path / "b.py").write_text("y = 2\n")
        (tmp_path / "c.py").write_text("z = 3\n")

        event = SupervisionEvent(
            project_root=str(tmp_path),
            files_changed=[str(tmp_path / "a.py")],
        )
        result = _scoped_discover(event)
        assert len(result) == 1
        assert result[0].endswith("a.py")

    def test_falls_back_to_full_for_many_files(self, tmp_path):
        from lintgate.controlplane.runtime import _scoped_discover
        from lintgate.controlplane.types import SupervisionEvent

        for i in range(10):
            (tmp_path / f"mod{i}.py").write_text(f"x = {i}\n")

        event = SupervisionEvent(
            project_root=str(tmp_path),
            files_changed=[str(tmp_path / f"mod{i}.py") for i in range(10)],
        )
        result = _scoped_discover(event)
        # More than 5 changed files → falls back to full discovery
        assert len(result) >= 10

    def test_falls_back_when_no_files_changed(self, tmp_path):
        from lintgate.controlplane.runtime import _scoped_discover
        from lintgate.controlplane.types import SupervisionEvent

        (tmp_path / "a.py").write_text("x = 1\n")

        event = SupervisionEvent(
            project_root=str(tmp_path),
            files_changed=[],
        )
        result = _scoped_discover(event)
        assert len(result) >= 1

    def test_rejects_paths_outside_project(self, tmp_path, monkeypatch):
        from lintgate.controlplane.runtime import _scoped_discover
        from lintgate.controlplane.types import SupervisionEvent

        (tmp_path / "a.py").write_text("x = 1\n")

        # Mock isfile so the external path passes the existence check —
        # this forces the test to exercise the path-boundary rejection
        # rather than falling back because isfile returns False.
        _real_isfile = os.path.isfile

        def _patched_isfile(p):
            if p == "/etc/passwd.py" or p == os.path.abspath("/etc/passwd.py"):
                return True
            return _real_isfile(p)

        monkeypatch.setattr(os.path, "isfile", _patched_isfile)

        event = SupervisionEvent(
            project_root=str(tmp_path),
            files_changed=["/etc/passwd.py"],
        )
        result = _scoped_discover(event)
        # Path outside project root → rejected by boundary check, falls back
        assert not any("/etc/passwd" in f for f in result)

    def test_ignores_non_python_files(self, tmp_path):
        from lintgate.controlplane.runtime import _scoped_discover
        from lintgate.controlplane.types import SupervisionEvent

        (tmp_path / "a.py").write_text("x = 1\n")
        (tmp_path / "readme.md").write_text("# readme\n")

        event = SupervisionEvent(
            project_root=str(tmp_path),
            files_changed=[
                str(tmp_path / "a.py"),
                str(tmp_path / "readme.md"),
            ],
        )
        result = _scoped_discover(event)
        # Only the .py file should be in the scoped result
        assert len(result) == 1
        assert result[0].endswith("a.py")

    def test_relative_paths_resolved(self, tmp_path):
        from lintgate.controlplane.runtime import _scoped_discover
        from lintgate.controlplane.types import SupervisionEvent

        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "mod.py").write_text("x = 1\n")

        event = SupervisionEvent(
            project_root=str(tmp_path),
            files_changed=["sub/mod.py"],
        )
        result = _scoped_discover(event)
        assert len(result) == 1
        assert result[0].endswith("mod.py")
