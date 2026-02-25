"""AST-based assertion classifier for test files.

Single-pass ast.NodeVisitor that classifies every assertion into the
mutation-killing taxonomy defined in types.py. Detects:
- assert statements (equality, identity, truthiness, comparisons)
- pytest.raises context managers
- Hypothesis @given decorated test functions
"""

from __future__ import annotations

import ast
import io
import tokenize as _tokenize

from .target_canonicalizer import canonicalize_target
from .types import STRENGTH_MAP, AssertionInfo, AssertionKind


def _build_comment_map(source: str) -> dict[int, str]:
    """Build a mapping of line_number -> comment_text for a source string.

    Used to detect inline annotations like `# lgignore: sentinel`.
    Returns empty dict on any tokenize failure.
    """
    comment_map: dict[int, str] = {}
    try:
        tokens = _tokenize.generate_tokens(io.StringIO(source).readline)
        for tok_type, tok_string, (srow, _), _, _ in tokens:
            if tok_type == _tokenize.COMMENT:
                comment_map[srow] = tok_string
    except Exception:
        pass
    return comment_map


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


def _classify_compare(node: ast.Compare) -> tuple[AssertionKind, str]:
    """Classify a Compare node into an assertion kind and confidence."""
    # Check for `x is None` / `x is not None` first
    none_kind = _is_none_compare(node)
    if none_kind is not None:
        return none_kind, "structural"

    if not node.ops:
        return AssertionKind.IS_TRUE, "structural"

    op = node.ops[0]
    if isinstance(op, (ast.Eq, ast.NotEq)):
        # Check for len(x) == n pattern
        if isinstance(node.left, ast.Call):
            func_name = _get_name(node.left.func)
            if func_name == "len":
                return AssertionKind.LENGTH_CHECK, "structural"
        if isinstance(op, ast.Eq):
            return AssertionKind.EQUALITY, "structural"
        return AssertionKind.INEQUALITY, "structural"

    if isinstance(op, (ast.Lt, ast.LtE, ast.Gt, ast.GtE)):
        # Check for range check: a <= x <= b (chained comparison)
        if len(node.ops) >= 2:
            return AssertionKind.RANGE_CHECK, "structural"
        return AssertionKind.COMPARISON, "structural"

    if isinstance(op, (ast.In, ast.NotIn)):
        # Distinguish dict key check vs collection membership
        right = node.comparators[0]
        if isinstance(right, (ast.Dict, ast.DictComp)):
            return AssertionKind.DICT_KEY_CHECK, "structural"
        if isinstance(right, ast.Call):
            func_name = _get_name(right.func)
            if func_name == "dict" or func_name.endswith(("json", "as_dict", "to_dict")):
                return AssertionKind.DICT_KEY_CHECK, "structural"
        if isinstance(right, ast.Constant) and isinstance(right.value, str):
            return AssertionKind.STRING_CONTAINS, "structural"
        if hasattr(ast, "Str") and isinstance(right, ast.Str):
            return AssertionKind.STRING_CONTAINS, "structural"
        return AssertionKind.COLLECTION_MEMBERSHIP, "structural"

    # (#80) Detect boolean contract identity: assert x is True / assert fn() is True
    if isinstance(op, (ast.Is, ast.IsNot)):
        comp = node.comparators[0]
        if isinstance(comp, ast.Constant) and isinstance(comp.value, bool):
            if isinstance(node.left, ast.Call):
                return AssertionKind.BOOLEAN_CONTRACT_CALL, "structural"
            if isinstance(node.left, (ast.Name, ast.Attribute)):
                return AssertionKind.BOOLEAN_CONTRACT_FIELD, "structural"
        return AssertionKind.IS_TRUE, "structural"

    return AssertionKind.IS_TRUE, "structural"


