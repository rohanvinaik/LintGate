"""Mutation-targeted tests for work_queue.py.

Targets VALUE and SWAP surviving mutants.
"""

from __future__ import annotations

from lintgate.controlplane.work_queue import (
    QueuedFinding,
    WorkQueue,
    _compute_dependency_tiers,
    build_work_queue,
)

# ── WorkQueue.to_dict VALUE tests ─────────────────────────────────


def test_work_queue_to_dict_empty():
    wq = WorkQueue()
    d = wq.to_dict()
    assert d == {"items": [], "total_files": 0, "parallelizable_groups": []}


def test_work_queue_to_dict_with_items():
    item = QueuedFinding(file="a.py", finding_ids=["F1"], tier=0, severity="warning")
    wq = WorkQueue(items=[item], total_files=1, parallelizable_groups=[["a.py"]])
    d = wq.to_dict()
    assert d["total_files"] == 1
    assert len(d["items"]) == 1
    assert d["items"][0]["file"] == "a.py"
    assert d["parallelizable_groups"] == [["a.py"]]


def test_work_queue_to_dict_preserves_all_item_fields():
    item = QueuedFinding(
        file="b.py",
        finding_ids=["X1", "X2"],
        tier=2,
        severity="blocking",
        locality="cross_file",
        depends_on=["a.py"],
        delegation_safe=False,
    )
    wq = WorkQueue(items=[item], total_files=1)
    d = wq.to_dict()
    item_d = d["items"][0]
    assert item_d["finding_ids"] == ["X1", "X2"]
    assert item_d["tier"] == 2
    assert item_d["severity"] == "blocking"
    assert item_d["locality"] == "cross_file"
    assert item_d["depends_on"] == ["a.py"]
    assert item_d["delegation_safe"] is False


# ── _compute_dependency_tiers SWAP tests ──────────────────────────


def test_dependency_tiers_leaf_modules():
    graph = {"a": [], "b": []}
    tiers = _compute_dependency_tiers(graph, {"a", "b"})
    assert tiers["a"] == 0
    assert tiers["b"] == 0


def test_dependency_tiers_linear_chain():
    graph = {"a": [], "b": ["a"], "c": ["b"]}
    tiers = _compute_dependency_tiers(graph, {"a", "b", "c"})
    assert tiers["a"] == 0
    assert tiers["b"] == 1
    assert tiers["c"] == 2


def test_dependency_tiers_swap_args_differ():
    """Different graphs produce different tiers — catches SWAP mutations."""
    graph_1 = {"a": [], "b": ["a"]}
    graph_2 = {"a": ["b"], "b": []}
    tiers_1 = _compute_dependency_tiers(graph_1, {"a", "b"})
    tiers_2 = _compute_dependency_tiers(graph_2, {"a", "b"})
    assert tiers_1 != tiers_2


def test_dependency_tiers_cycle_detection():
    graph = {"a": ["b"], "b": ["a"]}
    tiers = _compute_dependency_tiers(graph, {"a", "b"})
    # Cycle modules get max_tier + 1
    assert all(v > 0 for v in tiers.values())


# ── build_work_queue integration ──────────────────────────────────


def _make_finding(file, kind="F001", severity="warning"):
    from unittest.mock import MagicMock

    f = MagicMock()
    f.file = file
    f.kind = kind
    f.severity = severity
    return f


def test_build_work_queue_groups_by_file():
    findings = [_make_finding("a.py"), _make_finding("a.py", kind="F002"), _make_finding("b.py")]
    wq = build_work_queue(findings)
    assert wq.total_files == 2
    files = {item.file for item in wq.items}
    assert files == {"a.py", "b.py"}


def test_build_work_queue_severity_ordering():
    findings = [
        _make_finding("info.py", severity="informational"),
        _make_finding("block.py", severity="blocking"),
        _make_finding("warn.py", severity="warning"),
    ]
    wq = build_work_queue(findings)
    severities = [item.severity for item in wq.items]
    assert severities.index("blocking") < severities.index("informational")
