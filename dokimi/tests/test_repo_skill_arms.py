# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`repo run` skill arms (skill:, skill-catalog:, joined with guidance by +) and cache tokens. No docker, no LLM."""

from __future__ import annotations

import dataclasses
import json
import os
import shutil
from pathlib import Path

import pytest

from dokimi.cli import build_parser
from dokimi.repos.arms import ArmError, Skill, check_arms, resolve_skills, skill_usage, tree_sha256
from dokimi.repos.events import convert
from dokimi.repos.report import format_report, summarize
from dokimi.repos.workspace import PrepError, Task, _install_skills, parse_arm
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
    assert arm.base == "guidance:none"
    assert arm.skills == (("skill", "skills/c++/x"), ("skill-catalog", "/abs/cat"))
    assert str(arm) == "guidance:none+skill:x+skill-catalog:cat" and "/" not in arm  # `repo compare` reads BATCH/ARM
    assert arm.spec == "guidance:none+skill:skills/c++/x+skill-catalog:/abs/cat"
    alone = parse_arm("skill:a")
    assert alone.base == "guidance:raw" and str(alone) == "skill:a"
    assert parse_arm("guidance:bridge").skills == ()
    for bad, why in (("main-raw", "unknown arm"), ("skill:", "needs the path"), ("skill:..", "needs the path"),
                     ("guidance:none+guidance:raw+skill:x", "more than one guidance")):
        with pytest.raises(PrepError, match=why):
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
        check_arms([parse_arm("skill-catalog:skills")], tmp_path, [task])
    check_arms([parse_arm("skill:skills/lint")], tmp_path, [task])  # one skill: no selection to check


def test_skill_usage_from_skill_calls_and_skill_md_reads():
    events = [{"kind": "read", "path": ".claude/skills/format/SKILL.md"},
              {"kind": "tool", "name": "Skill", "skill": "lint"}, {"kind": "tool", "name": "Skill", "skill": "nope"}]
    used = skill_usage(events, ["format", "lint"], "lint")
    assert used["skills_used"] == ["format", "lint"]
    assert used["skill_invoked"] is True and used["skill_selected_correct"] is False
    none = skill_usage([], ["lint"], None)
    assert none["skill_invoked"] is False and none["skill_selected_correct"] is None
    wrong = skill_usage([{"kind": "tool", "name": "Skill", "skill": "format"}], ["format", "lint"], "lint")
    assert wrong["skills_used"] == ["format"] and wrong["skill_invoked"] is False  # another skill is not it
    plugin = skill_usage([{"kind": "tool", "name": "Skill", "skill": "plug:lint"}], ["lint"], "lint")
    assert plugin["skill_invoked"] is True and plugin["skill_selected_correct"] is True


def test_only_a_read_of_skill_md_counts_as_using_a_skill():
    for path in (".claude/skills/lint/references/x.md", ".claude/skills/lint/notes.md"):
        used = skill_usage([{"kind": "read", "path": path}], ["lint"], "lint")
        assert used["skills_used"] == [] and used["skill_invoked"] is False
    twice = skill_usage([{"kind": "tool", "name": "Skill", "skill": "lint"},
                         {"kind": "tool", "name": "Skill", "skill": "lint"},
                         {"kind": "read", "path": ".claude/skills/lint/SKILL.md"}], ["lint"], "lint")
    assert twice["skills_used"] == ["lint"]


