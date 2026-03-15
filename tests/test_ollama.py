"""Tests for lintgate.nsil.adapters.ollama module."""

from __future__ import annotations

import io
import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from lintgate.nsil.adapters.ollama import (
    OllamaAdapter,
    _iter_jsonl_stream,
)
from lintgate.nsil.runtime_adapter import RuntimeCapabilities

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_jsonl_body(chunks: list[dict[str, Any]], include_done: bool = True) -> bytes:
    """Build a JSONL byte stream from a list of JSON objects."""
    lines = [json.dumps(c) for c in chunks]
    if include_done and (not chunks or not chunks[-1].get("done", False)):
        lines.append(json.dumps({"done": True}))
    return ("\n".join(lines) + "\n").encode("utf-8")


class FakeResponse:
    """Mimics an HTTP response object with .read(n)."""

    def __init__(self, data: bytes) -> None:
        self._stream = io.BytesIO(data)

    def read(self, n: int = -1) -> bytes:
        return self._stream.read(n)


# ===========================================================================
# _iter_jsonl_stream tests
# ===========================================================================


class TestIterJsonlStream:
    def test_yields_response_field(self) -> None:
        body = _make_jsonl_body([{"response": "hello"}])
        result = list(_iter_jsonl_stream(FakeResponse(body)))
        assert result == ["hello"]

    def test_yields_multiple_chunks(self) -> None:
        body = _make_jsonl_body(
            [
                {"response": "a"},
                {"response": "b"},
                {"response": "c"},
            ]
        )
        result = list(_iter_jsonl_stream(FakeResponse(body)))
        assert result == ["a", "b", "c"]

    def test_stops_at_done_true(self) -> None:
        body = (
            json.dumps({"response": "first"})
            + "\n"
            + json.dumps({"response": "last", "done": True})
            + "\n"
            + json.dumps({"response": "after"})
            + "\n"
        ).encode("utf-8")
        result = list(_iter_jsonl_stream(FakeResponse(body)))
        assert result == ["first", "last"]

    def test_skips_empty_lines(self) -> None:
        raw = (
            json.dumps({"response": "a"})
            + "\n"
            + "\n"
            + json.dumps({"response": "b", "done": True})
            + "\n"
        ).encode("utf-8")
        result = list(_iter_jsonl_stream(FakeResponse(raw)))
        assert result == ["a", "b"]

    def test_skips_invalid_json(self) -> None:
        raw = ("{bad-json\n" + json.dumps({"response": "ok", "done": True}) + "\n").encode("utf-8")
        result = list(_iter_jsonl_stream(FakeResponse(raw)))
        assert result == ["ok"]

    def test_skips_lines_without_response(self) -> None:
        body = _make_jsonl_body(
            [
                {"model": "llama2"},
                {"response": "text"},
            ]
        )
        result = list(_iter_jsonl_stream(FakeResponse(body)))
        assert result == ["text"]

    def test_empty_stream(self) -> None:
        result = list(_iter_jsonl_stream(FakeResponse(b"")))
        assert result == []

    def test_done_false_continues(self) -> None:
        body = (
            json.dumps({"response": "a", "done": False})
            + "\n"
            + json.dumps({"response": "b", "done": True})
            + "\n"
        ).encode("utf-8")
        result = list(_iter_jsonl_stream(FakeResponse(body)))
        assert result == ["a", "b"]


# ===========================================================================
# OllamaAdapter.get_capabilities
# ===========================================================================


class TestGetCapabilities:
    def test_returns_runtime_capabilities(self) -> None:
        adapter = OllamaAdapter()
        caps = adapter.get_capabilities()
        assert isinstance(caps, RuntimeCapabilities)

    def test_state_injection_supported(self) -> None:
        caps = OllamaAdapter().get_capabilities()
        assert caps.supports_state_injection is True

    def test_streaming_hooks_supported(self) -> None:
        caps = OllamaAdapter().get_capabilities()
        assert caps.supports_streaming_hooks is True

    def test_grammar_constraints_not_supported(self) -> None:
        caps = OllamaAdapter().get_capabilities()
        assert caps.supports_grammar_constraints is False

    def test_logit_processors_not_supported(self) -> None:
        caps = OllamaAdapter().get_capabilities()
        assert caps.supports_logit_processors is False

    def test_max_context_tokens(self) -> None:
        caps = OllamaAdapter().get_capabilities()
        assert caps.max_context_tokens == 8192

    def test_api_protocol(self) -> None:
        caps = OllamaAdapter().get_capabilities()
        assert caps.api_protocol == "ollama"


