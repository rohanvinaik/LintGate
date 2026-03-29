"""Comprehensive tests for test_archetype_selector module.

Tests cover:
- Data classes: FunctionInfo, ClassInfo, SourceSignals, ArchetypeMatch
- Signal extraction: all visitor methods and composite signal derivation
- Archetype matching: all 7 archetypes with confidence scoring
- Public API: select_archetypes and extract_signals
- Edge cases: empty inputs, missing keys, boundary values, error paths
"""

from __future__ import annotations

import textwrap
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

from lintgate.controlplane.test_archetype_selector import (
    ArchetypeMatch,
    ClassInfo,
    FunctionInfo,
    SourceSignals,
    extract_signals,
    select_archetypes,
)


def _load_tool_result(json_str):
    import json as _j
    import os as _os
    r = _j.loads(json_str)
    if isinstance(r, dict) and "file" in r and "analysis_id" in r and _os.path.isfile(r.get("file","")):
        with open(r["file"]) as f:

            return _j.loads(f.read())
    return r


# ── Fixture helpers ─────────────────────────────────────────────────────


def _write_source(tmp_path: Path, content: str, filename: str = "module.py") -> str:
    """Write source content to a temp file and return its path."""
    filepath = tmp_path / filename
    filepath.write_text(textwrap.dedent(content))
    return str(filepath)


# ── Data class construction tests ───────────────────────────────────────


class TestFunctionInfo:
    """Tests for the FunctionInfo dataclass."""

    def test_default_values(self) -> None:
        fi = FunctionInfo(name="foo")
        assert fi.name == "foo"
        assert fi.args == []
        assert fi.has_type_annotations is False
        assert fi.has_return_type is False
        assert fi.has_docstring is False
        assert fi.raises == []
        assert fi.decorators == []
        assert fi.is_method is False
        assert fi.class_name is None

    def test_method_with_class_name(self) -> None:
        fi = FunctionInfo(name="do_work", is_method=True, class_name="Worker")
        assert fi.is_method is True
        assert fi.class_name == "Worker"

    def test_all_fields_populated(self) -> None:
        fi = FunctionInfo(
            name="process",
            args=["data", "count"],
            has_type_annotations=True,
            has_return_type=True,
            has_docstring=True,
            raises=["ValueError", "TypeError"],
            decorators=["staticmethod"],
            is_method=True,
            class_name="Processor",
        )
        assert fi.args == ["data", "count"]
        assert fi.has_type_annotations is True
        assert fi.has_return_type is True
        assert fi.has_docstring is True
        assert fi.raises == ["ValueError", "TypeError"]
        assert fi.decorators == ["staticmethod"]


class TestClassInfo:
    """Tests for the ClassInfo dataclass."""

    def test_default_values(self) -> None:
        ci = ClassInfo(name="Foo")
        assert ci.name == "Foo"
        assert ci.bases == []
        assert ci.has_init is False
        assert ci.init_defaults == 0
        assert ci.methods == []
        assert ci.is_dataclass is False
        assert ci.mutable_fields == []

    def test_all_fields_populated(self) -> None:
        ci = ClassInfo(
            name="Bar",
            bases=["Base", "Mixin"],
            has_init=True,
            init_defaults=3,
            methods=["__init__", "run", "stop"],
            is_dataclass=True,
            mutable_fields=["state", "count"],
        )
        assert ci.bases == ["Base", "Mixin"]
        assert ci.init_defaults == 3
        assert ci.methods == ["__init__", "run", "stop"]
        assert ci.is_dataclass is True
        assert ci.mutable_fields == ["state", "count"]


class TestSourceSignals:
    """Tests for the SourceSignals dataclass."""

    def test_default_values(self) -> None:
        ss = SourceSignals()
        assert ss.functions == []
        assert ss.classes == []
        assert ss.imports == set()
        assert ss.import_modules == set()
        assert ss.has_try_except is False
        assert ss.has_file_io is False
        assert ss.has_subprocess is False
        assert ss.has_http_imports is False
        assert ss.has_database_ops is False
        assert ss.has_dataclasses is False
        assert ss.has_yaml_toml_json is False
        assert ss.has_encode_decode is False
        assert ss.has_to_from_pairs is False
        assert ss.has_guard_clauses is False
        assert ss.has_typed_functions is False
        assert ss.has_value_errors is False
        assert ss.has_stateful_classes is False
        assert ss.has_complex_conditionals is False
        assert ss.has_mutable_collections is False
        assert ss.has_serialize_patterns is False
        assert ss.has_json_dump_load is False


class TestArchetypeMatch:
    """Tests for the ArchetypeMatch dataclass."""

    def test_required_fields(self) -> None:
        am = ArchetypeMatch(name="round_trip", confidence=0.9, reason="test")
        assert am.name == "round_trip"
        assert am.confidence == 0.9
        assert am.reason == "test"
        assert am.relevant_functions == []
        assert am.relevant_classes == []

    def test_with_relevant_items(self) -> None:
        am = ArchetypeMatch(
            name="state_invariant",
            confidence=0.8,
            reason="Has mutable state",
            relevant_functions=["add", "remove"],
            relevant_classes=["Queue"],
        )
        assert am.relevant_functions == ["add", "remove"]
        assert am.relevant_classes == ["Queue"]


# ── Signal extraction tests ─────────────────────────────────────────────


