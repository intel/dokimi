# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`dokimi repo skill-reach`: fixture placement, classification and collisions, against a fake harness."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from tests.cli_refusal import refusal

from dokimi import process
from dokimi.artifacts import validate_artifact
from dokimi.cli import build_parser
from dokimi.exit_codes import EXIT_USAGE
from dokimi.repos.harness_profile import load_profile
from dokimi.repos.reach import ReachError
from dokimi.repos.skill_reach import (
    Case,
    Fixture,
    Placed,
    SkillReachError,
    _git_init,
    build_fixture,
    classify,
    collisions,
    format_table,
    load_catalogue,
    read_catalogue,
    resolve_at,
    run_skill_reach,
    save_skill_reach,
    scenario_ids,
    skill_text,
)
from dokimi.repos.skill_reach import check_skill_ancestors as real_check_skill_ancestors

# Lists the top-level skills of .fake/skills (project) and $FAKE_CONFIG_DIR/skills (user); a user skill
# hides a project skill of the same name. It ignores .agents/skills and nested dirs, like Claude Code.
FAKE_HARNESS = r'''
import json, os, re, sys, time
from pathlib import Path

args = sys.argv[1:]
if args == ["--version"]:
    print("1.2.3 (Fake)"); sys.exit(0)
mode = os.environ.get("FAKE_MODE", "ok")
if mode == "crash":
    print("boom", file=sys.stderr); sys.exit(2)
if mode == "hang":
    time.sleep(120)
assert os.environ["HOME"] != os.environ.get("REAL_HOME"), "HOME not isolated"
cwd = Path.cwd().resolve()
root = next(p for p in [cwd, *cwd.parents] if (p / ".git").exists())
seen = {}
for base in (root / ".fake/skills", Path(os.environ["FAKE_CONFIG_DIR"]) / "skills"):
    for md in sorted(base.glob("*/SKILL.md")) if base.is_dir() else []:
        text = md.read_text(encoding="utf-8")
        name = re.search(r"^name: (\S+)", text, re.M)
        tok = re.search(r"SK[0-9A-F]{6}", text.split("---")[1])
        if name and tok:
            seen[name.group(1)] = tok.group(0)
answer = "\n".join(f"{n} {t}" for n, t in seen.items()) or "NONE"
if mode == "prose":  # two words and a token: the token's letters are not prose
    answer = "project-client SKBCFC2F"
if mode == "empty":  # exit 0 with an empty result: no usable answer, so an error, not not_listed
    answer = ""
events = [{"type": "system", "subtype": "init", "claude_code_version": "1.2.3"}]
if mode == "look":  # read a file before answering: what it reports then is fetched, not listed
    events.append({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": "t1", "name": "Read", "input": {"file_path": str(root / "x")}}]}})
if mode == "skill":  # invoke the Skill tool before answering: a skill's body is fetched on request, not listed
    events.append({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": "t2", "name": "Skill", "input": {"skill": "project-client"}}]}})
if mode == "grep":  # search the skill files before answering: what it reports then is fetched, not listed
    events.append({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": "t3", "name": "Grep", "input": {"pattern": "SK", "path": str(root)}}]}})
if mode == "bash":  # list the skill dirs with a shell command before answering: a command is looking too
    events.append({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": "t5", "name": "Bash", "input": {"command": "ls .fake/skills"}}]}})
if mode == "edit":  # edit a file before answering: an edit is a tool call too, so what it reports is fetched
    events.append({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": "t4", "name": "Edit", "input": {"file_path": str(root / "x"), "old_string": "a",
                                                                    "new_string": "b"}}]}})
result = {"type": "result", "subtype": "success", "is_error": False, "result": answer,
          "total_cost_usd": 0.001, "num_turns": 1, "usage": {}, "permission_denials": []}
if mode == "nocost":  # no total_cost_usd, as Codex reports it: the spend is unknown, not 0
    del result["total_cost_usd"]
if mode == "errresult":  # an error result that still carries an answer with tokens: not a measurement
    result.update(is_error=True, subtype="error_max_turns")
events.append(result)
for e in events:
    print(json.dumps(e))
if mode == "exit3":  # the answer is printed, then the process fails: not a measurement either
    sys.exit(3)
'''


def _profile(tmp_path: Path, **override) -> Path:
    script = tmp_path / "fake_harness.py"
    script.write_text(FAKE_HARNESS, encoding="utf-8")
    raw = {
        "schema": "harness_profile.v1",
        "name": "fake",
        "bin": sys.executable,
        "version_cmd": ["{bin}", str(script), "--version"],
        "invoke": ["{bin}", str(script), "-p", "{prompt}", "--model", "{model}", "--max-budget-usd", "{budget}",
                   "{tools}"],
        "tools": {"none": ["--tools", ""], "read": ["--tools", "Read"],
                  "skills": ["--tools", "Skill"]},
        "config_dir_env": "FAKE_CONFIG_DIR",
        "env_allow": ["PATH", "FAKE_MODE", "LD_LIBRARY_PATH", "SYSTEMROOT"],
        "trace": "claude-stream-json",
        "guidance": {"names": ["CLAUDE.md"]},
        "skills": {"project_dirs": [".fake/skills"], "user_dirs": ["config:skills"]},
        **override,
    }
    path = tmp_path / "fake.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return path


ONLY = ("project-client", "project-agents", "user-client", "nested-client", "subdir-client",
        "desc-folded", "dup-project-client", "dup-user-client")


