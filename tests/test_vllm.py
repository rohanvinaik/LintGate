"""Tests for lintgate.nsil.adapters.vllm module."""

from __future__ import annotations

import io
import json
import socket
from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from lintgate.nsil.adapters.vllm import (
    VLLMAdapter,
    _iter_sse_stream,
    check_optional_dependencies,
)
from lintgate.nsil.grammar_compiler import PolicyGrammar
from lintgate.nsil.runtime_adapter import RuntimeCapabilities


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_sse_body(chunks: list[dict[str, Any]], include_done: bool = True) -> bytes:
    """Build an SSE byte stream from a list of JSON data objects."""
    lines = []
    for chunk in chunks:
        lines.append(f"data: {json.dumps(chunk)}")
    if include_done:
        lines.append("data: [DONE]")
    return ("\n".join(lines) + "\n").encode("utf-8")


class FakeResponse:
    """Mimics an HTTP response object with .read(n) that returns data then b''."""

    def __init__(self, data: bytes) -> None:
        self._stream = io.BytesIO(data)

    def read(self, n: int = -1) -> bytes:
        return self._stream.read(n)


# ===========================================================================
# _iter_sse_stream tests
# ===========================================================================


class TestIterSseStream:
    def test_yields_content_from_valid_sse(self) -> None:
        body = _make_sse_body(
            [{"choices": [{"delta": {"content": "hello"}}]}],
        )
        result = list(_iter_sse_stream(FakeResponse(body)))
        assert result == ["hello"]

    def test_yields_multiple_chunks(self) -> None:
        body = _make_sse_body(
            [
                {"choices": [{"delta": {"content": "a"}}]},
                {"choices": [{"delta": {"content": "b"}}]},
                {"choices": [{"delta": {"content": "c"}}]},
            ],
        )
        result = list(_iter_sse_stream(FakeResponse(body)))
        assert result == ["a", "b", "c"]

    def test_skips_non_data_lines(self) -> None:
        raw = b"event: ping\ndata: " + json.dumps(
            {"choices": [{"delta": {"content": "ok"}}]}
        ).encode() + b"\ndata: [DONE]\n"
        result = list(_iter_sse_stream(FakeResponse(raw)))
        assert result == ["ok"]

    def test_skips_missing_content_key(self) -> None:
        body = _make_sse_body(
            [
                {"choices": [{"delta": {"role": "assistant"}}]},
                {"choices": [{"delta": {"content": "real"}}]},
            ],
        )
        result = list(_iter_sse_stream(FakeResponse(body)))
        assert result == ["real"]

    def test_skips_empty_choices(self) -> None:
        body = _make_sse_body([{"choices": []}])
        result = list(_iter_sse_stream(FakeResponse(body)))
        assert result == []

    def test_skips_no_choices_key(self) -> None:
        body = _make_sse_body([{"object": "chat.completion.chunk"}])
        result = list(_iter_sse_stream(FakeResponse(body)))
        assert result == []

    def test_stops_at_done_marker(self) -> None:
        raw = (
            b"data: " + json.dumps({"choices": [{"delta": {"content": "first"}}]}).encode()
            + b"\ndata: [DONE]\n"
            + b"data: " + json.dumps({"choices": [{"delta": {"content": "after"}}]}).encode()
            + b"\n"
        )
        result = list(_iter_sse_stream(FakeResponse(raw)))
        assert result == ["first"]

    def test_handles_invalid_json_gracefully(self) -> None:
        raw = b"data: {invalid-json\ndata: [DONE]\n"
        result = list(_iter_sse_stream(FakeResponse(raw)))
        assert result == []

    def test_empty_stream(self) -> None:
        result = list(_iter_sse_stream(FakeResponse(b"")))
        assert result == []


# ===========================================================================
# VLLMAdapter.get_capabilities
# ===========================================================================


