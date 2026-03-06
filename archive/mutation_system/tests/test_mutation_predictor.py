"""Tests for the static specification predictor."""



from lintgate.linters.test_effectiveness.types import (
    AssertionInfo,
    AssertionKind,
    EffectivenessWeakness,
    FunctionEffectiveness,
    TestEffectivenessManifest,
)
from lintgate.mutation.predictor import (
    CalibrationStore,
    PredictionCalibration,
    build_function_category_map,
    predict_for_file,
    predict_specification_level,
)
from lintgate.mutation.state import SpecificationLevel

# ── Helpers ─────────────────────────────────────────────────────────────


def _make_teff(
    name: str = "f",
    test_count: int = 3,
    semantic_ratio: float = 0.5,
    weakness: EffectivenessWeakness = EffectivenessWeakness.HEALTHY,
    assertions: list[AssertionInfo] | None = None,
) -> FunctionEffectiveness:
    fe = FunctionEffectiveness(
        function_name=name,
        test_count=test_count,
        semantic_ratio=semantic_ratio,
    )
    fe.weakness_taxonomy = weakness
    fe.assertions = assertions or []
    return fe


# ── Decision tree paths ─────────────────────────────────────────────────


class TestPredictSpecificationLevel:
    """Tests for each branch of the decision tree."""

    def test_no_tests_returns_unspecified(self):
        pred = predict_specification_level("f1", {"m1": "arithmetic"}, None, None)
        assert pred.predicted_level == SpecificationLevel.UNSPECIFIED
        assert pred.confidence == 0.95
        assert pred.needs_verification is False
        assert "no_tests" in pred.signals_used

    def test_zero_test_count_returns_unspecified(self):
        teff = _make_teff(test_count=0)
        pred = predict_specification_level("f1", {"m1": "arithmetic"}, teff, None)
        assert pred.predicted_level == SpecificationLevel.UNSPECIFIED
        assert pred.confidence == 0.95

    def test_strong_assertions_few_categories(self):
        teff = _make_teff(
            semantic_ratio=0.9,
            assertions=[AssertionInfo(kind=AssertionKind.EQUALITY, line=1, strength=0.9)],
        )
        cat_map = {"m1": "arithmetic", "m2": "arithmetic"}
        pred = predict_specification_level("f2", cat_map, teff, None)
        assert pred.predicted_level == SpecificationLevel.NEARLY_SPECIFIED
        assert pred.confidence == 0.65
        assert pred.needs_verification is True

    def test_structural_only_many_categories(self):
        teff = _make_teff(
            semantic_ratio=0.1,
            weakness=EffectivenessWeakness.STRUCTURAL_ONLY,
            assertions=[AssertionInfo(kind=AssertionKind.IS_NOT_NONE, line=1, strength=0.3)],
        )
        cat_map = {"m1": "arithmetic", "m2": "string", "m3": "conditional"}
        pred = predict_specification_level("f3", cat_map, teff, None)
        assert pred.predicted_level == SpecificationLevel.TANGLED
        assert pred.confidence == 0.70

    def test_genuinely_weak_many_categories(self):
        teff = _make_teff(
            semantic_ratio=0.1,
            weakness=EffectivenessWeakness.GENUINELY_WEAK,
            assertions=[],
        )
        cat_map = {"m1": "arithmetic", "m2": "string", "m3": "conditional"}
        pred = predict_specification_level("f3b", cat_map, teff, None)
        assert pred.predicted_level == SpecificationLevel.TANGLED

    def test_strong_assertions_many_categories(self):
        """Path 2b: strong tests + 3+ categories -> DECOMPOSITION_CANDIDATE."""
        teff = _make_teff(
            semantic_ratio=0.85,
            assertions=[AssertionInfo(kind=AssertionKind.EQUALITY, line=1, strength=0.9)],
        )
        cat_map = {"m1": "arithmetic", "m2": "string", "m3": "conditional"}
        pred = predict_specification_level("f2b", cat_map, teff, None)
        assert pred.predicted_level == SpecificationLevel.DECOMPOSITION_CANDIDATE
        assert pred.confidence == 0.55
        assert pred.needs_verification is True
        assert any("strong_assertions_many" in s for s in pred.signals_used)

    def test_strong_single_category(self):
        teff = _make_teff(
            semantic_ratio=0.7,
            assertions=[AssertionInfo(kind=AssertionKind.EQUALITY, line=1, strength=0.9)],
        )
        pred = predict_specification_level("f4", {"m1": "arithmetic"}, teff, None)
        assert pred.predicted_level == SpecificationLevel.NEARLY_SPECIFIED
        assert pred.confidence == 0.60
        assert pred.needs_verification is True

    def test_mid_range_semantic_ratio(self):
        teff = _make_teff(semantic_ratio=0.45)
        pred = predict_specification_level(
            "f5", {"m1": "arithmetic", "m2": "string"}, teff, None
        )
        assert pred.predicted_level == SpecificationLevel.DECOMPOSITION_CANDIDATE
        assert pred.confidence == 0.50
        assert pred.needs_verification is True

    def test_fallback_low_confidence(self):
        teff = _make_teff(semantic_ratio=0.1)
        pred = predict_specification_level("f6", {"m1": "arithmetic"}, teff, None)
        assert pred.predicted_level == SpecificationLevel.UNSPECIFIED
        assert pred.confidence == 0.30
        assert pred.needs_verification is True

    def test_signals_audit_trail(self):
        """Verify signals_used contains useful diagnostic info."""
        teff = _make_teff(semantic_ratio=0.9)
        pred = predict_specification_level(
            "f", {"m1": "arithmetic", "m2": "number"}, teff, None
        )
        assert any("semantic_ratio" in s for s in pred.signals_used)
        assert any("unique_categories" in s for s in pred.signals_used)
        assert any("path=" in s for s in pred.signals_used)


