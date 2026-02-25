"""Tests for the remediation router."""

from __future__ import annotations

from lintgate.orchestration.remediation_router import route_finding


def test_route_complexity_finding():
    finding = {"kind": "COMPLEXITY", "file": "app.py", "severity": "warning"}
    route = route_finding(finding, "lint")

    assert route["tool"] == "lint_files"
    assert "app.py" in route["args"]["files"]
    assert "Extract long code blocks" in str(route["remediation_sequence"])


def test_route_syntax_finding():
    finding = {"kind": "syntax-error", "file": "db.py", "line": 10}
    route = route_finding(finding, "lint")

    assert route["tool"] == "lint_files"
    assert " integrity" in route["rationale"]


def test_route_test_failure():
    finding = {"kind": "test_failure", "message": "Assert error"}
    route = route_finding(finding, "test")

    assert route["tool"] == "controlplane_get_details"
    assert route["args"]["channel"] == "test"


def test_route_security_finding():
    finding = {"kind": "security_vulnerability", "severity": "blocking"}
    route = route_finding(finding, "security")

    assert route["tool"] == "controlplane_run"
    assert route["args"]["strictness"] == "strict"


def test_route_fallback_unknown():
    finding = {"kind": "unknown_junk", "severity": "info"}
    route = route_finding(finding, "lint")

    assert route["tool"] == "controlplane_get_details"
    assert "Unknown finding classification" in route["rationale"]


def test_adversarial_missing_fields():
    # No file, no kind
    finding = {"severity": "warning"}
    route = route_finding(finding, "lint")

    assert route["tool"] == "controlplane_get_details"
    assert isinstance(route["args"], dict)
    assert len(route["remediation_sequence"]) > 0


def test_route_file_too_long():
    finding = {"kind": "too-many-lines", "file": "massive.py"}
    route = route_finding(finding, "lint")

    assert route["tool"] == "lint_files"
    assert "massive.py" in route["args"]["files"]
    assert "Split file" in str(route["remediation_sequence"])
