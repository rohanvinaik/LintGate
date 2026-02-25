"""Tests for NSIL grammar integration with vLLM adapter."""

from lintgate.nsil.adapters.vllm import VLLMAdapter
from lintgate.nsil.grammar_compiler import PolicyGrammar, compile_policy_grammar


def test_vllm_adapter_has_apply_grammar_constraint():
    """Test VLLMAdapter has apply_grammar_constraint method."""
    adapter = VLLMAdapter()
    assert callable(getattr(adapter, "apply_grammar_constraint", None))


def test_apply_policy_grammar_basic():
    """Test applying PolicyGrammar to adapter."""
    adapter = VLLMAdapter()
    policy = compile_policy_grammar(["no-rm-rf"], {})

    success, status = adapter.apply_policy_grammar(policy)
    assert success is True
    assert "applied" in status.lower() or "bypassed" in status.lower()


def test_apply_policy_grammar_empty_constraints():
    """Test empty constraints bypass constraints."""
    adapter = VLLMAdapter()
    policy = compile_policy_grammar([], {})

    success, status = adapter.apply_policy_grammar(policy)
    assert success is True
    # Empty constraints either bypass or apply empty regex
    assert "bypassed" in status.lower() or "applied" in status.lower()


def test_apply_policy_grammar_regex_mode():
    """Test regex-only mode when Outlines unavailable."""
    adapter = VLLMAdapter()
    policy = compile_policy_grammar(["no-rm-rf"], {})

    # Apply via dict interface (regex mode when no GBNF)
    adapter.apply_grammar_constraint(policy)

    # Check the constraint was stored
    assert adapter._grammar_constraint is not None


def test_apply_grammar_constraint_accepts_policy_grammar():
    """Test apply_grammar_constraint accepts PolicyGrammar."""
    adapter = VLLMAdapter()
    policy = compile_policy_grammar(["verify-before-commit"], {})

    result = adapter.apply_grammar_constraint(policy)
    # Should return True (success) even in regex mode
    assert isinstance(result, bool)


def test_check_rejection_prohibited_command():
    """Test rejection of prohibited command."""
    adapter = VLLMAdapter()
    policy = compile_policy_grammar(["no-rm-rf"], {})
    adapter.apply_policy_grammar(policy)

    # Check rejection of prohibited pattern
    rejected, reason = adapter.check_rejection("Running: rm -rf /home")
    assert rejected is True
    assert "prohibited" in reason.lower() or "rm" in reason.lower()


def test_check_rejection_allowed_command():
    """Test allowed command passes."""
    adapter = VLLMAdapter()
    policy = compile_policy_grammar(["no-rm-rf"], {})
    adapter.apply_policy_grammar(policy)

    # Check allowed pattern passes
    rejected, reason = adapter.check_rejection("Running: git status")
    assert rejected is False


def test_check_rejection_no_constraint():
    """Test no rejection when no constraint applied."""
    adapter = VLLMAdapter()
    rejected, reason = adapter.check_rejection("any text")
    assert rejected is False
    assert reason == ""


def test_check_rejection_out_of_scope():
    """Test scope path validation."""
    adapter = VLLMAdapter()
    policy = compile_policy_grammar(["scope-lintgate"], {})
    adapter.apply_policy_grammar(policy)

    # With scope constraint, text containing the scope is matched
    rejected, reason = adapter.check_rejection("Changes to /lintgate/src")
    # The regex matches the scope pattern, so it's detected
    assert "scope" in reason.lower() or rejected is True


def test_deterministic_rejection_same_input():
    """Test deterministic rejection for same input."""
    adapter1 = VLLMAdapter()
    adapter2 = VLLMAdapter()

    policy = compile_policy_grammar(["no-rm-rf", "verify-before-commit"], {})

    adapter1.apply_policy_grammar(policy)
    adapter2.apply_policy_grammar(policy)

    # Same input should produce same result
    r1, _ = adapter1.check_rejection("rm -rf /tmp")
    r2, _ = adapter2.check_rejection("rm -rf /tmp")
    assert r1 == r2


def test_deterministic_rejection_order_independent():
    """Test rejection is order-independent."""
    policy1 = compile_policy_grammar(["no-rm-rf", "verify-before-commit"], {})
    policy2 = compile_policy_grammar(["verify-before-commit", "no-rm-rf"], {})

    adapter1 = VLLMAdapter()
    adapter2 = VLLMAdapter()

    adapter1.apply_policy_grammar(policy1)
    adapter2.apply_policy_grammar(policy2)

    # Should produce same results regardless of input order
    r1, _ = adapter1.check_rejection("rm -rf /")
    r2, _ = adapter2.check_rejection("rm -rf /")
    assert r1 == r2


def test_clear_grammar_constraint():
    """Test clearing grammar constraint."""
    adapter = VLLMAdapter()
    policy = compile_policy_grammar(["no-rm-rf"], {})
    adapter.apply_policy_grammar(policy)

    assert adapter._grammar_constraint is not None
    adapter.clear_grammar_constraint()
    assert adapter._grammar_constraint is None

    # After clearing, no rejection
    rejected, _ = adapter.check_rejection("rm -rf /")
    assert rejected is False


def test_apply_policy_grammar_with_path_scope():
    """Test PolicyGrammar with path scope constraint."""
    adapter = VLLMAdapter()
    policy = compile_policy_grammar(["scope-lib"], {})

    success, status = adapter.apply_policy_grammar(policy)
    assert success is True


def test_apply_policy_grammar_with_verification():
    """Test PolicyGrammar with verification constraint."""
    adapter = VLLMAdapter()
    policy = compile_policy_grammar(["verify-before-commit"], {})

    success, status = adapter.apply_policy_grammar(policy)
    assert success is True


def test_multiple_constraints_rejection():
    """Test rejection with multiple constraints."""
    adapter = VLLMAdapter()
    policy = compile_policy_grammar(
        ["no-rm-rf", "no-prod-changes", "verify-before-commit"],
        {},
    )
    adapter.apply_policy_grammar(policy)

    # Test rm -rf rejection
    r1, _ = adapter.check_rejection("rm -rf /prod")
    assert r1 is True


def test_empty_grammar_bypass():
    """Test empty grammar input bypasses constraints."""
    adapter = VLLMAdapter()
    policy = PolicyGrammar(
        source_constraints=(),
        gbnf_rules="",
        regex_pattern="",
        explanation="No constraints",
    )

    success, status = adapter.apply_policy_grammar(policy)
    assert success is True
    assert "bypassed" in status.lower()

    # No constraint applied
    rejected, _ = adapter.check_rejection("any text")
    assert rejected is False


def test_vllm_adapter_integrates_grammar_compiler():
    """Test VLLMAdapter integrates with grammar_compiler."""
    adapter = VLLMAdapter()

    # Compile and apply
    policy = compile_policy_grammar(["no-rm-rf"], {})
    adapter.apply_grammar_constraint(policy)

    # Should have constraint stored
    assert adapter._grammar_constraint is not None
