"""Comprehensive tests for mcp_tools/compass_tools.py — targeting uncovered symbols.

Covers all _impl_* functions, helper functions (_load_mode_dict, _load_mode_obj,
_save_mode, _build_hooks_config, _deep_merge, _refresh_axis_scores, _render_targets,
_apply_answers), and the register() function.

All lazy imports inside function bodies are patched at their *source* module paths
(e.g. ``lintgate.compass_io.load_compass``) rather than on the compass_tools module.
"""

from __future__ import annotations

import json
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

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _make_gap_report(interview_recommended: bool = False) -> SimpleNamespace:
    return SimpleNamespace(
        to_dict=lambda: {
            "axis_depths": {},
            "spikiness": 0.0,
            "sparse_axes": [],
            "interview_recommended": interview_recommended,
        },
        interview_recommended=interview_recommended,
        axis_depths={},
    )


def _make_compass_state(
    *,
    axes: dict[str, Any] | None = None,
    directives: list | None = None,
    gap_report: Any | None = None,
    forged_at: float = 0.0,
    frozen: bool = False,
    frozen_hash: str = "",
) -> SimpleNamespace:
    return SimpleNamespace(
        axes=axes or {},
        directives=directives or [],
        gap_report=gap_report or _make_gap_report(),
        forged_at=forged_at,
        frozen=frozen,
        frozen_hash=frozen_hash,
    )


def _make_axis(
    name: str, depth: int = 0, claims: list | None = None, summary: str = ""
) -> SimpleNamespace:
    return SimpleNamespace(name=name, depth=depth, claims=claims or [], summary=summary)


def _make_claim(
    text: str = "test claim", confidence: float = 1.0, origin_facet: str = ""
) -> SimpleNamespace:
    return SimpleNamespace(text=text, confidence=confidence, origin_facet=origin_facet)


def _make_session(behavior_compass: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        behavior_compass=behavior_compass if behavior_compass is not None else {}
    )


def _make_mode_state(current: str = "normal") -> SimpleNamespace:
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

    def to_dict():
        return {"current": ms.current.value}

    ms.enter_theory = enter_theory
    ms.freeze_theory = freeze_theory
    ms.to_dict = to_dict
    return ms


def _make_reset_report(deleted: list | None = None) -> SimpleNamespace:
    d = deleted or []
    return SimpleNamespace(
        deleted=d,
        to_dict=lambda: {"deleted": d, "preserved": [], "errors": []},
    )


# ===================================================================
# _load_mode_dict
# ===================================================================


class TestLoadModeDict:
    def test_returns_mode_state_from_session(self) -> None:
        session = _make_session(behavior_compass={"mode_state": {"current": "theory"}})
        with patch(
            "lintgate.controlplane.session_memory.get_or_create_session",
            return_value=session,
        ):
            result = _load_mode_dict("/fake/root")
        assert result == {"current": "theory"}

    def test_returns_default_on_exception(self) -> None:
        with patch(
            "lintgate.controlplane.session_memory.get_or_create_session",
            side_effect=RuntimeError("fail"),
        ):
            result = _load_mode_dict("/fake/root")
        assert result == {"current": "normal"}

    def test_returns_default_when_mode_state_missing(self) -> None:
        session = _make_session(behavior_compass={})
        with patch(
            "lintgate.controlplane.session_memory.get_or_create_session",
            return_value=session,
        ):
            result = _load_mode_dict("/fake/root")
        assert result == {"current": "normal"}


# ===================================================================
# _load_mode_obj
# ===================================================================


class TestLoadModeObj:
    @patch("mcp_tools.compass_tools._load_mode_dict", return_value={"current": "theory"})
    def test_returns_theory_mode_object(self, _mock: MagicMock) -> None:
        from lintgate.modes.mode_state import CognitiveMode

        result = _load_mode_obj("/fake")
        assert result.current == CognitiveMode.THEORY

    @patch("mcp_tools.compass_tools._load_mode_dict", return_value={"current": "normal"})
    def test_returns_normal_mode_object(self, _mock: MagicMock) -> None:
        from lintgate.modes.mode_state import CognitiveMode

        result = _load_mode_obj("/fake")
        assert result.current == CognitiveMode.NORMAL


