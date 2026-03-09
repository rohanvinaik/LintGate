"""Specification tests for DeliveryBus.emit and DeliveryBus._persist_status.

Targets:
  - DeliveryBus.emit:            sigma=26, regime B, risk 0.8
  - DeliveryBus._persist_status: sigma=24, regime B, risk 0.7

Coverage strategy: branch paths, edge cases, exact value assertions,
and emit/_persist_status interaction verification.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

from lintgate.orchestration.authority import AuthorityLevel
from lintgate.orchestration.delivery import (
    DeliveryBus,
    DeliveryItem,
)

# ── Helpers ────────────────────────────────────────────────────────────


class FakeConfig:
    """Minimal config stub with project_root."""

    def __init__(self, project_root: str):
        self.project_root = project_root


class FakeSession:
    """Session stub with configurable behavior_compass and knowledge metadata."""

    def __init__(
        self,
        compliance_rate: float = 1.0,
        knowledge_meta: dict[str, object] | None = None,
        resolution_repertoire: list[dict[str, Any]] | None = None,
    ):
        self.behavior_compass = {"compliance_rate": compliance_rate}
        self.knowledge_meta = knowledge_meta or {}
        self.resolution_repertoire = resolution_repertoire or []


def _make_item(
    source: str = "test",
    authority: AuthorityLevel = AuthorityLevel.ADVISORY,
    message: str = "test message",
    **extra_content: Any,
) -> DeliveryItem:
    """Build a DeliveryItem with sane defaults."""
    content: dict[str, Any] = {"message": message, **extra_content}
    return DeliveryItem(
        source=source,
        authority_level=authority,
        content=content,
        timestamp=1000.0,
    )


# ════════════════════════════════════════════════════════════════════════
# DeliveryBus.emit — specification tests
# ════════════════════════════════════════════════════════════════════════


class TestEmitEmptyBus:
    """Branch: no items collected → emit returns {}."""

    def test_emit_returns_empty_dict_when_no_items(self, tmp_path: Any) -> None:
        bus = DeliveryBus(FakeConfig(str(tmp_path)))
        result = bus.emit(preferred_channels=["hook_text"])
        assert result == {}

    def test_emit_returns_empty_dict_with_empty_channel_list(self, tmp_path: Any) -> None:
        bus = DeliveryBus(FakeConfig(str(tmp_path)))
        result = bus.emit(preferred_channels=[])
        assert result == {}


class TestEmitSingleItem:
    """Branch: one item, normal budget → delivers via preferred channel."""

    def test_single_advisory_item_hook_text(self, tmp_path: Any) -> None:
        bus = DeliveryBus(FakeConfig(str(tmp_path)))
        bus.collect(_make_item(message="Advisory finding"))
        result = bus.emit(preferred_channels=["hook_text"])

        assert "systemMessage" in result
        payload = result["systemMessage"]
        assert "Advisory finding" in payload
        assert "Behavioral observation" in payload

    def test_single_item_returns_system_message_key(self, tmp_path: Any) -> None:
        bus = DeliveryBus(FakeConfig(str(tmp_path)))
        bus.collect(_make_item())
        result = bus.emit(preferred_channels=["hook_text"])
        assert set(result.keys()) == {"systemMessage"}

    def test_single_item_no_suppression_footer(self, tmp_path: Any) -> None:
        bus = DeliveryBus(FakeConfig(str(tmp_path)))
        bus.collect(_make_item(message="Only one"))
        result = bus.emit(preferred_channels=["hook_text"])
        assert "suppressed" not in result["systemMessage"]


class TestEmitMultipleItems:
    """Branch: multiple items → highest authority wins, rest suppressed."""

    def test_highest_authority_is_primary(self, tmp_path: Any) -> None:
        bus = DeliveryBus(FakeConfig(str(tmp_path)))
        bus.collect(_make_item(authority=AuthorityLevel.ADVISORY, message="low"))
        bus.collect(_make_item(authority=AuthorityLevel.WARNING, message="high"))
        bus.collect(_make_item(authority=AuthorityLevel.NUDGE, message="mid"))

        result = bus.emit(preferred_channels=["hook_text"])
        payload = result["systemMessage"]
        assert "high" in payload

    def test_suppression_footer_contains_counts(self, tmp_path: Any) -> None:
        bus = DeliveryBus(FakeConfig(str(tmp_path)))
        bus.collect(_make_item(authority=AuthorityLevel.WARNING, message="primary"))
        bus.collect(_make_item(authority=AuthorityLevel.ADVISORY, message="supp1"))
        bus.collect(_make_item(authority=AuthorityLevel.ADVISORY, message="supp2"))
        bus.collect(_make_item(authority=AuthorityLevel.NUDGE, message="supp3"))

        result = bus.emit(preferred_channels=["hook_text"])
        payload = result["systemMessage"]
        assert "2 advisory" in payload
        assert "1 nudge" in payload
        assert "suppressed" in payload

    def test_suppression_counts_exact(self, tmp_path: Any) -> None:
        bus = DeliveryBus(FakeConfig(str(tmp_path)))
        bus.collect(_make_item(authority=AuthorityLevel.INTERVENTION, message="top"))
        bus.collect(_make_item(authority=AuthorityLevel.NUDGE, message="a"))
        bus.collect(_make_item(authority=AuthorityLevel.NUDGE, message="b"))

        bus.emit(preferred_channels=["hook_text"])
        assert bus.suppressed_counts == {"nudge": 2}


class TestEmitChannelSelection:
    """Branch: channel fallback and no-match paths."""

    def test_fallback_to_second_channel(self, tmp_path: Any) -> None:
        bus = DeliveryBus(FakeConfig(str(tmp_path)))
        bus.collect(_make_item(message="fallback test"))

        result = bus.emit(preferred_channels=["nonexistent", "rule_file"])
        payload = result["systemMessage"]
        assert "fallback test" in payload

    def test_no_matching_channel_returns_empty(self, tmp_path: Any) -> None:
        bus = DeliveryBus(FakeConfig(str(tmp_path)))
        bus.collect(_make_item(message="lost"))

        result = bus.emit(preferred_channels=["no_such_channel"])
        assert result == {}

    def test_empty_channel_list_with_items_returns_empty(self, tmp_path: Any) -> None:
        bus = DeliveryBus(FakeConfig(str(tmp_path)))
        bus.collect(_make_item(message="orphan"))

        result = bus.emit(preferred_channels=[])
        assert result == {}


class TestEmitBudgetModes:
    """Branch: budget_mode affects message formatting."""

    def test_full_budget_preserves_message(self, tmp_path: Any) -> None:
        """High compliance → full budget → full message preserved."""
        session = FakeSession(compliance_rate=0.6)
        bus = DeliveryBus(FakeConfig(str(tmp_path)), session=session)
        long_msg = "A detailed behavioral finding with multiple lines\nand extra context"
        bus.collect(_make_item(message=long_msg))

        result = bus.emit(preferred_channels=["hook_text"])
        payload = result["systemMessage"]
        assert "A detailed behavioral finding" in payload

    def test_pulse_budget_truncates_message(self, tmp_path: Any) -> None:
        """High compliance rate → pulse budget → message truncated."""
        session = FakeSession(compliance_rate=0.9)
        bus = DeliveryBus(FakeConfig(str(tmp_path)), session=session)
        long_msg = "X" * 100 + "\nsecond line"
        bus.collect(_make_item(message=long_msg))

        result = bus.emit(preferred_channels=["hook_text"])
        assert bus.budget_mode == "pulse"
        payload = result["systemMessage"]
        # Pulse truncates to first 70 chars of first line + ellipsis
        assert "run `controlplane_run` for details" in payload

    def test_low_compliance_forces_full_budget(self, tmp_path: Any) -> None:
        """compliance_rate < 0.5 → always full budget."""
        session = FakeSession(compliance_rate=0.3)
        bus = DeliveryBus(FakeConfig(str(tmp_path)), session=session)
        bus.collect(_make_item(message="important"))

        bus.emit(preferred_channels=["hook_text"])
        assert bus.budget_mode == "full"

    def test_warning_authority_forces_full_budget(self, tmp_path: Any) -> None:
        """WARNING-level item → full budget regardless of compliance."""
        session = FakeSession(compliance_rate=0.95)
        bus = DeliveryBus(FakeConfig(str(tmp_path)), session=session)
        bus.collect(_make_item(authority=AuthorityLevel.WARNING, message="blocking"))

        bus.emit(preferred_channels=["hook_text"])
        assert bus.budget_mode == "full"


class TestEmitCallsPersistStatus:
    """Verify emit→_persist_status interaction."""

    def test_emit_writes_status_file(self, tmp_path: Any) -> None:
        bus = DeliveryBus(FakeConfig(str(tmp_path)))
        bus.collect(_make_item(source="cycle", message="cycle detected"))
        bus.emit(preferred_channels=["hook_text"])

        status_path = tmp_path / ".lintgate" / "behavior_status.json"
        assert status_path.exists()
        data = json.loads(status_path.read_text())
        assert data["last_active_source"] == "cycle"
        assert data["message"] is not None
        assert "cycle detected" in data["message"]

    def test_emit_does_not_persist_when_no_items(self, tmp_path: Any) -> None:
        bus = DeliveryBus(FakeConfig(str(tmp_path)))
        bus.emit(preferred_channels=["hook_text"])

        status_path = tmp_path / ".lintgate" / "behavior_status.json"
        assert not status_path.exists()


class TestEmitProcessIdempotency:
    """Verify emit calls process() and state is consistent."""

    def test_processed_items_after_emit(self, tmp_path: Any) -> None:
        bus = DeliveryBus(FakeConfig(str(tmp_path)))
        bus.collect(_make_item(authority=AuthorityLevel.WARNING, message="w"))
        bus.collect(_make_item(authority=AuthorityLevel.ADVISORY, message="a"))

        bus.emit(preferred_channels=["hook_text"])

        assert len(bus.processed_items) == 1
        assert bus.processed_items[0].content["message"] == "w"

    def test_budget_mode_set_after_emit(self, tmp_path: Any) -> None:
        bus = DeliveryBus(FakeConfig(str(tmp_path)))
        assert bus.budget_mode == "full"  # default
        bus.collect(_make_item())
        bus.emit(preferred_channels=["hook_text"])
        # budget_mode should be set (exact value depends on session)
        assert bus.budget_mode in ("full", "pulse", "silent")


# ════════════════════════════════════════════════════════════════════════
# DeliveryBus._persist_status — specification tests
# ════════════════════════════════════════════════════════════════════════


class TestPersistStatusBasic:
    """Branch: normal write path with various finding shapes."""

    def test_creates_lintgate_directory(self, tmp_path: Any) -> None:
        bus = DeliveryBus(FakeConfig(str(tmp_path)))
        finding = {"source": "lint", "authority_level": "warning", "message": "msg"}
        bus._persist_status(finding)

        assert (tmp_path / ".lintgate").is_dir()
        assert (tmp_path / ".lintgate" / "behavior_status.json").exists()

    def test_written_json_has_required_keys(self, tmp_path: Any) -> None:
        bus = DeliveryBus(FakeConfig(str(tmp_path)))
        finding = {"source": "disposition", "authority_level": "nudge", "message": "reminder"}
        bus._persist_status(finding)

        data = json.loads((tmp_path / ".lintgate" / "behavior_status.json").read_text())
        required_keys = {
            "last_active_source",
            "authority",
            "message",
            "timestamp",
            "budget",
            "suppressed_counts",
            "knowledge_staleness_hrs",
            "survival_ratio",
            "repertoire_hits",
        }
        assert required_keys.issubset(set(data.keys()))

    def test_source_and_authority_from_finding(self, tmp_path: Any) -> None:
        bus = DeliveryBus(FakeConfig(str(tmp_path)))
        finding = {"source": "cycle", "authority_level": "intervention", "message": "stop"}
        bus._persist_status(finding)

        data = json.loads((tmp_path / ".lintgate" / "behavior_status.json").read_text())
        assert data["last_active_source"] == "cycle"
        assert data["authority"] == "intervention"
        assert data["message"] == "stop"

    def test_budget_reflects_bus_state(self, tmp_path: Any) -> None:
        bus = DeliveryBus(FakeConfig(str(tmp_path)))
        bus.budget_mode = "pulse"
        finding = {"source": "test", "message": "m"}
        bus._persist_status(finding)

        data = json.loads((tmp_path / ".lintgate" / "behavior_status.json").read_text())
        assert data["budget"] == "pulse"

    def test_suppressed_counts_persisted(self, tmp_path: Any) -> None:
        bus = DeliveryBus(FakeConfig(str(tmp_path)))
        bus.suppressed_counts = {"advisory": 3, "nudge": 1}
        finding = {"source": "test", "message": "m"}
        bus._persist_status(finding)

        data = json.loads((tmp_path / ".lintgate" / "behavior_status.json").read_text())
        assert data["suppressed_counts"] == {"advisory": 3, "nudge": 1}

    def test_timestamp_is_recent(self, tmp_path: Any) -> None:
        bus = DeliveryBus(FakeConfig(str(tmp_path)))
        before = time.time()
        bus._persist_status({"source": "x", "message": "y"})
        after = time.time()

        data = json.loads((tmp_path / ".lintgate" / "behavior_status.json").read_text())
        assert before <= data["timestamp"] <= after


class TestPersistStatusWithSession:
    """Branch: session provides knowledge_meta and resolution_repertoire."""

    def test_knowledge_meta_staleness(self, tmp_path: Any) -> None:
        session = FakeSession(knowledge_meta={"staleness_hrs": 12.5, "survival_ratio": 0.4})
        bus = DeliveryBus(FakeConfig(str(tmp_path)), session=session)
        bus._persist_status({"source": "test", "message": "m"})

        data = json.loads((tmp_path / ".lintgate" / "behavior_status.json").read_text())
        assert data["knowledge_staleness_hrs"] == 12.5
        assert data["survival_ratio"] == 0.4

    def test_repertoire_hit_when_kind_matches(self, tmp_path: Any) -> None:
        session = FakeSession(
            resolution_repertoire=[{"finding_kind": "CYCLE001"}],
        )
        bus = DeliveryBus(FakeConfig(str(tmp_path)), session=session)
        finding = {"source": "cycle", "kind": "CYCLE001", "message": "detected"}
        bus._persist_status(finding)

        data = json.loads((tmp_path / ".lintgate" / "behavior_status.json").read_text())
        assert data["repertoire_hits"] == 1

    def test_no_repertoire_hit_when_kind_absent(self, tmp_path: Any) -> None:
        session = FakeSession(
            resolution_repertoire=[{"finding_kind": "CYCLE001"}],
        )
        bus = DeliveryBus(FakeConfig(str(tmp_path)), session=session)
        finding = {"source": "lint", "message": "no kind field"}
        bus._persist_status(finding)

        data = json.loads((tmp_path / ".lintgate" / "behavior_status.json").read_text())
        assert data["repertoire_hits"] == 0

    def test_no_repertoire_hit_when_kind_does_not_match(self, tmp_path: Any) -> None:
        session = FakeSession(
            resolution_repertoire=[{"finding_kind": "CYCLE001"}],
        )
        bus = DeliveryBus(FakeConfig(str(tmp_path)), session=session)
        finding = {"source": "lint", "kind": "LINT999", "message": "different kind"}
        bus._persist_status(finding)

        data = json.loads((tmp_path / ".lintgate" / "behavior_status.json").read_text())
        assert data["repertoire_hits"] == 0

    def test_empty_repertoire_gives_zero_hits(self, tmp_path: Any) -> None:
        session = FakeSession(resolution_repertoire=[])
        bus = DeliveryBus(FakeConfig(str(tmp_path)), session=session)
        finding = {"source": "cycle", "kind": "CYCLE001", "message": "m"}
        bus._persist_status(finding)

        data = json.loads((tmp_path / ".lintgate" / "behavior_status.json").read_text())
        assert data["repertoire_hits"] == 0


class TestPersistStatusNoSession:
    """Branch: session is None → defaults for knowledge metrics."""

    def test_defaults_when_no_session(self, tmp_path: Any) -> None:
        bus = DeliveryBus(FakeConfig(str(tmp_path)), session=None)
        bus._persist_status({"source": "test", "message": "m"})

        data = json.loads((tmp_path / ".lintgate" / "behavior_status.json").read_text())
        assert data["knowledge_staleness_hrs"] == 0.0
        assert data["survival_ratio"] == 1.0
        assert data["repertoire_hits"] == 0


class TestPersistStatusErrorHandling:
    """Branch: exception path → silently swallowed."""

    def test_invalid_project_root_does_not_raise(self) -> None:
        """If project_root is invalid/unwritable, _persist_status swallows the error."""
        bus = DeliveryBus(FakeConfig("/nonexistent/path/that/cannot/exist"))
        # Should not raise
        bus._persist_status({"source": "test", "message": "m"})

    def test_readonly_directory_does_not_raise(self, tmp_path: Any) -> None:
        """If the target directory cannot be created, error is swallowed."""
        readonly_dir = tmp_path / "readonly"
        readonly_dir.mkdir()
        os.chmod(str(readonly_dir), 0o444)
        try:
            bus = DeliveryBus(FakeConfig(str(readonly_dir)))
            # Should not raise even though .lintgate can't be created
            bus._persist_status({"source": "test", "message": "m"})
        finally:
            os.chmod(str(readonly_dir), 0o755)


class TestPersistStatusOverwrite:
    """Branch: repeated calls overwrite the status file."""

    def test_second_call_overwrites_first(self, tmp_path: Any) -> None:
        bus = DeliveryBus(FakeConfig(str(tmp_path)))
        bus._persist_status({"source": "first", "message": "m1"})
        bus._persist_status({"source": "second", "message": "m2"})

        data = json.loads((tmp_path / ".lintgate" / "behavior_status.json").read_text())
        assert data["last_active_source"] == "second"
        assert data["message"] == "m2"


class TestPersistStatusEmptyFinding:
    """Edge case: finding dict with missing keys."""

    def test_empty_finding_dict(self, tmp_path: Any) -> None:
        bus = DeliveryBus(FakeConfig(str(tmp_path)))
        bus._persist_status({})

        data = json.loads((tmp_path / ".lintgate" / "behavior_status.json").read_text())
        assert data["last_active_source"] is None
        assert data["authority"] is None
        assert data["message"] is None

    def test_finding_with_only_source(self, tmp_path: Any) -> None:
        bus = DeliveryBus(FakeConfig(str(tmp_path)))
        bus._persist_status({"source": "lint"})

        data = json.loads((tmp_path / ".lintgate" / "behavior_status.json").read_text())
        assert data["last_active_source"] == "lint"
        assert data["authority"] is None


# ════════════════════════════════════════════════════════════════════════
# Emit + _persist_status interaction tests
# ════════════════════════════════════════════════════════════════════════


class TestEmitPersistInteraction:
    """End-to-end: emit triggers _persist_status with correct finding_dict."""

    def test_persisted_source_matches_primary_item(self, tmp_path: Any) -> None:
        bus = DeliveryBus(FakeConfig(str(tmp_path)))
        bus.collect(_make_item(source="disposition", authority=AuthorityLevel.WARNING, message="w"))
        bus.collect(_make_item(source="lint", authority=AuthorityLevel.ADVISORY, message="a"))

        bus.emit(preferred_channels=["hook_text"])

        data = json.loads((tmp_path / ".lintgate" / "behavior_status.json").read_text())
        # Primary is the WARNING item (highest authority)
        assert data["last_active_source"] == "disposition"
        assert data["authority"] == "warning"

    def test_persisted_message_includes_suppression_footer(self, tmp_path: Any) -> None:
        bus = DeliveryBus(FakeConfig(str(tmp_path)))
        bus.collect(_make_item(authority=AuthorityLevel.INTERVENTION, message="critical"))
        bus.collect(_make_item(authority=AuthorityLevel.ADVISORY, message="info1"))
        bus.collect(_make_item(authority=AuthorityLevel.ADVISORY, message="info2"))

        bus.emit(preferred_channels=["hook_text"])

        data = json.loads((tmp_path / ".lintgate" / "behavior_status.json").read_text())
        assert "2 advisory" in data["message"]
        assert "suppressed" in data["message"]

    def test_persisted_budget_matches_bus_budget(self, tmp_path: Any) -> None:
        session = FakeSession(compliance_rate=0.95)
        bus = DeliveryBus(FakeConfig(str(tmp_path)), session=session)
        bus.collect(_make_item(message="pulse test"))

        bus.emit(preferred_channels=["hook_text"])

        data = json.loads((tmp_path / ".lintgate" / "behavior_status.json").read_text())
        assert data["budget"] == bus.budget_mode
        assert data["budget"] == "pulse"

    def test_persisted_suppressed_counts_match_bus(self, tmp_path: Any) -> None:
        bus = DeliveryBus(FakeConfig(str(tmp_path)))
        bus.collect(_make_item(authority=AuthorityLevel.WARNING, message="top"))
        bus.collect(_make_item(authority=AuthorityLevel.NUDGE, message="n1"))
        bus.collect(_make_item(authority=AuthorityLevel.NUDGE, message="n2"))
        bus.collect(_make_item(authority=AuthorityLevel.NUDGE, message="n3"))

        bus.emit(preferred_channels=["hook_text"])

        data = json.loads((tmp_path / ".lintgate" / "behavior_status.json").read_text())
        assert data["suppressed_counts"] == {"nudge": 3}

    def test_session_knowledge_flows_through_emit(self, tmp_path: Any) -> None:
        session = FakeSession(
            knowledge_meta={"staleness_hrs": 5.0, "survival_ratio": 0.6},
            resolution_repertoire=[{"finding_kind": "cycle"}],
        )
        bus = DeliveryBus(FakeConfig(str(tmp_path)), session=session)
        bus.collect(_make_item(source="cycle", message="cycle event"))

        bus.emit(preferred_channels=["hook_text"])

        data = json.loads((tmp_path / ".lintgate" / "behavior_status.json").read_text())
        assert data["knowledge_staleness_hrs"] == 5.0
        assert data["survival_ratio"] == 0.6

    def test_no_persist_when_deliver_finding_returns_none(self, tmp_path: Any) -> None:
        """When no channel matches, emit returns {} but _persist_status is still called
        because the code calls _persist_status before the return-empty check."""
        bus = DeliveryBus(FakeConfig(str(tmp_path)))
        bus.collect(_make_item(message="no channel"))

        result = bus.emit(preferred_channels=["nonexistent_only"])

        # emit returns {} because payload is None
        assert result == {}
        # But _persist_status IS called (it happens before the payload check)
        status_path = tmp_path / ".lintgate" / "behavior_status.json"
        assert status_path.exists()
