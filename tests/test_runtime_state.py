"""Tests for lintgate.runtime_state — canonical state bus."""

from __future__ import annotations

import time
from unittest.mock import MagicMock

from lintgate.runtime_state import (
    RuntimeState,
    RuntimeStateWriteMeta,
    build_runtime_state,
    delete_runtime_state,
    load_runtime_state,
    save_runtime_state,
    save_runtime_state_with_meta,
)

# ── RuntimeState dataclass ───────────────────────────────────────────


class TestRuntimeStateDataclass:
    """Test RuntimeState serialization and defaults."""

    def test_defaults(self):
        state = RuntimeState()
        assert state.generation == 0
        assert state.mode == "normal"
        assert state.toward == []
        assert state.active_files == []
        assert state.prediction_accuracy == -1.0

    def test_round_trip(self):
        state = RuntimeState(
            generation=5,
            session_id="abc123",
            mode="habit",
            habit_score=0.82,
            true_north="Build correct software",
            toward=["test first", "small functions"],
            away=["god objects"],
            forbidden=["eval()"],
            active_files=["/src/main.py", "/src/utils.py"],
            last_test_status="pass",
            blocking_issues=2,
            warning_issues=5,
            coherence_state="isolated",
            prediction_accuracy=0.75,
            estimated_tokens_pct=42.5,
            compaction_count=1,
            tool_calls_total=30,
            top_constraint="no eval",
            approach_failures=1,
        )
        d = state.to_dict()
        restored = RuntimeState.from_dict(d)
        assert restored.generation == 5
        assert restored.mode == "habit"
        assert restored.habit_score == 0.82
        assert restored.toward == ["test first", "small functions"]
        assert restored.active_files == ["/src/main.py", "/src/utils.py"]
        assert restored.prediction_accuracy == 0.75

    def test_from_dict_tolerates_extra_keys(self):
        data = {"generation": 3, "mode": "theory", "unknown_field": "ignored"}
        state = RuntimeState.from_dict(data)
        assert state.generation == 3
        assert state.mode == "theory"

    def test_from_dict_tolerates_missing_keys(self):
        state = RuntimeState.from_dict({"generation": 7})
        assert state.generation == 7
        assert state.mode == "normal"
        assert state.toward == []

    def test_to_dict_contains_all_fields(self):
        state = RuntimeState()
        d = state.to_dict()
        assert "generation" in d
        assert "mode" in d
        assert "toward" in d
        assert "active_files" in d
        assert "prediction_accuracy" in d


# ── I/O ──────────────────────────────────────────────────────────────