# ===========================================================================
# OllamaAdapter.inject_state
# ===========================================================================


class TestInjectState:
    def test_returns_true(self) -> None:
        adapter = OllamaAdapter()
        result = adapter.inject_state({"gate_status": "green"})
        assert result is True

    def test_copies_snapshot(self) -> None:
        adapter = OllamaAdapter()
        snap = {"gate_status": "red"}
        adapter.inject_state(snap)
        snap["gate_status"] = "changed"
        assert adapter._injected_state["gate_status"] == "red"

    def test_overwrites_previous_state(self) -> None:
        adapter = OllamaAdapter()
        adapter.inject_state({"a": 1})
        adapter.inject_state({"b": 2})
        assert adapter._injected_state == {"b": 2}


# ===========================================================================
# OllamaAdapter.register_action_hook
# ===========================================================================


class TestRegisterActionHook:
    def test_appends_callback(self) -> None:
        adapter = OllamaAdapter()
        cb = lambda a, d: None  # noqa: E731
        adapter.register_action_hook(cb)
        assert len(adapter._action_hooks) == 1
        assert adapter._action_hooks[0] is cb

    def test_multiple_hooks(self) -> None:
        adapter = OllamaAdapter()
        cb1 = lambda a, d: None  # noqa: E731
        cb2 = lambda a, d: None  # noqa: E731
        adapter.register_action_hook(cb1)
        adapter.register_action_hook(cb2)
        assert len(adapter._action_hooks) == 2


# ===========================================================================
# OllamaAdapter._make_prompt_with_state
# ===========================================================================


class TestMakePromptWithState:
    def test_no_state_returns_original(self) -> None:
        adapter = OllamaAdapter()
        assert adapter._make_prompt_with_state("hello") == "hello"

    def test_empty_state_returns_original(self) -> None:
        adapter = OllamaAdapter()
        adapter.inject_state({})
        assert adapter._make_prompt_with_state("hello") == "hello"

    def test_gate_status(self) -> None:
        adapter = OllamaAdapter()
        adapter.inject_state({"gate_status": "red"})
        result = adapter._make_prompt_with_state("test")
        assert result.startswith("[NSIL State: Gate Status: red]")
        assert result.endswith("\n\ntest")

    def test_risk_level(self) -> None:
        adapter = OllamaAdapter()
        adapter.inject_state({"risk_level": "high"})
        result = adapter._make_prompt_with_state("test")
        assert "Risk Level: high" in result

    def test_blocking_findings_list(self) -> None:
        adapter = OllamaAdapter()
        adapter.inject_state({"blocking_findings": ["F1", "F2", "F3", "F4"]})
        result = adapter._make_prompt_with_state("test")
        assert "Blocking: F1, F2, F3" in result
        assert "F4" not in result

    def test_blocking_findings_string(self) -> None:
        adapter = OllamaAdapter()
        adapter.inject_state({"blocking_findings": "single"})
        result = adapter._make_prompt_with_state("test")
        assert "Blocking: single" in result

    def test_active_constraints_list(self) -> None:
        adapter = OllamaAdapter()
        adapter.inject_state({"active_constraints": ["C1", "C2"]})
        result = adapter._make_prompt_with_state("test")
        assert "Constraints: C1, C2" in result

    def test_active_constraints_string(self) -> None:
        adapter = OllamaAdapter()
        adapter.inject_state({"active_constraints": "one"})
        result = adapter._make_prompt_with_state("test")
        assert "Constraints: one" in result

    def test_all_state_fields(self) -> None:
        adapter = OllamaAdapter()
        adapter.inject_state(
            {
                "gate_status": "yellow",
                "risk_level": "medium",
                "blocking_findings": ["X"],
                "active_constraints": ["Y"],
            }
        )
        result = adapter._make_prompt_with_state("p")
        assert "[NSIL State:" in result
        assert "Gate Status: yellow" in result
        assert "Risk Level: medium" in result
        assert "Blocking: X" in result
        assert "Constraints: Y" in result
        assert result.endswith("\n\np")

    def test_pipe_separator_between_parts(self) -> None:
        adapter = OllamaAdapter()
        adapter.inject_state({"gate_status": "red", "risk_level": "high"})
        result = adapter._make_prompt_with_state("x")
        assert "Gate Status: red | Risk Level: high" in result


# ===========================================================================
# OllamaAdapter._build_payload
# ===========================================================================


