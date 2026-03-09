"""Tests for behavioral compass — data model, hypothesis management, and detection rules. (Part 1/2)"""

from __future__ import annotations

import pytest

from lintgate.controlplane.behavior_compass import (
    DEFAULT_HYPOTHESIS_CONFIG,
    BehaviorHypothesis,
    decay_stale,
    error_memory_key,
    evict_overflow,
    extract_error_sig,
    new_compass,
    normalize_command_sig,
    record_tool_event,
    update_hypothesis,
)

# ── normalize_command_sig ─────────────────────────────────────────────


class TestNormalizeCommandSig:
    def test_simple_command(self):
        assert normalize_command_sig("git status") == "git:status"

    def test_uv_run_wrapper(self):
        result = normalize_command_sig("uv run python -m pytest tests/test_foo.py -v")
        # Strips uv run + python -m wrappers
        assert result.startswith("pytest:")
        assert "test_foo" in result

    def test_python_m_wrapper(self):
        result = normalize_command_sig("python -m pytest tests/")
        assert result.startswith("pytest:")
        assert "tests" in result

    def test_sudo_wrapper(self):
        result = normalize_command_sig("sudo apt install foo")
        assert result == "apt:install"

    def test_env_wrapper(self):
        result = normalize_command_sig("env FOO=bar python script.py")
        # env wrapper strips env then skips VAR=val tokens
        assert result.startswith("python:") or "python" in result

    def test_absolute_path_binary(self):
        result = normalize_command_sig("/usr/bin/idevicerestore -e custom.ipsw")
        assert result == "idevicerestore:custom"

    def test_flags_stripped(self):
        result = normalize_command_sig("ls -la --color /tmp")
        assert result.startswith("ls:")
        assert "tmp" in result

    def test_secret_redacted(self):
        result = normalize_command_sig(
            "curl -H 'Authorization: tok_fake_placeholder_value' https://api.example.com"
        )
        # The secret token should NOT appear in the result
        assert "tok_fake" not in result
        assert result.startswith("curl:")

    def test_empty_command(self):
        assert normalize_command_sig("") == "unknown:unknown"

    def test_malformed_quotes(self):
        # shlex.split fails on unbalanced quotes — should fall back
        result = normalize_command_sig("echo 'unterminated")
        assert result.startswith("echo:")

    def test_no_args(self):
        assert normalize_command_sig("pwd") == "pwd:default"

    def test_hfsplus_command(self):
        result = normalize_command_sig("hfsplus rootfs.dec ls /Applications/")
        assert result == "hfsplus:rootfs"


# ── extract_error_sig ────────────────────────────────────────────────


class TestExtractErrorSig:
    def test_simple_error(self):
        stderr = "Error: file not found\n"
        result = extract_error_sig(stderr)
        assert "file not found" in result

    def test_strips_absolute_paths(self):
        stderr = "Error: /usr/local/lib/python3.12/site-packages/foo.py: module failed"
        result = extract_error_sig(stderr)
        assert "/usr/local/lib" not in result
        assert "module failed" in result

    def test_strips_timestamps(self):
        stderr = "2024-01-15T10:30:00 ERROR: connection refused"
        result = extract_error_sig(stderr)
        assert "2024-01-15" not in result
        assert "connection refused" in result

    def test_empty_stderr(self):
        assert extract_error_sig("") == ""
        assert extract_error_sig("   ") == ""

    def test_last_meaningful_line(self):
        stderr = "Starting process...\nLoading config...\nFatal: unable to connect\n---\n"
        result = extract_error_sig(stderr)
        assert "unable to connect" in result

    def test_skips_exit_code_status_line(self):
        stderr = "error: Unable to send iBSS\nexit code: 1\n"
        result = extract_error_sig(stderr)
        assert "Unable to send iBSS" in result
        assert "exit code" not in result.lower()

    def test_truncation(self):
        stderr = "E" * 300
        result = extract_error_sig(stderr)
        assert len(result) <= 200


# ── record_tool_event ────────────────────────────────────────────────


