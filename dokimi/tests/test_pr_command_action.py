# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""actions/pr-command and actions/opportunities-comment, and the example workflow that wires them.

The gate runs only for the exact command from a writer or an admin; anyone else gets one reply.
Each script is run the way the runner does (bash -e -o pipefail) with a fake `gh` that logs its
calls and answers from env vars, so no network is used.
"""

from __future__ import annotations

import os
import re
import stat
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
GATE = ROOT / "actions/pr-command/action.yml"
COMMENT = ROOT / "actions/opportunities-comment/action.yml"
EXAMPLE = ROOT / "dokimi/docs/examples/dokimi-pr-command.yml"
SHA = "a" * 40

FAKE_GH = r"""#!/usr/bin/env bash
printf '%s\n' "$*" >> "$GH_LOG"
case "$*" in
  *collaborators*permission*) [ "$FAKE_PERM" = fail ] && exit 1; echo "$FAKE_PERM" ;;
  *"repos/o/r/pulls/"*) echo "$FAKE_SHA" ;;
  *"--paginate"*) [ -n "$FAKE_IDS" ] && echo "$FAKE_IDS"; exit 0 ;;
  *"-F body=@"*) all="$*"; f="${all##*body=@}"; f="${f%% *}"; cp -- "$f" "$GH_BODY" ;;
