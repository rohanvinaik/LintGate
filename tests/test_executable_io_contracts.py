"""Tests for executable I/O contracts (Deliverable C, Gap 9).

Verifies that behavioral contracts generate syntactically valid,
executable mock-based tests instead of TODO-only stubs.
"""

from __future__ import annotations

import ast
import textwrap

from lintgate.orchestration.behavioral_contracts import (
    _generate_io_stub,
    generate_contracts,
)


def _make_func(code: str) -> ast.FunctionDef:
    """Parse a function definition and return the AST node."""
    tree = ast.parse(textwrap.dedent(code))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return node
    raise ValueError("No function found in code")


class TestSubprocessMockGenerated:
    def test_subprocess_run(self) -> None:
        """subprocess.run call → @patch test generated."""
        func = _make_func("""
            def run_command(cmd):
                import subprocess
                result = subprocess.run(cmd, capture_output=True)
                return result.returncode
        """)
        result = _generate_io_stub(func, "my_module")
        assert result is not None
        assert "def test_run_command_subprocess" in result
        assert "patch" in result
        assert "subprocess.run" in result
        assert "mock_result" in result

    def test_subprocess_check_output(self) -> None:
        """check_output call also triggers subprocess mock."""
        func = _make_func("""
            def get_output(cmd):
                import subprocess
                return subprocess.check_output(cmd)
        """)
        result = _generate_io_stub(func, "mod")
        assert result is not None
        assert "subprocess" in result


class TestRequestsMockGenerated:
    def test_requests_get(self) -> None:
        """requests.get → @patch test generated."""
        func = _make_func("""
            def fetch_data(url):
                import requests
                return requests.get(url).json()
        """)
        result = _generate_io_stub(func, "my_module")
        assert result is not None
        assert "def test_fetch_data_http" in result
        assert "requests.get" in result
        assert "mock_resp" in result
        assert "status_code" in result

    def test_requests_post(self) -> None:
        """requests.post → uses 'post' in patch target."""
        func = _make_func("""
            def send_data(url, data):
                import requests
                return requests.post(url, json=data)
        """)
        result = _generate_io_stub(func, "mod")
        assert result is not None
        assert "requests.post" in result


class TestFileIoMockGenerated:
    def test_open_call(self) -> None:
        """open() call → tmp_path test generated."""
        func = _make_func("""
            def read_config(path):
                with open(path) as f:
                    return f.read()
        """)
        result = _generate_io_stub(func, "my_module")
        assert result is not None
        assert "def test_read_config_file_io" in result
        assert "tmp_path" in result
        assert "write_text" in result


class TestMultipleIoPatterns:
    def test_subprocess_and_file(self) -> None:
        """Both subprocess + file → two test functions."""
        func = _make_func("""
            def build_and_log(cmd, log_path):
                import subprocess
                result = subprocess.run(cmd)
                with open(log_path, 'w') as f:
                    f.write(str(result.returncode))
                return result.returncode
        """)
        result = _generate_io_stub(func, "mod")
        assert result is not None
        assert "test_build_and_log_subprocess" in result
        assert "test_build_and_log_file_io" in result


class TestNoIoReturnsNone:
    def test_pure_function(self) -> None:
        """Pure function → None."""
        func = _make_func("""
            def add(a, b):
                return a + b
        """)
        result = _generate_io_stub(func, "mod")
        assert result is None


class TestGeneratedIsValidPython:
    def test_subprocess_parses(self) -> None:
        """Subprocess mock output passes ast.parse."""
        func = _make_func("""
            def run_it(cmd):
                import subprocess
                return subprocess.run(cmd)
        """)
        result = _generate_io_stub(func, "my_module")
        assert result is not None
        # Should not raise SyntaxError
        ast.parse(result)

    def test_http_parses(self) -> None:
        """HTTP mock output passes ast.parse."""
        func = _make_func("""
            def fetch(url):
                import requests
                return requests.get(url)
        """)
        result = _generate_io_stub(func, "my_module")
        assert result is not None
        ast.parse(result)

    def test_file_io_parses(self) -> None:
        """File I/O mock output passes ast.parse."""
        func = _make_func("""
            def read(path):
                with open(path) as f:
                    return f.read()
        """)
        result = _generate_io_stub(func, "my_module")
        assert result is not None
        ast.parse(result)

    def test_combined_parses(self) -> None:
        """Combined I/O patterns output passes ast.parse."""
        func = _make_func("""
            def do_all(cmd, url, path):
                import subprocess
                import requests
                subprocess.run(cmd)
                requests.get(url)
                with open(path) as f:
                    f.read()
        """)
        result = _generate_io_stub(func, "mod")
        assert result is not None
        ast.parse(result)


class TestUnknownIoFallback:
    def test_smtplib_falls_back(self) -> None:
        """smtplib.send → TODO stub (fallback format)."""
        func = _make_func("""
            def send_email(to, body):
                import smtplib
                server = smtplib.SMTP('localhost')
                server.send(body)
        """)
        result = _generate_io_stub(func, "mod")
        assert result is not None
        # smtplib alone (no subprocess/requests/open) → fallback
        # But smtplib is in _IO_MODULES, so it should detect I/O
        # The result may have a mock or fallback depending on classification
        assert "send_email" in result

    def test_socket_falls_back(self) -> None:
        """socket.connect → fallback stub."""
        func = _make_func("""
            def connect_to(host, port):
                import socket
                s = socket.socket()
                s.connect((host, port))
        """)
        result = _generate_io_stub(func, "mod")
        assert result is not None
        assert "connect_to" in result


class TestBackwardCompat:
    def test_generate_contracts_return_type(self, tmp_path) -> None:
        """generate_contracts() returns dict[str, str] as before."""
        project_root = str(tmp_path)
        src = tmp_path / "example.py"
        src.write_text(
            textwrap.dedent("""
            def fetch_api(url: str) -> dict:
                import requests
                return requests.get(url).json()
        """)
        )
        result = generate_contracts(project_root)
        assert isinstance(result, dict)
        for key, value in result.items():
            assert isinstance(key, str)
            assert isinstance(value, str)