# ── Per-category predictions ────────────────────────────────────────────


class TestCategoryPredictions:
    def test_arithmetic_killed_by_equality(self):
        teff = _make_teff(
            semantic_ratio=0.9,
            assertions=[AssertionInfo(kind=AssertionKind.EQUALITY, line=1, strength=0.9)],
        )
        pred = predict_specification_level("f", {"m1": "arithmetic"}, teff, None)
        assert pred.category_predictions["arithmetic"] == "killed"

    def test_string_killed_by_string_contains(self):
        teff = _make_teff(
            semantic_ratio=0.9,
            assertions=[AssertionInfo(kind=AssertionKind.STRING_CONTAINS, line=1, strength=0.75)],
        )
        pred = predict_specification_level("f", {"m1": "string"}, teff, None)
        assert pred.category_predictions["string"] == "killed"

    def test_string_killed_by_regex_match(self):
        teff = _make_teff(
            semantic_ratio=0.9,
            assertions=[AssertionInfo(kind=AssertionKind.REGEX_MATCH, line=1, strength=0.7)],
        )
        pred = predict_specification_level("f", {"m1": "string"}, teff, None)
        assert pred.category_predictions["string"] == "killed"

    def test_structural_only_survive(self):
        teff = _make_teff(
            semantic_ratio=0.1,
            weakness=EffectivenessWeakness.STRUCTURAL_ONLY,
            assertions=[AssertionInfo(kind=AssertionKind.IS_NOT_NONE, line=1, strength=0.3)],
        )
        pred = predict_specification_level(
            "f", {"m1": "arithmetic", "m2": "string", "m3": "conditional"}, teff, None
        )
        assert pred.category_predictions["arithmetic"] == "survive"
        assert pred.category_predictions["string"] == "survive"
        assert pred.category_predictions["conditional"] == "survive"

    def test_no_assertions_uncertain(self):
        teff = _make_teff(semantic_ratio=0.5, assertions=[])
        pred = predict_specification_level("f", {"m1": "arithmetic"}, teff, None)
        assert pred.category_predictions["arithmetic"] == "uncertain"

    def test_no_teff_uncertain(self):
        pred = predict_specification_level("f", {"m1": "arithmetic"}, None, None)
        assert pred.category_predictions["arithmetic"] == "uncertain"