def test_an_older_claude_code_names_the_skill_in_command():
    raw = json.dumps({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": "a", "name": "Skill", "input": {"command": "lint"}}]}})
    assert convert("claude-stream-json", raw, "/w").events == [{"t": None, "kind": "tool", "name": "Skill",
                                                                 "skill": "lint"}]


def test_a_quoted_frontmatter_name_is_the_directory_name(tmp_path):
    for quote in ('"', "'"):
        write(tmp_path, "skills/lint/SKILL.md", f"---\nname: {quote}lint{quote}\ndescription: x\n---\nDo lint.\n")
        assert [s.name for s in resolve_skills(parse_arm("skill:skills/lint"), tmp_path)] == ["lint"]


def test_a_task_without_expected_skill_reports_no_selection(setup, monkeypatch):
    monkeypatch.setenv("FAKE_SKILLS", "lint")
    _run(setup, "--arm", "skill:skills/lint", "--task", "fix_add")  # fix_add names no expected_skill
    row = summarize(setup["out"] / "b1")["rows"][0]
    assert (row["skill_measured"], row["skill_invoked"]) == (1, 1) and "skill_selected_correct" not in row
    assert "skill selected correct" not in format_report(summarize(setup["out"] / "b1"))


def test_a_result_with_a_non_integer_num_turns_and_no_tool_events_still_measures_skill_uptake(setup, monkeypatch):
    # The agent finished (a result record) but made no tool call and reported "3" turns: it started, so uptake is 0, not unmeasured.
    raw = json.dumps({"type": "result", "result": "ok", "num_turns": "3"})
    monkeypatch.setattr("dokimi.repos.runner.convert", lambda trace, _raw, workdir=None: convert(trace, raw, workdir))
    _run(setup, "--arm", "skill:skills/lint", "--task", "fix_add")
    rd = next((setup["out"] / "b1").glob("fix_add__*"))
    result = json.loads((rd / "result.json").read_text())
    assert result["agent"]["num_turns"] is None
    assert any("num_turns '3' is not an integer" in e for e in result["agent"]["trace_errors"])
    assert result["skills"]["measured"] is True and result["skills"]["skills_used"] == []
    assert result["process"] is not None  # its tool calls are counted (zero), not left unmeasured


def test_catalog_arm_counts_the_expected_skill_picked_first(setup, monkeypatch):
    monkeypatch.setenv("FAKE_SKILLS", "lint,format")  # the agent picks the right skill first
    _run(setup, "--arm", "guidance:none+skill-catalog:skills", "--task", "fix_lint")
    rd = next((setup["out"] / "b1").glob("fix_lint__guidance-none_skill-catalog-skills__*"))
    skills = json.loads((rd / "result.json").read_text())["skills"]
    assert skills["skills_used"] == ["lint", "format"] and skills["skill_selected_correct"] is True
    rows = {r["arm"]: r for r in summarize(setup["out"] / "b1")["rows"]}
    assert rows["guidance:none+skill-catalog:skills"]["skill_selected_correct"] == 1


def test_guidance_none_keeps_a_skill_when_the_repo_ships_claude_skills(tmp_path, monkeypatch):
    write(tmp_path / "toy", ".claude/skills/other/SKILL.md", "---\nname: other\ndescription: x\n---\nOther.\n")
    root, _ = make_repo(tmp_path, {"fix_lint": {"family": "fix", "grader": "calc", "setup_patch": "setup.patch",
                                                "expected_skill": "lint", "prompt": "fix it."}})
    _skill(root, ".agent-eval/skills/lint", "lint")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "skills")
    exe, profile = fake_harness(tmp_path)
    runner = FakeRunner()
    monkeypatch.setattr("dokimi.repos.container.DockerRunner", lambda *a, **k: runner)
    monkeypatch.setenv("FAKE_ACTION", "fix")
    out = tmp_path / "runs"
    args = build_parser().parse_args(["repo", "run", f"{root}@HEAD", "--harness", str(profile), "--bin", str(exe),
                                      "--model", "m-1", "--batch", "b1", "--out", str(out),
                                      "--cache-root", str(out / "caches"), "--agent-network", "host",
                                      "--arm", "guidance:none+skill:skills/lint", "--task", "fix_lint"])
    args.func(args)
    rd = next((out / "b1").glob("fix_lint__*"))
    assert (rd / "workspace/.claude/skills/lint/SKILL.md").is_file()


@pytest.mark.parametrize("link", [".claude", ".claude/skills"])
def test_a_skill_is_not_written_through_a_repository_link(tmp_path, link):
    host = tmp_path / "host"  # a host directory the repository's own link points at
    host.mkdir()
    ws = tmp_path / "ws"
    (ws / ".claude").mkdir(parents=True) if link == ".claude/skills" else ws.mkdir()
    (ws / link).symlink_to(host, target_is_directory=True)
    _skill(tmp_path / "eval", "skills/lint", "lint")
    skill = Skill("lint", tmp_path / "eval/skills/lint")
    with pytest.raises(PrepError, match="is a symlink in the repository"):
        _install_skills(ws, Task("t", {}, tmp_path), [skill])
    assert not (host / "skills").exists()


def _installed_dest(tmp_path: Path):
    _skill(tmp_path / "eval", "skills/lint", "lint")
    ws = tmp_path / "ws"
    dest = ws / ".claude/skills/lint"
    dest.parent.mkdir(parents=True)
    return ws, dest, Skill("lint", tmp_path / "eval/skills/lint")


@pytest.mark.parametrize("make", ["file", "nested_file", "symlinked_dir", "dangling_link", "link_to_empty_dir", "regular_file"])
def test_a_skill_directory_that_holds_anything_but_empty_directories_is_refused_and_kept(tmp_path, make):
    """Only the emptied directories of a dropped skill are removed; real files, links and their targets stay."""
    ws, dest, skill = _installed_dest(tmp_path)
    keep = tmp_path / "elsewhere"
    keep.mkdir()
    (keep / "precious.md").write_text("keep\n")
    if make == "file":
        dest.mkdir()
        (dest / "SKILL.md").write_text("mine\n")
    elif make == "nested_file":
        (dest / "references/deep").mkdir(parents=True)
        (dest / "references/deep/a.md").write_text("mine\n")
    elif make == "symlinked_dir":
        (dest / "references").mkdir(parents=True)
        (dest / "references/linked").symlink_to(keep, target_is_directory=True)
    elif make == "dangling_link":
        dest.symlink_to(tmp_path / "nowhere")
    elif make == "regular_file":
        dest.write_text("mine\n")
    else:
        (keep / "empty").mkdir()
        dest.symlink_to(keep / "empty", target_is_directory=True)
    with pytest.raises(PrepError, match="already has"):
        _install_skills(ws, Task("t", {}, tmp_path), [skill])
    assert (keep / "precious.md").read_text() == "keep\n"
    assert os.path.lexists(dest)  # nothing was removed to make room, and the skill was not installed over it
    assert not (dest / "SKILL.md").exists() or (dest / "SKILL.md").read_text() == "mine\n"
    if make == "link_to_empty_dir":
        assert dest.is_symlink() and (keep / "empty").is_dir()
    if make == "nested_file":
        assert (dest / "references/deep/a.md").read_text() == "mine\n"


def test_a_skill_directory_of_only_empty_directories_is_replaced(tmp_path):
    ws, dest, skill = _installed_dest(tmp_path)
    (dest / "references/deep").mkdir(parents=True)
    out = _install_skills(ws, Task("t", {}, tmp_path), [skill])
    assert out[0]["name"] == "lint" and (dest / "SKILL.md").is_file() and not (dest / "references").exists()


def test_trace_records_the_skill_and_cache_tokens():
    raw = "\n".join(json.dumps(x) for x in [
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "a", "name": "Skill",
                                                       "input": {"skill": "lint"}}]}},
        {"type": "result", "result": "ok", "usage": {"input_tokens": 1, "output_tokens": 2,
                                                     "cache_read_input_tokens": 30, "cache_creation_input_tokens": 4}}])
    t = convert("claude-stream-json", raw, "/w")
    assert t.events == [{"t": None, "kind": "tool", "name": "Skill", "skill": "lint"}]
    assert (t.cache_read_tokens, t.cache_write_tokens) == (30, 4)


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
    assert result["agent"]["cache_read_tokens"] == 100 and result["agent"]["cache_write_tokens"] == 40
    raw_rd = next(batch.glob("fix_lint__guidance-raw__*"))
    assert "skills" not in json.loads((raw_rd / "result.json").read_text())

    rows = {r["arm"]: r for r in summarize(batch)["rows"]}
    cat = rows["guidance:none+skill-catalog:skills"]
    assert cat["skill_invoked"] == 1 and cat["skill_selected_correct"] == 0
    assert cat["cache_read_tokens"] == 100.0 and cat["cache_write_tokens"] == 40.0
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


