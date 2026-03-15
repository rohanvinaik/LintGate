"""Mutation-targeted tests for prescriptive_spec resolver functions."""

from __future__ import annotations

import os
import tempfile

from lintgate.specification.prescriptive_spec import (
    PredicateOp,
    _build_func_index,
    _find_function_at,
    _match_claims_to_symbols,
    _scan_pspec_stubs,
    compile_claim,
)

# ── _find_function_at ────────────────────────────────────────────────


class TestFindFunctionAt:
    def test_finds_function_right_after_annotation(self):
        source = "# PSPEC: toward:pure\ndef compute(x):\n    return x\n"
        assert _find_function_at(source, 0) == "compute"

    def test_finds_function_few_lines_after(self):
        source = "# PSPEC: toward:pure\n\n\ndef compute(x):\n    return x\n"
        assert _find_function_at(source, 0) == "compute"

    def test_returns_none_when_no_function(self):
        source = "# just a comment\nx = 1\ny = 2\n"
        assert _find_function_at(source, 0) is None

    def test_returns_none_on_syntax_error(self):
        source = "def broken(\n"
        assert _find_function_at(source, 0) is None

    def test_finds_async_function(self):
        source = "# PSPEC: safe\nasync def handler(req):\n    return 200\n"
        assert _find_function_at(source, 0) == "handler"

    def test_does_not_match_distant_function(self):
        source = "\n" * 10 + "def far_away():\n    pass\n"
        # annotation_line=0, function at line 11 — distance > 3
        assert _find_function_at(source, 0) is None

    def test_boundary_distance_exactly_3(self):
        """Function 3 lines after annotation should still match."""
        # annotation at line 0 → annotation_line + 1 = 1
        # function at line 4 → abs(4 - 1) = 3 → should match
        # 2 blank lines between annotation and def
        source = "# PSPEC: x\n\n\ndef f():\n    pass\n"
        assert _find_function_at(source, 0) == "f"


# ── _scan_pspec_stubs ────────────────────────────────────────────────


class TestScanPspecStubs:
    def test_finds_annotated_stub(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "mod.py")
            with open(src, "w") as f:
                f.write("# PSPEC: toward:pure\ndef compute(x):\n    raise NotImplementedError\n")

            stubs = _scan_pspec_stubs(tmp)
            assert len(stubs) == 1
            assert "compute" in stubs[0][0]
            assert stubs[0][1] == "toward:pure"

    def test_skips_hidden_dirs(self):
        with tempfile.TemporaryDirectory() as tmp:
            hidden = os.path.join(tmp, ".hidden")
            os.makedirs(hidden)
            src = os.path.join(hidden, "mod.py")
            with open(src, "w") as f:
                f.write("# PSPEC: x\ndef secret():\n    pass\n")

            stubs = _scan_pspec_stubs(tmp)
            assert len(stubs) == 0

    def test_skips_pycache(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = os.path.join(tmp, "__pycache__")
            os.makedirs(cache)
            src = os.path.join(cache, "mod.py")
            with open(src, "w") as f:
                f.write("# PSPEC: x\ndef cached():\n    pass\n")

            stubs = _scan_pspec_stubs(tmp)
            assert len(stubs) == 0

    def test_no_annotation_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "mod.py")
            with open(src, "w") as f:
                f.write("def plain():\n    return 1\n")

            stubs = _scan_pspec_stubs(tmp)
            assert len(stubs) == 0

    def test_multiple_annotations(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "mod.py")
            with open(src, "w") as f:
                f.write(
                    "# PSPEC: toward:pure\ndef a():\n    pass\n\n"
                    "# PSPEC: forbidden:mutate\ndef b():\n    pass\n"
                )

            stubs = _scan_pspec_stubs(tmp)
            assert len(stubs) == 2


# ── _build_func_index ────────────────────────────────────────────────


class TestBuildFuncIndex:
    def test_indexes_public_functions(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "core.py")
            with open(src, "w") as f:
                f.write("def compute(x):\n    return x\n\ndef validate(y):\n    return True\n")

            index = _build_func_index(tmp)
            func_names = {k.split("::")[-1] for k in index}
            assert "compute" in func_names
            assert "validate" in func_names

    def test_skips_private_functions(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "core.py")
            with open(src, "w") as f:
                f.write("def _helper():\n    pass\ndef public():\n    pass\n")

            index = _build_func_index(tmp)
            func_names = {k.split("::")[-1] for k in index}
            assert "_helper" not in func_names
            assert "public" in func_names

    def test_skips_test_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "test_core.py")
            with open(src, "w") as f:
                f.write("def test_something():\n    assert True\n")

            index = _build_func_index(tmp)
            assert len(index) == 0

    def test_depth_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            deep = os.path.join(tmp, "a", "b", "c")
            os.makedirs(deep)
            src = os.path.join(deep, "mod.py")
            with open(src, "w") as f:
                f.write("def deep_func():\n    pass\n")

            index = _build_func_index(tmp)
            func_names = {k.split("::")[-1] for k in index}
            assert "deep_func" not in func_names

    def test_handles_syntax_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "broken.py")
            with open(src, "w") as f:
                f.write("def broken(\n")

            # Should not raise, just skip
            index = _build_func_index(tmp)
            assert len(index) == 0


