import ast

from lintgate.linters.performance_checks.purity import analyze_purity


def test_purity_analyzer_basic():
    code = """
def pure_add(a, b):
    return a + b

def impure_add(a, b):
    global counter
    counter += 1
    return a + b
"""
    tree = ast.parse(code)
    results = analyze_purity(tree)
    assert len(results) == 2
    assert results["pure_add"].is_pure is True
    assert results["impure_add"].is_pure is False

def test_purity_edge_cases():
    code = """
def nonlocal_test():
    x = 1
    def inner():
        nonlocal x
        x += 1
    return inner

def list_append(lst):
    lst.append(1)
    return lst

def annotated(x: int) -> int:
    y: int = x
    return y
"""
    tree = ast.parse(code)
    results = analyze_purity(tree)
    # The analyze purity gives function level granularity
    assert results["nonlocal_test"].is_pure is False or results["inner"].is_pure is False
    assert results["list_append"].is_pure is True
    assert results["annotated"].is_pure is True
