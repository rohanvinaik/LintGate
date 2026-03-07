"""API module that calls core functions (cross-module)."""

from cross_module.core import compute, load_config


def process(x, y):
    """Process data using core computation."""
    result = compute(x, y)
    return result * 2


def load_and_process(path, x, y):
    """Load config and process."""
    config = load_config(path)
    value = compute(x, y)
    return {"config": config, "value": value}
