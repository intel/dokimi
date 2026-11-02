# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`dokimi repo skill-reach`: fixture placement, classification and collisions, against a fake harness."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import yaml

from dokimi.artifacts import validate_artifact
from dokimi.cli import build_parser
from dokimi.repos.harness_profile import load_profile
from dokimi.repos.skill_reach import (
    SkillReachError,
    load_catalogue,
    run_skill_reach,
    save_skill_reach,
    scenario_ids,
)

# Lists the top-level skills of .fake/skills (project) and $FAKE_CONFIG_DIR/skills (user); a user skill
# hides a project skill of the same name. It ignores .agents/skills and nested dirs, like Claude Code.
FAKE_HARNESS = r'''
import json, os, re, sys
from pathlib import Path

args = sys.argv[1:]
if args == ["--version"]:
    print("1.2.3 (Fake)"); sys.exit(0)
mode = os.environ.get("FAKE_MODE", "ok")
if mode == "crash":
    print("boom", file=sys.stderr); sys.exit(2)
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
events = [{"type": "system", "subtype": "init", "claude_code_version": "1.2.3"},
          {"type": "result", "subtype": "success", "is_error": False, "result": answer,
           "total_cost_usd": 0.001, "num_turns": 1, "usage": {}, "permission_denials": []}]
for e in events:
    print(json.dumps(e))
'''


def _profile(tmp_path: Path, **override) -> Path:
    script = tmp_path / "fake_harness.py"
    script.write_text(FAKE_HARNESS, encoding="utf-8")
    raw = {
        "schema": "harness_profile.v1",
        "name": "fake",
        "bin": sys.executable,
        "version_cmd": ["{bin}", str(script), "--version"],
        "invoke": ["{bin}", str(script), "-p", "{prompt}", "--model", "{model}", "{tools}"],
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
                           work_dir=work, salt="s", timeout_s=60, **kw)


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
    path = save_skill_reach(payload, tmp_path / "out")
    validate_artifact("skill_discovery", json.loads(path.read_text(encoding="utf-8")))


def test_crash_is_error_not_not_listed(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "crash")
    payload = _run(tmp_path, only=("project-client",))
    assert {r["class"] for r in payload["records"]} == {"error"}
    assert payload["scenarios"][0]["error"]


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
    with pytest.raises(SystemExit) as exc:
        args.func(args)
    assert exc.value.code == 2 and "nope" in capsys.readouterr().err
