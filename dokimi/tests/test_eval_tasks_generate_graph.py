# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""``eval tasks generate --from graph`` and its grader ``dokimi:graph_enum`` (contract §4.5, §4.7). No docker, no LLM."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from dokimi.cli import build_parser
from dokimi.repos import graph_tasks
from dokimi.repos.code_index import CodeIndex
from dokimi.repos.code_index.scip import DEFINITION, Decoded, Occurrence
from dokimi.repos.code_index.symbols import descriptor_name, descriptor_names
from dokimi.repos.graders import HOST_DIR, NAMES, graph, main
from dokimi.repos.graph_tasks import Options, plan, task_id, write
from repo_eval_fixture import git, write as write_file
from tests.test_repo_code_index_decode import document, index as scip_index, occurrence, symbol_info


def _fn(name: str, pkg: str = "p", mod: str = "m") -> str:
    return f"scip-python python {pkg} 0.1 `{pkg}.{mod}`/{name}()."


def _cls(name: str, pkg: str = "p", mod: str = "m") -> str:
    return f"scip-python python {pkg} 0.1 `{pkg}.{mod}`/{name}#"


def _occ(path: str, line: int, symbol: str, definition: bool = False,
         enclosing: tuple[int, int] | None = None) -> Occurrence:
    span = None if enclosing is None else (enclosing[0], 0, enclosing[1], 1)
    return Occurrence(path, line, 0, line, 1, symbol, DEFINITION if definition else 0, enclosing, span)


def _index(docs: dict[str, list[Occurrence]], bases: dict[str, tuple[str, ...]] | None = None,
           tool: str = "scip-python 0.6.8") -> CodeIndex:
    return CodeIndex(Decoded(tool, docs, bases or {}))


def _opts(tmp_path: Path, **kw) -> Options:
    return Options(out=tmp_path / "tasks", base="a" * 40, **{"min_gold": 2, **kw})


RUN, ALT_RUN, PARSE = _fn("run"), _fn("run", mod="alt"), _fn("parse")


def _callers_docs() -> dict[str, list[Occurrence]]:
    """run is defined in src/core.py and referenced from two code files and a test; alt's run shares its name."""
    return {
        "src/core.py": [_occ("src/core.py", 0, RUN, True), _occ("src/core.py", 5, RUN)],
        "src/cli.py": [_occ("src/cli.py", 3, RUN)],
        "src/api.py": [_occ("src/api.py", 1, RUN), _occ("src/api.py", 2, RUN)],
        "tests/test_core.py": [_occ("tests/test_core.py", 4, RUN)],
        "src/alt.py": [_occ("src/alt.py", 0, ALT_RUN, True)],
    }


TREE = ["src/core.py", "src/cli.py", "src/api.py", "src/alt.py", "tests/test_core.py", "README.md"]


# ------------------------------------------------------------------ the generator

def test_file_gold_is_the_referencing_files_outside_the_definition_tests_included(tmp_path):
    p = plan(_index(_callers_docs()), TREE, _opts(tmp_path))
    assert p.granularity == "files" and p.root_prefix == "" and p.documents_in_tree == 5
    (c,) = [o.candidate for o in p.outcomes]
    assert (c.kind, c.name, c.qualified) == ("references", "run", "p.m.run")
    assert c.defined_in == ("src/core.py",)
    assert c.gold == ("src/api.py", "src/cli.py", "tests/test_core.py")
    assert c.same_name == 2 and c.ambiguous


def test_a_reference_outside_the_tree_drops_the_target(tmp_path):
    docs = _callers_docs()
    docs["build/gen.py"] = [_occ("build/gen.py", 0, RUN)]  # indexed, but not a file of this revision
    assert plan(_index(docs), TREE, _opts(tmp_path)).outcomes == []


@pytest.mark.parametrize("defined_in", ["tests/test_core.py", "vendor/lib.py"])
def test_a_function_a_test_or_generated_file_defines_is_not_a_target(tmp_path, defined_in):
    docs = _callers_docs()
    docs["src/core.py"] = [_occ("src/core.py", 5, RUN)]
    docs[defined_in] = [*docs.get(defined_in, []), _occ(defined_in, 0, RUN, True)]
    assert plan(_index(docs), [*TREE, defined_in], _opts(tmp_path)).outcomes == []


def test_a_protocol_method_is_not_a_target(tmp_path):
    init = _fn("__init__")
    docs = {"src/a.py": [_occ("src/a.py", 0, init, True)],
            **{f"src/u{i}.py": [_occ(f"src/u{i}.py", 0, init)] for i in range(3)}}
    assert plan(_index(docs), list(docs), _opts(tmp_path)).candidates["references"] == 0


def test_the_gold_size_gate_holds_both_ends(tmp_path):
    assert plan(_index(_callers_docs()), TREE, _opts(tmp_path, min_gold=4)).outcomes == []
    assert plan(_index(_callers_docs()), TREE, _opts(tmp_path, max_gold=2)).outcomes == []
    assert len(plan(_index(_callers_docs()), TREE, _opts(tmp_path, min_gold=3, max_gold=3)).outcomes) == 1


def test_an_index_relative_to_a_subdirectory_maps_onto_tree_paths(tmp_path):
    tree = [f"pkg/{f}" for f in TREE]
    p = plan(_index(_callers_docs()), tree, _opts(tmp_path))
    assert p.root_prefix == "pkg/"
    assert p.outcomes[0].candidate.gold == ("pkg/src/api.py", "pkg/src/cli.py", "pkg/tests/test_core.py")


