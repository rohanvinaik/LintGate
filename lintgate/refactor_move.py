"""Safe module refactoring engine — libcst-based import rewriting.

Provides atomic module move operations with:
- AST-based import scanning (works without libcst)
- libcst-based import rewriting (requires libcst for auto-apply)
- Targeted string scanning for import-bearing call sites only
- Subprocess-based import smoke gate
- Optional shim generation for backward compatibility

Graceful degradation: without libcst, dry_run mode shows what needs
changing but cannot auto-apply. Returns ``{"degraded": true, ...}``.
"""

from __future__ import annotations

import ast
import os
from dataclasses import dataclass, field
from typing import Any

# ── Types ─────────────────────────────────────────────────────────


@dataclass
class ImportReference:
    """A single import reference that needs rewriting."""

    file: str
    line: int
    kind: str  # "import", "from_import", "string_ref"
    old_text: str
    new_text: str
    context: str = ""  # e.g., "mock.patch", "importlib.import_module"


@dataclass
class MoveResult:
    """Result of a module move operation."""

    source: str
    destination: str
    dry_run: bool
    degraded: bool = False
    references_found: list[ImportReference] = field(default_factory=list)
    references_rewritten: int = 0
    shim_generated: bool = False
    shim_path: str = ""
    smoke_test_passed: bool | None = None
    smoke_test_error: str = ""
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "source": self.source,
            "destination": self.destination,
            "dry_run": self.dry_run,
        }
        if self.degraded:
            d["degraded"] = True
        d["references_found"] = len(self.references_found)
        if self.references_found:
            d["references"] = [
                {
                    "file": r.file,
                    "line": r.line,
                    "kind": r.kind,
                    "old_text": r.old_text,
                    "new_text": r.new_text,
                    **({"context": r.context} if r.context else {}),
                }
                for r in self.references_found
            ]
        if not self.dry_run:
            d["references_rewritten"] = self.references_rewritten
        if self.shim_generated:
            d["shim_generated"] = True
            d["shim_path"] = self.shim_path
        if self.smoke_test_passed is not None:
            d["smoke_test_passed"] = self.smoke_test_passed
        if self.smoke_test_error:
            d["smoke_test_error"] = self.smoke_test_error
        if self.errors:
            d["errors"] = self.errors
        if self.warnings:
            d["warnings"] = self.warnings
        return d


# ── Import scanning (AST-based, always available) ────────────────


# ── Import scanning ───────────────────────────────────────────────


def scan_all_imports(
    project_root: str,
    old_module: str,
) -> list[ImportReference]:
    """Scan all Python files for imports of old_module.

    Uses ast (stdlib) — always available. Finds:
    - ``import old_module``
    - ``from old_module import ...``
    - ``from old_module.sub import ...``
    - String references in import-bearing call sites
    """
    refs: list[ImportReference] = []
    old_parts = old_module.split(".")

    for dirpath, dirnames, filenames in os.walk(project_root):
        dirnames[:] = [
            d
            for d in dirnames
            if not d.startswith(".") and d != "__pycache__" and d != "node_modules" and d != ".git"
        ]
        for fname in filenames:
            if not fname.endswith(".py"):
                continue
            filepath = os.path.join(dirpath, fname)
            try:
                rel_path = os.path.relpath(filepath, project_root)
            except ValueError:
                continue
            file_refs = _scan_file_imports(filepath, rel_path, old_module, old_parts)
            refs.extend(file_refs)

    return refs


def _scan_file_imports(
    filepath: str,
    rel_path: str,
    old_module: str,
    _old_parts: list[str],
) -> list[ImportReference]:
    """Scan a single file for imports of old_module."""
    try:
        with open(filepath, encoding="utf-8") as f:
            source = f.read()
        tree = ast.parse(source, filename=filepath)
    except (OSError, SyntaxError):
        return []

    refs: list[ImportReference] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == old_module or alias.name.startswith(old_module + "."):
                    refs.append(
                        ImportReference(
                            file=rel_path,
                            line=node.lineno,
                            kind="import",
                            old_text=f"import {alias.name}",
                            new_text="",  # Filled in by _compute_rewrites
                        )
                    )

        elif isinstance(node, ast.ImportFrom):
            if node.module and (
                node.module == old_module or node.module.startswith(old_module + ".")
            ):
                names = ", ".join(a.name for a in node.names)
                refs.append(
                    ImportReference(
                        file=rel_path,
                        line=node.lineno,
                        kind="from_import",
                        old_text=f"from {node.module} import {names}",
                        new_text="",
                    )
                )

        # String references in import-bearing call sites
        elif isinstance(node, ast.Call):
            _scan_string_refs(node, rel_path, old_module, refs)

    return refs


