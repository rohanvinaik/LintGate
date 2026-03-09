"""Phase 3A: Test archetype selector tests.

Verifies that the archetype selector correctly identifies
test patterns from source file AST analysis.
"""

from __future__ import annotations

import textwrap
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

from lintgate.controlplane.test_archetype_selector import (
    extract_signals,
    select_archetypes,
)

# ── Fixture helpers ─────────────────────────────────────────────────────


def _write_source(tmp_path: Path, content: str, filename: str = "module.py") -> str:
    """Write source content to a temp file and return its path."""
    filepath = tmp_path / filename
    filepath.write_text(textwrap.dedent(content))
    return str(filepath)


# ── Signal extraction tests ─────────────────────────────────────────────


def test_extract_signals_from_typed_function(tmp_path: Path) -> None:
    source = _write_source(
        tmp_path,
        """\
        def process(data: str, count: int = 0) -> list[str]:
            if not data:
                raise ValueError("empty")
            return [data] * count
    """,
    )
    signals = extract_signals(source)
    assert signals.has_typed_functions
    assert signals.has_guard_clauses
    assert signals.has_value_errors
    assert len(signals.functions) >= 1


def test_extract_signals_from_try_except(tmp_path: Path) -> None:
    source = _write_source(
        tmp_path,
        """\
        import json

        def parse_config(path: str) -> dict:
            try:
                with open(path) as f:
                    return json.load(f)
            except (OSError, json.JSONDecodeError) as e:
                raise ValueError(f"Bad config: {e}")
    """,
    )
    signals = extract_signals(source)
    assert signals.has_try_except
    assert signals.has_try_except is True
    assert signals.has_file_io
    assert signals.has_file_io is True
    assert signals.has_json_dump_load
    assert signals.has_json_dump_load is True
    # Exact-value: one function (parse_config) with typed annotations
    assert len(signals.functions) == 1
    assert signals.functions[0].name == "parse_config"
    assert "json" in signals.imports
    assert signals.has_typed_functions is True


def test_extract_signals_from_http_imports(tmp_path: Path) -> None:
    source = _write_source(
        tmp_path,
        """\
        import requests

        def fetch_data(url: str) -> dict:
            response = requests.get(url)
            return response.json()
    """,
    )
    signals = extract_signals(source)
    assert signals.has_http_imports
    assert signals.has_http_imports is True
    # Exact-value: requests is in the import set, one function extracted
    assert "requests" in signals.imports
    assert "requests" in signals.import_modules
    assert len(signals.functions) == 1
    assert signals.functions[0].name == "fetch_data"


def test_extract_signals_from_subprocess(tmp_path: Path) -> None:
    source = _write_source(
        tmp_path,
        """\
        import subprocess

        def run_command(cmd: str) -> str:
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
            return result.stdout
    """,
    )
    signals = extract_signals(source)
    assert signals.has_subprocess
    assert signals.has_subprocess is True
    # Exact-value: subprocess is in imports, one function extracted
    assert "subprocess" in signals.imports
    assert "subprocess" in signals.import_modules
    assert len(signals.functions) == 1
    assert signals.functions[0].name == "run_command"


def test_extract_signals_from_dataclass(tmp_path: Path) -> None:
    source = _write_source(
        tmp_path,
        """\
        from dataclasses import dataclass, field

        @dataclass
        class Config:
            name: str = "default"
            enabled: bool = True
            items: list[str] = field(default_factory=list)
    """,
    )
    signals = extract_signals(source)
    assert signals.has_dataclasses
    assert len(signals.classes) >= 1
    assert signals.classes[0].is_dataclass


def test_extract_signals_from_stateful_class(tmp_path: Path) -> None:
    source = _write_source(
        tmp_path,
        """\
        class Counter:
            def __init__(self):
                self.count = 0
                self.history = []

            def increment(self):
                self.count += 1
                self.history.append(self.count)

            def reset(self):
                self.count = 0
    """,
    )
    signals = extract_signals(source)
    assert signals.has_stateful_classes
    assert len(signals.classes) >= 1
    assert "count" in signals.classes[0].mutable_fields


def test_extract_signals_from_encode_decode(tmp_path: Path) -> None:
    source = _write_source(
        tmp_path,
        """\
        import json

        def encode_message(data: dict) -> str:
            return json.dumps(data)

        def decode_message(raw: str) -> dict:
            return json.loads(raw)
    """,
    )
    signals = extract_signals(source)
    assert signals.has_encode_decode
    assert signals.has_encode_decode is True
    assert signals.has_json_dump_load
    assert signals.has_json_dump_load is True
    # Exact-value: two functions with encode/decode in names
    assert len(signals.functions) == 2
    func_names = {f.name for f in signals.functions}
    assert func_names == {"encode_message", "decode_message"}
    assert "json" in signals.imports


