# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Tests for scripts/ci_local.py: synthetic workflows, stubbed subprocess, no network."""

from __future__ import annotations

import argparse
import importlib.util
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

_spec = importlib.util.spec_from_file_location(
    "ci_local", Path(__file__).resolve().parents[1] / "scripts" / "ci_local.py"
)
assert _spec and _spec.loader
cl = importlib.util.module_from_spec(_spec)
sys.modules["ci_local"] = cl
_spec.loader.exec_module(cl)

PYV = f"{sys.version_info.major}.{sys.version_info.minor}"
WF = """
name: t
on:
  pull_request:
defaults:
  run:
    working-directory: sub
jobs:
  a:
    runs-on: x
    env:
      JOBVAR: jv
    steps:
      - uses: actions/checkout@v1
      - run: pip install ruff 'mypy>=1' -r req.txt
      - name: one
        run: echo ${{ github.event.pull_request.base.ref }} ${{ matrix.python-version }}
      - name: two
        working-directory: other
        env:
          B: ${{ github.event.pull_request.base.ref }}
        run: exit 1
      - name: three
        if: always() && matrix.python-version == '3.11'
        run: echo hi
      - name: unk
        run: echo ${{ secrets.TOKEN }}
  b:
    runs-on: x
    if: github.event_name == 'schedule'
    steps:
      - run: echo never
  package-smoke:
    runs-on: x
    steps:
      - run: echo net
"""


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    wfdir = tmp_path / ".github" / "workflows"
    wfdir.mkdir(parents=True)
    (wfdir / "x.yml").write_text(WF.replace("'3.11'", f"'{PYV}'"), encoding="utf-8")
    (wfdir / "push.yml").write_text("on: push\njobs:\n  z:\n    steps:\n      - run: x\n")
    monkeypatch.setattr(cl, "WORKFLOWS", wfdir)
    monkeypatch.setattr(cl, "REPO", tmp_path)
    calls: list[dict[str, Any]] = []

    def fake_run(cmd: list[str], **kw: Any) -> Any:
        calls.append({"cmd": cmd, **kw})
        rc = 1 if cmd[-1].strip() == "exit 1" else 0
        return subprocess.CompletedProcess(cmd, rc)

    monkeypatch.setattr(cl.subprocess, "run", fake_run)
    monkeypatch.setattr(cl, "network_ok", lambda: True)
    monkeypatch.setattr(cl, "make_isolated_env", lambda t, py: (tmp_path, {"PATH": ""}))
    monkeypatch.setattr(cl.shutil, "which", lambda _: None)
    monkeypatch.setattr(cl, "upstream_remote", lambda override: override or "origin")
    monkeypatch.setattr(cl, "resolve_base", lambda base, remote: (f"{remote}/{base}", None))
    return {"calls": calls, "root": tmp_path}


def ns(**kw: Any) -> argparse.Namespace:
    d: dict[str, Any] = dict(jobs=None, base="main", remote=None, offline=False, with_docker=False)
    d.update(kw)
    return argparse.Namespace(**d)


def test_job_and_workflow_env_reach_the_step(env: dict[str, Any]) -> None:
    """Job-level env: is passed to bash; step env overrides it."""
    cl.run(ns(jobs="a"))
    assert env["calls"][0]["env"]["JOBVAR"] == "jv"
    wf = "on: pull_request\nenv:\n  W: w\n  J: wf\njobs:\n  a:\n    env:\n      J: job\n    steps:\n      - run: echo\n"
    (cl.WORKFLOWS / "x.yml").write_text(wf)
    env["calls"].clear()
    cl.run(ns())
    assert env["calls"][0]["env"]["W"] == "w" and env["calls"][0]["env"]["J"] == "job"


def test_unevaluable_condition_runs_step_with_notice(env: dict[str, Any], capsys: Any) -> None:
    """An `if:` outside the supported grammar does not crash the run."""
    for expr in ("github.ref == 'refs/heads/main'", "contains(github.event.labels.*.name, 'x')"):
        assert cl.Context("main", PYV, Path(".")).condition(expr) is True
    assert "NOTICE: cannot evaluate" in capsys.readouterr().out


def test_failure_condition_is_false_locally() -> None:
    """A local run has no failed job: pip-audit.yml's `report` job (which opens an issue) is skipped."""
    ctx = cl.Context("main", PYV, Path("."))
    assert ctx.condition("${{ failure() && github.event_name == 'schedule' }}") is False
    assert ctx.condition("failure()") is False