def _classify_assert_test(node: ast.expr) -> tuple[AssertionKind, str, str, str]:
    """Classify the test expression of an assert statement.

    Returns (kind, target_expression, target_root, confidence).
    """
    # assert x == y, assert x != y, assert x > y, etc.
    if isinstance(node, ast.Compare):
        kind, confidence = _classify_compare(node)
        target = _unparse_expr(node.left)
        target_root = canonicalize_target(node.left)
        return kind, target, target_root, confidence

    # assert not x → IS_FALSE
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        operand = node.operand
        # assert not isinstance(x, T)
        if isinstance(operand, ast.Call):
            func_name = _get_name(operand.func)
            if func_name == "isinstance":
                return (
                    AssertionKind.ISINSTANCE_CHECK,
                    _unparse_expr(operand),
                    canonicalize_target(operand),
                    "structural",
                )
        return (
            AssertionKind.IS_FALSE,
            _unparse_expr(operand),
            canonicalize_target(operand),
            "structural",
        )

    # assert isinstance(x, T)
    if isinstance(node, ast.Call):
        func_name = _get_name(node.func)
        if func_name == "isinstance":
            target = _unparse_expr(node.args[0]) if node.args else ""
            target_root = canonicalize_target(node.args[0]) if node.args else ""
            return AssertionKind.ISINSTANCE_CHECK, target, target_root, "structural"

        # (#82) hasattr-check kind
        if func_name == "hasattr":
            target = _unparse_expr(node.args[0]) if node.args else ""
            target_root = canonicalize_target(node.args[0]) if node.args else ""
            return AssertionKind.HASATTR_CHECK, target, target_root, "structural"

        if func_name == "callable":
            target = _unparse_expr(node.args[0]) if node.args else ""
            target_root = canonicalize_target(node.args[0]) if node.args else ""
            return AssertionKind.ISINSTANCE_CHECK, target, target_root, "structural"
        # re.match(...), re.search(...)
        if func_name in ("re.match", "re.search", "re.fullmatch"):
            expr = _unparse_expr(node)
            return AssertionKind.REGEX_MATCH, expr, expr, "structural"
        # assert any(...), assert all(...)
        if func_name in ("any", "all"):
            expr = _unparse_expr(node)
            return AssertionKind.COLLECTION_MEMBERSHIP, expr, expr, "structural"

        # (#80) Name-prefix heuristic for bare calls
        if func_name.split(".")[-1].startswith(
            (
                "_is_",
                "is_",
                "_has_",
                "has_",
                "_check_",
                "check_",
                "_can_",
                "can_",
                "_should_",
                "should_",
            )
        ):
            expr = _unparse_expr(node)
            return AssertionKind.BOOLEAN_CONTRACT_CALL, expr, expr, "heuristic"

        # (#80) Any other bare fn call: treat as boolean contract (heuristic)
        expr = _unparse_expr(node)
        target_root = canonicalize_target(node)
        return AssertionKind.BOOLEAN_CONTRACT_CALL, expr, target_root, "heuristic"

    # Bare `assert True` / `assert False` constants
    if isinstance(node, ast.Constant):
        if node.value is True:
            return AssertionKind.IS_TRUE, "True", "True", "structural"
        if node.value is False:
            return AssertionKind.IS_FALSE, "False", "False", "structural"

    # Bare assert x (name, attribute, etc.)
    target = _unparse_expr(node)
    target_root = canonicalize_target(node)
    return AssertionKind.IS_TRUE, target, target_root, "structural"


