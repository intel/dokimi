# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The effective-instructions receipt of a reach probe (no LLM): its mapping, its schema and the CLI."""

from __future__ import annotations

import hashlib
import json
import subprocess

import pytest

from dokimi.artifacts import ArtifactValidationError, validate_artifact
from dokimi.cli import build_parser
from dokimi.repos import WorkTree
from dokimi.repos.receipt import build_receipt, check_receipt_provenance, check_saved_reach, source_digests
from dokimi.repos.reach import ReachError, format_matrix, load_reach
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


CLASS_MAP = {  # reach class -> (observed, selected, loaded)
    "loaded": ("loaded", True, True),
    "fetched": ("fetched", True, "unknown"),
    "not_loaded": ("not_loaded", "unknown", False),
    "truncated": ("unknown", "unknown", "unknown"),
    "error": ("unknown", "unknown", "unknown"),
}


@pytest.mark.parametrize("cls", sorted(CLASS_MAP))
def test_each_reach_class_maps_to_its_observed_and_thread_states(tmp_path, cls):
    tree = _tree(tmp_path, {"AGENTS.md": "a\n", "CLAUDE.md": "c\n", "sub/AGENTS.md": "s\n"})
    reach = _reach([_record("AGENTS.md", "cwd=.", cls, read_by_tool=cls == "fetched")], [ROOT_ROW])
    src = _sources(build_receipt(reach, {"entries": []}, tree), "cwd=.")["AGENTS.md"]
    observed, selected, loaded = CLASS_MAP[cls]
    assert src["observed"] == observed
    assert src["observation_method"] == "unknown"  # the thread vocabulary: no runtime event backs the probe
    assert src["dokimi_method"] == "marker_self_report"  # the probe saw a marker record for this source
    assert (src["states"]["selected"], src["states"]["loaded"]) == (selected, loaded)
    assert src["states"]["followed"] == "unknown"


def test_a_source_the_probe_did_not_record_has_no_dokimi_method(tmp_path):
    tree = _tree(tmp_path, {"AGENTS.md": "a\n", "CLAUDE.md": "c\n", "docs/style.md": "d\n"})
    reach = _reach([_record("AGENTS.md", "cwd=.", "loaded")], [ROOT_ROW])
    receipt = build_receipt(reach, BUDGET, tree)
    src = _sources(receipt, "cwd=.")
    assert src["docs/style.md"]["dokimi_method"] is None and src["docs/style.md"]["observation_method"] == "unknown"
    assert receipt["observation"] == {"method": "unknown", "dokimi_method": "marker_self_report",
                                      "detail": receipt["observation"]["detail"]}


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
    ("/home/alice@corp/src/toy", "toy"),
    ("/home/alice@corp.com/toy@4ff761e2d943", "toy@4ff761e2d943"),
    ("C:\\Users\\alice@corp\\toy", "toy"),
    ("../alice/private/toy@4ff761e2d943", "toy@4ff761e2d943"),
    ("alice/private/toy", "toy"),
    ("file:///home/alice/toy.git@4ff761e2d943", "toy.git@4ff761e2d943"),
    ("git@github.com:org/toy.git@4ff761e2d943", "git@github.com:org/toy.git@4ff761e2d943"),
    ("ssh://git@example.com/org/toy.git@4ff761e2d943", "ssh://git@example.com/org/toy.git@4ff761e2d943"),
    ("https://alice:s3cret@example.com/org/toy.git@4ff761e2d943", "https://example.com/org/toy.git@4ff761e2d943"),
    ("https://alice@example.com/org/toy.git", "https://example.com/org/toy.git"),
    ("ssh://alice:s3cret@example.com/org/toy.git", "ssh://example.com/org/toy.git"),
    ("https://alice:p@ss@example.com/org/toy.git", "https://example.com/org/toy.git"),
])
def test_the_tree_label_keeps_no_local_directory(tmp_path, label, shared):
    tree = _tree(tmp_path, {"AGENTS.md": "a\n"})
    reach = _reach([_record("AGENTS.md", "cwd=.", "loaded")], [ROOT_ROW])
    reach["repo"]["tree"] = label
    receipt = build_receipt(reach, {"entries": []}, tree)
    assert receipt["repo"]["tree"] == shared
    assert "alice" not in json.dumps(receipt)


