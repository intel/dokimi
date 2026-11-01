# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The code-index reader on a real index: ``fixtures/code_index/sample.scip``.

The fixture is ``sample_pkg/`` (three Python files) indexed with scip-python 0.6.8 from an empty virtual
environment: ``node index.js index . --project-name sample_pkg --project-version 0.1.0 --output sample.scip``,
run in ``tests/fixtures/code_index/``, so its document paths are relative to that directory. Lines in the
assertions are 0-based, as SCIP writes them. ``scip print --json`` gives the same 33 occurrences.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dokimi.repos.code_index import load
from dokimi.repos.code_index.symbols import descriptor_name

PROJECT = Path(__file__).resolve().parents[1]
FIXTURE = PROJECT / "tests" / "fixtures" / "code_index" / "sample.scip"
ROOT = "tests/fixtures/code_index/"
BASE, CORE, UTIL = (f"{ROOT}sample_pkg/{name}.py" for name in ("base", "core", "util"))


@pytest.fixture(scope="module")
def index():
    return load(FIXTURE, root_prefix=ROOT)


def test_files_are_repo_relative_and_exist_in_the_tree(index):
    assert index.files() == (BASE, CORE, UTIL)
    assert all((PROJECT / path).is_file() for path in index.files())


def test_root_prefix_is_optional_and_a_trailing_slash_is_not_doubled():
    bare = load(FIXTURE)
    assert bare.files() == ("sample_pkg/base.py", "sample_pkg/core.py", "sample_pkg/util.py")
    assert load(FIXTURE, root_prefix=ROOT.rstrip("/")).files() == (BASE, CORE, UTIL)


def test_tool_and_document_occurrences_match_scip_print(index):
    assert index.tool == "scip-python 0.6.8"
    assert sum(len(index.occurrences(path)) for path in index.files()) == 33
    assert all(o.path == UTIL for o in index.occurrences(UTIL))


def test_name_lookup_returns_every_definition_of_that_name(index):
    runs = index.definitions("run")
    assert sorted((o.path, o.line) for o in runs) == [(BASE, 9), (CORE, 11)]
    assert all(o.is_definition for o in runs)


def test_a_parameter_defines_its_own_name(index):
    # `value` is a parameter of four functions: each is a definition, named by the parameter
    values = index.definitions("value")
    assert sorted((o.path, o.line) for o in values) == [(BASE, 9), (CORE, 11), (CORE, 16), (UTIL, 8)]


def test_a_full_symbol_string_is_looked_up_exactly(index):
    impl = index.definitions("Impl")
    assert len(impl) == 1
    assert index.definitions(impl[0].symbol) == impl
    assert index.definitions("Missing") == ()


def test_references_are_the_non_definition_occurrences(index):
    helper = index.definitions("helper")[0].symbol
    refs = index.references(helper)
    assert sorted((o.path, o.line) for o in refs) == [(CORE, 12), (UTIL, 5), (UTIL, 10)]
    assert not any(o.is_definition for o in refs)


def test_bases_follow_is_implementation_relationships(index):
    impl = index.definitions("Impl")[0].symbol
    base = index.definitions("Base")[0].symbol
    assert index.bases(impl) == (base,)
    assert index.bases(base) == ()


def test_enclosing_is_the_innermost_definition_whose_range_holds_the_line(index):
    assert index.has_enclosing_ranges
    body = index.enclosing(CORE, 12)
    assert body is not None and descriptor_name(body.symbol) == "run"
    assert index.enclosing(CORE, 16) is not None
    assert descriptor_name(index.enclosing(CORE, 16).symbol) == "helper"
    assert descriptor_name(index.enclosing(CORE, 9).symbol) == "Impl"
    assert index.enclosing(CORE, 0) is None
    assert index.enclosing("sample_pkg/nope.py", 12) is None


def test_local_ids_are_scoped_to_their_document(index):
    # `local 0` is the `total` of core.py and the `obj` of util.py: two symbols that share one id
    core_locals = [o for o in index.occurrences(CORE) if o.symbol == "local 0"]
    util_locals = [o for o in index.occurrences(UTIL) if o.symbol == "local 0"]
    assert [o.line for o in core_locals] == [12, 13]
    assert [o.line for o in util_locals] == [9, 10]
    assert core_locals[0].is_definition and util_locals[0].is_definition
    with pytest.raises(ValueError, match="local id"):
        index.references("local 0")
    with pytest.raises(ValueError, match="local id"):
        index.definitions("local 0")
    with pytest.raises(ValueError, match="local id"):
        index.bases("local 0")