# ===================================================================
# _save_mode
# ===================================================================


class TestSaveMode:
    def test_saves_mode_state_to_session(self) -> None:
        session = _make_session(behavior_compass={})
        mode_state = SimpleNamespace(to_dict=lambda: {"current": "theory"})
        with (
            patch(
                "lintgate.controlplane.session_memory.get_or_create_session",
                return_value=session,
            ),
            patch("lintgate.controlplane.session_memory.save_session") as mock_save,
        ):
            _save_mode("/fake", mode_state)
        assert session.behavior_compass["mode_state"] == {"current": "theory"}
        mock_save.assert_called_once_with(session)

    def test_silently_handles_exception(self) -> None:
        with patch(
            "lintgate.controlplane.session_memory.get_or_create_session",
            side_effect=RuntimeError("boom"),
        ):
            _save_mode("/fake", SimpleNamespace(to_dict=lambda: {}))


# ===================================================================
# _build_hooks_config
# ===================================================================


class TestBuildHooksConfig:
    def test_returns_all_hook_keys(self) -> None:
        config = _build_hooks_config()
        expected = {
            "SessionStart",
            "UserPromptSubmit",
            "PreToolUse",
            "PreCompact",
            "Stop",
            "SessionEnd",
        }
        assert set(config.keys()) == expected

    def test_session_start_has_matcher(self) -> None:
        config = _build_hooks_config()
        assert config["SessionStart"][0]["matcher"] == "startup"

    def test_pre_tool_use_has_matcher(self) -> None:
        config = _build_hooks_config()
        assert config["PreToolUse"][0]["matcher"] == "Write|Edit|MultiEdit|Bash"

    def test_session_end_is_async(self) -> None:
        config = _build_hooks_config()
        hook = config["SessionEnd"][0]["hooks"][0]
        assert hook.get("async") is True

    def test_all_hooks_are_command_type(self) -> None:
        config = _build_hooks_config()
        for entries in config.values():
            for entry in entries:
                for hook in entry["hooks"]:
                    assert hook["type"] == "command"
                    assert hook["command"].startswith("python -m lintgate.hooks.")
                    assert isinstance(hook["timeout"], int)

    def test_stop_has_no_matcher(self) -> None:
        assert "matcher" not in _build_hooks_config()["Stop"][0]

    def test_user_prompt_has_no_matcher(self) -> None:
        assert "matcher" not in _build_hooks_config()["UserPromptSubmit"][0]


# ===================================================================
# _deep_merge
# ===================================================================


class TestDeepMerge:
    def test_merges_flat_dicts(self) -> None:
        assert _deep_merge({"a": 1}, {"b": 2}) == {"a": 1, "b": 2}

    def test_override_wins_for_scalar(self) -> None:
        assert _deep_merge({"a": 1}, {"a": 2}) == {"a": 2}

    def test_deep_merges_nested_dicts(self) -> None:
        base = {"hooks": {"pre": {"timeout": 5}}}
        override = {"hooks": {"post": {"timeout": 10}}}
        result = _deep_merge(base, override)
        assert result == {"hooks": {"pre": {"timeout": 5}, "post": {"timeout": 10}}}

    def test_list_merge_deduplicates(self) -> None:
        assert _deep_merge({"i": [1, 2, 3]}, {"i": [3, 4, 5]}) == {"i": [1, 2, 3, 4, 5]}

    def test_list_merge_preserves_order(self) -> None:
        assert _deep_merge({"i": ["a", "b"]}, {"i": ["b", "c"]})["i"] == ["a", "b", "c"]

    def test_does_not_mutate_base(self) -> None:
        base = {"a": 1, "nested": {"x": 10}}
        _deep_merge(base, {"nested": {"y": 20}})
        assert "y" not in base["nested"]

    def test_type_mismatch_override_wins(self) -> None:
        assert _deep_merge({"a": [1]}, {"a": {"k": "v"}}) == {"a": {"k": "v"}}

    def test_empty_base(self) -> None:
        assert _deep_merge({}, {"a": 1}) == {"a": 1}

    def test_empty_override(self) -> None:
        assert _deep_merge({"a": 1}, {}) == {"a": 1}


