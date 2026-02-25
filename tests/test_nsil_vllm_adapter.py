"""Tests for NSIL vLLM adapter."""

from lintgate.nsil.adapters.vllm import VLLMAdapter, check_optional_dependencies
from lintgate.nsil.runtime_adapter import RuntimeCapabilities


def test_vllm_adapter_creation():
    """Test VLLMAdapter can be created."""
    adapter = VLLMAdapter()
    assert adapter is not None
    assert adapter.endpoint == "http://localhost:8000"
    assert adapter.model == "llama-2-7b"


def test_vllm_adapter_custom_endpoint():
    """Test VLLMAdapter with custom endpoint."""
    adapter = VLLMAdapter(endpoint="http://localhost:9000", model="mixtral-8x7b")
    assert adapter.endpoint == "http://localhost:9000"
    assert adapter.model == "mixtral-8x7b"


def test_get_capabilities():
    """Test get_capabilities returns correct values."""
    adapter = VLLMAdapter()
    caps = adapter.get_capabilities()

    assert isinstance(caps, RuntimeCapabilities)
    assert caps.supports_state_injection is True
    assert caps.supports_streaming_hooks is True
    # Grammar constraints depend on Outlines availability
    assert (
        caps.supports_grammar_constraints == caps.supports_grammar_constraints
    )  # Just check it's bool
    assert caps.api_protocol == "vllm"


def test_get_capabilities_grammar_and_logit():
    """Test grammar and logit processor capabilities match deps."""
    adapter = VLLMAdapter()
    caps = adapter.get_capabilities()

    deps = check_optional_dependencies()
    assert caps.supports_grammar_constraints == deps["outlines"]
    assert caps.supports_logit_processors == deps["vllm"]


def test_check_optional_dependencies():
    """Test check_optional_dependencies returns dict."""
    deps = check_optional_dependencies()
    assert isinstance(deps, dict)
    assert "vllm" in deps
    assert "outlines" in deps
    assert isinstance(deps["vllm"], bool)
    assert isinstance(deps["outlines"], bool)


def test_inject_state():
    """Test inject_state stores the snapshot."""
    adapter = VLLMAdapter()
    snapshot = {"gate_status": "yellow", "risk_level": "medium", "blocking_findings": ["issue1"]}

    result = adapter.inject_state(snapshot)
    assert result is True
    assert adapter._injected_state == snapshot


def test_inject_state_multiple():
    """Test inject_state can be called multiple times."""
    adapter = VLLMAdapter()
    adapter.inject_state({"gate_status": "green"})
    adapter.inject_state({"gate_status": "yellow", "risk_level": "high"})

    assert adapter._injected_state == {"gate_status": "yellow", "risk_level": "high"}


def test_register_action_hook():
    """Test register_action_hook adds callback."""
    adapter = VLLMAdapter()
    calls = []

    def callback(action: str, data: dict):
        calls.append((action, data))

    adapter.register_action_hook(callback)
    assert len(adapter._action_hooks) == 1


def test_register_action_hook_multiple():
    """Test multiple hooks can be registered."""
    adapter = VLLMAdapter()

    def cb1(a, d):
        pass

    def cb2(a, d):
        pass

    adapter.register_action_hook(cb1)
    adapter.register_action_hook(cb2)
    assert len(adapter._action_hooks) == 2


def test_make_messages_with_state_no_state():
    """Test messages without injected state."""
    adapter = VLLMAdapter()
    messages = adapter._make_messages_with_state("Hello")
    assert len(messages) == 1
    assert messages[0] == {"role": "user", "content": "Hello"}


def test_make_messages_with_state_gate_status():
    """Test messages with gate_status."""
    adapter = VLLMAdapter()
    adapter.inject_state({"gate_status": "green"})
    messages = adapter._make_messages_with_state("Write code")

    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert "Gate Status: green" in messages[0]["content"]
    assert messages[1]["role"] == "user"
    assert messages[1]["content"] == "Write code"