esac
exit 0
"""

FAKE_DOKIMI = r"""#!/usr/bin/env bash
printf '%s\n' "$*" >> "$DOKIMI_LOG"
out=""; while [ $# -gt 0 ]; do [ "$1" = --out ] && out="$2"; shift; done
printf '| # | title |\n|---|---|\n| 1 | ping @someone <!-- dokimi-opportunities --> |\n' > "$out"
"""


def _script(path: Path) -> str:
    return yaml.safe_load(path.read_text(encoding="utf-8"))["runs"]["steps"][0]["run"]


def _bin(tmp_path: Path) -> Path:
    b = tmp_path / "bin"
    b.mkdir(exist_ok=True)
    for name, text in (("gh", FAKE_GH), ("dokimi", FAKE_DOKIMI)):
        (b / name).write_text(text, encoding="utf-8", newline="\n")
        (b / name).chmod((b / name).stat().st_mode | stat.S_IEXEC)
    return b


def _run(tmp_path: Path, script: str, **env: str) -> tuple[subprocess.CompletedProcess[str], dict]:
    for name in ("output", "summary", "gh.log", "dokimi.log"):
        (tmp_path / name).touch()
    base = {
        "PATH": f"{_bin(tmp_path)}{os.pathsep}{os.environ['PATH']}",
        "RUNNER_TEMP": str(tmp_path), "GITHUB_OUTPUT": str(tmp_path / "output"),
        "GITHUB_STEP_SUMMARY": str(tmp_path / "summary"),
        "GH_LOG": str(tmp_path / "gh.log"), "GH_BODY": str(tmp_path / "body.md"),
        "DOKIMI_LOG": str(tmp_path / "dokimi.log"),
        "REPO": "o/r", "GH_TOKEN": "t", "DEFAULT_TOKEN": "t", "FAKE_SHA": SHA, "FAKE_IDS": "",
    }
    r = subprocess.run(["bash", "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", script],
                       env={**base, **env}, capture_output=True, text=True, check=False, cwd=tmp_path)
    out = {}
    for line in (tmp_path / "output").read_text(encoding="utf-8").splitlines():
        k, _, v = line.partition("=")
        out[k] = v
    return r, out


def _gate(tmp_path: Path, body: str = "/dokimi run", perm: str = "write", **env: str):
    kw = {"COMMAND": "/dokimi run", "EVENT": "issue_comment", "PR": "7", "IS_PR": "true",
          "ACTOR": "alice", "COMMENT_ID": "99", "BODY": body, "FAKE_PERM": perm, **env}
    return _run(tmp_path, _script(GATE), **kw)


@pytest.mark.parametrize("perm", ["write", "admin"])
def test_a_writer_runs_on_the_current_head(tmp_path: Path, perm: str) -> None:
    r, out = _gate(tmp_path, body="  /dokimi run  \r\nthanks", perm=perm)
    assert r.returncode == 0, r.stderr
    assert out == {"run": "true", "pr": "7", "head-sha": SHA}
    log = (tmp_path / "gh.log").read_text(encoding="utf-8")
    assert "repos/o/r/collaborators/alice/permission" in log
    assert "reactions" in log and "issues/7/comments" not in log


@pytest.mark.parametrize("perm", ["read", "none", "triage", "fail"])
def test_anyone_else_gets_one_reply_and_no_run(tmp_path: Path, perm: str) -> None:
    r, out = _gate(tmp_path, perm=perm)
    assert r.returncode == 0, r.stderr
    assert out == {"run": "false"}
    log = (tmp_path / "gh.log").read_text(encoding="utf-8")
    assert log.count("issues/7/comments") == 1
    assert "pulls/7" not in log
    assert "needs write or admin" in (tmp_path / "body.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("body", ["/dokimi runs", "please /dokimi run", "hi\n/dokimi run", ""])
def test_other_comments_do_nothing(tmp_path: Path, body: str) -> None:
    r, out = _gate(tmp_path, body=body)
    assert r.returncode == 0, r.stderr
    assert out == {"run": "false"}
    assert (tmp_path / "gh.log").read_text(encoding="utf-8") == ""


def test_an_issue_comment_does_nothing(tmp_path: Path) -> None:
    r, out = _gate(tmp_path, IS_PR="false")
    assert (r.returncode, out) == (0, {"run": "false"})


def test_the_comment_body_is_never_evaluated(tmp_path: Path) -> None:
    r, out = _gate(tmp_path, body='/dokimi run$(touch pwned)`touch pwned2`')
    assert out == {"run": "false"}
    assert not (tmp_path / "pwned").exists() and not (tmp_path / "pwned2").exists()


def _comment(tmp_path: Path, files: dict[str, str], **env: str):
    d = tmp_path / "in"
    d.mkdir()
    for name, text in files.items():
        (d / name).write_text(text, encoding="utf-8")
    kw = {"IN": str(d), "PR": "7", "SHA": SHA, "TOP_N": "5", "RUN_URL": "https://x/run", **env}
    return _run(tmp_path, _script(COMMENT), **kw)


def test_the_table_is_posted_once_with_mentions_and_markers_broken(tmp_path: Path) -> None:
    r, _ = _comment(tmp_path, {"llms.txt": "# x\n", "a.json": "{}", "notes.md": "skip"})
    assert r.returncode == 0, r.stderr
    call = (tmp_path / "dokimi.log").read_text(encoding="utf-8")
    assert "report opportunities --format markdown --top-n 5" in call
    assert "a.json" in call and "llms.txt" in call and "notes.md" not in call
    body = (tmp_path / "body.md").read_text(encoding="utf-8")
    assert body.startswith("<!-- dokimi-opportunities -->\n")
    assert body.count("<!--") == 1 and "@someone" not in body and SHA in body
    assert "issues/7/comments" in (tmp_path / "gh.log").read_text(encoding="utf-8")


def test_an_existing_comment_is_updated(tmp_path: Path) -> None:
    r, _ = _comment(tmp_path, {"llms.txt": "# x\n"}, FAKE_IDS="123")
    assert r.returncode == 0, r.stderr
    log = (tmp_path / "gh.log").read_text(encoding="utf-8")
    assert "-X PATCH repos/o/r/issues/comments/123" in log and "-X POST" not in log


def test_no_findings_says_so_without_running_dokimi(tmp_path: Path) -> None:
    r, _ = _comment(tmp_path, {})
    assert r.returncode == 0, r.stderr
    assert (tmp_path / "dokimi.log").read_text(encoding="utf-8") == ""
    assert "nothing to rank" in (tmp_path / "body.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("env", [{"PR": "7;x"}, {"TOP_N": "0"}, {"SHA": "main"}])
def test_malformed_inputs_are_refused(tmp_path: Path, env: dict[str, str]) -> None:
    r, _ = _comment(tmp_path, {"llms.txt": "# x\n"}, **env)
    assert r.returncode == 1
    assert (tmp_path / "gh.log").read_text(encoding="utf-8") == ""


def _expressions(text: str) -> list[str]:
    return re.findall(r"\$\{\{(.*?)\}\}", text)


@pytest.mark.parametrize("path", [GATE, COMMENT])
def test_run_steps_interpolate_no_expressions(path: Path) -> None:
    for step in yaml.safe_load(path.read_text(encoding="utf-8"))["runs"]["steps"]:
        assert not _expressions(step.get("run", "")), step.get("name")


def test_example_workflow_permissions_and_pins() -> None:
    wf = yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))
    assert wf["permissions"] == {}
    jobs = wf["jobs"]
    assert jobs["gate"]["permissions"] == {"pull-requests": "write"}
    assert jobs["checks"]["permissions"] == {"contents": "read"}
    assert jobs["comment"]["permissions"] == {"pull-requests": "write"}
    checkout = jobs["checks"]["steps"][0]
    assert checkout["with"] == {"ref": "${{ needs.gate.outputs.head-sha }}", "persist-credentials": False}
    # The jobs holding a write token never check out the pull request.
    for name in ("gate", "comment"):
        assert not any(str(s.get("uses", "")).startswith("actions/checkout@") for s in jobs[name]["steps"])
    for job in jobs.values():
        for step in job["steps"]:
            assert not _expressions(step.get("run", ""))
            uses = step.get("uses", "")
            if uses.startswith("actions/"):
                assert re.fullmatch(r"actions/[\w-]+@[0-9a-f]{40}", uses), uses


@pytest.mark.parametrize("pin", [SHA[:7], SHA.upper(), SHA])
def test_a_named_head_commit_runs(tmp_path: Path, pin: str) -> None:
    r, out = _gate(tmp_path, body=f"/dokimi run {pin}")
    assert r.returncode == 0, r.stderr
    assert out == {"run": "true", "pr": "7", "head-sha": SHA}


def test_a_commit_that_is_no_longer_the_head_is_refused(tmp_path: Path) -> None:
    # A push after the comment moves the head: the named commit no longer matches, nothing runs.
    r, out = _gate(tmp_path, body="/dokimi run bbbbbbb")
    assert (r.returncode, out) == (0, {"run": "false"})
    assert "is not the head" in (tmp_path / "body.md").read_text(encoding="utf-8")


def test_require_sha_refuses_the_bare_command(tmp_path: Path) -> None:
    r, out = _gate(tmp_path, REQUIRE_SHA="true")
    assert (r.returncode, out) == (0, {"run": "false"})
    assert SHA in (tmp_path / "body.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("body", ["/dokimi run abc", "/dokimi run aaaaaaa extra", "/dokimi run $(id)"])
def test_a_malformed_commit_does_nothing(tmp_path: Path, body: str) -> None:
    r, out = _gate(tmp_path, body=body)
    assert (r.returncode, out) == (0, {"run": "false"})
    assert (tmp_path / "gh.log").read_text(encoding="utf-8") == ""
