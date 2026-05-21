"""Tests for lintgate.testing.oracle_types."""

from __future__ import annotations

from lintgate.testing.oracle_types import OracleRequest


def test_oracle_request_to_dict():
    """OracleRequest.to_dict() returns all fields."""
    req = OracleRequest(
        function_key="mod.py::func",
        category="VALUE",
        mutation_diff="- x\n+ y",
        required_oracle_type="value",
        context={"source": "test"},
    )
    d = req.to_dict()
    assert d == {
        "function_key": "mod.py::func",
        "category": "VALUE",
        "mutation_diff": "- x\n+ y",
        "required_oracle_type": "value",
        "context": {"source": "test"},
    }


def test_oracle_request_defaults():
    """Default required_oracle_type is 'value', context is empty dict."""
    req = OracleRequest(function_key="f::g", category="BOUNDARY")
    assert req.required_oracle_type == "value"
    assert req.mutation_diff == ""
    assert req.context == {}


def test_oracle_request_from_manual_contract():
    """Oracle request with manual_contract_reroute context serializes correctly."""
    req = OracleRequest(
        function_key="mod.py::MyClass.method",
        category="VALUE",
        required_oracle_type="value",
        context={"source": "manual_contract_reroute"},
    )
    d = req.to_dict()
    assert d["context"]["source"] == "manual_contract_reroute"
    assert d["function_key"] == "mod.py::MyClass.method"
    assert d["category"] == "VALUE"
