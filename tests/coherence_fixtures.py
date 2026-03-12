"""Shared fixture builders and golden-file helpers for coherence tests.

Used by test_coherence_scenarios.py and test_coherence_weighting.py.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal, cast

from lintgate.controlplane.coherence import compute_coherence
from lintgate.controlplane.types import ChannelResult
from lintgate.types import LintIssue

GOLDEN_DIR = Path(__file__).parent / "golden" / "coherence"

# -- Fixture builders ---------------------------------------------------------


def _issue(
    linter: str = "ruff_check",
    kind: str = "E001",
    severity: str = "warning",
    message: str = "test issue",
    file: str | None = "/src/foo.py",
    line: int | None = 1,
) -> LintIssue:
    return LintIssue(
        linter=linter,
        kind=kind,
        severity=cast("str", severity),
        message=message,
        file=file,
        line=line,
    )


def _channel(
    name: str,
    status: str = "pass",
    severity: str = "none",
    findings: list[LintIssue] | None = None,
    error_message: str | None = None,
    duration_ms: float = 100.0,
) -> ChannelResult:
    return ChannelResult(
        channel=name,
        status=cast("Literal['pass', 'fail', 'skip', 'error', 'timeout']", status),
        severity=cast("Literal['blocking', 'warning', 'informational', 'none']", severity),
        findings=findings or [],
        error_message=error_message,
        duration_ms=duration_ms,
    )


def _build_scenario(spec: dict[str, Any]) -> list[ChannelResult]:
    """Build ChannelResult list from a scenario spec dict."""
    results = []
    for ch_spec in spec["channels"]:
        findings = []
        for f in ch_spec.get("findings", []):
            findings.append(
                _issue(
                    linter=f.get("linter", ch_spec["name"]),
                    kind=f.get("kind", "E001"),
                    severity=f.get("severity", "warning"),
                    message=f.get("message", "issue"),
                    file=f.get("file"),
                    line=f.get("line"),
                )
            )
        results.append(
            _channel(
                name=ch_spec["name"],
                status=ch_spec.get("status", "pass"),
                severity=ch_spec.get("severity", "none"),
                findings=findings,
                error_message=ch_spec.get("error_message"),
            )
        )
    return results


def _load_golden(name: str) -> dict[str, Any]:
    path = GOLDEN_DIR / f"{name}.json"
    with open(path) as f:
        result: dict[str, Any] = json.load(f)
        return result


def _save_golden(name: str, data: dict[str, Any]) -> None:
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    path = GOLDEN_DIR / f"{name}.json"
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


def _generate_golden_for_scenario(name: str, spec: dict[str, Any]) -> dict[str, Any]:
    """Run coherence engine and capture output as golden fixture."""
    results = _build_scenario(spec)
    coherence = compute_coherence(results)
    return {
        "scenario": name,
        "input": {
            "channels": [
                {
                    "name": cr.channel,
                    "status": cr.status,
                    "finding_count": len(cr.findings),
                }
                for cr in results
            ]
        },
        "expected": {
            "state": coherence.state,
            "summary": coherence.summary,
            "recommended_action": coherence.recommended_action,
            "silent_channels": coherence.silent_channels,
            "loud_channels": coherence.loud_channels,
            "confidence": coherence.confidence,
            "classification_notes": coherence.classification_notes,
        },
    }
