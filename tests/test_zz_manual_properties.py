import ast

from lintgate.linters.performance_checks.algebra_types import PurityResult
from lintgate.linters.performance_checks.properties import classify_properties


def test_properties_classifier():
    code = """
def add(a, b):
    return a + b
"""
    tree = ast.parse(code)
    func_node = tree.body[0]
    purity = PurityResult(
        is_pure=True,
        parameter_count=2,
        function_name="add",
        qualified_name="add",
        line=1,
        confidence=1.0,
        side_effects=(),
        return_annotation=None,
    )
    results = classify_properties(func_node, purity)
    assert results is not None
