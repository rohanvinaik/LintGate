"""Tests for behavioral compass — data model, hypothesis management, and detection rules."""

from __future__ import annotations

import time

import pytest

from lintgate.controlplane.behavior_compass import (
    DEFAULT_HYPOTHESIS_CONFIG,
    DEFAULT_INTENT_MAP,
    DEFAULT_INTENT_SIG_MAP,
    DEFAULT_THRESHOLDS,
    INTENT_CATEGORIES,
    ApproachAttempt,
    BehaviorCompass,
    BehaviorHypothesis,
    CoverageMetrics,
    add_declared_hypothesis,
    compute_coverage,
    compute_uncertainty_zones,
    decay_stale,
    error_memory_key,
    evict_overflow,
    extract_error_sig,
    find_relevant_hypotheses,
    new_compass,
    normalize_command_sig,
    record_tool_event,
    resolve_intent,
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
        result = normalize_command_sig("curl -H 'Authorization: sk_test_abc123defghijklmnop' https://api.example.com")
        # The secret token should NOT appear in the result
        assert "sk_test" not in result
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
            compass, "Bash",
            {"command": "idevicerestore -e custom.ipsw"},
            "error: Unable to send iBSS to device\nexit code: 1",
            now=1000.0,
        )
        # Should create an auto-hypothesis at 0.3 confidence
        assert len(compass.hypotheses) == 1
        assert compass.hypotheses[0].confidence == DEFAULT_HYPOTHESIS_CONFIG["auto_generate_confidence"]
        assert compass.hypotheses[0].source == "command_failure"
        assert "idevicerestore" in compass.hypotheses[0].claim

    def test_bash_success_no_hypothesis(self):
        compass = new_compass()
        record_tool_event(
            compass, "Bash",
            {"command": "git status"},
            "On branch main\nnothing to commit",
            now=1000.0,
        )
        assert len(compass.hypotheses) == 0

    def test_updates_approach(self):
        compass = new_compass()
        record_tool_event(
            compass, "Bash",
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
            compass, "Bash",
            {"command": "idevicerestore -e fw.ipsw"},
            "error: Unable to send iBSS\nexit code: 1",
            now=1000.0,
        )
        initial_conf = compass.hypotheses[0].confidence

        # Second failure with same error should strengthen
        record_tool_event(
            compass, "Bash",
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
        compass.hypotheses.append(BehaviorHypothesis(
            id="test1", claim="test claim", confidence=0.3,
            created_at=1000.0, last_tested=1000.0,
        ))
        update_hypothesis(compass, "test1", "strengthen", "evidence A", now=1001.0)
        assert compass.hypotheses[0].confidence == pytest.approx(0.3 + 0.15)
        assert len(compass.hypotheses[0].evidence_for) == 1

    def test_weaken(self):
        compass = new_compass()
        compass.hypotheses.append(BehaviorHypothesis(
            id="test1", claim="test claim", confidence=0.5,
            created_at=1000.0, last_tested=1000.0,
        ))
        update_hypothesis(compass, "test1", "weaken", "counter evidence", now=1001.0)
        assert compass.hypotheses[0].confidence == pytest.approx(0.5 - 0.1)

    def test_promote_to_confirmed(self):
        compass = new_compass()
        compass.hypotheses.append(BehaviorHypothesis(
            id="test1", claim="test claim", confidence=0.6,
            evidence_for=["ev1"],
            created_at=1000.0, last_tested=1000.0,
        ))
        # After strengthening, should reach 0.75 with 2 evidence → confirmed
        update_hypothesis(compass, "test1", "strengthen", "ev2", now=1001.0)
        assert compass.hypotheses[0].status == "confirmed"
        assert compass.hypotheses[0].confidence >= 0.7

    def test_weaken_to_expired(self):
        compass = new_compass()
        compass.hypotheses.append(BehaviorHypothesis(
            id="test1", claim="test claim", confidence=0.05,
            created_at=1000.0, last_tested=1000.0,
        ))
        update_hypothesis(compass, "test1", "weaken", "final blow", now=1001.0)
        assert compass.hypotheses[0].status == "expired"
        assert compass.hypotheses[0].confidence == 0.0

    def test_requires_min_evidence_for_promote(self):
        compass = new_compass()
        compass.hypotheses.append(BehaviorHypothesis(
            id="test1", claim="test claim", confidence=0.6,
            # Only 0 evidence_for items
            created_at=1000.0, last_tested=1000.0,
        ))
        # After strengthening: confidence=0.75, but only 1 evidence → not confirmed yet
        update_hypothesis(compass, "test1", "strengthen", "ev1", now=1001.0)
        assert compass.hypotheses[0].status == "active"  # Not confirmed yet


class TestDecay:
    def test_decay_after_one_hour(self):
        compass = new_compass()
        compass.hypotheses.append(BehaviorHypothesis(
            id="test1", claim="test", confidence=0.5,
            created_at=1000.0, last_tested=1000.0,
        ))
        # 2 hours later
        decay_stale(compass, 1000.0 + 7200)
        assert compass.hypotheses[0].confidence < 0.5

    def test_decay_within_one_hour_is_proportional(self):
        compass = new_compass()
        compass.hypotheses.append(BehaviorHypothesis(
            id="test1", claim="test", confidence=0.5,
            created_at=1000.0, last_tested=1000.0,
        ))
        # 30 minutes later
        decay_stale(compass, 1000.0 + 1800)
        assert compass.hypotheses[0].confidence == pytest.approx(0.475, abs=1e-3)

    def test_repeated_decay_uses_incremental_elapsed_time(self):
        compass = new_compass()
        compass.hypotheses.append(BehaviorHypothesis(
            id="test1", claim="test", confidence=1.0,
            created_at=1000.0, last_tested=1000.0,
        ))
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
        compass.hypotheses.append(BehaviorHypothesis(
            id="test1", claim="test", confidence=0.05,
            created_at=1000.0, last_tested=1000.0,
        ))
        # 24 hours later — should decay to 0 and expire
        decay_stale(compass, 1000.0 + 86400)
        assert compass.hypotheses[0].status == "expired"


class TestEviction:
    def test_evicts_when_over_cap(self):
        compass = new_compass()
        cfg = {**DEFAULT_HYPOTHESIS_CONFIG, "max_active": 3}

        for i in range(5):
            compass.hypotheses.append(BehaviorHypothesis(
                id=f"h{i}", claim=f"claim {i}", confidence=0.1 * (i + 1),
                created_at=1000.0 + i, last_tested=1000.0 + i,
            ))

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


# ── Coverage and uncertainty ─────────────────────────────────────────


class TestCoverage:
    def test_basic_coverage(self):
        compass = new_compass()
        compass.hypotheses = [
            BehaviorHypothesis(id="h1", claim="a", confidence=0.8, status="confirmed"),
            BehaviorHypothesis(id="h2", claim="b", confidence=0.3, status="active"),
        ]
        compass.approaches = [
            ApproachAttempt(approach_sig="cmd:a", outcome="failed", event_count=2),
            ApproachAttempt(approach_sig="cmd:b", outcome="success", event_count=1),
        ]

        coverage = compute_coverage(compass)
        assert coverage.constraints_verified == 1  # Only h1 above threshold
        assert coverage.approaches_attempted == 2
        assert coverage.approach_success_rate == 0.5

    def test_prediction_recall(self):
        compass = new_compass()
        compass.hypotheses = [
            BehaviorHypothesis(
                id="predicted", claim="p", confidence=0.8,
                source="precheck_declared", status="confirmed",
            ),
            BehaviorHypothesis(
                id="surprise", claim="s", confidence=0.6,
                source="command_failure", status="active",
            ),
        ]

        coverage = compute_coverage(compass)
        # predicted=1 (precheck_declared + high confidence), surprise=1 (command_failure + >=0.5)
        # recall = 1 / (1 + 1) = 0.5
        assert coverage.prediction_recall == pytest.approx(0.5)


class TestUncertaintyZones:
    def test_failed_approach_without_hypothesis(self):
        compass = new_compass()
        compass.approaches = [
            ApproachAttempt(
                approach_sig="idevicerestore:restore",
                outcome="failed",
                error_sigs=["ASR signature failed"],
            ),
        ]
        # No hypotheses cover this approach
        zones = compute_uncertainty_zones(compass)
        assert len(zones) >= 1
        assert "idevicerestore:restore" in zones[0]

    def test_low_confidence_hypothesis(self):
        compass = new_compass()
        compass.hypotheses = [
            BehaviorHypothesis(
                id="h1", claim="low confidence thing",
                confidence=0.2, status="active",
            ),
        ]
        zones = compute_uncertainty_zones(compass)
        assert any("low confidence" in z.lower() for z in zones)


# ── find_relevant_hypotheses ─────────────────────────────────────────


class TestFindRelevant:
    def test_matches_wildcard_sig(self):
        compass = new_compass()
        compass.hypotheses = [
            BehaviorHypothesis(
                id="h1", claim="idevicerestore fails",
                confidence=0.5, status="active",
                applies_to_sigs=["idevicerestore:*"],
            ),
            BehaviorHypothesis(
                id="h2", claim="git issue",
                confidence=0.5, status="active",
                applies_to_sigs=["git:*"],
            ),
        ]
        relevant = find_relevant_hypotheses(compass, "idevicerestore:restore")
        assert len(relevant) == 1
        assert relevant[0].id == "h1"

    def test_matches_tool_filter(self):
        compass = new_compass()
        compass.hypotheses = [
            BehaviorHypothesis(
                id="h1", claim="bash thing",
                confidence=0.5, status="active",
                applies_to_tools=["Bash"],
            ),
        ]
        relevant = find_relevant_hypotheses(compass, tool="Bash")
        assert len(relevant) == 1

    def test_excludes_expired(self):
        compass = new_compass()
        compass.hypotheses = [
            BehaviorHypothesis(
                id="h1", claim="expired",
                confidence=0.0, status="expired",
                applies_to_sigs=["git:*"],
            ),
        ]
        relevant = find_relevant_hypotheses(compass, "git:status")
        assert len(relevant) == 0


# ── add_declared_hypothesis ──────────────────────────────────────────


class TestDeclaredHypothesis:
    def test_adds_new(self):
        compass = new_compass()
        hyp = add_declared_hypothesis(compass, "Device requires signed iBSS", "idevicerestore:restore", now=1000.0)
        assert hyp.source == "precheck_declared"
        assert hyp.confidence == 0.5
        assert len(compass.hypotheses) == 1

    def test_strengthens_existing(self):
        compass = new_compass()
        hyp1 = add_declared_hypothesis(compass, "Device requires signed iBSS", "idevicerestore:restore", now=1000.0)
        initial = hyp1.confidence

        # Re-declare same claim
        hyp2 = add_declared_hypothesis(compass, "Device requires signed iBSS", "idevicerestore:restore", now=1001.0)
        assert len(compass.hypotheses) == 1  # No duplicate
        assert compass.hypotheses[0].confidence > initial  # Strengthened


# ── Serialization ────────────────────────────────────────────────────


class TestSerialization:
    def test_compass_roundtrip(self):
        compass = new_compass()
        compass.hypotheses.append(BehaviorHypothesis(
            id="h1", claim="test", confidence=0.5,
            evidence_for=["ev1"], created_at=1000.0, last_tested=1000.0,
            applies_to_sigs=["git:*"], applies_to_tools=["Bash"],
        ))
        compass.approaches.append(ApproachAttempt(
            approach_sig="git:push", exit_codes=[0, 1],
            started_at=1000.0, last_event=1001.0,
        ))
        compass.action_history.append({"tool": "Bash", "ts": 1000.0, "sig": "git:push", "exit": 0, "err": ""})
        compass.error_memory = {
            "err01": {"count": 2, "first_seen": 900.0, "last_seen": 1000.0, "last_sig": "error X"},
        }

        data = compass.to_dict()
        restored = BehaviorCompass.from_dict(data)

        assert len(restored.hypotheses) == 1
        assert restored.hypotheses[0].id == "h1"
        assert restored.hypotheses[0].applies_to_sigs == ["git:*"]
        assert len(restored.approaches) == 1
        assert restored.approaches[0].approach_sig == "git:push"
        assert len(restored.action_history) == 1
        assert restored.error_memory["err01"]["count"] == 2

    def test_empty_compass_from_dict(self):
        compass = BehaviorCompass.from_dict({})
        assert compass.hypotheses == []
        assert compass.approaches == []

    def test_empty_compass_from_none(self):
        compass = BehaviorCompass.from_dict(None)  # type: ignore
        assert compass.hypotheses == []


# ── v2: resolve_intent ──────────────────────────────────────────────────


class TestResolveIntent:
    def test_read_is_inspect(self):
        assert resolve_intent("Read", "") == "inspect"

    def test_write_is_modify(self):
        assert resolve_intent("Write", "") == "modify"

    def test_edit_is_modify(self):
        assert resolve_intent("Edit", "") == "modify"

    def test_multiedit_is_modify(self):
        assert resolve_intent("MultiEdit", "") == "modify"

    def test_grep_is_inspect(self):
        assert resolve_intent("Grep", "") == "inspect"

    def test_bash_pytest_is_verify(self):
        assert resolve_intent("Bash", "pytest:tests") == "verify"

    def test_bash_git_status_is_inspect(self):
        assert resolve_intent("Bash", "git:status") == "inspect"

    def test_bash_git_commit_is_modify(self):
        assert resolve_intent("Bash", "git:commit") == "modify"

    def test_bash_mkdir_is_modify(self):
        assert resolve_intent("Bash", "mkdir:foo") == "modify"

    def test_bash_cat_is_inspect(self):
        assert resolve_intent("Bash", "cat:file") == "inspect"

    def test_hfsplus_ls_is_inspect(self):
        assert resolve_intent("Bash", "hfsplus:ls") == "inspect"

    def test_hfsplus_rootfs_is_inspect(self):
        assert resolve_intent("Bash", "hfsplus:rootfs") == "inspect"

    def test_bash_no_sig_is_execute(self):
        """Bash with no command_sig falls through to execute."""
        assert resolve_intent("Bash", "") == "execute"

    def test_unknown_tool_is_unknown(self):
        assert resolve_intent("SomeUnknownTool", "") == "unknown"

    def test_custom_intent_map_override(self):
        custom = {"cargo": "execute"}
        assert resolve_intent("Bash", "cargo:build", intent_map=custom) == "execute"

    def test_custom_sig_map_override(self):
        custom_sig = {"cargo:test": "verify"}
        assert resolve_intent("Bash", "cargo:test", intent_sig_map=custom_sig) == "verify"

    def test_sig_map_takes_priority_over_bin_map(self):
        """Exact sig match checked before binary-only match."""
        # git default is "meta" but git:status is "inspect"
        assert resolve_intent("Bash", "git:status") == "inspect"

    def test_all_categories_valid(self):
        """All resolved intents must be in INTENT_CATEGORIES."""
        test_cases = [
            ("Read", ""), ("Write", ""), ("Bash", "pytest:tests"),
            ("Bash", "git:status"), ("Bash", "mkdir:foo"),
            ("Bash", ""), ("UnknownTool", ""),
        ]
        for tool, sig in test_cases:
            result = resolve_intent(tool, sig)
            assert result in INTENT_CATEGORIES, f"{tool}/{sig} → {result} not in categories"


# ── v2: intent history tracking ─────────────────────────────────────────


class TestIntentHistory:
    def test_intent_appended_on_record(self):
        compass = new_compass()
        record_tool_event(compass, "Read", {}, "", now=100.0)
        assert compass.intent_history == ["inspect"]

    def test_intent_appended_for_bash(self):
        compass = new_compass()
        record_tool_event(compass, "Bash", {"command": "pytest tests/"}, "exit_code: 0", now=100.0)
        assert len(compass.intent_history) == 1
        assert compass.intent_history[0] == "verify"

    def test_intent_rolls_at_max(self):
        compass = new_compass()
        for i in range(35):
            record_tool_event(compass, "Read", {}, "", now=100.0 + i)
        assert len(compass.intent_history) <= 30

    def test_intent_in_event_record(self):
        compass = new_compass()
        record_tool_event(compass, "Read", {}, "", now=100.0)
        assert compass.action_history[-1].get("intent") == "inspect"

    def test_backward_compat_empty_intent_history(self):
        """Old serialized compass without intent_history defaults to []."""
        old_data = {"hypotheses": [], "approaches": []}
        compass = BehaviorCompass.from_dict(old_data)
        assert compass.intent_history == []


# ── v2: hypothesis_version tracking ─────────────────────────────────────


class TestHypothesisVersion:
    def test_version_increments_on_auto_generate(self):
        compass = new_compass()
        record_tool_event(
            compass, "Bash", {"command": "idevicerestore -e foo.ipsw"},
            "ERROR: unable to send iBSS\nexit_code: 1", now=100.0,
        )
        assert compass.hypothesis_version >= 1

    def test_version_increments_on_strengthen(self):
        compass = new_compass()
        hyp = BehaviorHypothesis(
            id="test1234", claim="test fails",
            confidence=0.4, source="command_failure",
            created_at=100.0, last_tested=100.0, last_decay=100.0,
        )
        compass.hypotheses.append(hyp)
        v_before = compass.hypothesis_version
        update_hypothesis(compass, "test1234", "strengthen", "evidence", now=101.0)
        assert compass.hypothesis_version == v_before + 1

    def test_version_increments_on_weaken(self):
        compass = new_compass()
        hyp = BehaviorHypothesis(
            id="test1234", claim="test fails",
            confidence=0.5, source="command_failure",
            created_at=100.0, last_tested=100.0, last_decay=100.0,
        )
        compass.hypotheses.append(hyp)
        v_before = compass.hypothesis_version
        update_hypothesis(compass, "test1234", "weaken", "evidence", now=101.0)
        assert compass.hypothesis_version == v_before + 1

    def test_version_increments_on_decay_expire(self):
        compass = new_compass()
        hyp = BehaviorHypothesis(
            id="test1234", claim="test fails",
            confidence=0.01, source="command_failure",
            created_at=100.0, last_tested=100.0, last_decay=100.0,
        )
        compass.hypotheses.append(hyp)
        v_before = compass.hypothesis_version
        # Decay enough to expire
        decay_stale(compass, 100.0 + 3600 * 10)  # 10 hours later
        assert compass.hypothesis_version > v_before

    def test_version_unchanged_on_noop(self):
        compass = new_compass()
        v_before = compass.hypothesis_version
        # Read event — no hypothesis mutation
        record_tool_event(compass, "Read", {}, "", now=100.0)
        assert compass.hypothesis_version == v_before


# ── v2: approach hyp_version tracking ───────────────────────────────────


class TestApproachHypVersionTracking:
    def test_new_approach_captures_version(self):
        compass = new_compass()
        compass.hypothesis_version = 5
        record_tool_event(
            compass, "Bash", {"command": "make build"}, "exit_code: 0", now=100.0,
        )
        assert len(compass.approaches) == 1
        assert compass.approaches[0].hyp_version_at_start == 5

    def test_existing_approach_retains_original(self):
        compass = new_compass()
        compass.hypothesis_version = 3
        record_tool_event(
            compass, "Bash", {"command": "make build"}, "exit_code: 0", now=100.0,
        )
        compass.hypothesis_version = 7
        record_tool_event(
            compass, "Bash", {"command": "make build"}, "exit_code: 0", now=101.0,
        )
        assert compass.approaches[0].hyp_version_at_start == 3


# ── v2: event_counter tracking ──────────────────────────────────────────


class TestEventCounter:
    def test_counter_increments(self):
        compass = new_compass()
        assert compass.event_counter == 0
        record_tool_event(compass, "Read", {}, "", now=100.0)
        assert compass.event_counter == 1
        record_tool_event(compass, "Bash", {"command": "ls"}, "exit_code: 0", now=101.0)
        assert compass.event_counter == 2


# ── v2: precheck_count_session ──────────────────────────────────────────


class TestPrecheckCountSession:
    def test_not_incremented_by_declared_hypothesis_add(self):
        compass = new_compass()
        add_declared_hypothesis(compass, "test constraint", "test:cmd", now=100.0)
        assert compass.precheck_count_session == 0

    def test_not_incremented_by_strengthen_existing(self):
        compass = new_compass()
        add_declared_hypothesis(compass, "test constraint", "test:cmd", now=100.0)
        assert compass.precheck_count_session == 0
        add_declared_hypothesis(compass, "test constraint", "test:cmd", now=101.0)
        assert compass.precheck_count_session == 0

    def test_not_incremented_by_failure(self):
        compass = new_compass()
        record_tool_event(
            compass, "Bash", {"command": "bad_cmd"},
            "error: something failed\nexit_code: 1", now=100.0,
        )
        assert compass.precheck_count_session == 0


# ── v2: serialization of new fields ─────────────────────────────────────


class TestSerializationV2:
    def test_new_fields_roundtrip(self):
        compass = new_compass()
        compass.intent_history = ["inspect", "modify", "execute"]
        compass.hypothesis_version = 7
        compass.precheck_count_session = 3
        compass.event_counter = 42
        compass.last_fired = {"approach_cycling": 10}
        compass.signal_fire_counts = {"approach_cycling": 2}
        compass.early_nudge_emitted = True
        compass.error_memory = {
            "err01": {"count": 2, "first_seen": 10.0, "last_seen": 20.0, "last_sig": "error sig"},
        }

        data = compass.to_dict()
        restored = BehaviorCompass.from_dict(data)

        assert restored.intent_history == ["inspect", "modify", "execute"]
        assert restored.hypothesis_version == 7
        assert restored.precheck_count_session == 3
        assert restored.event_counter == 42
        assert restored.last_fired == {"approach_cycling": 10}
        assert restored.signal_fire_counts == {"approach_cycling": 2}
        assert restored.early_nudge_emitted is True
        assert restored.error_memory["err01"]["count"] == 2

    def test_backward_compat_old_dict(self):
        """Old serialized data without v2 fields defaults correctly."""
        old_data = {
            "hypotheses": [],
            "approaches": [],
            "coverage": {},
            "uncertainty_zones": [],
            "action_history": [],
        }
        compass = BehaviorCompass.from_dict(old_data)
        assert compass.intent_history == []
        assert compass.error_memory == {}
        assert compass.hypothesis_version == 0
        assert compass.precheck_count_session == 0
        assert compass.event_counter == 0
        assert compass.last_fired == {}
        assert compass.signal_fire_counts == {}
        assert compass.early_nudge_emitted is False

    def test_approach_attempt_hyp_version_roundtrip(self):
        approach = ApproachAttempt(
            approach_sig="test:cmd", hyp_version_at_start=5,
            started_at=100.0, last_event=100.0,
        )
        data = approach.to_dict()
        restored = ApproachAttempt.from_dict(data)
        assert restored.hyp_version_at_start == 5

    def test_approach_attempt_backward_compat(self):
        old_data = {"approach_sig": "test:cmd"}
        approach = ApproachAttempt.from_dict(old_data)
        assert approach.hyp_version_at_start == 0
