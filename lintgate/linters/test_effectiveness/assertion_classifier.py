"""AST-based assertion classifier for test files.

Single-pass ast.NodeVisitor that classifies every assertion into the
mutation-killing taxonomy defined in types.py. Detects:
- assert statements (equality, identity, truthiness, comparisons)
- pytest.raises context managers
- Hypothesis @given decorated test functions
"""

from __future__ import annotations

import ast

from .types import STRENGTH_MAP, AssertionInfo, AssertionKind


def _get_name(node: ast.expr) -> str:
    """Extract a dotted name from an AST expression node."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _get_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


def _unparse_expr(node: ast.expr) -> str:
    """Best-effort expression-to-string for target_expression."""
    try:
        return ast.unparse(node)
    except Exception:
        return _get_name(node) or ""


def _is_none_compare(node: ast.Compare) -> AssertionKind | None:
    """Check if a Compare node is `x is None` or `x is not None`."""
    if len(node.ops) != 1 or len(node.comparators) != 1:
        return None
    op = node.ops[0]
    comp = node.comparators[0]
    if isinstance(comp, ast.Constant) and comp.value is None:
        if isinstance(op, ast.Is):
            return AssertionKind.IS_NONE
        if isinstance(op, ast.IsNot):
            return AssertionKind.IS_NOT_NONE
    return None


def _classify_compare(node: ast.Compare) -> AssertionKind:
    """Classify a Compare node into an assertion kind."""
    # Check for `x is None` / `x is not None` first
    none_kind = _is_none_compare(node)
    if none_kind is not None:
        return none_kind

    if not node.ops:
        return AssertionKind.IS_TRUE

    op = node.ops[0]
    if isinstance(op, (ast.Eq, ast.NotEq)):
        # Check for len(x) == n pattern
        if isinstance(node.left, ast.Call):
            func_name = _get_name(node.left.func)
            if func_name == "len":
                return AssertionKind.LENGTH_CHECK
        if isinstance(op, ast.Eq):
            return AssertionKind.EQUALITY
        return AssertionKind.INEQUALITY

    if isinstance(op, (ast.Lt, ast.LtE, ast.Gt, ast.GtE)):
        # Check for range check: a <= x <= b (chained comparison)
        if len(node.ops) >= 2:
            return AssertionKind.RANGE_CHECK
        return AssertionKind.COMPARISON

    if isinstance(op, (ast.In, ast.NotIn)):
        # Distinguish dict key check vs collection membership
        # Heuristic: if the right side looks like a dict, it's a key check
        return AssertionKind.COLLECTION_MEMBERSHIP

    if isinstance(op, ast.Is):
        return AssertionKind.IS_TRUE
    if isinstance(op, ast.IsNot):
        return AssertionKind.IS_TRUE

    return AssertionKind.IS_TRUE


def _classify_assert_test(node: ast.expr) -> tuple[AssertionKind, str]:
    """Classify the test expression of an assert statement.

    Returns (kind, target_expression).
    """
    # assert x == y, assert x != y, assert x > y, etc.
    if isinstance(node, ast.Compare):
        kind = _classify_compare(node)
        target = _unparse_expr(node.left)
        return kind, target

    # assert not x → IS_FALSE
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        operand = node.operand
        # assert not isinstance(x, T)
        if isinstance(operand, ast.Call):
            func_name = _get_name(operand.func)
            if func_name == "isinstance":
                return AssertionKind.ISINSTANCE_CHECK, _unparse_expr(operand)
        return AssertionKind.IS_FALSE, _unparse_expr(operand)

    # assert isinstance(x, T)
    if isinstance(node, ast.Call):
        func_name = _get_name(node.func)
        if func_name == "isinstance":
            target = _unparse_expr(node.args[0]) if node.args else ""
            return AssertionKind.ISINSTANCE_CHECK, target
        if func_name == "callable":
            target = _unparse_expr(node.args[0]) if node.args else ""
            return AssertionKind.ISINSTANCE_CHECK, target
        # re.match(...), re.search(...)
        if func_name in ("re.match", "re.search", "re.fullmatch"):
            return AssertionKind.REGEX_MATCH, _unparse_expr(node)
        # assert any(...), assert all(...)
        if func_name in ("any", "all"):
            return AssertionKind.COLLECTION_MEMBERSHIP, _unparse_expr(node)

    # assert "key" in d (BoolOp won't hit here, but In op on Compare will)

    # Bare `assert x` → IS_TRUE
    if isinstance(node, ast.Constant):
        if node.value is True:
            return AssertionKind.IS_TRUE, "True"
        if node.value is False:
            return AssertionKind.IS_FALSE, "False"

    # Bare assert x, assert func()
    target = _unparse_expr(node)
    return AssertionKind.IS_TRUE, target


class _AssertionVisitor(ast.NodeVisitor):
    """Collects and classifies assertions within a single test function body."""

    def __init__(self) -> None:
        self.assertions: list[AssertionInfo] = []

    def visit_Assert(self, node: ast.Assert) -> None:
        kind, target = _classify_assert_test(node.test)
        strength = STRENGTH_MAP.get(kind, 0.2)
        self.assertions.append(
            AssertionInfo(
                kind=kind,
                line=node.lineno,
                strength=strength,
                target_expression=target,
            )
        )
        self.generic_visit(node)

    def visit_With(self, node: ast.With) -> None:
        """Detect pytest.raises context managers."""
        for item in node.items:
            ctx = item.context_expr
            if isinstance(ctx, ast.Call):
                func_name = _get_name(ctx.func)
                if func_name in ("pytest.raises", "raises"):
                    target = ""
                    if ctx.args:
                        target = _unparse_expr(ctx.args[0])
                    self.assertions.append(
                        AssertionInfo(
                            kind=AssertionKind.RAISES,
                            line=node.lineno,
                            strength=STRENGTH_MAP[AssertionKind.RAISES],
                            target_expression=target,
                        )
                    )
        self.generic_visit(node)


def _has_given_decorator(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Check if a function has a @given(...) or @hypothesis.given(...) decorator."""
    for dec in node.decorator_list:
        if isinstance(dec, ast.Call):
            name = _get_name(dec.func)
            if name in ("given", "hypothesis.given", "settings"):
                return True
        elif isinstance(dec, ast.Attribute):
            if dec.attr == "given":
                return True
        elif isinstance(dec, ast.Name) and dec.id == "given":
            return True
    return False


