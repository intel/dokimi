# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""agent-quality's `changes` step decides whether a push runs the smoke test.

A docs-only push may skip the code steps only when everything since the last commit
whose smoke run passed its code steps is documentation. A cancelled or failed run does
not count as passed, so the code it carried is diffed again. The step is run here as
the workflow runs it (its script, read from the YAML), with `gh` replaced by a stub
that serves run and job fixtures.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
import yaml

WORKFLOW = Path(__file__).resolve().parents[2] / ".github/workflows/agent-quality.yml"
SMOKE_JOB = "external-catalog-smoke"
SMOKE_NAME = "External catalog smoke test (uxlfoundation/skills)"
BULK_FILES = 2000

# The stub `gh api`: serves runs.json and jobs-<run id>.json from $FAKE_GH_DIR, applies --jq
# with jq -r as gh does, and fails for a missing fixture (the API being unreadable).
GH_STUB = r"""#!/bin/sh
[ "$1" = api ] || { echo "unexpected: $*" >&2; exit 2; }
path=$2
# The runs request's arguments, one per line, for the test to read back.
case "$path" in repos/*/actions/workflows/agent-quality.yml/runs*) printf '%s\n' "$@" > "$FAKE_GH_DIR/runs-request" ;; esac
shift 2
filter=""
while [ $# -gt 0 ]; do
  case "$1" in --jq) filter=$2; shift 2 ;; *) shift ;; esac
done
case "$path" in
  repos/*/actions/workflows/agent-quality.yml/runs*) file="$FAKE_GH_DIR/runs.json" ;;
  repos/*/actions/runs/*/jobs)
    id=${path#*/actions/runs/}; id=${id%/jobs}; file="$FAKE_GH_DIR/jobs-$id.json" ;;
  *) echo "no fixture for $path" >&2; exit 1 ;;
esac
[ -f "$file" ] || { echo "no fixture file $file" >&2; exit 1; }
if [ -n "$filter" ]; then jq -r "$filter" "$file"; else cat "$file"; fi
"""


def _step() -> dict:
    wf = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    steps = wf["jobs"][SMOKE_JOB]["steps"]
    return next(s for s in steps if s.get("id") == "changes")


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _history(repo: Path, names: list[tuple[str, str]]) -> dict[str, str]:
    """A linear history on main: `base` (code and docs), then each (name, "code" | "docs")."""
    _git(repo, "init", "-q", "-b", "main")
    shas: dict[str, str] = {}
    (repo / "src").mkdir()
    (repo / "src/app.py").write_text("base\n", encoding="utf-8")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    shas["base"] = _git(repo, "rev-parse", "HEAD")
    for name, kind in names:
        if kind == "bulk":
            # Enough changed paths to overflow a pipe buffer: the step must still read a
            # code change as code (a `printf | grep -q` pipeline fails under pipefail here).
            (repo / "src/generated").mkdir()
            for i in range(BULK_FILES):
                (repo / f"src/generated/module_{i:05d}_padding_to_make_the_path_long.py").write_text(
                    "x\n", encoding="utf-8"
                )
        elif kind == "docs_non_ascii":
            # git quotes a non-ASCII path in --name-only output unless core.quotepath is off,
            # and a quoted name does not match the DOCS pattern.
            (repo / "dokimi/docs").mkdir(parents=True, exist_ok=True)
            (repo / "dokimi/docs/é.md").write_text(f"{name}\n", encoding="utf-8")
        else:
            path = "src/app.py" if kind == "code" else "README.md"
            (repo / path).write_text(f"{name}\n", encoding="utf-8")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", name)
        shas[name] = _git(repo, "rev-parse", "HEAD")
    return shas


def _job(verify: str) -> dict:
    return {
        "jobs": [
            {
                "name": SMOKE_NAME,
                "steps": [{"name": "Verify pass rate", "conclusion": verify}],
            },
            {"name": "benchmark", "steps": []},
        ]
    }


