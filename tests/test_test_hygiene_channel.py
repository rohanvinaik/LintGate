"""Tests for the test_hygiene channel and MCP tool."""

from __future__ import annotations

import textwrap
from unittest.mock import patch

from lintgate.channels.test_hygiene_channel import (
    TestHygieneChannel,
    _build_test_fingerprints,
    _extract_test_functions,
    _is_stub_body,
    _parse_file,
    _thygiene001_stub_tests,
    _thygiene003_duplicates,
)
from lintgate.controlplane.types import (
    ChannelConfig,
    ControlPlaneConfig,
    SupervisionEvent,
)


def _load_tool_result(json_str):
    import json as _j
    import os as _os
    r = _j.loads(json_str)
    if isinstance(r, dict) and "file" in r and "analysis_id" in r and _os.path.isfile(r.get("file","")):
        with open(r["file"]) as f: return _j.loads(f.read())
    return r


# ── Helpers ──────────────────────────────────────────────────────────


def _write_test_file(tmp_path, name, content):
    """Write a test file and return its path."""
    filepath = tmp_path / name
    filepath.write_text(textwrap.dedent(content))
    return str(filepath)


# ── _is_stub_body tests ─────────────────────────────────────────────


class TestIsStubBody:
    def test_pass_stub(self, tmp_path):
        src = "def test_foo():\n    pass\n"
        path = _write_test_file(tmp_path, "test_stub.py", src)
        tree = _parse_file(path)
        funcs = _extract_test_functions(tree)  # type: ignore[arg-type]  # tree is Module (file is valid)
        assert len(funcs) == 1
        assert _is_stub_body(funcs[0][1]) == "pass"

    def test_ellipsis_stub(self, tmp_path):
        src = "def test_foo():\n    ...\n"
        path = _write_test_file(tmp_path, "test_stub.py", src)
        tree = _parse_file(path)
        funcs = _extract_test_functions(tree)  # type: ignore[arg-type]  # tree is Module (file is valid)
        assert _is_stub_body(funcs[0][1]) == "ellipsis"

    def test_not_implemented_stub(self, tmp_path):
        src = "def test_foo():\n    raise NotImplementedError()\n"
        path = _write_test_file(tmp_path, "test_stub.py", src)
        tree = _parse_file(path)
        funcs = _extract_test_functions(tree)  # type: ignore[arg-type]  # tree is Module (file is valid)
        assert _is_stub_body(funcs[0][1]) == "not_implemented"

    def test_docstring_then_pass(self, tmp_path):
        src = 'def test_foo():\n    """Docstring."""\n    pass\n'
        path = _write_test_file(tmp_path, "test_stub.py", src)
        tree = _parse_file(path)
        funcs = _extract_test_functions(tree)  # type: ignore[arg-type]  # tree is Module (file is valid)
        assert _is_stub_body(funcs[0][1]) == "pass"

    def test_real_body_not_stub(self, tmp_path):
        src = "def test_foo():\n    assert 1 == 1\n"
        path = _write_test_file(tmp_path, "test_stub.py", src)
        tree = _parse_file(path)
        funcs = _extract_test_functions(tree)  # type: ignore[arg-type]  # tree is Module (file is valid)
        assert _is_stub_body(funcs[0][1]) is None


# ── THYGIENE001 tests ────────────────────────────────────────────────


class TestTHYGIENE001:
    def test_detects_stub_tests(self, tmp_path):
        path = _write_test_file(
            tmp_path,
            "test_stubs.py",
            """\
            def test_one():
                pass

            def test_two():
                assert 1 == 1

            def test_three():
                ...
            """,
        )
        findings = _thygiene001_stub_tests([path])
        assert len(findings) == 2
        kinds = {f.evidence["body_type"] for f in findings}
        assert kinds == {"pass", "ellipsis"}
        assert all(f.kind == "THYGIENE001" for f in findings)

    def test_class_methods_detected(self, tmp_path):
        path = _write_test_file(
            tmp_path,
            "test_class.py",
            """\
            class TestFoo:
                def test_stub(self):
                    pass

                def test_real(self):
                    assert True
            """,
        )
        findings = _thygiene001_stub_tests([path])
        assert len(findings) == 1
        assert "TestFoo.test_stub" in findings[0].message