# ===================================================================
# _refresh_axis_scores
# ===================================================================


class TestRefreshAxisScores:
    def test_updates_depth_and_summary(self) -> None:
        c1 = _make_claim("short", confidence=0.5)
        c2 = _make_claim("longer claim text here", confidence=0.9)
        axis = _make_axis("problem", claims=[c1, c2])
        state = SimpleNamespace(axes={"problem": axis})

        with patch("lintgate.compass.compute_axis_depth", return_value=2):
            _refresh_axis_scores(state)

        assert axis.depth == 2
        assert axis.summary == "longer claim text here"

    def test_empty_claims_gives_empty_summary(self) -> None:
        axis = _make_axis("problem", claims=[])
        state = SimpleNamespace(axes={"problem": axis})

        with patch("lintgate.compass.compute_axis_depth", return_value=0):
            _refresh_axis_scores(state)

        assert axis.summary == ""

    def test_multiple_axes(self) -> None:
        a1 = _make_axis("problem", claims=[_make_claim("a")])
        a2 = _make_axis("solution", claims=[_make_claim("b", confidence=0.8)])
        state = SimpleNamespace(axes={"problem": a1, "solution": a2})

        with patch("lintgate.compass.compute_axis_depth", return_value=1):
            _refresh_axis_scores(state)

        assert a1.depth == 1 and a2.depth == 1
        assert a1.summary == "a" and a2.summary == "b"

    def test_best_claim_by_confidence_then_length(self) -> None:
        c1 = _make_claim("ab", confidence=0.9)
        c2 = _make_claim("abcdef", confidence=0.9)
        axis = _make_axis("world", claims=[c1, c2])
        state = SimpleNamespace(axes={"world": axis})

        with patch("lintgate.compass.compute_axis_depth", return_value=1):
            _refresh_axis_scores(state)

        assert axis.summary == "abcdef"


# ===================================================================
# _impl_status
# ===================================================================


class TestImplStatus:
    def test_no_compass_returns_no_compass_status(self) -> None:
        with (
            patch("lintgate.compass_io.load_compass", return_value=None),
            patch(
                "mcp_tools.compass_tools._load_mode_dict",
                return_value={"current": "normal"},
            ),
        ):
            result = _impl_status("/fake", "/fake")
        assert result["status"] == "no_compass"
        assert any(a["tool"] == "compass_update" for a in result["next_actions"])

    def test_returns_axes_info_and_mode(self) -> None:
        gap = _make_gap_report(interview_recommended=False)
        compass = _make_compass_state(
            axes={
                "problem": _make_axis("problem", depth=2, summary="Problem summary"),
                "solution": _make_axis("solution", depth=1, summary="Sol"),
            },
            directives=[SimpleNamespace(kind="toward", text="go")],
            gap_report=gap,
        )
        with (
            patch("lintgate.compass_io.load_compass", return_value=compass),
            patch("lintgate.compass.compute_staleness", return_value=0.5),
            patch(
                "mcp_tools.compass_tools._load_mode_dict",
                return_value={"current": "theory"},
            ),
        ):
            result = _impl_status("/fake", "/fake")

        assert result["axes"]["problem"]["depth"] == 2
        assert result["directives_count"] == 1
        assert result["staleness"] == 0.5
        assert result["mode"] == "theory"

    def test_stale_compass_suggests_update(self) -> None:
        gap = _make_gap_report(interview_recommended=True)
        compass = _make_compass_state(axes={}, directives=[], gap_report=gap)
        with (
            patch("lintgate.compass_io.load_compass", return_value=compass),
            patch("lintgate.compass.compute_staleness", return_value=0.9),
            patch(
                "mcp_tools.compass_tools._load_mode_dict",
                return_value={"current": "normal"},
            ),
        ):
            result = _impl_status("/fake", "/fake")

        tools = [a["tool"] for a in result["next_actions"]]
        assert "compass_update" in tools
        assert "compass_interview" in tools

    def test_fresh_compass_no_next_actions(self) -> None:
        gap = _make_gap_report(interview_recommended=False)
        compass = _make_compass_state(axes={}, directives=[], gap_report=gap, frozen=True)
        with (
            patch("lintgate.compass_io.load_compass", return_value=compass),
            patch("lintgate.compass.compute_staleness", return_value=0.3),
            patch(
                "mcp_tools.compass_tools._load_mode_dict",
                return_value={"current": "normal"},
            ),
        ):
            result = _impl_status("/fake", "/fake")

        assert result["next_actions"] == []
        assert result["frozen"] is True