def test_a_working_tree_receipt_says_its_source_is_read_after_the_run(tmp_path):
    tree = _tree(tmp_path, {"AGENTS.md": "a\n"})
    receipt = build_receipt(_reach([_record("AGENTS.md", "cwd=.", "loaded")], [ROOT_ROW]), {"entries": []}, tree)
    assert any("working tree" in u and "pin the source" in u for u in receipt["unknowns"])


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
    assert any("working tree" in u for u in data["unknowns"])  # live on a working tree, unpinned
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


def _from_argv(live: list[str], *extra: str) -> list[str]:
    """The argv that reads a saved reach.json against the same source and harness as a live run's argv."""
    return ["repo", "reach", live[2], "--manifest", "evals/repo-eval.yaml", "--harness",
            live[live.index("--harness") + 1], *extra]


def _saved(tmp_path) -> str:
    return str(tmp_path / "out" / "reach.json")


def _pinned(live: list[str]) -> list[str]:
    """The same argv with its source committed and pinned to HEAD: a receipt needs a commit on both sides."""
    root = live[2]
    for args in (["init", "-q"], ["add", "-A"],
                 ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--no-gpg-sign", "-m", "x"]):
        subprocess.run(["git", "-C", root, *args], check=True, capture_output=True)
    return [live[0], live[1], f"{root}@HEAD", *live[3:]]


def test_from_a_saved_reach_gives_the_receipt_of_the_live_run(tmp_path, monkeypatch, capsys):
    live = _pinned(_end_to_end_args(tmp_path, monkeypatch, "--format", "receipt"))
    args = build_parser().parse_args(live)
    args.func(args)
    live_receipt = json.loads(capsys.readouterr().out)
    assert not any("working tree" in u for u in live_receipt["unknowns"])  # pinned: nothing is read after the run
    args = build_parser().parse_args(_from_argv(live, "--from", _saved(tmp_path), "--format", "receipt"))
    args.func(args)
    assert json.loads(capsys.readouterr().out) == live_receipt
    assert (tmp_path / "out" / "effective_instructions.json").exists()  # beside the saved file


def test_from_prints_the_matrix_of_the_saved_probe_and_runs_nothing(tmp_path, monkeypatch, capsys):
    live = _end_to_end_args(tmp_path, monkeypatch)
    args = build_parser().parse_args(live)
    args.func(args)
    capsys.readouterr()

    def no_probe(**_kwargs):
        raise AssertionError("a saved probe must not run the harness")

    monkeypatch.setattr("dokimi.repos.reach.run_reach", no_probe)
    args = build_parser().parse_args(_from_argv(live, "--from", _saved(tmp_path)))
    args.func(args)
    assert capsys.readouterr().out == format_matrix(load_reach(tmp_path / "out" / "reach.json")) + "\n"


def test_from_a_missing_file_is_a_usage_error(tmp_path, monkeypatch, capsys):
    live = _end_to_end_args(tmp_path, monkeypatch)
    args = build_parser().parse_args(_from_argv(live, "--from", str(tmp_path / "nope.json")))
    with pytest.raises(SystemExit) as exc:
        args.func(args)
    assert exc.value.code == 2 and "nope.json" in capsys.readouterr().err


@pytest.mark.parametrize("text", ["{not json", "[1, 2]", '{"schema": "guidance_reach.v1"}'])
def test_from_a_malformed_or_wrong_shaped_file_is_a_usage_error(tmp_path, monkeypatch, capsys, text):
    live = _end_to_end_args(tmp_path, monkeypatch)
    bad = tmp_path / "bad.json"
    bad.write_text(text, encoding="utf-8")
    args = build_parser().parse_args(_from_argv(live, "--from", str(bad)))
    with pytest.raises(SystemExit) as exc:
        args.func(args)
    assert exc.value.code == 2 and capsys.readouterr().err.startswith("\u2717")


def test_from_refuses_a_saved_probe_of_another_repository_or_harness(tmp_path, monkeypatch, capsys):
    live = _end_to_end_args(tmp_path, monkeypatch)
    build_parser().parse_args(live).func(build_parser().parse_args(live))
    data = json.loads((tmp_path / "out" / "reach.json").read_text(encoding="utf-8"))
    for key, value, text in (("repo", {**data["repo"], "id": "other"}, "for repository 'other'"),
                             ("harness", {**data["harness"], "name": "codex"}, "harness 'codex'")):
        other = tmp_path / f"{key}.json"
        other.write_text(json.dumps({**data, key: value}), encoding="utf-8")
        args = build_parser().parse_args(_from_argv(live, "--from", str(other)))
        with pytest.raises(SystemExit) as exc:
            args.func(args)
        assert exc.value.code == 2 and text in capsys.readouterr().err


def test_from_refuses_the_probe_only_flags(tmp_path, monkeypatch, capsys):
    live = _end_to_end_args(tmp_path, monkeypatch)
    args = build_parser().parse_args(_from_argv(live, "--from", _saved(tmp_path), "--budget", "0.5", "--model", "m"))
    with pytest.raises(SystemExit) as exc:
        args.func(args)
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "--model, --budget" in err  # both named, in one message


def test_a_probe_needs_a_model(tmp_path, monkeypatch, capsys):
    live = _end_to_end_args(tmp_path, monkeypatch)
    no_model = live[:live.index("--model")] + live[live.index("--model") + 2:]
    args = build_parser().parse_args(no_model)
    with pytest.raises(SystemExit) as exc:
        args.func(args)
    assert exc.value.code == 2 and "--model is required" in capsys.readouterr().err


def test_a_saved_probe_must_match_the_commit_it_is_read_against(tmp_path):
    reach = _reach([_record("AGENTS.md", "cwd=.", "loaded")], [ROOT_ROW])
    reach["repo"]["sha"] = "a" * 40
    check_saved_reach(reach, "toy", "claude-code", "a" * 40)  # the same commit is accepted
    check_saved_reach(reach, "toy", "claude-code", None)  # a tree without a commit cannot be compared
    with pytest.raises(ReachError, match="probed at aaaaaaaaaaaa, the source is at bbbbbbbbbbbb"):
        check_saved_reach(reach, "toy", "claude-code", "b" * 40)


def test_a_receipt_needs_a_pinned_probe_and_a_pinned_source(tmp_path):
    reach = _reach([_record("AGENTS.md", "cwd=.", "loaded")], [ROOT_ROW])
    reach["repo"]["sha"] = "a" * 40
    check_receipt_provenance(reach, "a" * 40)  # both at the same commit: the digests describe the probed text
    with pytest.raises(ReachError, match="pinned"):  # a working tree can change after the probe
        check_receipt_provenance(reach, None)
    reach["repo"]["sha"] = None  # probed from a working tree: its observations have no commit to check against
    with pytest.raises(ReachError, match="pinned"):
        check_receipt_provenance(reach, "a" * 40)


def test_from_a_receipt_refuses_a_pinned_probe_read_against_a_working_tree(tmp_path, monkeypatch, capsys):
    live = _pinned(_end_to_end_args(tmp_path, monkeypatch))
    build_parser().parse_args(live).func(build_parser().parse_args(live))  # probed at HEAD: repo.sha is set
    capsys.readouterr()
    working = _from_argv(live, "--from", _saved(tmp_path), "--format", "receipt")
    working[2] = str(tmp_path / "repo")  # the same directory, read as a working tree (no revision)
    args = build_parser().parse_args(working)
    with pytest.raises(SystemExit) as exc:
        args.func(args)
    assert exc.value.code == 2 and "--format receipt needs a pinned source" in capsys.readouterr().err


def test_from_a_receipt_refuses_an_unpinned_source_where_the_matrix_runs(tmp_path, monkeypatch, capsys):
    live = _end_to_end_args(tmp_path, monkeypatch)
    build_parser().parse_args(live).func(build_parser().parse_args(live))  # an unpinned probe: repo.sha is null
    capsys.readouterr()
    args = build_parser().parse_args(_from_argv(live, "--from", _saved(tmp_path), "--format", "receipt"))
    with pytest.raises(SystemExit) as exc:
        args.func(args)
    err = capsys.readouterr().err
    assert exc.value.code == 2 and "--format receipt needs a pinned source" in err
    assert not (tmp_path / "out" / "effective_instructions.json").exists()
    args = build_parser().parse_args(_from_argv(live, "--from", _saved(tmp_path)))  # the matrix needs no commit
    args.func(args)
    assert capsys.readouterr().out == format_matrix(load_reach(tmp_path / "out" / "reach.json")) + "\n"


def test_from_a_receipt_refuses_an_unpinned_probe_read_against_a_pinned_source(tmp_path, monkeypatch, capsys):
    live = _end_to_end_args(tmp_path, monkeypatch)
    build_parser().parse_args(live).func(build_parser().parse_args(live))  # probed from a working tree: repo.sha null
    capsys.readouterr()
    pinned = _pinned(live)  # the same repository, committed, now read at HEAD
    args = build_parser().parse_args(_from_argv(pinned, "--from", _saved(tmp_path), "--format", "receipt"))
    with pytest.raises(SystemExit) as exc:
        args.func(args)
    err = capsys.readouterr().err
    assert exc.value.code == 2 and "probed from a working tree (repo.sha is null)" in err


def test_a_lazy_guidance_is_expected_only_in_the_subtree_it_activates(tmp_path):
    tree = _tree(tmp_path, {"shared.md": "x\n", "sub/AGENTS.md": "s\n"})
    records = [_record(f, s, "not_loaded") for f in ("shared.md", "sub/AGENTS.md") for s in ("cwd=.", "read=sub")]
    files = [{"path": "shared.md", "head": "RC000001", "tail": "RC000002", "aliases": ["sub/CLAUDE.md"]},
             {"path": "sub/AGENTS.md", "head": "RC000005", "tail": "RC000006"}]
    budget = {"entries": [  # sub/CLAUDE.md is a link to shared.md: budget's lazy is the link's activation, not shared.md's
        {"path": "sub/CLAUDE.md", "kind": "guidance", "when": "lazy", "chars": 10},
        {"path": "sub/AGENTS.md", "kind": "guidance", "when": "lazy", "chars": 10}]}
    receipt = build_receipt(_reach(records, [ROOT_ROW, SUB_ROW], files=files), budget, tree)
    root, sub = _sources(receipt, "cwd=."), _sources(receipt, "read=sub")
    assert root["sub/CLAUDE.md"]["expected"] == "unknown" and sub["sub/CLAUDE.md"]["expected"] == "unknown"
    assert root["shared.md"]["expected"] == "unknown" and sub["shared.md"]["expected"] == "unknown"
    assert root["sub/AGENTS.md"]["expected"] is False and sub["sub/AGENTS.md"]["expected"] is True


def test_a_start_up_skill_listing_is_a_source_and_its_body_is_not(tmp_path):
    tree = _tree(tmp_path, {"CLAUDE.md": "c\n", ".claude/skills/fmt/SKILL.md": "---\nname: fmt\n---\n"})
    budget = {"entries": [
        {"path": "CLAUDE.md", "kind": "guidance", "when": "start", "chars": 10},
        {"path": ".claude/skills/fmt/SKILL.md", "kind": "skill listing", "when": "start", "chars": 10},
        {"path": ".claude/skills/fmt/SKILL.md", "kind": "skill body", "when": "on demand", "chars": 10}]}
    files = [{"path": "CLAUDE.md", "head": "RC000003", "tail": "RC000004"}]
    receipt = build_receipt(_reach([_record("CLAUDE.md", "cwd=.", "loaded")], [ROOT_ROW], files=files), budget, tree)
    sources = receipt["receipts"][0]["sources"]
    skill = [s for s in sources if s["path"] == ".claude/skills/fmt/SKILL.md"]
    assert len(skill) == 1 and skill[0]["expected"] is True and skill[0]["observed"] == "unknown"
    assert skill[0]["sha256"] is not None


@pytest.mark.parametrize("fmt, drop, key", [
    ("receipt", "salt", "salt"),
    ("receipt", "generated_at", "generated_at"),
    ("matrix", "matrix", "summary.matrix"),
])
def test_a_schema_valid_probe_without_what_its_format_needs_is_a_usage_error(tmp_path, monkeypatch, capsys,
                                                                            fmt, drop, key):
    live = _end_to_end_args(tmp_path, monkeypatch)
    build_parser().parse_args(live).func(build_parser().parse_args(live))
    data = json.loads((tmp_path / "out" / "reach.json").read_text(encoding="utf-8"))
    if drop == "matrix":
        del data["summary"]["matrix"]
    else:
        del data[drop]
    bad = tmp_path / "partial.json"
    bad.write_text(json.dumps(data), encoding="utf-8")
    argv = _from_argv(live, "--from", str(bad), *(["--format", "receipt"] if fmt == "receipt" else []))
    args = build_parser().parse_args(argv)
    with pytest.raises(SystemExit) as exc:
        args.func(args)
    err = capsys.readouterr().err
    assert exc.value.code == 2 and key in err and "Traceback" not in err


@pytest.mark.parametrize("link", [False, True], ids=["renamed", "symlink"])
def test_from_never_writes_the_receipt_over_the_probe_it_reads(tmp_path, monkeypatch, capsys, link):
    live = _pinned(_end_to_end_args(tmp_path, monkeypatch))
    build_parser().parse_args(live).func(build_parser().parse_args(live))
    capsys.readouterr()
    saved = tmp_path / "out" / "reach.json"
    before = saved.read_text(encoding="utf-8")
    receipt = tmp_path / "out" / "effective_instructions.json"
    if link:
        receipt.symlink_to(saved)
    else:
        saved.rename(receipt)
        saved = receipt
    args = build_parser().parse_args(_from_argv(live, "--from", str(saved), "--format", "receipt"))
    with pytest.raises(SystemExit) as exc:
        args.func(args)
    assert exc.value.code == 2 and "Traceback" not in capsys.readouterr().err
    assert receipt.resolve().read_text(encoding="utf-8") == before


def test_a_receipt_that_cannot_be_written_is_a_runtime_failure_not_a_traceback(tmp_path, monkeypatch, capsys):
    live = _pinned(_end_to_end_args(tmp_path, monkeypatch))
    build_parser().parse_args(live).func(build_parser().parse_args(live))
    capsys.readouterr()
    (tmp_path / "out" / "effective_instructions.json").mkdir()  # a directory where the receipt goes
    args = build_parser().parse_args(_from_argv(live, "--from", _saved(tmp_path), "--format", "receipt"))
    with pytest.raises(SystemExit) as exc:
        args.func(args)
    err = capsys.readouterr().err
    assert exc.value.code == 1 and "cannot write" in err and "Traceback" not in err


def test_a_live_receipt_that_cannot_be_written_is_a_runtime_failure(tmp_path, monkeypatch, capsys):
    live = _end_to_end_args(tmp_path, monkeypatch, "--format", "receipt")
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "effective_instructions.json").mkdir()
    args = build_parser().parse_args(live)
    with pytest.raises(SystemExit) as exc:
        args.func(args)
    assert exc.value.code == 1 and "cannot write" in capsys.readouterr().err


