from lintgate.controlplane.session_memory import SessionMemory
from lintgate.orchestration.repertoire import RepertoireManager


def test_repertoire_manager_capture():
    session = SessionMemory()
    session.action_history = [
        {"intent": "fix", "tool": "replace_file_content"},
        {"intent": "verify", "tool": "run_command"},
        {"intent": "commit", "tool": "run_command"},
    ]

    manager = RepertoireManager(session)

    # Track new finding
    manager.track_findings({"foo_error"}, 1, 0)
    assert "foo_error" in session.active_finding_history

    # Resolve finding at step 3
    manager.track_findings(set(), 2, 3)
    assert "foo_error" not in session.active_finding_history
    assert len(session.resolution_repertoire) == 1
    record = session.resolution_repertoire[0]
    assert record["finding_kind"] == "foo_error"
    assert len(record["resolution_steps"]) == 3

    hint = manager.query_repertoire("foo_error")
    assert "Previously resolved via" in hint


def test_repertoire_bounded_lru():
    session = SessionMemory()
    manager = RepertoireManager(session)

    for i in range(60):
        session.action_history.append({"intent": f"step_{i}", "tool": "test"})
        manager.track_findings({f"error_{i}"}, i, i)
        manager.track_findings(set(), i + 1, i + 1)

    assert len(session.resolution_repertoire) == 50
    assert session.resolution_repertoire[-1]["finding_kind"] == "error_59"