class TestExtractSignalsImports:
    """Tests for import signal extraction."""

    def test_plain_import(self, tmp_path: Path) -> None:
        source = _write_source(tmp_path, "import os\nimport sys\n")
        signals = extract_signals(source)
        assert "os" in signals.imports
        assert "sys" in signals.imports
        assert "os" in signals.import_modules
        assert "sys" in signals.import_modules

    def test_dotted_import(self, tmp_path: Path) -> None:
        source = _write_source(tmp_path, "import os.path\n")
        signals = extract_signals(source)
        assert "os.path" in signals.imports
        assert "os" in signals.import_modules

    def test_from_import(self, tmp_path: Path) -> None:
        source = _write_source(tmp_path, "from os.path import join, exists\n")
        signals = extract_signals(source)
        assert "os.path" in signals.imports
        assert "os.path.join" in signals.imports
        assert "os.path.exists" in signals.imports
        assert "os" in signals.import_modules

    def test_from_import_no_names(self, tmp_path: Path) -> None:
        """ImportFrom with empty names list should not crash."""
        source = _write_source(tmp_path, "from os import *\n")
        signals = extract_signals(source)
        assert "os" in signals.imports

    def test_http_import_detection_requests(self, tmp_path: Path) -> None:
        source = _write_source(tmp_path, "import requests\n")
        signals = extract_signals(source)
        assert signals.has_http_imports is True

    def test_http_import_detection_httpx(self, tmp_path: Path) -> None:
        source = _write_source(tmp_path, "import httpx\n")
        signals = extract_signals(source)
        assert signals.has_http_imports is True

    def test_http_import_detection_aiohttp(self, tmp_path: Path) -> None:
        source = _write_source(tmp_path, "import aiohttp\n")
        signals = extract_signals(source)
        assert signals.has_http_imports is True

    def test_http_import_detection_urllib(self, tmp_path: Path) -> None:
        source = _write_source(tmp_path, "import urllib\n")
        signals = extract_signals(source)
        assert signals.has_http_imports is True

    def test_database_import_sqlite3(self, tmp_path: Path) -> None:
        source = _write_source(tmp_path, "import sqlite3\n")
        signals = extract_signals(source)
        assert signals.has_database_ops is True

    def test_database_import_sqlalchemy(self, tmp_path: Path) -> None:
        source = _write_source(tmp_path, "import sqlalchemy\n")
        signals = extract_signals(source)
        assert signals.has_database_ops is True

    def test_database_import_pymongo(self, tmp_path: Path) -> None:
        source = _write_source(tmp_path, "import pymongo\n")
        signals = extract_signals(source)
        assert signals.has_database_ops is True

    def test_database_import_redis(self, tmp_path: Path) -> None:
        source = _write_source(tmp_path, "import redis\n")
        signals = extract_signals(source)
        assert signals.has_database_ops is True

    def test_config_import_yaml(self, tmp_path: Path) -> None:
        source = _write_source(tmp_path, "import yaml\n")
        signals = extract_signals(source)
        assert signals.has_yaml_toml_json is True

    def test_config_import_toml(self, tmp_path: Path) -> None:
        source = _write_source(tmp_path, "import toml\n")
        signals = extract_signals(source)
        assert signals.has_yaml_toml_json is True

    def test_config_import_tomllib(self, tmp_path: Path) -> None:
        source = _write_source(tmp_path, "import tomllib\n")
        signals = extract_signals(source)
        assert signals.has_yaml_toml_json is True

    def test_config_import_configparser(self, tmp_path: Path) -> None:
        source = _write_source(tmp_path, "import configparser\n")
        signals = extract_signals(source)
        assert signals.has_yaml_toml_json is True

    def test_subprocess_import_sets_signal(self, tmp_path: Path) -> None:
        source = _write_source(tmp_path, "import subprocess\n")
        signals = extract_signals(source)
        assert signals.has_subprocess is True

    def test_no_matching_imports(self, tmp_path: Path) -> None:
        source = _write_source(tmp_path, "import math\nimport collections\n")
        signals = extract_signals(source)
        assert signals.has_http_imports is False
        assert signals.has_database_ops is False
        assert signals.has_yaml_toml_json is False
        assert signals.has_subprocess is False


class TestExtractSignalsFunctions:
    """Tests for function signal extraction."""

    def test_basic_function_extraction(self, tmp_path: Path) -> None:
        source = _write_source(
            tmp_path,
            """\
            def hello():
                pass
        """,
        )
        signals = extract_signals(source)
        assert len(signals.functions) == 1
        assert signals.functions[0].name == "hello"
        assert signals.functions[0].is_method is False
        assert signals.functions[0].class_name is None

    def test_function_args_excluding_self(self, tmp_path: Path) -> None:
        source = _write_source(
            tmp_path,
            """\
            def process(data, count, flag=True):
                pass
        """,
        )
        signals = extract_signals(source)
        assert signals.functions[0].args == ["data", "count", "flag"]

    def test_type_annotations_detected(self, tmp_path: Path) -> None:
        source = _write_source(
            tmp_path,
            """\
            def process(data: str) -> int:
                return len(data)
        """,
        )
        signals = extract_signals(source)
        func = signals.functions[0]
        assert func.has_type_annotations is True
        assert func.has_return_type is True
        assert signals.has_typed_functions is True

    def test_return_type_only(self, tmp_path: Path) -> None:
        source = _write_source(
            tmp_path,
            """\
            def process(data) -> int:
                return len(data)
        """,
        )
        signals = extract_signals(source)
        func = signals.functions[0]
        assert func.has_return_type is True
        assert func.has_type_annotations is True

    def test_docstring_detected(self, tmp_path: Path) -> None:
        source = _write_source(
            tmp_path,
            """\
            def process():
                \"\"\"This function processes stuff.\"\"\"
                pass
        """,
        )
        signals = extract_signals(source)
        assert signals.functions[0].has_docstring is True

    def test_no_docstring(self, tmp_path: Path) -> None:
        source = _write_source(
            tmp_path,
            """\
            def process():
                x = 1
                return x
        """,
        )
        signals = extract_signals(source)
        assert signals.functions[0].has_docstring is False

    def test_raises_extraction(self, tmp_path: Path) -> None:
        source = _write_source(
            tmp_path,
            """\
            def validate(x):
                if x < 0:
                    raise ValueError("negative")
                if x is None:
                    raise TypeError("none")
        """,
        )
        signals = extract_signals(source)
        func = signals.functions[0]
        assert "ValueError" in func.raises
        assert "TypeError" in func.raises
        assert signals.has_value_errors is True

    def test_raises_bare_name(self, tmp_path: Path) -> None:
        """Raise with a Name node (no Call), e.g., raise err."""
        source = _write_source(
            tmp_path,
            """\
            def reraise(err):
                raise err
        """,
        )
        signals = extract_signals(source)
        func = signals.functions[0]
        assert "err" in func.raises

    def test_decorator_extraction(self, tmp_path: Path) -> None:
        source = _write_source(
            tmp_path,
            """\
            def my_decorator(f):
                return f

            @my_decorator
            def process():
                pass
        """,
        )
        signals = extract_signals(source)
        decorated = [f for f in signals.functions if f.name == "process"]
        assert len(decorated) == 1
        assert "my_decorator" in decorated[0].decorators

    def test_dotted_decorator(self, tmp_path: Path) -> None:
        source = _write_source(
            tmp_path,
            """\
            import functools

            @functools.lru_cache
            def expensive():
                return 42
        """,
        )
        signals = extract_signals(source)
        func = [f for f in signals.functions if f.name == "expensive"][0]
        assert "functools.lru_cache" in func.decorators

    def test_call_decorator(self, tmp_path: Path) -> None:
        """Decorator with call syntax, e.g., @decorator(args)."""
        source = _write_source(
            tmp_path,
            """\
            import functools

            @functools.lru_cache(maxsize=128)
            def expensive():
                return 42
        """,
        )
        signals = extract_signals(source)
        func = [f for f in signals.functions if f.name == "expensive"][0]
        assert "functools.lru_cache" in func.decorators

    def test_async_function_extraction(self, tmp_path: Path) -> None:
        source = _write_source(
            tmp_path,
            """\
            async def fetch_data(url: str) -> dict:
                \"\"\"Fetch data from url.\"\"\"
                pass
        """,
        )
        signals = extract_signals(source)
        assert len(signals.functions) == 1
        func = signals.functions[0]
        assert func.name == "fetch_data"
        assert func.has_type_annotations is True
        assert func.has_return_type is True
        assert func.has_docstring is True

    def test_method_extraction_within_class(self, tmp_path: Path) -> None:
        source = _write_source(
            tmp_path,
            """\
            class MyClass:
                def do_work(self, x: int) -> None:
                    pass
        """,
        )
        signals = extract_signals(source)
        methods = [f for f in signals.functions if f.is_method]
        assert len(methods) == 1
        assert methods[0].name == "do_work"
        assert methods[0].class_name == "MyClass"
        assert methods[0].args == ["x"]  # self is excluded

    def test_multiple_functions(self, tmp_path: Path) -> None:
        source = _write_source(
            tmp_path,
            """\
            def foo():
                pass

            def bar():
                pass

            def baz():
                pass
        """,
        )
        signals = extract_signals(source)
        names = {f.name for f in signals.functions}
        assert names == {"foo", "bar", "baz"}


