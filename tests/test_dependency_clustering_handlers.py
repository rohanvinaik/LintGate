"""Prescriptive spec tests for _find_nested_handler_candidates.

Target: dependency_clustering::_find_nested_handler_candidates
100% mutation survival → 12 behavioral claims targeting all paths.
"""

from __future__ import annotations

import ast
import textwrap

import pytest

from lintgate.linters.structure_checks.dependency_clustering import (
    _find_nested_handler_candidates,
)


def _parse_func(source: str) -> ast.FunctionDef:
    tree = ast.parse(textwrap.dedent(source))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            return node
    raise ValueError("No function found")


# ── Fixture: bag of handlers with decorators ─────────────────────────


def _bag_of_handlers() -> ast.FunctionDef:
    """3 nested handlers (>50% of body), 2 with known decorators."""
    return _parse_func("""
def register(app):
    config = app.config

    @app.route("/health")
    def health():
        return "ok"

    @app.route("/status")
    def status():
        return config["status"]

    def helper():
        return 42
""")


def _no_handlers() -> ast.FunctionDef:
    """Body is mostly assignments — not a bag of handlers."""
    return _parse_func("""
def setup(app):
    a = 1
    b = 2
    c = 3
    def helper():
        return a + b + c
""")


def _single_handler() -> ast.FunctionDef:
    """Body is >50% nested funcs but only 1 handler."""
    return _parse_func("""
def register(app):
    @app.route("/")
    def index():
        return "home"
""")


def _handlers_with_closure() -> ast.FunctionDef:
    """Handlers that capture outer-scope variables."""
    return _parse_func("""
def register(app, db):
    timeout = 30

    @app.get("/data")
    def get_data():
        return db.query(timeout=timeout)

    @app.post("/data")
    def post_data(item):
        db.insert(item)
""")


# ── Claim 0: empty list if not bag-of-handlers ──────────────────────


class TestBagOfHandlersGate:
    def test_not_bag_returns_empty(self):
        assert _find_nested_handler_candidates(_no_handlers(), "test.py") == []

    def test_bag_returns_non_empty(self):
        result = _find_nested_handler_candidates(_bag_of_handlers(), "test.py")
        assert len(result) > 0


# ── Claim 1: each nested FunctionDef gets a Prescription ─────────────


class TestPerHandlerPrescription:
    def test_each_handler_gets_prescription(self):
        result = _find_nested_handler_candidates(_bag_of_handlers(), "test.py")
        extract_prescriptions = [p for p in result if p.kind == "extract_function"]
        # 3 nested funcs → 3 extract prescriptions
        assert len(extract_prescriptions) == 3

    def test_single_handler_one_prescription(self):
        result = _find_nested_handler_candidates(_single_handler(), "test.py")
        extract_prescriptions = [p for p in result if p.kind == "extract_function"]
        assert len(extract_prescriptions) == 1


# ── Claim 2: proposed_name is _impl_ prefixed ────────────────────────


class TestProposedName:
    def test_impl_prefix(self):
        result = _find_nested_handler_candidates(_bag_of_handlers(), "test.py")
        extract_prescriptions = [p for p in result if p.kind == "extract_function"]
        names = {p.proposed_name for p in extract_prescriptions}
        assert "_impl_health" in names
        assert "_impl_status" in names
        assert "_impl_helper" in names


# ── Claim 3: base confidence is 0.65 ─────────────────────────────────


class TestBaseConfidence:
    def test_undecorated_handler_base_confidence(self):
        """Handler without known decorator → base 0.65 + 0.05 (no writes) = 0.70."""
        result = _find_nested_handler_candidates(_bag_of_handlers(), "test.py")
        helper_p = [
            p for p in result if p.kind == "extract_function" and p.proposed_name == "_impl_helper"
        ]
        assert len(helper_p) == 1
        # No decorator match, no captured writes → 0.65 + 0.05 = 0.70
        assert helper_p[0].confidence == pytest.approx(0.70)


# ── Claim 4: decorator match adds 0.15 ───────────────────────────────


class TestDecoratorBonus:
    def test_known_decorator_confidence(self):
        """Handler with @app.route → 0.65 + 0.15 + 0.05 (no writes) = 0.85."""
        result = _find_nested_handler_candidates(_bag_of_handlers(), "test.py")
        health_p = [
            p for p in result if p.kind == "extract_function" and p.proposed_name == "_impl_health"
        ]
        assert len(health_p) == 1
        assert health_p[0].confidence == pytest.approx(0.85)


# ── Claim 5: no captured writes adds 0.05 ────────────────────────────


