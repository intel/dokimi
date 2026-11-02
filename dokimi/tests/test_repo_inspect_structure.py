# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Tests for ``repo inspect --index``: the structure section read from a SCIP code index.

The signals are checked on a hand-built index (:func:`_index`), whose every reference is spelled out; the
real fixture ``fixtures/code_index/sample.scip`` checks the path mapping and the command line.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from dokimi.artifacts import validate_artifact
from dokimi.cli import build_parser
from dokimi.repos.code_index import CodeIndex
from dokimi.repos.code_index.scip import DEFINITION, Decoded, Occurrence
from dokimi.commands.repo import _print_structure
from dokimi.repos.environment import _structure_findings, inspect_repo
from dokimi.repos.environment import structure
from dokimi.repos.environment.structure import _assign, analyze, detect_prefix, mentioned, same_tree, tree_of
from dokimi.repos.source import WorkTree
from tests.test_repo_environment import _repo

FIXTURES = Path(__file__).parent / "fixtures" / "code_index"


def _sym(name: str, pkg: str = "p") -> str:
    return f"scip-python python {pkg} 0.1 `{pkg}`/{name}()."


def _index(docs: dict[str, list[tuple[str, bool]]], tool: str = "scip-python 0.6.8") -> CodeIndex:
    """An index from {path: [(symbol, is_definition)]}, one occurrence per line."""
    occurrences = {
        path: [Occurrence(path, line, 0, line, 1, symbol, DEFINITION if is_def else 0, None)
               for line, (symbol, is_def) in enumerate(occs)]
        for path, occs in docs.items()}
    return CodeIndex(Decoded(tool, occurrences, {}))


# src/core defines run and parse, referenced from src/cli, lib and a test; lib's own run makes half of
# src/core's names ambiguous. With a module limit of 2 the modules are src/core, src/cli and lib.
CORE_RUN, CORE_PARSE, ALT_RUN = _sym("run"), _sym("parse"), _sym("run", "alt")
DOCS = {
    "src/core/a.py": [(CORE_RUN, True), (CORE_PARSE, True)],
    "src/core/b.py": [(CORE_RUN, False)],
    "src/core/c.py": [],
    "src/cli/main.py": [(CORE_RUN, False), (CORE_PARSE, False)],
    "src/cli/args.py": [(CORE_PARSE, False)],
    "lib/alt/x.py": [(ALT_RUN, True), (CORE_RUN, False)],
    "tests/test_core.py": [(CORE_RUN, False), (CORE_PARSE, False)],
}


def test_reference_fanin_counts_files_outside_the_module_and_skips_tests():
    section = analyze(_index(DOCS), list(DOCS), "", limit=2)
    core = next(m for m in section["modules"] if m["module"] == "src/core")
    # src/cli/main.py, src/cli/args.py and lib/alt/x.py; not src/core/b.py (inside) nor the test
    assert core["files"] == 3 and core["reference_fanin"] == 3
    assert core["kept"]
    assert {m["module"] for m in section["modules"]} == {"src/core", "src/cli", "lib"}


def test_other_tree_modules_count_either_way():
    section = analyze(_index(DOCS), list(DOCS), "", limit=2)
    by = {m["module"]: m for m in section["modules"]}
    assert tree_of("src/core") == tree_of("src/core/deep") == "src/core"
    assert by["src/core"]["other_tree_modules"] == 2  # src/cli and lib are both other trees
    assert by["lib"]["other_tree_modules"] == 1  # it references src/core; nothing references it


def test_a_shallow_module_is_of_the_tree_under_it():
    assert same_tree("pkg", "pkg/sub") and same_tree("pkg/sub/x", "pkg")
    assert not same_tree("pkg", "pkgx/sub") and not same_tree(".", "pkg") and not same_tree("a/b", "a/c")
    docs = {"pkg/a.py": [(CORE_RUN, True)], "pkg/sub/b.py": [(CORE_RUN, False)], "pkg/sub/c.py": []}
    by = {m["module"]: m for m in analyze(_index(docs), list(docs), "", limit=1)["modules"]}
    assert by["pkg"]["reference_fanin"] == 1 and by["pkg"]["other_tree_modules"] == 0