class TestBuildPayload:
    def test_default_payload(self) -> None:
        adapter = OllamaAdapter(model="llama2")
        payload = adapter._build_payload("hello")
        assert payload["model"] == "llama2"
        assert payload["prompt"] == "hello"
        assert payload["stream"] is True
        assert payload["temperature"] == 0.7

    def test_override_model_and_params(self) -> None:
        adapter = OllamaAdapter(model="default")
        payload = adapter._build_payload("hi", model="custom", temperature=0.1, stream=False)
        assert payload["model"] == "custom"
        assert payload["temperature"] == 0.1
        assert payload["stream"] is False

    def test_includes_options_when_provided(self) -> None:
        adapter = OllamaAdapter()
        payload = adapter._build_payload("test", options={"num_ctx": 4096})
        assert payload["options"] == {"num_ctx": 4096}

    def test_no_options_key_when_not_provided(self) -> None:
        adapter = OllamaAdapter()
        payload = adapter._build_payload("test")
        assert "options" not in payload

    def test_prompt_includes_state(self) -> None:
        adapter = OllamaAdapter()
        adapter.inject_state({"gate_status": "green"})
        payload = adapter._build_payload("ask something")
        assert "[NSIL State:" in payload["prompt"]
        assert "ask something" in payload["prompt"]


# ===========================================================================
# OllamaAdapter._make_request (static method)
# ===========================================================================


class TestMakeRequest:
    def test_returns_request_object(self) -> None:
        import urllib.request

        req = OllamaAdapter._make_request(
            "http://localhost:11434/api/generate",
            {"model": "llama2", "prompt": "test"},
        )
        assert isinstance(req, urllib.request.Request)
        assert req.full_url == "http://localhost:11434/api/generate"
        assert req.get_method() == "POST"
        assert req.get_header("Content-type") == "application/json"

    def test_payload_is_json_encoded(self) -> None:
        payload = {"model": "m", "prompt": "p"}
        req = OllamaAdapter._make_request("http://localhost:11434/test", payload)
        assert json.loads(req.data.decode("utf-8")) == payload

    def test_no_accept_header(self) -> None:
        req = OllamaAdapter._make_request("http://localhost:11434/test", {})
        # Ollama adapter does not set Accept header (unlike vLLM)
        assert req.get_header("Accept") is None


# ===========================================================================
# OllamaAdapter.get_generation_stream
# ===========================================================================