def test_tool_specs_keep_each_requirement_file_flagged(env: dict[str, Any]) -> None:
    """Every requirements file keeps its own -r; a repeated file is listed once."""
    wf = ("on: pull_request\njobs:\n  a:\n    steps:\n      - run: pip install -r one.txt\n"
          "      - run: pip install -r two.txt ruff\n      - run: pip install -r one.txt ruff\n")
    (cl.WORKFLOWS / "x.yml").write_text(wf)
    root = env["root"]
    assert cl.tool_specs() == ["-r", str(root / "one.txt"), "-r", str(root / "two.txt"), "ruff"]


def test_tool_specs_keep_constraint_and_editable_pairs(env: dict[str, Any]) -> None:
    """-c and -e keep their own flag in every spelling; URLs after -e stay as written."""
    wf = ("on: pull_request\njobs:\n  a:\n    steps:\n"
          "      - run: pip install -c a.txt --constraint=b.txt -cb.txt -e . --editable ../x\n"
          "      - run: pip install --requirement=r.txt -rr.txt -e git+https://h/p.git ruff\n")
    (cl.WORKFLOWS / "x.yml").write_text(wf)
    root = env["root"]
    assert cl.tool_specs() == [
        "-c", str(root / "a.txt"), "-c", str(root / "b.txt"),
        "-e", str(root / "."), "-e", str(root / "../x"),
        "-r", str(root / "r.txt"), "-e", "git+https://h/p.git", "ruff",
    ]


def test_pip_install_line_dropped_rest_of_step_runs(env: dict[str, Any]) -> None:
    """A step that starts with pip install and continues still runs its other lines."""
    wf = "on: pull_request\njobs:\n  a:\n    steps:\n      - run: |\n          pip install x\n          echo after\n      - run: pip install y\n"
    (cl.WORKFLOWS / "x.yml").write_text(wf)
    cl.run(ns())
    assert [c["cmd"][-1].strip() for c in env["calls"]] == ["echo after"]


def test_unknown_job_id_is_an_error(env: dict[str, Any], capsys: Any) -> None:
    """A stale --jobs id fails before anything runs instead of being skipped."""
    assert cl.run(ns(jobs="a,lint")) == 2
    assert not env["calls"]
    out = capsys.readouterr().out
    assert "unknown job id(s) lint" in out
    assert "make hooks" in out
    cl.run(ns(jobs="a,semgrep"))  # the semgrep pseudo-job is known
    assert env["calls"] and "unknown job id" not in capsys.readouterr().out


def test_the_local_leg_is_the_coverage_leg(env: dict[str, Any]) -> None:
    """Steps gated on matrix.coverage run locally, whatever the venv's Python."""
    wf = ("on: pull_request\njobs:\n  t:\n    steps:\n"
          "      - env:\n          COVERAGE: ${{ matrix.coverage }}\n        run: echo cov\n"
          "      - if: github.event_name == 'pull_request' && matrix.coverage\n        run: echo gate\n")
    (cl.WORKFLOWS / "x.yml").write_text(wf)
    cl.run(ns())
    assert [c["cmd"][-1].strip() for c in env["calls"]] == ["echo cov", "echo gate"]
    assert env["calls"][0]["env"]["COVERAGE"] == "true"


PY_WF = """on: pull_request
jobs:
  lint:
    steps:
      - uses: actions/setup-python@v1
        with:
          python-version: '{single}'
      - run: echo lint
  other:
    steps:
      - uses: actions/setup-python@v1
        with:
          python-version: '{single}'
  test:
    strategy:
      matrix:
        python-version: ['3.96', '{here}', '3.99']
    steps:
      - uses: actions/setup-python@v1
        with:
          python-version: ${{{{ matrix.python-version }}}}
  odd:
    steps:
      - uses: actions/setup-python@v1
        with:
          python-version: '3.98'
"""


def test_ci_pythons_reads_single_version_and_matrix_legs(env: dict[str, Any]) -> None:
    """The most-named literal setup-python version, and every matrix leg once."""
    (cl.WORKFLOWS / "x.yml").write_text(PY_WF.format(single="3.97", here=PYV))
    assert cl.ci_pythons() == ("3.97", ["3.96", PYV, "3.99"])


