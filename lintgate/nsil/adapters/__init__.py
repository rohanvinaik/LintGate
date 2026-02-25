"""NSIL runtime adapters.

This package contains concrete adapter implementations for local runtimes.
"""

from lintgate.nsil.adapters.ollama import OllamaAdapter
from lintgate.nsil.adapters.vllm import VLLMAdapter, check_optional_dependencies
from lintgate.nsil.runtime_adapter import (
    LocalRuntimeAdapter,
    RuntimeCapabilities,
    RuntimeProbeResult,
)

__all__ = [
    "OllamaAdapter",
    "VLLMAdapter",
    "check_optional_dependencies",
    "LocalRuntimeAdapter",
    "RuntimeCapabilities",
    "RuntimeProbeResult",
]
