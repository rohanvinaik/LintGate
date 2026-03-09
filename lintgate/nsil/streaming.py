"""Shared streaming verification utilities for NSIL adapters."""

from collections.abc import Generator
from typing import Any

from .action_verifier import ActionProposal, verify_action


class StreamingGuard:
    """Shared utility for mid-stream action verification.

    Implements a sliding window buffer and boundary-triggered verification
    to prevent dangerous payloads from escaping the LLM stream.
    """

    def __init__(
        self,
        project_root: str = ".",
        gate_contract: dict[str, Any] | None = None,
        active_constraints: list[str] | None = None,
        hygiene_state: dict[str, Any] | None = None,
        window_size: int = 64,
    ):
        self.project_root = project_root
        self.gate_contract = gate_contract or {}
        self.active_constraints = active_constraints or []
        self.hygiene_state = hygiene_state or {}
        self.window_size = window_size

        self._buffer = ""
        self._violation_detected = False

    def _check_buffer(self, buffer: str) -> bool:
        """Check if the current buffer violates any constraints.

        Uses a heuristic approach for streaming:
        1. Assume the buffer might be a bash command or a direct action.
        2. If it matches known dangerous patterns, reject.
        """
        # Create a speculative proposal
        # In a stream, we don't always know the action_type yet,
        # so we check as both 'bash' and 'write' for maximum safety.

        # 1. Speculative bash check
        bash_proposal = ActionProposal(action_type="bash", content=buffer)
        res_bash = verify_action(
            bash_proposal,
            project_root=self.project_root,
            gate_contract=self.gate_contract,
            active_constraints=self.active_constraints,
            hygiene_state=self.hygiene_state,
        )
        if not res_bash.approved:
            return False

        # 2. Speculative write check (if buffer looks like a path)
        # This is a bit coarse but safe
        if "/" in buffer or "." in buffer:
            write_proposal = ActionProposal(action_type="write", target=buffer.strip())
            res_write = verify_action(
                write_proposal,
                project_root=self.project_root,
                gate_contract=self.gate_contract,
                active_constraints=self.active_constraints,
                hygiene_state=self.hygiene_state,
            )
            if not res_write.approved:
                return False

        return True

    def guard_stream(self, stream: Generator[str, None, None]) -> Generator[str, None, None]:
        """Wrap a token stream with eager verification.

        Yields tokens only after they pass the sliding window look-ahead.
        """
        for chunk in stream:
            if self._violation_detected:
                break

            self._buffer += chunk

            # 1. Continuous Pattern Matching (Fast)
            # Check the sliding window for immediate dangerous patterns (regex level)
            window = self._buffer[-self.window_size :]
            if not self._check_buffer(window):
                self._violation_detected = True
                yield "\n[NSIL VIOLATION: Dangerous pattern detected in stream. Termination triggered.]"
                return

            # 2. Boundary Trigger (Deep check)
            # If we hit a boundary, check the whole buffer (up to a reasonable limit)
            if any(b in chunk for b in ("\n", "}", "]", "Selection:")):
                # Limit buffer size for deep check to avoid O(N^2) on very long streams
                deep_check_buffer = self._buffer[-1024:]
                if not self._check_buffer(deep_check_buffer):
                    self._violation_detected = True
                    yield "\n[NSIL VIOLATION: Constraint violation at boundary. Stream killed.]"
                    return

            # 3. Release tokens that are outside the sliding window
            # If buffer exceeds window_size, yield the overflow
            if len(self._buffer) > self.window_size:
                release_idx = len(self._buffer) - self.window_size
                to_release = self._buffer[:release_idx]
                self._buffer = self._buffer[release_idx:]
                yield to_release

        # Final release of remaining buffer if no violation
        if not self._violation_detected and self._buffer:
            yield self._buffer