def test_summary_names_the_uncovered_legs_and_a_python_mismatch(
        env: dict[str, Any], capsys: Any) -> None:
    """Legs not run are the matrix minus this venv's Python; a venv on another
    Python than CI's single-version jobs gets a notice, at the start and the end."""
    (cl.WORKFLOWS / "x.yml").write_text(PY_WF.format(single="3.97", here=PYV))
    cl.run(ns())
    out = capsys.readouterr().out
    others = ", ".join(v for v in ("3.96", PYV, "3.99") if v != PYV)
    assert f"matrix legs on other Pythons ({others})" in out
    assert out.count(f"this venv runs Python {PYV}, CI's single-version jobs 3.97") == 2

    (cl.WORKFLOWS / "x.yml").write_text(PY_WF.format(single=PYV, here=PYV))
    cl.run(ns())
    assert "this venv runs Python" not in capsys.readouterr().out


BASE_WF = """on: pull_request
jobs:
  a:
    steps:
      - run: diff-cover c.xml --compare-branch="origin/$BASE_REF"
      - run: git diff "origin/${BASE_REF}"...HEAD
      - run: echo origin/main
"""


def test_base_steps_use_the_resolved_remote(env: dict[str, Any]) -> None:
    """`origin/$BASE_REF` in a step reads the resolved remote; other text is untouched."""
    (cl.WORKFLOWS / "x.yml").write_text(BASE_WF)
    assert cl.run(ns(remote="intel")) == 0
    assert [c["cmd"][-1] for c in env["calls"]] == [
        'diff-cover c.xml --compare-branch="intel/$BASE_REF"',
        'git diff "intel/${BASE_REF}"...HEAD',
        "echo origin/main",
    ]


def test_dokimi_growth_base_env_is_passed_to_steps(env: dict[str, Any]) -> None:
    """ci_local.py passes the resolved base_ref as DOKIMI_GROWTH_BASE env var."""
    (cl.WORKFLOWS / "x.yml").write_text(BASE_WF)
    assert cl.run(ns(remote="intel", base="dev")) == 0
    # All steps should receive DOKIMI_GROWTH_BASE in their env
    for call in env["calls"]:
        assert call["env"]["DOKIMI_GROWTH_BASE"] == "intel/dev"


def test_dokimi_growth_base_reaches_isolated_jobs(env: dict[str, Any]) -> None:
    """An isolated job (`package-smoke`) runs in its own env; DOKIMI_GROWTH_BASE is still set there."""
    (cl.WORKFLOWS / "x.yml").write_text(BASE_WF.replace("  a:\n", "  package-smoke:\n"))
    assert cl.run(ns(remote="intel", base="dev")) == 0
    assert env["calls"], "the isolated job's steps should run"
    for call in env["calls"]:
        assert call["env"]["DOKIMI_GROWTH_BASE"] == "intel/dev"


