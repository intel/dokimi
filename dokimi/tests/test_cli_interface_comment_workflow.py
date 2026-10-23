# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""cli-interface-comment.yml: a write-token workflow_run job that must stay safe.

Static checks pin the trust boundary; behavioural checks run the embedded script under
node with a fake GitHub client, feeding it good and hostile artifacts.
"""

from __future__ import annotations

import io
import json
import re
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest
import yaml

WORKFLOW = Path(__file__).resolve().parents[2] / ".github/workflows/cli-interface-comment.yml"
SHA = "a" * 40
MARKER = "<!-- cli-interface-diff -->"


def _wf() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _trigger(wf: dict) -> dict:
    return wf.get("on") or wf[True]  # PyYAML reads the bare key `on` as True


def test_runs_only_after_cli_interface_and_never_checks_out_code():
    wf = _wf()
    assert set(_trigger(wf)) == {"workflow_run"}
    assert _trigger(wf)["workflow_run"]["workflows"] == ["cli-interface"]
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "actions/checkout" not in text
    assert "pull_request_target" not in text
    assert "github.event.workflow_run.event == 'pull_request'" in wf["jobs"]["post"]["if"]


def test_permissions_are_minimal():
    wf = _wf()
    assert wf["permissions"] == {}
    assert wf["jobs"]["post"]["permissions"] == {
        "actions": "read",
        "pull-requests": "write",
        "issues": "write",
    }


def test_actions_are_sha_pinned_and_no_run_steps_interpolate_expressions():
    for step in _wf()["jobs"]["post"]["steps"]:
        assert re.fullmatch(r"[\w./-]+@[0-9a-f]{40}", step["uses"]), step
        assert "run" not in step


# ---- behaviour, under node ------------------------------------------------------------

_HARNESS = r"""
const fs = require('fs');
const [scriptFile, caseFile] = process.argv.slice(2);
const c = JSON.parse(fs.readFileSync(caseFile, 'utf8'));
const calls = [];
const rec = (name, ret) => async (a) => { calls.push([name, a]); return ret; };
const rest = {
  actions: { listWorkflowRunArtifacts: 'list', downloadArtifact: async (a) => {
    calls.push(['download', a]); return { data: fs.readFileSync(c.zip) }; } },
  pulls: { get: async (a) => { calls.push(['pulls.get', a]); return { data: c.pr }; }, list: 'pulls' },
  repos: { listPullRequestsAssociatedWithCommit: 'by-commit' },  // GitHub: empty for a fork's commit
  issues: { listComments: 'comments',
    createLabel: c.fail_create_label ? async () => { throw new Error('422'); } : rec('createLabel'), addLabels: rec('addLabels'),
    removeLabel: c.fail_labels ? async () => { throw new Error('404'); } : rec('removeLabel'), createComment: rec('createComment'),
    updateComment: rec('updateComment') },
};
const github = { rest, paginate: async (fn, a) =>
  fn === 'list' ? c.artifacts : fn === 'by-commit' ? [] :
  fn === 'pulls' ? (calls.push(['pulls.list', a]), c.assoc) : c.comments };
const context = { repo: { owner: 'intel', repo: 'dokimi' },
  payload: { workflow_run: { id: 7, head_sha: c.run_sha, html_url: 'https://x/run/7',
    head_branch: 'topic', head_repository: { owner: { login: 'forker' } } } } };
const core = { warning: (m) => calls.push(['warning', m]), info: () => {} };
const script = JSON.parse(fs.readFileSync(scriptFile, 'utf8'));
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
new AsyncFunction('github', 'context', 'core', 'require', script)(github, context, core, require)
  .then(() => console.log(JSON.stringify(calls)),
        (e) => { console.log(JSON.stringify([['throw', String(e)]])); });
