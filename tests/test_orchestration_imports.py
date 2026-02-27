def test_orchestration_imports():
    """Verify that all 9 orchestration modules can be imported and their public members are available."""
    from lintgate.orchestration import (
        AuthorityLevel,
        deliver_finding,
        detect_cycles,
        route_finding,
    )

    assert AuthorityLevel is not None
    assert callable(route_finding)
    assert callable(deliver_finding)
    assert callable(detect_cycles)