def test_function_gold_is_the_innermost_enclosing_function_and_skips_module_level_code(tmp_path):
    outer, inner, helper = _fn("outer", mod="cli"), _fn("inner", mod="cli"), _fn("helper", mod="api")
    docs = {
        "src/core.py": [_occ("src/core.py", 0, RUN, True, (0, 3))],
        "src/cli.py": [_occ("src/cli.py", 0, outer, True, (0, 9)), _occ("src/cli.py", 2, inner, True, (2, 5)),
                       _occ("src/cli.py", 3, RUN), _occ("src/cli.py", 7, RUN)],
        "src/api.py": [_occ("src/api.py", 0, RUN), _occ("src/api.py", 2, helper, True, (2, 4)),
                       _occ("src/api.py", 3, RUN)],
    }
    p = plan(_index(docs), list(docs), _opts(tmp_path))
    assert p.granularity == "functions"
    (c,) = [o.candidate for o in p.outcomes if o.candidate.name == "run"]
    assert c.gold == ("src/api.py::helper", "src/cli.py::inner", "src/cli.py::outer")


@pytest.mark.parametrize("outer_range", [(0, 4), None], ids=["ranged-function", "class-range-only"])
def test_a_function_without_a_range_keeps_the_gold_at_file_level(tmp_path, outer_range):
    # a reference inside a function with no range is no function's gold, so the gold would silently drop it
    outer, bare, klass = _fn("outer", mod="cli"), _fn("bare", mod="cli"), _cls("Box", mod="cli")
    docs = {
        "src/core.py": [_occ("src/core.py", 0, RUN, True, (0, 3))],
        "src/cli.py": [_occ("src/cli.py", 0, outer, True, outer_range), _occ("src/cli.py", 2, RUN),
                       _occ("src/cli.py", 6, bare, True), _occ("src/cli.py", 7, RUN),
                       _occ("src/cli.py", 9, klass, True, (9, 12))],
        "src/api.py": [_occ("src/api.py", 1, RUN)],
    }
    p = plan(_index(docs), list(docs), _opts(tmp_path))
    assert p.granularity == "files"
    (c,) = [o.candidate for o in p.outcomes if o.candidate.name == "run"]
    assert c.gold == ("src/api.py", "src/cli.py")


def test_derives_gold_is_the_direct_subclasses(tmp_path):
    base, a, b, grand = _cls("Base"), _cls("A", mod="a"), _cls("B", mod="b"), _cls("G", mod="g")
    docs = {"src/base.py": [_occ("src/base.py", 0, base, True)], "src/a.py": [_occ("src/a.py", 0, a, True)],
            "src/b.py": [_occ("src/b.py", 0, b, True)], "src/g.py": [_occ("src/g.py", 0, grand, True)]}
    bases = {a: (base,), b: (base,), grand: (a,)}
    p = plan(_index(docs, bases), list(docs), _opts(tmp_path, references=0))
    (c,) = [o.candidate for o in p.outcomes]
    assert (c.kind, c.name, c.gold) == ("derives", "Base", ("src/a.py::A", "src/b.py::B"))
    # a subclass a test defines cannot be graded against the tree's code: the base is no target
    docs["tests/test_x.py"] = [_occ("tests/test_x.py", 0, _cls("T", mod="t"), True)]
    bases[_cls("T", mod="t")] = (base,)
    assert plan(_index(docs, bases), list(docs), _opts(tmp_path, references=0)).outcomes == []


def _pool(n_ambiguous: int, n_unique: int) -> list[graph_tasks.Candidate]:
    def cand(name: str, same: int) -> graph_tasks.Candidate:
        return graph_tasks.Candidate("references", (_fn(name),), name, name, ("a.py",), ("b.py",), (), same)
    return [cand(f"amb{i}", 2) for i in range(n_ambiguous)] + [cand(f"uni{i}", 1) for i in range(n_unique)]


def test_choice_is_seeded_takes_half_ambiguous_and_fills_from_either_pool():
    import random

    first = graph_tasks._choose(_pool(5, 5), 4, random.Random(7), set())
    assert [c.name for c in first] == [c.name for c in graph_tasks._choose(_pool(5, 5), 4, random.Random(7), set())]
    assert sum(c.ambiguous for c in first) == 2
    assert len(graph_tasks._choose(_pool(5, 1), 4, random.Random(7), set())) == 4  # unique ran out
    used = {task_id(c) for c in _pool(5, 0)}  # an id already taken (by the other kind) is not chosen twice
    assert graph_tasks._choose(_pool(5, 0), 4, random.Random(7), used) == []


def test_write_keeps_an_existing_task_unless_forced(tmp_path):
    opts = _opts(tmp_path)
    p = plan(_index(_callers_docs()), TREE, opts)
    write(p, opts, {"tool": "scip-python 0.6.8", "sha256": "0" * 64})
    tid = p.outcomes[0].task
    task = json.loads((opts.out / tid / "task.json").read_text())
    gold = json.loads((opts.out / tid / "gold.json").read_text())
    assert task["grader"] == "dokimi:graph_enum" and task["base"] == "a" * 40 and task["id"] == tid
    assert task["score"] == {"metric": "f1", "max": 1.0} and task["answer_key"] == "files"
    assert task["symbols"] == [RUN] and task["family"] == "navigate" and tid.startswith("graph_references_run_")
    assert "2 symbols named `run`" in task["prompt"] and '{"files": [' in task["prompt"]
    assert gold["gold"] == ["src/api.py", "src/cli.py", "tests/test_core.py"] and gold["exclude"] == ["src/core.py"]
    (opts.out / tid / "task.json").write_text("{}")
    assert plan(_index(_callers_docs()), TREE, opts).outcomes[0].status == "kept"
    forced = Options(out=opts.out, base=opts.base, min_gold=2, force=True)
    assert plan(_index(_callers_docs()), TREE, forced).outcomes[0].status == "written"