"""


def _run(
    tmp_path: Path,
    result,
    *,
    size: int | None = None,
    pr_sha: str = SHA,
    run_sha: str = SHA,
    comments=(),
    pr_repo: str = "intel/dokimi",
    artifact_run: int = 7,
    fail_labels: bool = False,
    fail_create_label: bool = False,
    assoc=None,
):
    node = shutil.which("node")
    if not (node and shutil.which("unzip")):
        pytest.skip("needs node and unzip")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("result.json", result if isinstance(result, str) else json.dumps(result))
    zpath = tmp_path / "a.zip"
    zpath.write_bytes(buf.getvalue())
    script = next(s["with"]["script"] for s in _wf()["jobs"]["post"]["steps"])
    (tmp_path / "script.json").write_text(json.dumps(script), encoding="utf-8")
    (tmp_path / "harness.js").write_text(_HARNESS, encoding="utf-8")
    case = {
        "zip": str(zpath),
        "run_sha": run_sha,
        "comments": list(comments),
        "fail_labels": fail_labels,
        "fail_create_label": fail_create_label,
        "assoc": [_assoc()] if assoc is None else assoc,
        "pr": {"head": {"sha": pr_sha}, "base": {"repo": {"full_name": pr_repo}}},
        "artifacts": [
            {
                "id": 3,
                "name": "cli-interface-result",
                "expired": False,
                "size_in_bytes": size if size is not None else len(buf.getvalue()),
                "workflow_run": {"id": artifact_run},
            }
        ],
    }
    (tmp_path / "case.json").write_text(json.dumps(case), encoding="utf-8")
    out = subprocess.run(
        [
            node,
            str(tmp_path / "harness.js"),
            str(tmp_path / "script.json"),
            str(tmp_path / "case.json"),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(out.stdout)


def _assoc(**kw):
    return {"number": 12, "state": "open", "head": {"sha": SHA}, "base": {"repo": {"full_name": "intel/dokimi"}}, **kw}


def _fence(body: str) -> tuple[str, str]:
    """The opening and closing fence lines of a comment body."""
    lines = body.split("\n")
    start = next(i for i, ln in enumerate(lines) if ln.startswith("```"))
    end = max(i for i, ln in enumerate(lines) if re.fullmatch(r"`+", ln))
    return lines[start], lines[end]


def _bodies(calls):
    return [a["body"] for n, a in calls if n in ("createComment", "updateComment")]


def _names(calls):
    return [c[0] for c in calls]


def _good(**kw):
    return {
        "pr_number": 12,
        "head_sha": SHA,
        "changed": "1",
        "diff": "## CLI diff\n+ `--x`\n",
        **kw,
    }


def test_changed_result_labels_and_creates_the_sticky_comment(tmp_path):
    calls = _run(tmp_path, _good())
    kinds = _names(calls)
    assert {"addLabels", "createComment"} <= set(kinds)
    body = next(a for n, a in calls if n == "createComment")
    assert body["issue_number"] == 12 and body["body"].startswith(MARKER)
    assert next(a for n, a in calls if n == "addLabels")["labels"] == ["cli-interface-change"]


def test_existing_sticky_comment_is_updated_not_duplicated(tmp_path):
    calls = _run(
        tmp_path,
        _good(),
        comments=[{"id": 99, "user": {"login": "github-actions[bot]"}, "body": MARKER + "\nold"}],
    )
    assert "createComment" not in _names(calls)
    assert next(a for n, a in calls if n == "updateComment")["comment_id"] == 99


@pytest.mark.parametrize("changed", ["1", "0", "error"])
def test_marker_comment_by_another_user_is_never_rewritten(tmp_path, changed):
    comments = [{"id": 5, "user": {"login": "mallory"}, "body": MARKER + " hi"}]
    calls = _run(tmp_path, _good(changed=changed), comments=comments)
    assert "updateComment" not in _names(calls)
    assert ("createComment" in _names(calls)) == (changed != "0")


def test_unchanged_result_removes_the_label(tmp_path):
    calls = _run(tmp_path, _good(changed="0"))
    assert "removeLabel" in _names(calls) and "addLabels" not in _names(calls)
    assert "createComment" not in _names(calls)


def test_unchanged_result_rewrites_an_existing_comment_even_if_removing_the_label_fails(tmp_path):
    comments = [{"id": 99, "user": {"login": "github-actions[bot]"}, "body": MARKER + "\nold"}]
    calls = _run(tmp_path, _good(changed="0"), comments=comments, fail_labels=True)
    assert "throw" not in _names(calls)
    body = next(a for n, a in calls if n == "updateComment")["body"]
    assert body.startswith(MARKER) and "matches the base branch" in body


def test_error_result_comments_and_leaves_the_label(tmp_path):
    calls = _run(tmp_path, _good(changed="error"))
    assert "createComment" in _names(calls)
    assert not {"addLabels", "removeLabel"} & set(_names(calls))


def test_mentions_and_forged_markers_in_the_diff_are_neutralised(tmp_path):
    calls = _run(tmp_path, _good(diff=f"{MARKER} hi @octocat"))
    body = next(a for n, a in calls if n == "createComment")["body"]
    assert body.count(MARKER) == 1 and "@octocat" not in body


def test_nested_marker_cannot_reassemble_after_stripping(tmp_path):
    nested = "<!-- cli-" + MARKER + "interface-diff -->"
    calls = _run(tmp_path, _good(diff=nested))
    body = next(a for n, a in calls if n == "createComment")["body"]
    assert body.count(MARKER) == 1


@pytest.mark.parametrize(
    "bad",
    [
        _good(pr_number="12"),
        _good(pr_number=1.5),
        _good(pr_number=-3),
        _good(pr_number=True),
        _good(pr_number=None),
        _good(head_sha="b" * 40),
        _good(changed="yes"),
        _good(changed=1),
        _good(diff=None),
        [1, 2],
        "not json",
        "null",
    ],
)
def test_malformed_artifacts_post_nothing(tmp_path, bad):
    calls = _run(tmp_path, bad)
    assert not {"createComment", "updateComment", "addLabels", "removeLabel", "pulls.get"} & set(
        _names(calls)
    )
    assert "warning" in _names(calls)


def test_stale_result_for_an_older_head_is_skipped(tmp_path):
    calls = _run(tmp_path, _good(), pr_sha="c" * 40)
    assert not {"createComment", "addLabels"} & set(_names(calls))


def test_pr_number_of_another_repository_is_refused(tmp_path):
    calls = _run(tmp_path, _good(), pr_repo="other/repo")
    assert not {"createComment", "addLabels"} & set(_names(calls))


def test_oversized_artifact_is_not_downloaded(tmp_path):
    calls = _run(tmp_path, _good(), size=10**9)
    assert "download" not in _names(calls)


def test_artifact_from_another_run_is_ignored(tmp_path):
    calls = _run(tmp_path, _good(), artifact_run=8)
    assert "download" not in _names(calls)


@pytest.mark.parametrize("char", ["@", "`"])
def test_many_mentions_cannot_push_the_body_past_githubs_limit(tmp_path, char):
    calls = _run(tmp_path, _good(diff=char * 60000))
    body = next(a for n, a in calls if n == "createComment")["body"]
    assert len(body) < 65536 and "truncated" in body
    assert len(_fence(body)[0]) <= len("````````diff")


def test_diff_is_fenced_so_links_and_references_do_not_render(tmp_path):
    diff = "[x](http://evil) ![i](http://t/p.png) intel/dokimi#1 ``` break out"
    body = next(a for n, a in _run(tmp_path, _good(diff=diff)) if n == "createComment")["body"]
    opening, closing = _fence(body)
    assert opening == "````diff" and closing == "````"


def test_concurrency_is_per_pr_branch_not_per_commit():
    group = _wf()["concurrency"]["group"]
    assert "head_branch" in group and "head_repository.full_name" in group
    assert "head_sha" not in group


OLD_COMMENT = [{"id": 99, "user": {"login": "github-actions[bot]"}, "body": MARKER + "\nold diff"}]


def test_a_diff_over_the_cap_is_truncated_and_its_label_and_comment_follow_the_result(tmp_path):
    big = _run(tmp_path, _good(diff="+ x\n" * 20000), comments=OLD_COMMENT)
    assert "addLabels" in _names(big) and "truncated" in _bodies(big)[0] and "old diff" not in _bodies(big)[0]
    gone = _run(tmp_path, _good(changed="0", diff="+ x\n" * 20000), comments=OLD_COMMENT)
    assert "removeLabel" in _names(gone) and "addLabels" not in _names(gone)
    assert "matches the base branch" in _bodies(gone)[0]


def test_a_result_over_the_size_cap_replaces_the_stale_comment_and_leaves_the_label(tmp_path):
    calls = _run(tmp_path, _good(diff="+ x\n" * 100000), comments=OLD_COMMENT)  # result.json over 200 KiB
    assert not {"addLabels", "removeLabel", "createComment"} & set(_names(calls)) and "warning" in _names(calls)
    update = next(a for n, a in calls if n == "updateComment")
    assert update["comment_id"] == 99 and "too large" in update["body"] and update["body"].startswith(MARKER)
    assert "old diff" not in update["body"] and "https://x/run/7" in update["body"]


def test_an_artifact_over_the_size_cap_replaces_the_stale_comment_without_a_download(tmp_path):
    calls = _run(tmp_path, _good(), size=10**9, comments=OLD_COMMENT)
    assert "download" not in _names(calls) and "too large" in _bodies(calls)[0]


@pytest.mark.parametrize("pr", [_assoc(state="closed"), _assoc(head={"sha": "c" * 40}),
                                _assoc(base={"repo": {"full_name": "other/repo"}})])
def test_a_too_large_result_is_reported_only_on_an_open_pr_of_this_repository_at_the_run_head(tmp_path, pr):
    calls = _run(tmp_path, _good(), size=10**9, comments=OLD_COMMENT, assoc=[pr])
    assert not {"createComment", "updateComment"} & set(_names(calls))


def test_a_too_large_result_on_a_fork_pr_is_found_by_its_head_branch(tmp_path):
    calls = _run(tmp_path, _good(), size=10**9, comments=OLD_COMMENT)
    query = next(a for n, a in calls if n == "pulls.list")
    assert query["head"] == "forker:topic" and query["state"] == "open"
    assert "too large" in _bodies(calls)[0]


def test_a_label_that_already_exists_does_not_stop_the_comment(tmp_path):
    calls = _run(tmp_path, _good(), fail_create_label=True)
    assert "throw" not in _names(calls) and {"addLabels", "createComment"} <= set(_names(calls))


def test_a_cut_through_an_emoji_leaves_no_lone_surrogate(tmp_path):
    body = _bodies(_run(tmp_path, _good(diff="a" * 59999 + "\U0001f600" * 5)))[0]
    assert "truncated" in body and not re.search("[\ud800-\udbff](?![\udc00-\udfff])", body)


def test_a_backtick_run_longer_than_seven_is_split_so_the_fence_stays_short(tmp_path):
    body = _bodies(_run(tmp_path, _good(diff="`" * 12)))[0]
    assert _fence(body)[0] == "````````diff" and "`" * 8 not in body.split("diff\n", 1)[1].split("\n")[0]


def test_a_diff_with_no_backticks_gets_a_three_backtick_fence(tmp_path):
    assert _fence(_bodies(_run(tmp_path, _good(diff="+ plain")))[0]) == ("```diff", "```")


def test_the_producers_markdown_is_unwrapped_and_the_heading_is_written_outside_the_fence(tmp_path):
    produced = ("## CLI interface change detected\n\nThis PR changes dokimi's user-facing CLI surface "
                "(commands and/or options):\n\n```diff\n+ `repo run` --x\n```\n")
    body = _bodies(_run(tmp_path, _good(diff=produced)))[0]
    lines = body.split("\n")
    assert lines[:2] == [MARKER, "## CLI interface change detected"] and lines[2] == ""
    assert body.count("```diff") == 1 and body.count("## CLI interface change detected") == 1
    assert "+ `repo run` --x" in body and _fence(body) == ("```diff", "```")


PRODUCED = ("## CLI interface change detected\n\nThis PR changes dokimi's user-facing CLI surface "
            "(commands and/or options):\n\n```diff\n+ `repo run` --x\n```")


@pytest.mark.parametrize("produced", [PRODUCED, PRODUCED + "\n\n", PRODUCED.replace("\n", "\r\n") + "\r\n"],
                         ids=["no-trailing-newline", "blank-line-after-fence", "crlf"])
def test_every_form_of_the_producers_wrapper_is_unwrapped(tmp_path, produced):
    body = _bodies(_run(tmp_path, _good(diff=produced)))[0]
    assert body.count("```diff") == 1 and body.count("## CLI interface change detected") == 1
    assert "+ `repo run` --x" in body and _fence(body) == ("```diff", "```")


@pytest.mark.parametrize(
    "diff",
    [
        "+ before\n" + PRODUCED,  # heading not at the start
        PRODUCED + "\n+ after",  # text after the closing fence
        PRODUCED.replace("options):", "options):\n\nsecond paragraph"),  # not the one-line intro
    ],
    ids=["text-before", "text-after", "two-paragraph-intro"],
)
def test_text_that_only_contains_the_wrapper_is_fenced_whole(tmp_path, diff):
    body = _bodies(_run(tmp_path, _good(diff=diff)))[0]
    assert body.count("## CLI interface change detected") == 2  # the job's own, and the text's in the fence
    assert body.count("```diff") == 2 and _fence(body)[0] == "````diff"


def test_every_mention_and_every_comment_opener_is_broken(tmp_path):
    body = _bodies(_run(tmp_path, _good(diff="@a @b <!-- x <!-- y")))[0]
    assert "@a" not in body and "@b" not in body and body.count("<!--") == 1  # the marker only


def test_a_run_of_exactly_eight_backticks_is_split(tmp_path):
    body = _bodies(_run(tmp_path, _good(diff="`" * 8)))[0]
    assert _fence(body)[0] == "````````diff" and "`" * 8 not in body.split("diff\n", 1)[1].split("\n")[0]


def test_a_too_large_result_skips_a_filtered_pr_and_reports_on_the_next(tmp_path):
    prs = [_assoc(number=11, state="closed"), _assoc(number=12)]
    calls = _run(tmp_path, _good(), size=10**9, assoc=prs)
    assert [a["issue_number"] for n, a in calls if n == "createComment"] == [12]


def test_a_too_large_result_with_no_open_pr_posts_nothing(tmp_path):
    calls = _run(tmp_path, _good(), size=10**9, assoc=[])
    assert "throw" not in _names(calls) and not {"createComment", "updateComment"} & set(_names(calls))


def test_the_error_comment_is_the_jobs_own_text_not_the_artifacts(tmp_path):
    body = _bodies(_run(tmp_path, _good(changed="error", diff="## x\n![i](http://t/p.png)")))[0]
    assert "could not run" in body and "http://t" not in body and "https://x/run/7" in body


def test_the_workflow_never_cancels_a_running_post_and_posts_only_after_a_successful_run():
    wf = _wf()
    assert wf["concurrency"]["cancel-in-progress"] is False
    assert "github.event.workflow_run.conclusion == 'success'" in wf["jobs"]["post"]["if"]