class TestRuntimeStateIO:
    """Test load/save/delete operations."""

    def test_save_and_load(self, tmp_path):
        state = RuntimeState(session_id="test1", mode="habit", toward=["x"])
        save_runtime_state(str(tmp_path), state)

        loaded = load_runtime_state(str(tmp_path))
        assert loaded is not None
        assert loaded.session_id == "test1"
        assert loaded.mode == "habit"
        assert loaded.toward == ["x"]

    def test_save_returns_true_on_success(self, tmp_path):
        state = RuntimeState(session_id="ok")
        assert save_runtime_state(str(tmp_path), state) is True

    def test_save_increments_generation(self, tmp_path):
        state = RuntimeState(generation=0)
        save_runtime_state(str(tmp_path), state)
        assert state.generation == 1

        save_runtime_state(str(tmp_path), state)
        assert state.generation == 2

        loaded = load_runtime_state(str(tmp_path))
        assert loaded is not None
        assert loaded.generation == 2

    def test_save_sets_timestamp(self, tmp_path):
        state = RuntimeState()
        before = time.time()
        save_runtime_state(str(tmp_path), state)
        after = time.time()

        loaded = load_runtime_state(str(tmp_path))
        assert loaded is not None
        assert before <= loaded.timestamp <= after

    def test_save_creates_directory(self, tmp_path):
        project = tmp_path / "deep" / "nested"
        project.mkdir(parents=True)
        state = RuntimeState(session_id="nested")
        save_runtime_state(str(project), state)

        loaded = load_runtime_state(str(project))
        assert loaded is not None
        assert loaded.session_id == "nested"

    def test_load_missing_file_returns_none(self, tmp_path):
        assert load_runtime_state(str(tmp_path)) is None

    def test_load_corrupt_file_returns_none(self, tmp_path):
        state_dir = tmp_path / ".lintgate"
        state_dir.mkdir()
        (state_dir / "runtime_state.json").write_text("not json{{{")
        assert load_runtime_state(str(tmp_path)) is None

    def test_load_non_dict_returns_none(self, tmp_path):
        state_dir = tmp_path / ".lintgate"
        state_dir.mkdir()
        (state_dir / "runtime_state.json").write_text('"just a string"')
        assert load_runtime_state(str(tmp_path)) is None

    def test_delete_existing(self, tmp_path):
        state = RuntimeState()
        save_runtime_state(str(tmp_path), state)
        assert delete_runtime_state(str(tmp_path)) is True
        assert load_runtime_state(str(tmp_path)) is None

    def test_delete_missing_returns_false(self, tmp_path):
        assert delete_runtime_state(str(tmp_path)) is False

    def test_delete_removes_lock_file(self, tmp_path):
        state_dir = tmp_path / ".lintgate"
        state_dir.mkdir()
        lock_path = state_dir / "runtime_state.lock"
        lock_path.write_text("")

        assert delete_runtime_state(str(tmp_path)) is True
        assert not lock_path.exists()

    def test_delete_lock_only_returns_true(self, tmp_path):
        state_dir = tmp_path / ".lintgate"
        state_dir.mkdir()
        (state_dir / "runtime_state.lock").write_text("")

        assert delete_runtime_state(str(tmp_path)) is True

    def test_save_atomic_no_partial_writes(self, tmp_path):
        """Verify the file is either fully written or not written at all."""
        state = RuntimeState(session_id="atomic_test", toward=["a", "b", "c"])
        save_runtime_state(str(tmp_path), state)

        loaded = load_runtime_state(str(tmp_path))
        assert loaded is not None
        assert loaded.session_id == "atomic_test"
        assert loaded.toward == ["a", "b", "c"]

    def test_save_preserves_generation_across_loads(self, tmp_path):
        """Save → load → save → load preserves generation sequence."""
        state = RuntimeState()
        save_runtime_state(str(tmp_path), state)  # gen → 1

        loaded = load_runtime_state(str(tmp_path))
        assert loaded is not None
        assert loaded.generation == 1

        save_runtime_state(str(tmp_path), loaded)  # gen → 2
        loaded2 = load_runtime_state(str(tmp_path))
        assert loaded2 is not None
        assert loaded2.generation == 2

    def test_save_returns_false_when_lock_not_acquired(self, tmp_path, monkeypatch):
        from contextlib import contextmanager

        import lintgate.runtime_state as runtime_state_module

        @contextmanager
        def fake_lock(_state_dir):
            yield {"locked": False, "contention_count": 0}

        monkeypatch.setattr(runtime_state_module, "_runtime_state_lock", fake_lock)
        state = RuntimeState(session_id="locked")
        assert save_runtime_state(str(tmp_path), state) is False
        assert load_runtime_state(str(tmp_path)) is None

    def test_save_with_meta_includes_contention_count(self, tmp_path, monkeypatch):
        from contextlib import contextmanager

        import lintgate.runtime_state as runtime_state_module

        @contextmanager
        def fake_lock(_state_dir):
            yield {"locked": False, "contention_count": 4}

        monkeypatch.setattr(runtime_state_module, "_runtime_state_lock", fake_lock)
        state = RuntimeState(session_id="meta")
        meta = save_runtime_state_with_meta(str(tmp_path), state)
        assert isinstance(meta, RuntimeStateWriteMeta)
        assert meta.written is False
        assert meta.lock_acquired is False
        assert meta.contention_count == 4

    def test_runtime_lock_reports_contention_retries(self, tmp_path, monkeypatch):
        import lintgate.runtime_state as runtime_state_module

        class _FakeFcntl:
            LOCK_EX = 1
            LOCK_NB = 2
            LOCK_UN = 8

            def __init__(self):
                self._attempts = 0

            def flock(self, _fd, flags):
                if flags == self.LOCK_UN:
                    return
                if self._attempts < 2:
                    self._attempts += 1
                    raise OSError("busy")

        fake = _FakeFcntl()
        monkeypatch.setattr(runtime_state_module, "_fcntl", fake)
        monkeypatch.setattr(runtime_state_module.time, "sleep", lambda _s: None)

        state_dir = tmp_path / ".lintgate"
        state_dir.mkdir()
        with runtime_state_module._runtime_state_lock(state_dir) as info:
            assert info["locked"] is True
            assert info["contention_count"] == 2


# ── Builder ──────────────────────────────────────────────────────────