class TestGetCapabilities:
    def test_returns_runtime_capabilities(self) -> None:
        adapter = VLLMAdapter()
        caps = adapter.get_capabilities()
        assert isinstance(caps, RuntimeCapabilities)
        assert caps.supports_state_injection is True
        assert caps.supports_streaming_hooks is True
        assert caps.max_context_tokens == 32768
        assert caps.api_protocol == "vllm"

    def test_grammar_constraints_depend_on_outlines(self) -> None:
        adapter = VLLMAdapter()
        caps = adapter.get_capabilities()
        # In test env outlines is not installed, so should be False
        import lintgate.nsil.adapters.vllm as vllm_mod

        assert caps.supports_grammar_constraints == vllm_mod._OUTLINES_AVAILABLE

    def test_logit_processors_depend_on_vllm(self) -> None:
        adapter = VLLMAdapter()
        caps = adapter.get_capabilities()
        import lintgate.nsil.adapters.vllm as vllm_mod

        assert caps.supports_logit_processors == vllm_mod._VLLM_AVAILABLE


# ===========================================================================
# VLLMAdapter.is_vllm_available / is_outlines_available properties
# ===========================================================================


class TestAvailabilityProperties:
    def test_is_vllm_available_returns_bool(self) -> None:
        adapter = VLLMAdapter()
        assert isinstance(adapter.is_vllm_available, bool)

    def test_is_outlines_available_returns_bool(self) -> None:
        adapter = VLLMAdapter()
        assert isinstance(adapter.is_outlines_available, bool)


# ===========================================================================
# VLLMAdapter.inject_state
# ===========================================================================


class TestInjectState:
    def test_returns_true(self) -> None:
        adapter = VLLMAdapter()
        result = adapter.inject_state({"gate_status": "green"})
        assert result is True

    def test_copies_snapshot(self) -> None:
        adapter = VLLMAdapter()
        snap = {"gate_status": "red"}
        adapter.inject_state(snap)
        snap["gate_status"] = "green"
        assert adapter._injected_state["gate_status"] == "red"

    def test_overwrites_previous_state(self) -> None:
        adapter = VLLMAdapter()
        adapter.inject_state({"gate_status": "red"})
        adapter.inject_state({"gate_status": "green"})
        assert adapter._injected_state == {"gate_status": "green"}


# ===========================================================================
# VLLMAdapter.register_action_hook
# ===========================================================================


class TestRegisterActionHook:
    def test_appends_callback(self) -> None:
        adapter = VLLMAdapter()
        cb = lambda action, data: None
        adapter.register_action_hook(cb)
        assert len(adapter._action_hooks) == 1
        assert adapter._action_hooks[0] is cb

    def test_multiple_hooks(self) -> None:
        adapter = VLLMAdapter()
        cb1 = lambda a, d: None
        cb2 = lambda a, d: None
        adapter.register_action_hook(cb1)
        adapter.register_action_hook(cb2)
        assert len(adapter._action_hooks) == 2


# ===========================================================================
# VLLMAdapter._make_messages_with_state
# ===========================================================================


