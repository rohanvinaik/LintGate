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


# ---------------------------------------------------------------------------
# VALUE assertion tests — verify exact computed fields, not just booleans
# ---------------------------------------------------------------------------


def test_pure_function_exact_fields():
    """Pure function: verify parameter_count, confidence, side_effects, annotations."""
    code = """
def pure_add(a, b) -> int:
    return a + b
"""
    tree = ast.parse(code)
    results = analyze_purity(tree)
    r = results["pure_add"]

    assert r.function_name == "pure_add"
    assert r.qualified_name == "pure_add"
    assert r.parameter_count == 2
    assert r.confidence == 0.8  # pure functions get 0.8 heuristic confidence
    assert r.side_effects == ()  # no side effects — empty tuple
    assert r.return_annotation == "int"
    assert r.line == 2


def test_pure_function_no_annotation():
    """Pure function without return annotation yields None."""
    code = """
def add(x, y):
    return x + y
"""
    tree = ast.parse(code)
    r = analyze_purity(tree)["add"]

    assert r.return_annotation is None
    assert r.parameter_count == 2
    assert r.side_effects == ()


def test_impure_global_write_side_effect_details():
    """Global write: verify exact side_effect kind, detail, and confidence."""
    code = """
def impure_add(a, b):
    global counter
    counter += 1
    return a + b
"""
    tree = ast.parse(code)
    r = analyze_purity(tree)["impure_add"]

    assert r.is_pure is False
    assert r.confidence == 1.0  # impure functions get 1.0 certainty
    assert r.parameter_count == 2
    assert len(r.side_effects) == 1

    se = r.side_effects[0]
    assert se.kind == "global_write"
    assert se.node_type == "Global"
    assert se.detail == "Writes to global 'counter'"


def test_impure_print_call_side_effect_details():
    """print() call: verify exact side_effect kind and detail string."""
    code = """
def print_test(x):
    print(x)
    return x
"""
    tree = ast.parse(code)
    r = analyze_purity(tree)["print_test"]

    assert r.is_pure is False
    assert r.confidence == 1.0
    assert r.parameter_count == 1
    assert len(r.side_effects) == 1

    se = r.side_effects[0]
    assert se.kind == "io_call"
    assert se.node_type == "Call"
    assert se.detail == "Calls impure namespace/function: print"


def test_mutable_default_side_effect_details():
    """Mutable default arg: verify exact side_effect kind and detail."""
    code = """
def mutable_default(x, lst=[]):
    lst.append(x)
    return lst
"""
    tree = ast.parse(code)
    r = analyze_purity(tree)["mutable_default"]

    assert r.is_pure is False
    assert r.parameter_count == 2
    # Should have a mutable_default side effect
    mutable_effects = [se for se in r.side_effects if se.kind == "mutable_default"]
    assert len(mutable_effects) == 1
    assert mutable_effects[0].node_type == "DefaultArg"
    assert "mutable default argument" in mutable_effects[0].detail


def test_os_call_side_effect_details():
    """os namespace call: verify exact impure namespace detection."""
    code = """
def os_usage():
    import os
    return os.getcwd()
"""
    tree = ast.parse(code)
    r = analyze_purity(tree)["os_usage"]

    assert r.is_pure is False
    assert r.confidence == 1.0
    assert r.parameter_count == 0
    assert len(r.side_effects) == 1

    se = r.side_effects[0]
    assert se.kind == "io_call"
    assert se.detail == "Calls impure namespace/function: os.getcwd"


def test_subprocess_namespace_side_effect():
    """Calling subprocess.run is flagged as impure namespace."""
    code = """
def run_cmd(cmd):
    import subprocess
    return subprocess.run(cmd)
"""
    tree = ast.parse(code)
    r = analyze_purity(tree)["run_cmd"]

    assert r.is_pure is False
    assert r.parameter_count == 1
    assert len(r.side_effects) == 1

    se = r.side_effects[0]
    assert se.kind == "io_call"
    assert se.detail == "Calls impure namespace/function: subprocess.run"


def test_transitive_impurity_side_effect_details():
    """Transitive impurity: calling an impure function propagates an impure_call effect."""
    code = """
def impure_helper(x):
    global y
    y = x
    return x

def main_impure(x):
    return impure_helper(x)
"""
    tree = ast.parse(code)
    results = analyze_purity(tree)

    # The direct impure function
    helper = results["impure_helper"]
    assert helper.parameter_count == 1
    assert len(helper.side_effects) == 1
    assert helper.side_effects[0].kind == "global_write"
    assert helper.side_effects[0].detail == "Writes to global 'y'"

    # The transitive caller
    main = results["main_impure"]
    assert main.parameter_count == 1
    assert main.confidence == 1.0
    assert len(main.side_effects) == 1
    assert main.side_effects[0].kind == "impure_call"
    assert "impure_helper" in main.side_effects[0].detail


def test_class_method_qualified_name_and_attribute_mutation():
    """Class method: verify qualified_name and attribute mutation side effect."""
    code = """
class MyClass:
    def set_value(self, v):
        self.value = v
"""
    tree = ast.parse(code)
    results = analyze_purity(tree)
    r = results["MyClass.set_value"]

    assert r.function_name == "set_value"
    assert r.qualified_name == "MyClass.set_value"
    assert r.is_pure is False
    assert r.parameter_count == 2  # self + v
    assert len(r.side_effects) == 1

    se = r.side_effects[0]
    assert se.kind == "attribute_mutation"
    assert se.node_type == "Assign"
    assert "self.value" in se.detail


