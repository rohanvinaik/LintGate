"""Static specification predictor with calibration.

Predicts a function's SpecificationLevel from signal convergence (test
effectiveness + AST category map + purity) without running mutation testing.
Predictions are deterministic — no LLM calls, no network, no subprocess.

Calibration tracks prediction accuracy per decision-tree path using an
exponential moving average, persisted to ~/.lintgate/prediction_calibration.json.
"""

from __future__ import annotations

import ast
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from lintgate.mutation.state import SpecificationLevel

if TYPE_CHECKING:
    from lintgate.linters.performance_checks.algebra_types import PurityResult
    from lintgate.linters.performance_checks.manifest import PropertyManifest
    from lintgate.linters.test_effectiveness.types import (
        FunctionEffectiveness,
        TestEffectivenessManifest,
    )

logger = logging.getLogger(__name__)

# ── Data types ──────────────────────────────────────────────────────────


@dataclass
class SpecificationPrediction:
    """Static prediction of a function's specification level from signal convergence."""

    function_id: str
    predicted_level: SpecificationLevel
    confidence: float  # 0.0-1.0
    needs_verification: bool  # True when confidence < 0.65
    category_predictions: dict[str, str]  # category -> "survive"|"killed"|"uncertain"
    signals_used: list[str]  # audit trail


# ── AST-based category map builder ──────────────────────────────────────


def _is_string_expr(node: ast.AST) -> bool:
    return isinstance(node, ast.JoinedStr) or (
        isinstance(node, ast.Constant) and isinstance(node.value, str)
    )


def _infer_category(node: ast.AST) -> str | None:
    if isinstance(node, ast.BinOp) and isinstance(
        node.op,
        (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow),
    ):
        if _is_string_expr(node.left) or _is_string_expr(node.right):
            return "string"
        return "arithmetic"
    if isinstance(node, ast.BoolOp):
        return "conditional"
    if isinstance(node, ast.Compare):
        return "conditional"
    if isinstance(node, ast.Constant):
        if isinstance(node.value, str):
            return "string"
        if isinstance(node.value, (int, float, complex)):
            return "number"
    return None


def _iter_function_body(node: ast.AST):
    """Iterate children of a function body, skipping nested functions/classes."""
    for child in ast.iter_child_nodes(node):
        if isinstance(
            child,
            (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda),
        ):
            continue
        yield child
        yield from _iter_function_body(child)


def build_function_category_map(source: str, file_path: str) -> dict[str, dict[str, str]]:
    """Build per-function category maps from source code.

    Returns ``{function_name: {mutant_id: category}}``.
    The mutant IDs are synthetic (``func::N``) since this is a static
    approximation — they don't correspond to real mutmut IDs.
    """
    try:
        tree = ast.parse(source, filename=file_path)
    except SyntaxError:
        return {}

    result: dict[str, dict[str, str]] = {}

    class _Visitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self._class_stack: list[str] = []

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            self._class_stack.append(node.name)
            self.generic_visit(node)
            self._class_stack.pop()

        def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
            qualname = (
                f"{'.'.join(self._class_stack)}.{node.name}"
                if self._class_stack
                else node.name
            )
            cat_map: dict[str, str] = {}
            counter = 0
            for child in _iter_function_body(node):
                category = _infer_category(child)
                if category:
                    counter += 1
                    cat_map[f"{qualname}::{counter}"] = category
            result[qualname] = cat_map
            self.generic_visit(node)

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            self._visit_function(node)

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            self._visit_function(node)

    _Visitor().visit(tree)
    return result


# ── Per-category prediction ─────────────────────────────────────────────

# Assertion kinds that kill arithmetic/number mutants
_ARITHMETIC_KILLERS = frozenset({"equality", "comparison", "range_check"})
# Assertion kinds that kill string mutants
_STRING_KILLERS = frozenset({"string_contains", "regex_match"})
# Structural-only assertion kinds
_STRUCTURAL_KINDS = frozenset({
    "is_none", "is_not_none", "is_true", "is_false",
    "isinstance_check", "hasattr_check",
})


def _predict_category_outcomes(
    categories: set[str],
    teff: FunctionEffectiveness | None,
) -> dict[str, str]:
    """Predict survive/killed/uncertain for each unique category."""
    if teff is None or not teff.assertions:
        return dict.fromkeys(categories, "uncertain")

    assertion_kinds = {a.kind.value for a in teff.assertions}
    all_structural = assertion_kinds <= _STRUCTURAL_KINDS

    predictions: dict[str, str] = {}
    for cat in categories:
        if all_structural:
            predictions[cat] = "survive"
        elif (cat in ("arithmetic", "number") and assertion_kinds & _ARITHMETIC_KILLERS) or (cat == "string" and assertion_kinds & _STRING_KILLERS):  # noqa: SIM114
            predictions[cat] = "killed"
        else:
            predictions[cat] = "uncertain"
    return predictions


