#!/usr/bin/env python
# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Run the pull-request CI locally, from the workflow files themselves.

`make ci-local` (top-level Makefile) runs this. It reads every workflow under
.github/workflows/ that triggers on pull_request, in file order, and executes
each job's `run:` steps with bash, from the step's working directory, with the
`${{ ... }}` expressions those steps use filled in as a PR against BASE_REF
would see them. Reading the workflows instead of restating them is the point:
a step added to CI is run here without anyone editing this file.

Differences from CI, all printed as notices:

- `uses:` steps (checkout, setup-python, upload-artifact) are skipped, and so
  are `pip install` steps: the tools come from the venv `make dev` builds,
  which installs exactly what those steps install (`tools` subcommand below).
  Jobs that install the package itself (package-smoke) run in a throwaway venv
  on a copy of the tracked tree instead, so the dev venv stays as CI's.
- Every step runs even when an earlier one fails, so one failure does not hide
  the next (CI stops a job at its first failing step).
- A matrix runs one leg: the dev venv's Python. A notice says so when that is
  not the Python CI's single-version jobs use.
- Steps that need the network are skipped when PyPI is unreachable or with
  --offline; the docker job runs only with --with-docker.
- Semgrep Secrets is an org-level check with no workflow file here; it runs as
  `semgrep scan --config p/secrets` when semgrep is on PATH, else is skipped.
- Steps that diff against `origin/$BASE_REF` (in CI, origin is intel/dokimi)
  use `<remote>/$BASE_REF` instead: <remote> is --remote / CI_LOCAL_REMOTE, else
  the remote whose URL is github.com/intel/dokimi, else origin. In a fork clone
  origin is often the fork or another repo. The ref is printed first; if it is
  missing or shares no history with HEAD, those steps fail with that reason
  instead of running. Nothing in the git config or refs is changed.

Usage:
  python scripts/ci_local.py tools                # pip specs `make dev` installs
  python scripts/ci_local.py run [--jobs a,b] [--offline] [--with-docker] [--remote intel]