# ── AST category map builder ────────────────────────────────────────────


class TestBuildFunctionCategoryMap:
    def test_simple_arithmetic(self):
        source = "def add(x, y):\n    return x + y\n"
        cats = build_function_category_map(source, "test.py")
        assert "add" in cats
        assert any(v == "arithmetic" for v in cats["add"].values())

    def test_string_operations(self):
        source = 'def greet(name):\n    return "hello " + name\n'
        cats = build_function_category_map(source, "test.py")
        assert "greet" in cats
        # String constant is detected
        assert any(v == "string" for v in cats["greet"].values())

    def test_class_methods(self):
        source = (
            "class Calc:\n"
            "    def multiply(self, a, b):\n"
            "        return a * b\n"
        )
        cats = build_function_category_map(source, "test.py")
        assert "Calc.multiply" in cats
        assert any(v == "arithmetic" for v in cats["Calc.multiply"].values())

    def test_conditional_operations(self):
        source = (
            "def check(x):\n"
            "    if x > 0:\n"
            "        return True\n"
            "    return False\n"
        )
        cats = build_function_category_map(source, "test.py")
        assert "check" in cats
        assert any(v == "conditional" for v in cats["check"].values())

    def test_nested_functions_separate(self):
        source = (
            "def outer(x):\n"
            "    def inner(y):\n"
            "        return y + 1\n"
            "    return inner(x) + 2\n"
        )
        cats = build_function_category_map(source, "test.py")
        assert "outer" in cats
        assert "inner" in cats

    def test_syntax_error_returns_empty(self):
        cats = build_function_category_map("def broken(", "test.py")
        assert cats == {}

    def test_empty_function(self):
        source = "def noop():\n    pass\n"
        cats = build_function_category_map(source, "test.py")
        assert "noop" in cats
        assert cats["noop"] == {}


# ── File-level prediction ───────────────────────────────────────────────


class TestPredictForFile:
    def test_with_no_manifests(self, tmp_path):
        source = "def add(x, y):\n    return x + y\n"
        f = tmp_path / "example.py"
        f.write_text(source)

        preds = predict_for_file(str(f), None, None)
        assert len(preds) == 1
        key = list(preds.keys())[0]
        assert "::add" in key
        # No teff -> UNSPECIFIED
        assert preds[key].predicted_level == SpecificationLevel.UNSPECIFIED
        assert preds[key].confidence == 0.95

    def test_with_project_root(self, tmp_path):
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        f = src_dir / "example.py"
        f.write_text("def mul(a, b):\n    return a * b\n")

        preds = predict_for_file(str(f), None, None, project_root=str(tmp_path))
        key = list(preds.keys())[0]
        assert key.startswith("src/example.py::")

    def test_with_teff_manifest(self, tmp_path):
        source = "def compute(x):\n    return x * 2\n"
        f = tmp_path / "mod.py"
        f.write_text(source)

        teff = _make_teff(
            name="compute",
            semantic_ratio=0.9,
            assertions=[AssertionInfo(kind=AssertionKind.EQUALITY, line=1, strength=0.9)],
        )
        manifest = TestEffectivenessManifest(functions={"mod.py::compute": teff})

        preds = predict_for_file(str(f), None, manifest)
        key = list(preds.keys())[0]
        # Should use teff data and predict nearly specified (strong + few cats)
        assert preds[key].predicted_level == SpecificationLevel.NEARLY_SPECIFIED

    def test_nonexistent_file_returns_empty(self):
        preds = predict_for_file("/nonexistent/file.py", None, None)
        assert preds == {}