# ===================================================================
# _impl_check
# ===================================================================


class TestImplCheck:
    def test_no_compass_returns_none_aligned(self) -> None:
        with patch("lintgate.compass_io.load_compass", return_value=None):
            result = _impl_check("/fake", "some action")
        assert result["aligned"] is None

    def test_aligned_action(self) -> None:
        from lintgate.compass import CompassState

        compass = CompassState()
        with patch("lintgate.compass_io.load_compass", return_value=compass):
            result = _impl_check("/fake", "add tests")
        assert result["aligned"] is True
        assert result["violations"] == []

    def test_forbidden_directive_triggers_violation(self) -> None:
        from lintgate.compass import CompassDirective, CompassState

        compass = CompassState(
            directives=[CompassDirective(kind="forbidden", text="Never disable linting")]
        )
        with patch("lintgate.compass_io.load_compass", return_value=compass):
            result = _impl_check("/fake", "disable linting globally")
        assert result["aligned"] is False
        assert len(result["violations"]) > 0

    def test_true_north_is_truncated(self) -> None:
        from lintgate.compass import CompassAxis, CompassState

        long_summary = "A" * 200
        compass = CompassState(axes={"problem": CompassAxis(name="problem", summary=long_summary)})
        with patch("lintgate.compass_io.load_compass", return_value=compass):
            result = _impl_check("/fake", "some action")
        assert len(result["true_north"]) <= 120


# ===================================================================
# _impl_update
# ===================================================================


class TestImplUpdate:
    def _patch_update_deps(
        self,
        state=None,
        inferred=None,
        render_result=None,
        compass_hash="abc123",
    ):
        """Context manager that patches all lazy imports used by _impl_update."""
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
            patch("mcp_tools.compass_tools._refresh_axis_scores"),
            patch("mcp_tools.compass_tools._render_targets", return_value=render_result),
        )

    def test_basic_no_write(self) -> None:
        patches = self._patch_update_deps()
        with (
            patches[0],
            patches[1],
            patches[2],
            patches[3] as save_mock,
            patches[4],
            patches[5],
            patches[6],
        ):
            result = _impl_update("/fake", None, write=False)
        assert result["compass_hash"] == "abc123"
        assert result["inferred_claims"] == 0
        save_mock.assert_not_called()
        assert "written" not in result

    def test_with_write(self) -> None:
        patches = self._patch_update_deps()
        with (
            patches[0],
            patches[1],
            patches[2],
            patches[3] as save_mock,
            patches[4],
            patches[5],
            patches[6],
        ):
            result = _impl_update("/fake", None, write=True)
        assert result["written"] is True
        save_mock.assert_called_once()

    def test_with_render_targets(self) -> None:
        from lintgate.compass import CompassClaim

        render_val = {
            "targets": ["claude"],
            "files": [".claude/CLAUDE.md"],
            "written": True,
        }
        patches = self._patch_update_deps(
            inferred=[CompassClaim(text="inferred", origin_facet="core_theory")],
            render_result=render_val,
        )
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
            result = _impl_update("/fake", ["claude"], write=True)
        assert result["rendered"] == render_val
        assert result["inferred_claims"] == 1

    def test_inferred_claims_routed_to_correct_axis(self) -> None:
        from lintgate.compass import CompassAxis, CompassClaim, CompassState, GapReport

        state = CompassState(axes={"problem": CompassAxis(name="problem")})
        state.gap_report = GapReport()
        patches = self._patch_update_deps(
            state=state,
            inferred=[
                CompassClaim(text="core", origin_facet="core_theory"),
                CompassClaim(text="unknown", origin_facet="unknown_facet"),
            ],
        )
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
            _impl_update("/fake", None, write=False)

        assert len(state.axes["problem"].claims) == 1
        assert "world" in state.axes


# ===================================================================
# _render_targets
# ===================================================================