# ── Core prediction function ────────────────────────────────────────────

# Decision tree path names (used as calibration signal keys)
_PATH_NO_TESTS = "no_tests"
_PATH_STRONG_FEW_CATS = "strong_assertions_few_categories"
_PATH_STRONG_MANY_CATS = "strong_assertions_many_categories"
_PATH_STRUCTURAL_MANY_CATS = "structural_only_many_categories"
_PATH_STRONG_SINGLE_CAT = "strong_single_category"
_PATH_MID_RANGE = "mid_range"
_PATH_FALLBACK = "fallback"


def predict_specification_level(
    func_key: str,
    category_map: dict[str, str],
    teff: FunctionEffectiveness | None,
    purity: PurityResult | None,
) -> SpecificationPrediction:
    """Predict a function's specification level from static signals.

    Decision tree (deterministic, no LLM):

    1. No test data → UNSPECIFIED, confidence=0.95
    2. Strong assertions + few categories → NEARLY_SPECIFIED, confidence=0.65
    3. Structural-only + many categories → TANGLED, confidence=0.70
    4. Strong tests + single category → NEARLY_SPECIFIED, confidence=0.60
    5. Mid-range semantic ratio → DECOMPOSITION_CANDIDATE, confidence=0.50
    6. Otherwise → UNSPECIFIED, confidence=0.30
    """
    signals: list[str] = []
    unique_categories = set(category_map.values())
    num_categories = len(unique_categories)

    if purity is not None:
        signals.append(f"purity={purity.is_pure}")

    # 1. No test data
    if teff is None or teff.test_count == 0:
        signals.append("no_tests")
        cat_preds = _predict_category_outcomes(unique_categories, None)
        return SpecificationPrediction(
            function_id=func_key,
            predicted_level=SpecificationLevel.UNSPECIFIED,
            confidence=0.95,
            needs_verification=False,
            category_predictions=cat_preds,
            signals_used=signals,
        )

    semantic_ratio = teff.semantic_ratio
    signals.append(f"semantic_ratio={semantic_ratio:.2f}")
    signals.append(f"unique_categories={num_categories}")
    signals.append(f"test_count={teff.test_count}")

    weakness = teff.weakness_taxonomy
    if weakness is not None:
        signals.append(f"weakness={weakness.value}")

    cat_preds = _predict_category_outcomes(unique_categories, teff)

    # 2. Strong assertions + few categories
    if semantic_ratio > 0.8 and num_categories <= 2:
        signals.append(f"path={_PATH_STRONG_FEW_CATS}")
        return SpecificationPrediction(
            function_id=func_key,
            predicted_level=SpecificationLevel.NEARLY_SPECIFIED,
            confidence=0.65,
            needs_verification=True,
            category_predictions=cat_preds,
            signals_used=signals,
        )

    # 2b. Strong assertions + many categories (well-tested complex function)
    if semantic_ratio > 0.6 and num_categories >= 3:
        signals.append(f"path={_PATH_STRONG_MANY_CATS}")
        return SpecificationPrediction(
            function_id=func_key,
            predicted_level=SpecificationLevel.DECOMPOSITION_CANDIDATE,
            confidence=0.55,
            needs_verification=True,
            category_predictions=cat_preds,
            signals_used=signals,
        )

    # 3. Structural-only + many categories
    from lintgate.linters.test_effectiveness.types import EffectivenessWeakness

    if weakness in (EffectivenessWeakness.STRUCTURAL_ONLY, EffectivenessWeakness.GENUINELY_WEAK) and num_categories >= 3:
        signals.append(f"path={_PATH_STRUCTURAL_MANY_CATS}")
        return SpecificationPrediction(
            function_id=func_key,
            predicted_level=SpecificationLevel.TANGLED,
            confidence=0.70,
            needs_verification=True,
            category_predictions=cat_preds,
            signals_used=signals,
        )

    # 4. Strong tests + single category
    if semantic_ratio > 0.6 and num_categories == 1:
        signals.append(f"path={_PATH_STRONG_SINGLE_CAT}")
        return SpecificationPrediction(
            function_id=func_key,
            predicted_level=SpecificationLevel.NEARLY_SPECIFIED,
            confidence=0.60,
            needs_verification=True,
            category_predictions=cat_preds,
            signals_used=signals,
        )

    # 5. Mid-range semantic ratio
    if 0.3 <= semantic_ratio <= 0.6:
        signals.append(f"path={_PATH_MID_RANGE}")
        return SpecificationPrediction(
            function_id=func_key,
            predicted_level=SpecificationLevel.DECOMPOSITION_CANDIDATE,
            confidence=0.50,
            needs_verification=True,
            category_predictions=cat_preds,
            signals_used=signals,
        )

    # 6. Fallback
    signals.append(f"path={_PATH_FALLBACK}")
    return SpecificationPrediction(
        function_id=func_key,
        predicted_level=SpecificationLevel.UNSPECIFIED,
        confidence=0.30,
        needs_verification=True,
        category_predictions=cat_preds,
        signals_used=signals,
    )