def test_a_matrix_of_a_probe_saved_without_unknown_tokens_renders(tmp_path, monkeypatch, capsys):
    live = _end_to_end_args(tmp_path, monkeypatch)
    build_parser().parse_args(live).func(build_parser().parse_args(live))
    data = json.loads((tmp_path / "out" / "reach.json").read_text(encoding="utf-8"))
    for scenario in data["scenarios"]:
        scenario.pop("unknown", None)  # schema-optional
    (tmp_path / "out" / "bare.json").write_text(json.dumps(data), encoding="utf-8")
    capsys.readouterr()
    args = build_parser().parse_args(_from_argv(live, "--from", str(tmp_path / "out" / "bare.json")))
    args.func(args)
    assert capsys.readouterr().out == format_matrix(data) + "\n"


def test_a_lazy_guidance_imported_by_a_nested_file_is_expected_only_where_that_file_is(tmp_path):
    tree = _tree(tmp_path, {"sub/CLAUDE.md": "@../other/AGENTS.md\n", "other/AGENTS.md": "o\n"})
    records = [_record(f, s, "not_loaded") for f in ("other/AGENTS.md", "sub/CLAUDE.md") for s in ("cwd=.", "read=sub")]
    files = [{"path": "other/AGENTS.md", "head": "RC000001", "tail": "RC000002"},
             {"path": "sub/CLAUDE.md", "head": "RC000003", "tail": "RC000004"}]
    budget = {"entries": [  # the import activates other/AGENTS.md in sub/, not where the file lies
        {"path": "sub/CLAUDE.md", "kind": "guidance", "when": "lazy", "chars": 10},
        {"path": "other/AGENTS.md", "kind": "guidance", "when": "lazy", "chars": 10}]}
    receipt = build_receipt(_reach(records, [ROOT_ROW, SUB_ROW], files=files), budget, tree)
    root, sub = _sources(receipt, "cwd=."), _sources(receipt, "read=sub")
    assert root["other/AGENTS.md"]["expected"] == "unknown" and sub["other/AGENTS.md"]["expected"] == "unknown"
    assert root["sub/CLAUDE.md"]["expected"] is False and sub["sub/CLAUDE.md"]["expected"] is True


