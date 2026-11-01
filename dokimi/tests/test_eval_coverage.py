# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`eval coverage`: coverage and relevance of an eval set against a frozen need graph (#331)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from dokimi.artifacts import ArtifactValidationError, load_artifact
from dokimi.cli import build_parser
from dokimi.eval_coverage import LabelledEval, coverage_report, exact_shares, graph_digest, load_graph, read_labels
from dokimi.novelty.needs import NeedGraph, NeedNode
from dokimi.novelty.needs import load_graph as novelty_load_graph
from dokimi.novelty.needs import save_graph

NODES = [
    {"id": "n1", "text": "convert with asnumpy before numpy.save", "path_class": "core"},
    {"id": "n2", "text": "memory-map files larger than RAM", "path_class": "core",
     "confirmed_by": "user"},
    {"id": "n3", "text": "keep allow_pickle=False for untrusted files", "path_class": "rare_critical"},
    {"id": "n4", "text": "choose HDF5 for chunked data", "path_class": "branch"},
    {"id": "n5", "text": "plot the array", "path_class": "out_of_scope"},
]


def _graph(tmp_path: Path) -> Path:
    path = tmp_path / "need_graph.json"
    path.write_text(json.dumps({"artifact_name": "dpnp-io", "nodes": NODES}))
    return path


def _skill(tmp_path: Path, rows: list[dict]) -> Path:
    skill = tmp_path / "skill"
    (skill / "evals").mkdir(parents=True)
    (skill / "evals" / "evals.json").write_text(json.dumps({"skill_name": "dpnp-io", "evals": rows}))
    return skill


ROWS = [
    {"id": "save", "prompt": "save a dpnp array", "needs": ["n1"]},
    {"id": "big-file", "prompt": "50 GB file", "needs": ["n2", "n4"]},
    {"id": "plot", "prompt": "plot it", "needs": ["n5"]},
    {"id": "css", "prompt": "fix my css", "needs": []},
    {"id": "new", "prompt": "not mapped yet"},
]


def _run(capsys, *argv: str) -> SimpleNamespace:
    args = build_parser().parse_args(["eval", "coverage", *argv])
    code = 0
    try:
        args.func(args)
    except SystemExit as exc:
        code = int(exc.code or 0)
    out = capsys.readouterr()
    return SimpleNamespace(returncode=code, stdout=out.out, stderr=out.err)


@pytest.mark.outcome("cjm-eval-coverage-gaps")
def test_report_lists_uncovered_critical_nodes_and_classifies_each_eval(tmp_path):
    report = coverage_report(load_graph(_graph(tmp_path)), read_labels(_skill(tmp_path, ROWS)), "skill")

    assert [g["id"] for g in report["gaps"]] == ["n3"]
    assert report["gaps"][0]["confirmed_by"] == "hypothesis"
    assert {e["id"]: e["relevance"] for e in report["evals"]} == {
        "save": "relevant_core", "big-file": "relevant_core", "plot": "out_of_scope",
        "css": "orphan", "new": "unlabelled",
    }
    # in scope: n1 1.0 + n2 1.0 + n3 1.0 + n4 0.7 = 3.7; covered n1, n2, n4 = 2.7
    assert report["coverage"]["weighted"] == pytest.approx(2.7 / 3.7, abs=1e-4)
    assert report["coverage"]["core_rare_critical"] == pytest.approx(2 / 3, abs=1e-4)
    assert report["coverage"]["rare_critical"] == 0.0
    assert (report["graph"]["hypotheses"], report["graph"]["in_scope"]) == (4, 4)


def test_peripheral_eval_and_task_metadata_needs(tmp_path):
    tasks = tmp_path / "tasks"
    task = tasks / "hdf5-write"
    task.mkdir(parents=True)
    (task / "task.toml").write_text('[metadata]\nneeds = ["n4"]\n')
    (task / "instruction.md").write_text("write chunks")

    labels = read_labels(tasks)
    report = coverage_report(load_graph(_graph(tmp_path)), labels, "tasks")

    assert [(e.id, e.source, e.needs) for e in labels] == [("hdf5-write", "task", ("n4",))]
    assert report["evals"][0]["relevance"] == "relevant_peripheral"
    assert report["coverage"]["by_class"]["branch"] == {"covered": 1, "total": 1}