def test_a_link_out_of_a_skill_or_catalog_is_refused(tmp_path):
    host = tmp_path / "host"  # outside eval_dir; a link to it must not copy its bytes into the workspace
    host.mkdir()
    (host / "SKILL.md").write_text("---\nname: evil\ndescription: x\n---\n")
    (host / "token.txt").write_text("TOPSECRET")
    evaldir = tmp_path / "eval"
    _skill(evaldir, "skills/good", "good")
    (evaldir / "skills/good/leak.txt").symlink_to(host / "token.txt")
    with pytest.raises(ArmError, match="leak.txt in skill good links outside it"):
        resolve_skills(parse_arm("skill:skills/good"), evaldir)
    (evaldir / "skills/good/leak.txt").unlink()
    (evaldir / "skills/evil").symlink_to(host)
    with pytest.raises(ArmError, match="evil in skills links outside it"):
        resolve_skills(parse_arm("skill-catalog:skills"), evaldir)
    (evaldir / "skills/evil").unlink()
    # inside the skill, but the copy cannot take them: each is refused before any cell runs
    good = evaldir / "skills/good"
    cases = (("loop", lambda: (good / "loop").symlink_to("."), ["loop"], "links to a directory"),
             ("up", lambda: ((good / "sub").mkdir(), (good / "sub/up").symlink_to("..")), ["sub"],
              "links to a directory"),
             ("dangling", lambda: (good / "d").symlink_to("nope"), ["d"], "dangling or looping link"),
             ("ping", lambda: (good / "a").symlink_to("b") or (good / "b").symlink_to("a"), ["a", "b"],
              "dangling or looping link"),
             ("fifo", lambda: os.mkfifo(good / "pipe"), ["pipe"], "is not a regular file"))
    for _name, make, names, why in cases:
        make()
        with pytest.raises(ArmError, match=why):
            resolve_skills(parse_arm("skill:skills/good"), evaldir)
        for leftover in names:
            if (good / leftover).is_dir() and not (good / leftover).is_symlink():
                shutil.rmtree(good / leftover)
            else:
                (good / leftover).unlink()


def test_a_tree_hash_changes_with_the_skill_bytes(tmp_path):
    for name, body in (("a", "Do lint."), ("b", "Do format.")):
        write(tmp_path, f"{name}/SKILL.md", body)
    assert tree_sha256(tmp_path / "a") != tree_sha256(tmp_path / "b")


def test_unmeasured_skill_usage_is_left_out_of_the_counts(tmp_path):
    batch = tmp_path / "b"

    def cell(name, skills):
        rd = batch / name
        rd.mkdir(parents=True)
        (rd / "meta.json").write_text("{}")
        rec = {"task": "t", "arm": "skill:skills/lint", "model": "m", "score_task": False, "grade": {
                   "status": "ok", "success": True, "score_only": False, "score": None},
               "agent": {"timed_out": False, "is_error": False, "over_budget": False, "cost_usd": 0.1,
                         "num_turns": 3, "network": "none"}, "skills": skills}
        (rd / "result.json").write_text(json.dumps(rec))

    cell("c1", {"measured": True, "skills_installed": ["lint"], "skills_used": ["lint"], "expected_skill": "lint",
                "skill_invoked": True, "skill_selected_correct": True})
    cell("c2", {"measured": False, "skills_installed": ["lint"], "skills_used": [], "expected_skill": "lint",
                "skill_invoked": None, "skill_selected_correct": None})
    row = summarize(batch)["rows"][0]
    assert (row["skill_measured"], row["skill_invoked"], row["skill_selected_correct"]) == (1, 1, 1)
    assert "1/1 skill invoked" in format_report(summarize(batch))


