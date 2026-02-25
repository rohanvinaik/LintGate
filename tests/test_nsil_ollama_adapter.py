"""Tests for NSIL Ollama adapter."""

from lintgate.nsil.adapters.ollama import OllamaAdapter
from lintgate.nsil.runtime_adapter import RuntimeCapabilities


def test_ollama_adapter_creation():
    """Test OllamaAdapter can be created."""
    adapter = OllamaAdapter()
    assert adapter is not None
    assert adapter.endpoint == "http://localhost:11434"
    assert adapter.model == "llama2"


def test_ollama_adapter_custom_endpoint():
    """Test OllamaAdapter with custom endpoint."""
    adapter = OllamaAdapter(endpoint="http://localhost:8080", model="codellama")
    assert adapter.endpoint == "http://localhost:8080"
    assert adapter.model == "codellama"


def test_get_capabilities():
    """Test get_capabilities returns correct values."""
    adapter = OllamaAdapter()
    caps = adapter.get_capabilities()

    assert isinstance(caps, RuntimeCapabilities)
    assert caps.supports_state_injection is True
    assert caps.supports_streaming_hooks is True
    assert caps.supports_grammar_constraints is False
    assert caps.supports_logit_processors is False
    assert caps.max_context_tokens == 8192
    assert caps.api_protocol == "ollama"


def test_inject_state():
    """Test inject_state stores the snapshot."""
    adapter = OllamaAdapter()
    snapshot = {"gate_status": "yellow", "risk_level": "medium", "blocking_findings": ["issue1"]}

    result = adapter.inject_state(snapshot)
    assert result is True
    assert adapter._injected_state == snapshot


def test_inject_state_multiple():
    """Test inject_state can be called multiple times."""
    adapter = OllamaAdapter()
    adapter.inject_state({"gate_status": "green"})
    adapter.inject_state({"gate_status": "yellow", "risk_level": "high"})

    # Last injection wins
    assert adapter._injected_state == {"gate_status": "yellow", "risk_level": "high"}


def test_register_action_hook():
    """Test register_action_hook adds callback."""
    adapter = OllamaAdapter()
    calls = []

    def callback(action: str, data: dict):
        calls.append((action, data))

    adapter.register_action_hook(callback)
    assert len(adapter._action_hooks) == 1


def test_register_action_hook_multiple():
    """Test multiple hooks can be registered."""
    adapter = OllamaAdapter()

    def cb1(a, d):
        pass

    def cb2(a, d):
        pass

    adapter.register_action_hook(cb1)
    adapter.register_action_hook(cb2)
    assert len(adapter._action_hooks) == 2


def test_make_prompt_with_state_no_state():
    """Test prompt without injected state."""
    adapter = OllamaAdapter()
    prompt = "Hello, world!"
    result = adapter._make_prompt_with_state(prompt)
    assert result == prompt


def test_make_prompt_with_state_gate_status():
    """Test prompt with gate_status."""
    adapter = OllamaAdapter()
    adapter.inject_state({"gate_status": "green"})
    prompt = "Write code"
    result = adapter._make_prompt_with_state(prompt)
    assert "Gate Status: green" in result
    assert prompt in result


def test_make_prompt_with_state_full_snapshot():
    """Test prompt with full snapshot."""
    adapter = OllamaAdapter()
    adapter.inject_state(
        {
            "gate_status": "yellow",
            "risk_level": "high",
            "blocking_findings": ["bug1", "bug2"],
            "active_constraints": ["constraint1"],
        }
    )
    prompt = "Test"
    result = adapter._make_prompt_with_state(prompt)

    assert "Gate Status: yellow" in result
    assert "Risk Level: high" in result
    assert "Blocking: bug1, bug2" in result
    assert "Constraints: constraint1" in result


def test_apply_grammar_constraint():
    """Test grammar constraint returns False (not supported)."""
    adapter = OllamaAdapter()
    result = adapter.apply_grammar_constraint({"type": "json"})
    assert result is False


def test_is_available_unavailable():
    """Test is_available returns False for unreachable endpoint."""
    adapter = OllamaAdapter(endpoint="http://localhost:59999")
    # This should return False (or may timeout)
    result = adapter.is_available()
    assert result is False


def test_get_generation_stream_returns_generator():
    """Test get_generation_stream returns a generator."""
    adapter = OllamaAdapter(endpoint="http://localhost:59999")  # Unavailable endpoint
    result = adapter.get_generation_stream("test prompt")
    assert hasattr(result, "__iter__")
    assert hasattr(result, "__next__")


def test_get_generation_stream_unavailable_returns_error():
    """Test unavailable runtime returns error message."""
    adapter = OllamaAdapter(endpoint="http://localhost:59999")
    result = list(adapter.get_generation_stream("test"))
    # Should contain error message
    assert len(result) > 0
    assert "Error" in result[0] or "unavailable" in result[0].lower()


def test_ollama_adapter_protocol_compliance():
    """Test adapter satisfies LocalRuntimeAdapter protocol."""
    adapter: OllamaAdapter = OllamaAdapter()

    # Test all protocol methods exist and work
    assert adapter.inject_state({}) is True
    adapter.register_action_hook(lambda a, d: None)
    assert adapter.apply_grammar_constraint({}) is False
    assert isinstance(adapter.get_capabilities(), RuntimeCapabilities)

    # Stream returns generator
    stream = adapter.get_generation_stream("test")
    assert hasattr(stream, "__iter__")
    assert hasattr(stream, "__next__")


def test_ollama_adapter_passes_kwargs_to_generate():
    """Test kwargs are passed to generation."""
    # We can't actually test generation without a running Ollama,
    # but we can verify the adapter accepts the parameters
    adapter = OllamaAdapter()
    # This should not raise
    gen = adapter.get_generation_stream(
        "test",
        model="custom-model",
        temperature=0.5,
        stream=True,
    )
    assert gen is not None