def test_cxx_overloads_are_one_target_named_with_the_index_separator(tmp_path):
    foo1, foo2 = "cxx . . $ ns/foo(001).", "cxx . . $ ns/foo(002)."
    docs = {"src/foo.cpp": [_occ("src/foo.cpp", 0, foo1, True), _occ("src/foo.cpp", 5, foo2, True)],
            "src/a.cpp": [_occ("src/a.cpp", 0, foo1)], "src/b.cpp": [_occ("src/b.cpp", 0, foo2)]}
    p = plan(_index(docs, tool="scip-clang 0.4.0"), list(docs), _opts(tmp_path))
    (c,) = [o.candidate for o in p.outcomes]
    assert c.symbols == (foo1, foo2) and c.qualified == "ns::foo" and c.gold == ("src/a.cpp", "src/b.cpp")
    write(p, _opts(tmp_path), {"tool": "scip-clang 0.4.0", "sha256": "0" * 64})
    assert "any of its 2 overloads" in json.loads((tmp_path / "tasks" / p.outcomes[0].task / "task.json")
                                                    .read_text())["prompt"]


def test_two_methods_of_one_name_in_a_file_are_two_gold_items(tmp_path):
    a_run, b_run = _fn("A#run", mod="cli"), _fn("B#run", mod="cli")
    docs = {"src/core.py": [_occ("src/core.py", 0, RUN, True, (0, 3))],
            "src/cli.py": [_occ("src/cli.py", 0, a_run, True, (0, 4)), _occ("src/cli.py", 2, RUN),
                           _occ("src/cli.py", 6, b_run, True, (6, 9)), _occ("src/cli.py", 8, RUN)]}
    p = plan(_index(docs), list(docs), _opts(tmp_path))
    (c,) = [o.candidate for o in p.outcomes if o.candidate.name == "run" and o.candidate.symbols == (RUN,)]
    assert c.gold == ("src/cli.py::A.run", "src/cli.py::B.run")


def test_same_named_subclasses_in_two_namespaces_of_one_file_are_two_gold_items(tmp_path):
    base, ch1, ch2 = "cxx . . $ Base#", "cxx . . $ ns1/Child#", "cxx . . $ ns2/Child#"
    docs = {"src/base.h": [_occ("src/base.h", 0, base, True)],
            "src/children.h": [_occ("src/children.h", 0, ch1, True), _occ("src/children.h", 5, ch2, True)]}
    p = plan(_index(docs, {ch1: (base,), ch2: (base,)}, tool="scip-clang 0.4.0"), list(docs),
             _opts(tmp_path, references=0))
    (cand,) = [o.candidate for o in p.outcomes if o.candidate.name == "Base"]
    assert cand.gold == ("src/children.h::ns1.Child", "src/children.h::ns2.Child")
    assert "namespace or class" in graph_tasks._prompt(cand, "classes")  # the answer must say which one


def test_same_named_callers_in_two_namespaces_of_one_file_are_two_gold_items(tmp_path):
    c1, c2, target = "cxx . . $ ns1/caller().", "cxx . . $ ns2/caller().", "cxx . . $ run()."
    docs = {"src/core.cpp": [_occ("src/core.cpp", 0, target, True, (0, 1))],
            "src/x.cpp": [_occ("src/x.cpp", 0, c1, True, (0, 3)), _occ("src/x.cpp", 2, target),
                          _occ("src/x.cpp", 4, c2, True, (4, 7)), _occ("src/x.cpp", 5, target)]}
    p = plan(_index(docs, tool="scip-clang 0.4.0"), list(docs), _opts(tmp_path))
    (cand,) = [o.candidate for o in p.outcomes if o.candidate.name == "run"]
    assert cand.gold == ("src/x.cpp::ns1.caller", "src/x.cpp::ns2.caller")


def test_a_reference_in_a_class_body_inside_a_function_is_no_function_gold(tmp_path):
    factory, local = _fn("factory", mod="api"), _cls("Local", mod="api")
    docs = {"src/core.py": [_occ("src/core.py", 0, RUN, True, (0, 3))],
            "src/api.py": [_occ("src/api.py", 0, factory, True, (0, 5)), _occ("src/api.py", 1, local, True, (1, 4)),
                           _occ("src/api.py", 2, RUN)],
            "src/cli.py": [_occ("src/cli.py", 0, _fn("g1", mod="cli"), True, (0, 2)), _occ("src/cli.py", 1, RUN)],
            "src/web.py": [_occ("src/web.py", 0, _fn("g2", mod="web"), True, (0, 2)), _occ("src/web.py", 1, RUN)]}
    p = plan(_index(docs), list(docs), _opts(tmp_path))
    (c,) = [o.candidate for o in p.outcomes if o.candidate.name == "run"]
    assert c.gold == ("src/cli.py::g1", "src/web.py::g2")