@pytest.fixture(autouse=True)
def _no_host_guard(monkeypatch):
    # A developer's own ~/.claude above the temp dir is not what these tests are about (test_ancestor_guard is).
    from dokimi.repos import skill_reach
    monkeypatch.setattr(skill_reach, "check_ancestors", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(skill_reach, "check_skill_ancestors", lambda *a, **k: None)


def _run(tmp_path: Path, only=ONLY, **kw):
    profile = load_profile(str(_profile(tmp_path)))
    cases = [c for c in load_catalogue() if c.id in only]
    work = tmp_path / "work"
    work.mkdir()
    return run_skill_reach(profile=profile, model="m", out_dir=tmp_path / "out", cases=cases,
                           work_dir=work, **{"salt": "s", "timeout_s": 60, **kw})


def _edit_profile(tmp_path: Path, name: str, **changes) -> Path:
    """The fake profile with some keys replaced; a None value removes the key."""
    raw = yaml.safe_load(_profile(tmp_path).read_text(encoding="utf-8"))
    for key, value in changes.items():
        if value is None:
            del raw[key]
        else:
            raw[key] = value
    path = tmp_path / name
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return path


def test_catalogue_loads_and_scenarios_follow_cwd():
    cases = load_catalogue()
    assert len({c.id for c in cases}) == len(cases)
    ids = [s for s, _ in scenario_ids(cases)]
    assert ids[0] == "cwd=." and any("pkg" in s for s in ids)


def test_classes_and_collision(tmp_path):
    payload = _run(tmp_path)
    by = {(r["case"], r["scenario"]): r["class"] for r in payload["records"]}
    assert by[("project-client", "cwd=.")] == "listed"
    assert by[("user-client", "cwd=.")] == "listed"
    assert by[("desc-folded", "cwd=.")] == "listed"
    assert by[("project-agents", "cwd=.")] == "not_listed"
    assert by[("nested-client", "cwd=.")] == "not_listed"
    assert by[("subdir-client", "cwd=.")] == "not_listed"
    (col,) = [c for c in payload["collisions"] if c["scenario"] == "cwd=."]
    assert col["verdict"] == "one" and col["listed"] == ["dup-user-client"]
    assert len(payload["catalogue_sha256"]) == 64
    path = save_skill_reach(payload, tmp_path / "out")
    validate_artifact("skill_discovery", json.loads(path.read_text(encoding="utf-8")))


def test_crash_is_error_not_not_listed(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "crash")
    payload = _run(tmp_path, only=("project-client",))
    assert {r["class"] for r in payload["records"]} == {"error"}
    assert payload["scenarios"][0]["error"]


def test_a_look_before_answering_is_fetched(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "look")
    payload = _run(tmp_path, only=("project-client", "project-agents"))
    by = {r["case"]: r["class"] for r in payload["records"]}
    assert by == {"project-client": "fetched", "project-agents": "not_listed"}
    assert payload["scenarios"][0]["looked"] is True


def test_a_skill_invocation_before_answering_is_fetched(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "skill")
    payload = _run(tmp_path, only=("project-client", "project-agents"))
    by = {r["case"]: r["class"] for r in payload["records"]}
    assert by == {"project-client": "fetched", "project-agents": "not_listed"}
    assert payload["scenarios"][0]["looked"] is True


def test_a_search_tool_before_answering_is_fetched(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "grep")
    payload = _run(tmp_path, only=("project-client", "project-agents"))
    by = {r["case"]: r["class"] for r in payload["records"]}
    assert by == {"project-client": "fetched", "project-agents": "not_listed"}
    assert payload["scenarios"][0]["looked"] is True


def test_an_edit_tool_before_answering_is_fetched(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "edit")
    payload = _run(tmp_path, only=("project-client", "project-agents"))
    by = {r["case"]: r["class"] for r in payload["records"]}
    assert by == {"project-client": "fetched", "project-agents": "not_listed"}
    assert payload["scenarios"][0]["looked"] is True


def test_a_shell_command_before_answering_is_fetched(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "bash")
    payload = _run(tmp_path, only=("project-client", "project-agents"))
    by = {r["case"]: r["class"] for r in payload["records"]}
    assert by == {"project-client": "fetched", "project-agents": "not_listed"}


def test_an_empty_answer_with_exit_0_is_an_error_not_not_listed(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "empty")
    payload = _run(tmp_path, only=("project-client",))
    assert {r["class"] for r in payload["records"]} == {"error"}
    assert "(no answer)" in payload["scenarios"][0]["error"]


def test_a_token_before_a_non_zero_exit_is_an_error_not_a_measurement(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "exit3")
    payload = _run(tmp_path, only=("project-client",))
    assert {r["class"] for r in payload["records"]} == {"error"}
    assert "exit 3" in payload["scenarios"][0]["error"]


def test_an_error_result_with_an_answer_is_an_error_not_a_measurement(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "errresult")
    payload = _run(tmp_path, only=("project-client",))
    assert {r["class"] for r in payload["records"]} == {"error"}
    assert "(error result)" in payload["scenarios"][0]["error"]


def test_an_unknown_cost_is_null_not_zero(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "nocost")
    payload = _run(tmp_path, only=("project-client",))
    assert payload["summary"]["cost_usd"] is None
    validate_artifact("skill_discovery", json.loads(save_skill_reach(payload, tmp_path / "out").read_text(
        encoding="utf-8")))
    assert "cost=unknown" in format_table(payload).splitlines()[0]


@pytest.mark.parametrize("description, cls", [("missing", "listed"), ("single", "not_listed")])
def test_only_a_description_missing_skill_is_listed_by_its_body_token(description, cls):
    case = Case(id="c", at="project:x", name="c", dirname="c", description=description)
    placed = Placed(case, "project", "c", "SK000001", "SB000001")
    assert classify(placed, SimpleNamespace(answer="c SB000001"), looked=False) == {"class": cls, "body": True}


def test_a_token_in_the_answer_is_not_counted_as_prose(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "prose")
    payload = _run(tmp_path, only=("project-client",))
    assert payload["scenarios"][0]["prose"] == 2  # "project", "client"; SKBCFC2F is a token, not words


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@invalid", *args], cwd=cwd, check=True,
                          capture_output=True, text=True).stdout.strip()