# ── Known import-bearing call site patterns ──────────────────────


def _scan_string_refs(
    node: ast.Call,
    rel_path: str,
    old_module: str,
    refs: list[ImportReference],
) -> None:
    """Scan a Call node for string references to old_module in import-bearing sites."""
    call_name = _get_call_name(node)
    if not call_name:
        return

    # Determine if this is an import-bearing call
    context = ""
    if call_name == "patch" or (call_name == "patch" and _is_mock_patch(node)):
        context = "mock.patch"
    elif call_name == "object" and _is_patch_object(node):
        context = "mock.patch.object"
    elif call_name == "setattr" and _is_monkeypatch_setattr(node):
        context = "monkeypatch.setattr"
    elif call_name == "import_module":
        context = "importlib.import_module"

    if not context:
        return

    # Check string arguments
    for arg in node.args:
        if not isinstance(arg, ast.Constant) or not isinstance(arg.value, str):
            continue
        val = arg.value
        if old_module in val:
            refs.append(
                ImportReference(
                    file=rel_path,
                    line=node.lineno,
                    kind="string_ref",
                    old_text=val,
                    new_text="",  # Filled in by _compute_rewrites
                    context=context,
                )
            )


def _get_call_name(node: ast.Call) -> str | None:
    """Get the leaf name of a call."""
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def _is_mock_patch(node: ast.Call) -> bool:
    """Check if call is mock.patch(...)."""
    func = node.func
    if isinstance(func, ast.Name) and func.id == "patch":
        return True
    return isinstance(func, ast.Attribute) and func.attr == "patch"


def _is_patch_object(node: ast.Call) -> bool:
    """Check if call is patch.object(...)."""
    func = node.func
    if isinstance(func, ast.Attribute) and func.attr == "object":
        val = func.value
        if isinstance(val, ast.Name) and val.id == "patch":
            return True
        if isinstance(val, ast.Attribute) and val.attr == "patch":
            return True
    return False


def _is_monkeypatch_setattr(node: ast.Call) -> bool:
    """Check if call is monkeypatch.setattr(...)."""
    func = node.func
    return isinstance(func, ast.Attribute) and func.attr == "setattr"


# ── Rewrite computation ──────────────────────────────────────────


def _compute_rewrites(
    refs: list[ImportReference],
    old_module: str,
    new_module: str,
) -> None:
    """Fill in new_text for each reference."""
    for ref in refs:
        if ref.kind == "import":
            ref.new_text = ref.old_text.replace(old_module, new_module)
        elif ref.kind == "from_import":
            ref.new_text = ref.old_text.replace(old_module, new_module)
        elif ref.kind == "string_ref":
            ref.new_text = ref.old_text.replace(old_module, new_module)


# ── libcst-based rewriting ───────────────────────────────────────


def _has_libcst() -> bool:
    """Check if libcst is available."""
    try:
        import libcst as _libcst  # noqa: F401
        del _libcst
        return True
    except ImportError:
        return False


