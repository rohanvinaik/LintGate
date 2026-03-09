import pytest

from mcp_tools.controlplane_tools import _resolve_scope_files


def test_resolve_scope_files_files_scope():
    """Test standard scope='files' behavior, valid and invalid."""
    helpers = {"_collect_python_files": lambda p: ["/app/a.py", "/app/b.py", "/app/c.py"]}

    # Valid
    resolved = _resolve_scope_files("/app", "files", ["a.py", "b.py"], helpers)
    assert set(resolved) == {"/app/a.py", "/app/b.py"}

    # Invalid missing files
    with pytest.raises(ValueError, match="requires a non-empty files list"):
        _resolve_scope_files("/app", "files", [], helpers)

    with pytest.raises(ValueError, match="requires a non-empty files list"):
        _resolve_scope_files("/app", "files", None, helpers)


def test_resolve_scope_files_unknown_scope():
    """Test validation of unknown scope name."""
    helpers = {"_collect_python_files": lambda p: []}
    with pytest.raises(ValueError, match="Unknown scope: magic"):
        _resolve_scope_files("/app", "magic", None, helpers)


def test_resolve_scope_files_project_scope():
    """Test project scope returns all files via helper limit 50."""
    all_files = [f"/app/f{i}.py" for i in range(100)]
    helpers = {"_collect_python_files": lambda p: all_files}
    resolved = _resolve_scope_files("/app", "project", None, helpers)

    assert len(resolved) == 50
    assert resolved == all_files[:50]
