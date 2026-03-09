"""NSIL local runtime adapter protocol and discovery."""

from collections.abc import Callable, Generator
from dataclasses import dataclass
from typing import Any, Literal, Protocol

# Type aliases for runtime capabilities
ApiProtocol = Literal["openai", "anthropic", "ollama", "vllm", "custom"]


@dataclass(frozen=True)
class RuntimeCapabilities:
    """Capabilities of a local runtime adapter."""

    supports_state_injection: bool = False
    supports_streaming_hooks: bool = False
    supports_grammar_constraints: bool = False
    supports_logit_processors: bool = False
    max_context_tokens: int = 0
    api_protocol: ApiProtocol = "custom"


@dataclass(frozen=True)
class RuntimeProbeResult:
    """Result of probing a local runtime.

    Status values:
    - available: Runtime is reachable and responsive
    - unavailable: Runtime is not running or not reachable
    - error: Probe failed with an error
    """

    runtime_name: str
    status: Literal["available", "unavailable", "error"]
    capabilities: RuntimeCapabilities | None = None
    endpoint: str | None = None
    error_message: str | None = None
    latency_ms: float | None = None


class LocalRuntimeAdapter(Protocol):
    """Protocol for local runtime adapters.

    Runtime adapters must implement these methods to integrate with NSIL.
    """

    def inject_state(self, snapshot: dict[str, Any]) -> bool:
        """Inject inference state into the runtime context.

        Args:
            snapshot: The InferenceStateSnapshot to inject

        Returns:
            True if state was injected successfully
        """
        ...

    def register_action_hook(self, callback: Callable[[str, Any], None]) -> None:
        """Register a callback for action events.

        Args:
            callback: Function to call on action events
        """
        ...

    def get_generation_stream(self, prompt: str, **kwargs: Any) -> Generator[str, None, None]:
        """Get a streaming generation response.

        Args:
            prompt: The prompt to generate from
            **kwargs: Additional generation parameters

        Yields:
            Generation chunks
        """
        ...

    def apply_grammar_constraint(self, grammar: dict[str, Any]) -> bool:
        """Apply a grammar constraint to generation.

        Args:
            grammar: Grammar specification

        Returns:
            True if constraint was applied
        """
        ...

    def get_capabilities(self) -> RuntimeCapabilities:
        """Get the runtime capabilities.

        Returns:
            RuntimeCapabilities describing this runtime
        """
        ...


# Known runtimes for discovery
KNOWN_RUNTIMES = [
    {
        "name": "ollama",
        "default_port": 11434,
        "health_endpoint": "/api/tags",
        "capabilities": RuntimeCapabilities(
            supports_state_injection=False,
            supports_streaming_hooks=False,
            supports_grammar_constraints=False,
            supports_logit_processors=False,
            max_context_tokens=8192,
            api_protocol="ollama",
        ),
    },
    {
        "name": "vllm",
        "default_port": 8000,
        "health_endpoint": "/v1/models",
        "capabilities": RuntimeCapabilities(
            supports_state_injection=False,
            supports_streaming_hooks=False,
            supports_grammar_constraints=True,
            supports_logit_processors=False,
            max_context_tokens=32768,
            api_protocol="vllm",
        ),
    },
    {
        "name": "lmstudio",
        "default_port": 1234,
        "health_endpoint": "/v1/models",
        "capabilities": RuntimeCapabilities(
            supports_state_injection=False,
            supports_streaming_hooks=False,
            supports_grammar_constraints=False,
            supports_logit_processors=False,
            max_context_tokens=32768,
            api_protocol="openai",
        ),
    },
    {
        "name": "llamafile",
        "default_port": 8080,
        "health_endpoint": "/v1/models",
        "capabilities": RuntimeCapabilities(
            supports_state_injection=False,
            supports_streaming_hooks=False,
            supports_grammar_constraints=False,
            supports_logit_processors=False,
            max_context_tokens=8192,
            api_protocol="openai",
        ),
    },
]


def _probe_runtime(
    name: str,
    port: int,
    health_endpoint: str,
    capabilities: RuntimeCapabilities,
    timeout_ms: int,
) -> RuntimeProbeResult:
    """Probe a single runtime using HTTP.

    Uses socket connection check first, then HTTP probe if available.
    Does NOT import any runtime SDKs.
    """
    import socket
    import urllib.error
    import urllib.request

    endpoint = f"http://localhost:{port}"
    full_url = f"{endpoint}{health_endpoint}"

    # First check if port is open (socket check)
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout_ms / 1000.0)
    try:
        result = sock.connect_ex(("localhost", port))
        sock.close()
        if result != 0:
            return RuntimeProbeResult(
                runtime_name=name,
                status="unavailable",
                endpoint=endpoint,
            )
    except OSError as e:
        return RuntimeProbeResult(
            runtime_name=name,
            status="error",
            error_message=f"Socket error: {e}",
            endpoint=endpoint,
        )

    # Port is open, try HTTP probe
    import time

    start_time = time.perf_counter()
    try:
        req = urllib.request.Request(full_url, method="GET")
        req.add_header("Accept", "application/json")
        with urllib.request.urlopen(req, timeout=timeout_ms / 1000.0) as response:
            latency_ms = (time.perf_counter() - start_time) * 1000
            # Read response to ensure connection is valid
            _ = response.read()
            return RuntimeProbeResult(
                runtime_name=name,
                status="available",
                capabilities=capabilities,
                endpoint=endpoint,
                latency_ms=round(latency_ms, 2),
            )
    except urllib.error.HTTPError as e:
        # 4xx errors mean server is running but returned error
        if 400 <= e.code < 500:
            latency_ms = (time.perf_counter() - start_time) * 1000
            return RuntimeProbeResult(
                runtime_name=name,
                status="available",
                capabilities=capabilities,
                endpoint=endpoint,
                latency_ms=round(latency_ms, 2),
            )
        return RuntimeProbeResult(
            runtime_name=name,
            status="error",
            error_message=f"HTTP {e.code}: {e.reason}",
            endpoint=endpoint,
        )
    except urllib.error.URLError as e:
        return RuntimeProbeResult(
            runtime_name=name,
            status="unavailable",
            error_message=f"URL error: {e.reason}",
            endpoint=endpoint,
        )
    except TimeoutError:
        return RuntimeProbeResult(
            runtime_name=name,
            status="unavailable",
            error_message="Timeout",
            endpoint=endpoint,
        )
    except Exception as e:
        return RuntimeProbeResult(
            runtime_name=name,
            status="error",
            error_message=str(e),
            endpoint=endpoint,
        )


def detect_local_runtimes(timeout_ms: int = 1000) -> list[RuntimeProbeResult]:
    """Discover available local runtimes.

    Probes known runtimes using HTTP checks only (no SDK imports).
    Returns deterministic ordered results sorted by runtime name.

    Args:
        timeout_ms: Timeout for each probe in milliseconds

    Returns:
        List of RuntimeProbeResult, sorted by runtime_name for determinism
    """
    results: list[RuntimeProbeResult] = []

    for runtime in KNOWN_RUNTIMES:
        result = _probe_runtime(
            name=runtime["name"],
            port=runtime["default_port"],
            health_endpoint=runtime["health_endpoint"],
            capabilities=runtime["capabilities"],
            timeout_ms=timeout_ms,
        )
        results.append(result)

    # Sort by runtime name for deterministic output
    results.sort(key=lambda r: r.runtime_name)

    return results
