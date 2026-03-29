"""Extract method refactoring — AST-based variable flow analysis.

Extracted from refactor_move.py. Provides extract_method() for splitting
function bodies into named helpers with automatic input/output detection.
"""

from __future__ import annotations

import ast
import os
from dataclasses import dataclass, field
from typing import Any

# ── Extract method ───────────────────────────────────────────────


@dataclass
class ExtractResult:
    """Result of an extract_method operation."""

    inputs: list[str] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)
    extracted_code: str = ""
    helper_signature: str = ""
    call_replacement: str = ""
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "inputs": self.inputs,
            "outputs": self.outputs,
            "extracted_code": self.extracted_code,
            "helper_signature": self.helper_signature,
            "call_replacement": self.call_replacement,
        }
        if self.errors:
            d["errors"] = self.errors
        return d


_BUILTINS = frozenset({
    "True", "False", "None", "print", "len", "range", "enumerate",
    "zip", "map", "filter", "sorted", "list", "dict", "set", "tuple",
    "str", "int", "float", "bool", "isinstance", "type", "super",
    "open", "any", "all", "min", "max", "sum", "abs", "round",
    "hasattr", "getattr", "setattr", "ValueError", "TypeError",
    "KeyError", "IndexError", "AttributeError", "RuntimeError",
    "Exception", "OSError", "FileNotFoundError", "ImportError",
})


def _collect_used_names(tree: ast.AST) -> set[str]:
    """Collect all variable names read (not assigned) in an AST."""
    names: set[str] = set()
    assigned: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            if isinstance(node.ctx, ast.Load):
                names.add(node.id)
            elif isinstance(node.ctx, ast.Store):
                assigned.add(node.id)
    return names


def _collect_augassign_only_names(tree: ast.AST) -> set[str]:
    """Collect names that are ONLY augassigned (+=, -=) — no regular assign."""
    augassign_targets: set[str] = set()
    regular_assign_targets: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name):
            augassign_targets.add(node.target.id)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    regular_assign_targets.add(target.id)
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            if isinstance(node.target, ast.Name):
                regular_assign_targets.add(node.target.id)
    return augassign_targets - regular_assign_targets


def _collect_names_before_line(
    func: ast.FunctionDef | ast.AsyncFunctionDef, line: int,
) -> set[str]:
    """Collect names assigned in the function body BEFORE the given line."""
    names: set[str] = set()
    for node in ast.iter_child_nodes(func):
        if hasattr(node, "lineno") and node.lineno >= line:
            break
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            if isinstance(node.target, ast.Name):
                names.add(node.target.id)
        elif isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return names


def _collect_names_after_line(
    func: ast.FunctionDef | ast.AsyncFunctionDef, line: int,
) -> set[str]:
    """Collect names used (Load context) in the function body AFTER the given line."""
    names: set[str] = set()
    for node in ast.walk(func):
        if not hasattr(node, "lineno"):
            continue
        if node.lineno <= line:
            continue
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            names.add(node.id)
    return names


def _check_control_flow(block_tree: ast.AST) -> list[str]:
    """Check for break/continue/return that would break enclosing control flow."""
    issues: list[str] = []
    for node in ast.walk(block_tree):
        if isinstance(node, ast.Return):
            issues.append(
                f"Line {getattr(node, 'lineno', '?')}: return statement "
                "would change enclosing function's control flow"
            )
        elif isinstance(node, ast.Break):
            issues.append(
                f"Line {getattr(node, 'lineno', '?')}: break statement "
                "would escape enclosing loop"
            )
        elif isinstance(node, ast.Continue):
            issues.append(
                f"Line {getattr(node, 'lineno', '?')}: continue statement "
                "would escape enclosing loop"
            )
    return issues


def _collect_augassign_all(block_tree: ast.AST) -> set[str]:
    """Collect ALL augassign targets (for input detection)."""
    targets: set[str] = set()
    for node in ast.walk(block_tree):
        if isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name):
            targets.add(node.target.id)
    return targets


