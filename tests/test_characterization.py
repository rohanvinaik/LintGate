"""Tests for characterization-backed oracles with provenance."""

from __future__ import annotations

from lintgate.testing.characterization import (
    GoldenCapture,
    Provenance,
    _eval_call_site,
    _try_capture,
    capture_golden,
    corroborate_captures,
    generate_golden_test,
)

# ── Call-site evaluation ──────────────────────────────────────────


class TestEvalCallSite:
    def test_literal_args(self):
        result = _eval_call_site({"args": ["1", "'hello'"], "kwargs": {"x": "True"}})
        assert result is not None
        args, kwargs = result
        assert args == [1, "hello"]
        assert kwargs == {"x": True}

    def test_variable_arg_rejected(self):
        assert _eval_call_site({"args": ["x", "1"]}) is None

    def test_empty_args(self):
        result = _eval_call_site({"args": [], "kwargs": {}})
        assert result is not None
        assert result == ([], {})

    def test_non_string_args_passthrough(self):
        result = _eval_call_site({"args": [42, True]})
        assert result is not None
        assert result[0] == [42, True]

    def test_variable_kwarg_rejected(self):
        assert _eval_call_site({"args": ["1"], "kwargs": {"k": "some_var"}}) is None

    def test_complex_literal(self):
        result = _eval_call_site({"args": ["[1, 2, 3]", "{'a': 1}"]})
        assert result is not None
        assert result[0] == [[1, 2, 3], {"a": 1}]

    # ── Integration: _find_call_sites key convention ─────────────

    def test_positional_args_key(self):
        """_find_call_sites produces 'positional_args'/'keyword_args' keys."""
        site = {"positional_args": ["1", "'hello'"], "keyword_args": {"x": "True"}}
        result = _eval_call_site(site)
        assert result is not None
        args, kwargs = result
        assert args == [1, "hello"]
        assert kwargs == {"x": True}

    def test_positional_args_key_variable_rejected(self):
        site = {"positional_args": ["some_var"], "keyword_args": {}}
        assert _eval_call_site(site) is None

    def test_mixed_keys_positional_preferred(self):
        """When both key conventions present, positional_args takes precedence."""
        site = {
            "positional_args": ["42"],
            "args": ["99"],  # should be ignored
            "keyword_args": {"k": "'v'"},
            "kwargs": {"k": "'other'"},
        }
        result = _eval_call_site(site)
        assert result is not None
        assert result[0] == [42]
        assert result[1] == {"k": "v"}


# ── Golden capture ────────────────────────────────────────────────


class TestTryCapture:
    def test_deterministic_capture(self):
        cap = _try_capture("os.path", "join", ["/tmp", "test"], {})
        assert cap is not None
        assert cap.deterministic is True
        assert "test" in cap.output

    def test_import_failure(self):
        assert _try_capture("nonexistent.module", "func", [], {}) is None

    def test_function_error(self):
        # len() with no args raises TypeError
        assert _try_capture("builtins", "len", [], {}) is None

    def test_builtin_function(self):
        cap = _try_capture("builtins", "abs", [-5], {})
        assert cap is not None
        assert cap.output == "5"
        assert cap.deterministic is True


class TestCaptureGolden:
    def test_with_literal_sites(self):
        sites = [{"args": ["'/tmp'", "'a'"]}]
        captures = capture_golden("os.path", "join", sites)
        assert len(captures) >= 1
        assert any(c.deterministic for c in captures)

    def test_deduplicates(self):
        sites = [{"args": ["'/tmp'", "'a'"]}, {"args": ["'/tmp'", "'a'"]}]
        captures = capture_golden("os.path", "join", sites)
        # Should not have duplicate captures for same args
        # At most: zero-arg (fails for join) + one unique call site
        assert len(captures) <= 2

    def test_skips_unevaluable(self):
        sites = [{"args": ["some_variable", "'x'"]}]
        captures = capture_golden("os.path", "join", sites)
        # Only zero-arg capture (which may fail for join)
        assert all(c.inputs != [] or c.kwargs != {} for c in captures) or len(captures) == 0


# ── Corroboration ─────────────────────────────────────────────────