def test_two_functions_on_one_line_each_own_the_reference_inside_its_range(tmp_path):
    a, b = _fn("a", mod="one"), _fn("b", mod="one")
    docs = {"src/core.py": [_occ("src/core.py", 0, RUN, True, (0, 3))],
            "src/one.py": [Occurrence("src/one.py", 0, 0, 0, 1, a, DEFINITION, (0, 0), (0, 0, 0, 10)),
                           Occurrence("src/one.py", 0, 11, 0, 12, b, DEFINITION, (0, 0), (0, 11, 0, 30)),
                           Occurrence("src/one.py", 0, 20, 0, 21, RUN, 0, None, None)]}
    p = plan(_index(docs), list(docs), _opts(tmp_path, min_gold=1))
    (c,) = [o.candidate for o in p.outcomes if o.candidate.name == "run"]
    assert c.gold == ("src/one.py::b",)


@pytest.mark.parametrize("col, owned", [(0, True), (9, True), (10, False)],
                         ids=["first-column", "last-column", "end-column"])
def test_a_span_holds_its_first_and_last_column_and_not_its_end_column(tmp_path, col, owned):
    a, cli_g = _fn("a", mod="one"), _fn("g", mod="cli")
    docs = {"src/core.py": [_occ("src/core.py", 0, RUN, True, (0, 3))],
            "src/one.py": [Occurrence("src/one.py", 0, 0, 0, 1, a, DEFINITION, (0, 0), (0, 0, 0, 10)),
                           Occurrence("src/one.py", 0, col, 0, col + 1, RUN, 0, None, None)],
            "src/cli.py": [_occ("src/cli.py", 0, cli_g, True, (0, 2)), _occ("src/cli.py", 1, RUN)]}
    p = plan(_index(docs), list(docs), _opts(tmp_path, min_gold=1))
    (c,) = [o.candidate for o in p.outcomes if RUN in o.candidate.symbols]
    assert c.gold == tuple(sorted(("src/cli.py::g", *(("src/one.py::a",) if owned else ()))))


@pytest.mark.parametrize("line, owned", [(3, False), (4, True)], ids=["class-last-line", "after-the-class"])
def test_a_class_range_without_a_span_ends_after_its_last_line(tmp_path, line, owned):
    # factory is ranged over lines 0..9; class Box has a range but no span, so it runs lines 1..3 (end excluded)
    box = _cls("Box", mod="api")
    docs = {"src/core.py": [_occ("src/core.py", 0, RUN, True, (0, 3))],
            "src/api.py": [_occ("src/api.py", 0, _fn("factory", mod="api"), True, (0, 9)),
                           Occurrence("src/api.py", 1, 0, 1, 1, box, DEFINITION, (1, 3), None),
                           Occurrence("src/api.py", line, 0, line, 1, RUN, 0, None, None)],
            "src/cli.py": [_occ("src/cli.py", 0, _fn("g", mod="cli"), True, (0, 2)), _occ("src/cli.py", 1, RUN)]}
    p = plan(_index(docs), list(docs), _opts(tmp_path, min_gold=1))
    (c,) = [o.candidate for o in p.outcomes if RUN in o.candidate.symbols]
    assert c.gold == tuple(sorted(("src/cli.py::g", *(("src/api.py::factory",) if owned else ()))))


def test_a_function_a_test_file_also_defines_is_a_target_with_both_files(tmp_path):
    docs = _callers_docs()
    docs["tests/test_core.py"] = [*docs["tests/test_core.py"], _occ("tests/test_core.py", 0, RUN, True)]
    p = plan(_index(docs), TREE, _opts(tmp_path, min_gold=1))
    (c,) = [o.candidate for o in p.outcomes if RUN in o.candidate.symbols]
    assert c.defined_in == ("src/core.py", "tests/test_core.py")


def test_a_function_defined_outside_the_tree_is_not_a_target(tmp_path):
    docs = _callers_docs()
    docs["build/gen.py"] = [_occ("build/gen.py", 0, RUN, True)]  # indexed, but not a file of this revision
    p = plan(_index(docs), TREE, _opts(tmp_path, min_gold=1))
    assert not [o for o in p.outcomes if RUN in o.candidate.symbols]


def test_task_ids_of_two_targets_with_one_name_differ():
    one = graph_tasks.Candidate("references", (RUN,), "run", "p.m.run", ("a.py",), ("b.py",), (), 2)
    other = graph_tasks.Candidate("references", (ALT_RUN,), "run", "p.alt.run", ("a.py",), ("b.py",), (), 2)
    assert task_id(one) != task_id(other) and task_id(one).startswith("graph_references_run_")
    long = graph_tasks.Candidate("references", (RUN,), "x" * 200, "x", ("a.py",), ("b.py",), (), 1)
    assert len(task_id(long)) == graph_tasks.ID_MAX


def test_derives_accepts_a_second_defining_file_and_a_class_a_reduction_dropped(tmp_path):
    base, a, b, c = _cls("Base"), _cls("A", mod="a"), _cls("B", mod="b"), _cls("C", mod="c")
    docs = {"src/base.py": [_occ("src/base.py", 0, base, True)],
            "src/a.py": [_occ("src/a.py", 0, a, True)], "src/a.pyi": [_occ("src/a.pyi", 0, a, True)],
            "src/b.py": [_occ("src/b.py", 0, b, True)], "src/c.py": [_occ("src/c.py", 0, c, True)]}
    # C lists Base and B, and B lists Base: on an index that lists ancestors the minimal bases of C are (B,)
    bases = {a: (base,), b: (base,), c: (b, base)}
    p = plan(_index(docs, bases, tool="scip-clang 0.4.0"), list(docs), _opts(tmp_path, references=0))
    (cand,) = [o.candidate for o in p.outcomes if o.candidate.name == "Base"]
    assert cand.gold == ("src/a.py::A", "src/b.py::B")
    assert cand.accepted == ("src/a.pyi::A", "src/c.py::C")


