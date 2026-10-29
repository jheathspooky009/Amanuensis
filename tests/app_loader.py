"""Loads the plain-logic parts of Amanuensis.py without starting the app.

Amanuensis.py is one big file that builds its window as soon as it is
imported, so a normal "import Amanuensis" would open a window (and fail on a
computer with no screen, like the GitHub runner). To test the logic
anyway, this helper reads the file as text and runs only the safe top-level
pieces:

  * every function and class (defining one does not run it),
  * plain settings such as numbers, text, lists, dictionaries, and compiled
    patterns (calls are limited to a short approved list),
  * the tk variable declarations (StringVar and friends), which are replaced
    by tiny stand-ins so validate_settings() can be tested.

Everything else (creating the window, building tabs, starting hotkeys) is
skipped. Nothing here changes Amanuensis.py, and a piece that cannot be
loaded is skipped, so the app code never needs a special test mode.
"""

import ast
import collections
import difflib
import hashlib
import html
import json
import math
import os
import queue
import random
import re
import threading
import time
import zipfile
import zlib
from datetime import datetime, timedelta

APP_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "Amanuensis.py")

# The only function calls allowed in a top-level setting while loading.
SAFE_CALL_NAMES = {
    "set", "frozenset", "dict", "list", "tuple", "range", "len", "min", "max",
    "sorted", "int", "float", "str", "bool", "sum", "abs", "round", "zip",
    "enumerate", "reversed", "bytes", "bytearray", "chr", "ord",
    # The app's own plain helpers that build lookup tables and the run state.
    "_build_finger_pair_shifts", "TyperState",
}
SAFE_TEXT_METHODS = {"split", "join", "splitlines", "strip", "lower", "upper"}
SAFE_CALL_ATTRIBUTES = {
    ("re", "compile"), ("re", "escape"), ("collections", "namedtuple"),
    ("collections", "OrderedDict"), ("collections", "defaultdict"),
    ("str", "maketrans"), ("threading", "Event"), ("threading", "Lock"),
    ("queue", "Queue"),
}
VARIABLE_CLASS_NAMES = {"StringVar", "BooleanVar", "IntVar", "DoubleVar", "Variable"}


class FakeVariable:
    """Stands in for a tkinter variable (a value with get and set)."""

    def __init__(self, master=None, value=None, name=None):
        self._value = value

    def get(self):
        return self._value

    def set(self, value):
        self._value = value

    def trace_add(self, *args, **kwargs):
        return None


class FakeTkModule:
    StringVar = BooleanVar = IntVar = DoubleVar = Variable = FakeVariable


class FakeMessageBox:
    """Records the dialogs the app would have shown instead of showing them."""

    def __init__(self):
        self.calls = []

    def _record(self, kind, *args, **kwargs):
        self.calls.append((kind, args))
        return True

    def showerror(self, *args, **kwargs):
        return self._record("error", *args)

    def showwarning(self, *args, **kwargs):
        return self._record("warning", *args)

    def showinfo(self, *args, **kwargs):
        return self._record("info", *args)

    def askyesno(self, *args, **kwargs):
        return self._record("askyesno", *args)

    def askokcancel(self, *args, **kwargs):
        return self._record("askokcancel", *args)


def _call_is_safe(node):
    func = node.func
    if isinstance(func, ast.Name):
        return func.id in SAFE_CALL_NAMES
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        return (func.value.id, func.attr) in SAFE_CALL_ATTRIBUTES
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Constant):
        return func.attr in SAFE_TEXT_METHODS
    return False


def _value_is_safe(node):
    """True when evaluating this expression cannot do anything but make data."""
    for child in ast.walk(node):
        if isinstance(child, ast.Call) and not _call_is_safe(child):
            return False
        if isinstance(child, (ast.Lambda, ast.Await, ast.Yield, ast.YieldFrom, ast.NamedExpr)):
            return False
    return True


def _is_variable_declaration(node):
    if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
        return False
    func = node.value.func
    return (
        isinstance(func, ast.Attribute)
        and isinstance(func.value, ast.Name)
        and func.value.id == "tk"
        and func.attr in VARIABLE_CLASS_NAMES
    )


def _run(node, namespace, module_name):
    module = ast.Module(body=[node], type_ignores=[])
    ast.fix_missing_locations(module)
    code = compile(module, module_name, "exec")
    exec(code, namespace)  # nosec B102


def load_app_namespace():
    """Returns a dict holding the app's functions, classes, and plain settings."""
    with open(APP_PATH, "r", encoding="utf-8") as app_file:
        source = app_file.read()
    tree = ast.parse(source, filename="Amanuensis.py")

    fake_box = FakeMessageBox()
    namespace = {
        "__name__": "amanuensis_under_test",
        "re": re, "math": math, "random": random, "time": time, "json": json,
        "os": os, "difflib": difflib, "hashlib": hashlib, "html": html,
        "zipfile": zipfile, "zlib": zlib, "threading": threading, "queue": queue,
        "collections": collections, "datetime": datetime, "timedelta": timedelta,
        "tk": FakeTkModule,
        "messagebox": fake_box,
        "HAS_PYGETWINDOW": False,
        "HAS_WINSOUND": False,
        "startup_theme": "dark",
        "startup_text_size": 100,
        "TEXT_SCALE": 1.0,
    }

    skipped = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            wanted = True
        elif isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
            wanted = _is_variable_declaration(node) or _value_is_safe(node.value)
        else:
            wanted = False
        if not wanted:
            continue
        try:
            _run(node, namespace, "Amanuensis.py")
        except Exception as problem:
            name = getattr(node, "name", None) or ast.dump(node)[:40]
            skipped.append((name, type(problem).__name__))
    namespace["__skipped__"] = skipped
    namespace["__messagebox__"] = fake_box
    return namespace


_CACHE = {}


def get_app():
    """Loads the app pieces once and shares them between all test files."""
    if "app" not in _CACHE:
        _CACHE["app"] = load_app_namespace()
    return _CACHE["app"]
