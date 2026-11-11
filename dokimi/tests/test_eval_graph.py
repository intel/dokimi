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

    result = _run(capsys, str(_skill(tmp_path)), "--out", str(out), "--extract-model", "anthropic/m1")

    assert result.returncode == 0, result.stderr
    assert len(prompts) == 1 and "Edit Excel workbooks safely" in prompts[0]
    assert BODY_MARKER not in prompts[0]
    data = load_artifact("need_graph", out)
    assert data["provenance"] == {"model": "anthropic/m1", "prompt_version": PROMPT_VERSION, "draws": 1}
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

    result = _run(capsys, str(_skill(tmp_path)), "--out", str(out))

    assert result.returncode == 2 and "frozen" in result.stderr
    assert out.read_text(encoding="utf-8") == "frozen" and prompts == []


def test_a_file_with_no_frontmatter_is_refused_before_any_call(tmp_path, monkeypatch, capsys):
    prompts = _stub(monkeypatch, REPLY)
    notes = tmp_path / "notes.md"
    notes.write_text("just text", encoding="utf-8")

    result = _run(capsys, str(notes), "--out", str(tmp_path / "g.json"))

    assert result.returncode == 2 and "cannot load" in result.stderr and prompts == []


def test_an_empty_graph_exits_1_and_writes_nothing(tmp_path, monkeypatch, capsys):
    _stub(monkeypatch, {"nodes": []})
    out = tmp_path / "g.json"

    result = _run(capsys, str(_skill(tmp_path)), "--out", str(out))

    assert result.returncode == 1 and "nothing was written" in result.stderr
    assert not out.exists()


def test_prompt_version_is_a_full_sha256():
    assert PROMPT_VERSION.startswith("sha256:") and len(PROMPT_VERSION) == 7 + 64


def test_a_file_written_during_the_call_is_not_replaced(tmp_path, monkeypatch, capsys):
    out = tmp_path / "g.json"

    def racer(self, prompt, system, *, accept=None, draw=0):
        out.write_text("other run", encoding="utf-8")
        return json.dumps(REPLY), None

    monkeypatch.setattr(LLMClient, "_default_transport", racer)

    result = _run(capsys, str(_skill(tmp_path)), "--out", str(out))

    assert result.returncode == 2 and "frozen" in result.stderr
    assert out.read_text(encoding="utf-8") == "other run"
    assert [p.name for p in tmp_path.iterdir() if p.name.endswith(".staged")] == []


def test_a_bad_model_ref_is_a_usage_error(capsys):
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(["eval", "graph", "x", "--out", "g", "--extract-model", "claude"])
    assert exc.value.code == 2


def test_a_failed_call_says_so_instead_of_reporting_an_empty_graph(tmp_path, monkeypatch, capsys):
    def down(self, prompt, system, *, accept=None, draw=0):
        raise RuntimeError("Not logged in")

    monkeypatch.setattr(LLMClient, "_default_transport", down)
    out = tmp_path / "g.json"

    result = _run(capsys, str(_skill(tmp_path)), "--out", str(out))

    assert result.returncode == 1 and "call failed: Not logged in" in result.stderr
    assert not out.exists()


def test_repeated_ids_and_odd_casing_still_build_one_scorable_graph(tmp_path, monkeypatch, capsys):
    _stub(monkeypatch, {"nodes": [
        {"id": "n1", "text": "save", "path_class": "Core"},
        {"id": "n1", "text": "recover", "path_class": "Rare-Critical"},
        {"text": "plot", "path_class": "out of scope"},
    ]})
    out = tmp_path / "g.json"

    result = _run(capsys, str(_skill(tmp_path)), "--out", str(out))

    assert result.returncode == 0, result.stderr
    nodes = load_artifact("need_graph", out)["nodes"]
    assert [n["id"] for n in nodes] == ["n1", "n2-1", "n3"]
    assert [n["path_class"] for n in nodes] == ["core", "rare_critical", "out_of_scope"]
    load_graph(out)  # eval coverage accepts it: no duplicate id


def test_a_symlink_at_out_is_refused(tmp_path, monkeypatch, capsys):
    prompts = _stub(monkeypatch, REPLY)
    out = tmp_path / "g.json"
    out.symlink_to(tmp_path / "missing.json")

    result = _run(capsys, str(_skill(tmp_path)), "--out", str(out))

    assert result.returncode == 2 and "frozen" in result.stderr and prompts == []


def test_a_missing_out_dir_is_made_and_an_unwritable_one_refused_before_the_call(tmp_path, monkeypatch, capsys):
    prompts = _stub(monkeypatch, REPLY)
    skill = _skill(tmp_path)

    made = _run(capsys, str(skill), "--out", str(tmp_path / "new" / "g.json"))
    assert made.returncode == 0 and (tmp_path / "new" / "g.json").is_file()

    locked = tmp_path / "locked"
    locked.mkdir(mode=0o500)
    try:
        refused = _run(capsys, str(skill), "--out", str(locked / "g.json"))
    finally:
        locked.chmod(0o700)
    assert refused.returncode == 2 and "cannot write to" in refused.stderr and len(prompts) == 1



def test_prompt_version_changes_with_the_prompt():
    from dokimi.novelty.needs import _GRAPH_PROMPT, _GRAPH_SYSTEM, _prompt_version

    assert PROMPT_VERSION == _prompt_version(_GRAPH_SYSTEM, _GRAPH_PROMPT)
    assert _prompt_version(_GRAPH_SYSTEM, _GRAPH_PROMPT + " ") != PROMPT_VERSION
    assert _prompt_version("a", "bc") != _prompt_version("ab", "c")


def test_no_hard_links_falls_back_to_an_exclusive_create(tmp_path, monkeypatch, capsys):
    import os

    _stub(monkeypatch, REPLY)

    def no_links(src, dst):
        raise PermissionError(1, "Operation not permitted")  # vfat, SMB

    monkeypatch.setattr(os, "link", no_links)
    out = tmp_path / "g.json"

    result = _run(capsys, str(_skill(tmp_path)), "--out", str(out), "--extract-model", "gemini/x")

    assert result.returncode == 0, result.stderr
    assert load_artifact("need_graph", out)["provenance"]["model"] == "google-vertex/x"
    assert [p.name for p in tmp_path.iterdir() if p.name.endswith(".staged")] == []


def test_a_failed_write_exits_2_and_leaves_no_staged_file(tmp_path, monkeypatch, capsys):
    from dokimi import artifacts

    _stub(monkeypatch, REPLY)

    def full(*a, **k):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(artifacts, "save_artifact", full)

    result = _run(capsys, str(_skill(tmp_path)), "--out", str(tmp_path / "g.json"))

    assert result.returncode == 2 and "cannot write" in result.stderr and "No space" in result.stderr
    assert not (tmp_path / "g.json").exists()