class TestMakeMessagesWithState:
    def test_no_state_returns_user_only(self) -> None:
        adapter = VLLMAdapter()
        msgs = adapter._make_messages_with_state("hello")
        assert msgs == [{"role": "user", "content": "hello"}]

    def test_with_gate_status(self) -> None:
        adapter = VLLMAdapter()
        adapter.inject_state({"gate_status": "red"})
        msgs = adapter._make_messages_with_state("test")
        assert len(msgs) == 2
        assert msgs[0]["role"] == "system"
        assert "Gate Status: red" in msgs[0]["content"]
        assert msgs[1] == {"role": "user", "content": "test"}

    def test_with_risk_level(self) -> None:
        adapter = VLLMAdapter()
        adapter.inject_state({"risk_level": "high"})
        msgs = adapter._make_messages_with_state("test")
        assert "Risk Level: high" in msgs[0]["content"]

    def test_with_blocking_findings_list(self) -> None:
        adapter = VLLMAdapter()
        adapter.inject_state({"blocking_findings": ["F001", "F002", "F003", "F004"]})
        msgs = adapter._make_messages_with_state("test")
        # Truncates to first 3
        assert "Blocking: F001, F002, F003" in msgs[0]["content"]
        assert "F004" not in msgs[0]["content"]

    def test_with_blocking_findings_string(self) -> None:
        adapter = VLLMAdapter()
        adapter.inject_state({"blocking_findings": "single-finding"})
        msgs = adapter._make_messages_with_state("test")
        assert "Blocking: single-finding" in msgs[0]["content"]

    def test_with_active_constraints_list(self) -> None:
        adapter = VLLMAdapter()
        adapter.inject_state({"active_constraints": ["C1", "C2"]})
        msgs = adapter._make_messages_with_state("test")
        assert "Constraints: C1, C2" in msgs[0]["content"]

    def test_with_active_constraints_string(self) -> None:
        adapter = VLLMAdapter()
        adapter.inject_state({"active_constraints": "single"})
        msgs = adapter._make_messages_with_state("test")
        assert "Constraints: single" in msgs[0]["content"]

    def test_all_state_fields(self) -> None:
        adapter = VLLMAdapter()
        adapter.inject_state({
            "gate_status": "yellow",
            "risk_level": "medium",
            "blocking_findings": ["X"],
            "active_constraints": ["Y"],
        })
        msgs = adapter._make_messages_with_state("p")
        system_content = msgs[0]["content"]
        assert "[NSIL State:" in system_content
        assert "Gate Status: yellow" in system_content
        assert "Risk Level: medium" in system_content
        assert "Blocking: X" in system_content
        assert "Constraints: Y" in system_content

    def test_empty_state_dict_no_system_message(self) -> None:
        adapter = VLLMAdapter()
        adapter.inject_state({})
        msgs = adapter._make_messages_with_state("test")
        assert len(msgs) == 1
        assert msgs[0]["role"] == "user"


# ===========================================================================
# VLLMAdapter._build_payload
# ===========================================================================


class TestBuildPayload:
    def test_default_payload(self) -> None:
        adapter = VLLMAdapter(model="test-model")
        payload = adapter._build_payload("hello")
        assert payload["model"] == "test-model"
        assert payload["temperature"] == 0.7
        assert payload["max_tokens"] == 512
        assert payload["stream"] is True
        assert len(payload["messages"]) == 1
        assert payload["messages"][0] == {"role": "user", "content": "hello"}

    def test_override_model_and_params(self) -> None:
        adapter = VLLMAdapter(model="default")
        payload = adapter._build_payload(
            "hi", model="custom", temperature=0.1, max_tokens=100
        )
        assert payload["model"] == "custom"
        assert payload["temperature"] == 0.1
        assert payload["max_tokens"] == 100

    def test_includes_grammar_gbnf(self) -> None:
        adapter = VLLMAdapter()
        adapter._grammar_constraint = {"gbnf": "root ::= 'yes' | 'no'"}
        payload = adapter._build_payload("test")
        assert payload["extra_body"] == {"guided_grammar": "root ::= 'yes' | 'no'"}

    def test_includes_grammar_regex(self) -> None:
        adapter = VLLMAdapter()
        adapter._grammar_constraint = {"regex": r"\d+"}
        payload = adapter._build_payload("test")
        assert payload["extra_body"] == {"guided_regex": r"\d+"}


# ===========================================================================
# VLLMAdapter._apply_grammar_to_payload
# ===========================================================================


class TestApplyGrammarToPayload:
    def test_no_constraint_noop(self) -> None:
        adapter = VLLMAdapter()
        payload: dict[str, Any] = {"model": "test"}
        adapter._apply_grammar_to_payload(payload)
        assert "extra_body" not in payload

    def test_gbnf_constraint(self) -> None:
        adapter = VLLMAdapter()
        adapter._grammar_constraint = {"gbnf": "rule"}
        payload: dict[str, Any] = {}
        adapter._apply_grammar_to_payload(payload)
        assert payload["extra_body"] == {"guided_grammar": "rule"}

    def test_regex_constraint(self) -> None:
        adapter = VLLMAdapter()
        adapter._grammar_constraint = {"regex": r"[a-z]+"}
        payload: dict[str, Any] = {}
        adapter._apply_grammar_to_payload(payload)
        assert payload["extra_body"] == {"guided_regex": r"[a-z]+"}

    def test_gbnf_takes_precedence_over_regex(self) -> None:
        adapter = VLLMAdapter()
        adapter._grammar_constraint = {"gbnf": "rule", "regex": "pat"}
        payload: dict[str, Any] = {}
        adapter._apply_grammar_to_payload(payload)
        assert payload["extra_body"] == {"guided_grammar": "rule"}


