"""Ollama adapter implementing LocalRuntimeAdapter protocol."""

from collections.abc import Callable, Generator
from dataclasses import dataclass, field
from typing import Any

from lintgate.nsil.runtime_adapter import RuntimeCapabilities


def _iter_jsonl_stream(response: Any) -> Generator[str, None, None]:
    """Yield text chunks from an Ollama JSONL streaming response."""
    import json

    buffer = b""
    while True:
        chunk = response.read(4096)
        if not chunk:
            break
        buffer += chunk

        lines = buffer.decode("utf-8").split("\n")
        buffer = lines[-1].encode("utf-8") if lines[-1] else b""

        for line in lines[:-1]:
            if not line.strip():
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "response" in data:
                yield data["response"]
            if data.get("done", False):
                return


@dataclass
class OllamaAdapter:
    """Ollama runtime adapter.

    Implements LocalRuntimeAdapter for Ollama's /api/generate endpoint.
    Uses system-prompt prepend for state injection.
    """

    endpoint: str = "http://localhost:11434"
    model: str = "llama2"
    _action_hooks: list[Callable[[str, Any], None]] = field(default_factory=list)
    _injected_state: dict[str, Any] = field(default_factory=dict)

    def get_capabilities(self) -> RuntimeCapabilities:
        """Get Ollama runtime capabilities."""
        return RuntimeCapabilities(
            supports_state_injection=True,
            supports_streaming_hooks=True,
            supports_grammar_constraints=False,
            supports_logit_processors=False,
            max_context_tokens=8192,
            api_protocol="ollama",
        )

    def inject_state(self, snapshot: dict[str, Any]) -> bool:
        """Inject inference state via system-prompt prepend.

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

    def _make_prompt_with_state(self, prompt: str) -> str:
        """Create prompt with injected state as system prepend."""
        if not self._injected_state:
            return prompt

        # Build state context from snapshot
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
                ", ".join(constraints[:3]) if isinstance(constraints, list) else str(constraints)
            )
            parts.append(f"Constraints: {constr}")

        state_context = " | ".join(parts) if parts else ""
        if state_context:
            return f"[NSIL State: {state_context}]\n\n{prompt}"
        return prompt

    def get_generation_stream(self, prompt: str, **kwargs: Any) -> Generator[str, None, None]:
        """Get streaming generation from Ollama.

        Args:
            prompt: The prompt to generate from
            **kwargs: Additional parameters (model, temperature, etc.)

        Yields:
            Generation chunks
        """
        import urllib.error
        import urllib.request

        payload = self._build_payload(prompt, **kwargs)
        url = f"{self.endpoint}/api/generate"

        try:
            req = self._make_request(url, payload)
            with urllib.request.urlopen(req, timeout=30) as response:
                yield from _iter_jsonl_stream(response)
        except urllib.error.URLError as e:
            yield f"[Error: Ollama unavailable - {e.reason}]"
        except TimeoutError:
            yield "[Error: Ollama request timed out]"
        except Exception as e:
            yield f"[Error: {str(e)}]"

    def _build_payload(self, prompt: str, **kwargs: Any) -> dict[str, Any]:
        """Build the Ollama API request payload."""
        payload: dict[str, Any] = {
            "model": kwargs.get("model", self.model),
            "prompt": self._make_prompt_with_state(prompt),
            "stream": kwargs.get("stream", True),
            "temperature": kwargs.get("temperature", 0.7),
        }
        if options := kwargs.get("options"):
            payload["options"] = options
        return payload

    @staticmethod
    def _make_request(url: str, payload: dict[str, Any]) -> Any:
        """Create an HTTP request for the Ollama API."""
        import json
        import urllib.request

        return urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

    def get_generation_guarded(self, prompt: str, **kwargs: Any) -> Generator[str, None, None]:
        """Get guarded streaming generation from Ollama.

        This wraps get_generation_stream with StreamingGuard.
        """
        from .streaming import StreamingGuard  # type: ignore[import-untyped]

        guard = StreamingGuard(
            project_root=kwargs.get("project_root", "."),
            gate_contract=kwargs.get("gate_contract"),
            active_constraints=kwargs.get("active_constraints"),
            hygiene_state=kwargs.get("hygiene_state"),
        )

        raw_stream = self.get_generation_stream(prompt, **kwargs)
        return guard.guard_stream(raw_stream)  # type: ignore[no-any-return]

    def apply_grammar_constraint(self, grammar: dict[str, Any]) -> bool:
        """Apply grammar constraint.

        Ollama does not support grammar-constrained decoding.
        Returns False to indicate not supported.
        """
        return False

    def is_available(self) -> bool:
        """Check if Ollama runtime is available.

        Returns:
            True if Ollama is reachable, False otherwise
        """
        import socket

        try:
            # Extract port from endpoint
            port = int(self.endpoint.split(":")[-1])
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1)
            result = sock.connect_ex(("localhost", port))
            sock.close()
            return result == 0
        except (OSError, ValueError):
            return False