def test_extract_signals_from_to_from_pairs(tmp_path: Path) -> None:
    source = _write_source(
        tmp_path,
        """\
        def to_dict(obj) -> dict:
            return {"name": obj.name, "value": obj.value}

        def from_dict(data: dict):
            return type("Obj", (), data)()
    """,
    )
    signals = extract_signals(source)
    assert signals.has_to_from_pairs
    assert signals.has_to_from_pairs is True
    # Exact-value: two functions forming to_/from_ pair
    assert len(signals.functions) == 2
    func_names = {f.name for f in signals.functions}
    assert func_names == {"to_dict", "from_dict"}


def test_extract_signals_from_yaml_import(tmp_path: Path) -> None:
    source = _write_source(
        tmp_path,
        """\
        import yaml

        def load_config(path: str) -> dict:
            with open(path) as f:
                return yaml.safe_load(f)
    """,
    )
    signals = extract_signals(source)
    assert signals.has_yaml_toml_json
    assert signals.has_yaml_toml_json is True
    assert signals.has_file_io
    assert signals.has_file_io is True
    # Exact-value: yaml is in imports, one function extracted
    assert "yaml" in signals.imports
    assert "yaml" in signals.import_modules
    assert len(signals.functions) == 1
    assert signals.functions[0].name == "load_config"


# ── Archetype selection tests ────────────────────────────────────────────


def test_http_file_selects_error_handling_and_mock_isolation(tmp_path: Path) -> None:
    """File with HTTP calls → error_handling + mock_isolation."""
    source = _write_source(
        tmp_path,
        """\
        import requests

        def fetch_users(base_url: str) -> list[dict]:
            try:
                response = requests.get(f"{base_url}/users")
                response.raise_for_status()
                return response.json()
            except requests.RequestException as e:
                raise ConnectionError(f"Failed to fetch: {e}")
    """,
    )
    matches = select_archetypes(source)
    archetype_names = {m.name for m in matches}

    assert "error_handling" in archetype_names
    assert "mock_isolation" in archetype_names


def test_dataclass_file_selects_configuration(tmp_path: Path) -> None:
    """File with dataclasses → configuration."""
    source = _write_source(
        tmp_path,
        """\
        from dataclasses import dataclass, field
        from typing import Any

        @dataclass
        class ServerConfig:
            host: str = "localhost"
            port: int = 8080
            debug: bool = False
            extras: dict[str, Any] = field(default_factory=dict)
    """,
    )
    matches = select_archetypes(source)
    archetype_names = {m.name for m in matches}

    assert "configuration" in archetype_names


def test_encode_decode_file_selects_round_trip(tmp_path: Path) -> None:
    """File with encode/decode functions → round_trip."""
    source = _write_source(
        tmp_path,
        """\
        import json
        import base64

        def encode_payload(data: dict) -> str:
            json_str = json.dumps(data)
            return base64.b64encode(json_str.encode()).decode()

        def decode_payload(encoded: str) -> dict:
            json_str = base64.b64decode(encoded.encode()).decode()
            return json.loads(json_str)
    """,
    )
    matches = select_archetypes(source)
    archetype_names = {m.name for m in matches}

    assert "round_trip" in archetype_names


def test_stateful_class_selects_state_invariant(tmp_path: Path) -> None:
    """File with stateful class → state_invariant."""
    source = _write_source(
        tmp_path,
        """\
        class TaskQueue:
            def __init__(self):
                self.tasks = []
                self.completed = []
                self.failed = []

            def add(self, task):
                self.tasks.append(task)

            def complete(self, task):
                self.tasks.remove(task)
                self.completed.append(task)

            def fail(self, task, error):
                self.tasks.remove(task)
                self.failed.append((task, error))
    """,
    )
    matches = select_archetypes(source)
    archetype_names = {m.name for m in matches}

    assert "state_invariant" in archetype_names


def test_no_clear_signals_returns_input_validation(tmp_path: Path) -> None:
    """File with no clear signals → default input_validation at low confidence."""
    source = _write_source(
        tmp_path,
        """\
        x = 1
        y = 2
        z = x + y
    """,
    )
    matches = select_archetypes(source)

    assert len(matches) >= 1
    # Input validation should be present as fallback
    iv = [m for m in matches if m.name == "input_validation"]
    assert len(iv) == 1
    assert iv[0].confidence <= 0.4  # Low confidence


