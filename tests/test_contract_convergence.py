"""Phase 5a: CONTRACT_COVERAGE convergence lens tests.

Verifies that the contract coverage adapter produces correct evidence
when functions appear in published metrics but not in consumed metrics.
"""

from __future__ import annotations

from lintgate.convergence.aggregator import adapt_contract_coverage
from lintgate.convergence.evidence import LensKind


def test_contract_coverage_basic():
    """Functions published but not consumed produce support evidence."""
    published = {
        "mod.py::func_a": {"channel": "specification", "metric_key": "specification_function_list"},
        "mod.py::func_b": {"channel": "specification", "metric_key": "specification_function_list"},
    }
    consumed = {
        "mod.py::func_a": {"channel": "coherence"},
    }

    evidence = adapt_contract_coverage(published, consumed)
    assert len(evidence) == 1
    assert evidence[0].target == "mod.py::func_b"
    assert evidence[0].lens == LensKind.CONTRACT_COVERAGE
    assert evidence[0].signal == "support"
    assert evidence[0].confidence == 0.5


def test_contract_coverage_all_consumed():
    """No evidence when all published targets are consumed."""
    published = {
        "mod.py::func_a": {"channel": "specification", "metric_key": "specification_function_list"},
    }
    consumed = {
        "mod.py::func_a": {"channel": "coherence"},
    }

    evidence = adapt_contract_coverage(published, consumed)
    assert evidence == []


def test_contract_coverage_empty_published():
    """No evidence when nothing is published."""
    evidence = adapt_contract_coverage({}, {"mod.py::func_a": {"channel": "coherence"}})
    assert evidence == []


def test_contract_coverage_empty_consumed():
    """All published targets appear as evidence when nothing is consumed."""
    published = {
        "mod.py::func_a": {"channel": "performance", "metric_key": "pure_function_list"},
        "mod.py::func_b": {"channel": "specification", "metric_key": "specification_function_list"},
    }

    evidence = adapt_contract_coverage(published, {})
    assert len(evidence) == 2
    targets = {e.target for e in evidence}
    assert targets == {"mod.py::func_a", "mod.py::func_b"}


def test_contract_coverage_lens_kind_exists():
    """CONTRACT_COVERAGE is a valid LensKind."""
    assert LensKind.CONTRACT_COVERAGE == "contract_coverage"
    assert LensKind.CONTRACT_COVERAGE.value == "contract_coverage"


def test_contract_coverage_integration_with_aggregate():
    """Contract coverage evidence participates in convergence aggregation."""
    from lintgate.convergence.aggregator import aggregate

    published = {
        "mod.py::func_a": {"channel": "specification", "metric_key": "specification_function_list"},
    }
    evidence = adapt_contract_coverage(published, {})
    results = aggregate(evidence)

    assert len(results) == 1
    assert results[0].target == "mod.py::func_a"
    assert LensKind.CONTRACT_COVERAGE in results[0].supporting_lenses
