# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""``eval graph``: a need graph built from name + description only, frozen once written (#331)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from dokimi.artifacts import load_artifact
from dokimi.cli import build_parser
from dokimi.eval_coverage import coverage_report, load_graph, read_labels
from dokimi.novelty.client import LLMClient
from dokimi.novelty.needs import PROMPT_VERSION
from tests.cli_refusal import refusal

BODY_MARKER = "BODY-ONLY-SECRET-STEP"
REPLY = {
    "nodes": [
        {"id": "n1", "text": "save the workbook", "type": "action", "path_class": "core"},
        {"id": "n2", "text": "recover a corrupt file", "type": "action", "path_class": "rare_critical"},
        {"id": "n3", "text": "plot a chart", "type": "intent", "path_class": "out_of_scope"},
    ],
    "edges": [{"from": "n1", "to": "n2", "kind": "requires"}],
}


def _skill(tmp_path: Path, description: str = "Edit Excel workbooks safely") -> Path:
    skill = tmp_path / "xlsx"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        f"---\nname: xlsx\ndescription: {description}\n---\n\n{BODY_MARKER}\n", encoding="utf-8")
    return skill


def _stub(monkeypatch, reply) -> list[str]:
    prompts: list[str] = []

    def transport(self, prompt, system, *, accept=None, draw=0):
        prompts.append(system + prompt)
        return json.dumps(reply), None

    monkeypatch.setattr(LLMClient, "_default_transport", transport)
    return prompts


def _run(capsys, *argv: str) -> SimpleNamespace:
    args = build_parser().parse_args(["eval", "graph", *argv])
    with refusal() as outcome:
        args.func(args)
    out = capsys.readouterr()
    return SimpleNamespace(returncode=outcome.code, stdout=out.out, stderr=out.err)


@pytest.mark.outcome("cjm-eval-graph-build")
def test_graph_is_built_from_name_and_description_only_and_scores_with_coverage(
        tmp_path, monkeypatch, capsys):
    prompts = _stub(monkeypatch, REPLY)
    out = tmp_path / "graph.json"

    result = _run(capsys, "--artifact", str(_skill(tmp_path)), "--out", str(out), "--model", "m1")

    assert result.returncode == 0, result.stderr
    assert len(prompts) == 1 and "Edit Excel workbooks safely" in prompts[0]
    assert BODY_MARKER not in prompts[0]
    data = load_artifact("need_graph", out)
    assert data["provenance"] == {"model": "m1", "prompt_version": PROMPT_VERSION, "draws": 1}
    assert {n["confirmed_by"] for n in data["nodes"]} == {"hypothesis"}
    (_skill_dir := tmp_path / "xlsx" / "evals").mkdir()
    (_skill_dir / "evals.json").write_text(json.dumps({"evals": [
        {"id": "save", "prompt": "save it", "needs": ["n1"]}]}), encoding="utf-8")
    report = coverage_report(load_graph(out), read_labels(tmp_path / "xlsx"), "xlsx")
    assert [g["id"] for g in report["gaps"]] == ["n2"] and not report["problems"]
    assert "3 nodes (core 1, rare_critical 1, out_of_scope 1)" in result.stdout


def test_an_existing_out_is_refused_and_left_untouched(tmp_path, monkeypatch, capsys):
    prompts = _stub(monkeypatch, REPLY)
    out = tmp_path / "graph.json"
    out.write_text("frozen", encoding="utf-8")

    result = _run(capsys, "--artifact", str(_skill(tmp_path)), "--out", str(out))

    assert result.returncode == 2 and "frozen" in result.stderr
    assert out.read_text(encoding="utf-8") == "frozen" and prompts == []


def test_a_text_artifact_with_no_description_is_refused_before_any_call(tmp_path, monkeypatch, capsys):
    prompts = _stub(monkeypatch, REPLY)
    notes = tmp_path / "notes.md"
    notes.write_text("just text", encoding="utf-8")

    result = _run(capsys, "--artifact", str(notes), "--out", str(tmp_path / "g.json"))

    assert result.returncode == 2 and "no description" in result.stderr and prompts == []


def test_an_empty_graph_exits_1_and_writes_nothing(tmp_path, monkeypatch, capsys):
    _stub(monkeypatch, {"nodes": []})
    out = tmp_path / "g.json"

    result = _run(capsys, "--artifact", str(_skill(tmp_path)), "--out", str(out))

    assert result.returncode == 1 and "nothing was written" in result.stderr
    assert not out.exists()


def test_prompt_version_tracks_the_graph_prompt():
    assert len(PROMPT_VERSION) == 12 and int(PROMPT_VERSION, 16) >= 0


def test_a_failed_call_says_so_instead_of_reporting_an_empty_graph(tmp_path, monkeypatch, capsys):
    def down(self, prompt, system, *, accept=None, draw=0):
        raise RuntimeError("Not logged in")

    monkeypatch.setattr(LLMClient, "_default_transport", down)
    out = tmp_path / "g.json"

    result = _run(capsys, "--artifact", str(_skill(tmp_path)), "--out", str(out))

    assert result.returncode == 1 and "call failed: Not logged in" in result.stderr
    assert not out.exists()