def test_a_harness_that_never_started_leaves_skill_usage_unmeasured(setup, monkeypatch):
    monkeypatch.setenv("FAKE_ACTION", "startfail")  # exit 1, no trace: not "the agent ignored the skill"
    _run(setup, "--arm", "guidance:raw+skill:skills/lint", "--task", "fix_lint", "--reps", "2")
    monkeypatch.setenv("FAKE_ACTION", "fix")  # ran, used no skill: measured
    _run(setup, "--batch", "b2", "--arm", "guidance:raw+skill:skills/lint", "--task", "fix_lint")
    for batch, measured in (("b1", False), ("b2", True)):
        cells = list((setup["out"] / batch).glob("fix_lint__*"))
        assert cells
        for rd in cells:
            skills = json.loads((rd / "result.json").read_text())["skills"]
            assert skills["measured"] is measured and skills["skill_invoked"] is (False if measured else None)
    assert "skill invoked" not in format_report(summarize(setup["out"] / "b1"))
    assert "0/1 skill invoked" in format_report(summarize(setup["out"] / "b2"))


def test_arms_that_share_a_result_directory_are_refused(tmp_path):
    _skill(tmp_path, "a/b", "b")
    _skill(tmp_path, "x/b", "b")
    _skill(tmp_path, "a_b", "a_b")
    _skill(tmp_path, "s/one", "one")
    _skill(tmp_path, "catalog-s", "catalog-s")
    with pytest.raises(ArmError, match="share one result directory"):
        check_arms([parse_arm("skill:a/b"), parse_arm("skill:x/b")], tmp_path, [])
    with pytest.raises(ArmError, match="share one result directory"):
        check_arms([parse_arm("skill-catalog:s"), parse_arm("skill:catalog-s")], tmp_path, [])


def test_two_skill_paths_with_one_directory_name_in_one_run_are_refused(setup, capsys):
    _skill(setup["root"], ".agent-eval/other/lint", "lint")  # both arms are named skill:lint
    git(setup["root"], "add", "-A")
    git(setup["root"], "commit", "-qm", "other lint")
    with pytest.raises(SystemExit):
        _run(setup, "--arm", "skill:skills/lint,skill:other/lint", "--task", "fix_lint")
    assert "name different skill paths" in capsys.readouterr().err
    assert not list((setup["out"] / "b1").glob("fix_lint__*"))


def test_a_resumed_batch_refuses_other_skills_under_one_arm_name(setup, tmp_path, capsys):
    _skill(setup["root"], ".agent-eval/other/lint", "lint")  # same directory name, other bytes
    write(setup["root"], ".agent-eval/other/lint/extra.md", "Other lint.\n")
    git(setup["root"], "add", "-A")
    git(setup["root"], "commit", "-qm", "other lint")
    host = tmp_path / "host/lint"
    _skill(tmp_path, "host/lint", "lint")
    write(tmp_path, "host/lint/extra.md", "Host lint.\n")
    batch = setup["out"] / "b1"
    _run(setup, "--arm", "skill:skills/lint,skill-catalog:skills", "--task", "fix_lint")
    _run(setup, "--arm", "skill:./skills/lint", "--task", "fix_lint", "--reps", "2")  # same bytes: one arm
    cells = sorted(p.name for p in batch.glob("fix_lint__*"))
    capsys.readouterr()
    for arm in ("skill:other/lint", f"skill:{host}"):
        with pytest.raises(SystemExit):
            _run(setup, "--arm", arm, "--task", "fix_lint", "--reps", "3")
        assert "ran arm skill:lint with skills lint " in capsys.readouterr().err
    assert sorted(p.name for p in batch.glob("fix_lint__*")) == cells
    _run(setup, "--batch", "b2", "--arm", f"skill:{host}", "--task", "fix_lint")
    (host / "SKILL.md").write_text("---\nname: lint\ndescription: lint things\n---\nEdited.\n")
    with pytest.raises(SystemExit):
        _run(setup, "--batch", "b2", "--arm", f"skill:{host}", "--task", "fix_lint", "--reps", "2")
    assert "use another --batch" in capsys.readouterr().err
    assert not list((setup["out"] / "b2").glob("*__r2"))


def test_claude_code_gets_mcp_config_only_when_the_workspace_has_the_file(tmp_path, monkeypatch):
    runner = FakeRunner()
    monkeypatch.setattr("dokimi.repos.container.DockerRunner", lambda *a, **k: runner)
    monkeypatch.setenv("FAKE_ACTION", "fix")
    profile = Path(__file__).resolve().parents[1] / "dokimi/repos/profiles/claude-code.yaml"
    exe, _ = fake_harness(tmp_path)
    for with_mcp in (True, False):
        runner.specs.clear()
        base = tmp_path / ("with" if with_mcp else "without")
        base.mkdir()
        if with_mcp:
            write(base / "toy", ".mcp.json", '{"mcpServers": {}}')  # make_repo commits it into the task base
        root, _ = make_repo(base)
        argv = ["repo", "run", f"{root}@HEAD", "--harness", str(profile), "--bin", str(exe), "--model", "m-1",
                "--batch", "b1", "--out", str(base / "runs"), "--cache-root", str(base / "caches"),
                "--agent-network", "host", "--arm", "guidance:raw", "--task", "fix_add", "--tools", "all"]
        args = build_parser().parse_args(argv)
        args.func(args)
        agent = [s.argv for s in runner.specs if "--strict-mcp-config" in s.argv]
        assert len(agent) == 1
        assert ("--mcp-config" in agent[0]) is with_mcp
        if with_mcp:
            assert agent[0][agent[0].index("--mcp-config") + 1] == ".mcp.json"