class TestRenderTargets:
    def test_returns_none_when_no_targets(self) -> None:
        assert _render_targets("/fake", None, None, False) is None

    def test_returns_none_for_empty_list(self) -> None:
        assert _render_targets("/fake", None, [], False) is None

    def test_renders_specified_targets(self) -> None:
        registry = MagicMock()
        registry.render_for_targets.return_value = {".claude/CLAUDE.md": "# Content"}
        with patch("lintgate.renderers.build_default_registry", return_value=registry):
            result = _render_targets("/fake", None, ["claude"], write=False)
        assert result is not None
        assert result["targets"] == ["claude"]
        assert result["written"] is False

    def test_writes_files_when_write_true(self, tmp_path: Any) -> None:
        registry = MagicMock()
        registry.render_for_targets.return_value = {"output.md": "# Hello"}
        with patch("lintgate.renderers.build_default_registry", return_value=registry):
            result = _render_targets(str(tmp_path), None, ["claude"], write=True)
        assert result is not None and result["written"] is True
        assert (tmp_path / "output.md").read_text() == "# Hello"

    def test_all_target_triggers_detection(self) -> None:
        registry = MagicMock()
        registry.detect_tools.return_value = ["claude", "cursor"]
        registry.render_for_targets.return_value = {}
        with patch("lintgate.renderers.build_default_registry", return_value=registry):
            _render_targets("/fake", None, ["all"], write=False)
        registry.detect_tools.assert_called_once_with("/fake")

    def test_all_target_defaults_when_detect_empty(self) -> None:
        registry = MagicMock()
        registry.detect_tools.return_value = []
        registry.render_for_targets.return_value = {}
        with patch("lintgate.renderers.build_default_registry", return_value=registry):
            _render_targets("/fake", None, ["all"], write=False)
        call_args = registry.render_for_targets.call_args
        assert call_args[0][0] == ["claude", "generic"]

    def test_returns_error_on_exception(self) -> None:
        with patch(
            "lintgate.renderers.build_default_registry",
            side_effect=RuntimeError("boom"),
        ):
            result = _render_targets("/fake", None, ["claude"], write=False)
        assert result is not None and "boom" in result["error"]


# ===================================================================
# _impl_interview
# ===================================================================


class TestImplInterview:
    def test_no_compass_returns_error(self) -> None:
        with patch("lintgate.compass_io.load_compass", return_value=None):
            result = _impl_interview("/fake", "/fake", None, False)
        assert "error" in result
        assert "next_actions" in result

    def test_skip_sets_status_skipped(self) -> None:
        compass = _make_compass_state()
        with (
            patch("lintgate.compass_io.load_compass", return_value=compass),
            patch("lintgate.gap_detector.skip_interview") as mock_skip,
            patch("lintgate.compass_io.save_compass") as mock_save,
        ):
            result = _impl_interview("/fake", "/fake", None, skip=True)
        assert result["status"] == "skipped"
        mock_skip.assert_called_once_with(compass)
        mock_save.assert_called_once()

    def test_answers_are_applied(self) -> None:
        gap = _make_gap_report()
        compass = _make_compass_state(gap_report=gap)
        applied = [{"axis": "problem", "question_idx": 0, "claim": "answer"}]
        with (
            patch("lintgate.compass_io.load_compass", return_value=compass),
            patch("mcp_tools.compass_tools._apply_answers", return_value=applied),
        ):
            result = _impl_interview("/fake", "/fake", {"problem:0": "answer"}, skip=False)
        assert result["applied"] == applied

    def test_no_answers_returns_questions(self) -> None:
        gap = _make_gap_report()
        compass = _make_compass_state(gap_report=gap)
        questions = [{"axis": "problem", "question": "What?", "priority": 1}]
        with (
            patch("lintgate.compass_io.load_compass", return_value=compass),
            patch("lintgate.gap_detector.detect_gaps", return_value=gap),
            patch("lintgate.gap_detector.build_interview", return_value=questions),
        ):
            result = _impl_interview("/fake", "/fake", None, skip=False)
        assert "questions" in result
        assert "usage" in result


# ===================================================================
# _apply_answers
# ===================================================================


