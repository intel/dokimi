# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`repo run` skill arms (skill:, skill-catalog:, joined with guidance by +) and cache tokens. No docker, no LLM."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dokimi.cli import build_parser
from dokimi.repos.arms import ArmError, check_arms, parse_arm, resolve_skills, skill_usage
from dokimi.repos.events import convert
from dokimi.repos.report import format_report, summarize
from dokimi.repos.workspace import Task
from repo_eval_fixture import FakeRunner, fake_harness, git, make_repo, write


def _skill(root: Path, rel: str, name: str) -> None:
    write(root, f"{rel}/SKILL.md", f"---\nname: {name}\ndescription: {name} things\n---\nDo {name}.\n")


@pytest.fixture
def setup(tmp_path, monkeypatch):
    root, _ = make_repo(tmp_path, {"fix_lint": {"family": "fix", "grader": "calc", "setup_patch": "setup.patch",
                                                "expected_skill": "lint", "prompt": "fix it."}})
    for name in ("lint", "format"):
        _skill(root, f".agent-eval/skills/{name}", name)
    git(root, "add", "-A")
    git(root, "commit", "-qm", "skills")
    exe, profile = fake_harness(tmp_path)
    runner = FakeRunner()
    monkeypatch.setattr("dokimi.repos.container.DockerRunner", lambda *a, **k: runner)
    monkeypatch.setenv("FAKE_ACTION", "fix")
    return {"root": root, "exe": exe, "profile": profile, "out": tmp_path / "runs"}


def _run(s, *extra):
    argv = ["repo", "run", f"{s['root']}@HEAD", "--harness", str(s["profile"]), "--bin", str(s["exe"]),
            "--model", "m-1", "--batch", "b1", "--out", str(s["out"]), "--cache-root", str(s["out"] / "caches"),
            "--agent-network", "host", *extra]
    args = build_parser().parse_args(argv)
    args.func(args)


def test_parse_arm_composes_guidance_and_skills():
    arm = parse_arm("guidance:none+skill:skills/c++/x+skill-catalog:/abs/cat")
    assert arm.guidance == "guidance:none"
    assert arm.skills == (("skill", "skills/c++/x"), ("skill-catalog", "/abs/cat")) and arm.catalog
    assert parse_arm("skill:a").guidance == "guidance:raw" and parse_arm("guidance:bridge").skills == ()
    for bad, why in (("main-raw", "unknown arm"), ("skill:", "needs a path"),
                     ("guidance:none+guidance:raw", "more than one guidance")):
        with pytest.raises(ArmError, match=why):
            parse_arm(bad)


def test_resolve_skills_is_relative_to_eval_dir_and_stays_in_it(tmp_path):
    _skill(tmp_path, "skills/lint", "lint")
    _skill(tmp_path, "skills/format", "format")
    (tmp_path / "skills/notes").mkdir()
    assert [s.name for s in resolve_skills(parse_arm("skill-catalog:skills"), tmp_path)] == ["format", "lint"]
    assert resolve_skills(parse_arm("skill:skills/lint"), tmp_path)[0].dir == (tmp_path / "skills/lint").resolve()
    for spec, why in (("skill:skills/notes", "no SKILL.md"), ("skill:../x", "leaves eval_dir"),
                      ("skill-catalog:skills/notes", "no subdirectory"),
                      ("skill:skills/lint+skill-catalog:skills", "more than one skill named lint")):
        with pytest.raises(ArmError, match=why):
            resolve_skills(parse_arm(spec), tmp_path)
    task = Task("t", {"expected_skill": "deploy"}, tmp_path)
    with pytest.raises(ArmError, match="expected_skill 'deploy'"):
        check_arms(["skill-catalog:skills"], tmp_path, [task])
    check_arms(["skill:skills/lint"], tmp_path, [task])  # one skill: no selection to check