def test_derives_accepts_a_listed_class_in_a_test_file(tmp_path):
    base, a, b, c = _cls("Base"), _cls("A", mod="a"), _cls("B", mod="b"), _cls("C", mod="test_c")
    docs = {"src/base.py": [_occ("src/base.py", 0, base, True)], "src/a.py": [_occ("src/a.py", 0, a, True)],
            "src/b.py": [_occ("src/b.py", 0, b, True)], "tests/test_c.py": [_occ("tests/test_c.py", 0, c, True)]}
    bases = {a: (base,), b: (base,), c: (b, base)}
    p = plan(_index(docs, bases, tool="scip-clang 0.4.0"), list(docs), _opts(tmp_path, references=0))
    (cand,) = [o.candidate for o in p.outcomes if o.candidate.name == "Base"]
    assert cand.gold == ("src/a.py::A", "src/b.py::B") and cand.accepted == ("tests/test_c.py::C",)


def test_derives_respects_the_gold_size_cap(tmp_path):
    base = _cls("Base")
    subs = {_cls(f"S{i}", mod=f"s{i}"): f"src/s{i}.py" for i in range(4)}
    docs = {"src/base.py": [_occ("src/base.py", 0, base, True)],
            **{path: [_occ(path, 0, sym, True)] for sym, path in subs.items()}}
    bases = {sym: (base,) for sym in subs}
    assert plan(_index(docs, bases), list(docs), _opts(tmp_path, references=0, max_gold=3)).outcomes == []
    assert len(plan(_index(docs, bases), list(docs), _opts(tmp_path, references=0, max_gold=4)).outcomes) == 1


def test_in_file_name_drops_the_leading_namespaces_only():
    assert graph_tasks.in_file_name("cxx . . $ Impl#run().") == "Impl.run"
    assert graph_tasks.in_file_name("cxx . . $ ns/inner/Impl#run(1a).") == "Impl.run"
    assert graph_tasks.in_file_name("cxx . . $ ns/foo(001).") == "foo"
    assert graph_tasks.in_file_name(_fn("A#run", mod="cli")) == "A.run"
    assert graph_tasks.in_file_name(_fn("run")) == "run"


def test_descriptor_names_are_outermost_first():
    assert descriptor_names(_fn("run")) == ("p.m", "run")
    assert descriptor_names("scip-python python p 0.1 `p.m`/Impl#run().(value)") == ("p.m", "Impl", "run", "value")
    assert descriptor_name("scip-python python p 0.1 `p.m`/Impl#run().(value)") == "value"
    assert descriptor_names("local 3") is None and descriptor_names("") is None


def test_symbols_are_the_globals_with_a_definition():
    idx = _index(_callers_docs())
    assert idx.symbols() == tuple(sorted((RUN, ALT_RUN)))


# ------------------------------------------------------------------ the grader

GOLD = {"answer_key": "files", "gold": ["src/a.py", "src/b.py", "src/c.py", "src/d.py"],
        "exclude": ["src/def.py"], "accepted": ["src/maybe.py"]}


def _grade(tmp_path: Path, answer: str, gold: dict | None = None) -> dict:
    (tmp_path / "gold.json").write_text(json.dumps(gold or GOLD))
    return graph.grade({"id": "t", "gold": "gold.json"}, tmp_path, answer)


def fence(doc) -> str:
    return "Done.\n```json\n" + json.dumps(doc) + "\n```\n"


def test_oracle_scores_one_and_the_null_answer_fails(tmp_path):
    (tmp_path / "gold.json").write_text(json.dumps(GOLD))
    assert _grade(tmp_path, graph.oracle({}, tmp_path))["f1"] == 1.0
    empty = _grade(tmp_path, "")
    assert empty["pass"] is False and empty["parse_error"] and empty["f1"] == 0.0


def test_f1_drops_the_defining_file_and_does_not_count_an_accepted_extra(tmp_path):
    g = _grade(tmp_path, fence({"files": ["./src/a.py", "/eval/run/workspace/src/b.py", "src/def.py",
                                          "src/maybe.py", "src/wrong.py"]}))
    assert (g["recall"], g["precision"], g["n_false_positive"]) == (0.5, 2 / 3, 1)
    assert g["f1"] == pytest.approx(2 * 0.5 * (2 / 3) / (0.5 + 2 / 3))
    assert g["pass"] is None and g["missed"] == ["src/c.py", "src/d.py"]
    assert g["false_positive"] == ["src/wrong.py"] and g["accepted_extra"] == ["src/maybe.py"]


def test_an_answer_without_the_key_is_a_parse_error_and_the_last_fence_counts(tmp_path):
    assert _grade(tmp_path, fence({"functions": ["src/a.py::f"]}))["parse_error"]
    two = fence({"files": ["src/x.py"]}) + fence({"files": GOLD["gold"]})
    assert _grade(tmp_path, two)["f1"] == 1.0


def test_function_items_keep_their_name_and_drop_a_defining_file(tmp_path):
    gold = {"answer_key": "functions", "gold": ["src/a.py::f", "src/b.py::g"], "exclude": ["src/def.py"]}
    g = _grade(tmp_path, fence({"functions": ["./src/a.py:: f", "src/def.py::h", "src/b.py::G"]}), gold)
    assert (g["recall"], g["n_false_positive"]) == (0.5, 1)


def test_a_gold_with_an_unknown_answer_key_is_a_grader_error(tmp_path):
    with pytest.raises(ValueError, match="answer_key"):
        _grade(tmp_path, fence({"x": []}), {"answer_key": "x", "gold": []})


