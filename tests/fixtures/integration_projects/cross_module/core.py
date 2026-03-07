"""Core module with pure and impure functions."""

import os


def compute(x, y):
    """Pure computation."""
    return x * y + 1


def load_config(path):
    """Impure: reads from filesystem."""
    if os.path.exists(path):
        with open(path) as f:
            return f.read()
    return ""
