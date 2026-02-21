"""Targeted tests for hook_posttooluse model-key helpers."""

from __future__ import annotations

import io
import json
import sys
from dataclasses import dataclass, field

import pytest

from lintgate.controlplane.model_profiles import ModelProfile, ModelProfileStore
from lintgate.hook_posttooluse import (
    _can_apply_session_telemetry,
    _mark_session_telemetry_applied,
    _resolve_event_model_key,
    _select_telemetry_profile,
    _session_telemetry_updates_used,
    main,
)


def test_resolve_event_model_key_from_top_level() -> None:
    assert _resolve_event_model_key({"model": "claude-opus-4"}) == "anthropic:claude-opus-4"


def test_resolve_event_model_key_from_metadata() -> None:
    payload = {"metadata": {"model_id": "gpt-4o"}}
    assert _resolve_event_model_key(payload) == "openai:gpt-4o"


def test_select_telemetry_profile_requires_explicit_model_key() -> None:
    store = ModelProfileStore(
        profiles={
            "anthropic:claude-opus-4": ModelProfile(
                model_key="anthropic:claude-opus-4",
                confidence=0.9,
            ),
            "openai:gpt-4o": ModelProfile(
                model_key="openai:gpt-4o",
                confidence=0.9,
            ),
        }
    )

    # No model identifier in payload -> no telemetry profile selected.
    assert _select_telemetry_profile(store, {}) is None


def test_select_telemetry_profile_uses_exact_match() -> None:
    profile = ModelProfile(
        model_key="anthropic:claude-opus-4",
        confidence=0.9,
    )
    store = ModelProfileStore(
        profiles={
            "anthropic:claude-opus-4": profile,
            "openai:gpt-4o": ModelProfile(
                model_key="openai:gpt-4o",
                confidence=0.9,
            ),
        }
    )

    selected = _select_telemetry_profile(store, {"model": "claude-opus-4"})
    assert selected is profile


@dataclass
class _DummySession:
    behavior_compass: dict = field(default_factory=dict)


def test_session_telemetry_counter_defaults_to_zero() -> None:
    session = _DummySession()
    assert _session_telemetry_updates_used(session) == 0
    assert _can_apply_session_telemetry(session) is True


def test_session_telemetry_counter_enforces_cap() -> None:
    session = _DummySession(behavior_compass={"_model_profile_telem_updates": 10})
    assert _session_telemetry_updates_used(session) == 10
    assert _can_apply_session_telemetry(session) is False


def test_mark_session_telemetry_applied_increments_counter() -> None:
    session = _DummySession()
    _mark_session_telemetry_applied(session)
    _mark_session_telemetry_applied(session)
    assert session.behavior_compass["_model_profile_telem_updates"] == 2


def _run_main_payload(payload: dict | list, monkeypatch: pytest.MonkeyPatch) -> tuple[int, str]:
    stdin = io.StringIO(json.dumps(payload))
    stdout = io.StringIO()
    monkeypatch.setattr(sys, "stdin", stdin)
    monkeypatch.setattr(sys, "stdout", stdout)

    with pytest.raises(SystemExit) as exc:
        main()

    return int(exc.value.code), stdout.getvalue().strip()


def test_main_exits_clean_on_non_dict_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    code, output = _run_main_payload([], monkeypatch)
    assert code == 0
    assert output == "{}"


def test_main_exits_clean_on_write_with_null_tool_input(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    code, output = _run_main_payload(
        {
            "tool_name": "Write",
            "tool_input": None,
            "tool_output": "ok",
            "cwd": str(tmp_path),
        },
        monkeypatch,
    )
    assert code == 0
    assert output == "{}"


def test_main_accepts_string_bash_tool_input(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    code, output = _run_main_payload(
        {
            "tool_name": "Bash",
            "tool_input": "pwd",
            "tool_output": "ok",
            "cwd": str(tmp_path),
        },
        monkeypatch,
    )
    assert code == 0
    assert output == "{}"


def test_main_exits_clean_on_multiedit_with_invalid_edits(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    code, output = _run_main_payload(
        {
            "tool_name": "MultiEdit",
            "tool_input": {"edits": ["bad"]},
            "tool_output": "ok",
            "cwd": str(tmp_path),
        },
        monkeypatch,
    )
    assert code == 0
    assert output == "{}"