def test_accessed_is_false_without_tools_unknown_when_a_read_is_unseen_and_true_when_seen(tmp_path):
    tree = _tree(tmp_path, {"AGENTS.md": "a\n"})
    seen = dict(SUB_ROW, id="read=seen")
    records = [_record("AGENTS.md", "cwd=.", "not_loaded"), _record("AGENTS.md", "read=sub", "not_loaded"),
               _record("AGENTS.md", "read=seen", "not_loaded", read_by_tool=True)]
    receipt = build_receipt(_reach(records, [ROOT_ROW, SUB_ROW, seen]), {"entries": []}, tree)
    accessed = {r["run_id"].split(":")[1]: _sources(receipt, r["run_id"].split(":")[1])["AGENTS.md"]["states"]
                ["accessed"] for r in receipt["receipts"]}
    assert accessed == {"cwd=.": False, "read=sub": "unknown", "read=seen": True}


def test_a_lazy_subtree_file_is_expected_in_its_directory_and_below_not_in_a_sibling(tmp_path):
    tree = _tree(tmp_path, {"sub/AGENTS.md": "s\n"})
    deep = dict(SUB_ROW, id="read=sub/deeper", read="sub/deeper/REACH_PROBE.txt")
    sibling = dict(SUB_ROW, id="read=sub2", read="sub2/REACH_PROBE.txt")
    rows = [ROOT_ROW, SUB_ROW, deep, sibling]
    records = [_record("sub/AGENTS.md", row["id"], "not_loaded") for row in rows]
    files = [{"path": "sub/AGENTS.md", "head": "RC000005", "tail": "RC000006"}]
    budget = {"entries": [{"path": "sub/AGENTS.md", "kind": "guidance", "when": "lazy", "chars": 10}]}
    receipt = build_receipt(_reach(records, rows, files=files), budget, tree)
    expected = {row["id"]: _sources(receipt, row["id"])["sub/AGENTS.md"]["expected"] for row in rows}
    assert expected == {"cwd=.": False, "read=sub": True, "read=sub/deeper": True, "read=sub2": False}


