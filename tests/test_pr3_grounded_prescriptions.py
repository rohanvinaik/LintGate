"""PR3: Grounded prescriptions and witness generation tests.

Validates that mutation_prescribe produces mutant-specific guidance
from survivor records, not just category templates.
"""

from __future__ import annotations

from lintgate.specification.witness_generation import (
    generate_witness_prescription,
)

# ── Witness generation per category ──────────────────────────────


class TestValueWitness:
    def test_produces_grounded_prescription(self):
        survivor = {
            "mutant_id": "VALUE_0",
            "category": "VALUE",
            "description": "VALUE_0: replace constant",
            "diff_summary": "- x + 1\n+ x + 0",
            "location": 5,
            "status": "survived",
        }
        rx = generate_witness_prescription(survivor, "mod.py::add")
        assert rx["mutant_id"] == "VALUE_0"
        assert rx["category"] == "VALUE"
        assert rx["source_of_evidence"] == "survivor_record"
        assert "VALUE" in rx["why_this_matters"]
        assert rx["confidence"] == 0.8
        assert "add" in rx["assertion_shape"]

    def test_without_diff_has_lower_confidence(self):
        survivor = {
            "mutant_id": "VALUE_1",
            "category": "VALUE",
            "description": "VALUE_1: replace constant",
            "diff_summary": "",
        }
        rx = generate_witness_prescription(survivor, "f")
        assert rx["confidence"] == 0.5
        assert rx["needs_source_review"] is True
        assert rx["source_of_evidence"] == "category_template"


class TestBoundaryWitness:
    def test_produces_boundary_prescription(self):
        survivor = {
            "mutant_id": "BOUNDARY_0",
            "category": "BOUNDARY",
            "description": "BOUNDARY_0: off-by-one",
            "diff_summary": "- x < 10\n+ x <= 10",
        }
        rx = generate_witness_prescription(survivor, "check.py::validate")
        assert rx["category"] == "BOUNDARY"
        assert "boundary" in rx["why_this_matters"].lower()
        assert rx["confidence"] == 0.85
        assert "10" in rx["suggested_input"]


class TestSwapWitness:
    def test_produces_swap_prescription(self):
        survivor = {
            "mutant_id": "SWAP_0",
            "category": "SWAP",
            "description": "SWAP_0: transpose call arguments",
            "diff_summary": "",
        }
        rx = generate_witness_prescription(survivor, "utils.py::compute")
        assert rx["category"] == "SWAP"
        assert "compute" in rx["assertion_shape"]
        assert rx["confidence"] == 0.75


class TestStateWitness:
    def test_return_none_variant(self):
        survivor = {
            "mutant_id": "STATE_return_none_0",
            "category": "STATE",
            "description": "STATE_return_none_0: replace return with None",
            "diff_summary": "",
        }
        rx = generate_witness_prescription(survivor, "m.py::get_value")
        assert "return" in rx["why_this_matters"].lower()
        assert "not None" in rx["assertion_shape"]

    def test_remove_assign_variant(self):
        survivor = {
            "mutant_id": "STATE_remove_assign_0",
            "category": "STATE",
            "description": "STATE_remove_assign_0: remove state assignment",
            "diff_summary": "",
        }
        rx = generate_witness_prescription(survivor, "m.py::set_value")
        assert "assignment" in rx["why_this_matters"].lower()
        assert rx["needs_source_review"] is True


class TestTypeWitness:
    def test_produces_type_prescription(self):
        survivor = {
            "mutant_id": "TYPE_0",
            "category": "TYPE",
            "description": "TYPE_0: replace isinstance with True",
            "diff_summary": "",
        }
        rx = generate_witness_prescription(survivor, "m.py::check")
        assert "isinstance" in rx["why_this_matters"]
        assert rx["confidence"] == 0.7


class TestGenericWitness:
    def test_unknown_category_falls_back(self):
        survivor = {
            "mutant_id": "WEIRD_0",
            "category": "WEIRD",
            "description": "some mutation",
            "diff_summary": "",
        }
        rx = generate_witness_prescription(survivor, "m.py::f")
        assert rx["confidence"] == 0.4
        assert rx["needs_source_review"] is True


# ── Prescription collection integration ──────────────────────────


class TestCollectPrescriptions:
    def test_uses_survivor_records_when_available(self):
        from mcp_tools._mutation_tools_impl import _collect_prescriptions

        states = [
            {
                "function_key": "mod.py::f",
                "per_category": [{"category": "VALUE", "survived": 1, "survival_rate": 1.0}],
                "survivor_records": [
                    {
                        "mutant_id": "VALUE_0",
                        "category": "VALUE",
                        "description": "VALUE_0: replace constant",
                        "diff_summary": "- 1\n+ 0",
                        "status": "survived",
                    }
                ],
            }
        ]
        prescriptions = _collect_prescriptions(states)
        assert len(prescriptions) == 1
        rx = prescriptions[0]
        assert rx["category"] == "VALUE"
        assert rx["function"] == "mod.py::f"
        assert "action" in rx

    def test_falls_back_to_category_templates(self):
        from mcp_tools._mutation_tools_impl import _collect_prescriptions

        states = [
            {
                "function_key": "mod.py::g",
                "per_category": [{"category": "BOUNDARY", "survived": 2, "survival_rate": 1.0}],
            }
        ]
        prescriptions = _collect_prescriptions(states)
        assert len(prescriptions) == 1
        rx = prescriptions[0]
        assert rx["category"] == "BOUNDARY"
        assert "action" in rx

    def test_no_prescriptions_when_no_survivors(self):
        from mcp_tools._mutation_tools_impl import _collect_prescriptions

        states = [
            {
                "function_key": "mod.py::h",
                "per_category": [{"category": "VALUE", "survived": 0}],
            }
        ]
        prescriptions = _collect_prescriptions(states)
        assert len(prescriptions) == 0
