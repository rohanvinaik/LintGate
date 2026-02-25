"""Tests for NSIL runtime adapter protocol and discovery."""

import pytest

from lintgate.nsil.runtime_adapter import (
    KNOWN_RUNTIMES,
    LocalRuntimeAdapter,
    RuntimeCapabilities,
    RuntimeProbeResult,
    _probe_runtime,
    detect_local_runtimes,
)


def test_runtime_capabilities_defaults():
    """Test RuntimeCapabilities has correct defaults."""
    caps = RuntimeCapabilities()
    assert caps.supports_state_injection is False
    assert caps.supports_streaming_hooks is False
    assert caps.supports_grammar_constraints is False
    assert caps.supports_logit_processors is False
    assert caps.max_context_tokens == 0
    assert caps.api_protocol == "custom"


def test_runtime_capabilities_frozen():
    """Test RuntimeCapabilities is frozen (immutable)."""
    caps = RuntimeCapabilities(supports_state_injection=True)
    with pytest.raises(AttributeError):
        caps.supports_state_injection = False  # type: ignore


def test_runtime_probe_result_status_values():
    """Test RuntimeProbeResult accepts valid status values."""
    # Valid status values
    available = RuntimeProbeResult(runtime_name="test", status="available")
    assert available.status == "available"

    unavailable = RuntimeProbeResult(runtime_name="test", status="unavailable")
    assert unavailable.status == "unavailable"

    error = RuntimeProbeResult(runtime_name="test", status="error")
    assert error.status == "error"


def test_runtime_probe_result_frozen():
    """Test RuntimeProbeResult is frozen."""
    result = RuntimeProbeResult(runtime_name="test", status="available")
    with pytest.raises(AttributeError):
        result.status = "unavailable"  # type: ignore


def test_local_runtime_adapter_protocol():
    """Test LocalRuntimeAdapter is a valid Protocol."""

    # Create a minimal implementation
    class MinimalAdapter:
        def inject_state(self, snapshot):
            return True

        def register_action_hook(self, callback):
            pass

        def get_generation_stream(self, prompt, **kwargs):
            yield "test"

        def apply_grammar_constraint(self, grammar):
            return True

        def get_capabilities(self):
            return RuntimeCapabilities()

    # Verify it satisfies the protocol
    adapter: LocalRuntimeAdapter = MinimalAdapter()
    assert adapter.inject_state({}) is True
    assert callable(adapter.register_action_hook)
    assert adapter.apply_grammar_constraint({}) is True
    assert isinstance(adapter.get_capabilities(), RuntimeCapabilities)


def test_detect_local_runtimes_returns_list():
    """Test detect_local_runtimes returns a list."""
    results = detect_local_runtimes(timeout_ms=50)
    assert isinstance(results, list)


def test_detect_local_runtimes_deterministic_order():
    """Test detect_local_runtimes returns deterministic order."""
    results = detect_local_runtimes(timeout_ms=50)
    names = [r.runtime_name for r in results]
    assert names == sorted(names)


def test_detect_local_runtimes_has_results():
    """Test detect_local_runtimes returns results for all known runtimes."""
    results = detect_local_runtimes(timeout_ms=50)
    result_names = {r.runtime_name for r in results}
    expected_names = {runtime["name"] for runtime in KNOWN_RUNTIMES}
    assert result_names == expected_names


def test_detect_local_runtimes_all_have_status():
    """Test all results have a valid status."""
    results = detect_local_runtimes(timeout_ms=50)
    for result in results:
        assert result.status in ("available", "unavailable", "error")
        assert result.runtime_name


def test_detect_local_runtimes_with_zero_timeout():
    """Test detect_local_runtimes handles zero timeout gracefully."""
    results = detect_local_runtimes(timeout_ms=0)
    # Should still return results, just with timeouts
    assert isinstance(results, list)
    assert len(results) > 0


def test_probe_runtime_unavailable_port():
    """Test probing an unavailable port returns unavailable status."""
    result = _probe_runtime(
        name="test_runtime",
        port=59999,  # Non-existent port
        health_endpoint="/health",
        capabilities=RuntimeCapabilities(),
        timeout_ms=100,
    )
    assert result.status == "unavailable"
    assert result.runtime_name == "test_runtime"


def test_runtime_capabilities_with_values():
    """Test RuntimeCapabilities with specific values."""
    caps = RuntimeCapabilities(
        supports_state_injection=True,
        supports_streaming_hooks=True,
        supports_grammar_constraints=True,
        supports_logit_processors=True,
        max_context_tokens=32768,
        api_protocol="openai",
    )
    assert caps.supports_state_injection is True
    assert caps.supports_streaming_hooks is True
    assert caps.supports_grammar_constraints is True
    assert caps.supports_logit_processors is True
    assert caps.max_context_tokens == 32768
    assert caps.api_protocol == "openai"


def test_runtime_probe_result_with_all_fields():
    """Test RuntimeProbeResult with all fields populated."""
    caps = RuntimeCapabilities(api_protocol="ollama")
    result = RuntimeProbeResult(
        runtime_name="test_runtime",
        status="available",
        capabilities=caps,
        endpoint="http://localhost:11434",
        error_message=None,
        latency_ms=25.5,
    )
    assert result.runtime_name == "test_runtime"
    assert result.status == "available"
    assert result.capabilities == caps
    assert result.endpoint == "http://localhost:11434"
    assert result.error_message is None
    assert result.latency_ms == 25.5


def test_detect_local_runtimes_creates_results_with_correct_fields():
    """Test that probe results have all required fields."""
    results = detect_local_runtimes(timeout_ms=50)
    for result in results:
        assert hasattr(result, "runtime_name")
        assert hasattr(result, "status")
        assert hasattr(result, "capabilities")
        assert hasattr(result, "endpoint")
        assert hasattr(result, "error_message")
        assert hasattr(result, "latency_ms")
        # endpoint should be set
        assert result.endpoint is not None
