"""Exact-value assertion tests for mcp_tools/compass_tools.py helpers.

Targets 69 surviving mutants across 17 functions with 0% prior kill rate.
Each test uses minimal mocking and exact value assertions to kill VALUE,
SWAP, BOUNDARY, STATE, and TYPE mutants.
"""

from __future__ import annotations

import json
import os
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

from mcp_tools.compass_tools import (
    _apply_answers,
    _build_hooks_config,
    _deep_merge,
    _impl_check,
    _impl_interview,
    _impl_reset,
    _impl_setup_hooks,
    _impl_status,
    _impl_theory_enter,
    _impl_theory_freeze,
    _impl_update,
    _load_mode_dict,
    _load_mode_obj,
    _refresh_axis_scores,
    _render_targets,
    _save_mode,
    register,
)


def _load_tool_result(json_str):
    import json as _j
    import os as _os
    r = _j.loads(json_str)
    if isinstance(r, dict) and "file" in r and "analysis_id" in r and _os.path.isfile(r.get("file","")):
        with open(r["file"]) as f:

            return _j.loads(f.read())
    return r


# ── Helpers ─────────────────────────────────────────────────────────


def _session(bc: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(behavior_compass=bc if bc is not None else {})


def _gap(interview: bool = False) -> SimpleNamespace:
    return SimpleNamespace(
        to_dict=lambda: {
            "axis_depths": {"problem": 2},
            "spikiness": 0.1,
            "sparse_axes": [],
            "interview_recommended": interview,
        },
        interview_recommended=interview,
        axis_depths={"problem": 2},
    )


def _axis(
    name: str, depth: int = 0, claims: list | None = None, summary: str = ""
) -> SimpleNamespace:
    return SimpleNamespace(name=name, depth=depth, claims=claims or [], summary=summary)


def _claim(text: str = "x", confidence: float = 1.0, origin_facet: str = "") -> SimpleNamespace:
    return SimpleNamespace(text=text, confidence=confidence, origin_facet=origin_facet)


def _compass(
    axes: dict | None = None,
    directives: list | None = None,
    gap_report: Any = None,
    forged_at: float = 0.0,
    frozen: bool = False,
    frozen_hash: str = "",
) -> SimpleNamespace:
    return SimpleNamespace(
        axes=axes or {},
        directives=directives or [],
        gap_report=gap_report or _gap(),
        forged_at=forged_at,
        frozen=frozen,
        frozen_hash=frozen_hash,
    )


def _mode(current: str = "normal") -> SimpleNamespace:
    ms = SimpleNamespace(current=SimpleNamespace(value=current))

    def enter_theory():
        if current == "normal":
            ms.current = SimpleNamespace(value="theory")
            return "normal->theory"
        return None

    def freeze_theory(compass_hash: str):
        if current == "theory":
            ms.current = SimpleNamespace(value="normal")
            return "theory->normal"
        return None

    ms.enter_theory = enter_theory
    ms.freeze_theory = freeze_theory
    ms.to_dict = lambda: {"current": ms.current.value}
    return ms


def _reset_report(deleted: list | None = None) -> SimpleNamespace:
    d = deleted or []
    return SimpleNamespace(
        deleted=d,
        to_dict=lambda: {"deleted": d, "preserved": [], "errors": []},
    )


# ===================================================================
# _load_mode_dict — exact return values
# ===================================================================


class TestLoadModeDictExact:
    def test_returns_exact_mode_state_dict(self) -> None:
        state = {"current": "habit", "extra": 42}
        sess = _session(bc={"mode_state": state})
        with patch(
            "lintgate.controlplane.session_memory.get_or_create_session",
            return_value=sess,
        ):
            result = _load_mode_dict("/root")
        assert result is state
        assert result["current"] == "habit"
        assert result["extra"] == 42

    def test_missing_mode_state_returns_normal_dict(self) -> None:
        sess = _session(bc={"other_key": True})
        with patch(
            "lintgate.controlplane.session_memory.get_or_create_session",
            return_value=sess,
        ):
            result = _load_mode_dict("/root")
        assert result == {"current": "normal"}
        assert isinstance(result, dict)

    def test_exception_returns_exact_default(self) -> None:
        with patch(
            "lintgate.controlplane.session_memory.get_or_create_session",
            side_effect=ImportError("no module"),
        ):
            result = _load_mode_dict("/root")
        assert result == {"current": "normal"}
        assert len(result) == 1

    def test_empty_behavior_compass_returns_default(self) -> None:
        sess = _session(bc={})
        with patch(
            "lintgate.controlplane.session_memory.get_or_create_session",
            return_value=sess,
        ):
            result = _load_mode_dict("/root")
        assert result == {"current": "normal"}


# ===================================================================
# _load_mode_obj — verifies ModeState.from_dict integration
# ===================================================================


class TestLoadModeObjExact:
    def test_habit_mode_creates_correct_state(self) -> None:
        from lintgate.modes.mode_state import CognitiveMode

        with patch(
            "lintgate.compass_helpers._load_mode_dict",
            return_value={"current": "habit", "entered_at": 100.0},
        ):
            result = _load_mode_obj("/root")
        assert result.current == CognitiveMode.HABIT
        assert result.entered_at == 100.0

    def test_unknown_mode_falls_to_normal(self) -> None:
        from lintgate.modes.mode_state import CognitiveMode

        with patch(
            "lintgate.compass_helpers._load_mode_dict",
            return_value={"current": "invalid_mode"},
        ):
            result = _load_mode_obj("/root")
        assert result.current == CognitiveMode.NORMAL

    def test_empty_dict_gives_normal(self) -> None:
        from lintgate.modes.mode_state import CognitiveMode

        with patch("lintgate.compass_helpers._load_mode_dict", return_value={}):
            result = _load_mode_obj("/root")
        assert result.current == CognitiveMode.NORMAL
        assert result.theory_frozen is False
        assert result.frozen_compass_hash == ""
        assert result.exploration_claims_added == 0


# ===================================================================
# _save_mode — verifies persistence side effects
# ===================================================================


class TestSaveModeExact:
    def test_writes_mode_dict_to_session_behavior_compass(self) -> None:
        sess = _session(bc={"existing": True})
        mode_state = SimpleNamespace(to_dict=lambda: {"current": "theory", "entered_at": 99.0})
        with (
            patch(
                "lintgate.controlplane.session_memory.get_or_create_session",
                return_value=sess,
            ),
            patch("lintgate.controlplane.session_memory.save_session") as mock_save,
        ):
            _save_mode("/root", mode_state)
        assert sess.behavior_compass["mode_state"] == {"current": "theory", "entered_at": 99.0}
        assert sess.behavior_compass["existing"] is True
        mock_save.assert_called_once_with(sess)

    def test_exception_in_get_session_silenced(self) -> None:
        with patch(
            "lintgate.controlplane.session_memory.get_or_create_session",
            side_effect=OSError("disk full"),
        ):
            # Should not raise
            _save_mode("/root", SimpleNamespace(to_dict=lambda: {}))

    def test_exception_in_save_session_silenced(self) -> None:
        sess = _session()
        with (
            patch(
                "lintgate.controlplane.session_memory.get_or_create_session",
                return_value=sess,
            ),
            patch(
                "lintgate.controlplane.session_memory.save_session",
                side_effect=RuntimeError("boom"),
            ),
        ):
            _save_mode("/root", SimpleNamespace(to_dict=lambda: {"current": "normal"}))
        # Should not raise, mode_state was still written to session before save failed
        assert sess.behavior_compass["mode_state"] == {"current": "normal"}


# ===================================================================
# _build_hooks_config — exact structure assertions
# ===================================================================


class TestBuildHooksConfigExact:
    def test_exact_six_hook_keys(self) -> None:
        config = _build_hooks_config()
        assert sorted(config.keys()) == sorted(
            [
                "SessionStart",
                "UserPromptSubmit",
                "PreToolUse",
                "PreCompact",
                "Stop",
                "SessionEnd",
            ]
        )

    def test_session_start_exact_structure(self) -> None:
        config = _build_hooks_config()
        entry = config["SessionStart"][0]
        hook = entry["hooks"][0]
        assert hook["type"] == "command"
        assert hook["command"] == "python -m lintgate.hooks.session_start"
        assert hook["timeout"] == 5
        assert "async" not in hook
        assert entry["matcher"] == "startup"

    def test_user_prompt_exact_structure(self) -> None:
        config = _build_hooks_config()
        entry = config["UserPromptSubmit"][0]
        hook = entry["hooks"][0]
        assert hook["command"] == "python -m lintgate.hooks.user_prompt"
        assert hook["timeout"] == 2
        assert "matcher" not in entry

    def test_pre_tool_use_exact_structure(self) -> None:
        config = _build_hooks_config()
        entry = config["PreToolUse"][0]
        hook = entry["hooks"][0]
        assert hook["command"] == "python -m lintgate.hooks.pre_tool"
        assert hook["timeout"] == 3
        assert entry["matcher"] == "Write|Edit|MultiEdit|Bash"

    def test_pre_compact_exact_structure(self) -> None:
        config = _build_hooks_config()
        entry = config["PreCompact"][0]
        hook = entry["hooks"][0]
        assert hook["command"] == "python -m lintgate.hooks.pre_compact"
        assert hook["timeout"] == 5
        assert entry["matcher"] == "auto|manual"

    def test_stop_exact_structure(self) -> None:
        config = _build_hooks_config()
        entry = config["Stop"][0]
        hook = entry["hooks"][0]
        assert hook["command"] == "python -m lintgate.hooks.stop_gate"
        assert hook["timeout"] == 3
        assert "async" not in hook
        assert "matcher" not in entry

    def test_session_end_exact_structure(self) -> None:
        config = _build_hooks_config()
        entry = config["SessionEnd"][0]
        hook = entry["hooks"][0]
        assert hook["command"] == "python -m lintgate.hooks.session_end"
        assert hook["timeout"] == 10
        assert hook["async"] is True

    def test_each_hook_list_has_exactly_one_entry(self) -> None:
        config = _build_hooks_config()
        for key, entries in config.items():
            assert len(entries) == 1, f"{key} should have exactly 1 entry, got {len(entries)}"
            assert len(entries[0]["hooks"]) == 1, f"{key} should have exactly 1 hook"


# ===================================================================
# _deep_merge — exact values and boundary conditions
# ===================================================================


class TestDeepMergeExact:
    def test_nested_dict_recursive_merge(self) -> None:
        base = {"a": {"b": {"c": 1}}}
        override = {"a": {"b": {"d": 2}}}
        result = _deep_merge(base, override)
        assert result == {"a": {"b": {"c": 1, "d": 2}}}

    def test_nested_dict_override_scalar(self) -> None:
        base = {"a": {"b": 1}}
        override = {"a": {"b": 99}}
        result = _deep_merge(base, override)
        assert result["a"]["b"] == 99

    def test_list_dedup_exact_values(self) -> None:
        result = _deep_merge({"x": [1, 2]}, {"x": [2, 3]})
        assert result["x"] == [1, 2, 3]

    def test_list_no_duplicates_added(self) -> None:
        result = _deep_merge({"x": [1, 2, 3]}, {"x": [1, 2, 3]})
        assert result["x"] == [1, 2, 3]

    def test_new_key_in_override_added(self) -> None:
        result = _deep_merge({"a": 1}, {"b": 2, "c": 3})
        assert result == {"a": 1, "b": 2, "c": 3}

    def test_base_not_mutated_with_list(self) -> None:
        base = {"x": [1, 2]}
        original_list = base["x"]
        _deep_merge(base, {"x": [3]})
        assert base["x"] is original_list
        assert base["x"] == [1, 2]

    def test_dict_over_list_replaces(self) -> None:
        result = _deep_merge({"a": [1]}, {"a": {"k": "v"}})
        assert result["a"] == {"k": "v"}

    def test_list_over_dict_replaces(self) -> None:
        result = _deep_merge({"a": {"k": "v"}}, {"a": [1, 2]})
        assert result["a"] == [1, 2]

    def test_three_level_deep_merge(self) -> None:
        base = {"l1": {"l2": {"l3": {"x": 1}}}}
        override = {"l1": {"l2": {"l3": {"y": 2}}}}
        result = _deep_merge(base, override)
        assert result["l1"]["l2"]["l3"] == {"x": 1, "y": 2}


# ===================================================================
# _refresh_axis_scores — exact behavior with compute_axis_depth
# ===================================================================


class TestRefreshAxisScoresExact:
    def test_best_claim_selected_by_confidence_primary(self) -> None:
        c_low = _claim("low conf claim text", confidence=0.3)
        c_high = _claim("high", confidence=0.9)
        axis = _axis("problem", claims=[c_low, c_high])
        state = SimpleNamespace(axes={"problem": axis})
        with patch("lintgate.compass.compute_axis_depth", return_value=1):
            _refresh_axis_scores(state)
        # max by (confidence, len(text)): c_high wins on confidence
        assert axis.summary == "high"

    def test_best_claim_tiebreak_by_text_length(self) -> None:
        c_short = _claim("ab", confidence=1.0)
        c_long = _claim("abcdef", confidence=1.0)
        axis = _axis("world", claims=[c_short, c_long])
        state = SimpleNamespace(axes={"world": axis})
        with patch("lintgate.compass.compute_axis_depth", return_value=2):
            _refresh_axis_scores(state)
        assert axis.summary == "abcdef"
        assert axis.depth == 2

    def test_no_claims_sets_empty_summary_and_depth(self) -> None:
        axis = _axis("implementation", claims=[])
        state = SimpleNamespace(axes={"implementation": axis})
        with patch("lintgate.compass.compute_axis_depth", return_value=0):
            _refresh_axis_scores(state)
        assert axis.summary == ""
        assert axis.depth == 0

    def test_single_claim_becomes_summary(self) -> None:
        c = _claim("only claim", confidence=0.5)
        axis = _axis("solution", claims=[c])
        state = SimpleNamespace(axes={"solution": axis})
        with patch("lintgate.compass.compute_axis_depth", return_value=1):
            _refresh_axis_scores(state)
        assert axis.summary == "only claim"

    def test_iterates_all_axes(self) -> None:
        a1 = _axis("problem", claims=[_claim("p")])
        a2 = _axis("solution", claims=[_claim("s")])
        a3 = _axis("world", claims=[])
        state = SimpleNamespace(axes={"problem": a1, "solution": a2, "world": a3})
        with patch("lintgate.compass.compute_axis_depth", side_effect=[2, 3, 0]):
            _refresh_axis_scores(state)
        assert a1.depth == 2
        assert a2.depth == 3
        assert a3.depth == 0
        assert a1.summary == "p"
        assert a2.summary == "s"
        assert a3.summary == ""


# ===================================================================
# _impl_status — exact return dict structure
# ===================================================================


class TestImplStatusExact:
    def test_no_compass_exact_response(self) -> None:
        with patch("lintgate.compass_io.load_compass", return_value=None):
            result = _impl_status("/root", "/root")
        assert result["status"] == "no_compass"
        assert result["message"] == "No compass found. Run compass_update to extract."
        assert len(result["next_actions"]) == 1
        action = result["next_actions"][0]
        assert action["tool"] == "compass_update"
        assert action["args"] == {"path": "/root", "write": True}

    def test_axes_info_exact_values_with_missing_axes(self) -> None:
        compass = _compass(
            axes={"problem": _axis("problem", depth=3, summary="A" * 200, claims=[_claim()] * 5)},
            directives=[],
            gap_report=_gap(interview=False),
        )
        with (
            patch("lintgate.compass_io.load_compass", return_value=compass),
            patch("lintgate.compass.compute_staleness", return_value=0.0),
            patch("lintgate.compass_helpers._load_mode_dict", return_value={"current": "normal"}),
        ):
            result = _impl_status("/root", "/root")
        # problem axis present
        assert result["axes"]["problem"]["depth"] == 3
        assert result["axes"]["problem"]["claim_count"] == 5
        assert len(result["axes"]["problem"]["summary"]) == 120  # truncated from 200
        # missing axes get zeros
        assert result["axes"]["solution"]["depth"] == 0
        assert result["axes"]["solution"]["claim_count"] == 0
        assert result["axes"]["solution"]["summary"] == ""
        assert result["axes"]["implementation"]["depth"] == 0
        assert result["axes"]["world"]["depth"] == 0

    def test_staleness_boundary_0_8_no_update_suggestion(self) -> None:
        compass = _compass(gap_report=_gap(interview=False))
        with (
            patch("lintgate.compass_io.load_compass", return_value=compass),
            patch("lintgate.compass.compute_staleness", return_value=0.8),
            patch("lintgate.compass_helpers._load_mode_dict", return_value={"current": "normal"}),
        ):
            result = _impl_status("/root", "/root")
        # staleness == 0.8 is NOT > 0.8, so no update suggestion
        assert not any(a.get("tool") == "compass_update" for a in result["next_actions"])

    def test_staleness_just_above_threshold_suggests_update(self) -> None:
        compass = _compass(gap_report=_gap(interview=False))
        with (
            patch("lintgate.compass_io.load_compass", return_value=compass),
            patch("lintgate.compass.compute_staleness", return_value=0.81),
            patch("lintgate.compass_helpers._load_mode_dict", return_value={"current": "normal"}),
        ):
            result = _impl_status("/root", "/root")
        tools = [a["tool"] for a in result["next_actions"]]
        assert "compass_update" in tools

    def test_interview_recommended_suggests_interview(self) -> None:
        compass = _compass(gap_report=_gap(interview=True))
        with (
            patch("lintgate.compass_io.load_compass", return_value=compass),
            patch("lintgate.compass.compute_staleness", return_value=0.5),
            patch("lintgate.compass_helpers._load_mode_dict", return_value={"current": "normal"}),
        ):
            result = _impl_status("/root", "/root")
        tools = [a["tool"] for a in result["next_actions"]]
        assert "compass_interview" in tools

    def test_frozen_field_exact(self) -> None:
        compass = _compass(frozen=True, gap_report=_gap(interview=False))
        with (
            patch("lintgate.compass_io.load_compass", return_value=compass),
            patch("lintgate.compass.compute_staleness", return_value=0.1),
            patch("lintgate.compass_helpers._load_mode_dict", return_value={"current": "normal"}),
        ):
            result = _impl_status("/root", "/root")
        assert result["frozen"] is True

    def test_directives_count_exact(self) -> None:
        compass = _compass(
            directives=[
                SimpleNamespace(kind="toward", text="a"),
                SimpleNamespace(kind="away", text="b"),
            ],
            gap_report=_gap(interview=False),
        )
        with (
            patch("lintgate.compass_io.load_compass", return_value=compass),
            patch("lintgate.compass.compute_staleness", return_value=0.0),
            patch("lintgate.compass_helpers._load_mode_dict", return_value={"current": "normal"}),
        ):
            result = _impl_status("/root", "/root")
        assert result["directives_count"] == 2

    def test_staleness_rounded_to_2_decimals(self) -> None:
        compass = _compass(gap_report=_gap(interview=False))
        with (
            patch("lintgate.compass_io.load_compass", return_value=compass),
            patch("lintgate.compass.compute_staleness", return_value=0.33333),
            patch("lintgate.compass_helpers._load_mode_dict", return_value={"current": "normal"}),
        ):
            result = _impl_status("/root", "/root")
        assert result["staleness"] == 0.33


# ===================================================================
# _impl_check — exact alignment result structure
# ===================================================================


class TestImplCheckExact:
    def test_no_compass_exact_result(self) -> None:
        with patch("lintgate.compass_io.load_compass", return_value=None):
            result = _impl_check("/root", "anything")
        assert result == {
            "aligned": None,
            "message": "Cannot evaluate — no compass loaded. Run compass_update first.",
        }

    def test_empty_compass_aligned_result(self) -> None:
        from lintgate.compass import CompassState

        compass = CompassState()
        with patch("lintgate.compass_io.load_compass", return_value=compass):
            result = _impl_check("/root", "add tests")
        assert result["aligned"] is True
        assert result["violations"] == []
        assert result["warnings"] == []
        assert result["true_north"] == ""

    def test_true_north_truncated_to_120(self) -> None:
        from lintgate.compass import CompassAxis, CompassState

        compass = CompassState(axes={"problem": CompassAxis(name="problem", summary="X" * 200)})
        with patch("lintgate.compass_io.load_compass", return_value=compass):
            result = _impl_check("/root", "action")
        assert len(result["true_north"]) == 120
        assert result["true_north"] == "X" * 120

    def test_away_directive_generates_warning(self) -> None:
        from lintgate.compass import CompassDirective, CompassState

        compass = CompassState(
            directives=[CompassDirective(kind="away", text="Avoid using globals")]
        )
        with patch("lintgate.compass_io.load_compass", return_value=compass):
            result = _impl_check("/root", "using globals everywhere")
        assert result["aligned"] is True  # away = warning, not violation
        assert len(result["warnings"]) == 1
        assert result["violations"] == []

    def test_forbidden_directive_generates_violation(self) -> None:
        from lintgate.compass import CompassDirective, CompassState

        compass = CompassState(
            directives=[CompassDirective(kind="forbidden", text="Never skip tests")]
        )
        with patch("lintgate.compass_io.load_compass", return_value=compass):
            result = _impl_check("/root", "skip tests for speed")
        assert result["aligned"] is False
        assert len(result["violations"]) == 1

    def test_empty_true_north_when_no_problem_axis(self) -> None:
        from lintgate.compass import CompassState

        compass = CompassState(axes={"solution": MagicMock(summary="sol")})
        with patch("lintgate.compass_io.load_compass", return_value=compass):
            result = _impl_check("/root", "action")
        assert result["true_north"] == ""


# ===================================================================
# _impl_update — exact inferred claim routing
# ===================================================================


class TestImplUpdateExact:
    def _patches(self, state=None, inferred=None, render_result=None, compass_hash="hash1"):
        from lintgate.compass import CompassState, GapReport

        if state is None:
            state = CompassState()
            state.gap_report = GapReport()
        if inferred is None:
            inferred = []

        return (
            patch("lintgate.axis_extractor.extract_compass", return_value=state),
            patch("lintgate.code_inference.infer_from_code", return_value=inferred),
            patch("lintgate.compass.compute_compass_hash", return_value=compass_hash),
            patch("lintgate.compass_io.save_compass"),
            patch("lintgate.gap_detector.detect_gaps"),
            patch("lintgate.compass_helpers._refresh_axis_scores"),
            patch("lintgate.compass_helpers._render_targets", return_value=render_result),
            patch("lintgate.compass_io.load_compass", return_value=None),
        )

    def test_no_write_no_written_key(self) -> None:
        p = self._patches()
        with p[0], p[1], p[2], p[3] as save, p[4], p[5], p[6], p[7]:
            result = _impl_update("/root", None, write=False)
        assert "written" not in result
        save.assert_not_called()

    def test_write_sets_written_true(self) -> None:
        p = self._patches()
        with p[0], p[1], p[2], p[3] as save, p[4], p[5], p[6], p[7]:
            result = _impl_update("/root", None, write=True)
        assert result["written"] is True
        save.assert_called_once()

    def test_compass_hash_exact(self) -> None:
        p = self._patches(compass_hash="deadbeef12345678")
        with p[0], p[1], p[2], p[3], p[4], p[5], p[6], p[7]:
            result = _impl_update("/root", None, write=False)
        assert result["compass_hash"] == "deadbeef12345678"

    def test_inferred_claims_count_exact(self) -> None:
        from lintgate.compass import CompassClaim

        inferred = [
            CompassClaim(text="a", origin_facet="core_theory"),
            CompassClaim(text="b", origin_facet="alignment"),
            CompassClaim(text="c", origin_facet="architecture"),
        ]
        p = self._patches(inferred=inferred)
        with p[0], p[1], p[2], p[3], p[4], p[5], p[6], p[7]:
            result = _impl_update("/root", None, write=False)
        assert result["inferred_claims"] == 3

    def test_inferred_claim_unknown_facet_routes_to_world(self) -> None:
        from lintgate.compass import CompassClaim, CompassState, GapReport

        state = CompassState()
        state.gap_report = GapReport()
        inferred = [CompassClaim(text="unknown", origin_facet="nonexistent_facet")]
        p = self._patches(state=state, inferred=inferred)
        with p[0], p[1], p[2], p[3], p[4], p[5], p[6], p[7]:
            _impl_update("/root", None, write=False)
        assert "world" in state.axes
        assert len(state.axes["world"].claims) == 1
        assert state.axes["world"].claims[0].text == "unknown"

    def test_rendered_field_present_when_render_returns_value(self) -> None:
        render = {"targets": ["cursor"], "files": ["out.md"], "written": False}
        p = self._patches(render_result=render)
        with p[0], p[1], p[2], p[3], p[4], p[5], p[6], p[7]:
            result = _impl_update("/root", ["cursor"], write=False)
        assert result["rendered"] == render

    def test_no_rendered_field_when_render_returns_none(self) -> None:
        p = self._patches(render_result=None)
        with p[0], p[1], p[2], p[3], p[4], p[5], p[6], p[7]:
            result = _impl_update("/root", None, write=False)
        assert "rendered" not in result

    def test_preserves_interviewed_claims_from_existing_compass(self) -> None:
        from lintgate.compass import CompassAxis, CompassClaim, CompassState, GapReport

        state = CompassState(axes={"solution": CompassAxis(name="solution")})
        state.gap_report = GapReport()
        existing = CompassState(
            axes={
                "solution": CompassAxis(
                    name="solution",
                    claims=[
                        CompassClaim(
                            text="Why this approach over alternatives?",
                            heading="Why this approach over alternatives?",
                            source="interview:solution",
                            provenance="interviewed",
                        )
                    ],
                )
            }
        )
        p = self._patches(state=state)
        with (
            patch("lintgate.compass_io.load_compass", return_value=existing),
            p[0],
            p[1],
            p[2],
            p[3],
            p[4],
            p[5],
            p[6],
        ):
            result = _impl_update("/root", None, write=False)
        assert result["retained_interview_claims"] == 1
        assert any(c.provenance == "interviewed" for c in state.axes["solution"].claims)


# ===================================================================
# _render_targets — exact boundary conditions
# ===================================================================


class TestRenderTargetsExact:
    def test_none_targets_returns_none(self) -> None:
        assert _render_targets("/root", None, None, False) is None

    def test_empty_list_returns_none(self) -> None:
        assert _render_targets("/root", None, [], False) is None

    def test_write_false_exact_return(self) -> None:
        reg = MagicMock()
        reg.render_for_targets.return_value = {"file.md": "content"}
        with patch("lintgate.renderers.build_default_registry", return_value=reg):
            result = _render_targets("/root", None, ["claude"], write=False)
        assert result == {"targets": ["claude"], "files": ["file.md"], "written": False}

    def test_write_true_creates_files(self, tmp_path: Any) -> None:
        reg = MagicMock()
        reg.render_for_targets.return_value = {"sub/file.txt": "hello"}
        with patch("lintgate.renderers.build_default_registry", return_value=reg):
            result = _render_targets(str(tmp_path), None, ["generic"], write=True)
        assert result is not None
        assert result["written"] is True
        assert (tmp_path / "sub" / "file.txt").read_text() == "hello"

    def test_all_target_with_detected_tools(self) -> None:
        reg = MagicMock()
        reg.detect_tools.return_value = ["cursor", "windsurf"]
        reg.render_for_targets.return_value = {}
        with patch("lintgate.renderers.build_default_registry", return_value=reg):
            result = _render_targets("/root", None, ["all"], write=False)
        reg.detect_tools.assert_called_once_with("/root")
        assert result is not None
        assert result["targets"] == ["cursor", "windsurf"]

    def test_all_target_empty_detection_defaults(self) -> None:
        reg = MagicMock()
        reg.detect_tools.return_value = []
        reg.render_for_targets.return_value = {}
        with patch("lintgate.renderers.build_default_registry", return_value=reg):
            _render_targets("/root", None, ["all"], write=False)
        call_targets = reg.render_for_targets.call_args[0][0]
        assert call_targets == ["claude", "generic"]

    def test_all_target_none_detection_defaults(self) -> None:
        reg = MagicMock()
        reg.detect_tools.return_value = None
        reg.render_for_targets.return_value = {}
        with patch("lintgate.renderers.build_default_registry", return_value=reg):
            _render_targets("/root", None, ["all"], write=False)
        call_targets = reg.render_for_targets.call_args[0][0]
        assert call_targets == ["claude", "generic"]

    def test_exception_returns_error_dict(self) -> None:
        with patch(
            "lintgate.renderers.build_default_registry",
            side_effect=ValueError("bad render"),
        ):
            result = _render_targets("/root", None, ["claude"], write=False)
        assert result is not None
        assert result["error"] == "bad render"


# ===================================================================
# _impl_interview — exact branch coverage
# ===================================================================


class TestImplInterviewExact:
    def test_no_compass_exact_error(self) -> None:
        with patch("lintgate.compass_io.load_compass", return_value=None):
            result = _impl_interview("/root", "/root", None, False)
        assert result["error"] == "No compass found. Run compass_update first."
        assert result["next_actions"][0]["tool"] == "compass_update"
        assert result["next_actions"][0]["args"] == {"path": "/root", "write": True}

    def test_skip_true_exact_response(self) -> None:
        compass = _compass()
        with (
            patch("lintgate.compass_io.load_compass", return_value=compass),
            patch("lintgate.gap_detector.skip_interview") as mock_skip,
            patch("lintgate.compass_io.save_compass") as mock_save,
        ):
            result = _impl_interview("/root", "/root", None, skip=True)
        assert result == {"status": "skipped"}
        mock_skip.assert_called_once_with(compass)
        mock_save.assert_called_once_with("/root", compass)

    def test_answers_provided_exact_response(self) -> None:
        compass = _compass(gap_report=_gap())
        applied = [{"axis": "problem", "question_idx": 0, "claim": "answer text"}]
        with (
            patch("lintgate.compass_io.load_compass", return_value=compass),
            patch("lintgate.compass_helpers._apply_answers", return_value=applied) as mock_apply,
        ):
            result = _impl_interview("/root", "/root", {"problem:0": "answer text"}, skip=False)
        assert result["applied"] == applied
        assert "gap_report" in result
        mock_apply.assert_called_once_with("/root", compass, {"problem:0": "answer text"})

    def test_no_answers_no_skip_returns_questions(self) -> None:
        gap = _gap()
        compass = _compass(gap_report=gap)
        questions = [{"axis": "problem", "question": "Why?", "priority": 1}]
        with (
            patch("lintgate.compass_io.load_compass", return_value=compass),
            patch("lintgate.gap_detector.detect_gaps", return_value=gap),
            patch("lintgate.gap_detector.build_interview", return_value=questions),
        ):
            result = _impl_interview("/root", "/root", None, skip=False)
        assert result["questions"] == questions
        assert result["usage"] == 'Pass answers={"axis:idx": "your answer"} to apply.'
        assert "gap_report" in result


# ===================================================================
# _apply_answers — exact parsing and routing
# ===================================================================


class TestApplyAnswersExact:
    def test_valid_key_parsed_correctly(self) -> None:
        compass = _compass()
        claim = SimpleNamespace(text="result claim")
        with (
            patch("lintgate.gap_detector.apply_answer", return_value=claim) as mock_apply,
            patch("lintgate.compass_io.save_compass") as mock_save,
        ):
            result = _apply_answers("/root", compass, {"solution:2": "my answer"})
        assert result == [{"axis": "solution", "question_idx": 2, "claim": "result claim"}]
        mock_apply.assert_called_once_with(compass, "solution", 2, "my answer")
        mock_save.assert_called_once_with("/root", compass)

    def test_no_colon_skipped(self) -> None:
        compass = _compass()
        with patch("lintgate.compass_io.save_compass"):
            result = _apply_answers("/root", compass, {"nocolon": "answer"})
        assert result == []

    def test_non_integer_index_skipped(self) -> None:
        compass = _compass()
        with patch("lintgate.compass_io.save_compass"):
            result = _apply_answers("/root", compass, {"axis:notanint": "answer"})
        assert result == []

    def test_mixed_valid_and_invalid_keys(self) -> None:
        compass = _compass()
        claim = SimpleNamespace(text="ok")
        with (
            patch("lintgate.gap_detector.apply_answer", return_value=claim),
            patch("lintgate.compass_io.save_compass"),
        ):
            result = _apply_answers(
                "/root",
                compass,
                {
                    "problem:0": "valid",
                    "bad": "no colon",
                    "also:bad:extra": "not parsed",
                },
            )
        # "bad" has no colon -> skipped
        # "also:bad:extra" splits on first colon -> parts[1] = "bad:extra" which fails int()
        # Only "problem:0" should succeed
        assert len(result) == 1
        assert result[0]["axis"] == "problem"
        assert result[0]["question_idx"] == 0

    def test_save_always_called(self) -> None:
        compass = _compass()
        with patch("lintgate.compass_io.save_compass") as mock_save:
            _apply_answers("/root", compass, {})
        mock_save.assert_called_once_with("/root", compass)


# ===================================================================
# _impl_reset — exact scope dispatch and dry_run logic
# ===================================================================


class TestImplResetExact:
    def test_invalid_scope_exact_error(self) -> None:
        result = _impl_reset("/root", "/root", "nonexistent", confirm=False)
        assert result == {"error": "Invalid scope: nonexistent"}

    def test_compass_scope_dry_run_exact(self) -> None:
        report = _reset_report(deleted=[{"path": "/root/c.yaml"}])
        with patch("lintgate.reset.reset_compass_only", return_value=report) as mock_fn:
            result = _impl_reset("/root", "/root", "compass", confirm=False)
        mock_fn.assert_called_once_with("/root", dry_run=True)
        assert result["scope"] == "compass"
        assert result["dry_run"] is True
        assert result["deleted"] == [{"path": "/root/c.yaml"}]
        assert result["next_actions"][0]["tool"] == "compass_reset"
        assert result["next_actions"][0]["args"]["confirm"] is True
        assert result["next_actions"][0]["args"]["scope"] == "compass"
        assert result["next_actions"][0]["args"]["path"] == "/root"

    def test_compass_scope_confirmed_no_next_actions(self) -> None:
        report = _reset_report(deleted=[])
        with patch("lintgate.reset.reset_compass_only", return_value=report):
            result = _impl_reset("/root", "/root", "compass", confirm=True)
        assert result["dry_run"] is False
        assert "next_actions" not in result

    def test_session_scope_dispatch(self) -> None:
        report = _reset_report()
        with patch("lintgate.reset.reset_session_only", return_value=report) as mock_fn:
            result = _impl_reset("/root", "/root", "session", confirm=True)
        mock_fn.assert_called_once_with("/root", dry_run=False)
        assert result["scope"] == "session"

    def test_project_scope_dispatch(self) -> None:
        report = _reset_report()
        with patch("lintgate.reset.reset_project", return_value=report) as mock_fn:
            result = _impl_reset("/root", "/root", "project", confirm=False)
        mock_fn.assert_called_once_with("/root", dry_run=True)
        assert result["scope"] == "project"

    def test_global_scope_dispatch(self) -> None:
        report = _reset_report()
        with patch("lintgate.reset.reset_global", return_value=report) as mock_fn:
            result = _impl_reset("/root", "/root", "global", confirm=True)
        mock_fn.assert_called_once_with(dry_run=False)
        assert result["scope"] == "global"

    def test_dry_run_false_means_confirm_true(self) -> None:
        report = _reset_report()
        with patch("lintgate.reset.reset_compass_only", return_value=report) as mock_fn:
            _impl_reset("/root", "/root", "compass", confirm=True)
        mock_fn.assert_called_once_with("/root", dry_run=False)

    def test_dry_run_no_deletions_no_next_actions(self) -> None:
        report = _reset_report(deleted=[])
        with patch("lintgate.reset.reset_compass_only", return_value=report):
            result = _impl_reset("/root", "/root", "compass", confirm=False)
        # dry_run=True but deleted is empty, so no next_actions
        assert "next_actions" not in result


# ===================================================================
# _impl_theory_enter — exact transitions
# ===================================================================


class TestImplTheoryEnterExact:
    def test_normal_to_theory_exact_result(self) -> None:
        ms = _mode("normal")
        with (
            patch("lintgate.compass_helpers._load_mode_obj", return_value=ms),
            patch("lintgate.compass_helpers._save_mode") as mock_save,
        ):
            result = _impl_theory_enter("/root")
        assert result == {"status": "entered", "mode": "theory", "transition": "normal->theory"}
        mock_save.assert_called_once_with("/root", ms)

    def test_habit_blocked_exact_error(self) -> None:
        ms = _mode("habit")
        with (
            patch("lintgate.compass_helpers._load_mode_obj", return_value=ms),
            patch("lintgate.compass_helpers._save_mode") as mock_save,
        ):
            result = _impl_theory_enter("/root")
        assert "error" in result
        assert "habit" in result["error"]
        mock_save.assert_not_called()

    def test_theory_to_theory_blocked(self) -> None:
        ms = _mode("theory")
        with (
            patch("lintgate.compass_helpers._load_mode_obj", return_value=ms),
            patch("lintgate.compass_helpers._save_mode") as mock_save,
        ):
            result = _impl_theory_enter("/root")
        assert "error" in result
        assert "theory" in result["error"]
        mock_save.assert_not_called()


# ===================================================================
# _impl_theory_freeze — exact freeze behavior
# ===================================================================


class TestImplTheoryFreezeExact:
    def test_freeze_sets_compass_fields(self) -> None:
        from lintgate.compass import CompassAxis, CompassState

        ms = _mode("theory")
        compass = CompassState(
            axes={
                "problem": CompassAxis(name="problem", depth=2),
                "solution": CompassAxis(name="solution", depth=1),
            }
        )
        with (
            patch("lintgate.compass_helpers._load_mode_obj", return_value=ms),
            patch("lintgate.compass_io.load_compass", return_value=compass),
            patch("lintgate.compass.compute_compass_hash", return_value="abc123"),
            patch("lintgate.compass_io.save_compass") as save_c,
            patch("lintgate.compass_helpers._save_mode") as save_m,
        ):
            result = _impl_theory_freeze("/root")
        assert result["status"] == "frozen"
        assert result["compass_hash"] == "abc123"
        assert result["warnings"] == []
        assert compass.frozen is True
        assert compass.frozen_hash == "abc123"
        save_c.assert_called_once_with("/root", compass)
        save_m.assert_called_once_with("/root", ms)

    def test_no_compass_error(self) -> None:
        ms = _mode("theory")
        with (
            patch("lintgate.compass_helpers._load_mode_obj", return_value=ms),
            patch("lintgate.compass_io.load_compass", return_value=None),
        ):
            result = _impl_theory_freeze("/root")
        assert result == {"error": "No compass to freeze."}

    def test_not_in_theory_mode_error(self) -> None:
        from lintgate.compass import CompassState

        ms = _mode("normal")
        compass = CompassState()
        with (
            patch("lintgate.compass_helpers._load_mode_obj", return_value=ms),
            patch("lintgate.compass_io.load_compass", return_value=compass),
            patch("lintgate.compass.compute_compass_hash", return_value="h"),
        ):
            result = _impl_theory_freeze("/root")
        assert "error" in result
        assert "normal" in result["error"]

    def test_empty_required_axes_generate_warnings(self) -> None:
        from lintgate.compass import CompassAxis, CompassState

        ms = _mode("theory")
        # problem has depth=0, solution missing entirely
        compass = CompassState(axes={"problem": CompassAxis(name="problem", depth=0)})
        with (
            patch("lintgate.compass_helpers._load_mode_obj", return_value=ms),
            patch("lintgate.compass_io.load_compass", return_value=compass),
            patch("lintgate.compass.compute_compass_hash", return_value="h"),
            patch("lintgate.compass_io.save_compass"),
            patch("lintgate.compass_helpers._save_mode"),
        ):
            result = _impl_theory_freeze("/root")
        assert result["status"] == "frozen"
        # Both required axes should warn: problem (depth=0) and solution (missing)
        assert len(result["warnings"]) == 2
        assert any("problem" in w for w in result["warnings"])
        assert any("solution" in w for w in result["warnings"])


# ===================================================================
# _impl_setup_hooks — exact file I/O behavior
# ===================================================================


class TestImplSetupHooksExact:
    def test_preview_exact_structure(self, tmp_path: Any) -> None:
        result = _impl_setup_hooks(str(tmp_path), write=False)
        assert result["status"] == "preview"
        assert result["path"] == os.path.join(str(tmp_path), ".claude", "settings.json")
        assert "hooks" in result
        assert result["merged_settings"] is not None
        assert "hooks" in result["merged_settings"]

    def test_write_exact_structure(self, tmp_path: Any) -> None:
        result = _impl_setup_hooks(str(tmp_path), write=True)
        assert result["status"] == "written"
        assert result["merged_settings"] is None
        settings_path = tmp_path / ".claude" / "settings.json"
        assert settings_path.exists()
        content = json.loads(settings_path.read_text())
        assert "hooks" in content
        assert "SessionStart" in content["hooks"]

    def test_merges_existing_preserves_custom(self, tmp_path: Any) -> None:
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        existing = {"custom": True, "hooks": {"MyHook": [{"hooks": [{"type": "x"}]}]}}
        (claude_dir / "settings.json").write_text(json.dumps(existing))
        _impl_setup_hooks(str(tmp_path), write=True)
        content = json.loads((claude_dir / "settings.json").read_text())
        assert content["custom"] is True
        assert "MyHook" in content["hooks"]
        assert "SessionStart" in content["hooks"]

    def test_malformed_json_treated_as_empty(self, tmp_path: Any) -> None:
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        (claude_dir / "settings.json").write_text("{broken")
        result = _impl_setup_hooks(str(tmp_path), write=True)
        assert result["status"] == "written"
        content = json.loads((claude_dir / "settings.json").read_text())
        assert "hooks" in content

    def test_missing_file_treated_as_empty(self, tmp_path: Any) -> None:
        result = _impl_setup_hooks(str(tmp_path), write=False)
        assert result["status"] == "preview"
        assert result["merged_settings"]["hooks"] is not None


# ===================================================================
# register — exact delegation and next_actions wiring
# ===================================================================


class TestRegisterExact:
    def _register(self) -> tuple[dict[str, Any], MagicMock]:
        mcp = MagicMock()
        mcp.tool.return_value = lambda fn: fn
        helpers = {
            "_json_dumps": json.dumps,
            "_validate_project_root": lambda p: p,
        }
        tools = register(mcp, helpers)
        return tools, mcp

    def test_exactly_eight_tools_registered(self) -> None:
        tools, mcp = self._register()
        assert len(tools) == 8
        assert mcp.tool.call_count == 8

    # Register-layer next_actions tests moved to test_scripts_compass_manage.py
    # since the wrapper is now a subprocess shell; those next_actions are built
    # in scripts.compass_manage.cmd_update, not in the MCP register() block.

    def test_all_tool_names_exact(self) -> None:
        tools, _ = self._register()
        assert set(tools.keys()) == {
            "compass_status",
            "compass_check",
            "compass_update",
            "compass_interview",
            "compass_reset",
            "theory_mode_enter",
            "theory_mode_freeze",
            "setup_hooks",
        }


# ---------------------------------------------------------------------------
# Subprocess argv tests (moved from test_mcp_compass_tools.py during test
# cleanup — the MCP wrapper is now a thin subprocess shell; these verify its
# argv assembly without duplicating the exhaustive helper coverage above).
# ---------------------------------------------------------------------------


class TestSubprocessArgv:
    """Verify the MCP wrapper assembles the correct script argv."""

    def _register(self) -> dict[str, Any]:
        mcp = MagicMock()
        mcp.tool.return_value = lambda fn: fn
        return register(mcp, helpers={})

    def _mock_proc(
        self,
        stdout: str = '{"analysis_id":"x","summary":"s","file":"/tmp/x.json"}',
        returncode: int = 0,
    ) -> MagicMock:
        proc = MagicMock()
        proc.stdout = stdout
        proc.stderr = ""
        proc.returncode = returncode
        return proc

    def test_compass_status_argv(self) -> None:
        tools = self._register()
        with patch("subprocess.run", return_value=self._mock_proc()) as run:
            tools["compass_status"](path="/p")
        argv = run.call_args[0][0]
        assert argv[1].endswith("compass_manage.py")
        assert argv[2] == "status"
        assert argv[3] == "/p"

    def test_compass_check_argv(self) -> None:
        tools = self._register()
        with patch("subprocess.run", return_value=self._mock_proc()) as run:
            tools["compass_check"](path="/p", action="do something")
        argv = run.call_args[0][0]
        assert argv[2] == "check"
        assert "--action" in argv
        assert "do something" in argv

    def test_compass_update_flags(self) -> None:
        tools = self._register()
        with patch("subprocess.run", return_value=self._mock_proc()) as run:
            tools["compass_update"](path="/p", targets=["cursor", "claude"], write=True)
        argv = run.call_args[0][0]
        assert argv[2] == "update"
        target_positions = [i for i, a in enumerate(argv) if a == "--target"]
        assert len(target_positions) == 2
        assert argv[target_positions[0] + 1] == "cursor"
        assert argv[target_positions[1] + 1] == "claude"
        assert "--write" in argv

    def test_compass_interview_answers_encoded(self) -> None:
        tools = self._register()
        with patch("subprocess.run", return_value=self._mock_proc()) as run:
            tools["compass_interview"](path="/p", answers={"solution:0": "text"})
        argv = run.call_args[0][0]
        assert argv[2] == "interview"
        assert "--answer" in argv
        assert "solution:0=text" in argv

    def test_compass_interview_skip_flag(self) -> None:
        tools = self._register()
        with patch("subprocess.run", return_value=self._mock_proc()) as run:
            tools["compass_interview"](path="/p", skip=True)
        argv = run.call_args[0][0]
        assert "--skip" in argv

    def test_compass_reset_scope_and_confirm(self) -> None:
        tools = self._register()
        with patch("subprocess.run", return_value=self._mock_proc()) as run:
            tools["compass_reset"](path="/p", scope="session", confirm=True)
        argv = run.call_args[0][0]
        assert argv[2] == "reset"
        assert "--scope" in argv
        assert "session" in argv
        assert "--confirm" in argv

    def test_theory_mode_enter_argv(self) -> None:
        tools = self._register()
        with patch("subprocess.run", return_value=self._mock_proc()) as run:
            tools["theory_mode_enter"](path="/p")
        argv = run.call_args[0][0]
        assert argv[2] == "theory-enter"
        assert argv[3] == "/p"

    def test_theory_mode_freeze_argv(self) -> None:
        tools = self._register()
        with patch("subprocess.run", return_value=self._mock_proc()) as run:
            tools["theory_mode_freeze"](path="/p")
        argv = run.call_args[0][0]
        assert argv[2] == "theory-freeze"

    def test_setup_hooks_write(self) -> None:
        tools = self._register()
        with patch("subprocess.run", return_value=self._mock_proc()) as run:
            tools["setup_hooks"](path="/p", write=True)
        argv = run.call_args[0][0]
        assert argv[2] == "setup-hooks"
        assert "--write" in argv
