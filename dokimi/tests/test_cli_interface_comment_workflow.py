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
  pulls: { get: async (a) => { calls.push(['pulls.get', a]); return { data: c.pr }; } },
  issues: { listComments: 'comments', createLabel: rec('createLabel'), addLabels: rec('addLabels'),
    removeLabel: c.fail_labels ? async () => { throw new Error('404'); } : rec('removeLabel'), createComment: rec('createComment'),
    updateComment: rec('updateComment') },
};
const github = { rest, paginate: async (fn, a) => fn === 'list' ? c.artifacts : c.comments };
const context = { repo: { owner: 'intel', repo: 'dokimi' },
  payload: { workflow_run: { id: 7, head_sha: c.run_sha } } };
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
):
    node = shutil.which("node")
    if not (node and shutil.which("unzip")):
        pytest.skip("needs node and unzip")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
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
        _good(diff="x" * 60001),
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
    assert len(body.split("\n")[1]) <= len("````````diff")


def test_diff_is_fenced_so_links_and_references_do_not_render(tmp_path):
    diff = "[x](http://evil) ![i](http://t/p.png) intel/dokimi#1 ``` break out"
    body = next(a for n, a in _run(tmp_path, _good(diff=diff)) if n == "createComment")["body"]
    lines = body.split("\n")
    assert lines[1].startswith("````diff") and lines[-2].startswith("````")
    assert not lines[-2].startswith("`````")


def test_concurrency_is_per_pr_branch_not_per_commit():
    group = _wf()["concurrency"]["group"]
    assert "head_branch" in group and "head_repository.full_name" in group
    assert "head_sha" not in group
