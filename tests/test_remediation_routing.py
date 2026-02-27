from lintgate.orchestration.remediation_router import route_finding


def test_route_finding_complexity():
    route = route_finding({"kind": "complexity_high", "file": "foo.py"}, "lint")
    assert route["tool"] == "lint_files"
    assert "foo.py" in route["args"]["files"]
    assert "High complexity" in route["rationale"]


def test_route_finding_file_too_long():
    route = route_finding({"kind": "file-too-long"}, "lint")
    assert route["tool"] == "lint_files"
    assert "length thresholds" in route["rationale"]


def test_route_finding_syntax():
    route = route_finding({"kind": "Syntax_error"}, "lint")
    assert route["tool"] == "lint_files"
    assert "Basic code integrity" in route["rationale"]


def test_route_finding_duplicate():
    route = route_finding({"kind": "Duplicate_stuff"}, "lint")
    assert route["tool"] == "lint_files"
    assert "Duplicate definition" in route["rationale"]


def test_route_finding_test_failure():
    route = route_finding({"kind": "assertion_error"}, "test")
    assert route["tool"] == "controlplane_get_details"
    assert route["args"]["channel"] == "test"
    assert "Mesh detected test regressions" in route["rationale"]


def test_route_finding_security():
    route = route_finding({"kind": "bandit_issue", "severity": "blocking"}, "lint")
    assert route["tool"] == "controlplane_run"
    assert route["args"]["strictness"] == "strict"
    assert "Security-sensitive" in route["rationale"]


def test_route_finding_fallback():
    route = route_finding({"kind": "mystery_finding"}, "weird_channel")
    assert route["tool"] == "controlplane_get_details"
    assert "Unknown finding classification" in route["rationale"]