def test_a_method_answered_qualified_or_bare_matches_its_gold_item(tmp_path):
    gold = {"answer_key": "functions", "gold": ["src/a.py::A.run", "src/a.py::B.run", "src/b.py::Impl.go"]}
    exact = _grade(tmp_path, fence({"functions": ["src/a.py::A::run()", "src/a.py::B.run", "src/b.py::go"]}), gold)
    assert exact["f1"] == 1.0
    bare = _grade(tmp_path, fence({"functions": ["src/a.py::run"]}), gold)  # two run in a.py: no guess
    assert bare["n_false_positive"] == 1 and bare["recall"] == 0.0


def test_a_method_of_another_class_never_names_the_gold_method(tmp_path):
    gold = {"answer_key": "functions", "gold": ["src/cli.py::A.run", "src/ns.py::ns.Impl"]}
    wrong = _grade(tmp_path, fence({"functions": ["src/cli.py::B.run", "src/cli.py::XA.run"]}), gold)
    assert wrong["recall"] == 0.0 and wrong["n_false_positive"] == 2
    shorter = _grade(tmp_path, fence({"functions": ["src/cli.py::run", "src/ns.py::Impl"]}), gold)
    assert shorter["f1"] == 1.0
    longer = _grade(tmp_path, fence({"functions": ["src/cli.py::pkg.A.run"]}), gold)
    assert longer["n_false_positive"] == 0 and longer["recall"] == 0.5


def test_a_repo_directory_in_a_path_is_kept(tmp_path):
    gold = {"answer_key": "files", "gold": ["tests/fixtures/repo/a.py"], "exclude": ["src/repo/a.py"]}
    assert _grade(tmp_path, fence({"files": gold["gold"]}), gold)["f1"] == 1.0


def test_a_blank_gold_entry_is_a_grader_error_not_an_unanswerable_task(tmp_path):
    with pytest.raises(ValueError, match="gold entries"):
        _grade(tmp_path, fence({"files": []}), {**GOLD, "gold": ["src/a.py", " "]})


@pytest.mark.parametrize("entry", ["src/a.py", "src/a.py::run", "./src/a.py::Impl.run()"])
def test_a_gold_entry_in_an_excluded_file_is_a_grader_error_not_a_zero_oracle(tmp_path, entry):
    # the grader drops every answer in an excluded file, so such a gold entry could never score
    gold = {"answer_key": "files", "gold": [entry], "exclude": ["src/a.py"]}
    with pytest.raises(ValueError, match="exclude"):
        _grade(tmp_path, fence({"files": [entry]}), gold)


@pytest.mark.parametrize("bad", [{"gold": []}, {"exclude": "src/def.py"}, {"accepted": None}, {"gold": [1]}])
def test_a_malformed_gold_is_a_grader_error(tmp_path, bad):
    with pytest.raises(ValueError, match="must be a"):
        _grade(tmp_path, fence({"files": []}), {**GOLD, **bad})


def test_an_excluded_gold_entry_is_refused_whatever_the_spelling_of_the_exclude(tmp_path):
    gold = {"answer_key": "files", "gold": ["src/a.py"], "exclude": ["./src/a.py"]}
    with pytest.raises(ValueError, match="must not lie in an exclude file"):
        _grade(tmp_path, fence({"files": []}), gold)


def test_answer_paths_and_the_exclude_list_are_normalised_before_scoring(tmp_path):
    gold = {"answer_key": "files", "gold": ["src/a.py", "src/b.py"], "accepted": ["src/c.py"],
            "exclude": ["./src/def.py"]}
    g = _grade(tmp_path, fence({"files": ["/src/a.py", "src\\b.py", "src/def.py", "./src/c.py", "src/x.py"]}), gold)
    assert (g["recall"], g["n_answer"], g["n_false_positive"]) == (1.0, 4, 1)
    assert g["false_positive"] == ["src/x.py"]


def test_a_bare_name_of_an_accepted_item_is_neither_a_hit_nor_a_false_positive(tmp_path):
    gold = {"answer_key": "functions", "gold": ["src/a.py::A.run"], "accepted": ["src/b.py::B.go"]}
    g = _grade(tmp_path, fence({"functions": ["src/a.py::A.run", "src/b.py::go"]}), gold)
    assert (g["recall"], g["n_false_positive"]) == (1.0, 0)


def test_graph_enum_is_a_known_builtin_and_runs_as_the_container_runs_it(tmp_path):
    assert "graph_enum" in NAMES and "graph_enum" in main.GRADERS
    task_dir, run_dir = tmp_path / "task", tmp_path / "run"
    task_dir.mkdir()
    run_dir.mkdir()
    (task_dir / "gold.json").write_text(json.dumps(GOLD))
    (run_dir / "task.json").write_text(json.dumps({"id": "t", "grader": "dokimi:graph_enum"}))
    for step in ("oracle", "grade"):  # -I: main.py loads graph.py and review.py by name from its own directory
        subprocess.run([sys.executable, "-I", "-B", str(HOST_DIR / "main.py"), step, "graph_enum", str(task_dir),
                        str(run_dir)], check=True)
    out = json.loads((run_dir / "grade.json").read_text())
    assert out["errors"] == [] and out["metrics"]["f1"] == 1.0 and out["details"]["grader_version"] == "1"


# ------------------------------------------------------------------ the command