UNITTEST_ASSERTION_MAP: dict[str, AssertionKind] = {
    "assertEqual": AssertionKind.EQUALITY,
    "assertNotEqual": AssertionKind.INEQUALITY,
    "assertTrue": AssertionKind.IS_TRUE,
    "assertFalse": AssertionKind.IS_FALSE,
    "assertIs": AssertionKind.IS_TRUE,
    "assertIsNot": AssertionKind.IS_TRUE,
    "assertIsNone": AssertionKind.IS_NONE,
    "assertIsNotNone": AssertionKind.IS_NOT_NONE,
    "assertIn": AssertionKind.COLLECTION_MEMBERSHIP,
    "assertNotIn": AssertionKind.COLLECTION_MEMBERSHIP,
    "assertIsInstance": AssertionKind.ISINSTANCE_CHECK,
    "assertNotIsInstance": AssertionKind.ISINSTANCE_CHECK,
    "assertRaises": AssertionKind.RAISES,
    "assertRaisesRegex": AssertionKind.RAISES,
    "assertWarns": AssertionKind.RAISES,
    "assertWarnsRegex": AssertionKind.RAISES,
    "assertRegex": AssertionKind.REGEX_MATCH,
    "assertNotRegex": AssertionKind.REGEX_MATCH,
    "assertLess": AssertionKind.COMPARISON,
    "assertLessEqual": AssertionKind.COMPARISON,
    "assertGreater": AssertionKind.COMPARISON,
    "assertGreaterEqual": AssertionKind.COMPARISON,
    "assertAlmostEqual": AssertionKind.EQUALITY,
    "assertNotAlmostEqual": AssertionKind.INEQUALITY,
    "assertCountEqual": AssertionKind.EQUALITY,
    "assertDictEqual": AssertionKind.EQUALITY,
    "assertListEqual": AssertionKind.EQUALITY,
    "assertTupleEqual": AssertionKind.EQUALITY,
    "assertSetEqual": AssertionKind.EQUALITY,
    "assertMultiLineEqual": AssertionKind.EQUALITY,
    "assertSequenceEqual": AssertionKind.EQUALITY,
    "assert_": AssertionKind.IS_TRUE,
}


class _AssertionVisitor(ast.NodeVisitor):
    """Collects and classifies assertions within a single test function body."""

    def __init__(self, comment_map: dict[int, str] | None = None) -> None:
        self.assertions: list[AssertionInfo] = []
        self._comment_map: dict[int, str] = comment_map or {}

    def visit_Assert(self, node: ast.Assert) -> None:
        kind, target, target_root, confidence = _classify_assert_test(node.test)
        strength = STRENGTH_MAP.get(kind, 0.2)

        # (#81) Check for `# lgignore: sentinel` annotation on this line
        comment = self._comment_map.get(node.lineno, "")
        if "lgignore: sentinel" in comment:
            kind = AssertionKind.SENTINEL_CHECK
            strength = STRENGTH_MAP.get(AssertionKind.SENTINEL_CHECK, 0.6)
            confidence = "annotated"

        self.assertions.append(
            AssertionInfo(
                kind=kind,
                line=node.lineno,
                strength=strength,
                target_expression=target,
                target_root=target_root,
                confidence=confidence,
            )
        )
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        """Detect unittest-style assertions (self.assertEqual, etc.)."""
        func_name = _get_name(node.func)
        bare_name = func_name.split(".")[-1]

        if bare_name in UNITTEST_ASSERTION_MAP:
            kind = UNITTEST_ASSERTION_MAP[bare_name]
            target = ""

            # Best-effort target extraction
            if kind == AssertionKind.RAISES:
                # assertRaises(Exc, func, args...) -> target is func
                if len(node.args) >= 2:
                    target = _unparse_expr(node.args[1])
            elif node.args:
                # assertEqual(a, b) -> target is a
                target = _unparse_expr(node.args[0])

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
        """Detect pytest.raises and unittest.assertRaises context managers."""
        for item in node.items:
            ctx = item.context_expr
            if isinstance(ctx, ast.Call):
                func_name = _get_name(ctx.func)
                bare_name = func_name.split(".")[-1]

                if func_name in ("pytest.raises", "raises") or bare_name in (
                    "assertRaises",
                    "assertRaisesRegex",
                    "assertWarns",
                    "assertWarnsRegex",
                ):
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
            if name in ("given", "hypothesis.given"):
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

    def __init__(self, comment_map: dict[int, str] | None = None) -> None:
        self.test_assertions: dict[str, list[AssertionInfo]] = {}
        self._class_stack: list[str] = []
        self._comment_map: dict[int, str] = comment_map or {}

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

        visitor = _AssertionVisitor(comment_map=self._comment_map)
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

    comment_map = _build_comment_map(source)
    analyzer = TestFileAnalyzer(comment_map=comment_map)
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