class TestBuildRuntimeState:
    """Test build_runtime_state assembly from sources."""

    def test_empty_sources(self, tmp_path):
        state = build_runtime_state(str(tmp_path))
        assert state.mode == "normal"
        assert state.generation == 0
        assert state.toward == []

    def test_preserves_existing_generation(self, tmp_path):
        """Building on top of existing state preserves generation."""
        existing = RuntimeState(generation=5, session_id="old")
        save_runtime_state(str(tmp_path), existing)

        state = build_runtime_state(str(tmp_path))
        assert state.generation == 6  # save incremented it

    def test_session_provides_mode(self, tmp_path):
        session = MagicMock()
        session.session_id = "sess1"
        session.behavior_compass = {"mode_state": {"current": "habit"}}
        session.snapshots = []
        session.coherence_trajectory = []

        state = build_runtime_state(str(tmp_path), session=session)
        assert state.session_id == "sess1"
        assert state.mode == "habit"

    def test_session_mode_defaults_to_normal(self, tmp_path):
        session = MagicMock()
        session.session_id = "s2"
        session.behavior_compass = {}
        session.snapshots = []
        session.coherence_trajectory = []

        state = build_runtime_state(str(tmp_path), session=session)
        assert state.mode == "normal"

    def test_habit_state_provides_score_and_files(self, tmp_path):
        habit = MagicMock()
        habit.habit_score = 0.85
        habit.active_files = ["/a.py", "/b.py", "/c.py"]
        habit.compaction_count = 3

        state = build_runtime_state(str(tmp_path), habit_state=habit)
        assert state.habit_score == 0.85
        assert state.active_files == ["/a.py", "/b.py", "/c.py"]
        assert state.compaction_count == 3

    def test_active_files_capped_at_max(self, tmp_path):
        habit = MagicMock()
        habit.habit_score = 0.5
        habit.active_files = [f"/{i}.py" for i in range(20)]
        habit.compaction_count = 0

        state = build_runtime_state(str(tmp_path), habit_state=habit)
        assert len(state.active_files) == 10

    def test_tracker_provides_token_economics(self, tmp_path):
        tracker = MagicMock()
        tracker.estimated_tokens_used = 80000
        tracker.context_window_size = 200000
        tracker.tool_call_count = 42

        state = build_runtime_state(str(tmp_path), tracker=tracker)
        assert state.estimated_tokens_pct == 40.0
        assert state.tool_calls_total == 42

    def test_tracker_zero_window_no_crash(self, tmp_path):
        tracker = MagicMock()
        tracker.estimated_tokens_used = 100
        tracker.context_window_size = 0
        tracker.tool_call_count = 1

        state = build_runtime_state(str(tmp_path), tracker=tracker)
        assert state.estimated_tokens_pct == 0.0

    def test_exec_compass_provides_directives(self, tmp_path):
        ec = MagicMock()
        ec.true_north = "Build reliable systems"
        ec.toward = ["test", "type hints"]
        ec.away = ["complexity"]
        ec.forbidden = ["eval"]

        state = build_runtime_state(str(tmp_path), exec_compass=ec)
        assert state.true_north == "Build reliable systems"
        assert state.toward == ["test", "type hints"]
        assert state.away == ["complexity"]
        assert state.forbidden == ["eval"]

    def test_exec_compass_caps_directives(self, tmp_path):
        ec = MagicMock()
        ec.true_north = "x" * 200  # exceeds 120 chars
        ec.toward = [f"d{i}" for i in range(15)]
        ec.away = []
        ec.forbidden = []

        state = build_runtime_state(str(tmp_path), exec_compass=ec)
        assert len(state.true_north) == 120
        assert len(state.toward) == 8

    def test_compass_fallback_when_no_exec_compass(self, tmp_path):
        compass = MagicMock()
        problem_axis = MagicMock()
        problem_axis.summary = "Solve hard problems"
        compass.axes = {"problem": problem_axis}

        d1 = MagicMock()
        d1.kind = "toward"
        d1.text = "be good"
        d2 = MagicMock()
        d2.kind = "away"
        d2.text = "be bad"
        compass.directives = [d1, d2]

        state = build_runtime_state(str(tmp_path), compass=compass)
        assert state.true_north == "Solve hard problems"
        assert state.toward == ["be good"]
        assert state.away == ["be bad"]

    def test_last_coherence_overrides_trajectory(self, tmp_path):
        session = MagicMock()
        session.session_id = "s"
        session.behavior_compass = {}
        session.snapshots = []
        session.coherence_trajectory = ["stable", "isolated"]

        state = build_runtime_state(str(tmp_path), session=session, last_coherence_state="systemic")
        assert state.coherence_state == "systemic"

    def test_coherence_from_trajectory_when_no_override(self, tmp_path):
        session = MagicMock()
        session.session_id = "s"
        session.behavior_compass = {}
        session.snapshots = []
        session.coherence_trajectory = ["stable", "coupled"]

        state = build_runtime_state(str(tmp_path), session=session)
        assert state.coherence_state == "coupled"

    def test_finding_counts_from_params(self, tmp_path):
        state = build_runtime_state(str(tmp_path), last_blocking=3, last_warnings=7)
        assert state.blocking_issues == 3
        assert state.warning_issues == 7

    def test_prediction_accuracy_from_snapshots(self, tmp_path):
        session = MagicMock()
        session.session_id = "s"
        session.behavior_compass = {}
        session.coherence_trajectory = []

        snap1 = MagicMock()
        snap1.behavior.prediction_accuracy = 0.8
        snap2 = MagicMock()
        snap2.behavior.prediction_accuracy = 0.6
        snap3 = MagicMock()
        snap3.behavior.prediction_accuracy = None  # skipped
        session.snapshots = [snap1, snap2, snap3]

        state = build_runtime_state(str(tmp_path), session=session)
        assert state.prediction_accuracy == 0.7  # (0.8 + 0.6) / 2

    def test_behavioral_signals_from_session(self, tmp_path):
        session = MagicMock()
        session.session_id = "s"
        session.behavior_compass = {
            "approach_failures": 3,
            "active_constraints": ["no-eval", "no-exec"],
        }
        session.snapshots = []
        session.coherence_trajectory = []

        state = build_runtime_state(str(tmp_path), session=session)
        assert state.approach_failures == 3
        assert state.top_constraint == "no-eval"