def _scip(path: Path) -> None:
    """run in src/core.py, referenced from five other files: one task at the default sizes."""
    run = "scip-python python p 0.1 `p.core`/run()."
    users = [document(f"src/u{i}.py", (occurrence([0, 0, 3], run),)) for i in range(5)]
    path.write_bytes(scip_index(document("src/core.py", (occurrence([0, 4, 7], run, 1),),
                                         symbol_info(run)), *users))


def _toy(tmp_path: Path) -> tuple[Path, str]:
    root = tmp_path / "toy"
    for name in ["core", *(f"u{i}" for i in range(5))]:
        write_file(root, f"src/{name}.py", "x = 1\n")
    git(tmp_path, "init", "-q", str(root))
    git(root, "add", "-A")
    git(root, "commit", "-qm", "code")
    return root, git(root, "rev-parse", "HEAD")


def _run(argv: list[str]) -> None:
    args = build_parser().parse_args(argv)
    args.func(args)


def test_cli_writes_a_task_whose_base_is_the_pinned_revision(tmp_path, capsys):
    root, sha = _toy(tmp_path)
    _scip(tmp_path / "index.scip")
    out = tmp_path / "tasks"
    _run(["eval", "tasks", "generate", f"{root}@HEAD", "--from", "graph", "--index", str(tmp_path / "index.scip"),
          "--out", str(out)])
    printed = capsys.readouterr().out
    assert "6/6 index documents in the tree; files; candidates: 1 references, 0 derives" in printed
    assert "1 task(s) written, 0 kept" in printed
    (task_file,) = out.glob("graph_references_run_*/task.json")
    task = json.loads(task_file.read_text())
    digest = hashlib.sha256((tmp_path / "index.scip").read_bytes()).hexdigest()
    assert task["base"] == sha and task["code_index"] == {"tool": "scip-test 0.1", "sha256": digest}
    _run(["eval", "tasks", "generate", f"{root}@HEAD", "--from", "graph", "--index", str(tmp_path / "index.scip"),
          "--out", str(out), "--dry-run"])
    printed = capsys.readouterr().out
    assert f"kept {task_file.parent.name}\n" in printed and "0 task(s) to write, 1 kept" in printed


@pytest.mark.parametrize("pinned, index, error", [
    (False, True, "pin the revision"),
    (True, False, "needs --index"),
    (True, "garbled", "cannot read the code index"),
    (True, "elsewhere", "no document of the code index"),
    (True, "none asked", "asks for no task"),
])
def test_cli_exits_2_on_a_usage_error(tmp_path, capsys, pinned, index, error):
    root, _ = _toy(tmp_path)
    path = tmp_path / "index.scip"
    if index is True:
        _scip(path)
    elif index == "garbled":
        path.write_bytes(b"\xff\xff\xff")
    elif index == "elsewhere":
        path.write_bytes(scip_index(document("lib/other.py")))
    elif index == "none asked":
        _scip(path)
    argv = ["eval", "tasks", "generate", f"{root}@HEAD" if pinned else str(root), "--from", "graph",
            "--out", str(tmp_path / "tasks")] + (["--index", str(path)] if index else [])
    if index == "none asked":
        argv += ["--references", "0", "--derives", "0"]
    with pytest.raises(SystemExit) as exc:
        _run(argv)
    assert exc.value.code == 2 and error in capsys.readouterr().err


def test_cli_exits_1_when_no_target_gives_a_task(tmp_path, capsys):
    root, _ = _toy(tmp_path)
    (tmp_path / "index.scip").write_bytes(scip_index(document("src/core.py")))
    with pytest.raises(SystemExit) as exc:
        _run(["eval", "tasks", "generate", f"{root}@HEAD", "--from", "graph", "--index",
              str(tmp_path / "index.scip"), "--out", str(tmp_path / "tasks")])
    assert exc.value.code == 1 and "no target of the index" in capsys.readouterr().err


# ------------------------------------------------------------------ review round 2 (PR #399)

def test_the_shared_name_hint_also_counts_accepted_items():
    c = graph_tasks.Candidate("derives", ("cxx . . $ Base#",), "Base", "Base", ("x.h",), ("x.h::ns1.Child",),
                              ("x.h::ns2.Child",), 1)
    assert "namespace or class" in graph_tasks._prompt(c, "classes")


def test_python_function_and_method_with_one_dotted_name_are_two_targets(tmp_path):
    mod_fn, method = "scip-python python p 0.1 `a.b`/c().", "scip-python python p 0.1 `a`/b#c()."
    docs = {"src/ab.py": [_occ("src/ab.py", 0, mod_fn, True)], "src/a.py": [_occ("src/a.py", 0, method, True)],
            **{f"src/u{i}.py": [_occ(f"src/u{i}.py", 0, mod_fn), _occ(f"src/u{i}.py", 1, method)] for i in range(2)}}
    p = plan(_index(docs), list(docs), _opts(tmp_path))
    assert p.candidates["references"] == 2  # one task is chosen: names of the chosen tasks are distinct
    (o,) = p.outcomes
    assert o.candidate.symbols in ((method,), (mod_fn,)) and o.candidate.same_name == 2
    assert "overloads" not in graph_tasks._prompt(o.candidate, "files")