class TestApplyAnswers:
    def test_applies_valid_answers(self) -> None:
        claim = SimpleNamespace(text="applied claim")
        compass = _make_compass_state()
        with (
            patch("lintgate.gap_detector.apply_answer", return_value=claim) as mock_apply,
            patch("lintgate.compass_io.save_compass") as mock_save,
        ):
            result = _apply_answers("/fake", compass, {"problem:0": "my answer"})
        assert len(result) == 1
        assert result[0] == {
            "axis": "problem",
            "question_idx": 0,
            "claim": "applied claim",
        }
        mock_apply.assert_called_once_with(compass, "problem", 0, "my answer")
        mock_save.assert_called_once()

    def test_skips_malformed_key_no_colon(self) -> None:
        compass = _make_compass_state()
        with patch("lintgate.compass_io.save_compass"):
            result = _apply_answers("/fake", compass, {"badkey": "answer"})
        assert result == []

    def test_skips_non_integer_index(self) -> None:
        compass = _make_compass_state()
        with patch("lintgate.compass_io.save_compass"):
            result = _apply_answers("/fake", compass, {"problem:abc": "answer"})
        assert result == []

    def test_multiple_answers(self) -> None:
        c1 = SimpleNamespace(text="c1")
        c2 = SimpleNamespace(text="c2")
        compass = _make_compass_state()
        with (
            patch("lintgate.gap_detector.apply_answer", side_effect=[c1, c2]),
            patch("lintgate.compass_io.save_compass"),
        ):
            result = _apply_answers("/fake", compass, {"problem:0": "a", "solution:1": "b"})
        assert len(result) == 2


# ===================================================================
# _impl_reset
# ===================================================================


class TestImplReset:
    def test_compass_scope_dry_run(self) -> None:
        report = _make_reset_report(deleted=[{"path": "/fake/compass.yaml"}])
        with patch("lintgate.reset.reset_compass_only", return_value=report) as mock_fn:
            result = _impl_reset("/fake", "/fake", "compass", confirm=False)
        assert result["scope"] == "compass"
        assert result["dry_run"] is True
        assert "next_actions" in result
        mock_fn.assert_called_once_with("/fake", dry_run=True)

    def test_compass_scope_confirmed(self) -> None:
        report = _make_reset_report()
        with patch("lintgate.reset.reset_compass_only", return_value=report) as mock_fn:
            result = _impl_reset("/fake", "/fake", "compass", confirm=True)
        assert result["dry_run"] is False
        mock_fn.assert_called_once_with("/fake", dry_run=False)

    def test_session_scope(self) -> None:
        report = _make_reset_report()
        with patch("lintgate.reset.reset_session_only", return_value=report):
            result = _impl_reset("/fake", "/fake", "session", confirm=True)
        assert result["scope"] == "session"

    def test_project_scope(self) -> None:
        report = _make_reset_report()
        with patch("lintgate.reset.reset_project", return_value=report):
            result = _impl_reset("/fake", "/fake", "project", confirm=True)
        assert result["scope"] == "project"

    def test_global_scope(self) -> None:
        report = _make_reset_report()
        with patch("lintgate.reset.reset_global", return_value=report):
            result = _impl_reset("/fake", "/fake", "global", confirm=True)
        assert result["scope"] == "global"

    def test_invalid_scope_returns_error(self) -> None:
        result = _impl_reset("/fake", "/fake", "invalid_scope", confirm=False)
        assert "error" in result

    def test_dry_run_with_deletions_suggests_confirm(self) -> None:
        report = _make_reset_report(deleted=[{"path": "file.yaml"}])
        with patch("lintgate.reset.reset_compass_only", return_value=report):
            result = _impl_reset("/fake", "/fake", "compass", confirm=False)
        assert result["next_actions"][0]["args"]["confirm"] is True


# ===================================================================
# _impl_theory_enter
# ===================================================================