def test_same_files_same_report_and_digest_ignores_node_order(tmp_path):
    graph, skill = _graph(tmp_path), _skill(tmp_path, ROWS)
    first = coverage_report(load_graph(graph), read_labels(skill), "skill")
    second = coverage_report(load_graph(graph), read_labels(skill), "skill")
    reordered = {"artifact_name": "dpnp-io", "nodes": list(reversed(NODES))}

    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
    assert graph_digest(reordered) == first["graph"]["digest"]
    assert graph_digest({"nodes": NODES[:4]}) != first["graph"]["digest"]
    defaults = [{**n, "type": "intent", "confirmed_by": n.get("confirmed_by", "hypothesis"),
                 "note": "x"} for n in NODES]
    assert graph_digest({"nodes": defaults}) == first["graph"]["digest"]
    confirmed = [{**n, "confirmed_by": "user"} for n in NODES]
    assert graph_digest({"nodes": confirmed}) != first["graph"]["digest"]


def test_novelty_save_graph_output_loads_as_need_graph(tmp_path):
    path = tmp_path / "g.json"
    node = NeedNode("n1", "a need", path_class="core", confirmed_by="user")
    save_graph(NeedGraph("x", "d", nodes=[node], provenance={"draws": 3}), path)

    assert load_graph(path)["nodes"][0]["confirmed_by"] == "user"
    assert novelty_load_graph(path).as_dict() == json.loads(path.read_text())


def test_both_readers_agree_on_the_fixture():
    fixture = Path(__file__).parent / "fixtures" / "artifacts" / "need_graph.v1.json"
    novelty = novelty_load_graph(fixture)

    assert [(n.id, n.path_class, n.confirmed_by) for n in novelty.nodes] == [
        (n["id"], n["path_class"], n.get("confirmed_by", "hypothesis"))
        for n in load_graph(fixture)["nodes"]]


def test_duplicate_node_ids_are_rejected(tmp_path):
    path = tmp_path / "g.json"
    path.write_text(json.dumps({"artifact_name": "x", "nodes": [
        {"id": "n1", "text": "a", "path_class": "core"},
        {"id": "n1", "text": "b", "path_class": "out_of_scope"}]}))

    with pytest.raises(ValueError, match="duplicate node ids"):
        load_graph(path)


def test_graph_with_unknown_path_class_is_rejected(tmp_path):
    path = tmp_path / "g.json"
    path.write_text(json.dumps({"artifact_name": "x", "nodes": [
        {"id": "n1", "text": "t", "path_class": "critical"}]}))

    with pytest.raises(ArtifactValidationError, match="path_class"):
        load_graph(path)


def test_cli_writes_artifact_and_fails_under_floor(tmp_path, capsys):
    out = tmp_path / "cov.json"
    graph, skill = _graph(tmp_path), _skill(tmp_path, ROWS)

    ok = _run(capsys, str(skill), "--graph", str(graph), "--out", str(out))
    low = _run(capsys, str(skill), "--graph", str(graph), "--min", "0.5",
               "--min", "rare_critical=0.5")

    assert ok.returncode == 0, ok.stderr
    assert "n3 [rare_critical, hypothesis]" in ok.stdout
    assert load_artifact("eval_coverage", out)["gaps"][0]["id"] == "n3"
    assert low.returncode == 1
    assert "FAIL: rare_critical coverage 0.000000 < 0.500000" in low.stderr
    assert load_artifact("eval_coverage", out)["coverage"]["weights"]["branch"] == 0.7


def test_cli_unknown_need_id_and_bad_inputs_exit_2(tmp_path, capsys):
    graph = _graph(tmp_path)
    typo = _skill(tmp_path, [{"id": "save", "needs": ["n9"]}])
    broken = tmp_path / "evals.json"
    broken.write_text("{not json")

    out = tmp_path / "cov.json"
    unknown = _run(capsys, str(typo), "--graph", str(graph), "--out", str(out))
    bad = _run(capsys, str(broken), "--graph", str(graph))
    missing = _run(capsys, str(tmp_path / "nope"), "--graph", str(graph))
    no_graph = _run(capsys, str(typo), "--graph", str(tmp_path / "none.json"))

    assert unknown.returncode == 2
    assert load_artifact("eval_coverage", out)["problems"] == [
        {"eval": "save", "source": "evals.json", "kind": "unknown_need", "need": "n9"}]
    assert (bad.returncode, missing.returncode, no_graph.returncode) == (2, 2, 2)
    assert "not a skill-creator evals.json" in bad.stderr


def test_cli_rejects_a_floor_outside_0_1(capsys):
    for value in ("1.5", "high", "recall=0.5"):
        with pytest.raises(SystemExit) as exc:
            build_parser().parse_args(["eval", "coverage", "x", "--graph", "g", "--min", value])
        assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "must lie in 0-1, got 1.5" in err and "got 'recall'" in err