def test_subprocess_file_selects_mock_isolation(tmp_path: Path) -> None:
    """File with subprocess calls → mock_isolation."""
    source = _write_source(
        tmp_path,
        """\
        import subprocess

        def run_linter(files: list[str]) -> str:
            result = subprocess.run(
                ["ruff", "check", *files],
                capture_output=True, text=True,
            )
            if result.returncode != 0:
                raise RuntimeError(f"Lint failed: {result.stderr}")
            return result.stdout
    """,
    )
    matches = select_archetypes(source)
    archetype_names = {m.name for m in matches}

    assert "mock_isolation" in archetype_names
    assert "error_handling" in archetype_names


def test_guard_clause_file_selects_input_validation(tmp_path: Path) -> None:
    """File with guard clauses → high-confidence input_validation."""
    source = _write_source(
        tmp_path,
        """\
        def validate_email(email: str) -> str:
            if not email:
                raise ValueError("Email cannot be empty")
            if "@" not in email:
                raise ValueError("Invalid email format")
            return email.strip().lower()
    """,
    )
    matches = select_archetypes(source)
    iv = [m for m in matches if m.name == "input_validation"]
    assert len(iv) == 1
    assert iv[0].confidence >= 0.7


# ── Result ordering tests ───────────────────────────────────────────────


def test_results_sorted_by_confidence(tmp_path: Path) -> None:
    """Verify that results are sorted by confidence descending."""
    source = _write_source(
        tmp_path,
        """\
        import requests
        from dataclasses import dataclass

        @dataclass
        class ApiConfig:
            base_url: str = "https://api.example.com"

        def fetch(config: ApiConfig) -> dict:
            try:
                resp = requests.get(config.base_url)
                return resp.json()
            except Exception as e:
                raise ConnectionError(str(e))
    """,
    )
    matches = select_archetypes(source)

    for i in range(len(matches) - 1):
        assert matches[i].confidence >= matches[i + 1].confidence, (
            f"Results not sorted: {matches[i].name}({matches[i].confidence}) "
            f"before {matches[i + 1].name}({matches[i + 1].confidence})"
        )


def test_all_results_above_threshold(tmp_path: Path) -> None:
    """Verify all returned results have confidence > 0.3."""
    source = _write_source(
        tmp_path,
        """\
        import subprocess
        import json

        def run_and_parse(cmd: str) -> dict:
            try:
                result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
                return json.loads(result.stdout)
            except (subprocess.SubprocessError, json.JSONDecodeError) as e:
                raise RuntimeError(str(e))
    """,
    )
    matches = select_archetypes(source)

    for m in matches:
        assert m.confidence > 0.3, f"{m.name} has confidence {m.confidence} <= 0.3"


# ── Edge cases ───────────────────────────────────────────────────────────


def test_nonexistent_file_returns_default() -> None:
    """Non-existent file should return default archetype."""
    matches = select_archetypes("/nonexistent/file.py")
    assert len(matches) >= 1
    assert matches[0].name == "input_validation"
    assert matches[0].confidence == 0.3


def test_syntax_error_file_returns_default(tmp_path: Path) -> None:
    """File with syntax errors should return default archetype."""
    source = _write_source(tmp_path, "def broken(:\n    pass")
    matches = select_archetypes(source)
    assert len(matches) >= 1
    assert matches[0].name == "input_validation"


def test_empty_file_returns_low_confidence(tmp_path: Path) -> None:
    """Empty file should return very low confidence matches."""
    source = _write_source(tmp_path, "")
    matches = select_archetypes(source)
    # Should still have input_validation at low confidence
    assert len(matches) >= 1
    for m in matches:
        assert m.confidence <= 0.4


def test_archetype_match_has_relevant_functions(tmp_path: Path) -> None:
    """Verify that matches include relevant function names."""
    source = _write_source(
        tmp_path,
        """\
        def validate_input(data: str) -> str:
            if not data:
                raise ValueError("empty")
            return data

        def helper():
            pass
    """,
    )
    matches = select_archetypes(source)
    iv = [m for m in matches if m.name == "input_validation"]
    assert len(iv) == 1
    assert "validate_input" in iv[0].relevant_functions


def test_archetype_match_has_relevant_classes(tmp_path: Path) -> None:
    """Verify that matches include relevant class names."""
    source = _write_source(
        tmp_path,
        """\
        from dataclasses import dataclass

        @dataclass
        class Settings:
            name: str = "default"
            value: int = 0
    """,
    )
    matches = select_archetypes(source)
    cfg = [m for m in matches if m.name == "configuration"]
    assert len(cfg) == 1
    assert "Settings" in cfg[0].relevant_classes
