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
    assert r.confidence == 0.95  # pure leaf function (no calls) gets highest pure confidence
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
    """Pure leaf functions get 0.95 confidence; impure functions get 1.0."""
    code = """
def pure_fn(x):
    return x * 2

def impure_fn():
    print("hi")
"""
    tree = ast.parse(code)
    results = analyze_purity(tree)

    assert results["pure_fn"].confidence == 0.95  # leaf function, no calls
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
    assert pure.confidence == 0.95  # leaf function, no calls

    impure = results["async_impure"]
    assert impure.is_pure is False
    assert impure.parameter_count == 1
    assert len(impure.side_effects) == 1
    assert impure.side_effects[0].kind == "io_call"


# ---------------------------------------------------------------------------
# Expanded side-effect detectors (#307)
# ---------------------------------------------------------------------------


class TestFileWriteDetection:
    """File write operations should be detected as impure."""

    def test_path_write_text(self):
        code = """
def writer(p):
    p.write_text("hello")
"""
        r = analyze_purity(ast.parse(code))["writer"]
        assert r.is_pure is False
        assert any(se.kind == "io_call" and "write_text" in se.detail for se in r.side_effects)

    def test_path_mkdir(self):
        code = """
def mkdirs(p):
    p.mkdir(parents=True)
"""
        r = analyze_purity(ast.parse(code))["mkdirs"]
        assert r.is_pure is False
        assert any("mkdir" in se.detail for se in r.side_effects)

    def test_json_dump(self):
        code = """
import json
def save(data, fh):
    json.dump(data, fh)
"""
        r = analyze_purity(ast.parse(code))["save"]
        assert r.is_pure is False
        assert any("json.dump" in se.detail for se in r.side_effects)

    def test_shutil_impure(self):
        code = """
import shutil
def copy_files(src, dst):
    shutil.copy(src, dst)
"""
        r = analyze_purity(ast.parse(code))["copy_files"]
        assert r.is_pure is False


class TestDatabaseOperationDetection:
    """Database mutation methods should be detected as impure."""

    def test_cursor_execute(self):
        code = """
def run_query(cursor, sql):
    cursor.execute(sql)
"""
        r = analyze_purity(ast.parse(code))["run_query"]
        assert r.is_pure is False
        assert any(se.kind == "io_call" and "execute" in se.detail for se in r.side_effects)

    def test_connection_commit(self):
        code = """
def save(conn):
    conn.commit()
"""
        r = analyze_purity(ast.parse(code))["save"]
        assert r.is_pure is False
        assert any(se.kind == "io_call" and "commit" in se.detail for se in r.side_effects)


class TestMLOperationDetection:
    """ML training/loading operations should be detected as impure."""

    def test_model_backward(self):
        code = """
def train_step(loss):
    loss.backward()
"""
        r = analyze_purity(ast.parse(code))["train_step"]
        assert r.is_pure is False
        assert any("backward" in se.detail for se in r.side_effects)

    def test_optimizer_step(self):
        code = """
def update(optimizer):
    optimizer.step()
"""
        r = analyze_purity(ast.parse(code))["update"]
        assert r.is_pure is False
        assert any("step" in se.detail for se in r.side_effects)

    def test_torch_load(self):
        code = """
import torch
def load_model(path):
    return torch.load(path)
"""
        r = analyze_purity(ast.parse(code))["load_model"]
        assert r.is_pure is False


class TestSubscriptAndAugAssignDetection:
    """Subscript writes and augmented assigns on externals should be detected."""

    def test_external_subscript_write(self):
        code = """
registry = {}
def register(name, val):
    registry[name] = val
"""
        r = analyze_purity(ast.parse(code))["register"]
        assert r.is_pure is False
        assert any(se.kind == "mutation" and "registry" in se.detail for se in r.side_effects)

    def test_local_subscript_write_is_pure(self):
        code = """
def build_dict(items):
    d = {}
    for k, v in items:
        d[k] = v
    return d
"""
        r = analyze_purity(ast.parse(code))["build_dict"]
        assert r.is_pure is True

    def test_augassign_on_external_attribute(self):
        code = """
counter = type('', (), {'value': 0})()
def increment():
    counter.value += 1
"""
        r = analyze_purity(ast.parse(code))["increment"]
        assert r.is_pure is False
        assert any(se.kind == "attribute_mutation" for se in r.side_effects)

    def test_delete_external_name(self):
        code = """
cache = {}
def clear_entry(key):
    del cache[key]
"""
        r = analyze_purity(ast.parse(code))["clear_entry"]
        assert r.is_pure is False


class TestExternalAttributeWrite:
    """Attribute writes on non-local, non-self objects should be detected."""

    def test_external_obj_attribute_write(self):
        code = """
config = type('', (), {})()
def set_debug(val):
    config.debug = val
"""
        r = analyze_purity(ast.parse(code))["set_debug"]
        assert r.is_pure is False
        assert any(se.kind == "attribute_mutation" and "config.debug" in se.detail for se in r.side_effects)


class TestConfidenceBands:
    """Evidence-weighted confidence replaces flat 0.8."""

    def test_leaf_function_gets_0_95(self):
        code = """
def add(a, b):
    return a + b
"""
        r = analyze_purity(ast.parse(code))["add"]
        assert r.confidence == 0.95

    def test_resolved_builtin_calls_get_0_90(self):
        code = """
def normalize(items):
    return sorted(set(items))
"""
        r = analyze_purity(ast.parse(code))["normalize"]
        assert r.confidence == 0.90

    def test_few_unresolved_lowercase_get_0_80(self):
        code = """
def process(x):
    return helper(x)
"""
        r = analyze_purity(ast.parse(code))["process"]
        assert r.confidence == 0.80

    def test_many_unresolved_lowercase_get_0_65(self):
        code = """
def pipeline(x):
    a = step_one(x)
    b = step_two(a)
    c = step_three(b)
    return step_four(c)
"""
        r = analyze_purity(ast.parse(code))["pipeline"]
        assert r.confidence == 0.65

    def test_impure_always_1_0(self):
        code = """
def log_it(x):
    print(x)
"""
        r = analyze_purity(ast.parse(code))["log_it"]
        assert r.confidence == 1.0

    def test_calls_known_pure_same_module_get_0_90(self):
        code = """
def helper(x):
    return x * 2

def caller(x):
    return helper(x) + 1
"""
        results = analyze_purity(ast.parse(code))
        assert results["caller"].confidence == 0.90