# ── THYGIENE003 duplicate tests ──────────────────────────────────────


class TestTHYGIENE003:
    def test_byte_identical_cross_file(self, tmp_path):
        body = """\
        def test_add():
            assert 1 + 1 == 2
        """
        path_a = _write_test_file(tmp_path, "test_a.py", body)
        path_b = _write_test_file(tmp_path, "test_b.py", body)
        findings, repairs = _thygiene003_duplicates([path_a, path_b], str(tmp_path))
        dup_findings = [f for f in findings if f.kind == "THYGIENE003"]
        assert len(dup_findings) >= 1
        assert dup_findings[0].evidence["duplicate_type"] == "byte_identical"

    def test_ast_equivalent_detected(self, tmp_path):
        path_a = _write_test_file(
            tmp_path,
            "test_a.py",
            """\
            def test_add():
                assert 1 + 1 == 2
            """,
        )
        path_b = _write_test_file(
            tmp_path,
            "test_b.py",
            """\
            def test_add():
                # Extra comment
                assert 1 + 1 == 2
            """,
        )
        findings, _ = _thygiene003_duplicates([path_a, path_b], str(tmp_path))
        # The comment changes the body source but AST is the same
        # (comments are stripped by parser)
        assert any(f.kind == "THYGIENE003" for f in findings)

    def test_no_false_positive_same_file(self, tmp_path):
        path = _write_test_file(
            tmp_path,
            "test_a.py",
            """\
            def test_one():
                assert 1 == 1

            def test_two():
                assert 1 == 1
            """,
        )
        findings, _ = _thygiene003_duplicates([path], str(tmp_path))
        # Same file duplicates should NOT be flagged (cross-file only)
        dup_findings = [f for f in findings if f.kind == "THYGIENE003"]
        assert len(dup_findings) == 0


# ── THYGIENE005 file subsumption ─────────────────────────────────────


class TestTHYGIENE005:
    def test_subsumed_file_detected(self, tmp_path):
        small = _write_test_file(
            tmp_path,
            "test_small.py",
            """\
            def test_a():
                assert 1 == 1
            """,
        )
        big = _write_test_file(
            tmp_path,
            "test_big.py",
            """\
            def test_a():
                assert 1 == 1

            def test_b():
                assert 2 == 2
            """,
        )
        findings, repairs = _thygiene003_duplicates([small, big], str(tmp_path))
        sub_findings = [f for f in findings if f.kind == "THYGIENE005"]
        assert len(sub_findings) == 1
        assert "test_small.py" in sub_findings[0].message
        assert len(repairs) >= 1
        assert repairs[0].kind == "safe_delete"


# ── Fingerprint tests ────────────────────────────────────────────────


class TestFingerprints:
    def test_fingerprints_include_class_methods(self, tmp_path):
        path = _write_test_file(
            tmp_path,
            "test_fp.py",
            """\
            class TestBar:
                def test_method(self):
                    assert True

            def test_top():
                assert False
            """,
        )
        fps = _build_test_fingerprints([path])
        names = {fp["name"] for fp in fps}
        assert names == {"test_method", "test_top"}
        classes = {fp["class_name"] for fp in fps}
        assert classes == {"TestBar", None}


# ── Channel integration ─────────────────────────────────────────────