def test_markdown_lists_problems_and_says_none_without_gaps(tmp_path, capsys):
    rows = [{"id": "all", "needs": ["n1", "n2", "n3", "n9"]}]
    res = _run(capsys, str(_skill(tmp_path, rows)), "--graph", str(_graph(tmp_path)))

    assert res.returncode == 2
    assert "- none" in res.stdout
    assert "Problems:\n- all (evals.json): unknown need id 'n9'" in res.stdout


@pytest.mark.parametrize("needs", ["n1", {"n1": 1}, 3, [1], None])
def test_needs_that_is_not_a_list_of_ids_is_a_problem(tmp_path, capsys, needs):
    res = _run(capsys, str(_skill(tmp_path, [{"id": "e", "needs": needs}])), "--graph",
               str(_graph(tmp_path)))

    assert res.returncode == 2
    assert "- e (evals.json): needs is not a list of node ids" in res.stdout


def test_rows_that_are_not_objects_are_ignored_and_an_unparsable_task_refused(tmp_path):
    root = tmp_path / "suite"
    root.mkdir()
    (root / "evals.json").write_text(json.dumps({"evals": ["text row", {"needs": ["n1"]}]}))
    assert read_labels(root) == [LabelledEval("1", "evals.json", ("n1",))]

    bad = root / "broken-task"
    bad.mkdir()
    (bad / "task.toml").write_text("[metadata\n")
    (bad / "instruction.md").write_text("x")
    with pytest.raises(ValueError, match="unreadable task"):
        read_labels(root)


def test_cli_refuses_an_empty_suite_a_non_list_evals_and_out_over_graph(tmp_path, capsys):
    graph, empty = _graph(tmp_path), tmp_path / "empty"
    empty.mkdir()
    (tmp_path / "evals.json").write_text(json.dumps({"evals": {"id": "x"}}))

    none = _run(capsys, str(empty), "--graph", str(graph))
    dict_rows = _run(capsys, str(tmp_path / "evals.json"), "--graph", str(graph))
    clobber = _run(capsys, str(_skill(tmp_path, ROWS)), "--graph", str(graph), "--out", str(graph))

    assert (none.returncode, dict_rows.returncode, clobber.returncode) == (2, 2, 2)
    assert "no evals.json rows or tasks" in none.stderr
    assert '"evals" is not a list' in dict_rows.stderr
    assert "--out would overwrite an input file" in clobber.stderr
    assert json.loads(graph.read_text())["nodes"] == NODES


def test_graph_with_only_out_of_scope_nodes_has_no_coverage_figures(tmp_path):
    graph = {"artifact_name": "x", "nodes": [{"id": "n1", "text": "t", "path_class": "out_of_scope"}]}
    report = coverage_report(graph, [LabelledEval("e", "task", None)], "r")

    assert report["coverage"] == {
        "weights": {"core": 1.0, "rare_critical": 1.0, "branch": 0.7, "supporting": 0.4,
                    "out_of_scope": 0.0},
        "weighted": None, "core_rare_critical": None, "rare_critical": None,
        "by_class": {c: {"covered": 0, "total": int(c == "out_of_scope")}
                     for c in ("core", "rare_critical", "branch", "supporting", "out_of_scope")},
    }


@pytest.mark.parametrize("target", ["graph", "evals", "task", "instruction"])
def test_out_never_overwrites_an_input_file(tmp_path, capsys, target):
    graph, skill = _graph(tmp_path), _skill(tmp_path, ROWS)
    tasks, task = tmp_path / "tasks", tmp_path / "tasks" / "t1"
    task.mkdir(parents=True)
    (task / "task.toml").write_text('[metadata]\nneeds = ["n1"]\n')
    (task / "instruction.md").write_text("x")
    victim = {"graph": graph, "evals": skill / "evals" / "evals.json", "task": task / "task.toml",
              "instruction": task / "instruction.md"}[target]
    root = tasks if target in ("task", "instruction") else skill
    before = victim.read_bytes()

    res = _run(capsys, str(root), "--graph", str(graph), "--out", str(victim))

    assert res.returncode == 2
    assert "--out would overwrite an input file" in res.stderr
    assert victim.read_bytes() == before


@pytest.mark.parametrize("name", ["task.yaml", "docker-compose.yaml"])
def test_out_never_overwrites_a_terminal_bench_task_file(tmp_path, capsys, name):
    task = tmp_path / "tasks" / "tb1"
    task.mkdir(parents=True)
    (task / "task.yaml").write_text("instruction: write chunks\n")
    (task / "docker-compose.yaml").write_text("services: {}\n")
    victim = task / name
    before = victim.read_bytes()

    res = _run(capsys, str(tmp_path / "tasks"), "--graph", str(_graph(tmp_path)), "--out", str(victim))

    assert res.returncode == 2, res.stderr
    assert "--out would overwrite an input file" in res.stderr
    assert victim.read_bytes() == before


