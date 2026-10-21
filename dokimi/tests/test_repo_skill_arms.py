# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`repo run` skill arms (skill:, skill-catalog:, joined with guidance by +) and cache tokens. No docker, no LLM."""

from __future__ import annotations

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
