"""Tests for NSIL grammar compiler."""

import pytest

from lintgate.nsil.grammar_compiler import (
    PolicyGrammar,
    compile_from_gate_contract,
    compile_policy_grammar,
)


def test_policy_grammar_defaults():
    """Test PolicyGrammar has correct defaults."""
    g = PolicyGrammar()
    assert g.source_constraints == ()
    assert g.gbnf_rules == ""
    assert g.regex_pattern == ""
    assert g.explanation == ""


def test_policy_grammar_frozen():
    """Test PolicyGrammar is frozen."""
    g = PolicyGrammar(source_constraints=("test",))
    with pytest.raises(AttributeError):
        g.source_constraints = ("other",)  # type: ignore


def test_compile_empty_constraints():
    """Test compilation with no constraints."""
    g = compile_policy_grammar([], {})
    assert g.source_constraints == ()
    assert g.gbnf_rules == "grammar ::= .* ;"
    assert g.regex_pattern == ".*"
    assert "No constraints" in g.explanation


def test_compile_dangerous_command():
    """Test dangerous command constraint compilation."""
    g = compile_policy_grammar(["no-rm-rf"], {})
    assert "rm" in g.gbnf_rules.lower()
    assert "rm" in g.regex_pattern.lower()
    assert "dangerous" in g.explanation.lower()


def test_compile_verify_commit():
    """Test verification constraint compilation."""
    g = compile_policy_grammar(["verify-before-commit"], {})
    assert "verification" in g.explanation.lower()
    assert "verification" in g.regex_pattern.lower()


def test_compile_path_scope():
    """Test path scope constraint compilation."""
    g = compile_policy_grammar(["scope-lintgate"], {})
    assert "lintgate" in g.gbnf_rules.lower() or "scope" in g.explanation.lower()
    assert "lintgate" in g.regex_pattern.lower() or "scope" in g.explanation.lower()


def test_compile_no_prod():
    """Test no-production constraint compilation."""
    g = compile_policy_grammar(["no-prod-changes"], {})
    assert "prod" in g.explanation.lower() or "production" in g.explanation.lower()


def test_compile_unknown_constraint():
    """Test unknown constraint produces explanation note."""
    g = compile_policy_grammar(["unknown-constraint-type"], {})
    assert "Unknown constraint" in g.explanation
    # Should not crash, still returns valid grammar
    assert g.gbnf_rules
    assert g.regex_pattern


def test_compile_multiple_constraints():
    """Test multiple constraints are compiled."""
    g = compile_policy_grammar(
        ["no-rm-rf", "verify-before-commit", "scope-lib"],
        {},
    )
    assert len(g.source_constraints) == 3
    assert "rm" in g.gbnf_rules.lower()
    assert "verification" in g.explanation.lower()
    assert "lib" in g.regex_pattern.lower() or "scope" in g.explanation.lower()


def test_compile_deterministic_output():
    """Test deterministic compilation - same input = same output."""
    g1 = compile_policy_grammar(["no-rm-rf", "verify-before-commit"], {})
    g2 = compile_policy_grammar(["verify-before-commit", "no-rm-rf"], {})
    g3 = compile_policy_grammar(["no-rm-rf", "verify-before-commit"], {})

    # Should be identical regardless of input order
    assert g1.gbnf_rules == g2.gbnf_rules == g3.gbnf_rules
    assert g1.regex_pattern == g2.regex_pattern == g3.regex_pattern
    assert g1.explanation == g2.explanation == g3.explanation


def test_compile_removes_duplicates():
    """Test duplicate constraints are deduplicated."""
    g = compile_policy_grammar(["no-rm-rf", "no-rm-rf", "verify-before-commit"], {})
    assert len(g.source_constraints) == 2


def test_compile_preserves_constraint_order():
    """Test source_constraints preserves sorted order."""
    g = compile_policy_grammar(
        ["z-constraint", "a-constraint", "m-constraint"],
        {},
    )
    assert g.source_constraints == ("a-constraint", "m-constraint", "z-constraint")


def test_compile_does_not_mutate_input():
    """Test compiler doesn't mutate input data."""
    original_constraints = ["no-rm-rf"]
    original_contract = {"key": "value"}

    compile_policy_grammar(original_constraints, original_contract)

    # Original should be unchanged
    assert original_constraints == ["no-rm-rf"]
    assert original_contract == {"key": "value"}


def test_compile_from_gate_contract_empty():
    """Test compile_from_gate_contract with no file."""
    g = compile_from_gate_contract()
    assert isinstance(g, PolicyGrammar)
    assert "No constraints" in g.explanation


def test_compile_from_gate_contract_with_file():
    """Test compile_from_gate_contract with file."""
    # Use the actual gate_contract.yaml
    g = compile_from_gate_contract(contract_path="gate_contract.yaml")
    assert isinstance(g, PolicyGrammar)
    # Gate contract exists and has data, may or may not have constraints


def test_compile_from_gate_contract_with_constraints():
    """Test compile_from_gate_contract with explicit constraints."""
    g = compile_from_gate_contract(
        contract_path="gate_contract.yaml",
        constraints=["no-rm-rf"],
    )
    assert isinstance(g, PolicyGrammar)
    assert "no-rm-rf" in g.source_constraints


def test_policy_grammar_all_fields():
    """Test PolicyGrammar with all fields populated."""
    g = PolicyGrammar(
        source_constraints=("c1", "c2"),
        gbnf_rules="grammar ::= .* ;",
        regex_pattern=".*",
        explanation="Test explanation",
    )
    assert g.source_constraints == ("c1", "c2")
    assert g.gbnf_rules == "grammar ::= .* ;"
    assert g.regex_pattern == ".*"
    assert g.explanation == "Test explanation"


def test_compile_require_tests():
    """Test require-tests constraint."""
    g = compile_policy_grammar(["require-tests"], {})
    assert "test" in g.explanation.lower()
    assert "test" in g.regex_pattern.lower()


def test_compile_mixed_known_unknown():
    """Test mix of known and unknown constraints."""
    g = compile_policy_grammar(
        ["no-rm-rf", "totally-unknown-constraint", "verify-before-commit"],
        {},
    )
    # Known constraints should be compiled
    assert "rm" in g.gbnf_rules.lower()
    assert "verification" in g.explanation.lower()
    # Unknown constraint should have explanation note
    assert "Unknown constraint" in g.explanation
    assert "totally-unknown-constraint" in g.explanation