def test_class_init_self_assign_is_not_mutation():
    """__init__ self assignment is NOT flagged as attribute mutation."""
    code = """
class MyClass:
    def __init__(self, x):
        self.x = x
"""
    tree = ast.parse(code)
    results = analyze_purity(tree)
    r = results["MyClass.__init__"]

    assert r.function_name == "__init__"
    assert r.qualified_name == "MyClass.__init__"
    assert r.parameter_count == 2  # self + x
    # __init__ self-assignment is NOT treated as mutation
    attr_mutations = [se for se in r.side_effects if se.kind == "attribute_mutation"]
    assert attr_mutations == []


def test_generator_yield_side_effect():
    """Yield makes a function impure with 'generator' side effect."""
    code = """
def gen(n):
    for i in range(n):
        yield i
"""
    tree = ast.parse(code)
    r = analyze_purity(tree)["gen"]

    assert r.is_pure is False
    assert r.parameter_count == 1
    assert len(r.side_effects) == 1

    se = r.side_effects[0]
    assert se.kind == "generator"
    assert se.node_type == "Yield"
    assert "stateful generator" in se.detail


def test_yield_from_side_effect():
    """yield from also produces a generator side effect."""
    code = """
def delegating_gen(items):
    yield from items
"""
    tree = ast.parse(code)
    r = analyze_purity(tree)["delegating_gen"]

    assert r.is_pure is False
    assert len(r.side_effects) == 1
    assert r.side_effects[0].kind == "generator"
    assert r.side_effects[0].node_type == "YieldFrom"


def test_multiple_side_effects_accumulated():
    """Function with multiple impurity sources accumulates all side effects."""
    code = """
def multi_impure(x, data=[]):
    global g
    g = x
    print(x)
    return data
"""
    tree = ast.parse(code)
    r = analyze_purity(tree)["multi_impure"]

    assert r.is_pure is False
    assert r.parameter_count == 2
    kinds = {se.kind for se in r.side_effects}
    assert "mutable_default" in kinds
    assert "global_write" in kinds
    assert "io_call" in kinds
    assert len(r.side_effects) == 3


def test_parameter_count_varargs_kwargs():
    """Parameter count includes *args and **kwargs."""
    code = """
def variadic(a, b, *args, key=None, **kwargs) -> str:
    return str(a)
"""
    tree = ast.parse(code)
    r = analyze_purity(tree)["variadic"]

    # a, b = 2 regular + *args = 1 + key = 1 kwonly + **kwargs = 1 => 5
    assert r.parameter_count == 5
    assert r.return_annotation == "str"
    assert r.is_pure is True
    assert r.side_effects == ()


def test_pure_function_confidence_vs_impure():
    """Pure functions get 0.8 confidence; impure functions get 1.0."""
    code = """
def pure_fn(x):
    return x * 2

def impure_fn():
    print("hi")
"""
    tree = ast.parse(code)
    results = analyze_purity(tree)

    assert results["pure_fn"].confidence == 0.8
    assert results["impure_fn"].confidence == 1.0


def test_external_mutation_via_method_call():
    """Mutating a non-local object via .append() is flagged."""
    code = """
external_list = []

def mutator():
    external_list.append(1)
"""
    tree = ast.parse(code)
    r = analyze_purity(tree)["mutator"]

    assert r.is_pure is False
    mutation_effects = [se for se in r.side_effects if se.kind == "mutation"]
    assert len(mutation_effects) == 1
    assert ".append()" in mutation_effects[0].detail


def test_multiple_globals_produce_multiple_effects():
    """A single `global a, b` statement produces one side effect per name."""
    code = """
def multi_global():
    global a, b
    a = 1
    b = 2
"""
    tree = ast.parse(code)
    r = analyze_purity(tree)["multi_global"]

    assert r.is_pure is False
    global_effects = [se for se in r.side_effects if se.kind == "global_write"]
    assert len(global_effects) == 2
    details = {se.detail for se in global_effects}
    assert "Writes to global 'a'" in details
    assert "Writes to global 'b'" in details


def test_dict_mutable_default():
    """Dict as default argument triggers mutable_default side effect."""
    code = """
def with_dict_default(x, cache={}):
    cache[x] = True
    return cache
"""
    tree = ast.parse(code)
    r = analyze_purity(tree)["with_dict_default"]

    assert r.is_pure is False
    mutable_effects = [se for se in r.side_effects if se.kind == "mutable_default"]
    assert len(mutable_effects) == 1
    assert "mutable default argument" in mutable_effects[0].detail
    assert mutable_effects[0].node_type == "DefaultArg"


def test_async_function_purity():
    """Async functions are analyzed the same way as regular functions."""
    code = """
async def async_pure(x, y):
    return x + y

async def async_impure(x):
    print(x)
"""
    tree = ast.parse(code)
    results = analyze_purity(tree)

    pure = results["async_pure"]
    assert pure.is_pure is True
    assert pure.parameter_count == 2
    assert pure.side_effects == ()
    assert pure.confidence == 0.8

    impure = results["async_impure"]
    assert impure.is_pure is False
    assert impure.parameter_count == 1
    assert len(impure.side_effects) == 1
    assert impure.side_effects[0].kind == "io_call"
