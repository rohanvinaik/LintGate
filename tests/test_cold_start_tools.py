"""Tests for cold-start bridge tools (test_triage, test_infer_inputs, test_characterize, test_characterize_mark)."""

from __future__ import annotations

import json
import textwrap

from mcp_tools.cold_start_tools import (
    _extract_signature,
    _find_call_sites,
    _find_output_patterns,
    _generate_characterization_test,
)

# ── Helpers ──────────────────────────────────────────────────────────


def _write_file(tmp_path, name, content):
    filepath = tmp_path / name
    filepath.parent.mkdir(parents=True, exist_ok=True)
    filepath.write_text(textwrap.dedent(content))
    return str(filepath)


# ── _extract_signature ───────────────────────────────────────────────


class TestExtractSignature:
    def test_basic_function(self, tmp_path):
        path = _write_file(
            tmp_path,
            "mod.py",
            """\
            def add(x: int, y: int) -> int:
                return x + y
            """,
        )
        sig = _extract_signature(path, "add")
        assert sig is not None
        assert len(sig["params"]) == 2
        assert sig["params"][0]["name"] == "x"
        assert sig["return_type_hint"] is not None
        assert sig["is_async"] is False

    def test_function_not_found(self, tmp_path):
        path = _write_file(tmp_path, "mod.py", "def foo(): pass\n")
        assert _extract_signature(path, "bar") is None

    def test_with_defaults(self, tmp_path):
        path = _write_file(
            tmp_path,
            "mod.py",
            """\
            def greet(name: str = "world"):
                return f"hello {name}"
            """,
        )
        sig = _extract_signature(path, "greet")
        assert sig is not None
        assert len(sig["defaults"]) == 1


# ── _find_call_sites ────────────────────────────────────────────────


class TestFindCallSites:
    def test_finds_direct_calls(self, tmp_path):
        _write_file(
            tmp_path,
            "src.py",
            """\
            def add(x, y):
                return x + y
            """,
        )
        caller = _write_file(
            tmp_path,
            "caller.py",
            """\
            from src import add
            result = add(1, 2)
            other = add(3, 4)
            """,
        )
        sites = _find_call_sites([caller], "add", 5)
        assert len(sites) == 2
        assert sites[0]["positional_args"] == ["1", "2"]

    def test_finds_method_calls(self, tmp_path):
        caller = _write_file(
            tmp_path,
            "caller.py",
            """\
            obj.process(data, timeout=30)
            """,
        )
        sites = _find_call_sites([caller], "process", 5)
        assert len(sites) == 1
        assert sites[0]["keyword_args"]["timeout"] == "30"


# ── _find_output_patterns ───────────────────────────────────────────


class TestFindOutputPatterns:
    def test_finds_attribute_access(self, tmp_path):
        path = _write_file(
            tmp_path,
            "use.py",
            """\
            result = compute(data)
            print(result.status)
            print(result.value)
            """,
        )
        patterns = _find_output_patterns([path], "compute")
        assert ".status" in patterns
        assert ".value" in patterns

    def test_finds_subscript_access(self, tmp_path):
        path = _write_file(
            tmp_path,
            "use.py",
            """\
            result = fetch("key")
            x = result["data"]
            """,
        )
        patterns = _find_output_patterns([path], "fetch")
        assert "['data']" in patterns


# ── _generate_characterization_test ─────────────────────────────────


class TestGenerateCharacterizationTest:
    def test_generates_valid_python(self):
        sig = {
            "params": [
                {"name": "x", "type_hint": "Name(id='int')"},
                {"name": "y", "type_hint": "Name(id='str')"},
            ],
            "return_type_hint": "Name(id='int')",
            "defaults": [],
            "is_async": False,
            "line": 1,
        }
        code = _generate_characterization_test("mymod", "compute", sig)
        assert "from mymod import compute" in code
        assert "def test_compute_characterization" in code
        assert "Maturity: unchecked" in code
        assert "x = 0" in code  # int type hint
        assert 'y = ""' in code  # str type hint

    def test_skips_self_param(self):
        sig = {
            "params": [
                {"name": "self"},
                {"name": "data", "type_hint": "Name(id='list')"},
            ],
            "return_type_hint": None,
            "defaults": [],
            "is_async": False,
            "line": 1,
        }
        code = _generate_characterization_test("mod", "process", sig)
        assert "self" not in code.split("# Arrange")[1]


