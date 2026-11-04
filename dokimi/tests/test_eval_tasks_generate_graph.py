# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""``eval tasks generate --from graph`` and its grader ``dokimi:graph_enum`` (contract §4.5, §4.7). No docker, no LLM."""

from __future__ import annotations

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
    assert (c.kind, c.name, c.qualified) == ("callers", "run", "p.m.run")
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
    assert plan(_index(docs), list(docs), _opts(tmp_path)).candidates["callers"] == 0


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


def test_derives_gold_is_the_direct_subclasses(tmp_path):
    base, a, b, grand = _cls("Base"), _cls("A", mod="a"), _cls("B", mod="b"), _cls("G", mod="g")
    docs = {"src/base.py": [_occ("src/base.py", 0, base, True)], "src/a.py": [_occ("src/a.py", 0, a, True)],
            "src/b.py": [_occ("src/b.py", 0, b, True)], "src/g.py": [_occ("src/g.py", 0, grand, True)]}
    bases = {a: (base,), b: (base,), grand: (a,)}
    p = plan(_index(docs, bases), list(docs), _opts(tmp_path, callers=0))
    (c,) = [o.candidate for o in p.outcomes]
    assert (c.kind, c.name, c.gold) == ("derives", "Base", ("src/a.py::A", "src/b.py::B"))
    # a subclass a test defines cannot be graded against the tree's code: the base is no target
    docs["tests/test_x.py"] = [_occ("tests/test_x.py", 0, _cls("T", mod="t"), True)]
    bases[_cls("T", mod="t")] = (base,)
    assert plan(_index(docs, bases), list(docs), _opts(tmp_path, callers=0)).outcomes == []


def _pool(n_ambiguous: int, n_unique: int) -> list[graph_tasks.Candidate]:
    def cand(name: str, same: int) -> graph_tasks.Candidate:
        return graph_tasks.Candidate("callers", _fn(name), name, name, ("a.py",), ("b.py",), same)
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
    write(p, opts, "scip-python 0.6.8")
    tid = p.outcomes[0].task
    task = json.loads((opts.out / tid / "task.json").read_text())
    gold = json.loads((opts.out / tid / "gold.json").read_text())
    assert task["grader"] == "dokimi:graph_enum" and task["base"] == "a" * 40 and task["id"] == tid
    assert task["score"] == {"metric": "f1", "max": 1.0} and task["answer_key"] == "files"
    assert "2 symbols named `run`" in task["prompt"] and '{"files": [' in task["prompt"]
    assert gold["gold"] == ["src/api.py", "src/cli.py", "tests/test_core.py"] and gold["exclude"] == ["src/core.py"]
    (opts.out / tid / "task.json").write_text("{}")
    assert plan(_index(_callers_docs()), TREE, opts).outcomes[0].status == "kept"
    forced = Options(out=opts.out, base=opts.base, min_gold=2, force=True)
    assert plan(_index(_callers_docs()), TREE, forced).outcomes[0].status == "written"


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
    assert "6/6 index documents in the tree; files; candidates: 1 callers, 0 derives" in printed
    assert "1 task(s) written, 0 kept" in printed
    task = json.loads((out / "graph_callers_run" / "task.json").read_text())
    assert task["base"] == sha and task["code_index"] == "scip-test 0.1"
    _run(["eval", "tasks", "generate", f"{root}@HEAD", "--from", "graph", "--index", str(tmp_path / "index.scip"),
          "--out", str(out), "--dry-run"])
    assert "0 task(s) to write, 1 kept" in capsys.readouterr().out


@pytest.mark.parametrize("pinned, index, error", [
    (False, True, "pin the revision"),
    (True, False, "needs --index"),
    (True, "garbled", "cannot read the code index"),
    (True, "elsewhere", "no document of the code index"),
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
    argv = ["eval", "tasks", "generate", f"{root}@HEAD" if pinned else str(root), "--from", "graph",
            "--out", str(tmp_path / "tasks")] + (["--index", str(path)] if index else [])
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
