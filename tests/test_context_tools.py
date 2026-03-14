"""Tests for mcp_tools/context_tools.py helper functions."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from mcp_tools.context_tools import (
    _do_bootstrap,
    _do_build_theory_pack,
    _do_extract_theory,
    _do_patch_apply,
    _do_patch_review,
)


# ---------------------------------------------------------------------------
# _do_patch_review
# ---------------------------------------------------------------------------


class TestDoPatchReview:
    def test_no_pending_patches(self):
        session = MagicMock()
        session.pending_patches = []
        with patch(
            "lintgate.controlplane.session_memory.get_or_create_session",
            return_value=session,
        ):
            result = _do_patch_review("/tmp/proj")
            assert result["pending_count"] == 0
            assert result["message"] == "No pending context patches."

    def test_skips_non_pending_patches(self):
        session = MagicMock()
        session.pending_patches = [{"status": "applied", "patch_id": "p1"}]
        with patch(
            "lintgate.controlplane.session_memory.get_or_create_session",
            return_value=session,
        ):
            result = _do_patch_review("/tmp/proj")
            assert result["pending_count"] == 0

    def test_generates_preview_for_pending_patch(self):
        patch_dict = {
            "status": "pending",
            "patch_id": "abc",
            "section_id": "machine_rules",
            "trigger": "test",
            "evidence": "ev",
            "rationale": "because",
        }
        session = MagicMock()
        session.pending_patches = [patch_dict]

        mock_patch_obj = MagicMock()
        mock_patch_obj.trigger = "test"
        mock_patch_obj.evidence = "ev"
        mock_patch_obj.patch_id = "abc"
        mock_patch_obj.section_id = "machine_rules"
        mock_patch_obj.rationale = "because"

        refreshed = MagicMock()
        refreshed.patch_id = "abc"
        refreshed.section_id = "machine_rules"
        refreshed.trigger = "test"
        refreshed.rationale = "refreshed reason"

        with (
            patch(
                "lintgate.controlplane.session_memory.get_or_create_session",
                return_value=session,
            ),
            patch(
                "lintgate.context_bootstrap.ContextPatch.from_dict",
                return_value=mock_patch_obj,
            ),
            patch(
                "lintgate.context_bootstrap.generate_context_patch",
                return_value=refreshed,
            ),
            patch(
                "lintgate.context_bootstrap.apply_context_patch",
                return_value={"diff_preview": "--- old\n+++ new"},
            ),
        ):
            result = _do_patch_review("/tmp/proj")
            assert result["pending_count"] == 1
            assert len(result["patches"]) == 1
            assert result["patches"][0]["patch_id"] == "abc"
            assert result["patches"][0]["diff_preview"] == "--- old\n+++ new"
            assert result["patches"][0]["status"] == "pending"

    def test_marks_no_op_when_refresh_returns_none(self):
        patch_dict = {
            "status": "pending",
            "patch_id": "xyz",
            "section_id": "do_dont",
            "trigger": "t",
            "evidence": "e",
            "rationale": "r",
        }
        session = MagicMock()
        session.pending_patches = [patch_dict]

        mock_patch_obj = MagicMock()
        mock_patch_obj.trigger = "t"
        mock_patch_obj.evidence = "e"
        mock_patch_obj.patch_id = "xyz"
        mock_patch_obj.section_id = "do_dont"
        mock_patch_obj.rationale = "r"

        with (
            patch(
                "lintgate.controlplane.session_memory.get_or_create_session",
                return_value=session,
            ),
            patch(
                "lintgate.context_bootstrap.ContextPatch.from_dict",
                return_value=mock_patch_obj,
            ),
            patch(
                "lintgate.context_bootstrap.generate_context_patch",
                return_value=None,
            ),
        ):
            result = _do_patch_review("/tmp/proj")
            assert result["patches"][0]["status"] == "no_op"
            assert result["patches"][0]["diff_preview"] is None


# ---------------------------------------------------------------------------
# _do_patch_apply
# ---------------------------------------------------------------------------


class TestDoPatchApply:
    def test_no_matching_patches(self):
        session = MagicMock()
        session.pending_patches = []
        with patch(
            "lintgate.controlplane.session_memory.get_or_create_session",
            return_value=session,
        ):
            result = _do_patch_apply("/tmp/proj", None, False)
            assert result["applied"] == 0
            assert result["message"] == "No matching pending patches."

    def test_filters_by_patch_ids(self):
        session = MagicMock()
        session.pending_patches = [
            {"status": "pending", "patch_id": "a"},
            {"status": "pending", "patch_id": "b"},
        ]
        with patch(
            "lintgate.controlplane.session_memory.get_or_create_session",
            return_value=session,
        ):
            result = _do_patch_apply("/tmp/proj", ["nonexistent"], False)
            assert result["applied"] == 0

    def test_applies_patch_and_saves_session(self):
        patch_dict = {
            "status": "pending",
            "patch_id": "p1",
            "section_id": "s1",
            "trigger": "t",
            "evidence": "e",
            "rationale": "r",
        }
        session = MagicMock()
        session.pending_patches = [patch_dict]

        mock_patch_obj = MagicMock()
        mock_patch_obj.trigger = "t"
        mock_patch_obj.evidence = "e"
        mock_patch_obj.patch_id = "p1"
        mock_patch_obj.section_id = "s1"

        refreshed = MagicMock()
        refreshed.patch_id = "p1"
        refreshed.section_id = "s1"

        with (
            patch(
                "lintgate.controlplane.session_memory.get_or_create_session",
                return_value=session,
            ),
            patch(
                "lintgate.context_bootstrap.ContextPatch.from_dict",
                return_value=mock_patch_obj,
            ),
            patch(
                "lintgate.context_bootstrap.generate_context_patch",
                return_value=refreshed,
            ),
            patch(
                "lintgate.context_bootstrap.apply_context_patch",
                return_value={"applied": True, "diff_preview": "+new line"},
            ),
            patch("lintgate.controlplane.session_memory.save_session") as mock_save,
            patch("lintgate.state.log_feature_usage"),
        ):
            result = _do_patch_apply("/tmp/proj", None, False)
            assert result["applied"] == 1
            assert result["dry_run"] is False
            assert result["results"][0]["applied"] is True
            mock_save.assert_called_once_with(session)

    def test_dry_run_does_not_save(self):
        patch_dict = {
            "status": "pending",
            "patch_id": "p2",
            "section_id": "s2",
            "trigger": "t",
            "evidence": "e",
            "rationale": "r",
        }
        session = MagicMock()
        session.pending_patches = [patch_dict]

        mock_patch_obj = MagicMock()
        mock_patch_obj.trigger = "t"
        mock_patch_obj.evidence = "e"
        mock_patch_obj.patch_id = "p2"
        mock_patch_obj.section_id = "s2"

        refreshed = MagicMock()
        refreshed.patch_id = "p2"
        refreshed.section_id = "s2"

        with (
            patch(
                "lintgate.controlplane.session_memory.get_or_create_session",
                return_value=session,
            ),
            patch(
                "lintgate.context_bootstrap.ContextPatch.from_dict",
                return_value=mock_patch_obj,
            ),
            patch(
                "lintgate.context_bootstrap.generate_context_patch",
                return_value=refreshed,
            ),
            patch(
                "lintgate.context_bootstrap.apply_context_patch",
                return_value={"applied": False, "diff_preview": "preview"},
            ),
            patch("lintgate.controlplane.session_memory.save_session") as mock_save,
        ):
            result = _do_patch_apply("/tmp/proj", None, True)
            assert result["dry_run"] is True
            mock_save.assert_not_called()


# ---------------------------------------------------------------------------
# _do_bootstrap
# ---------------------------------------------------------------------------


class TestDoBootstrap:
    def test_delegates_to_bootstrap_context_files(self):
        expected = {"status": "ok", "files_written": 2}
        with (
            patch(
                "lintgate.context_bootstrap.bootstrap_context_files",
                return_value=expected,
            ) as mock_bs,
            patch("lintgate.state.log_feature_usage"),
        ):
            result = _do_bootstrap("/tmp/proj", write=True, overwrite=False)
            assert result == expected
            mock_bs.assert_called_once_with("/tmp/proj", write=True, overwrite=False)

    def test_passes_kwargs_through(self):
        with (
            patch(
                "lintgate.context_bootstrap.bootstrap_context_files",
                return_value={},
            ) as mock_bs,
            patch("lintgate.state.log_feature_usage"),
        ):
            _do_bootstrap("/tmp/proj", max_machine_rules=5, model_id="test-model")
            mock_bs.assert_called_once_with(
                "/tmp/proj", max_machine_rules=5, model_id="test-model"
            )


# ---------------------------------------------------------------------------
# _do_extract_theory
# ---------------------------------------------------------------------------


class TestDoExtractTheory:
    def test_returns_extract_theory_result(self):
        expected = {"theory_profile": {"core": []}, "enforceable_rules": []}
        with (
            patch(
                "lintgate.theory_extractor.extract_theory",
                return_value=expected,
            ),
            patch("lintgate.state.log_feature_usage"),
        ):
            result = _do_extract_theory("/tmp/proj")
            assert result == expected

    def test_logs_feature_usage(self):
        with (
            patch("lintgate.theory_extractor.extract_theory", return_value={}),
            patch("lintgate.state.log_feature_usage") as mock_log,
        ):
            _do_extract_theory("/tmp/proj")
            mock_log.assert_called_once_with("theory_extraction", "/tmp/proj")


# ---------------------------------------------------------------------------
# _do_build_theory_pack
# ---------------------------------------------------------------------------


class TestDoBuildTheoryPack:
    def test_returns_theory_pack(self):
        pack = {"summary": "compact", "claims": 5}
        with (
            patch("lintgate.theory_extractor.build_theory_pack", return_value=pack),
            patch("lintgate.state.log_feature_usage"),
        ):
            result = _do_build_theory_pack("/tmp/proj")
            assert result == pack

    def test_passes_include_full_profile(self):
        with (
            patch(
                "lintgate.theory_extractor.build_theory_pack", return_value={}
            ) as mock_build,
            patch("lintgate.state.log_feature_usage"),
        ):
            _do_build_theory_pack("/tmp/proj", include_full_profile=True)
            mock_build.assert_called_once_with("/tmp/proj", include_full_profile=True)