def test_a_nested_link_has_the_expectation_of_the_file_it_links_to(tmp_path):
    tree = _tree(tmp_path, {"sub/AGENTS.md": "s\n"})
    records = [_record("sub/AGENTS.md", r, "not_loaded") for r in ("cwd=.", "read=sub")]
    files = [{"path": "sub/AGENTS.md", "head": "RC000005", "tail": "RC000006", "aliases": ["sub/CLAUDE.md"]}]
    budget = {"entries": [{"path": "sub/AGENTS.md", "kind": "guidance", "when": "lazy", "chars": 10}]}
    receipt = build_receipt(_reach(records, [ROOT_ROW, SUB_ROW], files=files), budget, tree)
    root, sub = _sources(receipt, "cwd=."), _sources(receipt, "read=sub")
    assert root["sub/CLAUDE.md"]["scope"] == "adapter" and sub["sub/CLAUDE.md"]["scope"] == "adapter"
    assert root["sub/CLAUDE.md"]["expected"] is False and sub["sub/CLAUDE.md"]["expected"] is True


@pytest.mark.parametrize("path, scope", [(".claude/CLAUDE.md", "repo"), (".claude/AGENTS.md", "repo"),
                                         ("docs/CLAUDE.md", "subtree"), ("README.md", "repo")])
