# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""actions/pr-command and actions/opportunities-comment, and the example workflow that wires them.

The gate runs only for the exact command from a writer or an admin; anyone else gets one reply.
Each script is run the way the runner does (bash -e -o pipefail) with a fake `gh` that logs its
calls and answers from env vars, so no network is used.
"""

from __future__ import annotations

import json
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
  "api user "*) [ -n "${FAKE_USER:-}" ] || exit 1; echo "$FAKE_USER"; exit 0 ;;
  *"--paginate"*)
    [ "${FAKE_LOOKUP:-}" = fail ] && exit 1
    if [ -n "${FAKE_COMMENTS:-}" ]; then
      while [ "$1" != --jq ]; do shift; done
      printf '%s' "$FAKE_COMMENTS" | jq -r "$2"; exit
    fi
    [ -n "$FAKE_IDS" ] && echo "$FAKE_IDS"; exit 0 ;;
  *reactions*) [ "${FAKE_REACT:-}" = fail ] && exit 1 ;;
  *"-F body=@"*) all="$*"; f="${all##*body=@}"; f="${f%% *}"; cp -- "$f" "$GH_BODY" ;;
esac
exit 0
"""

FAKE_DOKIMI = r"""#!/usr/bin/env bash
printf '%s\n' "$*" >> "$DOKIMI_LOG"
[ -n "${FAKE_RC:-}" ] && exit "$FAKE_RC"
out=""; while [ $# -gt 0 ]; do [ "$1" = --out ] && out="$2"; shift; done
if [ -n "${FAKE_TABLE:-}" ]; then
  cp -- "$FAKE_TABLE" "$out"
  exit 0
fi
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
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"comment": {"body": body}}), encoding="utf-8")
    kw = {"COMMAND": "/dokimi run", "EVENT": "issue_comment", "PR": "7", "IS_PR": "true",
          "ACTOR": "alice", "COMMENT_ID": "99", "GITHUB_EVENT_PATH": str(event), "FAKE_PERM": perm, **env}
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


@pytest.mark.parametrize("login", ["alice_acme", "octocat-1_emu", "dependabot[bot]"])
def test_a_writer_with_a_managed_user_login_runs(tmp_path: Path, login: str) -> None:
    # Enterprise managed user logins are `<handle>_<shortcode>`.
    r, out = _gate(tmp_path, ACTOR=login)
    assert r.returncode == 0, r.stdout
    assert out == {"run": "true", "pr": "7", "head-sha": SHA}
    assert f"repos/o/r/collaborators/{login}/permission" in (tmp_path / "gh.log").read_text(encoding="utf-8")


@pytest.mark.parametrize("login", ["a/b", "a_b/../x", "_lead", "a.b", "a?x", "a b"])
def test_a_login_that_could_change_the_api_path_is_refused(tmp_path: Path, login: str) -> None:
    r, out = _gate(tmp_path, ACTOR=login)
    assert (r.returncode, out) == (1, {"run": "false"})
    assert (tmp_path / "gh.log").read_text(encoding="utf-8") == ""


@pytest.mark.parametrize("head", ["", "main", "null"])
def test_an_unreadable_head_commit_stops_the_run(tmp_path: Path, head: str) -> None:
    # An empty head-sha would check out the default branch and publish it as the pull request's result.
    r, out = _gate(tmp_path, FAKE_SHA=head)
    assert (r.returncode, out) == (1, {"run": "false"})
    assert "reactions" not in (tmp_path / "gh.log").read_text(encoding="utf-8")


def test_a_failed_reaction_does_not_stop_the_run(tmp_path: Path) -> None:
    r, out = _gate(tmp_path, FAKE_REACT="fail")
    assert r.returncode == 0, r.stdout
    assert out == {"run": "true", "pr": "7", "head-sha": SHA}


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


MARKER = "<!-- dokimi-opportunities -->"


def _comments(*rows: tuple[int, str, str]) -> str:
    return json.dumps([{"id": i, "user": {"login": login}, "body": body} for i, login, body in rows])


def _writes(tmp_path: Path) -> list[str]:
    log = (tmp_path / "gh.log").read_text(encoding="utf-8").splitlines()
    return [line for line in log if line.startswith(("api -X PATCH", "api -X POST"))]


def test_only_the_bots_own_marked_comment_is_updated(tmp_path: Path) -> None:
    # The gate's refusal reply comes from the same bot, and anyone can post a comment with the marker.
    comments = _comments((1, "github-actions[bot]", "`/dokimi run` needs write or admin permission"),
                         (2, "mallory", MARKER + "\nall clear"), (3, "github-actions[bot]", MARKER + "\nold"))
    r, _ = _comment(tmp_path, {"llms.txt": "# x\n"}, FAKE_COMMENTS=comments)
    assert r.returncode == 0, r.stderr
    assert [w.split()[2:4] for w in _writes(tmp_path)] == [["PATCH", "repos/o/r/issues/comments/3"]]


def test_a_user_token_edits_only_the_comment_of_its_own_login(tmp_path: Path) -> None:
    comments = _comments((4, "github-actions[bot]", MARKER), (6, "carol", MARKER))
    r, _ = _comment(tmp_path, {"llms.txt": "# x\n"}, GH_TOKEN="u", FAKE_USER="carol", FAKE_COMMENTS=comments)
    assert r.returncode == 0, r.stderr
    assert [w.split()[2:4] for w in _writes(tmp_path)] == [["PATCH", "repos/o/r/issues/comments/6"]]


def test_a_user_token_without_a_readable_login_writes_nothing(tmp_path: Path) -> None:
    comments = _comments((4, "github-actions[bot]", MARKER))
    r, _ = _comment(tmp_path, {"llms.txt": "# x\n"}, GH_TOKEN="u", FAKE_COMMENTS=comments)
    assert r.returncode == 1 and _writes(tmp_path) == []


def test_a_failed_lookup_posts_no_duplicate_and_keeps_the_table_in_the_summary(tmp_path: Path) -> None:
    r, _ = _comment(tmp_path, {"llms.txt": "# x\n"}, FAKE_LOOKUP="fail")
    assert r.returncode == 0, r.stderr
    assert _writes(tmp_path) == []
    assert "the comment lookup failed" in r.stdout
    assert "| 1 |" in (tmp_path / "summary").read_text(encoding="utf-8")


def test_a_failed_report_is_posted_as_failed_and_fails_the_step(tmp_path: Path) -> None:
    r, _ = _comment(tmp_path, {"a.json": "{"}, FAKE_RC="2")
    assert r.returncode == 2
    assert [w.split()[3] for w in _writes(tmp_path)] == ["repos/o/r/issues/7/comments"]
    assert "`dokimi report opportunities` failed (exit 2); see the [run](https://x/run)" in (
        tmp_path / "body.md").read_text(encoding="utf-8")


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


def test_overlapping_publications_share_one_pr_lock_and_skip_a_stale_head(tmp_path: Path) -> None:
    jobs = yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))["jobs"]
    lock = jobs["comment"]["concurrency"]
    assert "needs.gate.outputs.pr" in lock["group"]
    assert lock["cancel-in-progress"] is False
    r, _ = _comment(tmp_path, {}, FAKE_SHA="b" * 40, FAKE_IDS="123")
    assert r.returncode == 0, r.stderr
    calls = (tmp_path / "gh.log").read_text(encoding="utf-8")
    assert "pulls/7" in calls
    assert "-X POST" not in calls and "-X PATCH" not in calls


def test_example_workflow_permissions_and_pins() -> None:
    wf = yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))
    assert wf["permissions"] == {}
    jobs = wf["jobs"]
    assert jobs["gate"]["permissions"] == {"pull-requests": "write"}
    assert jobs["checks"]["permissions"] == {"contents": "read"}
    assert jobs["comment"]["permissions"] == {"pull-requests": "write"}
    assert jobs["comment"]["if"] == "needs.gate.outputs.run == 'true' && needs.checks.result == 'success'"
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


@pytest.mark.parametrize("pin", [SHA.upper(), SHA])
def test_a_named_head_commit_runs(tmp_path: Path, pin: str) -> None:
    r, out = _gate(tmp_path, body=f"/dokimi run {pin}")
    assert r.returncode == 0, r.stderr
    assert out == {"run": "true", "pr": "7", "head-sha": SHA}


def test_a_commit_that_is_no_longer_the_head_is_refused(tmp_path: Path) -> None:
    # A push after the comment moves the head: the named commit no longer matches, nothing runs.
    r, out = _gate(tmp_path, body="/dokimi run " + "b" * 40)
    assert (r.returncode, out) == (0, {"run": "false"})
    assert "is not the head" in (tmp_path / "body.md").read_text(encoding="utf-8")


def test_require_sha_refuses_the_bare_command(tmp_path: Path) -> None:
    r, out = _gate(tmp_path, REQUIRE_SHA="true")
    assert (r.returncode, out) == (0, {"run": "false"})
    assert SHA in (tmp_path / "body.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("body", ["/dokimi run abc", "/dokimi run aaaaaaa", f"/dokimi run {SHA} extra", "/dokimi run $(id)"])
def test_a_malformed_commit_does_nothing(tmp_path: Path, body: str) -> None:
    r, out = _gate(tmp_path, body=body)
    assert (r.returncode, out) == (0, {"run": "false"})
    assert (tmp_path / "gh.log").read_text(encoding="utf-8") == ""


def test_long_comment_still_recognizes_first_line(tmp_path: Path) -> None:
    step = yaml.safe_load(GATE.read_text(encoding="utf-8"))["runs"]["steps"][0]
    assert all("github.event.comment.body" not in value for value in step["env"].values())
    result, outputs = _gate(tmp_path, "  /dokimi run\n" + "✨" * 50000)
    assert result.returncode == 0, result.stderr
    assert outputs["run"] == "true"


def test_escaping_precedes_utf8_comment_limit(tmp_path: Path) -> None:
    table = tmp_path / "table.md"
    table.write_text("@alice <!-- title --!> ✨" * 5000, encoding="utf-8")
    result, _ = _comment(tmp_path, {"llms.txt": "# x\n"}, FAKE_TABLE=str(table), LC_ALL="C")
    assert result.returncode == 0, result.stderr
    body = (tmp_path / "body.md").read_bytes()
    assert len(body) <= 60000
    assert body.decode("utf-8").count("<!--") == 1
    assert "@alice" not in body.decode("utf-8")


def test_entities_and_html_from_the_findings_cannot_mention_or_render(tmp_path: Path) -> None:
    # GitHub renders `&#64;bob` as a working @bob mention; a claim file name can carry it.
    table = tmp_path / "table.md"
    table.write_text("| 1 | docs/&#64;bob &commat;bob &#x40;org/team <img src=x> <!-- m --> |\n", encoding="utf-8")
    result, _ = _comment(tmp_path, {"llms.txt": "# x\n"}, FAKE_TABLE=str(table))
    assert result.returncode == 0, result.stderr
    body = (tmp_path / "body.md").read_text(encoding="utf-8")
    for raw in ("&#64;", "&commat;", "&#x40;", "<img", "<!-- m"):
        assert raw not in body, raw
    assert body.count("<") == 1 and "&amp;#64;bob" in body
