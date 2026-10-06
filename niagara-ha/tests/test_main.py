"""Guards on the poll loop's module structure.

A sibling add-on was taken down on boot by an edit that landed a *call* to
a new helper without landing the definition — the anchor the edit matched
was inside a function that no longer existed. Nothing caught it until the
service failed to start, because the module is only ever imported by the
process that runs it.

These tests are cheap and would have caught that in CI.
"""
import ast
import builtins
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

MAIN = SRC / "main.py"
TREE = ast.parse(MAIN.read_text())


def _defined_names(tree):
    """Everything callable by a bare name at module scope."""
    names = set(dir(builtins))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            names.add(node.id)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                names.add(alias.asname or alias.name)
        elif isinstance(node, ast.arg):
            names.add(node.arg)
        elif isinstance(node, (ast.ExceptHandler,)) and node.name:
            names.add(node.name)
    return names


def test_every_bare_call_resolves_to_something_defined():
    """A call to a helper whose definition never landed would fail on boot."""
    defined = _defined_names(TREE)
    missing = sorted({
        node.func.id
        for node in ast.walk(TREE)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        and node.func.id not in defined
    })
    assert not missing, f"called but never defined in main.py: {missing}"


def test_the_guard_catches_a_missing_definition():
    """The test above is worthless if it cannot fail."""
    broken = ast.parse("def run():\n    _helper_that_does_not_exist()\n")
    defined = _defined_names(broken)
    missing = [
        node.func.id
        for node in ast.walk(broken)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        and node.func.id not in defined
    ]
    assert missing == ["_helper_that_does_not_exist"]


def test_the_module_imports_cleanly():
    """Catches a syntax error or a bad top-level import."""
    import importlib

    import main

    importlib.reload(main)
    assert callable(main.main)


def test_alarm_helpers_are_defined_before_use():
    defined = {
        node.name for node in ast.walk(TREE)
        if isinstance(node, ast.FunctionDef)
    }
    assert {"_write_alarm_probe", "_poll_alarms", "_write_json"} <= defined