def test_unusable_base_fails_its_steps_without_running_them(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    """No base ref: the diffing steps FAIL with the reason; the rest still run."""
    (cl.WORKFLOWS / "x.yml").write_text(BASE_WF)
    monkeypatch.setattr(cl, "resolve_base", lambda base, remote: (f"{remote}/{base}", "no such ref"))
    assert cl.run(ns()) == 1
    assert [c["cmd"][-1] for c in env["calls"]] == ["echo origin/main"]
    out = capsys.readouterr().out
    assert "Base: origin/main" in out and out.count("ERROR: no such ref") == 3


def test_unusable_base_is_not_an_error_when_no_selected_job_diffs(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    """With only jobs that never diff against the base selected, nothing reports it."""
    (cl.WORKFLOWS / "x.yml").write_text(BASE_WF + "  b:\n    steps:\n      - run: echo b\n")
    monkeypatch.setattr(cl, "resolve_base", lambda base, remote: (f"{remote}/{base}", "no such ref"))
    assert cl.run(ns(jobs="b")) == 0
    assert [c["cmd"][-1] for c in env["calls"]] == ["echo b"]
    assert "ERROR" not in capsys.readouterr().out


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """A real git repo with one commit; returns a git runner for it."""
    for k, v in {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
                 "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                 "GIT_COMMITTER_EMAIL": "t@t"}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(cl, "REPO", tmp_path)

    def git(*args: str) -> str:
        return subprocess.run(["git", *args], cwd=tmp_path, check=True,
                              capture_output=True, text=True).stdout.strip()

    git("init", "-q")
    git("commit", "-q", "--allow-empty", "-m", "c")
    return git


def test_upstream_remote_is_the_intel_dokimi_url(repo: Any) -> None:
    """origin pointing elsewhere does not win; an override always does."""
    assert cl.upstream_remote(None) == "origin"  # no remotes at all
    repo("remote", "add", "origin", "https://github.com/someone/other")
    repo("remote", "add", "fork", "https://github.com/me/dokimi")
    repo("remote", "add", "lookalike", "https://notgithub.com/intel/dokimi")
    repo("remote", "add", "renamed", "https://github.com/intel/dokimi-fork")
    assert cl.upstream_remote(None) == "origin"  # nothing matches: fall back
    repo("remote", "add", "intel", "git@github.com:Intel/dokimi.git")
    assert cl.upstream_remote(None) == "intel"
    repo("remote", "set-url", "intel", "ssh://git@github.com/intel/dokimi")
    assert cl.upstream_remote(None) == "intel"
    assert cl.upstream_remote("fork") == "fork"
    repo("remote", "set-url", "origin", "https://github.com/intel/dokimi")
    assert cl.upstream_remote(None) == "origin"  # several match: origin first


def test_upstream_remote_expands_insteadof_aliases(repo: Any) -> None:
    """A remote written as an insteadOf alias matches by the URL git fetches from."""
    repo("config", "url.https://github.com/.insteadOf", "gh:")
    repo("remote", "add", "origin", "https://github.com/me/dokimi")
    repo("remote", "add", "upstream", "gh:intel/dokimi")
    assert cl.upstream_remote(None) == "upstream"


def test_resolve_base_reports_missing_and_unrelated_refs(repo: Any) -> None:
    """A usable base passes; a missing ref or one with no shared history says why."""
    head = repo("rev-parse", "HEAD")
    repo("update-ref", "refs/remotes/intel/main", head)
    assert cl.resolve_base("main", "intel") == ("intel/main", None)
    ref, err = cl.resolve_base("main", "origin")
    assert ref == "origin/main" and err and "does not exist" in err and "git fetch origin" in err
    tree = repo("hash-object", "-t", "tree", "-w", os.devnull)  # the empty tree
    repo("update-ref", "refs/remotes/origin/main", repo("commit-tree", tree, "-m", "other"))
    ref, err = cl.resolve_base("main", "origin")
    assert err and "no merge base" in err and "--remote" in err


def test_needs_network_entries_match_real_jobs_and_steps() -> None:
    """Every tuple in NEEDS_NETWORK names a real job id and step name, catching renames."""
    workflows = cl.load_workflows()
    job_ids = {job_id for _, wf in workflows for job_id in wf["jobs"]}
    step_names_by_job = {}
    for _, wf in workflows:
        for job_id, job in wf["jobs"].items():
            step_names_by_job.setdefault(job_id, set()).update(
                step.get("name", "") for step in job.get("steps", []) if step.get("name")
            )

    for entry in cl.NEEDS_NETWORK:
        if isinstance(entry, str):
            # Plain job id
            assert entry in job_ids, f"NEEDS_NETWORK job id '{entry}' does not exist in workflows"
        else:
            # (job_id, step_name) tuple
            job_id, step_name = entry
            assert job_id in job_ids, f"NEEDS_NETWORK job id '{job_id}' does not exist in workflows"
            steps = step_names_by_job.get(job_id, set())
            assert step_name in steps, (
                f"NEEDS_NETWORK step name '{step_name}' not found in job '{job_id}'. "
                f"Job has steps: {sorted(steps)}"
            )


def test_gitleaks_job_in_needs_network() -> None:
    """Regression: gitleaks job is in NEEDS_NETWORK so ci-local skips it offline."""
    assert "gitleaks" in cl.NEEDS_NETWORK


def test_gitleaks_scan_reports_each_line_once_under_its_own_commit(tmp_path: Path) -> None:
    """The Scan commits step on a PR merge with a conflict, with a stub gitleaks that
    runs the `git log -p -U0 <log-opts>` gitleaks runs: the PR's lines and its conflict
    resolution are scanned, and a line from main only under main's own commit (where
    its .gitleaksignore fingerprint points), on a PR and on a push."""
    version = subprocess.run(["git", "--version"], capture_output=True, text=True, check=True).stdout
    if tuple(int(n) for n in re.findall(r"\d+", version)[:2]) < (2, 36):
        pytest.skip("--diff-merges=remerge needs git 2.36")
    wf = dict(cl.load_workflows())["gitleaks.yml"]
    step = next(s for s in wf["jobs"]["gitleaks"]["steps"] if s.get("name") == "Scan commits")
    repo = tmp_path / "repo"
    repo.mkdir()
    env = {"PATH": os.environ["PATH"], "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
           "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t", "RUNNER_TEMP": str(tmp_path), "OUT": str(tmp_path / "out")}

    def git(*args: str, check: bool = True) -> str:
        return subprocess.run(["git", *args], cwd=repo, env=env, check=check,
                              capture_output=True, text=True).stdout.strip()

    def commit(path: str, text: str) -> str:
        (repo / path).write_bytes(text.encode())
        git("add", path)
        git("commit", "-q", "-m", path)
        return git("rev-parse", "HEAD")

    git("init", "-q", "-b", "main")
    base_sha = commit("c.txt", "base\n")
    git("checkout", "-q", "-b", "feat")
    commit("pr.txt", "PR_OWN\n")
    commit("c.txt", "feat\n")
    # A ref HEAD does not reach: only the push scan's --all covers it.
    git("checkout", "-q", "-b", "other", base_sha)
    commit("only.txt", "ONLY_ON_OTHER\n")
    git("checkout", "-q", "main")
    main_sha = commit("main.txt", "MAIN_ONLY\n")
    commit("c.txt", "main\n")
    git("update-ref", "refs/remotes/origin/main", "main")
    git("checkout", "-q", "--detach", "main")
    git("merge", "-q", "feat", check=False)  # conflicts in c.txt
    commit("c.txt", "RESOLVED\n")
    (tmp_path / "gitleaks").mkdir()
    stub = tmp_path / "gitleaks" / "gitleaks"
    # Exits 0 even when git log fails, as gitleaks does.
    stub.write_bytes(b'#!/bin/sh\nprintf "%s\\n" "$@" > "$OUT.argv"\n'
                     b'for a; do case $a in --log-opts=*) opts=${a#--log-opts=};; esac; done\n'
                     b'git log -p -U0 $opts > "$OUT" 2>/dev/null\nexit 0\n')
    stub.chmod(0o755)

    def run_step(event: str, base: str = "main") -> int:
        """The step's exit code, with its own env: mapping expanded as GitHub would."""
        ctx = cl.Context(base, PYV, tmp_path)
        ctx.values["github.event_name"] = event
        step_env = {k: ctx.expand(str(v)) for k, v in step["env"].items()}
        return subprocess.run(["bash", "-c", str(step["run"])], env={**env, **step_env}, cwd=repo,
                              check=False).returncode

    def scan(event: str) -> list[tuple[str, str]]:
        """(commit, added line) pairs gitleaks would scan."""
        assert run_step(event) == 0
        assert "--redact" in (tmp_path / "out.argv").read_text(encoding="utf-8").splitlines()
        pairs, sha = [], ""
        for line in (tmp_path / "out").read_text(encoding="utf-8").splitlines():
            if line.startswith("commit "):
                sha = line.split()[1]
            elif line.startswith("+") and not line.startswith("+++"):
                pairs.append((sha, line[1:]))
        return pairs

    pr = scan("pull_request")
    assert {"PR_OWN", "RESOLVED"} <= {line for _, line in pr}, pr
    assert len([sha for sha, line in pr if line == "PR_OWN"]) == 1, pr  # once, under its own commit
    assert [sha for sha, line in pr if line == "MAIN_ONLY"] == [], pr
    assert "ONLY_ON_OTHER" not in {line for _, line in pr}, pr
    push = scan("push")
    assert "RESOLVED" in {line for _, line in push}, push
    assert [sha for sha, line in push if line == "MAIN_ONLY"] == [main_sha], push
    assert "ONLY_ON_OTHER" in {line for _, line in push}, push  # the push scan's --all
    assert len([sha for sha, line in push if line == "PR_OWN"]) == 1, push  # remerge: not repeated
    # A PR cut before main's newer commits: only its own commit is in the `..` range.
    git("checkout", "-q", "-b", "early", base_sha)
    commit("early.txt", "EARLY_OWN\n")
    assert {line for _, line in scan("pull_request")} == {"EARLY_OWN"}
    # A base that is not fetched fails the step instead of scanning nothing.
    (tmp_path / "out").unlink()
    assert run_step("pull_request", base="nomain") != 0
    assert not (tmp_path / "out").exists()
    checkout = next(s for s in wf["jobs"]["gitleaks"]["steps"] if "checkout" in str(s.get("uses")))
    assert checkout["with"]["fetch-depth"] == 0  # the PR range needs the base branch's history


def test_isolated_job_ids_match_real_jobs() -> None:
    """Every job id in ISOLATED names a real job that pip-installs the built wheel."""
    workflows = cl.load_workflows()
    job_ids = {job_id for _, wf in workflows for job_id in wf["jobs"]}

    for job_id in cl.ISOLATED:
        assert job_id in job_ids, f"ISOLATED job id '{job_id}' does not exist in workflows"
        # A `pip install` of a wheel: `pip install ... dist/*.whl`. A step that only
        # installs requirements (`pip install -r requirements.lock.txt`) does not count.
        found_wheel_install = False
        for _, wf in workflows:
            if job_id in wf["jobs"]:
                job = wf["jobs"][job_id]
                for step in job.get("steps", []):
                    run = str(step.get("run", ""))
                    if "pip install" in run and ".whl" in run:
                        found_wheel_install = True
                        break
        assert found_wheel_install, (
            f"ISOLATED job id '{job_id}' does not contain a pip install of a wheel"
        )


def test_tool_specs_carry_no_unexpanded_shell_syntax() -> None:
    """A job that installs a wheel from $RUNNER_TEMP must be in ISOLATED. Otherwise tool_specs()
    hands `make dev` a literal `$RUNNER_TEMP/dist/*.whl`, which nothing expands."""
    bad = [spec for spec in cl.tool_specs() if "$" in spec or "*" in spec]
    assert not bad, f"tool_specs() has unexpanded shell syntax; register the job in ISOLATED: {bad}"


def test_tool_specs_come_only_from_hashed_locks() -> None:
    """`make dev` installs tool_specs() with `uv pip install --require-hashes`, which
    rejects any requirement without a hash, `==` pin or not. So a workflow outside
    ISOLATED installs only from a lock compiled with --generate-hashes; a bare
    `pip install PyYAML` step broke `make dev` in every fresh checkout."""
    specs = cl.tool_specs()
    assert "--require-hashes" in specs
    bad, i = [], 0
    while i < len(specs):
        flag = specs[i]
        if flag in ("-r", "-c"):
            path = Path(specs[i + 1])
            head = path.read_text(encoding="utf-8")[:2000] if path.is_file() else ""
            if "--generate-hashes" not in head:
                bad.append(f"{flag} {path}")
            i += 2
            continue
        if flag == "-e" or not flag.startswith("-"):
            bad.append(flag if flag != "-e" else f"-e {specs[i + 1]}")
        i += 2 if flag == "-e" else 1
    assert not bad, (
        "a CI install step outside ISOLATED installs a requirement without a hash; "
        f"add it to requirements-ci.txt and install requirements-ci.lock.txt instead: {bad}"
    )


def test_isolated_jobs_also_need_network() -> None:
    """An isolated job installs from the package index, so it is skipped offline like the other network jobs."""
    assert cl.ISOLATED <= cl.NEEDS_NETWORK


def test_job_needing_a_missing_tool_is_skipped(env: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
                                               capsys: Any) -> None:
    """conda-forge-deps runs only where micromamba is on PATH; elsewhere it is a skip, not a FAIL."""
    wf = "on: pull_request\njobs:\n  conda-forge-deps:\n    steps:\n      - run: micromamba create\n"
    (cl.WORKFLOWS / "x.yml").write_text(wf)
    cl.run(ns())
    assert not env["calls"]
    assert "skip (micromamba not on PATH)" in capsys.readouterr().out
    monkeypatch.setattr(cl.shutil, "which", lambda tool: f"/usr/bin/{tool}")
    cl.run(ns())
    assert env["calls"][0]["cmd"][-1] == "micromamba create"


def test_needs_tool_job_ids_match_real_jobs() -> None:
    """Every NEEDS_TOOL key names a real job, catching renames."""
    job_ids = {job_id for _, wf in cl.load_workflows() for job_id in wf["jobs"]}
    assert set(cl.NEEDS_TOOL) <= job_ids