def test_keep_rule_and_function_like_definitions():
    cls = "scip-python python p 0.1 `p`/Thing#"
    docs = {"a/x.py": [(CORE_RUN, True), (cls, True)], "a/y.py": [], "a/z.py": [],
            "b/u.py": [(CORE_RUN, False), (cls, False)], "c/v.py": [(CORE_RUN, False)]}
    by = {m["module"]: m for m in analyze(_index(docs), list(docs), "", limit=1)["modules"]}
    assert by["a"]["names"] == 1  # the class is not function-like
    assert by["a"]["reference_fanin"] == 2 and not by["a"]["kept"]  # 3 files, fan-in 2
    assert not by["b"]["kept"]  # 1 file
    three = {"f1", "f2", "f3"}
    assert structure._row("m", 3, set(), {}, three, set(), "")["kept"]
    assert not structure._row("m", 2, set(), {}, three, set(), "")["kept"]


def test_a_test_file_defines_no_module_name():
    docs = {**DOCS, "tests/test_core.py": [(_sym("run", "t"), True), (_sym("run", "t"), False)]}
    by = {m["module"]: m for m in analyze(_index(docs), list(docs), "", limit=2)["modules"]}
    assert by["src/core"]["ambiguity"] == 0.5  # lib's run only, not the test's


def test_a_second_definition_is_not_a_reference():
    # a C++ declaration in a header and its definition in a source file are both definitions
    docs = {"inc/x.h": [(CORE_RUN, True)], "src/x.cpp": [(CORE_RUN, True)], "src/y.cpp": [(ALT_RUN, True)]}
    by = {m["module"]: m for m in analyze(_index(docs), list(docs), "", limit=1)["modules"]}
    assert by["inc"]["reference_fanin"] == 0 and by["src"]["reference_fanin"] == 0


def _row(module: str, fanin: int, other: int = 0, ambiguity: float = 0.0) -> dict:
    return {"module": module, "files": 3, "reference_fanin": fanin, "other_tree_modules": other,
            "ambiguity": ambiguity, "names": 4, "in_guidance": False, "kept": True}


def test_findings_come_from_the_top_hotspots_past_the_bars():
    rows = [_row(f"m{i:02}", 100 - i, other=5, ambiguity=0.5) for i in range(11)]
    rows += [_row("few", 1, other=4, ambiguity=0.49)]
    found = {f["id"] for f in _structure_findings({"modules": rows})}
    assert "structure.cross_tree_bridge:m09" in found and "structure.name_ambiguity:m09" in found
    assert not any(":m10" in f or ":few" in f for f in found)  # past the 10 hotspots
    assert {f["id"] for f in _structure_findings({"modules": [_row("x", 9, other=4, ambiguity=0.49)]})} == set()
    assert _structure_findings(None) == []


def test_print_structure_lists_kept_modules_escaped(capsys):
    section = {"index": {"documents_in_tree": 2, "documents": 3},
               "modules": [_row(f"m{i}", 10 - i) for i in range(6)] + [_row("bad\x1b[2Jdir", 1)]}
    section["modules"][-1]["kept"] = True
    section["modules"].insert(0, {**_row("first\x1b[31m", 99, other=2), "in_guidance": True})
    _print_structure(section)
    out = capsys.readouterr().out
    assert "2/3 index documents in the tree, 8 modules, 8 kept" in out
    assert "\x1b" not in out and "other-tree modules 2, ambiguity 0.00\n" in out
    assert "m3: fan-in 7" in out and "m4:" not in out  # the top five only


@pytest.mark.parametrize("module, text, expected", [
    ("src/core", "tests/core/ is tests", False),       # the name between slashes, not the path
    ("a/core", "b/core/x", False),
    ("src/core", "edit `src/core-utils` first", False),  # a hyphen continues the token
    ("src/core", "src/core.v2 and src/core.py", False),  # an extension continues it
    ("src/core", "src/core.", True),                    # sentence-ending punctuation is not an extension
    ("src", "see src/ for code", True),                 # a trailing slash names the module
    ("src", "the src/main.py file", True),
    ("docs", "docs/guide.md", True),
    ("utils", "docs/utils/ is guidance", False),
    ("src/utils", "`utils`", True),                     # the bare name counts only between backticks
    ("src/utils", "the utils folder", False),
    ("src/utils", "use `utils/` here", True),
])
def test_guidance_mentions_match_the_path_or_a_backticked_name(module, text, expected):
    assert mentioned(module, text) is expected


