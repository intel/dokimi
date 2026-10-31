# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The effective-instructions receipt of a reach probe (no LLM): its mapping, its schema and the CLI."""

from __future__ import annotations

import hashlib
import json

import pytest

from dokimi.artifacts import ArtifactValidationError, validate_artifact
from dokimi.cli import build_parser
from dokimi.repos import WorkTree
from dokimi.repos.receipt import build_receipt, source_digests
from tests.test_repo_reach import _profile, _repo

ROOT_ROW = {"id": "cwd=.", "cwd": "", "tools": "none", "read": None, "error": None, "prose": 0, "unknown": []}
SUB_ROW = {"id": "read=sub", "cwd": "", "tools": "read", "read": "sub/REACH_PROBE.txt", "error": None,
           "prose": 0, "unknown": []}
BUDGET = {"entries": [
    {"path": "CLAUDE.md", "kind": "guidance", "when": "start", "chars": 10},
    {"path": "AGENTS.md", "kind": "guidance", "when": "off", "chars": 10},
    {"path": "sub/AGENTS.md", "kind": "guidance", "when": "lazy", "chars": 10},
    {"path": "docs/style.md", "kind": "import", "when": "start", "chars": 10},
]}


def _record(file: str, scenario: str, cls: str, read_by_tool: bool = False) -> dict:
    return {"file": file, "scenario": scenario, "harness": "claude-code", "harness_version": "2.1.0",
            "model": "m", "head": cls in ("loaded", "fetched", "truncated"), "tail": cls in ("loaded", "fetched"),
            "read_by_tool": read_by_tool, "class": cls}


def _reach(records: list[dict], scenarios: list[dict], harness: str = "claude-code",
           files: list[dict] | None = None) -> dict:
    files = files or [{"path": "AGENTS.md", "head": "RC000001", "tail": "RC000002"},
                      {"path": "CLAUDE.md", "head": "RC000003", "tail": "RC000004"},
                      {"path": "sub/AGENTS.md", "head": "RC000005", "tail": "RC000006"}]
    return {"generated_at": "2026-10-31T00:00:00+00:00", "repo": {"id": "toy", "tree": "toy", "sha": None,
            "manifest": "evals/repo-eval.yaml"}, "harness": {"name": harness, "version": "2.1.0"}, "model": "m",
            "salt": "salt", "files": files, "scenarios": scenarios, "records": records, "summary": {}}


def _tree(tmp_path, files: dict[str, str | bytes]) -> WorkTree:
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        if isinstance(text, bytes):
            (tmp_path / rel).write_bytes(text)
        else:
            (tmp_path / rel).write_text(text, encoding="utf-8")
    return WorkTree(tmp_path)


def _sources(receipt: dict, run_id: str) -> dict[str, dict]:
    run = next(r for r in receipt["receipts"] if r["run_id"].endswith(run_id))
    return {s["path"]: s for s in run["sources"]}


CLASS_MAP = {  # reach class -> (observed, observation_method, selected, loaded)
    "loaded": ("loaded", "prompt_capture", True, True),
    "fetched": ("fetched", "prompt_capture", True, "unknown"),
    "not_loaded": ("not_loaded", "prompt_capture", "unknown", False),
    "truncated": ("unknown", "prompt_capture", "unknown", "unknown"),
    "error": ("unknown", "unknown", "unknown", "unknown"),
}


@pytest.mark.parametrize("cls", sorted(CLASS_MAP))
def test_each_reach_class_maps_to_its_observed_and_thread_states(tmp_path, cls):
    tree = _tree(tmp_path, {"AGENTS.md": "a\n", "CLAUDE.md": "c\n", "sub/AGENTS.md": "s\n"})
    reach = _reach([_record("AGENTS.md", "cwd=.", cls, read_by_tool=cls == "fetched")], [ROOT_ROW])
    src = _sources(build_receipt(reach, {"entries": []}, tree), "cwd=.")["AGENTS.md"]
    observed, method, selected, loaded = CLASS_MAP[cls]
    assert (src["observed"], src["observation_method"]) == (observed, method)
    assert (src["states"]["selected"], src["states"]["loaded"]) == (selected, loaded)
    assert src["states"]["followed"] == "unknown"