def _run_changes(
    tmp_path: Path,
    history,
    runs,
    before: str,
    after: str,
    head_ref: str = "feature",
    action: str = "synchronize",
) -> str:
    """Run the changes step's script for a push (or an opened PR); return the code= output."""
    repo = tmp_path / "repo"
    repo.mkdir()
    shas = _history(repo, history)
    # The base branch's tip, as the checkout fetches it: the diff base of an opened PR.
    _git(repo, "update-ref", "refs/remotes/origin/main", shas["base"])
    fake = tmp_path / "fake"
    fake.mkdir()
    gh = fake / "gh"
    gh.write_text(GH_STUB, encoding="utf-8")
    gh.chmod(0o755)
    if runs is not None:
        listing = []
        for run_id, (name, verify) in enumerate(runs, start=1):
            listing.append({"id": run_id, "head_sha": shas[name]})
            (fake / f"jobs-{run_id}.json").write_text(json.dumps(_job(verify)), encoding="utf-8")
        (fake / "runs.json").write_text(json.dumps({"workflow_runs": listing}), encoding="utf-8")
    out = tmp_path / "github_output"
    step = _step()
    env = {
        **os.environ,
        **{k: str(v) for k, v in step["env"].items()},
        "EVENT": "pull_request",
        "ACTION": action,
        "BASE_REF": "main",
        "HEAD_REF": head_ref,
        "REPO": "intel/dokimi",
        "GH_TOKEN": "test-token",
        "BEFORE": shas[before],
        "AFTER": shas[after],
        "GITHUB_OUTPUT": str(out),
        "FAKE_GH_DIR": str(fake),
        "PATH": f"{fake}{os.pathsep}{os.environ['PATH']}",
    }
    proc = subprocess.run(
        ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", step["run"]],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    lines = [line for line in out.read_text(encoding="utf-8").splitlines() if line]
    assert len(lines) == 1, lines
    return lines[0]


# (history after base, runs newest first as (commit, Verify pass rate conclusion) or None
# for an unreadable API, before, after, the code output expected)
SCENARIOS = {
    # Push B (docs) cancels push A's run (code), so A's smoke run never passed: A's code
    # must still run. The only passed run is V's, so the diff starts there.
    "docs_push_after_cancelled_code_run": (
        [("V", "code"), ("A", "code"), ("B", "docs")],
        [("A", "cancelled"), ("V", "success")],
        "A",
        "B",
        "true",
    ),
    "docs_push_after_failed_code_run": (
        [("V", "code"), ("A", "code"), ("B", "docs")],
        [("A", "failure"), ("V", "success")],
        "A",
        "B",
        "true",
    ),
    "no_passed_run_on_the_pr": (
        [("V", "code"), ("B", "docs")],
        [("V", "cancelled")],
        "V",
        "B",
        "true",
    ),
    "api_unreadable": (
        [("V", "code"), ("B", "docs")],
        None,
        "V",
        "B",
        "true",
    ),
    # Everything since the last passed run (V) is documentation: skip.
    "docs_push_after_passed_code_run": (
        [("V", "code"), ("B", "docs")],
        [("V", "success")],
        "V",
        "B",
        "false",
    ),
    # A docs-only run in between skipped its code steps (Verify pass rate skipped), so it
    # does not count, and the docs it carries are still docs: skip.
    "docs_push_after_skipped_docs_run": (
        [("V", "code"), ("D", "docs"), ("B", "docs")],
        [("D", "skipped"), ("V", "success")],
        "D",
        "B",
        "false",
    ),
    "code_push_after_passed_code_run": (
        [("V", "code"), ("A", "code")],
        [("V", "success")],
        "V",
        "A",
        "true",
    ),
    # Many changed paths, the first one code: still code, not a docs-only push.
    "code_push_with_many_paths_after_passed_run": (
        [("V", "code"), ("A", "bulk")],
        [("V", "success")],
        "V",
        "A",
        "true",
    ),
    "docs_push_with_non_ascii_doc_name_after_passed_run": (
        [("V", "code"), ("B", "docs_non_ascii")],
        [("V", "success")],
        "V",
        "B",
        "false",
    ),
}


@pytest.mark.skipif(shutil.which("jq") is None, reason="needs jq for the gh stub's --jq")
@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_docs_only_push_skips_only_when_all_since_the_last_passed_run_is_docs(tmp_path, name):
    history, runs, before, after, expect = SCENARIOS[name]
    assert _run_changes(tmp_path, history, runs, before, after) == f"code={expect}"


def _runs_request(fake: Path) -> tuple[str, dict[str, list[str]]]:
    """The method and the query of the runs request: the URL's own query, plus the -f fields."""
    args = (fake / "runs-request").read_text(encoding="utf-8").splitlines()
    method = args[args.index("--method") + 1] if "--method" in args else "GET"
    query = parse_qs(urlsplit(args[1]).query, keep_blank_values=True)
    for i, flag in enumerate(args[:-1]):
        if flag in ("-f", "-F", "--raw-field", "--field"):
            key, _, value = args[i + 1].partition("=")
            query.setdefault(key, []).append(value)
    return method, query


@pytest.mark.skipif(shutil.which("jq") is None, reason="needs jq for the gh stub's --jq")
def test_a_branch_name_with_query_characters_reaches_the_runs_query_whole(tmp_path):
    # & # and + are legal in a branch name. In the URL they would end the branch value; as
    # GET fields gh encodes them. gh sends fields as POST unless --method GET is given.
    branch = "a&per_page=1#b+c"
    code = _run_changes(
        tmp_path, [("V", "code"), ("B", "docs")], [("V", "success")], "V", "B", head_ref=branch
    )
    assert code == "code=false"
    method, query = _runs_request(tmp_path / "fake")
    assert method == "GET"
    assert query["branch"] == [branch]
    assert query["event"] == ["pull_request"]


def test_an_opened_pr_with_only_a_non_ascii_doc_name_is_docs_only(tmp_path):
    # The PR diff (origin/main...HEAD) takes the same quoting path as the push diff.
    history = [("B", "docs_non_ascii")]
    assert _run_changes(tmp_path, history, None, "base", "B", action="opened") == "code=false"


def test_the_smoke_job_may_read_the_runs_it_is_scoped_by():
    job = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"][SMOKE_JOB]
    assert job["permissions"]["actions"] == "read"