def test_one_function_defined_in_two_modules_is_not_ambiguous():
    docs = {"include/x.h": [(_sym("run"), True)], "src/x.cc": [(_sym("run"), True)]}
    module_of = {"include/x.h": "include", "src/x.cc": "src"}
    _, names_of, ambiguous_of = structure._definitions(_index(docs), {doc: doc for doc in docs}, module_of)
    assert names_of["include"] == {"run"} and ambiguous_of["include"] == set()
    docs["lib/alt.py"] = [(_sym("run", "alt"), True)]  # a different function named run: now both are ambiguous
    module_of["lib/alt.py"] = "lib"
    _, _, ambiguous_of = structure._definitions(_index(docs), {doc: doc for doc in docs}, module_of)
    assert ambiguous_of["include"] == {"run"} and ambiguous_of["lib"] == {"run"}


def test_a_share_below_the_threshold_that_rounds_up_is_not_a_finding():
    from dokimi.repos.environment import _structure_findings
    row = structure._row("m", 3, {f"n{i}" for i in range(1001)}, {f"n{i}" for i in range(500)}, set(), set(), "")
    assert row["ambiguity"] == pytest.approx(500 / 1001) and row["ambiguity"] < 0.5
    row["kept"] = True
    assert not [f for f in _structure_findings({"modules": [row]}) if f["rule"] == "structure.name_ambiguity"]


def test_kept_modules_are_ordered_by_fan_in_then_name():
    syms = {m: _sym(f"f{m}") for m in "abc"}
    docs = {f"src/{m}/{i}.py": [] for m in "abc" for i in (1, 2, 3)}
    for m in "abc":
        docs[f"src/{m}/1.py"] = [(syms[m], True)]
    fan_in = {"a": 3, "b": 5, "c": 4}
    for r in range(1, 6):
        docs[f"lib/r{r}.py"] = [(syms[m], False) for m, n in fan_in.items() if r <= n]
    modules = analyze(_index(docs), list(docs), "", limit=3)["modules"]  # one module per src directory
    assert [m["module"] for m in modules if m["kept"]] == ["src/b", "src/c", "src/a"]


def test_ambiguity_is_the_share_of_names_another_module_defines():
    section = analyze(_index(DOCS), list(DOCS), "", limit=2)
    by = {m["module"]: m for m in section["modules"]}
    assert by["src/core"]["names"] == 2 and by["src/core"]["ambiguity"] == 0.5
    assert by["lib"]["ambiguity"] == 1.0
    one_of_three = {**DOCS, "src/core/c.py": [(_sym("a"), True), (_sym("b"), True)]}
    by = {m["module"]: m for m in analyze(_index(one_of_three), list(DOCS), "", limit=2)["modules"]}
    assert by["src/core"]["ambiguity"] == 0.25  # run, of run, parse, a and b
    assert structure._row("m", 3, {"a", "b", "c"}, {"a"}, set(), set(), "")["ambiguity"] == pytest.approx(1 / 3)
    assert by["src/cli"]["names"] == 0 and by["src/cli"]["ambiguity"] == 0.0


def test_a_large_directory_is_split_into_its_subdirectories():
    files = [f"pkg/a/{i}.py" for i in range(20)] + [f"pkg/b/{i}.py" for i in range(20)] + ["pkg/top.py"]
    modules = _assign(files, limit=30)
    assert {modules[f] for f in files} == {"pkg", "pkg/a", "pkg/b"}
    assert modules["pkg/top.py"] == "pkg"
    assert set(_assign(files, limit=41).values()) == {"."}
    assert structure.module_limit(10) == 30 and structure.module_limit(1000) == 50


def test_guidance_mentions_need_a_token_boundary():
    assert mentioned("src/core", "edit `src/core` first")
    assert not mentioned("src/core", "see the /core/ package")  # a name between slashes is not a mention
    assert not mentioned("src/core", "src/core2 holds the rest")
    assert not mentioned("src/core", "a core idea")
    assert mentioned("src/core", "run src/core first") and mentioned("src/core", "(src/core).")
    assert not mentioned(".", "see `.` and ./x")


def test_the_root_prefix_is_found_from_the_tree():
    tree = ["README.md", "dokimi/pkg/a.py", "dokimi/pkg/b.py", "other/pkg/a.py"]
    assert detect_prefix(["pkg/a.py", "pkg/b.py"], tree) == ("dokimi/", 2)
    assert detect_prefix(["dokimi/pkg/a.py"], tree) == ("", 1)
    assert detect_prefix(["gone.py"], tree) == ("", 0)
    # one document under each of two prefixes: the shorter wins
    assert detect_prefix(["a.py"], ["x/a.py", "xy/a.py"]) == ("x/", 1)