# ── _match_claims_to_symbols ─────────────────────────────────────────


class _FakeDirective:
    def __init__(self, text: str, kind: str = "toward"):
        self.text = text
        self.kind = kind


class _FakeClaim:
    def __init__(self, text: str, confidence: float = 0.8):
        self.text = text
        self.confidence = confidence


class _FakeAxis:
    def __init__(self, claims: list[_FakeClaim]):
        self.claims = claims


class _FakeCompass:
    def __init__(
        self,
        directives: list[_FakeDirective] | None = None,
        axes: dict[str, _FakeAxis] | None = None,
    ):
        self.directives = directives or []
        self.axes = axes or {}


class TestMatchClaimsToSymbols:
    def test_matches_function_name_in_directive(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "core.py")
            with open(src, "w") as f:
                f.write("def validate(x):\n    return x > 0\n")

            compass = _FakeCompass(
                directives=[_FakeDirective("validate must be pure")]
            )
            results = _match_claims_to_symbols(compass, {}, tmp, set())
            func_names = [r.target_key.split("::")[-1] for r in results]
            assert "validate" in func_names

    def test_matches_from_theory_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "engine.py")
            with open(src, "w") as f:
                f.write("def process(data):\n    return data\n")

            theory = {
                "core": {
                    "claims": [
                        {"text": "process must handle errors", "confidence": 0.8}
                    ]
                }
            }
            results = _match_claims_to_symbols(_FakeCompass(), theory, tmp, set())
            func_names = [r.target_key.split("::")[-1] for r in results]
            assert "process" in func_names

    def test_skips_low_confidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "core.py")
            with open(src, "w") as f:
                f.write("def transform(x):\n    return x\n")

            theory = {
                "core": {
                    "claims": [
                        {"text": "transform might be useful", "confidence": 0.3}
                    ]
                }
            }
            results = _match_claims_to_symbols(_FakeCompass(), theory, tmp, set())
            assert len(results) == 0

    def test_deduplicates_with_seen_set(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "core.py")
            with open(src, "w") as f:
                f.write("def compute(x):\n    return x\n")

            compass = _FakeCompass(
                directives=[_FakeDirective("compute must be safe")]
            )
            seen = {"core.py::compute"}
            results = _match_claims_to_symbols(compass, {}, tmp, seen)
            # Already in seen, should not be added again
            assert len(results) == 0

    def test_skips_short_symbols(self):
        """Symbols <= 3 chars are filtered as stopwords."""
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "core.py")
            with open(src, "w") as f:
                f.write("def run(x):\n    return x\n")

            compass = _FakeCompass(
                directives=[_FakeDirective("run the tests")]
            )
            # "run" is only 3 chars, should be filtered
            results = _match_claims_to_symbols(compass, {}, tmp, set())
            assert len(results) == 0

    def test_axis_claims_matched(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "analysis.py")
            with open(src, "w") as f:
                f.write("def analyze(data):\n    return data\n")

            compass = _FakeCompass(
                axes={"problem": _FakeAxis([_FakeClaim("analyze should be deterministic")])}
            )
            results = _match_claims_to_symbols(compass, {}, tmp, set())
            func_names = [r.target_key.split("::")[-1] for r in results]
            assert "analyze" in func_names


# ── compile_claim dedup ──────────────────────────────────────────────


class TestCompileClaimDedup:
    """Tests for compile_claim deduplication behavior."""

    def test_duplicate_patterns_deduped(self):
        """'must be pure and no side effects' should produce one PURE, not two."""
        p = compile_claim("must be pure and no side effects")
        # Both patterns match PURE, but dedup by (op, subject, value)
        if p.op == PredicateOp.AND:
            pure_count = sum(1 for c in p.operands if c.op == PredicateOp.PURE)
            assert pure_count == 1
        else:
            # Only one match total (correctly deduped to single pred)
            assert p.op == PredicateOp.PURE

    def test_non_matching_returns_custom(self):
        p = compile_claim("this is totally unrecognizable")
        assert p.op == PredicateOp.CUSTOM
        assert "unrecognizable" in p.description

    def test_single_match_returns_raw_predicate(self):
        """Single match should NOT wrap in AND."""
        p = compile_claim("must return dict")
        assert p.op == PredicateOp.IS_TYPE
        assert p.op != PredicateOp.AND