def test_a_task_md_without_frontmatter_is_a_note_not_a_task(tmp_path, capsys):
    tasks = tmp_path / "tasks"
    notes = tasks / "notes"
    notes.mkdir(parents=True)
    (notes / "task.md").write_text("# planning notes\n")
    real = tasks / "t1"
    real.mkdir()
    (real / "task.toml").write_text('[metadata]\nneeds = ["n1"]\n')

    labels = read_labels(tasks)
    res = _run(capsys, str(tasks), "--graph", str(_graph(tmp_path)))

    assert [e.id for e in labels] == ["t1"]
    assert res.returncode == 0, res.stderr


def test_exact_shares_are_the_hand_computed_figures():
    # weighted = (core c1 + 3 branch at 0.7) / (core c1, c2 + rare r1 + 3 branch) = 3.1 / 5.1; out_of_scope is outside it
    graph = {"artifact_name": "x", "nodes": [
        {"id": "c1", "text": "t", "path_class": "core"},
        {"id": "c2", "text": "t", "path_class": "core"},
        {"id": "r1", "text": "t", "path_class": "rare_critical"},
        {"id": "b1", "text": "t", "path_class": "branch"},
        {"id": "b2", "text": "t", "path_class": "branch"},
        {"id": "b3", "text": "t", "path_class": "branch"},
        {"id": "o1", "text": "t", "path_class": "out_of_scope"},
    ]}
    evals = [LabelledEval("e1", "task", ("c1", "b1", "b2", "b3", "o1"))]

    figures = exact_shares(coverage_report(graph, evals, "r"))

    assert figures["weighted"] == pytest.approx(3.1 / 5.1, abs=1e-12)
    assert figures["core_rare_critical"] == pytest.approx(1 / 3, abs=1e-12)
    assert figures["rare_critical"] == 0.0


def test_a_bare_floor_is_on_the_weighted_figure(tmp_path, capsys):
    # weighted is 0.6078 and core_rare_critical 0.3333: a bare 0.5 passes only if the default metric is weighted
    graph = tmp_path / "need_graph.json"
    graph.write_text(json.dumps({"artifact_name": "x", "nodes": [
        {"id": "c1", "text": "t", "path_class": "core"},
        {"id": "c2", "text": "t", "path_class": "core"},
        {"id": "r1", "text": "t", "path_class": "rare_critical"},
        {"id": "b1", "text": "t", "path_class": "branch"},
        {"id": "b2", "text": "t", "path_class": "branch"},
        {"id": "b3", "text": "t", "path_class": "branch"},
    ]}))
    skill = _skill(tmp_path, [{"id": "e", "needs": ["c1", "b1", "b2", "b3"]}])

    res = _run(capsys, str(skill), "--graph", str(graph), "--min", "0.5")

    assert res.returncode == 0, res.stderr


def test_a_floor_equal_to_the_share_passes(tmp_path, capsys):
    nodes = [{"id": f"c{i}", "text": "t", "path_class": "core"} for i in range(4)]
    graph = tmp_path / "need_graph.json"
    graph.write_text(json.dumps({"artifact_name": "x", "nodes": nodes}))
    skill = _skill(tmp_path, [{"id": "e", "needs": ["c0", "c1"]}])

    res = _run(capsys, str(skill), "--graph", str(graph), "--min", "core_rare_critical=0.5")

    assert res.returncode == 0, res.stderr


def test_an_unreadable_evals_directory_exits_2_not_a_traceback(tmp_path, capsys, monkeypatch):
    skill = _skill(tmp_path, ROWS)
    real_exists = Path.exists

    def exists(self, *args, **kwargs):
        if self == skill / "evals" / "evals.json" or self == skill:
            raise PermissionError(13, "Permission denied", str(self))
        return real_exists(self, *args, **kwargs)

    monkeypatch.setattr(Path, "exists", exists)
    res = _run(capsys, str(skill), "--graph", str(_graph(tmp_path)))

    assert res.returncode == 2
    assert "Permission denied" in res.stderr


def test_out_whose_rejected_sibling_is_the_graph_is_refused(tmp_path, capsys):
    # save_artifact removes <out>.rejected after a valid save: an input at that path must not be deleted
    graph = _graph(tmp_path)
    sibling = graph.with_name("cov.json.rejected")
    graph.rename(sibling)
    before = sibling.read_bytes()

    res = _run(capsys, str(_skill(tmp_path, ROWS)), "--graph", str(sibling), "--out", str(tmp_path / "cov.json"))

    assert res.returncode == 2
    assert "--out would overwrite an input file" in res.stderr
    assert sibling.read_bytes() == before