def test_skill_usage_from_skill_calls_and_skill_md_reads():
    events = [{"kind": "read", "path": ".claude/skills/format/SKILL.md"},
              {"kind": "tool", "name": "Skill", "skill": "lint"}, {"kind": "tool", "name": "Skill", "skill": "nope"}]
    used = skill_usage(events, ["format", "lint"], "lint")
    assert used["skills_used"] == ["format", "lint"]
    assert used["skill_invoked"] is True and used["skill_selected_correct"] is False
    none = skill_usage([], ["lint"], None)
    assert none["skill_invoked"] is False and none["skill_selected_correct"] is None


def test_trace_records_the_skill_and_cache_tokens():
    raw = "\n".join(json.dumps(x) for x in [
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "a", "name": "Skill",
                                                       "input": {"skill": "lint"}}]}},
        {"type": "result", "result": "ok", "usage": {"input_tokens": 1, "output_tokens": 2,
                                                     "cache_read_input_tokens": 30, "cache_creation_input_tokens": 4}}])
    t = convert("claude-stream-json", raw, "/w")
    assert t.events == [{"t": None, "kind": "tool", "name": "Skill", "skill": "lint"}]
    assert (t.cache_read_input_tokens, t.cache_creation_input_tokens) == (30, 4)


def test_catalog_arm_installs_every_skill_and_records_selection(setup, monkeypatch, capsys):
    monkeypatch.setenv("FAKE_SKILLS", "format,lint")  # the agent tries the wrong skill first
    _run(setup, "--arm", "guidance:none+skill-catalog:skills,guidance:raw", "--task", "fix_lint")
    batch = setup["out"] / "b1"
    rd = next(batch.glob("fix_lint__guidance-none_skill-catalog-skills__*"))
    meta = json.loads((rd / "meta.json").read_text())
    assert [s["path"] for s in meta["skills"]] == [".claude/skills/format", ".claude/skills/lint"]
    assert (rd / "workspace/.claude/skills/lint/SKILL.md").is_file()
    assert not (rd / "workspace/AGENTS.md").exists()  # the guidance part still applies
    result = json.loads((rd / "result.json").read_text())
    assert result["arm"] == "guidance:none+skill-catalog:skills"
    assert result["skills"]["skills_used"] == ["format", "lint"] and result["skills"]["expected_skill"] == "lint"
    assert result["skills"]["skill_invoked"] is True and result["skills"]["skill_selected_correct"] is False
    assert result["agent"]["cache_read_input_tokens"] == 100 and result["agent"]["cache_creation_input_tokens"] == 40
    raw_rd = next(batch.glob("fix_lint__guidance-raw__*"))
    assert "skills" not in json.loads((raw_rd / "result.json").read_text())

    rows = {r["arm"]: r for r in summarize(batch)["rows"]}
    cat = rows["guidance:none+skill-catalog:skills"]
    assert cat["skill_invoked"] == 1 and cat["skill_selected_correct"] == 0
    assert cat["cache_read_input_tokens"] == 100.0 and cat["cache_creation_input_tokens"] == 40.0
    assert "skill_invoked" not in rows["guidance:raw"]
    assert "0/1 skill selected correct" in format_report(summarize(batch))


def test_bad_skill_arms_are_refused_before_any_cell(setup, capsys):
    for arm in ("skill:skills/missing", "skill-catalog:skills/lint"):
        with pytest.raises(SystemExit):
            _run(setup, "--arm", arm, "--task", "fix_lint")
        assert "✗" in capsys.readouterr().err
    assert not list((setup["out"] / "b1").glob("*/meta.json"))


def test_tasks_check_validates_skill_arms(setup, capsys):
    argv = ["repo", "tasks", "check", f"{setup['root']}@HEAD", "--out", str(setup["out"] / "chk"),
            "--cache-root", str(setup["out"] / "caches"), "--arm", "skill:skills/nope"]
    args = build_parser().parse_args(argv)
    with pytest.raises(SystemExit):
        args.func(args)
    assert "no SKILL.md in skills/nope" in capsys.readouterr().err
