"""Invocation recorder loaded by the historical pytest interpreter."""

import inspect
import json
import os
import sys

import pytest


class Recorder:
    def __init__(self, root, destination, limit):
        self.root = os.path.normcase(os.path.realpath(root)) + os.sep
        self.destination = destination
        self.limit = limit
        self.count = 0
        self.active = {}
        self.files = {}
        self.truncated = False
        self.fixtures = set()

    def profile(self, frame, event, arg):
        if event == "return":
            self.active.pop(id(frame), None)
        if event != "call":
            return
        code = frame.f_code
        filename = code.co_filename
        if filename not in self.files:
            path = os.path.normcase(os.path.realpath(filename))
            self.files[filename] = path[len(self.root):].replace(os.sep, "/") if (
                not filename.startswith("<") and path.startswith(self.root)
            ) else None
        path = self.files[filename]
        if path is None:
            return
        if self.count >= self.limit:
            self.truncated = True
            return
        caller = frame.f_back
        ancestor = caller
        while ancestor is not None and id(ancestor) not in self.active:
            ancestor = ancestor.f_back
        self.count += 1
        self.active[id(frame)] = self.count
        record = dict(
            id=self.count, parent=self.active[id(ancestor)] if ancestor else None,
            direct_parent=ancestor is not None and ancestor is caller,
            file=path, function=getattr(code, "co_qualname", code.co_name),
            line=code.co_firstlineno, caller_line=ancestor.f_lineno if ancestor else None,
            phase=self.phase, selector=self.selector, test_entry=code is self.entry,
            fixture_entry=code in self.fixtures,
        )
        self.stream.write(json.dumps(record) + "\n")

    def trace(self, item, phase):
        self.phase = phase
        self.selector = item.nodeid
        obj = getattr(item, "obj", None)
        self.entry = getattr(inspect.unwrap(obj), "__code__", None)
        previous = sys.getprofile()
        self.active.clear()
        with open(self.destination, "a", encoding="utf-8") as self.stream:
            sys.setprofile(self.profile)
            try:
                yield
            finally:
                sys.setprofile(previous)

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_setup(self, item):
        yield from self.trace(item, "setup")

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_call(self, item):
        yield from self.trace(item, "call")

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_teardown(self, item):
        yield from self.trace(item, "teardown")

    @pytest.hookimpl(hookwrapper=True)
    def pytest_fixture_setup(self, fixturedef, request):
        code = getattr(inspect.unwrap(fixturedef.func), "__code__", None)
        if code is None or code in self.fixtures:
            yield
            return
        self.fixtures.add(code)
        try:
            yield
        finally:
            self.fixtures.remove(code)

    def pytest_sessionfinish(self, session):
        with open(self.destination + ".meta", "w", encoding="utf-8") as handle:
            json.dump(dict(calls=self.count, truncated=self.truncated,
                           scope="current thread and process; Python calls"), handle)