def test_git_init_ignores_the_callers_repository(tmp_path, monkeypatch):
    other = tmp_path / "other"
    other.mkdir()
    _git(other, "init", "-q")
    (other / "notes.txt").write_text("mine\n", encoding="utf-8")
    _git(other, "add", "-A")
    _git(other, "commit", "-q", "-m", "mine")
    head = _git(other, "rev-parse", "HEAD")
    monkeypatch.setenv("GIT_DIR", str(other / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(other))
    workspace = tmp_path / "ws"
    workspace.mkdir()
    _git_init(workspace)
    assert (workspace / ".git").is_dir()
    assert _git(other, "rev-parse", "HEAD") == head


@pytest.mark.skipif(os.name == "nt", reason="the hook is a POSIX shell script")
def test_git_init_ignores_the_callers_global_config(tmp_path, monkeypatch):
    home, hooks, marker = tmp_path / "home", tmp_path / "hooks", tmp_path / "hook-ran"
    home.mkdir()
    hooks.mkdir()
    hook = hooks / "pre-commit"
    hook.write_text(f"#!/bin/sh\ntouch '{marker}'\n", encoding="utf-8")
    hook.chmod(0o755)
    (home / ".gitconfig").write_text(f"[core]\n\thooksPath = {hooks}\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    workspace = tmp_path / "ws"
    workspace.mkdir()
    _git_init(workspace)
    assert not marker.exists()


def test_a_missing_git_executable_is_a_skill_reach_error(tmp_path, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: "/nonexistent/git")
    with pytest.raises(SkillReachError, match="could not start"):
        _git_init(tmp_path)


def test_profile_without_skills_block_is_refused(tmp_path):
    raw = yaml.safe_load(_profile(tmp_path).read_text(encoding="utf-8"))
    del raw["skills"]
    p = tmp_path / "noskills.yaml"
    p.write_text(yaml.safe_dump(raw), encoding="utf-8")
    with pytest.raises(SkillReachError, match="skills"):
        run_skill_reach(profile=load_profile(str(p)), model="m", out_dir=tmp_path / "o", cases=[])


def test_cli_dry_run_and_unknown_case(tmp_path, capsys):
    base = ["repo", "skill-reach", "--model", "m", "--harness", str(_profile(tmp_path)), "--dry-run"]
    args = build_parser().parse_args([*base, "--only", "project-client"])
    args.func(args)
    assert json.loads(capsys.readouterr().out)["cases"] == ["project-client"]
    args = build_parser().parse_args([*base, "--only", "nope"])
    with refusal() as out:
        args.func(args)
    assert out.code == 2 and "nope" in capsys.readouterr().err


def _cli(*argv: str) -> None:
    args = build_parser().parse_args(["repo", "skill-reach", "--model", "m", *argv])
    args.func(args)


@pytest.mark.parametrize("changes, message", [
    pytest.param({"native_prompt": True, "invoke": ["{bin}", "--model", "{model}", "{tools}"]}, "native_prompt",
                 id="native-prompt"),
    pytest.param({"skills": None}, "declares no `skills` block", id="no-skills-block"),
    pytest.param({"tools": {"none": ["--tools", ""], "read": ["--tools", "Read"]}}, "tool mode", id="no-skills-tool"),
    pytest.param({"trace": "text"}, "no tool events", id="text-trace"),
])
def test_cli_dry_run_refuses_what_a_run_would_refuse(tmp_path, capsys, changes, message):
    profile = _edit_profile(tmp_path, "refused.yaml", **changes)
    with refusal() as out:
        _cli("--harness", str(profile), "--dry-run")
    assert out.code == EXIT_USAGE and message in capsys.readouterr().err


@pytest.fixture
def _real_ancestor_guard(monkeypatch):
    """Undo the autouse no-op guard, so the real ancestor checks run."""
    from dokimi.repos import reach, skill_reach
    monkeypatch.setattr(skill_reach, "check_ancestors", reach.check_ancestors)
    monkeypatch.setattr(skill_reach, "check_skill_ancestors", real_check_skill_ancestors)


def test_cli_dry_run_refuses_when_no_selected_case_has_a_skill_dir_in_the_profile(tmp_path, capsys):
    profile = _edit_profile(tmp_path, "no-user.yaml", skills={"project_dirs": [".fake/skills"]})
    with refusal() as out:
        _cli("--harness", str(profile), "--only", "user-client", "--dry-run")
    assert out.code == EXIT_USAGE and "no case could be placed" in capsys.readouterr().err


def test_cli_dry_run_refuses_a_work_dir_under_guidance_as_a_run_would(tmp_path, capsys, _real_ancestor_guard):
    (tmp_path / "CLAUDE.md").write_text("guidance\n", encoding="utf-8")
    work = tmp_path / "work"
    work.mkdir()
    with refusal() as out:
        _cli("--harness", str(_profile(tmp_path)), "--only", "project-client", "--dry-run", "--work-dir", str(work))
    assert out.code == EXIT_USAGE and "guidance files above the workspace" in capsys.readouterr().err


def test_cli_dry_run_refuses_a_work_dir_that_is_not_a_directory(tmp_path, capsys):
    with refusal() as out:
        _cli("--harness", str(_profile(tmp_path)), "--dry-run", "--work-dir", str(tmp_path / "missing"))
    assert out.code == EXIT_USAGE and "is not a directory" in capsys.readouterr().err


def test_the_ancestor_guard_also_refuses_the_profiles_own_guidance_names(tmp_path, _real_ancestor_guard):
    (tmp_path / "AGENTS.override.md").write_text("guidance\n", encoding="utf-8")
    workspace = tmp_path / "ws"
    workspace.mkdir()
    real_check_skill_ancestors(workspace)  # not a name the default guard holds: passes
    with pytest.raises(ReachError, match="AGENTS.override.md"):
        real_check_skill_ancestors(workspace, ("AGENTS.override.md",))


def test_cli_run_passes_its_timeout_salt_and_budget_to_the_run(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "hang")
    work = tmp_path / "work"
    work.mkdir()
    with pytest.raises(SystemExit) as exc:
        _cli("--harness", str(_profile(tmp_path)), "--only", "project-client", "--out", str(tmp_path / "out"),
             "--work-dir", str(work), "--timeout", "1", "--salt", "SALT42", "--budget", "0.5")
    assert exc.value.code == 1
    captured = capsys.readouterr()
    assert "at most $0.5 each" in captured.err and "timed out after 1 seconds" in captured.out
    artifact = json.loads((tmp_path / "out" / "skill-discovery.json").read_text(encoding="utf-8"))
    assert artifact["salt"] == "SALT42"


@pytest.mark.parametrize("budget", ["0", "-1", "nan"])
def test_cli_refuses_a_budget_that_is_not_a_positive_number(tmp_path, capsys, budget):
    with refusal() as out:
        _cli("--harness", str(_profile(tmp_path)), "--budget", budget)
    assert out.code == EXIT_USAGE
    assert "--budget must be > 0" in capsys.readouterr().err


def test_cli_run_writes_the_artifact_and_the_table(tmp_path, capsys):
    work = tmp_path / "work"
    work.mkdir()
    _cli("--harness", str(_profile(tmp_path)), "--only", "project-client",
         "--out", str(tmp_path / "out"), "--work-dir", str(work))
    captured = capsys.readouterr()
    assert "1 cases, 1 scenarios, at most $0.1 each" in captured.err
    assert "fake 1.2.3  model=m" in captured.out and "wrote " in captured.out
    validate_artifact("skill_discovery", json.loads((tmp_path / "out" / "skill-discovery.json").read_text(
        encoding="utf-8")))


def test_cli_run_exits_1_when_a_scenario_errors(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "crash")
    work = tmp_path / "work"
    work.mkdir()
    with pytest.raises(SystemExit) as exc:
        _cli("--harness", str(_profile(tmp_path)), "--only", "project-client",
             "--out", str(tmp_path / "out"), "--work-dir", str(work))
    assert exc.value.code == 1
    out = capsys.readouterr().out
    assert "error in cwd=.: exit 2 (no answer): boom" in out and "wrote " in out


def test_cli_run_reports_a_missing_work_dir_and_a_blocked_out_dir(tmp_path, capsys):
    profile = _profile(tmp_path)
    with refusal() as out:
        _cli("--harness", str(profile), "--only", "project-client", "--out", str(tmp_path / "out"),
             "--work-dir", str(tmp_path / "no-such-parent" / "work"))
    assert out.code == EXIT_USAGE and "is not a directory" in capsys.readouterr().err
    blocked = tmp_path / "blocked"  # a file where the output directory should be
    blocked.write_text("not a directory", encoding="utf-8")
    work = tmp_path / "work"
    work.mkdir()
    with refusal() as out:
        _cli("--harness", str(profile), "--only", "project-client", "--out", str(blocked), "--work-dir", str(work))
    assert out.code == EXIT_USAGE and "is not a directory" in capsys.readouterr().err
    assert not (tmp_path / "out").exists()


def test_cli_run_reports_an_artifact_it_cannot_write_and_exits_1(tmp_path, capsys):
    # the run itself succeeds; a directory where skill-discovery.json goes makes the save raise an OSError
    (tmp_path / "out" / "skill-discovery.json").mkdir(parents=True)
    work = tmp_path / "work"
    work.mkdir()
    with pytest.raises(SystemExit) as exc:
        _cli("--harness", str(_profile(tmp_path)), "--only", "project-client", "--out", str(tmp_path / "out"),
             "--work-dir", str(work))
    assert exc.value.code == 1
    captured = capsys.readouterr()
    assert "✗ " in captured.err and "skill-discovery.json" in captured.err and "wrote " not in captured.out


@pytest.mark.skipif(os.name == "nt", reason="the wrapper is a POSIX shell script")
def test_cli_run_starts_the_harness_with_the_bin_it_is_given(tmp_path, capsys):
    # the wrapper runs the same fake harness in crash mode: only a run through --bin reports its exit 2
    wrapper = tmp_path / "harness-wrapper"
    wrapper.write_text(f'#!/bin/sh\nFAKE_MODE=crash exec "{sys.executable}" "$@"\n', encoding="utf-8")
    wrapper.chmod(0o755)
    work = tmp_path / "work"
    work.mkdir()
    with pytest.raises(SystemExit) as exc:
        _cli("--harness", str(_profile(tmp_path)), "--only", "project-client", "--out", str(tmp_path / "out"),
             "--work-dir", str(work), "--bin", str(wrapper))
    assert exc.value.code == 1
    assert "error in cwd=.: exit 2 (no answer): boom" in capsys.readouterr().out


def test_cli_run_with_keep_leaves_the_fixture_and_names_it(tmp_path, capsys):
    work = tmp_path / "work"
    work.mkdir()
    _cli("--harness", str(_profile(tmp_path)), "--only", "project-client", "--out", str(tmp_path / "out"),
         "--work-dir", str(work), "--keep")
    artifact = json.loads((tmp_path / "out" / "skill-discovery.json").read_text(encoding="utf-8"))
    kept = list(work.resolve().iterdir())
    assert [str(p) for p in kept] == [artifact["workspace"]] and (kept[0] / "ws" / ".git").is_dir()


def test_cli_refuses_a_budget_for_a_harness_with_no_spend_cap(capsys):
    with refusal() as out:
        _cli("--harness", "codex", "--budget", "0.01", "--dry-run")
    assert out.code == EXIT_USAGE
    assert "has no spend-cap flag" in capsys.readouterr().err


def test_cli_run_without_a_budget_says_a_spend_cap_is_not_enforced(tmp_path, capsys):
    profile = _edit_profile(tmp_path, "nobudget.yaml", invoke=[
        "{bin}", str(tmp_path / "fake_harness.py"), "-p", "{prompt}", "--model", "{model}", "{tools}"])
    work = tmp_path / "work"
    work.mkdir()
    _cli("--harness", str(profile), "--only", "project-client", "--out", str(tmp_path / "out"),
         "--work-dir", str(work))
    assert "no spend cap (the timeout bounds each run)" in capsys.readouterr().err


def test_cli_run_refuses_a_profile_without_a_skills_block(tmp_path, capsys):
    profile = _edit_profile(tmp_path, "noskills.yaml", skills=None)
    with refusal() as out:
        _cli("--harness", str(profile), "--out", str(tmp_path / "out"))
    assert out.code == EXIT_USAGE
    err = capsys.readouterr().err
    assert "declares no `skills` block" in err and "cases," not in err


@pytest.mark.parametrize("only", [["--only", "user-client"], []], ids=["only-the-user-case", "whole-catalogue"])
def test_cli_dry_run_refuses_a_profile_skill_dir_that_escapes(tmp_path, capsys, only):
    profile = _edit_profile(tmp_path, "escape.yaml", skills={"project_dirs": [".fake/skills"],
                                                             "user_dirs": ["config:../x"]})
    with refusal() as out:
        _cli("--harness", str(profile), *only, "--dry-run")
    assert out.code == EXIT_USAGE and "must be project:|home:|config:" in capsys.readouterr().err


CATALOGUE_HEAD = "schema: skill_fixtures.v1\ncases:\n"


def _catalogue(*items: str) -> str:
    return CATALOGUE_HEAD + "".join(f"  - {item}\n" for item in items)


BAD_CATALOGUES = [
    pytest.param("schema: [unclosed\n", "cannot read the catalogue", id="not-yaml"),
    pytest.param("schema: other.v1\ncases: []\n", "not a skill_fixtures.v1", id="wrong-schema"),
    pytest.param("schema: skill_fixtures.v1\ncases: 3\n", "not a skill_fixtures.v1", id="no-cases-list"),
    pytest.param(_catalogue("{id: a, at: 'project:x'}", "{id: a, at: 'project:y'}"), "appears twice",
                 id="duplicate-id"),
    pytest.param(_catalogue("{at: 'project:x'}"), "needs `id` and `at`", id="no-id"),
    pytest.param(_catalogue("{id: a, at: 'project:x', colour: red}"), "unknown field", id="unknown-field"),
    pytest.param(_catalogue("{id: a, at: 'project:x', 123: red, colour: blue}"), "unknown field(s) 123, colour",
                 id="unknown-field-not-a-string"),
    pytest.param(_catalogue('{id: a, at: "project:x", dirname: "d\\0e"}'), "inside the fixture", id="dirname-nul"),
    pytest.param(_catalogue('{id: a, at: "project:x", cwd: "d\\0e"}'), "inside the fixture", id="cwd-nul"),
    pytest.param(_catalogue("{id: a, at: 'project:x', description: fancy}"), "description must be one of",
                 id="bad-description"),
    pytest.param(_catalogue("{id: a, at: 'project:x', eol: cr}"), "eol is lf|crlf", id="bad-eol"),
    pytest.param(_catalogue("{id: a, at: 'project:x', link: hard}"), "eol is lf|crlf", id="bad-link"),
    pytest.param(_catalogue("{id: a, at: 'project:x', dirname: '../escape'}"), "inside the fixture",
                 id="dirname-escapes"),
    pytest.param(_catalogue("{id: a, at: 'project:x', cwd: /etc}"), "inside the fixture", id="cwd-absolute"),
    pytest.param(_catalogue("{id: a, at: 'project:x', dirname: 'C:/escape'}"), "inside the fixture",
                 id="dirname-drive"),
    pytest.param(_catalogue("{id: a, at: 'project:x', cwd: 'C:escape'}"), "inside the fixture", id="cwd-drive"),
    pytest.param(_catalogue("{id: a, at: 'project:x', name: one, collision: g}",
                            "{id: b, at: 'project:x', name: two, collision: g}"),
                 "mixes names", id="collision-mixes-names"),
    pytest.param(_catalogue("{id: a, at: 'project:x', cwd: 1}"), "cwd must be a string", id="cwd-not-a-string"),
    pytest.param(_catalogue("{id: a, at: 'project:x', collision: [g]}"), "collision must be a string",
                 id="collision-not-a-string"),
    pytest.param(_catalogue("{id: a, at: 'nowhere:x'}"), "must be project:|home:|config:", id="at-unknown-scope"),
    pytest.param(_catalogue("{id: a, at: 'project:../escape'}"), "must be project:|home:|config:", id="at-escapes"),
    pytest.param(_catalogue("{id: a, at: 'project:{client_projet}'}"), "must be project:|home:|config:",
                 id="at-placeholder-typo"),
]


@pytest.mark.parametrize("text, message", BAD_CATALOGUES)
def test_a_bad_catalogue_is_refused(tmp_path, text, message):
    path = tmp_path / "cat.yaml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(SkillReachError, match=re.escape(message)):
        load_catalogue(path)


@pytest.mark.parametrize("at", ["nowhere:x", "project:../escape", "project:{client_projet}"])
def test_cli_dry_run_refuses_a_catalogue_location_a_run_would_refuse(tmp_path, capsys, at):
    path = tmp_path / "cat.yaml"
    path.write_text(_catalogue(f"{{id: a, at: '{at}'}}"), encoding="utf-8")
    with refusal() as out:
        _cli("--harness", str(_profile(tmp_path)), "--catalogue", str(path), "--dry-run")
    assert out.code == EXIT_USAGE and "must be project:|home:|config:" in capsys.readouterr().err


@pytest.mark.parametrize("text", [CATALOGUE_HEAD + "  - {id: a, at: 2026-99-99}\n", "[" * 100000],
                         ids=["bad-date", "deep"])
def test_cli_dry_run_refuses_a_catalogue_whose_yaml_does_not_load(tmp_path, capsys, text):
    path = tmp_path / "cat.yaml"
    path.write_text(text, encoding="utf-8")
    with refusal() as out:
        _cli("--harness", str(_profile(tmp_path)), "--catalogue", str(path), "--dry-run")
    assert out.code == EXIT_USAGE and f"{path}: cannot read the catalogue" in capsys.readouterr().err


@pytest.mark.skipif(not Path("/dev/fd").is_dir(), reason="needs /dev/fd")
def test_the_digest_is_of_the_catalogue_bytes_read_from_a_pipe(tmp_path):
    data = _catalogue("{id: project-client, at: 'project:{client_project}'}").encode()
    read_fd, write_fd = os.pipe()
    os.write(write_fd, data)
    os.close(write_fd)
    work = tmp_path / "work"
    work.mkdir()
    try:
        _cli("--harness", str(_profile(tmp_path)), "--catalogue", f"/dev/fd/{read_fd}",
             "--out", str(tmp_path / "out"), "--work-dir", str(work))
    finally:
        os.close(read_fd)
    payload = json.loads((tmp_path / "out" / "skill-discovery.json").read_text(encoding="utf-8"))
    assert payload["catalogue_sha256"] == hashlib.sha256(data).hexdigest()


def test_a_catalogue_removed_after_loading_still_gives_an_artifact(tmp_path):
    path = tmp_path / "cat.yaml"
    path.write_text(_catalogue("{id: project-client, at: 'project:{client_project}'}"), encoding="utf-8")
    cases, digest = read_catalogue(path)
    path.unlink()
    work = tmp_path / "work"
    work.mkdir()
    payload = run_skill_reach(profile=load_profile(str(_profile(tmp_path))), model="m", out_dir=tmp_path / "out",
                              cases=cases, catalogue=path, catalogue_sha256=digest, work_dir=work, salt="s",
                              timeout_s=60)
    assert payload["catalogue_sha256"] == digest


def test_cases_without_a_digest_are_refused_when_their_catalogue_is_gone(tmp_path):
    cases = load_catalogue()[:1]
    with pytest.raises(SkillReachError, match="cannot read the catalogue"):
        run_skill_reach(profile=load_profile(str(_profile(tmp_path))), model="m", out_dir=tmp_path / "out",
                        cases=cases, catalogue=tmp_path / "gone.yaml", work_dir=tmp_path)
    assert not (tmp_path / "out").exists()


def test_a_missing_catalogue_is_refused(tmp_path):
    with pytest.raises(SkillReachError, match="cannot read the catalogue"):
        load_catalogue(tmp_path / "missing.yaml")


def test_a_catalogue_that_is_not_utf8_is_refused(tmp_path):
    path = tmp_path / "cat.yaml"
    path.write_bytes(b"schema: skill_fixtures.v1\ncases:\n  - {id: a, at: 'project:x', name: \xff}\n")
    with pytest.raises(SkillReachError, match="cannot read the catalogue"):
        load_catalogue(path)


def _placed(cid: str, collision: str | None = None) -> Placed:
    case = Case(id=cid, at="project:x", name=cid, dirname=cid, collision=collision)
    return Placed(case, "project", cid, "SK000001", "SB000001")


def _rec(case: str, cls: str, scenario: str = "cwd=.") -> dict:
    return {"case": case, "scenario": scenario, "class": cls}


def test_a_collision_verdict_counts_only_the_listed_copies():
    fixture = Fixture(placed=[_placed("a", "g"), _placed("b", "g"), _placed("c", "g"), _placed("solo", "s")])
    records = [_rec("a", "listed"), _rec("b", "not_listed"), _rec("c", "listed"), _rec("solo", "listed")]
    (verdict,) = collisions(fixture, records)  # a one-member group is not a collision
    assert verdict == {"group": "g", "scenario": "cwd=.", "members": ["a", "b", "c"],
                       "listed": ["a", "c"], "verdict": "some"}


@pytest.mark.parametrize("classes, verdict", [
    (("listed", "listed"), "all"),
    (("not_listed", "not_listed"), "none"),
    (("listed", "not_listed"), "one"),
    (("listed", "error"), "error"),  # an errored copy makes the group's verdict unknown
    (("fetched", "not_listed"), "fetched"),  # the agent looked for a copy itself: no precedence was measured
    (("listed", "fetched"), "fetched"),
])
def test_collision_verdicts(classes, verdict):
    fixture = Fixture(placed=[_placed("a", "g"), _placed("b", "g")])
    (result,) = collisions(fixture, [_rec("a", classes[0]), _rec("b", classes[1])])
    assert result["verdict"] == verdict


def test_resolve_at_uses_the_profile_dirs_and_refuses_a_bad_location():
    dirs = {"client_project": ".fake/skills", "client_user": "config:skills"}

    def case(at: str) -> Case:
        return Case(id="c", at=at, name="c", dirname="c")

    assert resolve_at(case("project:{client_project}"), dirs) == ("project", ".fake/skills")
    assert resolve_at(case("{client_user}/extra"), dirs) == ("config", "skills/extra")
    assert resolve_at(case("project:{client_project}"), {}) is None
    assert resolve_at(case("{client_user}"), {}) is None
    with pytest.raises(SkillReachError, match="must be"):
        resolve_at(case("cloud:skills"), dirs)


@pytest.mark.parametrize("at", ["project:C:/existing", "project:C:escape", "project:x\x00y"])
def test_a_drive_qualified_or_nul_location_is_refused(at):
    with pytest.raises(SkillReachError, match="must be"):
        resolve_at(Case(id="c", at=at, name="c", dirname="c"), {})


@pytest.mark.parametrize("case_id", ["../../escape", "ABSOLUTE"])
def test_a_symlink_case_id_cannot_write_outside_the_fixture(tmp_path, case_id):
    escape = tmp_path / "escape"
    case_id = str(escape) if case_id == "ABSOLUTE" else case_id
    profile = load_profile(str(_profile(tmp_path)))
    base = tmp_path / "base"
    roots = {scope: base / scope for scope in ("project", "home", "config")}
    for root in roots.values():
        root.mkdir(parents=True)
    case = Case(id=case_id, at="project:{client_project}", name="skill", dirname="skill", link="symlink")
    build_fixture([case], profile, roots, base / "outside", "s")
    assert not escape.exists()


def test_build_fixture_links_a_symlink_case_and_skips_an_undeclared_dir(tmp_path):
    try:  # Windows without symlink privilege: the fallback is test_build_fixture_reports_a_symlink_the_os_refuses
        os.symlink(tmp_path, tmp_path / "probe-link", target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"cannot create a symlink here: {exc}")
    profile = load_profile(str(_edit_profile(tmp_path, "project-only.yaml",
                                             skills={"project_dirs": [".fake/skills"]})))
    cases = [c for c in load_catalogue() if c.id in ("symlink-client", "user-client")]
    roots = {scope: tmp_path / scope for scope in ("project", "home", "config")}
    for root in roots.values():
        root.mkdir()
    fixture = build_fixture(cases, profile, roots, tmp_path / "outside", "s")
    assert [(p.case.id, p.link_kind) for p in fixture.placed] == [("symlink-client", "symlink")]
    assert (roots["project"] / ".fake/skills/symlink-client/SKILL.md").is_file()
    assert [s["case"] for s in fixture.skipped] == ["user-client"]
    assert "declares no skill dir" in fixture.skipped[0]["reason"]


def test_build_fixture_reports_a_symlink_the_os_refuses(tmp_path, monkeypatch):
    def refuse(*args, **kwargs):
        raise OSError("a symlink needs developer mode")

    monkeypatch.setattr(os, "symlink", refuse)
    profile = load_profile(str(_profile(tmp_path)))
    cases = [c for c in load_catalogue() if c.id == "symlink-client"]
    roots = {scope: tmp_path / scope for scope in ("project", "home", "config")}
    for root in roots.values():
        root.mkdir()
    fixture = build_fixture(cases, profile, roots, tmp_path / "outside", "s")
    assert fixture.placed == []
    assert fixture.skipped[0]["reason"].startswith("this OS refused a symlink")


def _roots(tmp_path: Path) -> dict[str, Path]:
    roots = {scope: tmp_path / scope for scope in ("project", "home", "config")}
    for root in roots.values():
        root.mkdir()
    return roots


def test_a_second_case_for_the_same_directory_is_skipped_not_written(tmp_path):
    profile = load_profile(str(_profile(tmp_path)))
    cases = [Case(id="first", at="project:x", name="first", dirname="same"),
             Case(id="second", at="project:x", name="second", dirname="same")]
    roots = _roots(tmp_path)
    fixture = build_fixture(cases, profile, roots, tmp_path / "outside", "s")
    assert [p.case.id for p in fixture.placed] == ["first"]
    assert fixture.skipped == [{"case": "second", "reason": "project:x/same is taken by first"}]
    assert "name: first" in (roots["project"] / "x/same/SKILL.md").read_text(encoding="utf-8")


def test_the_bundled_agents_dir_collision_places_one_copy(tmp_path):
    profile = load_profile(str(_edit_profile(tmp_path, "agents.yaml", skills={"project_dirs": [".agents/skills"]})))
    cases = [c for c in load_catalogue() if c.id in ("dup-project-agents", "dup-project-client2")]
    fixture = build_fixture(cases, profile, _roots(tmp_path), tmp_path / "outside", "s")
    assert [p.case.id for p in fixture.placed] == ["dup-project-agents"]
    assert [s["case"] for s in fixture.skipped] == ["dup-project-client2"]


@pytest.mark.parametrize("name", ["null", "true", "123", "a: b", "project-client", "2026-99-99",
                                  pytest.param("[" * 100000, id="deep")])
def test_the_skill_name_loads_back_as_the_same_string(name):
    text = skill_text(Case(id="c", at="project:x", name=name, dirname="c"), "SK000001", "SB000001")
    assert yaml.safe_load(text.split("---")[1])["name"] == name


def test_a_skill_dir_above_the_workspace_is_refused(tmp_path):
    (tmp_path / ".claude" / "skills").mkdir(parents=True)
    workspace = tmp_path / "ws"
    workspace.mkdir()
    with pytest.raises(ReachError, match="is above the fixture"):
        real_check_skill_ancestors(workspace)


def test_a_native_prompt_profile_is_refused(tmp_path):
    script = tmp_path / "fake_harness.py"
    profile = _edit_profile(tmp_path, "native.yaml", native_prompt=True,
                            invoke=["{bin}", str(script), "--model", "{model}", "{tools}"])
    with pytest.raises(SkillReachError, match="native_prompt"):
        run_skill_reach(profile=load_profile(str(profile)), model="m", out_dir=tmp_path / "o", cases=[])


def test_a_profile_without_a_skills_tool_mode_is_refused(tmp_path):
    profile = _edit_profile(tmp_path, "notools.yaml", tools={"none": ["--tools", ""], "read": ["--tools", "Read"]})
    with pytest.raises(SkillReachError, match="tool mode"):
        run_skill_reach(profile=load_profile(str(profile)), model="m", out_dir=tmp_path / "o", cases=[])


def test_only_places_the_named_cases(tmp_path):
    payload = run_skill_reach(profile=load_profile(str(_profile(tmp_path))), model="m", out_dir=tmp_path / "o",
                              cases=load_catalogue(), only=("project-client",), salt="s", timeout_s=60,
                              work_dir=tmp_path)
    assert [c["id"] for c in payload["cases"]] == ["project-client"]


def test_an_unknown_only_case_is_refused(tmp_path):
    with pytest.raises(SkillReachError, match="matches no case"):
        run_skill_reach(profile=load_profile(str(_profile(tmp_path))), model="m", out_dir=tmp_path / "o",
                        cases=load_catalogue(), only=("nope",))


def test_a_profile_that_places_no_case_is_refused(tmp_path):
    profile = _edit_profile(tmp_path, "project-only.yaml", skills={"project_dirs": [".fake/skills"]})
    cases = [c for c in load_catalogue() if c.id == "user-client"]
    work = tmp_path / "work"
    work.mkdir()
    with pytest.raises(SkillReachError, match="no case could be placed"):
        run_skill_reach(profile=load_profile(str(profile)), model="m", out_dir=tmp_path / "o", cases=cases,
                        work_dir=work)


def test_a_run_leaves_the_guidance_reach_traces_in_a_shared_out_dir(tmp_path):
    reach = tmp_path / "out" / "traces"
    reach.mkdir(parents=True)
    (reach / "00-root.stdout").write_text("reach", encoding="utf-8")
    (reach / "03-cwd_src.events.jsonl").write_text("reach", encoding="utf-8")
    _run(tmp_path, only=("project-client",))
    assert sorted(p.name for p in reach.iterdir()) == ["00-root.stdout", "03-cwd_src.events.jsonl"]
    assert (tmp_path / "out" / "skill-traces" / "00-cwd_..stdout").is_file()


def test_a_new_run_clears_the_traces_of_an_earlier_one(tmp_path):
    trace = tmp_path / "out" / "skill-traces"
    trace.mkdir(parents=True)
    (trace / "07-stale.stdout").write_text("old", encoding="utf-8")
    (trace / "07-stale.events.jsonl").write_text("old", encoding="utf-8")
    (trace / "notes.txt").write_text("keep", encoding="utf-8")
    _run(tmp_path, only=("project-client",))
    assert not (trace / "07-stale.stdout").exists()
    assert not (trace / "07-stale.events.jsonl").exists()
    assert (trace / "notes.txt").read_text(encoding="utf-8") == "keep"


def test_a_failed_git_init_stops_the_run(tmp_path, monkeypatch):
    def failing(argv, **kwargs):
        return SimpleNamespace(returncode=128, stderr="fatal: no", stdout="")

    monkeypatch.setattr(process, "run", failing)
    with pytest.raises(SkillReachError, match="git init -q failed: fatal: no"):
        _run(tmp_path, only=("project-client",))


def test_a_harness_that_times_out_is_an_error_not_not_listed(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "hang")
    payload = _run(tmp_path, only=("project-client",), timeout_s=1)
    assert {r["class"] for r in payload["records"]} == {"error"}
    assert "timed out after 1 seconds" in payload["scenarios"][0]["error"]


def test_a_harness_that_cannot_start_is_an_error(tmp_path):
    profile = _edit_profile(tmp_path, "missing-exe.yaml",
                            invoke=["/nonexistent/fake-harness", "-p", "{prompt}", "--model", "{model}", "{tools}"])
    work = tmp_path / "work"
    work.mkdir()
    cases = [c for c in load_catalogue() if c.id == "project-client"]
    payload = run_skill_reach(profile=load_profile(str(profile)), model="m", out_dir=tmp_path / "out",
                              cases=cases, work_dir=work, salt="s", timeout_s=60)
    assert {r["class"] for r in payload["records"]} == {"error"}
    assert "FileNotFoundError" in payload["scenarios"][0]["error"]


def test_the_table_shows_the_collision_verdict_and_legend(tmp_path):
    text = format_table(_run(tmp_path))
    assert "collision project-vs-user [cwd=.]: one: dup-user-client" in text
    assert "L listed" in text


def test_the_table_flags_skipped_and_looked_scenarios(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "look")
    profile = _edit_profile(tmp_path, "project-only.yaml", skills={"project_dirs": [".fake/skills"]})
    cases = [c for c in load_catalogue() if c.id in ("project-client", "user-client")]
    work = tmp_path / "work"
    work.mkdir()
    payload = run_skill_reach(profile=load_profile(str(profile)), model="m", out_dir=tmp_path / "out",
                              cases=cases, work_dir=work, salt="s", timeout_s=60)
    text = format_table(payload)
    assert "skipped user-client: fake declares no skill dir for {client_user}" in text
    assert "warning: cwd=.: the agent ran a command, read a file or used a tool" in text


def test_the_table_names_a_scenario_that_errored(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "crash")
    assert "error in cwd=.: exit 2 (no answer): boom" in format_table(_run(tmp_path, only=("project-client",)))


def test_the_table_names_a_token_the_harness_invented(tmp_path):
    payload = _run(tmp_path, only=("project-client",))
    payload["scenarios"][0]["unknown"] = ["SK0BAD00"]
    assert "unknown tokens in cwd=.: SK0BAD00" in format_table(payload)