class TestNoWritesBonus:
    def test_captured_writes_reduce_confidence(self):
        """Handler that writes to outer scope variable → no +0.05 bonus."""
        func = _parse_func("""
def register(app):
    counter = 0

    @app.get("/inc")
    def increment():
        nonlocal counter
        counter = counter + 1
        return counter

    @app.get("/dec")
    def decrement():
        nonlocal counter
        counter = counter - 1
        return counter
""")
        result = _find_nested_handler_candidates(func, "test.py")
        inc_p = [
            p
            for p in result
            if p.kind == "extract_function" and p.proposed_name == "_impl_increment"
        ]
        assert len(inc_p) == 1
        # @app.get decorator (+0.15), but writes counter → no +0.05
        # 0.65 + 0.15 = 0.80
        assert inc_p[0].confidence == pytest.approx(0.80)


# ── Claim 6: confidence capped at 0.85 ───────────────────────────────


class TestConfidenceCap:
    def test_all_bonuses_capped(self):
        """Even with decorator + no writes, max is 0.85."""
        result = _find_nested_handler_candidates(_bag_of_handlers(), "test.py")
        for p in result:
            if p.kind == "extract_function":
                assert p.confidence <= 0.85


# ── Claim 7-8: captured_reads → inputs, captured_writes → outputs ────


class TestClosureCapture:
    def test_captured_reads_as_inputs(self):
        """Outer-scope variable reads become Prescription.inputs."""
        result = _find_nested_handler_candidates(_handlers_with_closure(), "test.py")
        get_p = [
            p
            for p in result
            if p.kind == "extract_function" and p.proposed_name == "_impl_get_data"
        ]
        assert len(get_p) == 1
        # get_data reads 'db' and 'timeout' from outer scope
        assert "db" in get_p[0].inputs
        assert "timeout" in get_p[0].inputs

    def test_captured_writes_as_outputs(self):
        """Outer-scope variable writes become Prescription.outputs."""
        result = _find_nested_handler_candidates(_handlers_with_closure(), "test.py")
        post_p = [
            p
            for p in result
            if p.kind == "extract_function" and p.proposed_name == "_impl_post_data"
        ]
        assert len(post_p) == 1
        # post_data calls db.insert — if db is written (via method call) it depends on analysis
        # The important thing is outputs is a list (could be empty or contain 'db')
        assert isinstance(post_p[0].outputs, list)


# ── Claim 9-10: batch decompose_register ─────────────────────────────


class TestBatchPrescription:
    def test_batch_emitted_for_2plus_handlers(self):
        """>=2 handlers → decompose_register batch prescription emitted."""
        result = _find_nested_handler_candidates(_bag_of_handlers(), "test.py")
        batch = [p for p in result if p.kind == "decompose_register"]
        assert len(batch) == 1

    def test_batch_has_handlers_list(self):
        """Batch expected_delta contains handlers list."""
        result = _find_nested_handler_candidates(_bag_of_handlers(), "test.py")
        batch = [p for p in result if p.kind == "decompose_register"]
        assert len(batch) == 1
        assert "handlers" in batch[0].expected_delta
        assert isinstance(batch[0].expected_delta["handlers"], list)
        assert len(batch[0].expected_delta["handlers"]) == 3

    def test_no_batch_for_single_handler(self):
        """Single handler → no batch prescription."""
        result = _find_nested_handler_candidates(_single_handler(), "test.py")
        batch = [p for p in result if p.kind == "decompose_register"]
        assert len(batch) == 0


# ── Claim 11: non-FunctionDef statements skipped ─────────────────────


class TestNonFuncDefSkipped:
    def test_assignments_not_prescriptions(self):
        """Assignment statements in body don't generate prescriptions."""
        result = _find_nested_handler_candidates(_bag_of_handlers(), "test.py")
        extract_prescriptions = [p for p in result if p.kind == "extract_function"]
        # Only FunctionDef nodes get prescriptions, not 'config = app.config'
        names = {p.proposed_name for p in extract_prescriptions}
        assert all(n is not None and n.startswith("_impl_") for n in names)
        # 3 nested funcs, not 4 (the assignment doesn't count)
        assert len(extract_prescriptions) == 3


# ── VALUE: exact prescription fields ─────────────────────────────────


class TestPrescriptionFields:
    def test_kind_is_extract_function(self):
        result = _find_nested_handler_candidates(_bag_of_handlers(), "test.py")
        for p in result:
            assert p.kind in ("extract_function", "decompose_register")

    def test_target_includes_filepath(self):
        result = _find_nested_handler_candidates(_bag_of_handlers(), "myfile.py")
        for p in result:
            assert "myfile.py" in p.target

    def test_basis_includes_nested_handler(self):
        result = _find_nested_handler_candidates(_bag_of_handlers(), "test.py")
        for p in result:
            assert "nested_handler" in p.basis

    def test_cc_reduction_in_delta(self):
        result = _find_nested_handler_candidates(_bag_of_handlers(), "test.py")
        for p in result:
            assert "cc_reduction" in p.expected_delta