class TestExtractSignalsClasses:
    """Tests for class signal extraction."""

    def test_basic_class(self, tmp_path: Path) -> None:
        source = _write_source(
            tmp_path,
            """\
            class Foo:
                pass
        """,
        )
        signals = extract_signals(source)
        assert len(signals.classes) == 1
        assert signals.classes[0].name == "Foo"
        assert signals.classes[0].bases == []
        assert signals.classes[0].has_init is False

    def test_class_with_bases(self, tmp_path: Path) -> None:
        source = _write_source(
            tmp_path,
            """\
            class Child(Parent, Mixin):
                pass
        """,
        )
        signals = extract_signals(source)
        assert signals.classes[0].bases == ["Parent", "Mixin"]

    def test_class_with_dotted_base(self, tmp_path: Path) -> None:
        source = _write_source(
            tmp_path,
            """\
            import abc

            class MyABC(abc.ABC):
                pass
        """,
        )
        signals = extract_signals(source)
        assert "abc.ABC" in signals.classes[0].bases

    def test_class_init_detection(self, tmp_path: Path) -> None:
        source = _write_source(
            tmp_path,
            """\
            class Foo:
                def __init__(self, x, y=1, z=2):
                    self.x = x
                    self.y = y
                    self.z = z
        """,
        )
        signals = extract_signals(source)
        ci = signals.classes[0]
        assert ci.has_init is True
        assert ci.init_defaults == 2  # y=1, z=2

    def test_class_methods_list(self, tmp_path: Path) -> None:
        source = _write_source(
            tmp_path,
            """\
            class Foo:
                def __init__(self):
                    pass

                def run(self):
                    pass

                def stop(self):
                    pass
        """,
        )
        signals = extract_signals(source)
        assert "__init__" in signals.classes[0].methods
        assert "run" in signals.classes[0].methods
        assert "stop" in signals.classes[0].methods

    def test_dataclass_detection(self, tmp_path: Path) -> None:
        source = _write_source(
            tmp_path,
            """\
            from dataclasses import dataclass

            @dataclass
            class Config:
                name: str = "default"
        """,
        )
        signals = extract_signals(source)
        assert signals.classes[0].is_dataclass is True
        assert signals.has_dataclasses is True

    def test_dataclass_with_module_prefix(self, tmp_path: Path) -> None:
        source = _write_source(
            tmp_path,
            """\
            import dataclasses

            @dataclasses.dataclass
            class Config:
                name: str = "default"
        """,
        )
        signals = extract_signals(source)
        assert signals.classes[0].is_dataclass is True

    def test_mutable_fields_detection(self, tmp_path: Path) -> None:
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
        """,
        )
        signals = extract_signals(source)
        ci = signals.classes[0]
        assert "count" in ci.mutable_fields
        assert "history" in ci.mutable_fields
        assert signals.has_stateful_classes is True
        assert signals.has_mutable_collections is True

    def test_class_without_mutable_fields(self, tmp_path: Path) -> None:
        source = _write_source(
            tmp_path,
            """\
            class Pure:
                def compute(self, x):
                    return x * 2
        """,
        )
        signals = extract_signals(source)
        assert signals.classes[0].mutable_fields == []
        assert signals.has_stateful_classes is False

    def test_multiple_classes(self, tmp_path: Path) -> None:
        source = _write_source(
            tmp_path,
            """\
            class Alpha:
                pass

            class Beta:
                pass
        """,
        )
        signals = extract_signals(source)
        names = {c.name for c in signals.classes}
        assert names == {"Alpha", "Beta"}

    def test_nested_class_context_restored(self, tmp_path: Path) -> None:
        """After visiting inner class methods, outer class context should be restored."""
        source = _write_source(
            tmp_path,
            """\
            class Outer:
                def outer_method(self):
                    pass

                class Inner:
                    def inner_method(self):
                        pass

                def another_outer_method(self):
                    pass
        """,
        )
        signals = extract_signals(source)
        outer_methods = [f for f in signals.functions if f.class_name == "Outer"]
        inner_methods = [f for f in signals.functions if f.class_name == "Inner"]
        assert len(outer_methods) == 2
        assert len(inner_methods) == 1


class TestExtractSignalsCallDetection:
    """Tests for call-based signal detection (file I/O, subprocess, JSON)."""

    def test_open_call_detects_file_io(self, tmp_path: Path) -> None:
        source = _write_source(
            tmp_path,
            """\
            def read_file(path):
                with open(path) as f:
                    return f.read()
        """,
        )
        signals = extract_signals(source)
        assert signals.has_file_io is True

    def test_path_read_text_detects_file_io(self, tmp_path: Path) -> None:
        source = _write_source(
            tmp_path,
            """\
            from pathlib import Path

            def read_file(p):
                return Path.read_text(p)
        """,
        )
        signals = extract_signals(source)
        assert signals.has_file_io is True

    def test_json_dump_detects_signal(self, tmp_path: Path) -> None:
        source = _write_source(
            tmp_path,
            """\
            import json

            def save(data, f):
                json.dump(data, f)
        """,
        )
        signals = extract_signals(source)
        assert signals.has_json_dump_load is True

    def test_json_loads_detects_signal(self, tmp_path: Path) -> None:
        source = _write_source(
            tmp_path,
            """\
            import json

            def parse(raw):
                return json.loads(raw)
        """,
        )
        signals = extract_signals(source)
        assert signals.has_json_dump_load is True

    def test_subprocess_call_detects_signal(self, tmp_path: Path) -> None:
        source = _write_source(
            tmp_path,
            """\
            import subprocess

            def run(cmd):
                subprocess.run(cmd)
        """,
        )
        signals = extract_signals(source)
        assert signals.has_subprocess is True

    def test_no_call_signals_for_regular_code(self, tmp_path: Path) -> None:
        source = _write_source(
            tmp_path,
            """\
            def add(a, b):
                return a + b
        """,
        )
        signals = extract_signals(source)
        assert signals.has_file_io is False
        assert signals.has_subprocess is False
        assert signals.has_json_dump_load is False


class TestExtractSignalsConditionals:
    """Tests for conditional signal detection (guard clauses, complex conditionals)."""

    def test_guard_clause_not_pattern(self, tmp_path: Path) -> None:
        source = _write_source(
            tmp_path,
            """\
            def validate(x):
                if not x:
                    raise ValueError("empty")
        """,
        )
        signals = extract_signals(source)
        assert signals.has_guard_clauses is True

    def test_guard_clause_compare_pattern(self, tmp_path: Path) -> None:
        source = _write_source(
            tmp_path,
            """\
            def validate(x):
                if x is None:
                    raise ValueError("none")
        """,
        )
        signals = extract_signals(source)
        assert signals.has_guard_clauses is True

    def test_no_guard_clause_for_normal_if(self, tmp_path: Path) -> None:
        source = _write_source(
            tmp_path,
            """\
            def process(x):
                if x > 0:
                    return x * 2
                return 0
        """,
        )
        signals = extract_signals(source)
        assert signals.has_guard_clauses is False

    def test_complex_conditional_three_values(self, tmp_path: Path) -> None:
        source = _write_source(
            tmp_path,
            """\
            def check(a, b, c, d):
                if a and b and c and d:
                    return True
                return False
        """,
        )
        signals = extract_signals(source)
        assert signals.has_complex_conditionals is True

    def test_simple_conditional_not_flagged(self, tmp_path: Path) -> None:
        source = _write_source(
            tmp_path,
            """\
            def check(a, b):
                if a and b:
                    return True
                return False
        """,
        )
        signals = extract_signals(source)
        assert signals.has_complex_conditionals is False


class TestExtractSignalsTryExcept:
    """Tests for try/except signal detection."""

    def test_try_except_detected(self, tmp_path: Path) -> None:
        source = _write_source(
            tmp_path,
            """\
            def safe_parse(raw):
                try:
                    return int(raw)
                except ValueError:
                    return 0
        """,
        )
        signals = extract_signals(source)
        assert signals.has_try_except is True

    def test_no_try_except(self, tmp_path: Path) -> None:
        source = _write_source(
            tmp_path,
            """\
            def add(a, b):
                return a + b
        """,
        )
        signals = extract_signals(source)
        assert signals.has_try_except is False


class TestCompositeSignalDerivation:
    """Tests for _derive_composite_signals logic tested through public API."""

    def test_encode_decode_name_detection(self, tmp_path: Path) -> None:
        source = _write_source(
            tmp_path,
            """\
            def encode_token(data):
                pass
        """,
        )
        signals = extract_signals(source)
        assert signals.has_encode_decode is True

    def test_encrypt_decrypt_name_detection(self, tmp_path: Path) -> None:
        source = _write_source(
            tmp_path,
            """\
            def encrypt_payload(data):
                pass

            def decrypt_payload(data):
                pass
        """,
        )
        signals = extract_signals(source)
        assert signals.has_encode_decode is True

    def test_compress_decompress_name_detection(self, tmp_path: Path) -> None:
        source = _write_source(
            tmp_path,
            """\
            def compress_data(data):
                pass
        """,
        )
        signals = extract_signals(source)
        assert signals.has_encode_decode is True

    def test_no_encode_decode_for_normal_names(self, tmp_path: Path) -> None:
        source = _write_source(
            tmp_path,
            """\
            def process(data):
                pass
        """,
        )
        signals = extract_signals(source)
        assert signals.has_encode_decode is False

    def test_to_from_pairs_both_required(self, tmp_path: Path) -> None:
        """to_ and from_ must BOTH be present."""
        source = _write_source(
            tmp_path,
            """\
            def to_json(obj):
                pass
        """,
        )
        signals = extract_signals(source)
        assert signals.has_to_from_pairs is False

    def test_to_from_pairs_only_from(self, tmp_path: Path) -> None:
        source = _write_source(
            tmp_path,
            """\
            def from_json(data):
                pass
        """,
        )
        signals = extract_signals(source)
        assert signals.has_to_from_pairs is False

    def test_to_from_pairs_both_present(self, tmp_path: Path) -> None:
        source = _write_source(
            tmp_path,
            """\
            def to_json(obj):
                pass

            def from_json(data):
                pass
        """,
        )
        signals = extract_signals(source)
        assert signals.has_to_from_pairs is True

    def test_serialize_pattern_detection(self, tmp_path: Path) -> None:
        source = _write_source(
            tmp_path,
            """\
            def serialize(obj):
                pass
        """,
        )
        signals = extract_signals(source)
        assert signals.has_serialize_patterns is True

    def test_deserialize_pattern_detection(self, tmp_path: Path) -> None:
        source = _write_source(
            tmp_path,
            """\
            def deserialize(data):
                pass
        """,
        )
        signals = extract_signals(source)
        assert signals.has_serialize_patterns is True

    def test_marshal_unmarshal_detection(self, tmp_path: Path) -> None:
        source = _write_source(
            tmp_path,
            """\
            def marshal_data(obj):
                pass
        """,
        )
        signals = extract_signals(source)
        assert signals.has_serialize_patterns is True

    def test_to_dict_from_dict_detection(self, tmp_path: Path) -> None:
        source = _write_source(
            tmp_path,
            """\
            def to_dict(obj):
                pass
        """,
        )
        signals = extract_signals(source)
        assert signals.has_serialize_patterns is True

    def test_dump_load_name_detection(self, tmp_path: Path) -> None:
        source = _write_source(
            tmp_path,
            """\
            def dump_state(obj):
                pass
        """,
        )
        signals = extract_signals(source)
        assert signals.has_serialize_patterns is True

    def test_no_serialize_pattern_for_normal_names(self, tmp_path: Path) -> None:
        source = _write_source(
            tmp_path,
            """\
            def process(data):
                pass
        """,
        )
        signals = extract_signals(source)
        assert signals.has_serialize_patterns is False

    def test_value_error_types_detected(self, tmp_path: Path) -> None:
        source = _write_source(
            tmp_path,
            """\
            def check(x):
                if not x:
                    raise KeyError("missing")
        """,
        )
        signals = extract_signals(source)
        assert signals.has_value_errors is True

    def test_attribute_error_detected(self, tmp_path: Path) -> None:
        source = _write_source(
            tmp_path,
            """\
            def check(x):
                if not hasattr(x, 'name'):
                    raise AttributeError("no name")
        """,
        )
        signals = extract_signals(source)
        assert signals.has_value_errors is True

    def test_non_tracked_error_not_flagged(self, tmp_path: Path) -> None:
        """RuntimeError is not in the tracked error_types set."""
        source = _write_source(
            tmp_path,
            """\
            def check():
                raise RuntimeError("boom")
        """,
        )
        signals = extract_signals(source)
        assert signals.has_value_errors is False


# ── Archetype selection tests ────────────────────────────────────────────


class TestSelectArchetypesInputValidation:
    """Tests for the input_validation archetype."""

    def test_baseline_always_present(self, tmp_path: Path) -> None:
        """Even with minimal code, input_validation should appear."""
        source = _write_source(tmp_path, "x = 1\n")
        matches = select_archetypes(source)
        names = {m.name for m in matches}
        assert "input_validation" in names

    def test_baseline_confidence_is_0_3(self, tmp_path: Path) -> None:
        source = _write_source(tmp_path, "x = 1\n")
        matches = select_archetypes(source)
        iv = [m for m in matches if m.name == "input_validation"][0]
        assert iv.confidence == 0.3

    def test_typed_functions_boost(self, tmp_path: Path) -> None:
        source = _write_source(
            tmp_path,
            """\
            def process(data: str) -> int:
                return len(data)
        """,
        )
        matches = select_archetypes(source)
        iv = [m for m in matches if m.name == "input_validation"][0]
        assert iv.confidence >= 0.6

    def test_guard_clauses_high_confidence(self, tmp_path: Path) -> None:
        source = _write_source(
            tmp_path,
            """\
            def validate(x):
                if not x:
                    raise ValueError("empty")
        """,
        )
        matches = select_archetypes(source)
        iv = [m for m in matches if m.name == "input_validation"][0]
        assert iv.confidence >= 0.8

    def test_value_errors_boost(self, tmp_path: Path) -> None:
        source = _write_source(
            tmp_path,
            """\
            def check(x):
                raise ValueError("bad")
        """,
        )
        matches = select_archetypes(source)
        iv = [m for m in matches if m.name == "input_validation"][0]
        assert iv.confidence >= 0.7

    def test_relevant_functions_populated(self, tmp_path: Path) -> None:
        source = _write_source(
            tmp_path,
            """\
            def typed_func(x: int) -> str:
                return str(x)

            def untyped_func(x):
                return x
        """,
        )
        matches = select_archetypes(source)
        iv = [m for m in matches if m.name == "input_validation"][0]
        assert "typed_func" in iv.relevant_functions
        assert "untyped_func" not in iv.relevant_functions


class TestSelectArchetypesErrorHandling:
    """Tests for the error_handling archetype."""

    def test_try_except_gives_high_confidence(self, tmp_path: Path) -> None:
        source = _write_source(
            tmp_path,
            """\
            def safe_call():
                try:
                    return 1 / 0
                except ZeroDivisionError:
                    return 0
        """,
        )
        matches = select_archetypes(source)
        eh = [m for m in matches if m.name == "error_handling"]
        assert len(eh) == 1
        assert eh[0].confidence >= 0.8

    def test_http_imports_trigger_error_handling(self, tmp_path: Path) -> None:
        source = _write_source(
            tmp_path,
            """\
            import requests

            def fetch(url):
                return requests.get(url)
        """,
        )
        matches = select_archetypes(source)
        eh = [m for m in matches if m.name == "error_handling"]
        assert len(eh) == 1
        assert eh[0].confidence >= 0.7

    def test_subprocess_triggers_error_handling(self, tmp_path: Path) -> None:
        source = _write_source(
            tmp_path,
            """\
            import subprocess

            def run(cmd):
                subprocess.run(cmd)
        """,
        )
        matches = select_archetypes(source)
        eh = [m for m in matches if m.name == "error_handling"]
        assert len(eh) == 1
        assert eh[0].confidence >= 0.8

    def test_file_io_triggers_error_handling(self, tmp_path: Path) -> None:
        source = _write_source(
            tmp_path,
            """\
            def read(path):
                with open(path) as f:
                    return f.read()
        """,
        )
        matches = select_archetypes(source)
        eh = [m for m in matches if m.name == "error_handling"]
        assert len(eh) == 1
        assert eh[0].confidence >= 0.6

    def test_raising_functions_boost(self, tmp_path: Path) -> None:
        source = _write_source(
            tmp_path,
            """\
            def validate(x):
                raise ValueError("bad")
        """,
        )
        matches = select_archetypes(source)
        eh = [m for m in matches if m.name == "error_handling"]
        assert len(eh) == 1
        assert eh[0].confidence >= 0.7

    def test_no_error_signals_no_match(self, tmp_path: Path) -> None:
        source = _write_source(
            tmp_path,
            """\
            def add(a, b):
                return a + b
        """,
        )
        matches = select_archetypes(source)
        eh = [m for m in matches if m.name == "error_handling"]
        assert len(eh) == 0

    def test_relevant_functions_are_raising_ones(self, tmp_path: Path) -> None:
        source = _write_source(
            tmp_path,
            """\
            def good():
                pass

            def bad(x):
                raise ValueError("nope")
        """,
        )
        matches = select_archetypes(source)
        eh = [m for m in matches if m.name == "error_handling"]
        assert len(eh) == 1
        assert "bad" in eh[0].relevant_functions
        assert "good" not in eh[0].relevant_functions


class TestSelectArchetypesConfiguration:
    """Tests for the configuration archetype."""

    def test_dataclass_gives_high_confidence(self, tmp_path: Path) -> None:
        source = _write_source(
            tmp_path,
            """\
            from dataclasses import dataclass

            @dataclass
            class Config:
                name: str = "default"
        """,
        )
        matches = select_archetypes(source)
        cfg = [m for m in matches if m.name == "configuration"]
        assert len(cfg) == 1
        assert cfg[0].confidence >= 0.8

    def test_yaml_toml_import_gives_high_confidence(self, tmp_path: Path) -> None:
        source = _write_source(tmp_path, "import yaml\n")
        matches = select_archetypes(source)
        cfg = [m for m in matches if m.name == "configuration"]
        assert len(cfg) == 1
        assert cfg[0].confidence >= 0.8

    def test_init_with_defaults_gives_confidence(self, tmp_path: Path) -> None:
        source = _write_source(
            tmp_path,
            """\
            class Settings:
                def __init__(self, host="localhost", port=8080):
                    self.host = host
                    self.port = port
        """,
        )
        matches = select_archetypes(source)
        cfg = [m for m in matches if m.name == "configuration"]
        assert len(cfg) == 1
        assert cfg[0].confidence >= 0.7

    def test_init_without_defaults_no_match(self, tmp_path: Path) -> None:
        source = _write_source(
            tmp_path,
            """\
            class Worker:
                def __init__(self, name):
                    self.name = name
        """,
        )
        matches = select_archetypes(source)
        cfg = [m for m in matches if m.name == "configuration"]
        assert len(cfg) == 0

    def test_relevant_classes_populated(self, tmp_path: Path) -> None:
        source = _write_source(
            tmp_path,
            """\
            from dataclasses import dataclass

            @dataclass
            class Config:
                name: str = "default"

            class Plain:
                pass
        """,
        )
        matches = select_archetypes(source)
        cfg = [m for m in matches if m.name == "configuration"][0]
        assert "Config" in cfg.relevant_classes


class TestSelectArchetypesStateInvariant:
    """Tests for the state_invariant archetype."""

    def test_stateful_class_gives_high_confidence(self, tmp_path: Path) -> None:
        source = _write_source(
            tmp_path,
            """\
            class Counter:
                def __init__(self):
                    self.count = 0

                def increment(self):
                    self.count += 1
        """,
        )
        matches = select_archetypes(source)
        si = [m for m in matches if m.name == "state_invariant"]
        assert len(si) == 1
        assert si[0].confidence >= 0.8

    def test_many_mutable_fields_gives_highest_confidence(self, tmp_path: Path) -> None:
        source = _write_source(
            tmp_path,
            """\
            class Complex:
                def __init__(self):
                    self.a = 0
                    self.b = 0
                    self.c = 0
                    self.d = 0
        """,
        )
        matches = select_archetypes(source)
        si = [m for m in matches if m.name == "state_invariant"]
        assert len(si) == 1
        assert si[0].confidence >= 0.9

    def test_no_mutable_state_no_match(self, tmp_path: Path) -> None:
        source = _write_source(
            tmp_path,
            """\
            class Pure:
                def compute(self, x):
                    return x * 2
        """,
        )
        matches = select_archetypes(source)
        si = [m for m in matches if m.name == "state_invariant"]
        assert len(si) == 0

    def test_relevant_classes_are_stateful_ones(self, tmp_path: Path) -> None:
        source = _write_source(
            tmp_path,
            """\
            class Stateful:
                def __init__(self):
                    self.x = 0

            class Stateless:
                def compute(self):
                    return 42
        """,
        )
        matches = select_archetypes(source)
        si = [m for m in matches if m.name == "state_invariant"]
        assert len(si) == 1
        assert "Stateful" in si[0].relevant_classes
        assert "Stateless" not in si[0].relevant_classes


class TestSelectArchetypesMockIsolation:
    """Tests for the mock_isolation archetype."""

    def test_http_imports_high_confidence(self, tmp_path: Path) -> None:
        source = _write_source(tmp_path, "import requests\n")
        matches = select_archetypes(source)
        mi = [m for m in matches if m.name == "mock_isolation"]
        assert len(mi) == 1
        assert mi[0].confidence >= 0.9

    def test_subprocess_high_confidence(self, tmp_path: Path) -> None:
        source = _write_source(tmp_path, "import subprocess\n")
        matches = select_archetypes(source)
        mi = [m for m in matches if m.name == "mock_isolation"]
        assert len(mi) == 1
        assert mi[0].confidence >= 0.9

    def test_database_ops_high_confidence(self, tmp_path: Path) -> None:
        source = _write_source(tmp_path, "import sqlite3\n")
        matches = select_archetypes(source)
        mi = [m for m in matches if m.name == "mock_isolation"]
        assert len(mi) == 1
        assert mi[0].confidence >= 0.8

    def test_file_io_moderate_confidence(self, tmp_path: Path) -> None:
        source = _write_source(
            tmp_path,
            """\
            def read(path):
                with open(path) as f:
                    return f.read()
        """,
        )
        matches = select_archetypes(source)
        mi = [m for m in matches if m.name == "mock_isolation"]
        assert len(mi) == 1
        assert mi[0].confidence >= 0.7

    def test_no_io_no_match(self, tmp_path: Path) -> None:
        source = _write_source(
            tmp_path,
            """\
            def add(a, b):
                return a + b
        """,
        )
        matches = select_archetypes(source)
        mi = [m for m in matches if m.name == "mock_isolation"]
        assert len(mi) == 0


class TestSelectArchetypesRegression:
    """Tests for the regression archetype."""

    def test_complex_conditionals_low_confidence(self, tmp_path: Path) -> None:
        source = _write_source(
            tmp_path,
            """\
            def check(a, b, c, d):
                if a and b and c and d:
                    return True
                return False
        """,
        )
        matches = select_archetypes(source)
        reg = [m for m in matches if m.name == "regression"]
        assert len(reg) == 1
        assert reg[0].confidence >= 0.4

    def test_guard_plus_value_errors_moderate(self, tmp_path: Path) -> None:
        source = _write_source(
            tmp_path,
            """\
            def validate(x):
                if not x:
                    raise ValueError("empty")
                if x is None:
                    raise TypeError("none")
        """,
        )
        matches = select_archetypes(source)
        reg = [m for m in matches if m.name == "regression"]
        assert len(reg) == 1
        assert reg[0].confidence >= 0.5

    def test_no_signals_low_fallback_filtered_out(self, tmp_path: Path) -> None:
        """With no regression signals, confidence is 0.2 which is < 0.3 threshold."""
        source = _write_source(
            tmp_path,
            """\
            def add(a, b):
                return a + b
        """,
        )
        matches = select_archetypes(source)
        reg = [m for m in matches if m.name == "regression"]
        # 0.2 < 0.3 threshold, so it should be filtered out
        assert len(reg) == 0


class TestSelectArchetypesRoundTrip:
    """Tests for the round_trip archetype."""

    def test_encode_decode_high_confidence(self, tmp_path: Path) -> None:
        source = _write_source(
            tmp_path,
            """\
            def encode_data(x):
                return str(x)

            def decode_data(s):
                return int(s)
        """,
        )
        matches = select_archetypes(source)
        rt = [m for m in matches if m.name == "round_trip"]
        assert len(rt) == 1
        assert rt[0].confidence >= 0.9

    def test_to_from_pairs_high_confidence(self, tmp_path: Path) -> None:
        source = _write_source(
            tmp_path,
            """\
            def to_json(obj):
                pass

            def from_json(data):
                pass
        """,
        )
        matches = select_archetypes(source)
        rt = [m for m in matches if m.name == "round_trip"]
        assert len(rt) == 1
        assert rt[0].confidence >= 0.9

    def test_serialize_patterns_high_confidence(self, tmp_path: Path) -> None:
        source = _write_source(
            tmp_path,
            """\
            def serialize(obj):
                pass
        """,
        )
        matches = select_archetypes(source)
        rt = [m for m in matches if m.name == "round_trip"]
        assert len(rt) == 1
        assert rt[0].confidence >= 0.8

    def test_json_dump_load_moderate_confidence(self, tmp_path: Path) -> None:
        source = _write_source(
            tmp_path,
            """\
            import json

            def save(data):
                return json.dumps(data)
        """,
        )
        matches = select_archetypes(source)
        rt = [m for m in matches if m.name == "round_trip"]
        assert len(rt) == 1
        assert rt[0].confidence >= 0.7

    def test_no_round_trip_signals_no_match(self, tmp_path: Path) -> None:
        source = _write_source(
            tmp_path,
            """\
            def add(a, b):
                return a + b
        """,
        )
        matches = select_archetypes(source)
        rt = [m for m in matches if m.name == "round_trip"]
        assert len(rt) == 0


# ── Ordering and filtering tests ────────────────────────────────────────


class TestSelectArchetypesOrdering:
    """Tests for result ordering and filtering."""

    def test_results_sorted_by_confidence_descending(self, tmp_path: Path) -> None:
        source = _write_source(
            tmp_path,
            """\
            import requests
            from dataclasses import dataclass

            @dataclass
            class ApiConfig:
                base_url: str = "https://api.example.com"

            def fetch(config):
                try:
                    resp = requests.get(config.base_url)
                    return resp.json()
                except Exception as e:
                    raise ConnectionError(str(e))
        """,
        )
        matches = select_archetypes(source)
        for i in range(len(matches) - 1):
            assert matches[i].confidence >= matches[i + 1].confidence

    def test_all_results_at_or_above_0_3_threshold(self, tmp_path: Path) -> None:
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
            assert m.confidence >= 0.3, f"{m.name} has confidence {m.confidence} < 0.3"

    def test_regression_0_2_filtered_out(self, tmp_path: Path) -> None:
        """Regression with no signals has 0.2 confidence, should be filtered."""
        source = _write_source(tmp_path, "x = 1\n")
        matches = select_archetypes(source)
        reg = [m for m in matches if m.name == "regression"]
        assert len(reg) == 0


class TestSelectArchetypesMultipleMatches:
    """Tests for files that match multiple archetypes."""

    def test_http_file_matches_error_and_mock(self, tmp_path: Path) -> None:
        source = _write_source(
            tmp_path,
            """\
            import requests

            def fetch_users(base_url: str) -> list:
                try:
                    response = requests.get(f"{base_url}/users")
                    response.raise_for_status()
                    return response.json()
                except requests.RequestException as e:
                    raise ConnectionError(f"Failed: {e}")
        """,
        )
        matches = select_archetypes(source)
        names = {m.name for m in matches}
        assert "error_handling" in names
        assert "mock_isolation" in names

    def test_config_dataclass_with_validation(self, tmp_path: Path) -> None:
        source = _write_source(
            tmp_path,
            """\
            from dataclasses import dataclass

            @dataclass
            class Config:
                host: str = "localhost"
                port: int = 8080

            def validate_config(cfg: Config) -> Config:
                if not cfg.host:
                    raise ValueError("host required")
                return cfg
        """,
        )
        matches = select_archetypes(source)
        names = {m.name for m in matches}
        assert "configuration" in names
        assert "input_validation" in names

    def test_stateful_class_with_io(self, tmp_path: Path) -> None:
        source = _write_source(
            tmp_path,
            """\
            class FileProcessor:
                def __init__(self):
                    self.processed = []
                    self.errors = []
                    self.count = 0

                def process(self, path):
                    try:
                        with open(path) as f:
                            data = f.read()
                        self.processed.append(path)
                        self.count += 1
                    except OSError as e:
                        self.errors.append(str(e))
        """,
        )
        matches = select_archetypes(source)
        names = {m.name for m in matches}
        assert "state_invariant" in names
        assert "error_handling" in names
        assert "mock_isolation" in names

    def test_round_trip_with_serialization(self, tmp_path: Path) -> None:
        source = _write_source(
            tmp_path,
            """\
            import json

            def to_json(obj):
                return json.dumps(obj)

            def from_json(data):
                return json.loads(data)

            def serialize(obj):
                return to_json(obj)
        """,
        )
        matches = select_archetypes(source)
        names = {m.name for m in matches}
        assert "round_trip" in names


# ── Error path tests ────────────────────────────────────────────────────


class TestSelectArchetypesErrorPaths:
    """Tests for error handling in select_archetypes and extract_signals."""

    def test_nonexistent_file_returns_default(self) -> None:
        matches = select_archetypes("/nonexistent/file.py")
        assert len(matches) == 1
        assert matches[0].name == "input_validation"
        assert matches[0].confidence == 0.3
        assert "Could not read file" in matches[0].reason

    def test_syntax_error_file_returns_default(self, tmp_path: Path) -> None:
        source = _write_source(tmp_path, "def broken(:\n    pass")
        matches = select_archetypes(source)
        assert len(matches) == 1
        assert matches[0].name == "input_validation"
        assert matches[0].confidence == 0.3
        assert "Syntax error" in matches[0].reason

    def test_empty_file(self, tmp_path: Path) -> None:
        source = _write_source(tmp_path, "")
        matches = select_archetypes(source)
        assert len(matches) >= 1
        for m in matches:
            assert m.confidence <= 0.4

    def test_extract_signals_nonexistent_file(self) -> None:
        signals = extract_signals("/nonexistent/file.py")
        assert signals.functions == []
        assert signals.classes == []
        assert signals.imports == set()

    def test_extract_signals_syntax_error(self, tmp_path: Path) -> None:
        source = _write_source(tmp_path, "def broken(:\n    pass")
        signals = extract_signals(source)
        assert signals.functions == []
        assert signals.classes == []

    def test_unicode_decode_error_returns_default(self, tmp_path: Path) -> None:
        """Binary file that cannot be decoded should return default archetype."""
        filepath = tmp_path / "binary.py"
        filepath.write_bytes(b"\x80\x81\x82\x83\x84")
        matches = select_archetypes(str(filepath))
        assert len(matches) >= 1
        assert matches[0].name == "input_validation"

    def test_file_with_only_comments(self, tmp_path: Path) -> None:
        source = _write_source(
            tmp_path,
            """\
            # This is just a comment file
            # No actual code here
        """,
        )
        matches = select_archetypes(source)
        assert len(matches) >= 1

    def test_file_with_only_docstring(self, tmp_path: Path) -> None:
        source = _write_source(
            tmp_path,
            '"""Module docstring."""\n',
        )
        matches = select_archetypes(source)
        assert len(matches) >= 1


# ── Edge case and boundary tests ────────────────────────────────────────


class TestEdgeCases:
    """Edge case and boundary value tests."""

    def test_function_with_no_args(self, tmp_path: Path) -> None:
        source = _write_source(
            tmp_path,
            """\
            def no_args():
                return 42
        """,
        )
        signals = extract_signals(source)
        assert signals.functions[0].args == []

    def test_method_self_excluded_from_args(self, tmp_path: Path) -> None:
        source = _write_source(
            tmp_path,
            """\
            class Foo:
                def bar(self, x, y):
                    pass
        """,
        )
        signals = extract_signals(source)
        method = [f for f in signals.functions if f.name == "bar"][0]
        assert "self" not in method.args
        assert method.args == ["x", "y"]

    def test_kw_defaults_counted_in_init(self, tmp_path: Path) -> None:
        source = _write_source(
            tmp_path,
            """\
            class Foo:
                def __init__(self, *, a=1, b=2):
                    pass
        """,
        )
        signals = extract_signals(source)
        ci = signals.classes[0]
        assert ci.has_init is True
        assert ci.init_defaults >= 2

    def test_empty_class_body(self, tmp_path: Path) -> None:
        source = _write_source(
            tmp_path,
            """\
            class Empty:
                pass
        """,
        )
        signals = extract_signals(source)
        assert len(signals.classes) == 1
        assert signals.classes[0].methods == []
        assert signals.classes[0].mutable_fields == []

    def test_project_root_parameter_accepted(self, tmp_path: Path) -> None:
        """select_archetypes should accept project_root without error."""
        source = _write_source(tmp_path, "x = 1\n")
        matches = select_archetypes(source, project_root=str(tmp_path))
        assert len(matches) >= 1

    def test_guard_clause_compare_none_pattern(self, tmp_path: Path) -> None:
        """if x is None: raise ... should trigger guard clause."""
        source = _write_source(
            tmp_path,
            """\
            def validate(x):
                if x is None:
                    raise ValueError("cannot be None")
        """,
        )
        signals = extract_signals(source)
        assert signals.has_guard_clauses is True

    def test_guard_clause_equality_compare(self, tmp_path: Path) -> None:
        """if x == something: raise ... should trigger guard clause."""
        source = _write_source(
            tmp_path,
            """\
            def validate(x):
                if x == "":
                    raise ValueError("empty string")
        """,
        )
        signals = extract_signals(source)
        assert signals.has_guard_clauses is True

    def test_complex_conditional_exactly_three_values(self, tmp_path: Path) -> None:
        """BoolOp with exactly 3 values should trigger complex conditional."""
        source = _write_source(
            tmp_path,
            """\
            def check(a, b, c):
                if a and b and c:
                    return True
        """,
        )
        signals = extract_signals(source)
        assert signals.has_complex_conditionals is True

    def test_two_value_boolop_not_complex(self, tmp_path: Path) -> None:
        """BoolOp with only 2 values should NOT trigger complex conditional."""
        source = _write_source(
            tmp_path,
            """\
            def check(a, b):
                if a and b:
                    return True
        """,
        )
        signals = extract_signals(source)
        assert signals.has_complex_conditionals is False

    def test_multiple_archetypes_all_have_reasons(self, tmp_path: Path) -> None:
        """Every returned match should have a non-empty reason."""
        source = _write_source(
            tmp_path,
            """\
            import requests
            import json

            def fetch_and_parse(url: str) -> dict:
                try:
                    resp = requests.get(url)
                    return json.loads(resp.text)
                except Exception:
                    raise ValueError("failed")
        """,
        )
        matches = select_archetypes(source)
        assert len(matches) > 0
        for m in matches:
            assert m.reason, f"Archetype {m.name} has empty reason"

    def test_reasons_contain_meaningful_text(self, tmp_path: Path) -> None:
        source = _write_source(
            tmp_path,
            """\
            import requests

            def fetch(url):
                try:
                    return requests.get(url).json()
                except Exception:
                    pass
        """,
        )
        matches = select_archetypes(source)
        eh = [m for m in matches if m.name == "error_handling"]
        assert len(eh) == 1
        # reason should contain descriptive text about the signals found
        assert "try/except" in eh[0].reason or "HTTP" in eh[0].reason

    def test_case_insensitive_encode_decode_matching(self, tmp_path: Path) -> None:
        """Encode/decode matching should be case-insensitive on function name."""
        source = _write_source(
            tmp_path,
            """\
            def ENCODE_data(x):
                pass
        """,
        )
        signals = extract_signals(source)
        assert signals.has_encode_decode is True

    def test_serialize_pattern_case_insensitive(self, tmp_path: Path) -> None:
        source = _write_source(
            tmp_path,
            """\
            def Serialize_Object(obj):
                pass
        """,
        )
        signals = extract_signals(source)
        assert signals.has_serialize_patterns is True

    def test_large_file_with_many_functions(self, tmp_path: Path) -> None:
        """Ensure the module handles files with many functions."""
        funcs = "\n".join(f"def func_{i}(x: int) -> int:\n    return x + {i}\n" for i in range(50))
        source = _write_source(tmp_path, funcs)
        signals = extract_signals(source)
        assert len(signals.functions) == 50
        assert signals.has_typed_functions is True

    def test_dataclass_with_call_decorator(self, tmp_path: Path) -> None:
        """@dataclass() with parentheses should still be detected."""
        source = _write_source(
            tmp_path,
            """\
            from dataclasses import dataclass

            @dataclass()
            class Config:
                name: str = "default"
        """,
        )
        signals = extract_signals(source)
        assert signals.classes[0].is_dataclass is True
        assert signals.has_dataclasses is True