def _collect_augassign_all_tokenized(block_lines: list[str]) -> set[str]:
    """Fallback: collect augassign targets via tokenizer when AST fails."""
    import io
    import keyword as _kw
    import textwrap
    import tokenize

    _AUG_OPS = {"+=", "-=", "*=", "/=", "|=", "&=", "%=", "**=", "//="}
    targets: set[str] = set()
    try:
        src = textwrap.dedent("".join(block_lines))
        toks = list(tokenize.generate_tokens(io.StringIO(src).readline))
        _SKIP = {tokenize.NL, tokenize.NEWLINE, tokenize.INDENT,
                 tokenize.DEDENT, tokenize.COMMENT}
        for i, tok in enumerate(toks):
            if tok.type == tokenize.NAME and not _kw.iskeyword(tok.string):
                for j in range(i + 1, len(toks)):
                    if toks[j].type not in _SKIP:
                        if toks[j].type == tokenize.OP and toks[j].string in _AUG_OPS:
                            targets.add(tok.string)
                        break
    except Exception:
        pass
    return targets


def _analyze_block_names(
    block_lines: list[str],
    dedented: str,
) -> tuple[set[str], set[str], set[str], set[str], ast.AST | None]:
    """Analyze a code block to find defined, used, augassign-only, and all-augassign names.

    Returns (defined_in_block, used_in_block, augassign_only, augassign_all, block_tree).
    """
    try:
        block_tree: ast.AST | None = ast.parse(dedented)
    except SyntaxError:
        block_tree = None

    if block_tree is not None:
        defined_in_block = _collect_assigned_names(block_tree)
        used_in_block = _collect_used_names(block_tree)
        augassign_only = _collect_augassign_only_names(block_tree)
        augassign_all = _collect_augassign_all(block_tree)
    else:
        defined_in_block, used_in_block, augassign_only = _scan_names_tokenized(block_lines)
        augassign_all = augassign_only | _collect_augassign_all_tokenized(block_lines)

    return defined_in_block, used_in_block, augassign_only, augassign_all, block_tree


def _compute_inputs_outputs(
    defined_in_block: set[str],
    used_in_block: set[str],
    augassign_only: set[str],
    augassign_all: set[str],
    enclosing: ast.FunctionDef | ast.AsyncFunctionDef,
    tree: ast.Module,
    start_line: int,
    end_line: int,
    block_tree: ast.AST | None,
) -> tuple[list[str], list[str], set[str]]:
    """Compute inputs, outputs, and needs_init for extract_method.

    Returns (inputs, outputs, needs_init).
    """
    post_block_used = _collect_names_after_line(enclosing, end_line)
    module_level_names = _collect_module_level_names(tree)

    pre_block = _collect_names_before_line(enclosing, start_line)
    func_params = {arg.arg for arg in enclosing.args.args}
    enclosing_scope = pre_block | func_params
    augassign_inputs = augassign_all & enclosing_scope

    inputs = sorted(
        (used_in_block | augassign_inputs)
        - (defined_in_block - augassign_inputs)
        - module_level_names
    )

    needs_init = augassign_only - set(inputs)
    outputs = sorted(defined_in_block & post_block_used)

    for var in sorted(needs_init):
        if var in post_block_used and var not in outputs:
            outputs.append(var)
    outputs = sorted(set(outputs))

    inputs = [n for n in inputs if n not in _BUILTINS]
    outputs = [n for n in outputs if n not in _BUILTINS]

    if block_tree is not None:
        mutated_args = _detect_mutable_side_effects(block_tree, set(inputs))
        for ma in mutated_args:
            if ma not in outputs:
                outputs.append(ma)
        outputs = sorted(set(outputs))

    return inputs, outputs, needs_init