class TestImplTheoryEnter:
    def test_enter_from_normal(self) -> None:
        ms = _make_mode_state("normal")
        with (
            patch("mcp_tools.compass_tools._load_mode_obj", return_value=ms),
            patch("mcp_tools.compass_tools._save_mode") as mock_save,
        ):
            result = _impl_theory_enter("/fake")
        assert result["status"] == "entered"
        assert result["mode"] == "theory"
        assert result["transition"] == "normal->theory"
        mock_save.assert_called_once()

    def test_enter_from_habit_blocked(self) -> None:
        ms = _make_mode_state("habit")
        with (
            patch("mcp_tools.compass_tools._load_mode_obj", return_value=ms),
            patch("mcp_tools.compass_tools._save_mode") as mock_save,
        ):
            result = _impl_theory_enter("/fake")
        assert "error" in result
        mock_save.assert_not_called()


# ===================================================================
# _impl_theory_freeze
# ===================================================================


class TestImplTheoryFreeze:
    def test_freeze_from_theory(self) -> None:
        from lintgate.compass import CompassAxis, CompassState

        ms = _make_mode_state("theory")
        compass = CompassState(
            axes={
                "problem": CompassAxis(name="problem", depth=2),
                "solution": CompassAxis(name="solution", depth=1),
            }
        )
        with (
            patch("mcp_tools.compass_tools._load_mode_obj", return_value=ms),
            patch("lintgate.compass_io.load_compass", return_value=compass),
            patch("lintgate.compass.compute_compass_hash", return_value="frozen_hash"),
            patch("lintgate.compass_io.save_compass") as save_c,
            patch("mcp_tools.compass_tools._save_mode") as save_m,
        ):
            result = _impl_theory_freeze("/fake")

        assert result["status"] == "frozen"
        assert result["compass_hash"] == "frozen_hash"
        assert compass.frozen is True
        assert compass.frozen_hash == "frozen_hash"
        save_c.assert_called_once()
        save_m.assert_called_once()

    def test_freeze_no_compass(self) -> None:
        ms = _make_mode_state("theory")
        with (
            patch("mcp_tools.compass_tools._load_mode_obj", return_value=ms),
            patch("lintgate.compass_io.load_compass", return_value=None),
        ):
            result = _impl_theory_freeze("/fake")
        assert "error" in result
        assert "no compass" in result["error"].lower()

    def test_freeze_not_in_theory_mode(self) -> None:
        from lintgate.compass import CompassState

        ms = _make_mode_state("normal")
        compass = CompassState()
        with (
            patch("mcp_tools.compass_tools._load_mode_obj", return_value=ms),
            patch("lintgate.compass_io.load_compass", return_value=compass),
            patch("lintgate.compass.compute_compass_hash", return_value="h"),
        ):
            result = _impl_theory_freeze("/fake")
        assert "error" in result

    def test_freeze_warns_on_empty_required_axes(self) -> None:
        from lintgate.compass import CompassAxis, CompassState

        ms = _make_mode_state("theory")
        compass = CompassState(
            axes={
                "problem": CompassAxis(name="problem", depth=0),
                "solution": CompassAxis(name="solution", depth=2),
            }
        )
        with (
            patch("mcp_tools.compass_tools._load_mode_obj", return_value=ms),
            patch("lintgate.compass_io.load_compass", return_value=compass),
            patch("lintgate.compass.compute_compass_hash", return_value="h"),
            patch("lintgate.compass_io.save_compass"),
            patch("mcp_tools.compass_tools._save_mode"),
        ):
            result = _impl_theory_freeze("/fake")
        assert result["status"] == "frozen"
        assert any("problem" in w for w in result["warnings"])


# ===================================================================
# _impl_setup_hooks
# ===================================================================


class TestImplSetupHooks:
    def test_preview_mode(self, tmp_path: Any) -> None:
        result = _impl_setup_hooks(str(tmp_path), write=False)
        assert result["status"] == "preview"
        assert result["merged_settings"] is not None

    def test_write_mode_creates_file(self, tmp_path: Any) -> None:
        result = _impl_setup_hooks(str(tmp_path), write=True)
        assert result["status"] == "written"
        settings_path = tmp_path / ".claude" / "settings.json"
        assert settings_path.exists()
        content = json.loads(settings_path.read_text())
        assert "hooks" in content

    def test_write_returns_none_merged_settings(self, tmp_path: Any) -> None:
        result = _impl_setup_hooks(str(tmp_path), write=True)
        assert result["merged_settings"] is None

    def test_merges_with_existing_settings(self, tmp_path: Any) -> None:
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        existing = {
            "customSetting": True,
            "hooks": {"ExistingHook": [{"hooks": [{"type": "custom"}]}]},
        }
        (claude_dir / "settings.json").write_text(json.dumps(existing))

        _impl_setup_hooks(str(tmp_path), write=True)
        content = json.loads((claude_dir / "settings.json").read_text())
        assert content["customSetting"] is True
        assert "ExistingHook" in content["hooks"]
        assert "SessionStart" in content["hooks"]

    def test_handles_malformed_existing_settings(self, tmp_path: Any) -> None:
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        (claude_dir / "settings.json").write_text("not json {{{")
        result = _impl_setup_hooks(str(tmp_path), write=True)
        assert result["status"] == "written"