def test_a_skill_whose_frontmatter_name_differs_from_its_directory_is_refused(tmp_path):
    _skill(tmp_path, "skills/lint", "pretty-lint")  # the agent would call it pretty-lint; usage matches on "lint"
    _skill(tmp_path, "skills/format", "format")
    with pytest.raises(ArmError, match="named 'pretty-lint', but its directory is 'lint'"):
        resolve_skills(parse_arm("skill:skills/lint"), tmp_path)
    with pytest.raises(ArmError, match="named 'pretty-lint'"):
        resolve_skills(parse_arm("skill-catalog:skills"), tmp_path)
    write(tmp_path, "skills/bare/SKILL.md", "No frontmatter.\n")  # no name: the directory names it
    assert [s.name for s in resolve_skills(parse_arm("skill:skills/bare"), tmp_path)] == ["bare"]


def test_repo_compare_selects_a_skill_arm_named_from_a_path_with_slashes():
    from dokimi.repos.compare import parse_selector, selector_matches

    arm = str(parse_arm("guidance:raw+skill:skills/lint"))
    assert parse_selector(arm) == (None, arm)
    assert parse_selector(f"b1/{arm}") == ("b1", arm)
    assert selector_matches(parse_selector(f"b1/{arm}"), {"arm": arm, "batch": "b1"})


@pytest.mark.parametrize("front", [
    "name: lint # the linter\ndescription: x",
    "name: >\n  lint\ndescription: x",
    "metadata:\n  name: other\nname: lint\ndescription: x",
    "description: |\n  name: foo\nname: lint",
    "name: lint\nmetadata:\n  name: other",
])
def test_a_valid_frontmatter_name_is_read_from_the_top_level_key_only(tmp_path, front):
    write(tmp_path, "skills/lint/SKILL.md", f"---\n{front}\n---\nname: other\nBody.\n")
    assert [s.name for s in resolve_skills(parse_arm("skill:skills/lint"), tmp_path)] == ["lint"]


def test_a_nested_name_alone_is_not_the_skill_name(tmp_path):
    write(tmp_path, "skills/lint/SKILL.md", "---\nmetadata:\n  name: other\n---\nBody.\n")
    assert [s.name for s in resolve_skills(parse_arm("skill:skills/lint"), tmp_path)] == ["lint"]


def test_a_skill_the_task_base_already_ships_is_refused_before_any_cell(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr("dokimi.repos.container.DockerRunner", lambda *a, **k: FakeRunner())
    _skill(tmp_path / "toy", ".claude/skills/lint", "lint")
    root, base = make_repo(tmp_path)
    _skill(root, ".agent-eval/skills/lint", "lint")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "skills")
    task = Task("t", {"base": base}, root)
    with pytest.raises(ArmError, match="base of task t already has .claude/skills/lint"):
        check_arms([parse_arm("skill:skills/lint")], root / ".agent-eval", [task], root / ".git")
    with pytest.raises(SystemExit):  # `repo tasks check --arm` goes through the same check
        args = build_parser().parse_args(["repo", "tasks", "check", f"{root}@HEAD", "--out", str(tmp_path / "chk"),
                                          "--cache-root", str(tmp_path / "caches"), "--arm", "skill:skills/lint"])
        args.func(args)
    assert "already has .claude/skills/lint" in capsys.readouterr().err


def test_a_cell_records_the_optional_harness_arguments_it_ran_with(tmp_path, monkeypatch):
    import yaml
    from repo_eval_fixture import PROFILE
    write(tmp_path / "toy", ".mcp.json", "{}")
    root, _ = make_repo(tmp_path)
    exe, profile = fake_harness(tmp_path)
    profile.write_text(yaml.safe_dump({**PROFILE, "args_if_present": {".mcp.json": ["--mcp-config", ".mcp.json"]}}),
                       encoding="utf-8")
    monkeypatch.setattr("dokimi.repos.container.DockerRunner", lambda *a, **k: FakeRunner())
    monkeypatch.setenv("FAKE_ACTION", "fix")
    out = tmp_path / "runs"
    args = build_parser().parse_args(["repo", "run", f"{root}@HEAD", "--harness", str(profile), "--bin", str(exe),
                                      "--model", "m-1", "--batch", "b1", "--out", str(out),
                                      "--cache-root", str(out / "caches"), "--agent-network", "host",
                                      "--arm", "guidance:none", "--task", "fix_add"])
    args.func(args)
    meta = json.loads(next((out / "b1").glob("fix_add__*")).joinpath("meta.json").read_text())
    assert meta["args_if_present"] == [".mcp.json"]


@pytest.mark.parametrize(("tools", "unreadable"), [("none", False), ("read", False), ("all", True)])
def test_the_recorded_optional_arguments_are_the_ones_the_agent_got(tmp_path, monkeypatch, tools, unreadable):
    import yaml
    from repo_eval_fixture import PROFILE
    write(tmp_path / "toy", ".mcp.json", "{}")
    root, _ = make_repo(tmp_path)
    exe, profile = fake_harness(tmp_path)
    profile.write_text(yaml.safe_dump({**PROFILE, "args_if_present": {".mcp.json": ["--mcp-config", ".mcp.json"]}}),
                       encoding="utf-8")
    runner = FakeRunner()
    monkeypatch.setattr("dokimi.repos.container.DockerRunner", lambda *a, **k: runner)
    monkeypatch.setenv("FAKE_ACTION", "fix")
    if unreadable:  # meta.json is rewritten only after the trace converts
        monkeypatch.setattr("dokimi.repos.runner.convert", lambda *a, **k: (_ for _ in ()).throw(ValueError("bad")))
    out = tmp_path / "runs"
    args = build_parser().parse_args(["repo", "run", f"{root}@HEAD", "--harness", str(profile), "--bin", str(exe),
                                      "--model", "m-1", "--batch", "b1", "--out", str(out),
                                      "--cache-root", str(out / "caches"), "--agent-network", "host",
                                      "--arm", "guidance:raw", "--task", "fix_add", "--tools", tools])
    args.func(args)
    meta = json.loads(next((out / "b1").glob("fix_add__*")).joinpath("meta.json").read_text())
    agent = [s.argv for s in runner.specs if "--max-budget-usd" in s.argv]
    assert len(agent) == 1 and ("--mcp-config" in agent[0]) is (tools == "all")
    assert meta["args_if_present"] == ([".mcp.json"] if tools == "all" else [])