def test_a_same_named_function_of_a_gold_file_is_written_as_distinct(tmp_path):
    a_run, b_run, mod_run = _fn("A#run", mod="cli"), _fn("B#run", mod="cli"), _fn("run", mod="cli")
    docs = {"src/core.py": [_occ("src/core.py", 0, RUN, True, (0, 3))],
            "src/cli.py": [_occ("src/cli.py", 0, a_run, True, (0, 4)), _occ("src/cli.py", 2, RUN),
                           _occ("src/cli.py", 6, b_run, True, (6, 9)), _occ("src/cli.py", 8, RUN),
                           _occ("src/cli.py", 10, mod_run, True, (10, 12))]}
    p = plan(_index(docs), list(docs), _opts(tmp_path))
    (o,) = [o for o in p.outcomes if o.candidate.symbols == (RUN,)]
    assert o.candidate.distinct == ("src/cli.py::run",)
    assert "namespace or class" in graph_tasks._prompt(o.candidate, "functions")
    write(p, _opts(tmp_path), {"tool": "scip-python", "sha256": "0" * 64})
    assert json.loads((tmp_path / "tasks" / o.task / "gold.json").read_text())["distinct"] == ["src/cli.py::run"]


def test_a_bare_name_of_a_distinct_function_is_a_false_positive(tmp_path):
    gold = {"answer_key": "functions", "gold": ["a.py::Impl.run", "b.py::f"], "distinct": ["a.py::run"]}
    r = _grade(tmp_path, fence({"functions": ["a.py::run", "b.py::f"]}), gold)
    assert r["n_false_positive"] == 1 and r["recall"] == 0.5
    r = _grade(tmp_path, fence({"functions": ["a.py::Impl.run", "b.py::f"]}), gold)
    assert r["f1"] == 1.0


def test_an_unreadable_index_exits_2_before_the_source_is_resolved(tmp_path, capsys):
    root, _ = _toy(tmp_path)
    (tmp_path / "index.scip").write_bytes(b"\xff\xff\xff")
    with pytest.raises(SystemExit) as exc:
        _run(["eval", "tasks", "generate", f"{root}@nonexistent", "--from", "graph",
              "--index", str(tmp_path / "index.scip"), "--out", str(tmp_path / "tasks")])
    assert exc.value.code == 2 and "cannot read the code index" in capsys.readouterr().err


def test_an_over_qualified_answer_names_the_longest_known_item(tmp_path):
    gold = {"answer_key": "functions", "gold": ["a.py::Impl.run"], "distinct": ["a.py::run"]}
    assert _grade(tmp_path, fence({"functions": ["a.py::cli.Impl.run"]}), gold)["f1"] == 1.0
    two = {"answer_key": "functions", "gold": ["a.py::A.run", "a.py::B.run"]}
    assert _grade(tmp_path, fence({"functions": ["a.py::run"]}), two)["n_false_positive"] == 1  # still ambiguous
    nested = {"answer_key": "functions", "gold": ["a.py::run"], "distinct": ["a.py::Outer.Impl.run"]}
    assert _grade(tmp_path, fence({"functions": ["a.py::Impl.run"]}), nested)["n_false_positive"] == 1


# ------------------------------------------------------------------ review round 3 (PR #399)

@pytest.mark.parametrize("tool, base, sub, other, second, path", [
    ("scip-python 0.6.8", _cls("Base"), _cls("Outer#Child", mod="kids"), _cls("Child", mod="kids"),
     _cls("Second", mod="kids"), "src/kids.py"),
    ("scip-clang 0.4.0", "cxx . . $ Base#", "cxx . . $ ns1/Child#", "cxx . . $ ns2/Child#", "cxx . . $ Second#",
     "src/kids.h"),
])
def test_a_same_named_class_of_a_gold_file_that_does_not_derive_is_distinct(tmp_path, tool, base, sub, other,
                                                                            second, path):
    docs = {"src/base.py": [_occ("src/base.py", 0, base, True)],
            path: [_occ(path, 0, sub, True), _occ(path, 5, other, True), _occ(path, 9, second, True)]}
    p = plan(_index(docs, {sub: (base,), second: (base,)}, tool=tool), list(docs), _opts(tmp_path, references=0))
    (o,) = p.outcomes
    (wrong,) = o.candidate.distinct
    assert "namespace or class" in graph_tasks._prompt(o.candidate, "classes")
    write(p, _opts(tmp_path), {"tool": tool, "sha256": "0" * 64})
    gold = json.loads((tmp_path / "tasks" / o.task / "gold.json").read_text())
    assert gold["distinct"] == [wrong] and wrong not in gold["gold"]
    r = graph.grade({"id": "t", "gold": "gold.json"}, tmp_path / "tasks" / o.task,
                    fence({"classes": [f"{path}::Child", f"{path}::Second"]}))
    assert r["n_false_positive"] == 1 and r["f1"] < 1.0


def test_a_method_of_a_gold_function_s_name_is_distinct_and_a_false_positive(tmp_path):
    target = _fn("target", mod="core")
    docs = {"src/core.py": [_occ("src/core.py", 0, target, True, (0, 3))]}
    for i in range(3):
        f = f"src/u{i}.py"
        docs[f] = [_occ(f, 0, _fn("run", mod=f"u{i}"), True, (0, 3)), _occ(f, 2, target),
                   _occ(f, 5, _fn("Impl#run", mod=f"u{i}"), True, (5, 8))]
    p = plan(_index(docs), list(docs), _opts(tmp_path))
    (o,) = [o for o in p.outcomes if o.candidate.symbols == (target,)]
    assert o.candidate.distinct == tuple(f"src/u{i}.py::Impl.run" for i in range(3))
    write(p, _opts(tmp_path), {"tool": "scip-python", "sha256": "0" * 64})
    r = graph.grade({"id": "t", "gold": "gold.json"}, tmp_path / "tasks" / o.task,
                    fence({"functions": [f"src/u{i}.py::Impl.run" for i in range(3)]}))
    assert r["f1"] == 0.0 and r["n_false_positive"] == 3
