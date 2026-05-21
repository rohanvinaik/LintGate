"""In-process runtime probe for test→source linkage.

When static discovery returns nothing and the fallback has to load every
test in the filename-matched files, the result is noisy: most of those
tests don't actually exercise the target function. This probe filters
that noisy set down to tests that *demonstrably* enter the target
function's code object at least once.

Uses ``sys.setprofile`` to observe frame entries. Each test callable is
invoked once with the profiler installed; we record whether the frame
entered the target's ``__code__``. Ground-truth by construction — there
is no heuristic to get wrong.

Cost is one test invocation per candidate (they were going to run anyway
during mutation). The probe is opt-in via ``probe_fallback_callables``
and bounded by ``max_probe`` to cap time on pathological test files.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from types import CodeType
from typing import Any

_DEFAULT_MAX_PROBE = 100


def resolve_target_code(source_file: str, func_name: str) -> CodeType | None:
    """Import *source_file* as a throwaway module and return the function's code object.

    Returns None when the file can't be loaded, the symbol doesn't exist,
    or it isn't a callable. Qualified names (``Class.method``) are walked
    via getattr.
    """
    if not source_file or not os.path.isfile(source_file):
        return None
    module_name = f"_lintgate_probe_{abs(hash(source_file))}"
    spec = importlib.util.spec_from_file_location(module_name, source_file)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception:
        return None

    obj: Any = module
    for part in func_name.split("."):
        obj = getattr(obj, part, None)
        if obj is None:
            return None

    code = getattr(obj, "__code__", None)
    if isinstance(code, CodeType):
        return code
    return None


def probe_fallback_callables(
    source_file: str,
    func_name: str,
    callables: list[Any],
    *,
    max_probe: int = _DEFAULT_MAX_PROBE,
) -> tuple[list[Any], int]:
    """Filter *callables* to those that enter the target function's code object.

    Returns ``(verified, probed_count)``. Callables beyond ``max_probe``
    are passed through unfiltered to avoid unbounded probe time — when
    the fallback set is enormous, bias toward inclusion.

    When the target code can't be resolved (file missing, symbol gone)
    returns ``(callables, 0)`` unchanged so the caller degrades safely.
    """
    target_code = resolve_target_code(source_file, func_name)
    if target_code is None or not callables:
        return list(callables), 0

    # Match by (filename, first line) rather than code-object identity:
    # re-imports of the same source produce distinct code objects for the
    # same function, but both share the same file + line. Code object
    # identity would give false negatives whenever anything re-imports.
    target_filename = target_code.co_filename
    target_lineno = target_code.co_firstlineno

    probe_slice = callables[:max_probe]
    unprobed_tail = callables[max_probe:]

    verified: list[Any] = []
    hit = {"flag": False}

    def _profiler(frame: Any, event: str, _arg: Any) -> None:
        if event != "call":
            return
        code = frame.f_code
        if code.co_filename == target_filename and code.co_firstlineno == target_lineno:
            hit["flag"] = True

    for fn in probe_slice:
        hit["flag"] = False
        previous = sys.getprofile()
        sys.setprofile(_profiler)
        try:
            underlying = getattr(fn, "__func__", fn)
            underlying()
        except BaseException:
            # Tests may raise assertion errors or other exceptions during
            # probe — irrelevant; we only care whether the frame entered.
            pass
        finally:
            sys.setprofile(previous)
        if hit["flag"]:
            verified.append(fn)

    return verified + unprobed_tail, len(probe_slice)