def test_the_base_check_ignores_a_skill_the_guidance_arm_removes_and_reads_each_base_once(tmp_path, monkeypatch):
    _skill(tmp_path / "toy", ".claude/skills/lint", "lint")
    root, base = make_repo(tmp_path)
    _skill(root, ".agent-eval/skills/lint", "lint")
    _skill(root, ".claude/skills/lint", "lint")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "skills")
    tasks = [Task("t1", {"base": base}, root), Task("t2", {"base": base}, root)]
    content, globs = root / ".agent-eval", ["**/AGENTS.md", ".claude/skills/**"]
    check_arms([parse_arm("guidance:none+skill:skills/lint")], content, tasks, root / ".git", globs)  # removed first
    for arm, given in (("guidance:raw+skill:skills/lint", globs), ("guidance:none+skill:skills/lint", ["**/AGENTS.md"])):
        with pytest.raises(ArmError, match="already has .claude/skills/lint"):
            check_arms([parse_arm(arm)], content, tasks, root / ".git", given)
    from dokimi import process
    calls = []
    real = process.run
    monkeypatch.setattr(process, "run", lambda cmd, **kw: calls.append(cmd) or real(cmd, **kw))
    check_arms([parse_arm("guidance:none+skill:skills/lint")], content, tasks, root / ".git", globs)
    assert len(calls) == 1  # two tasks on one base: one git process


def test_the_selected_correct_rate_counts_only_cells_whose_task_names_a_skill(tmp_path):
    batch = tmp_path / "b"
    for name, expected, correct in (("c1", "lint", True), ("c2", "lint", False), ("c3", None, None), ("c4", None, None)):
        rd = batch / name
        rd.mkdir(parents=True)
        (rd / "meta.json").write_text("{}")
        skills = {"measured": True, "skills_installed": ["lint"], "skills_used": ["lint"], "expected_skill": expected,
                  "skill_invoked": True, "skill_selected_correct": correct}
        rec = {"task": "t", "arm": "skill:skills/lint", "model": "m", "score_task": False, "grade": {
                   "status": "ok", "success": True, "score_only": False, "score": None},
               "agent": {"timed_out": False, "is_error": False, "over_budget": False, "cost_usd": 0.1,
                         "num_turns": 3, "network": "none"}, "skills": skills}
        (rd / "result.json").write_text(json.dumps(rec))
    summary = summarize(batch)
    row = summary["rows"][0]
    assert (row["skill_measured"], row["skill_expected"], row["skill_selected_correct"]) == (4, 2, 1)
    assert "1/2 skill selected correct" in format_report(summary) and "4/4 skill invoked" in format_report(summary)


def test_a_skill_path_whose_leaf_is_not_its_directory_name_is_refused(tmp_path):
    _skill(tmp_path, "skills/lint-v2", "lint-v2")
    (tmp_path / "skills" / "current").symlink_to("lint-v2")  # the arm would be skill:current, the install lint-v2
    with pytest.raises(ArmError, match="resolves to a directory named 'lint-v2', not 'current'"):
        resolve_skills(parse_arm("skill:skills/current"), tmp_path)
    assert [s.name for s in resolve_skills(parse_arm("skill:skills/lint-v2"), tmp_path)] == ["lint-v2"]


def test_the_base_check_drops_guidance_for_an_unpinned_revision_arm(tmp_path):
    _skill(tmp_path / "toy", ".claude/skills/lint", "lint")
    root, base = make_repo(tmp_path)
    _skill(root, ".agent-eval/skills/lint", "lint")
    git(root, "rm", "-rq", ".claude/skills/lint")  # HEAD's guidance has no lint skill, the task base has one
    git(root, "add", "-A")
    git(root, "commit", "-qm", "skills")
    tasks = [Task("t1", {"base": base}, root)]
    check_arms([parse_arm("guidance:rev:HEAD+skill:skills/lint")], root / ".agent-eval", tasks, root / ".git",
               [".claude/skills/**"])  # repo run pins the arm and removes the base's skill first
    with pytest.raises(ArmError, match=f"guidance revision {base} already has .claude/skills/lint"):
        check_arms([parse_arm(f"guidance:rev:{base}+skill:skills/lint")], root / ".agent-eval", tasks,
                   root / ".git", [".claude/skills/**"])  # ... and then writes the revision's own back