"""

from __future__ import annotations

import argparse
import collections
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
WORKFLOWS = REPO / ".github" / "workflows"

# Jobs or steps that cannot run offline. Keyed by job id, or (job id, step name).
# pre-commit clones its hook repositories on first use.
NEEDS_NETWORK = {"package-smoke", "pre-commit", ("security", "pip-audit")}
NEEDS_DOCKER = {"external-catalog-smoke"}
# Jobs that `pip install .`: run on a copy of the tree in a throwaway venv.
ISOLATED = {"package-smoke"}
PIP_INSTALL = re.compile(r"^\s*(python -m )?pip install\b")
# pip options that take a value, by spelling, mapped to the short form `tools` emits.
PAIRED = {"-r": "-r", "--requirement": "-r", "-c": "-c", "--constraint": "-c",
          "-e": "-e", "--editable": "-e"}
URL = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")  # `-e git+https://...` is not a path
UPSTREAM_URL = re.compile(r"(^|[/@])github\.com[:/]intel/dokimi(\.git)?/?$", re.IGNORECASE)
ORIGIN_BASE = re.compile(r"\borigin/(?=\$\{?BASE_REF\b)")  # `origin/$BASE_REF` in a step


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True)


def upstream_remote(override: str | None) -> str:
    """The remote standing in for CI's origin: the override, else the remote whose
    URL is intel/dokimi (origin first if several are), else origin. The URL is the one
    git fetches from, so `url.<base>.insteadOf` aliases are expanded first."""
    if override:
        return override
    names = [name for name in _git("remote").stdout.split()
             if UPSTREAM_URL.search(_git("remote", "get-url", name).stdout.strip())]
    return "origin" if "origin" in names or not names else names[0]


def diffs_against_base(workflows: list[tuple[str, dict]], want: set[str] | None) -> bool:
    """Whether a selected job has a step that diffs against `origin/$BASE_REF`."""
    return any(ORIGIN_BASE.search(str(step.get("run", "")))
               for _, wf in workflows for job_id, job in wf["jobs"].items()
               if want is None or job_id in want for step in job.get("steps", []))


def resolve_base(base: str, remote: str) -> tuple[str, str | None]:
    """`<remote>/<base>`, and why it cannot be diffed against (None if it can)."""
    ref = f"{remote}/{base}"
    pick = "or choose the remote with --remote / CI_LOCAL_REMOTE"
    if _git("rev-parse", "--verify", "--quiet", f"refs/remotes/{ref}^{{commit}}").returncode:
        return ref, f"{ref} does not exist: run `git fetch {remote}`, {pick}"
    if _git("merge-base", "HEAD", ref).returncode:
        return ref, (f"{ref} has no merge base with HEAD: {remote} is not intel/dokimi "
                     f"or the clone is shallow (`git fetch --unshallow {remote}`), {pick}")
    return ref, None


def load_workflows() -> list[tuple[str, dict]]:
    """The pull_request workflows, in file-name order."""
    out = []
    for path in sorted(WORKFLOWS.glob("*.yml")):
        wf = yaml.safe_load(path.read_text(encoding="utf-8"))
        on = wf.get("on", wf.get(True))  # YAML 1.1 reads a bare `on` as True
        triggers = on if isinstance(on, (list, dict)) else [on]
        if "pull_request" in triggers:
            out.append((path.name, wf))
    return out


def tool_specs() -> list[str]:
    """pip arguments of every `pip install` step outside the isolated jobs."""
    specs: list[str] = []
    for _, wf in load_workflows():
        default_wd = wf.get("defaults", {}).get("run", {}).get("working-directory", ".")
        for job_id, job in wf["jobs"].items():
            if job_id in ISOLATED or "schedule" in str(job.get("if", "")):
                continue
            wd = job.get("defaults", {}).get("run", {}).get("working-directory", default_wd)
            for step in job.get("steps", []):
                for line in str(step.get("run", "")).splitlines():
                    if not PIP_INSTALL.match(line):
                        continue
                    args = iter(shlex.split(PIP_INSTALL.sub("", line)))
                    for a in args:
                        # `-r f`, `-rf`, `--requirement f`, `--requirement=f`
                        flag, eq, value = a.partition("=")
                        if a[:2] in PAIRED and len(a) > 2 and a[1] != "-":
                            flag, eq, value = a[:2], "=", a[2:]
                        if flag not in PAIRED:
                            if a not in specs:
                                specs.append(a)
                            continue
                        value = value if eq else next(args, "")
                        if not (PAIRED[flag] == "-e" and URL.match(value)):
                            value = str(REPO / wd / value)  # a path, from the step's cwd
                        # Deduplicate the pair: a repeated flag on its own would leave
                        # its value to be read as a package.
                        pair = [PAIRED[flag], value]
                        if not any(specs[j:j + 2] == pair for j in range(len(specs))):
                            specs.extend(pair)
    return specs


def ci_pythons() -> tuple[str | None, list[str]]:
    """CI's single-version Python (the one most `setup-python` steps name) and the
    Pythons of every matrix leg, from the workflows."""
    single: collections.Counter[str] = collections.Counter()
    legs: list[str] = []
    for _, wf in load_workflows():
        for job in wf["jobs"].values():
            matrix = (job.get("strategy") or {}).get("matrix") or {}
            for v in matrix.get("python-version", []):
                if str(v) not in legs:
                    legs.append(str(v))
            for step in job.get("steps", []):
                v = str((step.get("with") or {}).get("python-version", ""))
                if "setup-python" in str(step.get("uses", "")) and v and "${{" not in v:
                    single[v] += 1
    return (single.most_common(1)[0][0] if single else None), legs


class Context:
    def __init__(self, base: str, py: str, tmp: Path) -> None:
        self.tmp = tmp
        self.values = {
            "github.event.pull_request.base.ref": base,
            "github.event_name": "pull_request",
            "matrix.python-version": py,
            "runner.temp": str(tmp),
            "github.workspace": str(REPO),
            "needs.changes.outputs.code": "true",
            "contains(github.event.pull_request.labels.*.name, 'schema-break-ok')": "false",
            "contains(github.event.pull_request.labels.*.name, 'skip-changelog')": "false",
        }

    def expand(self, text: str) -> str:
        def sub(m: re.Match) -> str:
            key = m.group(1).strip()
            if key not in self.values:
                raise KeyError(key)
            return self.values[key]

        return re.sub(r"\$\{\{(.*?)\}\}", sub, text)

    def condition(self, expr: str) -> bool:
        expr = expr.strip()
        if expr.startswith("${{"):
            expr = expr[3:-2]
        for fn in ("always()", "success()", "!cancelled()"):
            expr = expr.replace(fn, "True")
        for key, val in sorted(self.values.items(), key=lambda kv: -len(kv[0])):
            expr = expr.replace(key, repr(val))
        expr = expr.replace("&&", " and ").replace("||", " or ")
        expr = re.sub(r"!(?!=)", " not ", expr)
        try:
            return bool(eval(expr, {"__builtins__": {}}, {"True": True}))  # nosec B307
        except (NameError, SyntaxError, TypeError):
            print(f"NOTICE: cannot evaluate `if: {expr.strip()}`; running the step")
            return True


def network_ok() -> bool:
    try:
        urllib.request.urlopen("https://pypi.org/simple/pip/", timeout=8)  # nosec B310
        return True
    except Exception:
        return False


def make_isolated_env(tmp: Path, py: str) -> tuple[Path, dict]:
    """A fresh venv and a copy of the tracked tree, for jobs that `pip install .`."""
    src = tmp / "src"
    files = subprocess.run(
        ["git", "ls-files", "-co", "--exclude-standard", "-z"],
        cwd=REPO, check=True, capture_output=True,
    ).stdout.decode().split("\0")
    for f in filter(None, files):
        if (REPO / f).is_file():
            (src / f).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(REPO / f, src / f)
    venv = tmp / "venv"
    subprocess.run(["uv", "venv", "-q", "--seed", "--python", py, str(venv)], check=True)
    env = dict(os.environ, VIRTUAL_ENV=str(venv))
    env["PATH"] = f"{venv / 'bin'}{os.pathsep}{env['PATH']}"
    return src, env


def run(args: argparse.Namespace) -> int:
    py = f"{sys.version_info.major}.{sys.version_info.minor}"
    want = set(args.jobs.split(",")) if args.jobs else None
    ci_py, legs = ci_pythons()
    other_py = (f"NOTICE: this venv runs Python {py}, CI's single-version jobs {ci_py}: "
                "steps conditioned on CI's version are skipped. Re-run `make dev` to "
                "convert the venv in place.") if ci_py and py != ci_py else ""
    if other_py:
        print(other_py)
    online = not args.offline and network_ok()
    if not args.offline and not online:
        print("NOTICE: PyPI unreachable -- network steps will be skipped")
    results: list[tuple[str, str, float]] = []

    def record(name: str, status: str, secs: float = 0.0) -> None:
        results.append((name, status, secs))
        if status.startswith("skip"):
            print(f"\n--- {name}: {status}")

    with tempfile.TemporaryDirectory(prefix="ci-local-") as t:
        tmp = Path(t)
        ctx = Context(args.base, py, tmp)
        workflows = load_workflows()
        remote = upstream_remote(args.remote)
        base_ref, base_err = resolve_base(args.base, remote)
        print(f"Base: {base_ref} (CI's origin/{args.base})")
        if base_err and diffs_against_base(workflows, want):
            print(f"ERROR: {base_err}\n  Steps that diff against the base will fail.")
        summary = tmp / "step-summary.md"
        base_env = dict(os.environ, RUNNER_TEMP=str(tmp), GITHUB_STEP_SUMMARY=str(summary),
                        GITHUB_OUTPUT=str(tmp / "github-output"), GITHUB_WORKSPACE=str(REPO),
                        CI="true")
        for wf_name, wf in workflows:
            default_wd = wf.get("defaults", {}).get("run", {}).get("working-directory", ".")
            for job_id, job in wf["jobs"].items():
                label = f"{wf_name}:{job_id}"
                if want is not None and job_id not in want:
                    continue
                if "if" in job and not ctx.condition(str(job["if"])):
                    record(label, "skip (job condition false on a PR)")
                    continue
                if job_id in NEEDS_DOCKER and not args.with_docker:
                    record(label, "skip (needs docker + network; pass --with-docker)")
                    continue
                if job_id in NEEDS_NETWORK and not online:
                    record(label, "skip (needs network)")
                    continue
                wd = job.get("defaults", {}).get("run", {}).get("working-directory", default_wd)
                root, env = REPO, base_env
                if job_id in ISOLATED:
                    root, iso_env = make_isolated_env(tmp / job_id, py)
                    env = {**iso_env, **{k: base_env[k] for k in ("RUNNER_TEMP", "CI")}}
                for i, step in enumerate(job.get("steps", [])):
                    if "run" not in step:
                        continue
                    name = f"{label} / {step.get('name', step.get('id', f'step {i}'))}"
                    script = str(step["run"])
                    if job_id not in ISOLATED:  # provided by `make dev`
                        lines = [x for x in script.splitlines() if not PIP_INSTALL.match(x)]
                        if not any(x.strip() for x in lines):
                            continue
                        script = "\n".join(lines)
                    if (job_id, step.get("name")) in NEEDS_NETWORK and not online:
                        record(name, "skip (needs network)")
                        continue
                    if "if" in step and not ctx.condition(str(step["if"])):
                        record(name, "skip (step condition false)")
                        continue
                    if ORIGIN_BASE.search(script):
                        if base_err:
                            print(f"\n=== {name}\nERROR: {base_err}")
                            record(name, "FAIL (no usable base ref, see ERROR)")
                            continue
                        script = ORIGIN_BASE.sub(f"{remote}/", script)
                    try:
                        script = ctx.expand(script)
                        step_env = {k: ctx.expand(str(v)) for src in (wf, job, step)
                                    for k, v in (src.get("env") or {}).items()}
                    except KeyError as e:
                        record(name, f"skip (unknown expression {e})")
                        continue
                    cwd = root / step.get("working-directory", wd)
                    print(f"\n=== {name}  (cwd {cwd.relative_to(root)})", flush=True)
                    t0 = time.monotonic()
                    rc = subprocess.run(
                        ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", script],
                        cwd=cwd, env={**env, **step_env},
                    ).returncode
                    record(name, "pass" if rc == 0 else f"FAIL (exit {rc})", time.monotonic() - t0)

        if want is None or "semgrep" in want:
            if shutil.which("semgrep") and online:
                print("\n=== Semgrep Secrets (approximation: p/secrets)", flush=True)
                t0 = time.monotonic()
                rc = subprocess.run(["semgrep", "scan", "--config", "p/secrets", "--error",
                                     "--metrics", "off", "--quiet"], cwd=REPO).returncode
                record("semgrep secrets", "pass" if rc == 0 else f"FAIL (exit {rc})",
                       time.monotonic() - t0)
            else:
                record("semgrep secrets (org-level check, not in workflows)",
                       "skip (semgrep not on PATH or offline; runs on the PR)")

    print("\n================ ci-local summary ================")
    for name, status, secs in results:
        print(f"{status:<52} {secs:6.0f}s  {name}")
    failed = [r for r in results if r[1].startswith("FAIL")]
    print(f"\n{len(failed)} failed, "
          f"{sum(r[1] == 'pass' for r in results)} passed, "
          f"{sum(r[1].startswith('skip') for r in results)} skipped")
    others = ", ".join(v for v in legs if v != py) or "none"
    print(f"Not covered locally: the matrix legs on other Pythons ({others}), "
          "and CI's merge commit (this runs your working tree).")
    if other_py:
        print(other_py)
    return 1 if failed else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("tools", help="print the pip specs the CI install steps use, one per line")
    r = sub.add_parser("run", help="run the PR workflows' steps")
    r.add_argument("--jobs", help="comma-separated job ids to run (default: all)")
    r.add_argument("--base", default=os.environ.get("BASE_REF", "main"),
                   help="base branch, compared as <remote>/<base> (default: main)")
    r.add_argument("--remote", default=os.environ.get("CI_LOCAL_REMOTE"),
                   help="remote with intel/dokimi's branches, standing in for CI's origin "
                        "(default: $CI_LOCAL_REMOTE, else the remote whose URL is "
                        "github.com/intel/dokimi, else origin)")
    r.add_argument("--offline", action="store_true", help="skip steps that need the network")
    r.add_argument("--with-docker", action="store_true", help="also run the docker job")
    args = ap.parse_args()
    if args.cmd == "tools":
        print("\n".join(tool_specs()))
        return 0
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