class TestFileAnalyzer(ast.NodeVisitor):
    """Top-level visitor that analyzes an entire test file.

    Produces a mapping of test function names to their classified assertions.
    """

    def __init__(self) -> None:
        self.test_assertions: dict[str, list[AssertionInfo]] = {}
        self._class_stack: list[str] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._class_stack.append(node.name)
        self.generic_visit(node)
        self._class_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_test_func(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_test_func(node)

    def _visit_test_func(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        """Analyze a test function for assertions."""
        # Only analyze test functions
        if not node.name.startswith("test_"):
            return

        qualname = node.name
        if self._class_stack:
            qualname = f"{'.'.join(self._class_stack)}.{node.name}"

        visitor = _AssertionVisitor()
        visitor.visit(node)

        assertions = visitor.assertions

        # If function has @given decorator, add a hypothesis property assertion
        if _has_given_decorator(node):
            assertions.append(
                AssertionInfo(
                    kind=AssertionKind.HYPOTHESIS_PROPERTY,
                    line=node.lineno,
                    strength=STRENGTH_MAP[AssertionKind.HYPOTHESIS_PROPERTY],
                    target_expression=qualname,
                )
            )

        self.test_assertions[qualname] = assertions


def classify_test_file(source: str, filename: str = "<test>") -> dict[str, list[AssertionInfo]]:
    """Classify all assertions in a test file.

    Args:
        source: Python source code of the test file.
        filename: Filename for error messages.

    Returns:
        Mapping of test function qualified names to their classified assertions.
    """
    try:
        tree = ast.parse(source, filename=filename)
    except SyntaxError:
        return {}

    analyzer = TestFileAnalyzer()
    analyzer.visit(tree)
    return analyzer.test_assertions


def classify_test_file_from_path(filepath: str) -> dict[str, list[AssertionInfo]]:
    """Classify all assertions in a test file by path.

    Returns empty dict on read/parse failure.
    """
    try:
        with open(filepath, encoding="utf-8") as f:
            source = f.read()
    except OSError:
        return {}
    return classify_test_file(source, filename=filepath)
