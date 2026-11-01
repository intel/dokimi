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
from dokimi.eval_coverage import LabelledEval, coverage_report, graph_digest, load_graph, read_labels
from dokimi.novelty.needs import NeedGraph, NeedNode, save_graph

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
    (task / "task.toml").write_text('[metadata]\nneeds = "n4"\n')
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


def test_novelty_save_graph_output_loads_as_need_graph(tmp_path):
    path = tmp_path / "g.json"
    save_graph(NeedGraph("x", "d", nodes=[NeedNode("n1", "a need", path_class="core")]), path)

    assert load_graph(path)["nodes"][0]["path_class"] == "core"


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
    low = _run(capsys, str(skill), "--graph", str(graph), "--min-rare-critical", "0.5")

    assert ok.returncode == 0, ok.stderr
    assert "n3 [rare_critical, hypothesis]" in ok.stdout
    assert load_artifact("eval_coverage", out)["gaps"][0]["id"] == "n3"
    assert low.returncode == 1
    assert "rare_critical coverage 0.00 < 0.50" in low.stderr


def test_cli_unknown_need_id_and_bad_inputs_exit_2(tmp_path, capsys):
    graph = _graph(tmp_path)
    typo = _skill(tmp_path, [{"id": "save", "needs": ["n9"]}])
    broken = tmp_path / "evals.json"
    broken.write_text("{not json")

    unknown = _run(capsys, str(typo), "--graph", str(graph), "--format", "json")
    bad = _run(capsys, str(broken), "--graph", str(graph))
    missing = _run(capsys, str(tmp_path / "nope"), "--graph", str(graph))
    no_graph = _run(capsys, str(typo), "--graph", str(tmp_path / "none.json"))

    assert unknown.returncode == 2
    assert json.loads(unknown.stdout)["problems"] == ["save: unknown need id 'n9'"]
    assert (bad.returncode, missing.returncode, no_graph.returncode) == (2, 2, 2)
    assert "not a skill-creator evals.json" in bad.stderr


def test_cli_rejects_a_floor_outside_0_1(capsys):
    for value in ("1.5", "high"):
        with pytest.raises(SystemExit) as exc:
            build_parser().parse_args(["eval", "coverage", "x", "--graph", "g", "--min-coverage", value])
        assert exc.value.code == 2
    assert "must lie in 0-1, got 1.5" in capsys.readouterr().err


def test_markdown_lists_problems_and_says_none_without_gaps(tmp_path, capsys):
    rows = [{"id": "all", "needs": ["n1", "n2", "n3", "n9"]}]
    res = _run(capsys, str(_skill(tmp_path, rows)), "--graph", str(_graph(tmp_path)))

    assert res.returncode == 2
    assert "- none" in res.stdout
    assert "Problems:\n- all: unknown need id 'n9'" in res.stdout


def test_unparsable_task_is_skipped_and_rows_that_are_not_objects_ignored(tmp_path):
    root = tmp_path / "suite"
    bad = root / "broken-task"
    bad.mkdir(parents=True)
    (bad / "task.toml").write_text("[metadata\n")
    (bad / "instruction.md").write_text("x")
    (root / "evals.json").write_text(json.dumps({"evals": ["text row", {"needs": "n1"}]}))

    assert read_labels(root) == [LabelledEval("1", "evals.json", ("n1",))]


def test_graph_with_only_out_of_scope_nodes_has_no_coverage_figures(tmp_path):
    graph = {"artifact_name": "x", "nodes": [{"id": "n1", "text": "t", "path_class": "out_of_scope"}]}
    report = coverage_report(graph, [LabelledEval("e", "task", None)], "r")

    assert report["coverage"] == {
        "weighted": None, "core_rare_critical": None, "rare_critical": None,
        "by_class": {c: {"covered": 0, "total": int(c == "out_of_scope")}
                     for c in ("core", "rare_critical", "branch", "supporting", "out_of_scope")},
    }