# ===========================================================================
# VLLMAdapter._make_request (static method)
# ===========================================================================


class TestMakeRequest:
    def test_returns_request_object(self) -> None:
        import urllib.request

        req = VLLMAdapter._make_request(
            "http://localhost:8000/v1/chat/completions",
            {"model": "test", "messages": []},
        )
        assert isinstance(req, urllib.request.Request)
        assert req.full_url == "http://localhost:8000/v1/chat/completions"
        assert req.get_method() == "POST"
        assert req.get_header("Content-type") == "application/json"
        assert req.get_header("Accept") == "text/event-stream"

    def test_payload_is_json_encoded(self) -> None:
        payload = {"model": "m", "messages": [{"role": "user", "content": "hi"}]}
        req = VLLMAdapter._make_request("http://localhost:8000/test", payload)
        assert json.loads(req.data.decode("utf-8")) == payload


# ===========================================================================
# VLLMAdapter.get_generation_stream
# ===========================================================================


class TestGetGenerationStream:
    @patch("urllib.request.urlopen")
    def test_streams_sse_chunks(self, mock_urlopen: MagicMock) -> None:
        body = _make_sse_body(
            [
                {"choices": [{"delta": {"content": "word1"}}]},
                {"choices": [{"delta": {"content": " word2"}}]},
            ],
        )
        mock_response = FakeResponse(body)
        mock_response.close = lambda: None  # type: ignore[attr-defined]
        mock_urlopen.return_value.__enter__ = lambda s: mock_response
        mock_urlopen.return_value.__exit__ = lambda s, *a: None

        adapter = VLLMAdapter(endpoint="http://localhost:9000")
        result = list(adapter.get_generation_stream("test prompt"))
        assert result == ["word1", " word2"]

    @patch("urllib.request.urlopen")
    def test_url_error_yields_error_message(self, mock_urlopen: MagicMock) -> None:
        import urllib.error

        mock_urlopen.side_effect = urllib.error.URLError("connection refused")
        adapter = VLLMAdapter()
        result = list(adapter.get_generation_stream("test"))
        assert len(result) == 1
        assert "[Error: vLLM unavailable" in result[0]
        assert "connection refused" in result[0]

    @patch("urllib.request.urlopen")
    def test_timeout_yields_error_message(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.side_effect = TimeoutError()
        adapter = VLLMAdapter()
        result = list(adapter.get_generation_stream("test"))
        assert result == ["[Error: vLLM request timed out]"]

    @patch("urllib.request.urlopen")
    def test_generic_exception_yields_error(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.side_effect = RuntimeError("boom")
        adapter = VLLMAdapter()
        result = list(adapter.get_generation_stream("test"))
        assert result == ["[Error: boom]"]

    def test_uses_correct_url(self) -> None:
        adapter = VLLMAdapter(endpoint="http://myhost:5000")
        with patch.object(adapter, "_make_request") as mock_req, \
             patch("urllib.request.urlopen", side_effect=TimeoutError()):
            adapter_gen = adapter.get_generation_stream("prompt")
            list(adapter_gen)
            mock_req.assert_called_once()
            url_arg = mock_req.call_args[0][0]
            assert url_arg == "http://myhost:5000/v1/chat/completions"


# ===========================================================================
# VLLMAdapter.get_generation_guarded
# ===========================================================================


class TestGetGenerationGuarded:
    @patch("lintgate.nsil.adapters.vllm.VLLMAdapter.get_generation_stream")
    def test_wraps_stream_with_guard(self, mock_stream: MagicMock) -> None:
        mock_stream.return_value = iter(["chunk1", "chunk2"])

        mock_guard_instance = MagicMock()
        mock_guard_instance.guard_stream.return_value = iter(["guarded1"])

        mock_streaming_module = MagicMock()
        mock_streaming_module.StreamingGuard.return_value = mock_guard_instance

        import sys

        sys.modules["lintgate.nsil.adapters.streaming"] = mock_streaming_module
        try:
            adapter = VLLMAdapter()
            result = list(adapter.get_generation_guarded("test", project_root="/tmp"))
            assert result == ["guarded1"]
            mock_guard_instance.guard_stream.assert_called_once()
        finally:
            sys.modules.pop("lintgate.nsil.adapters.streaming", None)


# ===========================================================================
# VLLMAdapter.apply_grammar_constraint
# ===========================================================================


class TestApplyGrammarConstraint:
    def test_dict_input_stores_constraint(self) -> None:
        adapter = VLLMAdapter()
        adapter.apply_grammar_constraint({"gbnf": "rule"})
        assert adapter._grammar_constraint == {"gbnf": "rule"}

    def test_dict_returns_outlines_availability(self) -> None:
        import lintgate.nsil.adapters.vllm as vllm_mod

        adapter = VLLMAdapter()
        result = adapter.apply_grammar_constraint({"gbnf": "rule"})
        assert result == vllm_mod._OUTLINES_AVAILABLE

    def test_policy_grammar_with_regex_only(self) -> None:
        pg = PolicyGrammar(regex_pattern=r"\d+", explanation="digits only")
        adapter = VLLMAdapter()
        result = adapter.apply_grammar_constraint(pg)
        assert result is True
        assert adapter._grammar_constraint == {
            "regex": r"\d+",
            "explanation": "digits only",
        }

    def test_policy_grammar_empty_returns_true_clears(self) -> None:
        adapter = VLLMAdapter()
        adapter._grammar_constraint = {"old": "value"}
        pg = PolicyGrammar()
        result = adapter.apply_grammar_constraint(pg)
        assert result is True
        assert adapter._grammar_constraint is None

    @patch("lintgate.nsil.adapters.vllm._OUTLINES_AVAILABLE", True)
    def test_policy_grammar_gbnf_with_outlines(self) -> None:
        pg = PolicyGrammar(gbnf_rules="root ::= 'a'", regex_pattern=r"a")
        adapter = VLLMAdapter()
        result = adapter.apply_grammar_constraint(pg)
        assert result is True
        assert adapter._grammar_constraint == {"gbnf": "root ::= 'a'"}

    @patch("lintgate.nsil.adapters.vllm._OUTLINES_AVAILABLE", False)
    def test_policy_grammar_gbnf_without_outlines_falls_to_regex(self) -> None:
        pg = PolicyGrammar(
            gbnf_rules="root ::= 'a'",
            regex_pattern=r"a",
            explanation="test",
        )
        adapter = VLLMAdapter()
        result = adapter.apply_grammar_constraint(pg)
        assert result is True
        assert adapter._grammar_constraint == {"regex": r"a", "explanation": "test"}

    @patch("lintgate.nsil.adapters.vllm._OUTLINES_AVAILABLE", False)
    def test_policy_grammar_gbnf_no_regex_returns_true_empty(self) -> None:
        pg = PolicyGrammar(gbnf_rules="root ::= 'a'")
        adapter = VLLMAdapter()
        result = adapter.apply_grammar_constraint(pg)
        # Falls through to empty grammar branch
        assert result is True
        assert adapter._grammar_constraint is None


# ===========================================================================
# VLLMAdapter.apply_policy_grammar
# ===========================================================================


class TestApplyPolicyGrammar:
    def test_empty_grammar_bypasses(self) -> None:
        adapter = VLLMAdapter()
        success, msg = adapter.apply_policy_grammar(PolicyGrammar())
        assert success is True
        assert msg == "bypassed - no constraints"
        assert adapter._grammar_constraint is None

    @patch("lintgate.nsil.adapters.vllm._OUTLINES_AVAILABLE", True)
    def test_gbnf_with_outlines(self) -> None:
        pg = PolicyGrammar(gbnf_rules="root ::= 'x'")
        adapter = VLLMAdapter()
        success, msg = adapter.apply_policy_grammar(pg)
        assert success is True
        assert msg == "applied - GBNF mode"
        assert adapter._grammar_constraint == {"gbnf": "root ::= 'x'"}

    @patch("lintgate.nsil.adapters.vllm._OUTLINES_AVAILABLE", False)
    def test_gbnf_without_outlines_fallback_to_regex(self) -> None:
        pg = PolicyGrammar(
            gbnf_rules="root ::= 'x'",
            regex_pattern=r"x",
            explanation="exp",
        )
        adapter = VLLMAdapter()
        success, msg = adapter.apply_policy_grammar(pg)
        assert success is True
        assert msg == "applied - regex-only mode (Outlines unavailable)"
        assert adapter._grammar_constraint == {"regex": r"x", "explanation": "exp"}

    def test_regex_only_mode(self) -> None:
        pg = PolicyGrammar(regex_pattern=r"\w+", explanation="words")
        adapter = VLLMAdapter()
        success, msg = adapter.apply_policy_grammar(pg)
        assert success is True
        assert msg == "applied - regex-only mode"
        assert adapter._grammar_constraint == {"regex": r"\w+", "explanation": "words"}

    @patch("lintgate.nsil.adapters.vllm._OUTLINES_AVAILABLE", False)
    def test_gbnf_no_regex_no_outlines(self) -> None:
        pg = PolicyGrammar(gbnf_rules="root ::= 'z'")
        adapter = VLLMAdapter()
        success, msg = adapter.apply_policy_grammar(pg)
        assert success is False
        assert msg == "no grammar backend available"


# ===========================================================================
# VLLMAdapter.check_rejection
# ===========================================================================


class TestCheckRejection:
    def test_no_constraint_not_rejected(self) -> None:
        adapter = VLLMAdapter()
        rejected, reason = adapter.check_rejection("anything")
        assert rejected is False
        assert reason == ""

    def test_regex_match_is_rejected(self) -> None:
        adapter = VLLMAdapter()
        adapter._grammar_constraint = {"regex": r"rm\s+-rf", "explanation": "dangerous"}
        rejected, reason = adapter.check_rejection("run rm -rf /")
        assert rejected is True
        assert reason == "dangerous"

    def test_regex_no_match_not_rejected(self) -> None:
        adapter = VLLMAdapter()
        adapter._grammar_constraint = {"regex": r"rm\s+-rf", "explanation": "dangerous"}
        rejected, reason = adapter.check_rejection("ls -la")
        assert rejected is False
        assert reason == ""

    def test_regex_case_insensitive(self) -> None:
        adapter = VLLMAdapter()
        adapter._grammar_constraint = {"regex": r"DROP TABLE", "explanation": "sql"}
        rejected, reason = adapter.check_rejection("drop table users")
        assert rejected is True

    def test_regex_default_explanation(self) -> None:
        adapter = VLLMAdapter()
        adapter._grammar_constraint = {"regex": r"bad"}
        rejected, reason = adapter.check_rejection("this is bad")
        assert rejected is True
        assert reason == "text matches prohibited pattern"

    def test_gbnf_constraint_not_rejected(self) -> None:
        adapter = VLLMAdapter()
        adapter._grammar_constraint = {"gbnf": "root ::= 'x'"}
        rejected, reason = adapter.check_rejection("anything")
        assert rejected is False
        assert reason == ""

    def test_empty_regex_not_rejected(self) -> None:
        adapter = VLLMAdapter()
        adapter._grammar_constraint = {"regex": ""}
        rejected, reason = adapter.check_rejection("test")
        assert rejected is False
        assert reason == ""


# ===========================================================================
# VLLMAdapter.clear_grammar_constraint
# ===========================================================================


class TestClearGrammarConstraint:
    def test_clears_constraint(self) -> None:
        adapter = VLLMAdapter()
        adapter._grammar_constraint = {"gbnf": "rule"}
        adapter.clear_grammar_constraint()
        assert adapter._grammar_constraint is None
        # Verify cleared constraint does not affect payload
        payload: dict[str, Any] = {"model": "test"}
        adapter._apply_grammar_to_payload(payload)
        assert "extra_body" not in payload

    def test_clears_when_already_none(self) -> None:
        adapter = VLLMAdapter()
        assert adapter._grammar_constraint is None
        adapter.clear_grammar_constraint()
        assert adapter._grammar_constraint is None
        # Verify check_rejection returns not-rejected after clear
        rejected, reason = adapter.check_rejection("anything")
        assert rejected is False
        assert reason == ""


# ===========================================================================
# VLLMAdapter.is_available
# ===========================================================================


class TestIsAvailable:
    @patch("socket.socket")
    def test_available_when_connect_succeeds(self, mock_socket_cls: MagicMock) -> None:
        mock_sock = MagicMock()
        mock_sock.connect_ex.return_value = 0
        mock_socket_cls.return_value = mock_sock

        adapter = VLLMAdapter(endpoint="http://localhost:8000")
        assert adapter.is_available() is True
        mock_sock.connect_ex.assert_called_once_with(("localhost", 8000))
        mock_sock.close.assert_called_once()

    @patch("socket.socket")
    def test_unavailable_when_connect_fails(self, mock_socket_cls: MagicMock) -> None:
        mock_sock = MagicMock()
        mock_sock.connect_ex.return_value = 1
        mock_socket_cls.return_value = mock_sock

        adapter = VLLMAdapter(endpoint="http://localhost:8000")
        assert adapter.is_available() is False

    @patch("socket.socket")
    def test_unavailable_on_os_error(self, mock_socket_cls: MagicMock) -> None:
        mock_socket_cls.side_effect = OSError("fail")
        adapter = VLLMAdapter()
        assert adapter.is_available() is False

    def test_invalid_endpoint_rejected(self) -> None:
        import pytest
        with pytest.raises(ValueError, match="http"):
            VLLMAdapter(endpoint="no-port-here")

    @patch("socket.socket")
    def test_custom_port(self, mock_socket_cls: MagicMock) -> None:
        mock_sock = MagicMock()
        mock_sock.connect_ex.return_value = 0
        mock_socket_cls.return_value = mock_sock

        adapter = VLLMAdapter(endpoint="http://localhost:9999")
        adapter.is_available()
        mock_sock.connect_ex.assert_called_once_with(("localhost", 9999))


# ===========================================================================
# check_optional_dependencies
# ===========================================================================


class TestCheckOptionalDependencies:
    def test_returns_dict_with_expected_keys(self) -> None:
        result = check_optional_dependencies()
        assert "vllm" in result
        assert "outlines" in result
        assert isinstance(result["vllm"], bool)
        assert isinstance(result["outlines"], bool)

    def test_values_match_module_flags(self) -> None:
        import lintgate.nsil.adapters.vllm as vllm_mod

        result = check_optional_dependencies()
        assert result["vllm"] == vllm_mod._VLLM_AVAILABLE
        assert result["outlines"] == vllm_mod._OUTLINES_AVAILABLE


# ===========================================================================
# VLLMAdapter dataclass defaults
# ===========================================================================


class TestVLLMAdapterDefaults:
    def test_default_endpoint(self) -> None:
        adapter = VLLMAdapter()
        assert adapter.endpoint == "http://localhost:8000"

    def test_default_model(self) -> None:
        adapter = VLLMAdapter()
        assert adapter.model == "llama-2-7b"

    def test_custom_endpoint_and_model(self) -> None:
        adapter = VLLMAdapter(endpoint="http://gpu:5000", model="mistral-7b")
        assert adapter.endpoint == "http://gpu:5000"
        assert adapter.model == "mistral-7b"

    def test_hooks_and_state_are_isolated(self) -> None:
        a1 = VLLMAdapter()
        a2 = VLLMAdapter()
        a1._action_hooks.append(lambda a, d: None)
        a1._injected_state["key"] = "val"
        assert len(a2._action_hooks) == 0
        assert a2._injected_state == {}