def test_the_base_check_matches_a_skill_directory_not_a_name_prefix(tmp_path):
    _skill(tmp_path / "toy", ".claude/skills/linter", "linter")  # the base ships linter, the arm installs lint
    root, base = make_repo(tmp_path)
    _skill(root, ".agent-eval/skills/lint", "lint")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "skills")
    check_arms([parse_arm("skill:skills/lint")], root / ".agent-eval", [Task("t", {"base": base}, root)],
               root / ".git")


def test_the_base_check_counts_only_the_files_the_revision_writes_back(tmp_path):
    root, base = make_repo(tmp_path)
    _skill(root, ".agent-eval/skills/lint", "lint")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "skills")
    write(root, ".claude/skills/lint/helper.py", "x = 1\n")  # tracked at HEAD, matched by no guidance glob
    git(root, "add", "-A")
    git(root, "commit", "-qm", "helper")
    tasks = [Task("t", {"base": base}, root)]
    arm = parse_arm("guidance:rev:HEAD+skill:skills/lint")
    check_arms([arm], root / ".agent-eval", tasks, root / ".git", ["**/AGENTS.md"])  # preparation writes no helper
    with pytest.raises(ArmError, match="guidance revision HEAD already has .claude/skills/lint"):
        check_arms([arm], root / ".agent-eval", tasks, root / ".git", [".claude/skills/**"])


def test_the_base_check_reads_the_pinned_commit_of_a_revision_arm_not_the_moved_branch(tmp_path):
    from dokimi.repos.workspace import pin_arm

    root, base = make_repo(tmp_path)
    _skill(root, ".agent-eval/skills/lint", "lint")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "skills")
    git(root, "branch", "guide")
    pinned = pin_arm(parse_arm("guidance:rev:guide+skill:skills/lint"), root / ".git")
    git(root, "checkout", "-q", "guide")
    _skill(root, ".claude/skills/lint", "lint")  # the branch moves to a commit that ships the skill
    git(root, "add", "-A")
    git(root, "commit", "-qm", "moved")
    tasks = [Task("t", {"base": base}, root)]
    check_arms([pinned], root / ".agent-eval", tasks, root / ".git", [".claude/skills/**"])  # checked at the pin
    with pytest.raises(ArmError, match="already has .claude/skills/lint"):
        check_arms([parse_arm("guidance:rev:guide+skill:skills/lint")], root / ".agent-eval", tasks,
                   root / ".git", [".claude/skills/**"])


@pytest.mark.parametrize(("head", "name"), [
    ("﻿---\nname: other\n---\nBody.\n", "a BOM"),
    ("---   \nname: other\n---\nBody.\n", "trailing spaces after the fence"),
])
def test_a_frontmatter_with_a_bom_or_a_padded_fence_is_still_read(tmp_path, head, name):
    (tmp_path / "skills" / "lint").mkdir(parents=True)
    (tmp_path / "skills" / "lint" / "SKILL.md").write_bytes(head.encode("utf-8"))
    with pytest.raises(ArmError, match="named 'other', but its directory is 'lint'"):
        resolve_skills(parse_arm("skill:skills/lint"), tmp_path)


def test_a_frontmatter_that_is_not_a_mapping_has_no_name(tmp_path):
    for body in ("- a\n- b", "just text", ""):
        write(tmp_path, "skills/lint/SKILL.md", f"---\n{body}\n---\nBody.\n")
        assert [s.name for s in resolve_skills(parse_arm("skill:skills/lint"), tmp_path)] == ["lint"]


def test_a_read_of_a_skills_directory_is_not_a_use_of_a_skill():
    events = [{"kind": "read", "path": ".claude/skills/lint"}, {"kind": "read", "path": ".claude/skills"},
              {"kind": "read", "path": ""}]
    used = skill_usage(events, ["lint"], "lint")
    assert used["skills_used"] == [] and used["skill_invoked"] is False


def _claude_run(tmp_path, monkeypatch, mcp_json, *extra, patch=None):
    """`repo run` with the built-in claude-code profile on a base that ships `mcp_json`; the agent specs."""
    runner = FakeRunner()
    monkeypatch.setattr("dokimi.repos.container.DockerRunner", lambda *a, **k: runner)
    monkeypatch.setenv("FAKE_ACTION", "fix")
    profile = Path(__file__).resolve().parents[1] / "dokimi/repos/profiles/claude-code.yaml"
    exe, _ = fake_harness(tmp_path)
    if mcp_json is not None:
        write(tmp_path / "toy", ".mcp.json", mcp_json)
    root, _ = make_repo(tmp_path)
    argv = ["repo", "run", f"{root}@HEAD", "--harness", str(profile), "--bin", str(exe), "--model", "m-1",
            "--batch", "b1", "--out", str(tmp_path / "runs"), "--cache-root", str(tmp_path / "caches"),
            "--agent-network", "host", "--task", "fix_add", *extra]
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
        code = 0
    except SystemExit as exc:
        code = exc.code
    return code, [s for s in runner.specs if "--strict-mcp-config" in s.argv]


@pytest.mark.parametrize("text", ["{", "[]", '{"mcpServers": "x"}'])
def test_run_refuses_a_malformed_mcp_json_before_any_cell_when_the_agent_is_given_it(tmp_path, monkeypatch, capsys,
                                                                                    text):
    code, agents = _claude_run(tmp_path, monkeypatch, text, "--arm", "guidance:raw", "--tools", "all")
    err = capsys.readouterr().err
    assert code == 1 and agents == []
    assert ".mcp.json" in err and "--tools read/none" in err
    assert not list((tmp_path / "runs" / "b1").glob("fix_add__*"))  # no cell was even prepared