class TestRecordToolEvent:
    def test_appends_to_history(self):
        compass = new_compass()
        record_tool_event(compass, "Read", "/some/file.py", "contents", now=1000.0)
        assert len(compass.action_history) == 1
        assert compass.action_history[0]["tool"] == "Read"

    def test_bash_failure_creates_hypothesis(self):
        compass = new_compass()
        record_tool_event(
            compass,
            "Bash",
            {"command": "idevicerestore -e custom.ipsw"},
            "error: Unable to send iBSS to device\nexit code: 1",
            now=1000.0,
        )
        # Should create an auto-hypothesis at 0.3 confidence
        assert len(compass.hypotheses) == 1
        assert (
            compass.hypotheses[0].confidence
            == DEFAULT_HYPOTHESIS_CONFIG["auto_generate_confidence"]
        )
        assert compass.hypotheses[0].source == "command_failure"
        assert "idevicerestore" in compass.hypotheses[0].claim

    def test_bash_success_no_hypothesis(self):
        compass = new_compass()
        record_tool_event(
            compass,
            "Bash",
            {"command": "git status"},
            "On branch main\nnothing to commit",
            now=1000.0,
        )
        assert len(compass.hypotheses) == 0

    def test_updates_approach(self):
        compass = new_compass()
        record_tool_event(
            compass,
            "Bash",
            {"command": "pytest tests/"},
            "exit code: 1\n2 failed",
            now=1000.0,
        )
        assert len(compass.approaches) == 1
        assert compass.approaches[0].outcome == "failed"

    def test_history_capped_at_30(self):
        compass = new_compass()
        for i in range(35):
            record_tool_event(compass, "Read", f"/file_{i}.py", "ok", now=1000.0 + i)
        assert len(compass.action_history) == 30

    def test_repeated_error_strengthens_hypothesis(self):
        compass = new_compass()
        # First failure creates hypothesis
        record_tool_event(
            compass,
            "Bash",
            {"command": "idevicerestore -e fw.ipsw"},
            "error: Unable to send iBSS\nexit code: 1",
            now=1000.0,
        )
        initial_conf = compass.hypotheses[0].confidence

        # Second failure with same error should strengthen
        record_tool_event(
            compass,
            "Bash",
            {"command": "idevicerestore -d fw.ipsw"},
            "error: Unable to send iBSS\nexit code: 1",
            now=1001.0,
        )
        # Hypothesis should be strengthened (not duplicated)
        assert len(compass.hypotheses) == 1
        assert compass.hypotheses[0].confidence > initial_conf

    def test_failure_updates_error_memory(self):
        compass = new_compass()
        raw_error = "error: Unable to send iBSS"
        record_tool_event(
            compass,
            "Bash",
            {"command": "idevicerestore -e fw.ipsw"},
            f"{raw_error}\nexit code: 1",
            now=1000.0,
        )
        key = error_memory_key(raw_error)
        assert key in compass.error_memory
        assert compass.error_memory[key]["count"] == 1

    def test_repeated_failure_increments_error_memory(self):
        compass = new_compass()
        raw_error = "error: Unable to send iBSS"
        record_tool_event(
            compass,
            "Bash",
            {"command": "idevicerestore -e fw.ipsw"},
            f"{raw_error}\nexit code: 1",
            now=1000.0,
        )
        record_tool_event(
            compass,
            "Bash",
            {"command": "idevicerestore -d fw.ipsw"},
            f"{raw_error}\nexit code: 1",
            now=1001.0,
        )
        key = error_memory_key(raw_error)
        assert compass.error_memory[key]["count"] == 2


# ── Hypothesis management ────────────────────────────────────────────


class TestHypothesisManagement:
    def test_strengthen(self):
        compass = new_compass()
        compass.hypotheses.append(
            BehaviorHypothesis(
                id="test1",
                claim="test claim",
                confidence=0.3,
                created_at=1000.0,
                last_tested=1000.0,
            )
        )
        update_hypothesis(compass, "test1", "strengthen", "evidence A", now=1001.0)
        assert compass.hypotheses[0].confidence == pytest.approx(0.3 + 0.15)
        assert len(compass.hypotheses[0].evidence_for) == 1

    def test_weaken(self):
        compass = new_compass()
        compass.hypotheses.append(
            BehaviorHypothesis(
                id="test1",
                claim="test claim",
                confidence=0.5,
                created_at=1000.0,
                last_tested=1000.0,
            )
        )
        update_hypothesis(compass, "test1", "weaken", "counter evidence", now=1001.0)
        assert compass.hypotheses[0].confidence == pytest.approx(0.5 - 0.1)

    def test_promote_to_confirmed(self):
        compass = new_compass()
        compass.hypotheses.append(
            BehaviorHypothesis(
                id="test1",
                claim="test claim",
                confidence=0.6,
                evidence_for=["ev1"],
                created_at=1000.0,
                last_tested=1000.0,
            )
        )
        # After strengthening, should reach 0.75 with 2 evidence → confirmed
        update_hypothesis(compass, "test1", "strengthen", "ev2", now=1001.0)
        assert compass.hypotheses[0].status == "confirmed"
        assert compass.hypotheses[0].confidence >= 0.7

    def test_weaken_to_expired(self):
        compass = new_compass()
        compass.hypotheses.append(
            BehaviorHypothesis(
                id="test1",
                claim="test claim",
                confidence=0.05,
                created_at=1000.0,
                last_tested=1000.0,
            )
        )
        update_hypothesis(compass, "test1", "weaken", "final blow", now=1001.0)
        assert compass.hypotheses[0].status == "expired"
        assert compass.hypotheses[0].confidence == 0.0

    def test_requires_min_evidence_for_promote(self):
        compass = new_compass()
        compass.hypotheses.append(
            BehaviorHypothesis(
                id="test1",
                claim="test claim",
                confidence=0.6,
                # Only 0 evidence_for items
                created_at=1000.0,
                last_tested=1000.0,
            )
        )
        # After strengthening: confidence=0.75, but only 1 evidence → not confirmed yet
        update_hypothesis(compass, "test1", "strengthen", "ev1", now=1001.0)
        assert compass.hypotheses[0].status == "active"  # Not confirmed yet