def test_start_up_expectations_follow_budget_and_the_run_directory(tmp_path):
    tree = _tree(tmp_path, {"AGENTS.md": "a\n", "CLAUDE.md": "c\n", "sub/AGENTS.md": "s\n", "docs/style.md": "d\n"})
    records = [_record(f, s, "not_loaded") for f in ("AGENTS.md", "CLAUDE.md", "sub/AGENTS.md")
               for s in ("cwd=.", "read=sub")]
    reach = _reach(records, [ROOT_ROW, SUB_ROW])
    receipt = build_receipt(reach, BUDGET, tree)
    root = _sources(receipt, "cwd=.")
    assert root["CLAUDE.md"]["expected"] is True  # start-up
    assert root["AGENTS.md"]["expected"] is False  # off: a root CLAUDE.md displaces it
    assert root["sub/AGENTS.md"]["expected"] is False  # lazy, and the run starts at the root
    assert root["docs/style.md"]["expected"] is True and root["docs/style.md"]["observed"] == "unknown"
    assert _sources(receipt, "read=sub")["sub/AGENTS.md"]["expected"] is True  # the run reads inside its subtree


def test_a_displacement_seen_in_the_run_is_precedence_observed(tmp_path):
    tree = _tree(tmp_path, {"AGENTS.md": "a\n", "CLAUDE.md": "c\n", "sub/AGENTS.md": "s\n"})
    records = [_record("CLAUDE.md", "cwd=.", "loaded"), _record("AGENTS.md", "cwd=.", "not_loaded")]
    receipt = build_receipt(_reach(records, [ROOT_ROW]), BUDGET, tree)
    run = receipt["receipts"][0]
    assert run["precedence_observed"] == [{"winner": "CLAUDE.md", "displaced": "AGENTS.md"}]


def test_no_displacement_claim_without_the_winner_loaded(tmp_path):
    tree = _tree(tmp_path, {"AGENTS.md": "a\n", "CLAUDE.md": "c\n", "sub/AGENTS.md": "s\n"})
    records = [_record("CLAUDE.md", "cwd=.", "not_loaded"), _record("AGENTS.md", "cwd=.", "not_loaded")]
    assert build_receipt(_reach(records, [ROOT_ROW]), BUDGET, tree)["receipts"][0]["precedence_observed"] == []


def test_other_harnesses_get_no_expectations_and_say_so(tmp_path):
    tree = _tree(tmp_path, {"AGENTS.md": "a\n", "CLAUDE.md": "c\n"})
    records = [_record("AGENTS.md", "cwd=.", "loaded")]
    files = [{"path": "AGENTS.md", "head": "RC000001", "tail": "RC000002"},
             {"path": "CLAUDE.md", "head": "RC000003", "tail": "RC000004"}]
    receipt = build_receipt(_reach(records, [ROOT_ROW], harness="codex", files=files), BUDGET, tree)
    src = _sources(receipt, "cwd=.")
    assert set(src) == {"AGENTS.md", "CLAUDE.md"}  # budget's start-up chain is Claude Code's, so none is added
    assert all(s["expected"] == "unknown" for s in src.values())
    assert any("no source has an expected state for codex" in u for u in receipt["unknowns"])


def test_a_link_is_an_adapter_with_the_targets_observation(tmp_path):
    tree = _tree(tmp_path, {"AGENTS.md": "a\n", "sub/AGENTS.md": "s\n"})
    files = [{"path": "AGENTS.md", "head": "RC000001", "tail": "RC000002", "aliases": ["CLAUDE.md"]},
             {"path": "sub/AGENTS.md", "head": "RC000005", "tail": "RC000006"}]
    records = [_record("AGENTS.md", "cwd=.", "loaded")]
    src = _sources(build_receipt(_reach(records, [ROOT_ROW], files=files), {"entries": []}, tree), "cwd=.")
    assert src["CLAUDE.md"]["alias_of"] == "AGENTS.md"
    assert src["CLAUDE.md"]["scope"] == "adapter" and src["CLAUDE.md"]["observed"] == "loaded"
    assert src["sub/AGENTS.md"]["scope"] == "subtree" and src["sub/AGENTS.md"]["observed"] == "unknown"


def test_a_scenario_with_no_answer_is_unknown_and_says_why(tmp_path):
    tree = _tree(tmp_path, {"AGENTS.md": "a\n", "CLAUDE.md": "c\n"})
    row = dict(ROOT_ROW, error="exit 1")
    receipt = build_receipt(_reach([_record("AGENTS.md", "cwd=.", "error")], [row]), {"entries": []}, tree)
    run = receipt["receipts"][0]
    assert all(s["observed"] == "unknown" for s in run["sources"])
    assert any("no usable answer" in u for u in run["unknowns"])