def test_project_memory_in_dot_claude_applies_to_the_repo_not_a_subtree(tmp_path, path, scope):
    tree = _tree(tmp_path, {path: "x\n"})
    files = [{"path": path, "head": "RC000001", "tail": "RC000002"}]
    receipt = build_receipt(_reach([_record(path, "cwd=.", "loaded")], [ROOT_ROW], files=files), {"entries": []}, tree)
    assert _sources(receipt, "cwd=.")[path]["scope"] == scope


@pytest.mark.parametrize("bad", [{"CLAUDE.md": None}, {"CLAUDE.md": {"cwd=.": "bogus"}}],
                         ids=["row-not-an-object", "cell-not-a-class"])
def test_a_saved_matrix_with_a_malformed_row_or_cell_is_a_usage_error(tmp_path, monkeypatch, capsys, bad):
    live = _end_to_end_args(tmp_path, monkeypatch)
    build_parser().parse_args(live).func(build_parser().parse_args(live))
    data = json.loads((tmp_path / "out" / "reach.json").read_text(encoding="utf-8"))
    data["summary"]["matrix"] = bad
    (tmp_path / "out" / "malformed.json").write_text(json.dumps(data), encoding="utf-8")
    capsys.readouterr()
    args = build_parser().parse_args(_from_argv(live, "--from", str(tmp_path / "out" / "malformed.json")))
    with pytest.raises(SystemExit) as exc:
        args.func(args)
    err = capsys.readouterr().err
    assert exc.value.code == 2 and "cannot be shown as a matrix" in err and "Traceback" not in err