# ── Calibration ─────────────────────────────────────────────────────────


class TestPredictionCalibration:
    def test_initial_accuracy(self):
        cal = PredictionCalibration()
        assert cal.accuracy == 0.5
        assert cal.total_predictions == 0
        assert cal.correct_predictions == 0

    def test_correct_prediction_increases_accuracy(self):
        cal = PredictionCalibration()
        cal.update(SpecificationLevel.UNSPECIFIED, SpecificationLevel.UNSPECIFIED)
        assert cal.accuracy > 0.5
        assert cal.total_predictions == 1
        assert cal.correct_predictions == 1

    def test_incorrect_prediction_decreases_accuracy(self):
        cal = PredictionCalibration()
        cal.update(SpecificationLevel.UNSPECIFIED, SpecificationLevel.TANGLED)
        assert cal.accuracy < 0.5
        assert cal.total_predictions == 1
        assert cal.correct_predictions == 0

    def test_ema_alpha(self):
        cal = PredictionCalibration()
        # Correct: 0.85 * 0.5 + 0.15 * 1.0 = 0.575
        cal.update(SpecificationLevel.UNSPECIFIED, SpecificationLevel.UNSPECIFIED)
        assert abs(cal.accuracy - 0.575) < 0.001
        # Incorrect: 0.85 * 0.575 + 0.15 * 0.0 = 0.48875
        cal.update(SpecificationLevel.UNSPECIFIED, SpecificationLevel.TANGLED)
        assert abs(cal.accuracy - 0.48875) < 0.001

    def test_serialization_roundtrip(self):
        cal = PredictionCalibration(total_predictions=10, correct_predictions=7, accuracy=0.72)
        d = cal.to_dict()
        restored = PredictionCalibration.from_dict(d)
        assert restored.total_predictions == 10
        assert restored.correct_predictions == 7
        assert abs(restored.accuracy - 0.72) < 0.001


class TestCalibrationStore:
    def test_get_creates_new_entry(self, tmp_path):
        store = CalibrationStore(store_path=tmp_path / "cal.json")
        cal = store.get("no_tests")
        assert cal.accuracy == 0.5
        assert cal.total_predictions == 0

    def test_record_and_persist(self, tmp_path):
        path = tmp_path / "cal.json"
        store = CalibrationStore(store_path=path)
        store.record("no_tests", SpecificationLevel.UNSPECIFIED, SpecificationLevel.UNSPECIFIED)
        store.record("no_tests", SpecificationLevel.UNSPECIFIED, SpecificationLevel.TANGLED)

        cal = store.get("no_tests")
        assert cal.total_predictions == 2
        assert cal.correct_predictions == 1

        # Reload from disk
        store2 = CalibrationStore(store_path=path)
        cal2 = store2.get("no_tests")
        assert cal2.total_predictions == 2
        assert cal2.correct_predictions == 1

    def test_missing_file_graceful(self, tmp_path):
        store = CalibrationStore(store_path=tmp_path / "nonexistent" / "cal.json")
        cal = store.get("key")
        assert cal.accuracy == 0.5

    def test_corrupt_file_graceful(self, tmp_path):
        path = tmp_path / "cal.json"
        path.write_text("not valid json")
        store = CalibrationStore(store_path=path)
        cal = store.get("key")
        assert cal.accuracy == 0.5

    def test_multiple_signal_keys(self, tmp_path):
        store = CalibrationStore(store_path=tmp_path / "cal.json")
        store.record("no_tests", SpecificationLevel.UNSPECIFIED, SpecificationLevel.UNSPECIFIED)
        store.record("mid_range", SpecificationLevel.DECOMPOSITION_CANDIDATE, SpecificationLevel.TANGLED)

        assert store.get("no_tests").correct_predictions == 1
        assert store.get("mid_range").correct_predictions == 0