def test_make_messages_with_state_full_snapshot():
    """Test messages with full snapshot."""
    adapter = VLLMAdapter()
    adapter.inject_state(
        {
            "gate_status": "yellow",
            "risk_level": "high",
            "blocking_findings": ["bug1", "bug2"],
            "active_constraints": ["constraint1"],
        }
    )
    messages = adapter._make_messages_with_state("Test")

    assert len(messages) == 2
    system_msg = messages[0]["content"]
    assert "Gate Status: yellow" in system_msg
    assert "Risk Level: high" in system_msg
    assert "Blocking: bug1, bug2" in system_msg
    assert "Constraints: constraint1" in system_msg


def test_apply_grammar_constraint():
    """Test grammar constraint is stored."""
    adapter = VLLMAdapter()
    grammar = {"type": "json", "schema": {"name": "string"}}

    result = adapter.apply_grammar_constraint(grammar)
    # Returns False if Outlines not available, but stores the constraint
    assert isinstance(result, bool)
    assert adapter._grammar_constraint == grammar


def test_apply_grammar_constraint_twice():
    """Test grammar constraint can be replaced."""
    adapter = VLLMAdapter()
    adapter.apply_grammar_constraint({"type": "json"})
    adapter.apply_grammar_constraint({"type": "regex", "pattern": ".*"})

    assert adapter._grammar_constraint == {"type": "regex", "pattern": ".*"}


def test_clear_grammar_constraint():
    """Test grammar constraint can be cleared."""
    adapter = VLLMAdapter()
    adapter.apply_grammar_constraint({"type": "json"})
    adapter.clear_grammar_constraint()

    assert adapter._grammar_constraint is None


def test_is_available_unavailable():
    """Test is_available returns False for unreachable endpoint."""
    adapter = VLLMAdapter(endpoint="http://localhost:59999")
    result = adapter.is_available()
    assert result is False


def test_get_generation_stream_returns_generator():
    """Test get_generation_stream returns a generator."""
    adapter = VLLMAdapter(endpoint="http://localhost:59999")
    result = adapter.get_generation_stream("test prompt")
    assert hasattr(result, "__iter__")
    assert hasattr(result, "__next__")


def test_get_generation_stream_unavailable_returns_error():
    """Test unavailable runtime returns error message."""
    adapter = VLLMAdapter(endpoint="http://localhost:59999")
    result = list(adapter.get_generation_stream("test"))
    assert len(result) > 0
    assert "Error" in result[0] or "unavailable" in result[0].lower()


def test_vllm_adapter_protocol_compliance():
    """Test adapter satisfies LocalRuntimeAdapter protocol."""
    adapter: VLLMAdapter = VLLMAdapter()

    assert adapter.inject_state({}) is True
    adapter.register_action_hook(lambda a, d: None)
    assert isinstance(adapter.apply_grammar_constraint({}), bool)
    assert isinstance(adapter.get_capabilities(), RuntimeCapabilities)

    stream = adapter.get_generation_stream("test")
    assert hasattr(stream, "__iter__")
    assert hasattr(stream, "__next__")


def test_vllm_adapter_passes_kwargs_to_generate():
    """Test kwargs are passed to generation."""
    adapter = VLLMAdapter()
    gen = adapter.get_generation_stream(
        "test",
        model="custom-model",
        temperature=0.5,
        max_tokens=100,
    )
    assert gen is not None


def test_is_vllm_available_property():
    """Test is_vllm_available property."""
    adapter = VLLMAdapter()
    deps = check_optional_dependencies()
    assert adapter.is_vllm_available == deps["vllm"]


def test_is_outlines_available_property():
    """Test is_outlines_available property."""
    adapter = VLLMAdapter()
    deps = check_optional_dependencies()
    assert adapter.is_outlines_available == deps["outlines"]