def _rewrite_file_with_libcst(
    filepath: str,
    old_module: str,
    new_module: str,
) -> tuple[bool, str]:
    """Rewrite imports in a file using libcst.

    Returns (success, error_message).
    """
    try:
        import libcst as cst
    except ImportError:
        return False, "libcst not available"

    try:
        with open(filepath, encoding="utf-8") as f:
            source = f.read()
    except OSError as e:
        return False, str(e)

    try:
        tree = cst.parse_module(source)
    except cst.ParserSyntaxError as e:
        return False, f"Parse error: {e}"

    class ImportRewriter(cst.CSTTransformer):
        def __init__(self) -> None:
            self.changed = False

        def leave_ImportFrom(
            self,
            original_node: cst.ImportFrom,
            updated_node: cst.ImportFrom,
        ) -> cst.ImportFrom:
            # Get the module string
            module_str = _cst_module_to_str(updated_node.module)
            if module_str and (
                module_str == old_module
                or module_str.startswith(old_module + ".")
            ):
                new_mod_str = module_str.replace(old_module, new_module, 1)
                new_mod = _str_to_cst_module(new_mod_str)
                if new_mod is not None:
                    self.changed = True
                    return updated_node.with_changes(module=new_mod)
            return updated_node

        def leave_Import(
            self,
            original_node: cst.Import,
            updated_node: cst.Import,
        ) -> cst.Import:
            if not isinstance(updated_node.names, cst.ImportStar):
                new_names = []
                changed = False
                for alias in updated_node.names:
                    name_str = _cst_module_to_str(alias.name)
                    if name_str and (
                        name_str == old_module
                        or name_str.startswith(old_module + ".")
                    ):
                        new_name_str = name_str.replace(old_module, new_module, 1)
                        new_name = _str_to_cst_module(new_name_str)
                        if new_name is not None:
                            new_names.append(alias.with_changes(name=new_name))
                            changed = True
                            continue
                    new_names.append(alias)
                if changed:
                    self.changed = True
                    return updated_node.with_changes(names=new_names)
            return updated_node

        # NOTE: String literals are NOT rewritten here. The CST transformer
        # only handles import/from-import statements. String refs in
        # import-bearing call sites (mock.patch, monkeypatch.setattr,
        # importlib.import_module) are rewritten separately via targeted
        # regex replacement in _rewrite_import_bearing_strings(), which
        # only touches strings inside those specific call patterns.

    rewriter = ImportRewriter()
    try:
        new_tree = tree.visit(rewriter)
    except Exception as e:
        return False, f"CST transform error: {e}"

    if not rewriter.changed:
        return True, ""  # No changes needed

    try:
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(new_tree.code)
    except OSError as e:
        return False, f"Write error: {e}"

    return True, ""


def _rewrite_import_bearing_strings(
    project_root: str,
    string_refs: list[ImportReference],
    _old_module: str,
    _new_module: str,
) -> int:
    """Rewrite string refs in import-bearing call sites only.

    Targets only the specific string literals identified by the AST scanner
    in _scan_string_refs (mock.patch, monkeypatch.setattr,
    importlib.import_module). Does NOT rewrite arbitrary strings.

    Uses line-targeted text replacement so only the exact string on the
    exact line reported by the scanner is touched.
    """
    rewritten = 0
    # Group refs by file
    by_file: dict[str, list[ImportReference]] = {}
    for ref in string_refs:
        full_path = os.path.join(project_root, ref.file)
        by_file.setdefault(full_path, []).append(ref)

    for fpath, refs_in_file in by_file.items():
        try:
            with open(fpath, encoding="utf-8") as f:
                lines = f.readlines()
        except OSError:
            continue

        changed = False
        for ref in refs_in_file:
            line_idx = ref.line - 1  # 0-indexed
            if 0 <= line_idx < len(lines):
                old_line = lines[line_idx]
                # Only replace the exact old_text within this line
                if ref.old_text in old_line:
                    lines[line_idx] = old_line.replace(ref.old_text, ref.new_text, 1)
                    changed = True

        if changed:
            try:
                with open(fpath, "w", encoding="utf-8") as f:
                    f.writelines(lines)
                rewritten += 1
            except OSError:
                pass

    return rewritten


def _cst_module_to_str(module: Any) -> str | None:
    """Convert a libcst module attribute to a dotted string."""
    try:
        import libcst as cst
    except ImportError:
        return None
    if module is None:
        return None
    if isinstance(module, cst.Attribute):
        left = _cst_module_to_str(module.value)
        if left is None:
            return None
        return f"{left}.{module.attr.value}"
    if isinstance(module, cst.Name):
        return module.value
    return None