def _build_helper_code(
    helper_name: str,
    inputs: list[str],
    outputs: list[str],
    needs_init: set[str],
    block_lines: list[str],
    block_indent: str,
    enclosing_col_offset: int,
) -> tuple[str, str, str]:
    """Build the helper function source, signature, and call replacement.

    Returns (extracted_code, helper_signature, call_replacement).
    """
    params = ", ".join(inputs)
    enclosing_indent = " " * enclosing_col_offset

    if len(outputs) == 0:
        ret_hint = " -> None"
        return_stmt = ""
    elif len(outputs) == 1:
        ret_hint = ""
        return_stmt = f"{enclosing_indent}    return {outputs[0]}\n"
    else:
        ret_hint = ""
        return_stmt = f"{enclosing_indent}    return {', '.join(outputs)}\n"

    helper_def = f"{enclosing_indent}def {helper_name}({params}){ret_hint}:\n"

    init_lines = ""
    for var in sorted(needs_init):
        init_lines += f"{enclosing_indent}    {var} = 0\n"

    helper_body = init_lines
    for line in block_lines:
        if line.strip():
            relative = line[len(block_indent):] if line.startswith(block_indent) else line.lstrip()
            helper_body += enclosing_indent + "    " + relative
        else:
            helper_body += "\n"
    if return_stmt:
        helper_body += return_stmt

    extracted_code = helper_def + helper_body
    helper_signature = f"def {helper_name}({params}){ret_hint}"

    if len(outputs) == 0:
        call = f"{block_indent}{helper_name}({', '.join(inputs)})\n"
    elif len(outputs) == 1:
        call = f"{block_indent}{outputs[0]} = {helper_name}({', '.join(inputs)})\n"
    else:
        call = f"{block_indent}{', '.join(outputs)} = {helper_name}({', '.join(inputs)})\n"

    return extracted_code, helper_signature, call.strip()


def extract_method(
    project_root: str,
    file: str,
    start_line: int,
    end_line: int,
    helper_name: str,
    *,
    dry_run: bool = True,
) -> ExtractResult:
    """Extract a block of code into a named helper function.

    Analyzes the block to detect inputs (variables read from enclosing scope)
    and outputs (variables written and used after the block), then generates
    or applies the extraction.

    Args:
        project_root: Absolute path to the project root.
        file: Relative path to the source file.
        start_line: First line of the block to extract (1-indexed).
        end_line: Last line of the block to extract (1-indexed).
        helper_name: Name for the extracted helper function.
        dry_run: If True, preview only. If False, apply the extraction.
    """
    result = ExtractResult()
    source_path = os.path.join(project_root, file)

    if not os.path.isfile(source_path):
        result.errors.append(f"File not found: {source_path}")
        return result

    with open(source_path, encoding="utf-8") as f:
        lines = f.readlines()

    if start_line < 1 or end_line > len(lines) or start_line > end_line:
        result.errors.append(
            f"Invalid line range {start_line}-{end_line} (file has {len(lines)} lines)"
        )
        return result

    # Parse the full file AST
    source = "".join(lines)
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        result.errors.append(f"Parse error: {e}")
        return result

    # Find the enclosing function
    enclosing = _find_enclosing_function(tree, start_line)
    if enclosing is None:
        result.errors.append(
            f"No enclosing function found for line {start_line}"
        )
        return result

    # Extract the block
    block_lines = lines[start_line - 1 : end_line]
    if not any(line.strip() for line in block_lines):
        result.errors.append("Selected block is empty")
        return result

    # Determine block indentation from first non-empty line
    block_indent = ""
    for line in block_lines:
        if line.strip():
            block_indent = line[: len(line) - len(line.lstrip())]
            break

    # Dedent the block for AST parsing
    dedented = _dedent_block(block_lines, block_indent)

    # Analyze names in the block
    defined_in_block, used_in_block, augassign_only, augassign_all, block_tree = (
        _analyze_block_names(block_lines, dedented)
    )

    # Control flow validation
    if block_tree is not None:
        flow_issues = _check_control_flow(block_tree)
        if flow_issues:
            result.errors.extend(flow_issues)
            if not dry_run:
                return result

    # Compute inputs and outputs
    inputs, outputs, needs_init = _compute_inputs_outputs(
        defined_in_block, used_in_block, augassign_only, augassign_all,
        enclosing, tree, start_line, end_line, block_tree,
    )
    result.inputs = inputs
    result.outputs = outputs

    # Build the helper function
    extracted_code, helper_signature, call_replacement = _build_helper_code(
        helper_name, inputs, outputs, needs_init,
        block_lines, block_indent, enclosing.col_offset or 0,
    )
    result.extracted_code = extracted_code
    result.helper_signature = helper_signature
    result.call_replacement = call_replacement

    if dry_run:
        return result

    # Apply: insert helper before the enclosing function, replace block with call
    new_lines = list(lines)
    insert_at = enclosing.lineno - 1
    helper_lines = (result.extracted_code + "\n\n").splitlines(keepends=True)
    for i, hl in enumerate(helper_lines):
        new_lines.insert(insert_at + i, hl)

    offset = len(helper_lines)
    adjusted_start = start_line - 1 + offset
    adjusted_end = end_line + offset
    call_line = f"{call_replacement}\n" if not call_replacement.endswith("\n") else call_replacement
    new_lines[adjusted_start:adjusted_end] = [call_line]

    with open(source_path, "w", encoding="utf-8") as f:
        f.writelines(new_lines)

    return result