def test_run_leaves_a_malformed_mcp_json_alone_when_the_agent_is_not_given_it(tmp_path, monkeypatch):
    code, agents = _claude_run(tmp_path, monkeypatch, "{", "--arm", "guidance:raw", "--tools", "read")
    assert code in (0, None) and len(agents) == 1 and "--mcp-config" not in agents[0].argv


def test_run_refuses_a_malformed_mcp_json_that_an_arms_patch_wrote(tmp_path, monkeypatch, capsys):
    patch = tmp_path / "mcp.diff"
    patch.write_text("diff --git a/.mcp.json b/.mcp.json\nnew file mode 100644\n--- /dev/null\n+++ b/.mcp.json\n"
                     "@@ -0,0 +1 @@\n+{\n")
    code, agents = _claude_run(tmp_path, monkeypatch, None, "--arm", f"patch:{patch}", "--tools", "all")
    assert code == 1 and agents == []
    out = capsys.readouterr()
    assert ".mcp.json in the workspace is not valid JSON" in out.out + out.err


def test_run_refuses_a_non_utf8_mcp_json_in_the_workspace_as_the_runner_reads_it(tmp_path, monkeypatch, capsys):
    # The base check would stop a tracked file first, so the workspace is written after preparation.
    from dokimi.repos import runner

    real_prepare = runner.prepare_workspace

    def prepare_then_write_non_utf8(*args, **kwargs):
        meta = real_prepare(*args, **kwargs)
        (args[4] / "workspace" / ".mcp.json").write_bytes(b"\xff{}")  # args[4] is the cell directory
        return meta

    monkeypatch.setattr(runner, "prepare_workspace", prepare_then_write_non_utf8)
    code, agents = _claude_run(tmp_path, monkeypatch, None, "--arm", "guidance:raw", "--tools", "all")
    out = capsys.readouterr()
    assert code == 1 and agents == []
    assert ".mcp.json in the workspace is not valid UTF-8 text" in out.out + out.err


def test_a_guidance_none_arm_installs_a_skill_the_base_ships_under_the_guidance_globs(tmp_path):
    _skill(tmp_path / "toy", ".claude/skills/lint", "lint")
    root, base = make_repo(tmp_path)
    _skill(root, ".agent-eval/skills/lint", "lint")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "skills")
    from dokimi.repos import RepoSource, load_manifest as load, open_source
    from dokimi.repos.workspace import load_tasks, prepare_workspace

    source = RepoSource.parse(f"{root}@HEAD")
    manifest = load(open_source(source), ".agent-eval/repo-eval.yaml")
    manifest = dataclasses.replace(manifest, guidance_globs=(*manifest.guidance_globs, ".claude/skills/**"))
    content = root / ".agent-eval"
    task = load_tasks(content, manifest, ["fix_add"])[0]
    arm = "guidance:none+skill:skills/lint"
    check_arms([parse_arm(arm)], content, [task], root / ".git", manifest.guidance_globs)
    rd = tmp_path / "rd"
    rd.mkdir()
    meta = prepare_workspace(root / ".git", manifest, task, arm, rd, None, content)
    assert (rd / "workspace/.claude/skills/lint/SKILL.md").is_file() and meta["skills"][0]["name"] == "lint"


@pytest.mark.parametrize("link", [".claude", ".claude/skills"])
def test_the_base_check_refuses_a_tracked_symlink_at_claude_or_its_skills(tmp_path, link):
    root, base = make_repo(tmp_path)
    _skill(root, ".agent-eval/skills/lint", "lint")
    (root / "shared").mkdir()
    if link == ".claude/skills":
        (root / ".claude").mkdir()
    (root / link).symlink_to("../shared" if "/" in link else "shared")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "link")
    task = Task("t", {"base": git(root, "rev-parse", "HEAD")}, root)
    with pytest.raises(ArmError, match=rf"tracks {link} as a symlink"):
        check_arms([parse_arm("skill:skills/lint")], root / ".agent-eval", [task], root / ".git")
    check_arms([parse_arm("guidance:raw")], root / ".agent-eval", [task], root / ".git")  # no skill: nothing to refuse


def test_a_frontmatter_name_that_yaml_reads_as_an_impossible_date_is_the_directory_name_not_a_crash(tmp_path):
    write(tmp_path, "skills/lint/SKILL.md", "---\nname: 2024-13-45\n---\nBody.\n")
    assert [s.name for s in resolve_skills(parse_arm("skill:skills/lint"), tmp_path)] == ["lint"]


def test_the_as_skill_arm_removes_the_base_skill_it_packages_so_it_is_not_refused(tmp_path):
    _skill(tmp_path / "toy", ".claude/skills/lint", "lint")  # make_repo commits this as the base, which guidance:raw refuses
    root, base = make_repo(tmp_path)
    _skill(root, ".agent-eval/skills/lint", "lint")
    _skill(root, ".claude/skills/lint", "lint")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "skills")
    tasks = [Task("t1", {"base": base}, root)]
    content, globs = root / ".agent-eval", ["**/AGENTS.md", ".claude/skills/**"]
    check_arms([parse_arm("guidance:as-skill+skill:skills/lint")], content, tasks, root / ".git", globs)
    with pytest.raises(ArmError, match="already has .claude/skills/lint"):  # the same base under guidance:raw
        check_arms([parse_arm("guidance:raw+skill:skills/lint")], content, tasks, root / ".git", globs)