def test_documents_outside_the_tree_are_counted_not_used():
    section = analyze(_index(DOCS), [p for p in DOCS if p != "src/cli/args.py"], "", limit=2)
    assert section["index"]["documents"] == 7
    assert section["index"]["documents_in_tree"] == 6 and section["index"]["documents_not_in_tree"] == 1
    core = next(m for m in section["modules"] if m["module"] == "src/core")
    assert core["reference_fanin"] == 2
    assert section["rules"]["module_limit"] == 2


@pytest.mark.outcome("cjm-repo-inspect")
def test_inspect_with_an_index_adds_the_section_and_info_findings(tmp_path):
    # 30 more files in src/cli: past the default module limit (30), so the tree splits as with limit=2
    docs = {**DOCS, **{f"src/cli/f{i}.py": [] for i in range(30)}}
    _repo(tmp_path, {**{p: "x = 1\n" for p in docs}, "AGENTS.md": "Start in `src/cli`.\n"})
    payload = inspect_repo(WorkTree(tmp_path), index=_index(docs))
    validate_artifact("repo_environment", {"schema_version": "repo_environment.v1", **payload})
    by = {m["module"]: m for m in payload["structure"]["modules"]}
    assert by["src/cli"]["in_guidance"] and not by["src/core"]["in_guidance"]
    found = {f["id"]: f for f in payload["findings"] if f["rule"].startswith("structure.")}
    assert set(found) == {"structure.name_ambiguity:src/core"}  # 2 other-tree modules: under the bar
    assert all(f["severity"] == "info" and f["confidence"] == "low" for f in found.values())
    assert "calls: the code index records references, not call expressions" in payload["not_observed"]
    assert not any("not files of this tree" in n for n in payload["not_observed"])
    (tmp_path / "src/cli/args.py").unlink()
    gone = inspect_repo(WorkTree(tmp_path), index=_index(docs))["not_observed"]
    assert "1 index documents that are not files of this tree" in gone
    assert "structure" not in inspect_repo(WorkTree(tmp_path))


def _fixture_repo(root: Path) -> None:
    # the fixture index is relative to tests/fixtures/code_index/; here its files sit under vendor/
    files = {f"vendor/sample_pkg/{p.name}": p.read_text() for p in (FIXTURES / "sample_pkg").glob("*.py")}
    _repo(root, {**files, "AGENTS.md": "x\n"})


@pytest.mark.outcome("cjm-repo-inspect")
def test_cli_index_maps_a_real_index_onto_the_tree(tmp_path, capsys):
    _fixture_repo(tmp_path / "toy")
    shutil.copy(FIXTURES / "sample.scip", tmp_path / "sample.scip")
    args = build_parser().parse_args(["repo", "inspect", str(tmp_path / "toy"), "--index",
                                      str(tmp_path / "sample.scip"), "--out", str(tmp_path / "env.json")])
    args.func(args)
    out = capsys.readouterr().out
    assert "structure: 3/3 index documents in the tree, 1 modules" in out
    data = json.loads((tmp_path / "env.json").read_text())
    assert data["structure"]["index"]["root_prefix"] == "vendor/"
    assert data["structure"]["index"]["tool"] == "scip-python 0.6.8"


@pytest.mark.outcome("cjm-repo-inspect")
@pytest.mark.parametrize("content, error", [(None, "cannot read the code index"),
                                            (b"\x0a\xff", "cannot read the code index"),
                                            ("", "cannot read the code index"),
                                            ("fixture", "no document of the code index")])
def test_cli_exits_2_on_an_unusable_index(tmp_path, capsys, content, error):
    _repo(tmp_path / "toy", {"AGENTS.md": "x"})
    index = tmp_path / "index.scip"
    if isinstance(content, bytes):
        index.write_bytes(content)
    elif content == "fixture":  # a real index of another tree
        shutil.copy(FIXTURES / "sample.scip", index)
    path = "" if content == "" else str(index)
    args = build_parser().parse_args(["repo", "inspect", str(tmp_path / "toy"), "--index", path])
    with pytest.raises(SystemExit) as exc:
        args.func(args)
    assert exc.value.code == 2
    assert error in capsys.readouterr().err
