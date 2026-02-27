"""vLLM adapter implementing LocalRuntimeAdapter protocol.

This module can be imported even if the optional 'vllm' or 'Outlines' packages
are not installed. Attempting to use features that require these packages will
raise an appropriate error.
"""

from collections.abc import Callable, Generator
from dataclasses import dataclass, field
from typing import Any

from lintgate.nsil.grammar_compiler import PolicyGrammar
from lintgate.nsil.runtime_adapter import RuntimeCapabilities

# Check for optional dependencies at module level, but don't fail import
_VLLM_AVAILABLE = False
_OUTLINES_AVAILABLE = False

try:
    import vllm  # noqa: F401

    _VLLM_AVAILABLE = True
except ImportError:
    pass

try:
    import outlines  # noqa: F401

    _OUTLINES_AVAILABLE = True
except ImportError:
    pass


@dataclass
class VLLMAdapter:
    """vLLM runtime adapter.

    Implements LocalRuntimeAdapter for vLLM's OpenAI-compatible API.
    Supports grammar constraints via Outlines (if available).
    """

    endpoint: str = "http://localhost:8000"
    model: str = "llama-2-7b"
    _action_hooks: list[Callable[[str, Any], None]] = field(default_factory=list)
    _injected_state: dict[str, Any] = field(default_factory=dict)
    _grammar_constraint: dict[str, Any] | None = field(default_factory=lambda: None)

    def get_capabilities(self) -> RuntimeCapabilities:
        """Get vLLM runtime capabilities.

        Returns capabilities based on available dependencies.
        Grammar constraints and logit processors require respective packages.
        """
        return RuntimeCapabilities(
            supports_state_injection=True,
            supports_streaming_hooks=True,
            supports_grammar_constraints=_OUTLINES_AVAILABLE,
            supports_logit_processors=_VLLM_AVAILABLE,
            max_context_tokens=32768,
            api_protocol="vllm",
        )

    @property
    def is_vllm_available(self) -> bool:
        """Check if vllm package is available."""
        return _VLLM_AVAILABLE

    @property
    def is_outlines_available(self) -> bool:
        """Check if Outlines package is available."""
        return _OUTLINES_AVAILABLE

    def inject_state(self, snapshot: dict[str, Any]) -> bool:
        """Inject inference state via system message prepend.

        Args:
            snapshot: The InferenceStateSnapshot to inject

        Returns:
            True if state was injected successfully
        """
        self._injected_state = snapshot.copy()
        return True

    def register_action_hook(self, callback: Callable[[str, Any], None]) -> None:
        """Register a callback for action events.

        Args:
            callback: Function to call on action events
        """
        self._action_hooks.append(callback)

    def _make_messages_with_state(self, prompt: str) -> list[dict[str, str]]:
        """Create messages list with injected state as system message."""
        messages: list[dict[str, str]] = []

        # Build system message from state if present
        if self._injected_state:
            parts = []
            if gate_status := self._injected_state.get("gate_status"):
                parts.append(f"Gate Status: {gate_status}")
            if risk_level := self._injected_state.get("risk_level"):
                parts.append(f"Risk Level: {risk_level}")
            if blocking := self._injected_state.get("blocking_findings"):
                findings = ", ".join(blocking[:3]) if isinstance(blocking, list) else str(blocking)
                parts.append(f"Blocking: {findings}")
            if constraints := self._injected_state.get("active_constraints"):
                constr = (
                    ", ".join(constraints[:3])
                    if isinstance(constraints, list)
                    else str(constraints)
                )
                parts.append(f"Constraints: {constr}")

            state_context = " | ".join(parts) if parts else ""
            if state_context:
                messages.append(
                    {
                        "role": "system",
                        "content": f"[NSIL State: {state_context}]",
                    }
                )

        messages.append({"role": "user", "content": prompt})
        return messages

    def get_generation_stream(self, prompt: str, **kwargs: Any) -> Generator[str, None, None]:
        """Get streaming generation from vLLM.

        Args:
            prompt: The prompt to generate from
            **kwargs: Additional parameters

        Yields:
            Generation chunks
        """
        import json
        import urllib.error
        import urllib.request

        # Build messages with state injection
        messages = self._make_messages_with_state(prompt)

        model = kwargs.get("model", self.model)
        temperature = kwargs.get("temperature", 0.7)
        max_tokens = kwargs.get("max_tokens", 512)

        # Build request payload (OpenAI chat completion format)
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }

        # Add grammar constraint if set
        if self._grammar_constraint:
            # vLLM supports grammar through guided decoding parameters
            if "gbnf" in self._grammar_constraint:
                payload["extra_body"] = {
                    "guided_grammar": self._grammar_constraint["gbnf"],
                }
            elif "regex" in self._grammar_constraint:
                payload["extra_body"] = {
                    "guided_regex": self._grammar_constraint["regex"],
                }

        url = f"{self.endpoint}/v1/chat/completions"

        try:
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                url,
                data=data,
                headers={
                    "Content-Type": "application/json",
                    "Accept": "text/event-stream",
                },
                method="POST",
            )

            with urllib.request.urlopen(req, timeout=60) as response:
                buffer = b""
                while True:
                    chunk = response.read(4096)
                    if not chunk:
                        break
                    buffer += chunk

                    # Parse SSE (Server-Sent Events)
                    text = buffer.decode("utf-8")
                    lines = text.split("\n")
                    buffer = bytes(lines[-1], "utf-8") if lines[-1] else b""

                    for line in lines[:-1]:
                        if not line.startswith("data: "):
                            continue
                        data_str = line[6:]  # Remove "data: " prefix
                        if data_str == "[DONE]":
                            return
                        try:
                            data = json.loads(data_str)
                            if "choices" in data and len(data["choices"]) > 0:
                                delta = data["choices"][0].get("delta", {})
                                if "content" in delta:
                                    raw_content = delta["content"]
                                    yield raw_content
                        except json.JSONDecodeError:
                            # Malformed chunk - ignore and continue
                            continue

        except urllib.error.URLError as e:
            yield f"[Error: vLLM unavailable - {e.reason}]"
        except TimeoutError:
            yield "[Error: vLLM request timed out]"
        except Exception as e:
            yield f"[Error: {str(e)}]"

    def get_generation_guarded(self, prompt: str, **kwargs: Any) -> Generator[str, None, None]:
        """Get guarded streaming generation from vLLM.

        This wraps get_generation_stream with StreamingGuard.
        """
        from .streaming import StreamingGuard

        guard = StreamingGuard(
            project_root=kwargs.get("project_root", "."),
            gate_contract=kwargs.get("gate_contract"),
            active_constraints=kwargs.get("active_constraints"),
            hygiene_state=kwargs.get("hygiene_state"),
        )

        raw_stream = self.get_generation_stream(prompt, **kwargs)
        return guard.guard_stream(raw_stream)

    def apply_grammar_constraint(self, grammar: dict[str, Any] | PolicyGrammar) -> bool:
        """Apply a grammar constraint to generation.

        Accepts either a dict or PolicyGrammar. If PolicyGrammar is provided,
        extracts the GBNF rules or falls back to regex-only mode.

        If Outlines is not available, this stores the constraint for later
        but returns False to indicate full integration is not complete.

        Args:
            grammar: Grammar specification (dict or PolicyGrammar)

        Returns:
            True if constraint was applied/stored, False if Outlines missing
        """
        # Handle PolicyGrammar input
        if isinstance(grammar, PolicyGrammar):
            if grammar.gbnf_rules and _OUTLINES_AVAILABLE:
                # Full GBNF support with Outlines
                self._grammar_constraint = {"gbnf": grammar.gbnf_rules}
                return True
            elif grammar.regex_pattern:
                # Regex-only mode shim (when GBNF not available)
                self._grammar_constraint = {
                    "regex": grammar.regex_pattern,
                    "explanation": grammar.explanation,
                }
                return True
            else:
                # Empty grammar - bypass constraints
                self._grammar_constraint = None
                return True

        # Handle dict input
        self._grammar_constraint = grammar
        # Constraint stored but can't be compiled without Outlines
        # When Outlines is available, compile grammar here (deferred)
        return _OUTLINES_AVAILABLE

    def apply_policy_grammar(self, policy: PolicyGrammar) -> tuple[bool, str]:
        """Apply PolicyGrammar to generation with status reporting.

        Args:
            policy: Compiled PolicyGrammar from grammar_compiler

        Returns:
            Tuple of (success, status_message)
        """
        if not policy.gbnf_rules and not policy.regex_pattern:
            # Empty grammar - bypass constraints
            self._grammar_constraint = None
            return True, "bypassed - no constraints"

        if policy.gbnf_rules:
            if _OUTLINES_AVAILABLE:
                self._grammar_constraint = {"gbnf": policy.gbnf_rules}
                return True, "applied - GBNF mode"
            elif policy.regex_pattern:
                # Fall back to regex-only mode
                self._grammar_constraint = {
                    "regex": policy.regex_pattern,
                    "explanation": policy.explanation,
                }
                return True, "applied - regex-only mode (Outlines unavailable)"
        elif policy.regex_pattern:
            # Regex-only mode
            self._grammar_constraint = {
                "regex": policy.regex_pattern,
                "explanation": policy.explanation,
            }
            return True, "applied - regex-only mode"

        # No grammar available
        return False, "no grammar backend available"

    def check_rejection(self, text: str) -> tuple[bool, str]:
        """Check if text violates the applied grammar constraint.

        Args:
            text: Text to check

        Returns:
            Tuple of (is_rejected, reason)
        """
        if not self._grammar_constraint:
            return False, ""

        constraint = self._grammar_constraint
        if isinstance(constraint, dict) and "regex" in constraint:
            import re

            regex = constraint["regex"]
            if regex and re.search(regex, text, re.IGNORECASE):
                return True, constraint.get("explanation", "text matches prohibited pattern")

        return False, ""

    def clear_grammar_constraint(self) -> None:
        """Clear any stored grammar constraint."""
        self._grammar_constraint = None

    def is_available(self) -> bool:
        """Check if vLLM runtime is available.

        Returns:
            True if vLLM is reachable, False otherwise
        """
        import socket

        try:
            port = int(self.endpoint.split(":")[-1])
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1)
            result = sock.connect_ex(("localhost", port))
            sock.close()
            return result == 0
        except (OSError, ValueError):
            return False


def check_optional_dependencies() -> dict[str, bool]:
    """Check availability of optional dependencies.

    Returns:
        Dict with 'vllm' and 'outlines' boolean flags
    """
    return {
        "vllm": _VLLM_AVAILABLE,
        "outlines": _OUTLINES_AVAILABLE,
    }