class TestTestHygieneChannel:
    def test_channel_protocol(self):
        ch = TestHygieneChannel()
        assert ch.name == "test_hygiene"
        assert ch.timeout_ms == 10000
        assert ch.blocking_capable is False

    def test_channel_skips_without_project_root(self):
        ch = TestHygieneChannel()
        event = SupervisionEvent(surface="mcp", project_root="")
        config = ControlPlaneConfig(enabled=True)
        assert ch.should_run(event, config) is False

    def test_channel_skips_no_test_files(self, tmp_path):
        ch = TestHygieneChannel()
        event = SupervisionEvent(surface="mcp", project_root=str(tmp_path))
        config = ControlPlaneConfig(enabled=True)
        result = ch.execute(event, config)
        assert result.status == "skip"
        assert result.metrics.get("reason") == "no_test_files"

    def test_channel_finds_stubs(self, tmp_path):
        test_dir = tmp_path / "tests"
        test_dir.mkdir()
        (test_dir / "__init__.py").touch()
        (test_dir / "test_example.py").write_text(
            "def test_stub():\n    pass\n\ndef test_real():\n    assert 1 == 1\n"
        )
        ch = TestHygieneChannel()
        event = SupervisionEvent(surface="mcp", project_root=str(tmp_path))
        config = ControlPlaneConfig(enabled=True)
        result = ch.execute(event, config)
        assert result.status == "fail"
        assert result.metrics["stub_tests"] >= 1
        assert any(f.kind == "THYGIENE001" for f in result.findings)

    def test_channel_file_filter(self, tmp_path):
        test_dir = tmp_path / "tests"
        test_dir.mkdir()
        (test_dir / "__init__.py").touch()
        (test_dir / "test_a.py").write_text("def test_stub():\n    pass\n")
        (test_dir / "test_b.py").write_text("def test_stub():\n    pass\n")

        ch = TestHygieneChannel()
        event = SupervisionEvent(surface="mcp", project_root=str(tmp_path))
        config = ControlPlaneConfig(
            enabled=True,
            channels={
                "test_hygiene": ChannelConfig(enabled=True, settings={"file_filter": "test_a"})
            },
        )
        result = ch.execute(event, config)
        # Only test_a should be scanned
        assert all("test_a" in (f.file or "") for f in result.findings)


# ── MCP tool contract ────────────────────────────────────────────────


class TestTestHygieneScanTool:
    def test_tool_registered(self):
        from mcp_server import test_hygiene_scan

        assert callable(test_hygiene_scan)

    def test_tool_returns_json(self, tmp_path):

        from mcp_tools.test_hygiene_tools import register

        class FakeMCP:
            def tool(self):
                def decorator(fn):
                    self._fn = fn
                    return fn

                return decorator

        fake = FakeMCP()
        tools = register(fake, {})
        fn = tools["test_hygiene_scan"]

        test_dir = tmp_path / "tests"
        test_dir.mkdir()
        (test_dir / "__init__.py").touch()
        (test_dir / "test_x.py").write_text("def test_stub():\n    pass\n")

        result = fn(path=str(tmp_path))
        data = _load_tool_result(result)
        assert "status" in data
        assert "findings" in data
        assert "next_actions" in data
        assert "metrics" in data


# ── Safe delete repair execution ─────────────────────────────────────


class TestSafeDeleteExecution:
    def test_blocks_outside_project_root(self):
        from mcp_tools.controlplane_tools import _execute_safe_delete

        repair = {
            "action_id": "test123",
            "kind": "safe_delete",
            "payload": {"target_path": "/etc/passwd"},
        }
        result = _execute_safe_delete(repair, "/tmp/project", None)
        assert result["status"] == "blocked"

    def test_blocks_non_test_directory(self, tmp_path):
        from mcp_tools.controlplane_tools import _execute_safe_delete

        src_file = tmp_path / "src" / "main.py"
        src_file.parent.mkdir()
        src_file.write_text("x = 1")

        repair = {
            "action_id": "test123",
            "kind": "safe_delete",
            "payload": {"target_path": str(src_file)},
        }
        result = _execute_safe_delete(repair, str(tmp_path), None)
        assert result["status"] == "blocked"
        assert "not in test directory" in result["reason"]

    def test_deletes_test_file(self, tmp_path):
        from mcp_tools.controlplane_tools import _execute_safe_delete

        test_file = tmp_path / "tests" / "test_dup.py"
        test_file.parent.mkdir()
        test_file.write_text("def test_x(): pass")

        class FakeSession:
            pass

        with patch("lintgate.controlplane.session_memory.report_repair_outcome"):
            repair = {
                "action_id": "test456",
                "kind": "safe_delete",
                "payload": {"target_path": str(test_file)},
            }
            result = _execute_safe_delete(repair, str(tmp_path), FakeSession())

        assert result["status"] == "ok"
        assert not test_file.exists()

    def test_skips_already_deleted(self, tmp_path):
        from mcp_tools.controlplane_tools import _execute_safe_delete

        repair = {
            "action_id": "test789",
            "kind": "safe_delete",
            "payload": {"target_path": str(tmp_path / "tests" / "gone.py")},
        }
        result = _execute_safe_delete(repair, str(tmp_path), None)
        assert result["status"] == "skipped"
