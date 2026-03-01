"""Tests for session-aware refactor checkpointing (#199).

Covers:
1. RefactorState persistence (save/load round-trip)
2. checkpoint — file progress tracking
3. set_thesis — thesis recording
4. resume — structured summary for session resumption
5. update_finding_counts — controlplane_run integration
6. update_file_findings — lint_files integration
7. archive_if_complete — automatic archival
8. _recommend_next_file — next file recommendation
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from lintgate.refactor_state import (
    FileProgress,
    PatternRecord,
    RefactorState,
    _recommend_next_file,
    archive_if_complete,
    checkpoint,
    load_state,
    resume,
    save_state,
    set_thesis,
    update_file_findings,
    update_finding_counts,
)

# ── Persistence ───────────────────────────────────────────────────────


class TestPersistence:
    def test_save_load_round_trip(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state = RefactorState(
                session_id="abc123",
                started_at="2026-02-28T10:00:00Z",
                thesis="Test thesis",
            )
            state.files["mod.py"] = FileProgress(
                status="in_progress",
                initial_findings=5,
                remaining_findings=3,
                patterns_applied=["guard-clause"],
                notes="WIP",
            )
            save_state(tmpdir, state)
            loaded = load_state(tmpdir)
            assert loaded is not None
            assert loaded.session_id == "abc123"
            assert loaded.thesis == "Test thesis"
            assert "mod.py" in loaded.files
            assert loaded.files["mod.py"].status == "in_progress"
            assert loaded.files["mod.py"].patterns_applied == ["guard-clause"]

    def test_load_nonexistent_returns_none(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            assert load_state(tmpdir) is None

    def test_load_corrupt_returns_none(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / ".lintgate" / "refactor_state.json"
            state_path.parent.mkdir(parents=True)
            state_path.write_text("not json{{{")
            assert load_state(tmpdir) is None

    def test_save_creates_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state = RefactorState(session_id="x")
            save_state(tmpdir, state)
            assert (Path(tmpdir) / ".lintgate" / "refactor_state.json").exists()

    def test_updated_at_set_on_save(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state = RefactorState(session_id="x")
            save_state(tmpdir, state)
            loaded = load_state(tmpdir)
            assert loaded.updated_at != ""


# ── checkpoint ────────────────────────────────────────────────────────


class TestCheckpoint:
    def test_creates_session_on_first_call(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state = checkpoint(tmpdir, "mod.py", "in_progress")
            assert state.session_id != ""
            assert state.started_at != ""
            assert "mod.py" in state.files

    def test_updates_file_status(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            checkpoint(tmpdir, "mod.py", "in_progress")
            state = checkpoint(tmpdir, "mod.py", "completed", notes="Done")
            assert state.files["mod.py"].status == "completed"
            assert state.files["mod.py"].notes == "Done"

    def test_initial_findings_set_once(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            checkpoint(tmpdir, "mod.py", "in_progress", initial_findings=10)
            state = checkpoint(tmpdir, "mod.py", "in_progress", initial_findings=5)
            # Should keep the original value
            assert state.files["mod.py"].initial_findings == 10

    def test_remaining_findings_updated(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            checkpoint(tmpdir, "mod.py", "in_progress", remaining_findings=10)
            state = checkpoint(tmpdir, "mod.py", "in_progress", remaining_findings=3)
            assert state.files["mod.py"].remaining_findings == 3

    def test_patterns_tracked(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            checkpoint(
                tmpdir, "mod.py", "in_progress", patterns_applied=["guard-clause"]
            )
            state = checkpoint(
                tmpdir, "mod.py", "in_progress", patterns_applied=["helper-extraction"]
            )
            assert "guard-clause" in state.files["mod.py"].patterns_applied
            assert "helper-extraction" in state.files["mod.py"].patterns_applied

    def test_pattern_dedup(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            checkpoint(
                tmpdir, "mod.py", "in_progress", patterns_applied=["guard-clause"]
            )
            state = checkpoint(
                tmpdir, "mod.py", "in_progress", patterns_applied=["guard-clause"]
            )
            assert state.files["mod.py"].patterns_applied.count("guard-clause") == 1

    def test_global_pattern_record(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            checkpoint(tmpdir, "a.py", "completed", patterns_applied=["guard-clause"])
            state = checkpoint(
                tmpdir, "b.py", "completed", patterns_applied=["guard-clause"]
            )
            pr = state.applied_patterns["guard-clause"]
            assert pr.count == 2
            assert "a.py" in pr.files_applied
            assert "b.py" in pr.files_applied

    def test_invalid_status_raises(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            try:
                checkpoint(tmpdir, "mod.py", "invalid_status")
                raise AssertionError("Should have raised")
            except ValueError:
                pass

    def test_multiple_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            checkpoint(tmpdir, "a.py", "completed")
            checkpoint(tmpdir, "b.py", "in_progress")
            state = checkpoint(tmpdir, "c.py", "pending")
            assert len(state.files) == 3

    def test_preserves_session_id(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            s1 = checkpoint(tmpdir, "a.py", "in_progress")
            s2 = checkpoint(tmpdir, "b.py", "in_progress")
            assert s1.session_id == s2.session_id


# ── set_thesis ────────────────────────────────────────────────────────


class TestSetThesis:
    def test_creates_session(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state = set_thesis(tmpdir, "Organic growth, needs separation")
            assert state.session_id != ""
            assert state.thesis == "Organic growth, needs separation"

    def test_updates_thesis(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            set_thesis(tmpdir, "First thesis")
            state = set_thesis(tmpdir, "Revised thesis")
            assert state.thesis == "Revised thesis"

    def test_preserves_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            checkpoint(tmpdir, "mod.py", "in_progress")
            state = set_thesis(tmpdir, "My thesis")
            assert "mod.py" in state.files


# ── resume ────────────────────────────────────────────────────────────


class TestResume:
    def test_no_active_session(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            result = resume(tmpdir)
            assert not result["active"]
            assert "message" in result

    def test_active_session_summary(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            set_thesis(tmpdir, "Test thesis")
            checkpoint(
                tmpdir, "a.py", "completed", initial_findings=5, remaining_findings=1
            )
            checkpoint(
                tmpdir, "b.py", "in_progress", initial_findings=8, remaining_findings=4
            )
            checkpoint(
                tmpdir, "c.py", "pending", initial_findings=3, remaining_findings=3
            )

            result = resume(tmpdir)
            assert result["active"]
            assert result["thesis"] == "Test thesis"
            assert result["file_summary"]["completed"] == 1
            assert result["file_summary"]["in_progress"] == 1
            assert result["file_summary"]["pending"] == 1
            assert result["total_files"] == 3
            assert result["finding_trend"]["initial"] == 16
            assert result["finding_trend"]["remaining"] == 8
            assert result["finding_trend"]["resolved"] == 8

    def test_recommended_next(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            checkpoint(tmpdir, "a.py", "completed")
            checkpoint(tmpdir, "b.py", "in_progress")
            checkpoint(tmpdir, "c.py", "pending")

            result = resume(tmpdir)
            assert result["recommended_next"] == "b.py"

    def test_patterns_in_summary(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            checkpoint(tmpdir, "a.py", "completed", patterns_applied=["guard-clause"])
            checkpoint(tmpdir, "b.py", "completed", patterns_applied=["guard-clause"])

            result = resume(tmpdir)
            assert "guard-clause" in result["patterns"]
            assert result["patterns"]["guard-clause"]["count"] == 2


# ── update_finding_counts ─────────────────────────────────────────────


class TestUpdateFindingCounts:
    def test_updates_counts(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            checkpoint(tmpdir, "mod.py", "in_progress")
            update_finding_counts(tmpdir, "run_123", {"blocking": 5, "warning": 10})
            state = load_state(tmpdir)
            assert state.last_controlplane_run == "run_123"
            assert state.last_finding_counts["blocking"] == 5

    def test_no_session_noop(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            # Should not raise
            update_finding_counts(tmpdir, "run_123", {"blocking": 0})
            assert load_state(tmpdir) is None


# ── update_file_findings ──────────────────────────────────────────────


class TestUpdateFileFindings:
    def test_updates_remaining(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            checkpoint(tmpdir, "mod.py", "in_progress", remaining_findings=10)
            update_file_findings(tmpdir, "mod.py", 3)
            state = load_state(tmpdir)
            assert state.files["mod.py"].remaining_findings == 3

    def test_untracked_file_ignored(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            checkpoint(tmpdir, "mod.py", "in_progress")
            update_file_findings(tmpdir, "other.py", 5)
            state = load_state(tmpdir)
            assert "other.py" not in state.files

    def test_no_session_noop(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            update_file_findings(tmpdir, "mod.py", 5)
            assert load_state(tmpdir) is None


# ── archive_if_complete ───────────────────────────────────────────────


class TestArchiveIfComplete:
    def test_archives_when_all_done(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            checkpoint(tmpdir, "a.py", "completed")
            checkpoint(tmpdir, "b.py", "skipped")

            archived = archive_if_complete(tmpdir)
            assert archived
            assert load_state(tmpdir) is None
            # Archive directory should exist
            archive_dir = Path(tmpdir) / ".lintgate" / "refactor_archive"
            assert archive_dir.exists()
            archive_files = list(archive_dir.glob("*.json"))
            assert len(archive_files) == 1

    def test_not_archived_when_pending(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            checkpoint(tmpdir, "a.py", "completed")
            checkpoint(tmpdir, "b.py", "pending")

            archived = archive_if_complete(tmpdir)
            assert not archived
            assert load_state(tmpdir) is not None

    def test_not_archived_when_in_progress(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            checkpoint(tmpdir, "a.py", "completed")
            checkpoint(tmpdir, "b.py", "in_progress")

            archived = archive_if_complete(tmpdir)
            assert not archived

    def test_no_session_returns_false(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            assert not archive_if_complete(tmpdir)

    def test_empty_files_returns_false(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            set_thesis(tmpdir, "thesis only, no files")
            assert not archive_if_complete(tmpdir)


# ── _recommend_next_file ──────────────────────────────────────────────


class TestRecommendNextFile:
    def test_in_progress_first(self):
        state = RefactorState()
        state.files["a.py"] = FileProgress(status="pending", initial_findings=10)
        state.files["b.py"] = FileProgress(status="in_progress", initial_findings=2)
        assert _recommend_next_file(state) == "b.py"

    def test_pending_by_findings(self):
        state = RefactorState()
        state.files["a.py"] = FileProgress(status="pending", initial_findings=3)
        state.files["b.py"] = FileProgress(status="pending", initial_findings=10)
        state.files["c.py"] = FileProgress(status="completed")
        assert _recommend_next_file(state) == "b.py"

    def test_all_done_returns_none(self):
        state = RefactorState()
        state.files["a.py"] = FileProgress(status="completed")
        state.files["b.py"] = FileProgress(status="skipped")
        assert _recommend_next_file(state) is None

    def test_empty_returns_none(self):
        state = RefactorState()
        assert _recommend_next_file(state) is None


# ── Serialization ─────────────────────────────────────────────────────


class TestSerialization:
    def test_to_dict_round_trip(self):
        state = RefactorState(
            session_id="test",
            thesis="my thesis",
        )
        state.files["mod.py"] = FileProgress(
            status="completed",
            initial_findings=5,
            remaining_findings=0,
            patterns_applied=["x"],
        )
        state.applied_patterns["x"] = PatternRecord(
            description="test pattern",
            files_applied=["mod.py"],
            count=1,
        )
        d = state.to_dict()
        restored = RefactorState.from_dict(d)
        assert restored.session_id == "test"
        assert restored.thesis == "my thesis"
        assert restored.files["mod.py"].status == "completed"
        assert restored.applied_patterns["x"].count == 1

    def test_json_serializable(self):
        state = RefactorState(session_id="test")
        state.files["a.py"] = FileProgress(status="pending")
        # Should not raise
        json.dumps(state.to_dict())