# ── MCP tool integration ────────────────────────────────────────────


class TestToolRegistration:
    def test_all_tools_registered(self):
        from mcp_server import (
            test_characterize,
            test_characterize_mark,
            test_infer_inputs,
            test_triage,
        )

        assert callable(test_triage)
        assert callable(test_infer_inputs)
        assert callable(test_characterize)
        assert callable(test_characterize_mark)


class TestTestTriage:
    def test_returns_ranked_functions(self, tmp_path):
        # Create a mini project with source and test files
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "__init__.py").touch()
        (src_dir / "core.py").write_text(
            textwrap.dedent("""\
            def compute(data, mode):
                if mode == "fast":
                    return sum(data)
                return sorted(data)

            def helper():
                return 42
            """)
        )
        test_dir = tmp_path / "tests"
        test_dir.mkdir()
        (test_dir / "__init__.py").touch()
        (test_dir / "test_core.py").write_text(
            "def test_helper():\n    from src.core import helper\n    assert helper() == 42\n"
        )

        class FakeMCP:
            def tool(self):
                def dec(fn):
                    self._fn = fn
                    return fn

                return dec

        fake = FakeMCP()
        from mcp_tools.cold_start_tools import register

        tools = register(fake, {"_validate_project_root": lambda p: p})
        result = json.loads(tools["test_triage"](path=str(tmp_path)))
        assert result["total_untested"] >= 1
        assert len(result["functions"]) >= 1
        # compute should be in results (it's untested and has branches)
        names = {f["function"] for f in result["functions"]}
        assert "compute" in names


class TestTestCharacterize:
    def test_generates_test_code(self, tmp_path):
        src = tmp_path / "mymod.py"
        src.write_text("def add(x: int, y: int) -> int:\n    return x + y\n")

        class FakeMCP:
            def tool(self):
                def dec(fn):
                    self._fn = fn
                    return fn

                return dec

        fake = FakeMCP()
        from mcp_tools.cold_start_tools import register

        tools = register(fake, {"_validate_project_root": lambda p: p})
        result = json.loads(
            tools["test_characterize"](
                path=str(tmp_path), file="mymod.py", function="add", write=False
            )
        )
        assert result["maturity"] == "unchecked"
        assert "def test_add_characterization" in result["test_code"]
        assert result["written"] is False

    def test_writes_test_file(self, tmp_path):
        src = tmp_path / "mymod.py"
        src.write_text("def foo(): return 1\n")

        class FakeMCP:
            def tool(self):
                def dec(fn):
                    self._fn = fn
                    return fn

                return dec

        fake = FakeMCP()
        from mcp_tools.cold_start_tools import register

        tools = register(fake, {"_validate_project_root": lambda p: p})
        result = json.loads(
            tools["test_characterize"](
                path=str(tmp_path), file="mymod.py", function="foo", write=True
            )
        )
        assert result["written"] is True
        test_path = tmp_path / result["test_path"]
        assert test_path.exists()
        content = test_path.read_text()
        assert "Maturity: unchecked" in content


class TestTestCharacterizeMark:
    def test_updates_maturity(self, tmp_path):
        test_file = tmp_path / "tests" / "generated" / "test_char_foo.py"
        test_file.parent.mkdir(parents=True)
        test_file.write_text('"""Characterization test. Maturity: unchecked."""\n')

        class FakeMCP:
            def tool(self):
                def dec(fn):
                    self._fn = fn
                    return fn

                return dec

        fake = FakeMCP()
        from mcp_tools.cold_start_tools import register

        tools = register(fake, {"_validate_project_root": lambda p: p})
        result = json.loads(
            tools["test_characterize_mark"](
                path=str(tmp_path),
                test_file=str(test_file.relative_to(tmp_path)),
                maturity="approved",
            )
        )
        assert result["status"] == "updated"
        assert "Maturity: approved" in test_file.read_text()

    def test_rejects_invalid_maturity(self, tmp_path):
        class FakeMCP:
            def tool(self):
                def dec(fn):
                    self._fn = fn
                    return fn

                return dec

        fake = FakeMCP()
        from mcp_tools.cold_start_tools import register

        tools = register(fake, {"_validate_project_root": lambda p: p})
        result = json.loads(
            tools["test_characterize_mark"](
                path=str(tmp_path), test_file="x.py", maturity="invalid"
            )
        )
        assert "error" in result