# ===================================================================
# register()
# ===================================================================


class TestRegister:
    def _register(self) -> tuple[dict[str, Any], MagicMock]:
        mcp = MagicMock()
        mcp.tool.return_value = lambda fn: fn
        helpers = {
            "_json_dumps": json.dumps,
            "_validate_project_root": lambda p: p,
        }
        tools = register(mcp, helpers)
        return tools, mcp

    def test_registers_all_eight_tools(self) -> None:
        tools, mcp = self._register()
        expected = {
            "compass_status",
            "compass_check",
            "compass_update",
            "compass_interview",
            "compass_reset",
            "theory_mode_enter",
            "theory_mode_freeze",
            "setup_hooks",
        }
        assert set(tools.keys()) == expected
        assert mcp.tool.call_count == 8

    def test_compass_status_delegates(self) -> None:
        tools, _ = self._register()
        with patch("mcp_tools.compass_tools._impl_status", return_value={"status": "ok"}) as m:
            raw = tools["compass_status"](path="/p")
        m.assert_called_once_with("/p", "/p")
        assert json.loads(raw) == {"status": "ok"}

    def test_compass_check_delegates(self) -> None:
        tools, _ = self._register()
        with patch("mcp_tools.compass_tools._impl_check", return_value={"aligned": True}) as m:
            tools["compass_check"](path="/p", action="test")
        m.assert_called_once_with("/p", "test")

    def test_compass_update_adds_next_actions_interview(self) -> None:
        tools, _ = self._register()
        update_result = {
            "compass_hash": "x",
            "axes": {},
            "gap_report": {"interview_recommended": True},
            "inferred_claims": 0,
        }
        with patch("mcp_tools.compass_tools._impl_update", return_value=update_result):
            result = json.loads(tools["compass_update"](path="/p", write=False))
        action_tools = [a["tool"] for a in result["next_actions"]]
        assert "compass_interview" in action_tools
        assert "compass_update" in action_tools  # write=False suggests re-run

    def test_compass_update_no_interview_when_not_recommended(self) -> None:
        tools, _ = self._register()
        update_result = {
            "compass_hash": "x",
            "axes": {},
            "gap_report": {"interview_recommended": False},
            "inferred_claims": 0,
            "written": True,
        }
        with patch("mcp_tools.compass_tools._impl_update", return_value=update_result):
            result = json.loads(tools["compass_update"](path="/p", write=True))
        action_tools = [a["tool"] for a in result["next_actions"]]
        assert "compass_interview" not in action_tools
        assert "compass_update" not in action_tools

    def test_setup_hooks_delegates(self) -> None:
        tools, _ = self._register()
        with patch(
            "mcp_tools.compass_tools._impl_setup_hooks",
            return_value={"status": "preview"},
        ) as m:
            tools["setup_hooks"](path="/p", write=False)
        m.assert_called_once_with("/p", False)

    def test_theory_mode_enter_delegates(self) -> None:
        tools, _ = self._register()
        with patch(
            "mcp_tools.compass_tools._impl_theory_enter",
            return_value={"status": "entered"},
        ) as m:
            tools["theory_mode_enter"](path="/p")
        m.assert_called_once_with("/p")

    def test_theory_mode_freeze_delegates(self) -> None:
        tools, _ = self._register()
        with patch(
            "mcp_tools.compass_tools._impl_theory_freeze",
            return_value={"status": "frozen"},
        ) as m:
            tools["theory_mode_freeze"](path="/p")
        m.assert_called_once_with("/p")