class TestDecay:
    def test_decay_after_one_hour(self):
        compass = new_compass()
        compass.hypotheses.append(
            BehaviorHypothesis(
                id="test1",
                claim="test",
                confidence=0.5,
                created_at=1000.0,
                last_tested=1000.0,
            )
        )
        # 2 hours later
        decay_stale(compass, 1000.0 + 7200)
        assert compass.hypotheses[0].confidence < 0.5

    def test_decay_within_one_hour_is_proportional(self):
        compass = new_compass()
        compass.hypotheses.append(
            BehaviorHypothesis(
                id="test1",
                claim="test",
                confidence=0.5,
                created_at=1000.0,
                last_tested=1000.0,
            )
        )
        # 30 minutes later
        decay_stale(compass, 1000.0 + 1800)
        assert compass.hypotheses[0].confidence == pytest.approx(0.475, abs=1e-3)

    def test_repeated_decay_uses_incremental_elapsed_time(self):
        compass = new_compass()
        compass.hypotheses.append(
            BehaviorHypothesis(
                id="test1",
                claim="test",
                confidence=1.0,
                created_at=1000.0,
                last_tested=1000.0,
            )
        )
        # First decay call after 2h
        decay_stale(compass, 1000.0 + 7200)
        first = compass.hypotheses[0].confidence
        # Second call 1 minute later should only decay by ~0.0008, not another 0.1
        decay_stale(compass, 1000.0 + 7260)
        second = compass.hypotheses[0].confidence
        assert first == pytest.approx(0.9, abs=1e-3)
        assert (first - second) < 0.005

    def test_decay_to_expired(self):
        compass = new_compass()
        compass.hypotheses.append(
            BehaviorHypothesis(
                id="test1",
                claim="test",
                confidence=0.05,
                created_at=1000.0,
                last_tested=1000.0,
            )
        )
        # 24 hours later — should decay to 0 and expire
        decay_stale(compass, 1000.0 + 86400)
        assert compass.hypotheses[0].status == "expired"


class TestEviction:
    def test_evicts_when_over_cap(self):
        compass = new_compass()
        cfg = {**DEFAULT_HYPOTHESIS_CONFIG, "max_active": 3}

        for i in range(5):
            compass.hypotheses.append(
                BehaviorHypothesis(
                    id=f"h{i}",
                    claim=f"claim {i}",
                    confidence=0.1 * (i + 1),
                    created_at=1000.0 + i,
                    last_tested=1000.0 + i,
                )
            )

        evict_overflow(compass, cfg)
        assert len(compass.hypotheses) == 3
        # Should keep highest confidence
        ids = [h.id for h in compass.hypotheses]
        assert "h4" in ids  # highest confidence (0.5)
        assert "h3" in ids  # second highest (0.4)

    def test_removes_expired_first(self):
        compass = new_compass()
        cfg = {**DEFAULT_HYPOTHESIS_CONFIG, "max_active": 2}

        compass.hypotheses = [
            BehaviorHypothesis(id="expired1", claim="old", confidence=0.0, status="expired"),
            BehaviorHypothesis(id="active1", claim="a1", confidence=0.3),
            BehaviorHypothesis(id="active2", claim="a2", confidence=0.5),
        ]

        evict_overflow(compass, cfg)
        assert len(compass.hypotheses) == 2
        ids = [h.id for h in compass.hypotheses]
        assert "expired1" not in ids