def _scan_names_tokenized(block_lines: list[str]) -> tuple[set[str], set[str], set[str]]:
    """Token-based fallback when AST parse fails (try/except, with, partial blocks).

    Uses Python's tokenizer to extract identifiers from raw source lines.
    Returns (defined_in_block, used_in_block, augassign_only).
    """
    import io
    import keyword
    import textwrap
    import tokenize
    source = textwrap.dedent("".join(block_lines))
    defined: set[str] = set()
    used: set[str] = set()
    augassign_targets: set[str] = set()
    regular_assign_targets: set[str] = set()

    # Tokenize the dedented block
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    except (tokenize.TokenError, IndentationError):
        # Even tokenizer fails — last resort: regex scan
        import re
        identifiers = set(re.findall(r'\b([a-zA-Z_]\w*)\b', source))
        kw = set(keyword.kwlist)
        identifiers -= kw
        return set(), identifiers, set()

    kw = set(keyword.kwlist)
    _SKIP = tokenize.NL, tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT, tokenize.COMMENT, tokenize.ENCODING

    def _next_meaningful(idx: int):
        for j in range(idx + 1, len(tokens)):
            if tokens[j].type not in _SKIP:
                return tokens[j]
        return None

    def _prev_meaningful(idx: int):
        for j in range(idx - 1, -1, -1):
            if tokens[j].type not in _SKIP:
                return tokens[j]
        return None

    for i, tok in enumerate(tokens):
        if tok.type != tokenize.NAME or tok.string in kw:
            continue

        name = tok.string
        next_tok = _next_meaningful(i)
        prev_tok = _prev_meaningful(i)

        # Skip: names after a dot are attribute accesses, not standalone variables
        # e.g., results.append → skip "append", keep "results"
        if prev_tok and prev_tok.type == tokenize.OP and prev_tok.string == '.':
            continue

        # Skip: exception variable in `except X as name`
        if prev_tok and prev_tok.type == tokenize.NAME and prev_tok.string == 'as':
            defined.add(name)
            continue

        # Skip: names after def/class/import/from/except keywords
        if prev_tok and prev_tok.type == tokenize.NAME and prev_tok.string in ('def', 'class', 'import', 'from', 'except'):
            continue

        # Assignment: name = ... (but not ==)
        if next_tok and next_tok.type == tokenize.OP and next_tok.string == '=':
            after_eq = _next_meaningful(tokens.index(next_tok))
            if not (after_eq and after_eq.type == tokenize.OP and after_eq.string == '='):
                defined.add(name)
                regular_assign_targets.add(name)
                continue

        # AugAssign: name += / -= / *= etc.
        if next_tok and next_tok.type == tokenize.OP and next_tok.string in ('+=', '-=', '*=', '/=', '|=', '&=', '%=', '**=', '//='):
            augassign_targets.add(name)
            defined.add(name)
            used.add(name)
            continue

        # For loop target: `for name in ...`
        if prev_tok and prev_tok.type == tokenize.NAME and prev_tok.string == 'for':
            defined.add(name)
            regular_assign_targets.add(name)
            continue

        # Default: it's a variable reference (use)
        used.add(name)

    augassign_only = augassign_targets - regular_assign_targets
    return defined, used, augassign_only