class TestCorroborateCaptures:
    def _provisional(self, **kwargs):
        return GoldenCapture(
            inputs=[1, 2],
            output="3",
            deterministic=True,
            provenance=Provenance.PROVISIONAL,
            **kwargs,
        )

    def test_pure_deterministic_corroborates(self):
        caps = corroborate_captures([self._provisional()], None, is_pure=True)
        assert caps[0].provenance == Provenance.CORROBORATED
        assert caps[0].corroborating_lens == "pure_deterministic"

    def test_value_killed_corroborates(self):
        state = {"killed_records": [{"category": "VALUE"}]}
        caps = corroborate_captures([self._provisional()], state, is_pure=False)
        assert caps[0].provenance == Provenance.CORROBORATED
        assert caps[0].corroborating_lens == "mutation_value_killed"

    def test_no_evidence_stays_provisional(self):
        caps = corroborate_captures([self._provisional()], None, is_pure=False)
        assert caps[0].provenance == Provenance.PROVISIONAL

    def test_non_deterministic_not_pure_corroborated(self):
        cap = GoldenCapture(
            inputs=[1],
            output="1",
            deterministic=False,
            provenance=Provenance.PROVISIONAL,
        )
        caps = corroborate_captures([cap], None, is_pure=True)
        # Not deterministic → pure alone doesn't corroborate
        assert caps[0].provenance == Provenance.PROVISIONAL

    def test_already_corroborated_preserved(self):
        cap = GoldenCapture(
            inputs=[],
            output="1",
            deterministic=True,
            provenance=Provenance.CORROBORATED,
            corroborating_lens="manual",
        )
        caps = corroborate_captures([cap], None)
        assert caps[0].provenance == Provenance.CORROBORATED
        assert caps[0].corroborating_lens == "manual"

    def test_empty_captures(self):
        assert corroborate_captures([], None) == []

    def test_swap_killed_does_not_corroborate(self):
        state = {"killed_records": [{"category": "SWAP"}]}
        caps = corroborate_captures([self._provisional()], state, is_pure=False)
        assert caps[0].provenance == Provenance.PROVISIONAL


# ── Test generation ───────────────────────────────────────────────


class TestGenerateGoldenTest:
    def test_deterministic_assertion(self):
        cap = GoldenCapture(
            inputs=[1, 2],
            output="3",
            deterministic=True,
            provenance=Provenance.CORROBORATED,
            corroborating_lens="pure_deterministic",
        )
        code = generate_golden_test("mymod::add", [cap])
        assert "from mymod import add" in code
        assert "def test_add_golden():" in code
        assert "assert repr(result) == '3'" in code
        assert "corroborated" in code.lower()

    def test_provisional_tag(self):
        cap = GoldenCapture(
            inputs=[1],
            output="'hello'",
            deterministic=True,
            provenance=Provenance.PROVISIONAL,
        )
        code = generate_golden_test("mod::f", [cap])
        assert "PROVISIONAL" in code
        assert "# provisional" in code

    def test_nondeterministic_assertion(self):
        cap = GoldenCapture(
            inputs=[],
            output="0.5",
            deterministic=False,
            provenance=Provenance.PROVISIONAL,
        )
        code = generate_golden_test("mod::f", [cap])
        assert "is not None" in code
        assert "non-deterministic" in code

    def test_multiple_captures_numbered(self):
        caps = [
            GoldenCapture(inputs=[1], output="1", deterministic=True),
            GoldenCapture(inputs=[2], output="4", deterministic=True),
        ]
        code = generate_golden_test("mod::f", caps)
        assert "test_f_golden_0" in code
        assert "test_f_golden_1" in code

    def test_kwargs_in_call(self):
        cap = GoldenCapture(
            inputs=[1],
            kwargs={"key": "val"},
            output="ok",
            deterministic=True,
            provenance=Provenance.CORROBORATED,
        )
        code = generate_golden_test("mod::f", [cap])
        assert "key='val'" in code

    def test_empty_captures(self):
        assert generate_golden_test("mod::f", []) == ""

    def test_no_module_path(self):
        cap = GoldenCapture(inputs=[], output="1", deterministic=True)
        code = generate_golden_test("f", [cap])
        assert "from" not in code
        assert "def test_f_golden():" in code
