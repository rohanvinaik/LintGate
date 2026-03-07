"""Pure calculator functions for integration testing."""


def add(a, b):
    """Add two numbers."""
    return a + b


def multiply(a, b):
    """Multiply two numbers."""
    return a * b


def factorial(n):
    """Compute factorial of n."""
    if n <= 1:
        return 1
    return n * factorial(n - 1)
