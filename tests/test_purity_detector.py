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

def print_test(x):
    print(x)
    return x

def mutable_default(x, lst=[]):
    lst.append(x)
    return lst
"""
    tree = ast.parse(code)
    results = analyze_purity(tree)

    assert results["pure_add"].is_pure is True
    assert results["impure_add"].is_pure is False
    assert results["print_test"].is_pure is False  # print is impure
    assert results["mutable_default"].is_pure is False

def test_purity_builtin_recognition():
    code = """
def math_usage(x):
    import math
    return math.sqrt(x)

def len_usage(lst):
    return len(lst)

def os_usage():
    import os
    return os.getcwd()
"""
    tree = ast.parse(code)
    results = analyze_purity(tree)

    assert results["math_usage"].is_pure is True
    assert results["len_usage"].is_pure is True
    assert results["os_usage"].is_pure is False

def test_purity_transitive():
    code = """
def helper(x):
    return x + 1

def main_pure(x):
    return helper(x)

def impure_helper(x):
    global y
    y = x
    return x

def main_impure(x):
    return impure_helper(x)
"""
    tree = ast.parse(code)
    results = analyze_purity(tree)

    assert results["helper"].is_pure is True
    assert results["main_pure"].is_pure is True
    assert results["impure_helper"].is_pure is False
    assert results["main_impure"].is_pure is False

def test_purity_local_mutation_is_safe():
    code = """
def pure_transform(data):
    # Local multiplication/mutation of local state is often safe
    res = []
    for x in data:
        res.append(x * 2)
    return res
"""
    tree = ast.parse(code)
    results = analyze_purity(tree)
    assert results["pure_transform"].is_pure is True
