"""Signal-Source Decomposition — isolate findings from heterogeneous upstream tools.

Provides a unified signal extraction pipeline that normalizes outputs
from various tools into a structured evidence map.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ExtractedSignal:
    """A normalized signal extracted from heterogeneous upstream tool output."""

    kind: str
    severity: str
    message: str
    evidence_map: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "severity": self.severity,
            "message": self.message,
            "evidence_map": self.evidence_map,
        }


class SignalExtractor:
    """Universal parser for extracting structured signals from raw text or JSON."""

    def __init__(self):
        self._regex_cache: dict[str, re.Pattern[str]] = {}

    def extract(self, raw_input: Any, channel_type: str = "generic") -> list[ExtractedSignal]:
        """Extract signals from raw input."""
        if isinstance(raw_input, dict):
            return self._extract_from_json(raw_input, channel_type)
        if isinstance(raw_input, list):
            signals = []
            for item in raw_input:
                signals.extend(self.extract(item, channel_type))
            return signals

        return self._extract_from_text(str(raw_input), channel_type)

    def _extract_from_json(self, data: dict[str, Any], channel_type: str) -> list[ExtractedSignal]:
        """Extract structured signal from an AST/JSON-like object."""
        # Typically upstream linters (like ruff) output something with 'code', 'message' etc.
        kind = data.get("kind") or data.get("code") or "unknown_json_kind"
        msg = data.get("message") or data.get("detail") or str(data)
        severity = data.get("severity", "advisory").lower()

        evidence = {
            k: str(v)
            for k, v in data.items()
            if k not in ["kind", "code", "message", "detail", "severity"]
        }

        return [
            ExtractedSignal(
                kind=str(kind),
                severity=severity,
                message=str(msg),
                evidence_map=evidence,
            )
        ]

    def _extract_from_text(self, text: str, channel_type: str) -> list[ExtractedSignal]:
        """Extract unstructured signal via regex heuristics."""
        signals = []

        # Look for standard Error/Warning lines
        error_pattern = self._get_regex(r"(?i)^(error|warning|fatal):\s*(.*?)$")
        file_line_pattern = self._get_regex(r"^(.*?):(\d+):\s*(.*)$")

        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue

            match = error_pattern.match(line)
            if match:
                severity = match.group(1).lower()
                msg = match.group(2)
                signals.append(
                    ExtractedSignal(
                        kind=f"{channel_type}_issue",
                        severity="blocking" if severity in ("error", "fatal") else "warning",
                        message=msg,
                        evidence_map={"raw_line": line},
                    )
                )
                continue

            match = file_line_pattern.match(line)
            if match:
                file_path, line_num, msg = match.groups()
                signals.append(
                    ExtractedSignal(
                        kind=f"{channel_type}_file_issue",
                        severity="blocking",
                        message=msg,
                        evidence_map={
                            "file": file_path,
                            "line": line_num,
                            "raw_line": line,
                        },
                    )
                )

        return signals

    def _get_regex(self, pattern: str) -> re.Pattern:
        if pattern not in self._regex_cache:
            self._regex_cache[pattern] = re.compile(pattern)
        return self._regex_cache[pattern]