def _str_to_cst_module(dotted: str) -> Any:
    """Convert a dotted string to a libcst Attribute/Name chain."""
    try:
        block_tree = ast.parse(dedented)
    except SyntaxError:
        block_tree = None

    augassign_all: set[str] = set()  # ALL augassign targets (for input detection)
    if block_tree is not None:
        defined_in_block = _collect_assigned_names(block_tree)
        used_in_block = _collect_used_names(block_tree)
        augassign_only = _collect_augassign_only_names(block_tree)
        # Collect ALL augassign targets (not just "only" ones)
        for node in ast.walk(block_tree):
            if isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name):
                augassign_all.add(node.target.id)
    else:
        # AST parse failed (try/except, with, partial blocks).
        # Fall back to token-based scanning of the raw block lines.
        defined_in_block, used_in_block, augassign_only = _scan_names_tokenized(block_lines)
        augassign_all = augassign_only.copy()
        # Also extract all augassign targets from the tokenizer
        # (augassign_only excludes vars with regular assigns, but we need all)
        import io, tokenize, textwrap, keyword as _kw
        try:
            src = textwrap.dedent("".join(block_lines))
            for tok in tokenize.generate_tokens(io.StringIO(src).readline):
                pass  # already parsed above
        except Exception:
            pass
        # Re-scan: augassign_all = any NAME followed by +=/-= etc.
        _AUG_OPS = {'+=', '-=', '*=', '/=', '|=', '&=', '%=', '**=', '//='}
        try:
            toks = list(tokenize.generate_tokens(io.StringIO(textwrap.dedent("".join(block_lines))).readline))
            for i, tok in enumerate(toks):
                if tok.type == tokenize.NAME and not _kw.iskeyword(tok.string):
                    for j in range(i + 1, len(toks)):
                        if toks[j].type not in (tokenize.NL, tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT, tokenize.COMMENT):
                            if toks[j].type == tokenize.OP and toks[j].string in _AUG_OPS:
                                augassign_all.add(tok.string)
                            break
        except Exception:
            pass

    # Variables used AFTER the block in the enclosing function
    post_block_used = _collect_names_after_line(enclosing, end_line)

    # Module-level names: imports, top-level assignments, class/function defs.
    # These are accessible without being passed as parameters.
    module_level_names = _collect_module_level_names(tree)

    # Inputs: ANY name used in the block but not defined there, UNLESS it's
    # a builtin or module-level name. This catches closure variables, outer
    # scope locals, and all enclosing function state — no false negatives.
    #
    # AugAssign targets (+=, -=) are ALWAYS potential inputs — they read the
    # current value before writing. Even if the block also has a regular assign
    # (e.g., `errors = 0` in try + `errors += 1` in except), the augassign
    # path needs the enclosing scope value.
    # For augassign targets: if the variable is assigned BEFORE the block in the
    # enclosing function, it's an input (the block reads from the enclosing scope).
    # If NOT assigned before, and augassign_only (no regular assign in block), it
    # needs initialization inside the helper.
    # This correctly handles: `errors = 0` before block + `errors += 1` inside → input.
    # And: `verdict, ok = validate(...)` inside block → NOT input (defined in block).
    pre_block = _collect_names_before_line(enclosing, start_line)
    func_params = {arg.arg for arg in enclosing.args.args}
    enclosing_scope = pre_block | func_params
    augassign_inputs = augassign_all & enclosing_scope  # augassign vars from enclosing scope

    inputs = sorted((used_in_block | augassign_inputs) - (defined_in_block - augassign_inputs) - module_level_names)

    # AugAssign-only vars (no regular assign) that aren't passed as inputs
    # need initialization inside the helper (e.g., errors = 0).
    needs_init = augassign_only - set(inputs)

    # Outputs: defined in block and used after it
    outputs = sorted(defined_in_block & post_block_used)

    # Ensure needs_init vars that are used after the block appear in outputs.
    # A conditional accumulator like `errors += 1` in an except branch defines
    # the variable (augassign) but only on the error path. If used after the
    # block, it must be returned — even though we initialize it inside the helper.
    for var in sorted(needs_init):
        if var in post_block_used and var not in outputs:
            outputs.append(var)
    outputs = sorted(set(outputs))

    # Filter out builtins and module-level names
    _BUILTINS = {"True", "False", "None", "print", "len", "range", "enumerate",
                 "zip", "map", "filter", "sorted", "list", "dict", "set", "tuple",
                 "str", "int", "float", "bool", "isinstance", "type", "super",
                 "open", "any", "all", "min", "max", "sum", "abs", "round",
                 "hasattr", "getattr", "setattr", "ValueError", "TypeError",
                 "KeyError", "IndexError", "AttributeError", "RuntimeError",
                 "Exception", "OSError", "FileNotFoundError", "ImportError"}
    inputs = [n for n in inputs if n not in _BUILTINS]
    outputs = [n for n in outputs if n not in _BUILTINS]

    # Detect mutable argument side effects: if the block calls .append(),
    # .update(), .extend(), .pop(), etc. on a variable, it's an implicit
    # output even if not reassigned. No IDE handles this well.
    if block_tree is not None:
        mutated_args = _detect_mutable_side_effects(block_tree, set(inputs))
        for ma in mutated_args:
            if ma not in outputs:
                outputs.append(ma)
        outputs = sorted(set(outputs))

    # Control flow validation: block must not contain break/continue/return
    # that would change the enclosing function's control flow
    if block_tree is not None:
        flow_issues = _check_control_flow(block_tree)
        if flow_issues:
            result.errors.extend(flow_issues)
            if not dry_run:
                return result  # Refuse to extract unsafe control flow

    result.inputs = inputs
    result.outputs = outputs

    # Build the helper function
    params = ", ".join(inputs)
    # Determine return type hint
    if len(outputs) == 0:
        ret_hint = " -> None"
        return_stmt = ""
    elif len(outputs) == 1:
        ret_hint = ""
        return_stmt = f"\n{block_indent}    return {outputs[0]}"
    else:
        ret_hint = ""
        return_stmt = f"\n{block_indent}    return {', '.join(outputs)}"

    # The helper is inserted before the enclosing function, at the same
    # indent level as the enclosing def (usually column 0 for top-level,
    # or class body indent for methods).
    enclosing_indent = " " * (enclosing.col_offset or 0)

    helper_def = f"{enclosing_indent}def {helper_name}({params}){ret_hint}:\n"
    # Prepend initialization for AugAssign-only variables (+=, -=)
    # that aren't passed in as inputs and need a starting value.
    init_lines = ""
    for var in sorted(needs_init):
        init_lines += f"{enclosing_indent}    {var} = 0\n"

    # Re-indent: strip block's indent, add helper body indent
    helper_body = init_lines
    for line in block_lines:
        if line.strip():
            if line.startswith(block_indent):
                relative = line[len(block_indent):]
            else:
                relative = line.lstrip()
            helper_body += enclosing_indent + "    " + relative
        else:
            helper_body += "\n"
    if return_stmt:
        helper_body += enclosing_indent + "    " + return_stmt.strip() + "\n"

    result.extracted_code = helper_def + helper_body
    result.helper_signature = f"def {helper_name}({params}){ret_hint}"

    # Build the call replacement
    if len(outputs) == 0:
        call = f"{block_indent}{helper_name}({', '.join(inputs)})\n"
    elif len(outputs) == 1:
        call = f"{block_indent}{outputs[0]} = {helper_name}({', '.join(inputs)})\n"
    else:
        call = f"{block_indent}{', '.join(outputs)} = {helper_name}({', '.join(inputs)})\n"
    result.call_replacement = call.strip()

    if dry_run:
        return result

    # Apply: insert helper before the enclosing function, replace block with call
    new_lines = list(lines)

    # Insert helper function before the enclosing function
    insert_at = enclosing.lineno - 1  # Before the enclosing def
    helper_lines = (result.extracted_code + "\n\n").splitlines(keepends=True)
    for i, hl in enumerate(helper_lines):
        new_lines.insert(insert_at + i, hl)

    # Adjust line numbers after insertion
    offset = len(helper_lines)
    adjusted_start = start_line - 1 + offset
    adjusted_end = end_line + offset

    # Replace the block with the call
    new_lines[adjusted_start:adjusted_end] = [call]

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
    import tokenize

    import textwrap
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
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names.add(node.name)
        elif isinstance(node, ast.ClassDef):
            names.add(node.name)
    return names