def _collect_module_level_names(tree: ast.Module) -> set[str]:
    """Collect names defined at module level: imports, assignments, defs, classes."""
    names: set[str] = set()
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                names.add(alias.asname or alias.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
    return names


def _find_enclosing_function(
    tree: ast.Module, line: int
) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    """Find the function definition containing the given line."""
    for node in getattr(tree, "body", []):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.lineno <= line <= (node.end_lineno or node.lineno):
                # Check for nested functions — prefer the innermost
                for child in ast.walk(node):
                    if child is node:
                        continue
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        if child.lineno <= line <= (child.end_lineno or child.lineno):
                            return child
                return node
    return None


def _find_all_enclosing_functions(
    tree: ast.Module, line: int
) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    """Find ALL enclosing functions for a line, outermost first."""
    enclosing: list[ast.FunctionDef | ast.AsyncFunctionDef] = []

    def _walk(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if child.lineno <= line <= (child.end_lineno or child.lineno):
                    enclosing.append(child)
                    _walk(child)
                    return
            elif isinstance(child, ast.ClassDef):
                if child.lineno <= line <= (child.end_lineno or child.lineno):
                    _walk(child)
                    return

    _walk(tree)
    return enclosing


def _collect_outer_scope_names(
    tree: ast.Module,
    immediate_enclosing: ast.FunctionDef | ast.AsyncFunctionDef,
    block_start_line: int,
) -> set[str]:
    """Collect names from all enclosing scopes ABOVE the immediate enclosing function.

    When a block uses closure variables (e.g., tokenizer, device, model defined
    in an outer function), those names aren't in the immediate function's
    pre-block assignments or params. This walks up the scope chain.
    """
    all_enclosing = _find_all_enclosing_functions(tree, block_start_line)
    outer_names: set[str] = set()
    for func in all_enclosing:
        if func is immediate_enclosing:
            break  # Stop before the immediate enclosing — those are already handled
        # Collect params
        for arg in func.args.args:
            outer_names.add(arg.arg)
        # Collect assignments in the outer function body (shallow — skip nested defs)
        _collect_assigns_shallow(func, outer_names)
    return outer_names


def _collect_assigns_shallow(node: ast.AST, names: set[str]) -> None:
    """Collect assigned names without descending into nested function/class defs."""
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue  # Don't descend — nested scope
        if isinstance(child, ast.Assign):
            for target in child.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
        elif isinstance(child, (ast.For, ast.AsyncFor)) and isinstance(child.target, ast.Name):
            names.add(child.target.id)
        _collect_assigns_shallow(child, names)


def _dedent_block(lines: list[str], indent: str) -> str:
    """Remove common indentation from a block of lines."""
    result = []
    for line in lines:
        if line.startswith(indent):
            result.append(line[len(indent):])
        else:
            result.append(line)
    return "".join(result)


def _collect_assigned_names(tree: ast.AST) -> set[str]:
    """Collect all variable names assigned in an AST."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
                elif isinstance(target, ast.Tuple):
                    for elt in target.elts:
                        if isinstance(elt, ast.Name):
                            names.add(elt.id)
        elif isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            if isinstance(node.target, ast.Name):
                names.add(node.target.id)
            elif isinstance(node.target, ast.Tuple):
                for elt in node.target.elts:
                    if isinstance(elt, ast.Name):
                        names.add(elt.id)
        elif isinstance(node, ast.NamedExpr):
            names.add(node.target.id)
    return names


_MUTATING_METHODS = frozenset({
    "append", "extend", "insert", "pop", "remove", "clear",  # list
    "update", "setdefault", "popitem",                        # dict
    "add", "discard",                                         # set
    "write", "writelines",                                    # file-like
    "sort", "reverse",                                        # in-place list ops
})


def _detect_mutable_side_effects(tree: ast.AST, input_names: set[str]) -> list[str]:
    """Detect variables that are mutated via method calls or subscript assignment.

    Tracks three mutation patterns:
    1. Method calls: var.append(), var.update(), etc.
    2. Subscript assignment: var[key] = value
    3. Subscript augmented assignment: var[key] += 1

    All three mutate the container in-place — the caller sees the change.
    No production IDE handles this comprehensively.
    """
    mutated: set[str] = set()
    for node in ast.walk(tree):
        # Pattern 1: method-based mutation (var.append(...))
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr in _MUTATING_METHODS:
                if isinstance(func.value, ast.Name) and func.value.id in input_names:
                    mutated.add(func.value.id)

        # Pattern 2: subscript assignment (var[key] = value)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Subscript) and isinstance(target.value, ast.Name):
                    if target.value.id in input_names:
                        mutated.add(target.value.id)

        # Pattern 3: subscript augmented assignment (var[key] += 1)
        elif isinstance(node, ast.AugAssign):
            if isinstance(node.target, ast.Subscript) and isinstance(node.target.value, ast.Name):
                if node.target.value.id in input_names:
                    mutated.add(node.target.value.id)
    return sorted(mutated)