def test_a_prose_heavy_or_unseeded_answer_is_flagged(tmp_path):
    tree = _tree(tmp_path, {"AGENTS.md": "a\n"})
    row = dict(ROOT_ROW, prose=40, unknown=["RC0000FF"])
    run = build_receipt(_reach([_record("AGENTS.md", "cwd=.", "not_loaded")], [row]), {"entries": []}, tree)
    text = " ".join(run["receipts"][0]["unknowns"])
    assert "under-reports" in text and "RC0000FF" in text


def test_digests_are_of_the_text_read_and_none_when_unreadable(tmp_path):
    tree = _tree(tmp_path, {"AGENTS.md": "a\r\nb\n", "blob.md": b"\xff\xfe\x00bin"})
    digests = source_digests(tree, ["AGENTS.md", "blob.md", "missing.md"])
    assert digests["AGENTS.md"] == hashlib.sha256("a\nb\n".encode("utf-8")).hexdigest()  # LF, as dokimi reads it
    assert digests["blob.md"] is None and digests["missing.md"] is None


@pytest.mark.parametrize("label, shared", [
    ("/home/alice/src/toy@4ff761e2d943", "toy@4ff761e2d943"),
    ("C:\\Users\\alice\\toy", "toy"),
    ("~alice/toy", "toy"),
    ("https://example.com/org/toy.git@4ff761e2d943", "https://example.com/org/toy.git@4ff761e2d943"),
    ("toy@4ff761e2d943", "toy@4ff761e2d943"),
])
def test_the_tree_label_keeps_no_local_directory(tmp_path, label, shared):
    tree = _tree(tmp_path, {"AGENTS.md": "a\n"})
    reach = _reach([_record("AGENTS.md", "cwd=.", "loaded")], [ROOT_ROW])
    reach["repo"]["tree"] = label
    receipt = build_receipt(reach, {"entries": []}, tree)
    assert receipt["repo"]["tree"] == shared
    assert "alice" not in json.dumps(receipt)


def test_the_receipt_is_a_valid_artifact_and_a_schema_rejects_a_followed_claim(tmp_path):
    tree = _tree(tmp_path, {"AGENTS.md": "a\n", "CLAUDE.md": "c\n"})
    receipt = build_receipt(_reach([_record("AGENTS.md", "cwd=.", "loaded")], [ROOT_ROW]), BUDGET, tree)
    validate_artifact("effective_instructions", receipt)
    receipt["receipts"][0]["sources"][0]["states"]["followed"] = "loaded"
    with pytest.raises(ArtifactValidationError, match="followed"):
        validate_artifact("effective_instructions", receipt)


def _end_to_end_args(tmp_path, monkeypatch, *extra):
    monkeypatch.setenv("FAKE_MODE", "ok")
    root = _repo(tmp_path)
    profile = _profile(tmp_path, name="claude-code", guidance={"names": ["CLAUDE.md", "AGENTS.md"]})
    work = tmp_path / "work"
    work.mkdir()
    return ["repo", "reach", str(root), "--manifest", "evals/repo-eval.yaml", "--harness", str(profile),
            "--model", "m", "--out", str(tmp_path / "out"), "--work-dir", str(work), "--salt", "s",
            "--scenarios", "root", *extra]


def test_cli_receipt_format_prints_and_writes_a_valid_receipt(tmp_path, monkeypatch, capsys):
    args = build_parser().parse_args(_end_to_end_args(tmp_path, monkeypatch, "--format", "receipt"))
    args.func(args)
    out = capsys.readouterr().out
    data = json.loads(out)
    validate_artifact("effective_instructions", data)
    assert json.loads((tmp_path / "out" / "effective_instructions.json").read_text(encoding="utf-8")) == data
    run = data["receipts"][0]
    assert run["run_id"] == "s:cwd=." and run["runtime"] == {"name": "claude-code", "version": "9.8.7"}
    states = {s["path"]: s["observed"] for s in run["sources"]}
    assert states["CLAUDE.md"] == "loaded" and states["AGENTS.md"] == "not_loaded"
    # budget turns off every AGENTS.md while a root CLAUDE.md exists, nested ones included
    assert sorted(p["displaced"] for p in run["precedence_observed"]) == ["AGENTS.md", "sub/AGENTS.md"]
    assert all(p["winner"] == "CLAUDE.md" for p in run["precedence_observed"])
    assert out.lstrip().startswith("{") and "wrote" not in out  # the matrix is not printed in receipt mode


def test_default_format_still_prints_the_matrix(tmp_path, monkeypatch, capsys):
    args = build_parser().parse_args(_end_to_end_args(tmp_path, monkeypatch))
    args.func(args)
    out = capsys.readouterr().out
    assert "wrote" in out and not (tmp_path / "out" / "effective_instructions.json").exists()