def _find_enclosing_function(
    tree: ast.Module, line: int
) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    """Find the function definition containing the given line."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "__all__":
                    if isinstance(node.value, (ast.List, ast.Tuple)):
                        return [
                            elt.value
                            for elt in node.value.elts
                            if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
                        ]

    # Fallback: all non-underscore top-level names
    names: list[str] = []
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
                if isinstance(target, ast.Name) and not target.id.startswith("_"):
                    names.append(target.id)
    return names


# ── Smoke gate ───────────────────────────────────────────────────


def smoke_test_import(
    module_name: str,
    project_root: str,
    *,
    timeout: int = 10,
) -> tuple[bool, str]:
    """Smoke-test that a module can be imported.

    Uses subprocess to avoid polluting the current process.

    Returns (success, error_message).
    """
    try:
        result = subprocess.run(
            [sys.executable, "-c", f"import {module_name}"],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode == 0:
            return True, ""
        return False, result.stderr.strip()[:500]
    except subprocess.TimeoutExpired:
        return False, f"Import timed out after {timeout}s"
    except Exception as e:
        return False, str(e)


# ── Main orchestration ───────────────────────────────────────────


# ── Orchestrator ──────────────────────────────────────────────────


def refactor_move(
    project_root: str,
    source: str,
    destination: str,
    *,
    dry_run: bool = True,
    generate_shim_file: bool = True,
) -> MoveResult:
    """Move a module from source to destination with import rewriting.

    Args:
        project_root: Absolute path to the project root.
        source: Dotted module path (e.g., ``"lintgate.old_module"``).
        destination: Dotted module path (e.g., ``"lintgate.new_module"``).
        dry_run: If True, only scan and report — don't rewrite.
        generate_shim_file: If True, generate a backward-compatibility shim.

    Returns:
        MoveResult with all findings and rewrite outcomes.
    """
    result = MoveResult(source=source, destination=destination, dry_run=dry_run)

    # Validate source exists
    source_path = os.path.join(project_root, source.replace(".", os.sep) + ".py")
    if not os.path.isfile(source_path):
        result.errors.append(f"Source module not found: {source_path}")
        return result

    # Scan for all references
    refs = scan_all_imports(project_root, source)
    _compute_rewrites(refs, source, destination)
    result.references_found = refs

    if dry_run:
        # In dry-run, check if we can auto-apply
        if not _has_libcst():
            result.degraded = True
            result.warnings.append(
                "libcst not installed — dry_run shows what needs changing "
                "but auto-apply requires: pip install 'lintgate[refactor]'"
            )
        return result

    # ── Apply phase ──────────────────────────────────────────────

    # Gate: libcst is required for auto-apply. Without it, refuse to
    # move the file — a moved file with unrewritten imports is worse
    # than no move at all.
    if not _has_libcst():
        result.degraded = True
        result.warnings.append(
            "libcst not installed — cannot auto-apply. "
            "Install with: pip install 'lintgate[refactor]'"
        )
        return result

    dest_path = os.path.join(project_root, destination.replace(".", os.sep) + ".py")

    # Move the file
    if source_path != dest_path:
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
        if os.path.exists(dest_path):
            result.warnings.append(f"Destination already exists: {dest_path}")
        else:
            try:
                os.rename(source_path, dest_path)
            except OSError as e:
                result.errors.append(f"Failed to move file: {e}")
                return result

    # Rewrite imports (libcst guaranteed available by gate above)
    rewritten = 0
    files_to_rewrite: set[str] = set()
    for ref in refs:
        full_path = os.path.join(project_root, ref.file)
        files_to_rewrite.add(full_path)

    for fpath in sorted(files_to_rewrite):
        success, error = _rewrite_file_with_libcst(fpath, source, destination)
        if success:
            rewritten += 1
        elif error:
            result.errors.append(f"Rewrite failed for {fpath}: {error}")

    # Rewrite string refs in import-bearing call sites only
    string_refs = [r for r in refs if r.kind == "string_ref"]
    if string_refs:
        str_rewritten = _rewrite_import_bearing_strings(
            project_root, string_refs, source, destination
        )
        rewritten += str_rewritten

    result.references_rewritten = rewritten

    # Generate shim if requested
    if generate_shim_file and source_path != dest_path:
        try:
            shim_path = generate_shim(project_root, source, destination)
            result.shim_generated = True
            result.shim_path = os.path.relpath(shim_path, project_root)
        except Exception as e:
            result.warnings.append(f"Shim generation failed: {e}")

    # Smoke test
    passed, error = smoke_test_import(destination, project_root)
    result.smoke_test_passed = passed
    result.smoke_test_error = error

    return result