# ── File-level prediction ───────────────────────────────────────────────


def predict_for_file(
    file_path: str,
    property_manifest: PropertyManifest | None,
    teff_manifest: TestEffectivenessManifest | None,
    project_root: str | None = None,
) -> dict[str, SpecificationPrediction]:
    """Predict specification levels for all functions in a file.

    Returns predictions keyed by function ID (``relpath::funcname`` when
    *project_root* is provided, otherwise ``file_path::funcname``).
    """
    try:
        source = Path(file_path).read_text("utf-8")
    except OSError:
        return {}

    func_category_maps = build_function_category_map(source, file_path)
    if not func_category_maps:
        return {}

    predictions: dict[str, SpecificationPrediction] = {}

    for func_name, cat_map in func_category_maps.items():
        # Build canonical function ID
        if project_root:
            relpath = os.path.relpath(file_path, project_root)
            func_id = f"{relpath}::{func_name}"
        else:
            func_id = f"{file_path}::{func_name}"

        # Look up test effectiveness
        teff = _lookup_teff(func_name, func_id, teff_manifest)

        # Look up purity
        purity = _lookup_purity(func_name, func_id, property_manifest)

        predictions[func_id] = predict_specification_level(
            func_key=func_id,
            category_map=cat_map,
            teff=teff,
            purity=purity,
        )

    return predictions


def _lookup_teff(
    func_name: str,
    func_id: str,
    teff_manifest: TestEffectivenessManifest | None,
) -> FunctionEffectiveness | None:
    """Look up test effectiveness data for a function."""
    if teff_manifest is None:
        return None

    # Try exact match first
    if func_id in teff_manifest.functions:
        return teff_manifest.functions[func_id]

    # Try matching by function name suffix
    for key, fe in teff_manifest.functions.items():
        if key.endswith(f"::{func_name}") or fe.function_name == func_name:
            return fe

    return None


def _lookup_purity(
    func_name: str,
    func_id: str,
    property_manifest: PropertyManifest | None,
) -> PurityResult | None:
    """Look up purity result for a function."""
    if property_manifest is None:
        return None

    # Try exact match first
    if func_id in property_manifest.functions:
        return property_manifest.functions[func_id].purity

    # Try matching by function name suffix
    for key, fp in property_manifest.functions.items():
        if key.endswith(f"::{func_name}"):
            return fp.purity

    return None


# ── Calibration (Step 6) ───────────────────────────────────────────────


@dataclass
class PredictionCalibration:
    """Tracks prediction accuracy for a single decision-tree path."""

    total_predictions: int = 0
    correct_predictions: int = 0
    accuracy: float = 0.5  # start neutral

    def update(self, predicted: SpecificationLevel, actual: SpecificationLevel) -> None:
        self.total_predictions += 1
        if predicted == actual:
            self.correct_predictions += 1
        self.accuracy = 0.85 * self.accuracy + 0.15 * (1.0 if predicted == actual else 0.0)

    def to_dict(self) -> dict:
        return {
            "total_predictions": self.total_predictions,
            "correct_predictions": self.correct_predictions,
            "accuracy": round(self.accuracy, 4),
        }

    @classmethod
    def from_dict(cls, data: dict) -> PredictionCalibration:
        return cls(
            total_predictions=data.get("total_predictions", 0),
            correct_predictions=data.get("correct_predictions", 0),
            accuracy=data.get("accuracy", 0.5),
        )


class CalibrationStore:
    """Persists prediction accuracy per signal combination."""

    def __init__(self, store_path: Path | str | None = None):
        self._path = Path(store_path) if store_path else Path.home() / ".lintgate" / "prediction_calibration.json"
        self._data: dict[str, PredictionCalibration] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text("utf-8"))
            for key, entry in raw.items():
                self._data[key] = PredictionCalibration.from_dict(entry)
        except (json.JSONDecodeError, OSError):
            self._data = {}

    def save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        try:
            payload = {k: v.to_dict() for k, v in self._data.items()}
            self._path.write_text(json.dumps(payload, indent=2), "utf-8")
        except OSError:
            pass

    def get(self, signal_key: str) -> PredictionCalibration:
        if signal_key not in self._data:
            self._data[signal_key] = PredictionCalibration()
        return self._data[signal_key]

    def record(self, signal_key: str, predicted: SpecificationLevel, actual: SpecificationLevel) -> None:
        cal = self.get(signal_key)
        cal.update(predicted, actual)
        self.save()
