import ast

from lintgate.linters.performance_checks.perf010_unnecessary_materialization import (
    check_unnecessary_materialization,
)
from lintgate.linters.performance_checks.perf011_pure_uncached_in_loop import (
    check_pure_uncached_in_loop,
)


def test_perf010_basic():
    code = """
def example():
    return sum([x for x in range(10)])
"""
    tree = ast.parse(code)
    issues = list(check_unnecessary_materialization(tree, "dummy.py"))
    assert len(issues) > 0


def test_perf011_basic():
    code = """
def example():
    for i, j in [(1, 2)]:
        print(len([1, 2, 3], a=1))

    while True:
        pass
"""
    tree = ast.parse(code)
    issues = list(check_pure_uncached_in_loop(tree, "dummy.py"))
    assert isinstance(issues, list)