class TestGetGenerationStream:
    @patch("urllib.request.urlopen")
    def test_streams_jsonl_chunks(self, mock_urlopen: MagicMock) -> None:
        body = _make_jsonl_body(
            [
                {"response": "hello"},
                {"response": " world"},
            ]
        )
        mock_response = FakeResponse(body)
        mock_response.close = lambda: None  # type: ignore[attr-defined]
        mock_urlopen.return_value.__enter__ = lambda s: mock_response
        mock_urlopen.return_value.__exit__ = lambda s, *a: None

        adapter = OllamaAdapter()
        result = list(adapter.get_generation_stream("test prompt"))
        assert result == ["hello", " world"]

    @patch("urllib.request.urlopen")
    def test_url_error_yields_error_message(self, mock_urlopen: MagicMock) -> None:
        import urllib.error

        mock_urlopen.side_effect = urllib.error.URLError("connection refused")
        adapter = OllamaAdapter()
        result = list(adapter.get_generation_stream("test"))
        assert len(result) == 1
        assert "[Error: Ollama unavailable" in result[0]
        assert "connection refused" in result[0]

    @patch("urllib.request.urlopen")
    def test_timeout_yields_error_message(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.side_effect = TimeoutError()
        adapter = OllamaAdapter()
        result = list(adapter.get_generation_stream("test"))
        assert result == ["[Error: Ollama request timed out]"]

    @patch("urllib.request.urlopen")
    def test_generic_exception_yields_error(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.side_effect = RuntimeError("boom")
        adapter = OllamaAdapter()
        result = list(adapter.get_generation_stream("test"))
        assert result == ["[Error: boom]"]

    def test_uses_correct_url(self) -> None:
        adapter = OllamaAdapter(endpoint="http://myhost:5000")
        with (
            patch.object(adapter, "_make_request") as mock_req,
            patch("urllib.request.urlopen", side_effect=TimeoutError()),
        ):
            list(adapter.get_generation_stream("prompt"))
            mock_req.assert_called_once()
            url_arg = mock_req.call_args[0][0]
            assert url_arg == "http://myhost:5000/api/generate"


# ===========================================================================
# OllamaAdapter.get_generation_guarded
# ===========================================================================


class TestGetGenerationGuarded:
    @patch("lintgate.nsil.adapters.ollama.OllamaAdapter.get_generation_stream")
    def test_wraps_stream_with_guard(self, mock_stream: MagicMock) -> None:
        mock_stream.return_value = iter(["chunk1", "chunk2"])

        mock_guard_instance = MagicMock()
        mock_guard_instance.guard_stream.return_value = iter(["guarded1"])

        mock_streaming_module = MagicMock()
        mock_streaming_module.StreamingGuard.return_value = mock_guard_instance

        import sys

        sys.modules["lintgate.nsil.adapters.streaming"] = mock_streaming_module
        try:
            adapter = OllamaAdapter()
            result = list(adapter.get_generation_guarded("test", project_root="/tmp"))
            assert result == ["guarded1"]
            mock_guard_instance.guard_stream.assert_called_once()
        finally:
            sys.modules.pop("lintgate.nsil.adapters.streaming", None)


# ===========================================================================
# OllamaAdapter.apply_grammar_constraint
# ===========================================================================


class TestApplyGrammarConstraint:
    def test_always_returns_false(self) -> None:
        adapter = OllamaAdapter()
        assert adapter.apply_grammar_constraint({"gbnf": "rule"}) is False

    def test_returns_false_with_empty_dict(self) -> None:
        adapter = OllamaAdapter()
        assert adapter.apply_grammar_constraint({}) is False

    def test_returns_false_with_regex(self) -> None:
        adapter = OllamaAdapter()
        assert adapter.apply_grammar_constraint({"regex": r"\d+"}) is False


# ===========================================================================
# OllamaAdapter.is_available
# ===========================================================================


class TestIsAvailable:
    @patch("socket.socket")
    def test_available_when_connect_succeeds(self, mock_socket_cls: MagicMock) -> None:
        mock_sock = MagicMock()
        mock_sock.connect_ex.return_value = 0
        mock_socket_cls.return_value = mock_sock

        adapter = OllamaAdapter(endpoint="http://localhost:11434")
        assert adapter.is_available() is True
        mock_sock.connect_ex.assert_called_once_with(("localhost", 11434))
        mock_sock.close.assert_called_once()

    @patch("socket.socket")
    def test_unavailable_when_connect_fails(self, mock_socket_cls: MagicMock) -> None:
        mock_sock = MagicMock()
        mock_sock.connect_ex.return_value = 1
        mock_socket_cls.return_value = mock_sock

        adapter = OllamaAdapter()
        assert adapter.is_available() is False

    @patch("socket.socket")
    def test_unavailable_on_os_error(self, mock_socket_cls: MagicMock) -> None:
        mock_socket_cls.side_effect = OSError("fail")
        adapter = OllamaAdapter()
        assert adapter.is_available() is False

    def test_invalid_endpoint_rejected(self) -> None:
        with pytest.raises(ValueError, match="http"):
            OllamaAdapter(endpoint="no-port-here")

    @patch("socket.socket")
    def test_custom_port(self, mock_socket_cls: MagicMock) -> None:
        mock_sock = MagicMock()
        mock_sock.connect_ex.return_value = 0
        mock_socket_cls.return_value = mock_sock

        adapter = OllamaAdapter(endpoint="http://localhost:9999")
        adapter.is_available()
        mock_sock.connect_ex.assert_called_once_with(("localhost", 9999))


# ===========================================================================
# OllamaAdapter dataclass defaults
# ===========================================================================


class TestOllamaAdapterDefaults:
    def test_default_endpoint(self) -> None:
        adapter = OllamaAdapter()
        assert adapter.endpoint == "http://localhost:11434"

    def test_default_model(self) -> None:
        adapter = OllamaAdapter()
        assert adapter.model == "llama2"

    def test_custom_endpoint_and_model(self) -> None:
        adapter = OllamaAdapter(endpoint="http://gpu:5000", model="mistral")
        assert adapter.endpoint == "http://gpu:5000"
        assert adapter.model == "mistral"

    def test_hooks_and_state_are_isolated(self) -> None:
        a1 = OllamaAdapter()
        a2 = OllamaAdapter()
        a1._action_hooks.append(lambda a, d: None)
        a1._injected_state["key"] = "val"
        assert len(a2._action_hooks) == 0
        assert a2._injected_state == {}