def test_a_floor_on_a_class_the_graph_lacks_is_n_a_and_fails(tmp_path, capsys):
    nodes = [{"id": f"c{i}", "text": "t", "path_class": "core"} for i in range(2)]
    graph = tmp_path / "need_graph.json"
    graph.write_text(json.dumps({"artifact_name": "x", "nodes": nodes}))
    skill = _skill(tmp_path, [{"id": "e", "needs": ["c0"]}])

    res = _run(capsys, str(skill), "--graph", str(graph), "--min", "rare_critical=0.5")

    assert res.returncode == 1
    assert "FAIL: rare_critical coverage n/a < 0.500000" in res.stderr


def test_out_that_is_a_symlink_loop_exits_2_not_a_traceback(tmp_path, capsys, monkeypatch):
    # Python 3.11/3.12 raise RuntimeError from Path.resolve() on a loop; 3.13+ do not, so raise it here
    monkeypatch.chdir(tmp_path)
    real_resolve = Path.resolve

    def resolve(self, *args, **kwargs):
        if self.name == "loop":
            raise RuntimeError("Symlink loop")
        return real_resolve(self, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolve)

    res = _run(capsys, str(_skill(tmp_path, ROWS)), "--graph", str(_graph(tmp_path)), "--out", "loop")

    assert res.returncode == 2
    assert "cannot resolve" in res.stderr


def test_out_as_a_relative_path_to_an_input_is_refused(tmp_path, capsys, monkeypatch):
    skill = _skill(tmp_path, ROWS)
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    victim = skill / "evals" / "evals.json"
    before = victim.read_bytes()

    res = _run(capsys, "../skill", "--graph", str(_graph(tmp_path)), "--out", "../skill/evals/evals.json")

    assert res.returncode == 2
    assert "--out would overwrite an input file" in res.stderr
    assert victim.read_bytes() == before


def test_a_null_task_needs_is_malformed_and_a_missing_key_is_unlabelled(tmp_path):
    tasks = tmp_path / "tasks"
    task = tasks / "t-null"
    task.mkdir(parents=True)
    (task / "task.md").write_text("---\nmetadata:\n  needs:\n---\n\nwrite chunks\n")
    missing = tasks / "t-missing"
    missing.mkdir()
    (missing / "task.toml").write_text("[metadata]\n")
    (missing / "instruction.md").write_text("x")

    labels = {e.id: e for e in read_labels(tasks)}

    assert labels["t-null"].malformed and labels["t-null"].needs is None
    assert not labels["t-missing"].malformed and labels["t-missing"].needs is None


@pytest.mark.parametrize(("covered", "floor", "code"), [(6, "0.85714", 0), (2, "0.66669", 1)])
def test_a_floor_is_judged_on_the_unrounded_share(tmp_path, capsys, covered, floor, code):
    # 6 of 7 core nodes is 0.857142... (rounds to 0.8571); 2 of 3 is 0.666666... (rounds to 0.6667)
    cores = 7 if covered == 6 else 3
    nodes = [{"id": f"c{i}", "text": "t", "path_class": "core"} for i in range(cores)]
    graph = tmp_path / "need_graph.json"
    graph.write_text(json.dumps({"artifact_name": "x", "nodes": nodes}))
    skill = _skill(tmp_path, [{"id": "e", "needs": [f"c{i}" for i in range(covered)]}])

    res = _run(capsys, str(skill), "--graph", str(graph), "--min", f"core_rare_critical={floor}")

    assert res.returncode == code, res.stderr


def test_markdown_table_cells_escape_pipes_and_newlines(tmp_path, capsys):
    rows = [{"id": "save|load", "needs": ["n1"]}, {"id": "two\nlines", "needs": ["n2", "n3"]}]
    res = _run(capsys, str(_skill(tmp_path, rows)), "--graph", str(_graph(tmp_path)))

    assert res.returncode == 0, res.stderr
    assert "| save\\|load | evals.json | n1 | " in res.stdout
    assert "| two lines | evals.json | n2, n3 | " in res.stdout


def test_out_on_a_directory_is_a_controlled_error(tmp_path, capsys):
    res = _run(capsys, str(_skill(tmp_path, ROWS)), "--graph", str(_graph(tmp_path)), "--out", str(tmp_path))

    assert res.returncode == 2
    assert "Error: cannot write" in res.stderr and "Traceback" not in res.stderr
