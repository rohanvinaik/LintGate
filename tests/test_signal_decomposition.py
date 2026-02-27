from lintgate.orchestration.signals import SignalExtractor


def test_extract_json():
    extractor = SignalExtractor()
    raw = {
        "code": "F401",
        "message": "imported but unused",
        "severity": "WARNING",
        "file": "foo.py",
        "line": 10,
    }
    signals = extractor.extract(raw, "lint")
    assert len(signals) == 1
    assert signals[0].kind == "F401"
    assert signals[0].severity == "warning"
    assert signals[0].message == "imported but unused"
    assert signals[0].evidence_map["file"] == "foo.py"


def test_extract_text_errors():
    extractor = SignalExtractor()
    text = "Error: Something went wrong\nWarning: Be careful"
    signals = extractor.extract(text, "build")
    assert len(signals) == 2
    assert signals[0].severity == "blocking"
    assert signals[0].message == "Something went wrong"
    assert signals[1].severity == "warning"
    assert signals[1].message == "Be careful"


def test_extract_file_lines():
    extractor = SignalExtractor()
    text = "src/main.py:42: Missing docstring"
    signals = extractor.extract(text, "test")
    assert len(signals) == 1
    assert signals[0].kind == "test_file_issue"
    assert signals[0].evidence_map["file"] == "src/main.py"
    assert signals[0].evidence_map["line"] == "42"