def test_a_receipt_output_link_that_loops_is_a_usage_error_before_the_harness_runs(tmp_path, monkeypatch, capsys):
    live = _end_to_end_args(tmp_path, monkeypatch, "--format", "receipt")
    (tmp_path / "out").mkdir()
    loop = tmp_path / "out" / "effective_instructions.json"
    loop.symlink_to(loop)  # a link to itself: it cannot be resolved
    args = build_parser().parse_args(live)
    with pytest.raises(SystemExit) as exc:
        args.func(args)
    err = capsys.readouterr().err
    assert exc.value.code == 2 and "cannot resolve" in err and "Traceback" not in err
    assert not (tmp_path / "out" / "reach.json").exists()  # the probe never ran


def test_from_a_receipt_output_link_that_loops_is_a_usage_error_not_a_traceback(tmp_path, monkeypatch, capsys):
    live = _pinned(_end_to_end_args(tmp_path, monkeypatch))
    build_parser().parse_args(live).func(build_parser().parse_args(live))
    capsys.readouterr()
    loop = tmp_path / "out" / "effective_instructions.json"
    loop.symlink_to(loop)
    args = build_parser().parse_args(_from_argv(live, "--from", _saved(tmp_path), "--format", "receipt"))
    with pytest.raises(SystemExit) as exc:
        args.func(args)
    err = capsys.readouterr().err
    assert exc.value.code == 2 and "cannot resolve" in err and "Traceback" not in err


@pytest.mark.parametrize("cls", ["truncated", "error"])
def test_the_unknowns_name_a_truncated_record_and_an_error_record_apart(tmp_path, cls):
    tree = _tree(tmp_path, {"AGENTS.md": "a\n", "CLAUDE.md": "c\n", "sub/AGENTS.md": "s\n"})
    reach = _reach([_record("AGENTS.md", "cwd=.", cls)], [ROOT_ROW])
    run = next(r for r in build_receipt(reach, {"entries": []}, tree)["receipts"] if r["run_id"].endswith("cwd=."))
    text = " | ".join(run["unknowns"])
    assert ("AGENTS.md: one of its two marker tokens was reported" in text) == (cls == "truncated")
    assert ("AGENTS.md: the probe recorded an error for this file" in text) == (cls == "error")


@pytest.mark.parametrize("flags", [["--budget", "0.10"], ["--max-turns", "4"], ["--scenarios", "root,cwd,read"],
                                   ["--timeout", "300"]])
def test_from_refuses_a_probe_flag_given_at_its_default_value(tmp_path, monkeypatch, capsys, flags):
    live = _end_to_end_args(tmp_path, monkeypatch)
    args = build_parser().parse_args(_from_argv(live, "--from", _saved(tmp_path), *flags))
    with pytest.raises(SystemExit) as exc:
        args.func(args)
    assert exc.value.code == 2 and flags[0] in capsys.readouterr().err


def test_from_an_empty_file_name_is_a_usage_error_and_runs_no_probe(tmp_path, monkeypatch, capsys):
    live = _end_to_end_args(tmp_path, monkeypatch)
    args = build_parser().parse_args([*live, "--from", ""])  # a shell variable that holds no file name
    with pytest.raises(SystemExit) as exc:
        args.func(args)
    assert exc.value.code == 2 and "--from needs a file name" in capsys.readouterr().err
    assert not (tmp_path / "out" / "reach.json").exists()
