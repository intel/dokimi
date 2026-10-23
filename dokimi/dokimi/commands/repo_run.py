# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""repo tasks check / repo run / repo report / repo compare: the repository-mode runner (contract §3, §4, §4.3)."""

from __future__ import annotations

import argparse
import contextlib
import json
import math
import shutil
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from dokimi.repos.manifest import RepoManifest
    from dokimi.repos.workspace import Arm

from dokimi.repos.harness_profile import DELEGATION_MODES


def _context(args: argparse.Namespace, out: Path, pin: bool = False):
    """Source, manifest, exported eval dir, image and caches shared by `tasks check` and `run`.

    With ``pin`` (a batch), the batch keeps the eval tree it started with: a later invocation
    from another revision would grade new cells with other graders, so it is refused. A working
    tree has no revision, so the batch is pinned to the digests of its eval dir and of the image
    (whose tag is the build context's digest).
    """
    from dokimi.repos import RepoSource, open_source
    from dokimi.repos.container import DockerRunner
    from dokimi.repos.grading import EvalContext
    from dokimi.repos.graders import graders_digest
    from dokimi.repos.runner import slug
    from dokimi.repos.source import default_cache_dir
    from dokimi.repos.workspace import export_build_context, export_eval_dir, source_git_dir

    from dokimi.repos.manifest import require_runnable

    from .repo import _cache_dir, _load_manifest

    source = RepoSource.parse(args.source)
    tree = open_source(source, _cache_dir(args))
    manifest = _load_manifest(tree, args)
    require_runnable(manifest)
    if manifest.image is None:
        raise ValueError(f"{manifest.path} declares no `image`; entry points and agents run in it")
    git_dir = source_git_dir(source, tree, _cache_dir(args))
    content = out / "_eval" / "content"
    # a pinned batch keeps its content until the pin is checked
    staged = export_eval_dir(tree, manifest, content.with_name("content.next") if pin else content)
    runner = DockerRunner(args.docker)
    image, ctx_dir = args.image, None
    if not image:
        sha = getattr(tree, "sha", None)
        if sha is None:  # a working tree: tag by what is built, or an edited Dockerfile reuses a stale image
            ctx_dir = export_build_context(tree, manifest, out / "_eval" / "build-context")
            tag = "worktree-" + context_digest(ctx_dir)[:12]
        else:
            tag = sha[:12]
        image = f"dokimi/{slug(manifest.id).lower()}:{tag}"
    elif not runner.image_exists(image):
        raise ValueError(f"--image {image} does not exist")
    if pin:
        stamp = {"tree": tree.label, "eval_dir": context_digest(staged), "image": image}
        if _uses_builtin_graders(staged, manifest):  # else no score depends on them: an upgrade keeps the batch
            stamp["graders"] = graders_digest()
        _pin_batch(out, stamp)
        shutil.rmtree(content, ignore_errors=True)
        staged.rename(content)
    if not args.image and (args.rebuild_image or not runner.image_exists(image)):
        print(f"building {image} from {tree.label} (tasks removed)", file=sys.stderr)
        ctx_dir = ctx_dir or export_build_context(tree, manifest, out / "_eval" / "build-context")
        runner.build(ctx_dir, ctx_dir / manifest.image.dockerfile, image, args.build_network,
                     timeout_s=args.build_timeout)
    if ctx_dir is not None:
        shutil.rmtree(ctx_dir)
    overrides = dict(_pair(c) for c in args.cache or ())
    unknown = sorted(set(overrides) - {c.name for c in manifest.image.caches})
    if unknown:
        raise ValueError(f"--cache names no cache of the manifest: {', '.join(unknown)}")
    cache_root = Path(args.cache_root) if args.cache_root else default_cache_dir().parent / "caches" / manifest.id
    caches = {}
    for c in manifest.image.caches:
        caches[c.name] = Path(overrides.get(c.name) or cache_root / c.name).resolve()
        caches[c.name].mkdir(parents=True, exist_ok=True)
    uses_source = any("{source_dir}" in v for v in manifest.entry_points.values())
    source_dir = Path(args.source_dir).resolve() if args.source_dir else (git_dir if uses_source else None)
    ctx = EvalContext(manifest=manifest, git_dir=git_dir, content_dir=content, image=image, runner=runner,
                      caches=caches, source_dir=source_dir, entry_timeout_s=args.grade_timeout)
    return ctx, tree


def context_digest(root: Path) -> str:
    """sha256 over every path, mode bit and byte of a build context (a link by its target)."""
    import hashlib
    import os

    h = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root).as_posix()
        if path.is_symlink():
            h.update(f"L {rel}\0{os.readlink(path)}\0".encode())
        elif path.is_file():
            h.update(f"F {rel}\0{int(os.access(path, os.X_OK))}\0".encode())
            h.update(hashlib.sha256(path.read_bytes()).digest())
        elif path.is_dir():
            h.update(f"D {rel}\0".encode())
    return h.hexdigest()


def _uses_builtin_graders(content: Path, manifest: RepoManifest) -> bool:
    """Whether a task of the exported eval dir names a ``dokimi:`` grader; an unreadable task.json counts."""
    from dokimi.repos.graders import builtin_name
    from dokimi.repos.workspace import load_tasks

    try:
        tasks = load_tasks(content, manifest)
    except (OSError, ValueError):
        return True
    return any(isinstance(t.raw, dict) and builtin_name(t.raw.get("grader")) is not None for t in tasks)


def _pin_batch(batch_dir: Path, pin: dict[str, str]) -> None:
    path = batch_dir / "_eval" / "batch.json"
    if path.is_file():
        old = json.loads(path.read_text(encoding="utf-8"))
        changed = [k for k in pin if old.get(k) != pin[k]]
        if changed:
            was = ", ".join(f"{k} {old.get(k)}" for k in changed)
            now = ", ".join(f"{k} {pin[k]}" for k in changed)
            raise ValueError(f"batch {batch_dir.name} was run from {was}, not {now}; use another --batch")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(pin) + "\n", encoding="utf-8")


def _pair(text: str) -> tuple[str, str]:
    name, sep, path = text.partition("=")
    if not sep or not name or not path:
        raise ValueError(f"--cache takes NAME=PATH, got {text!r}")
    return name, path


def _progress(line: str) -> None:
    """One progress line, flushed: under a pipe (CI) stdout is block-buffered and the per-task
    lines would otherwise all appear when the command exits."""
    print(line, flush=True)


def _errors():
    from dokimi.repos import ManifestError, SourceError
    from dokimi.repos.container import ContainerError
    from dokimi.repos.harness_profile import ProfileError
    from dokimi.repos.workspace import PrepError

    return (SourceError, ManifestError, ContainerError, PrepError, ProfileError, ValueError, OSError)


def cmd_repo_tasks_check(args: argparse.Namespace) -> None:
    """Self-check (no LLM): null fails the gate, oracle passes it, the agent cannot see the tasks."""
    from dokimi.repos.selfcheck import run_check
    from dokimi.repos.workspace import load_tasks

    out = Path(args.out).resolve()
    try:
        ctx, _ = _context(args, out)
        tasks = load_tasks(ctx.content_dir, ctx.manifest, args.task)
        print(f"{len(tasks)} task(s), image {ctx.image}{' (warm: with network)' if args.warm else ''}",
              file=sys.stderr)
        results = run_check(ctx, tasks, out / "check", warm=args.warm, jobs=args.jobs, log=_progress)
    except _errors() as exc:
        print(f"✗ {exc}", file=sys.stderr)
        sys.exit(1)
    bad = [r for r in results if r["verdict"] != "ok"]
    print(f"{len(results) - len(bad)}/{len(results)} ok; wrote {out / 'check' / 'check.json'}")
    allowed = [r for r in bad if args.allow_unsupported and r.get("unsupported_by") == "host"]
    if allowed:
        print(f"{len(allowed)} unsupported on this host, allowed by --allow-unsupported: "
              + ", ".join(r["task"] for r in allowed))
    if len(bad) > len(allowed):
        sys.exit(1)


def cmd_repo_run(args: argparse.Namespace) -> None:
    """Run agents on the tasks, grade every run, write one result.json per cell."""
    arms = [a for spec in args.arm for a in spec.split(",") if a]
    batch_dir = (Path(args.out) / args.batch).resolve()
    with contextlib.ExitStack() as lock:  # held until every cell is graded
        _run_locked(args, arms, batch_dir, lock)


def _run_locked(args: argparse.Namespace, arms: list[str], batch_dir: Path, lock: contextlib.ExitStack) -> None:
    from dokimi.repos import RepoSource, open_source
    from dokimi.repos.container import docker_network
    from dokimi.repos.harness_profile import load_profile
    from dokimi.repos.runner import (
        AgentOptions, agent_network, batch_lock, harness_version, run_batch, task_caps, validate_tasks)
    from dokimi.repos.selfcheck import ANCESTOR_NAMES, isolation_probe
    from dokimi.repos.workspace import load_tasks, parse_arm, source_git_dir

    from .repo import _cache_dir

    try:
        parsed = [parse_arm(a) for a in arms]  # before the image build: a typo or an unreadable patch fails now
        profile = load_profile(args.harness)
        profile.tool_args(args.tools, args.delegation)  # a profile that cannot turn subagents off: refuse now
        harness_bin = _harness_bin(args.bin or profile.bin)
        source = RepoSource.parse(args.source)  # a misspelled revision fails before the build and the pin too
        arms = _pin_arms(parsed, source_git_dir(source, open_source(source, _cache_dir(args)), _cache_dir(args)),
                         args.source)
        lock.enter_context(batch_lock(batch_dir))  # before _context, which rewrites _eval/content
        ctx, _ = _context(args, batch_dir, pin=True)
        tasks = load_tasks(ctx.content_dir, ctx.manifest, args.task)
        validate_tasks(tasks)  # refuse malformed host requirements before containers start
        options = [AgentOptions(profile=profile, model=m, harness_bin=harness_bin, agent_network=args.agent_network,
                                tools=args.tools, delegation=args.delegation, timeout_s=args.timeout,
                                max_turns=args.max_turns, budget_usd=args.budget) for m in args.model]
        for task in tasks:  # refuse before anything runs, not cell by cell
            task_caps(task, options[0])
            agent_network(options[0], ctx, task)
            docker_network(ctx.manifest.image.network, task.egress)  # grade has no egress route either
        version = harness_version(ctx, options[0], batch_dir / "_eval")
        for opt in options:
            opt.harness_version = version
        names = tuple(dict.fromkeys(ANCESTOR_NAMES + profile.guidance_names))
        problems = isolation_probe(ctx, tasks, batch_dir / "_eval" / "probe", names)
        if problems:
            raise ValueError("the agent's container is not isolated: " + "; ".join(problems))
        print(f"{profile.name} {version} in {ctx.image}; {len(tasks)} task(s) x {len(arms)} arm(s) x "
              f"{len(options)} model(s) x {args.reps} rep(s) -> {batch_dir}", file=sys.stderr)
        summary = run_batch(ctx, tasks, arms, options, args.reps, batch_dir, jobs=args.jobs, force=args.force,
                            max_batch_usd=args.max_batch_usd, log=_progress)
    except _errors() as exc:
        print(f"✗ {exc}", file=sys.stderr)
        sys.exit(1)
    print(f"{summary['ran']} ran, {summary['skipped']} already done, {len(summary['errors'])} crashed, "
          f"{summary['budget_stopped']} not started (batch budget); ${summary['spent_usd']:.4f}")
    if summary["probe_failed"]:
        print(f"✗ the host probe failed, so these tasks have cells that were not run: {', '.join(summary['probe_failed'])}",
              file=sys.stderr)
    if summary["errors"] or summary["budget_stopped"] or summary["probe_failed"]:
        sys.exit(1)


def _pin_arms(arms: list[Arm], git_dir: Path, source: str) -> list[str]:
    """Each arm under the name its cells get, resolved once for the whole batch (a revision to its
    full SHA, so a branch that moves later names another arm); two spellings of one arm run once."""
    from dokimi.repos import RepoSource
    from dokimi.repos.workspace import pin_arm

    parsed = RepoSource.parse(source)
    remote = parsed.remote
    worktree = None if remote else Path(parsed.location)  # HEAD of a linked worktree is not the main one's
    pinned: list[str] = []  # Arm objects: run_batch hands each to prepare_workspace
    first_path: dict[str, str | None] = {}
    for arm in arms:
        named = pin_arm(arm, git_dir, fetch=remote, worktree_dir=worktree)
        if named != named.spec:
            detail = f"{named.patch_path}, sha256 {named.patch_sha256}" if named.patch is not None else named.spec
            print(f"arm {named} = {detail}", file=sys.stderr)
        first = first_path.setdefault(named, named.patch_path)
        if first != named.patch_path:  # same bytes under another path: one cell, recorded under the first path
            print(f"arm {named}: {named.patch_path} and {first} are the same patch and share one cell; "
                  f"meta.json names {first}", file=sys.stderr)
        pinned.append(named)
    return list(dict.fromkeys(pinned))


def _harness_bin(name: str) -> Path:
    """The harness executable on this host (followed through symlinks), mounted into the agent's container."""
    found = shutil.which(name)
    if not found:
        raise ValueError(f"harness executable {name!r} not found; pass --bin <path>")
    return Path(found).resolve()


def cmd_repo_report(args: argparse.Namespace) -> None:
    """Per (task, arm, model): gated success, grader failures, missing cells, cost, turns."""
    from dokimi.repos.report import format_report, summarize

    batch_dir = Path(args.batch_dir)
    if not batch_dir.is_dir():
        print(f"✗ not a directory: {batch_dir}", file=sys.stderr)
        sys.exit(1)
    summary = summarize(batch_dir)
    print(json.dumps(summary, indent=2) if args.json else format_report(summary))


def cmd_repo_compare(args: argparse.Namespace) -> None:
    """Per (treatment, model, measure): per-task deltas against the baseline, 95% CI, sign-flip p."""
    from dokimi.repos.compare import CompareError, compare, format_compare

    dirs = [Path(d) for d in args.batch_dirs]
    for d in dirs:
        if not d.is_dir():
            print(f"✗ not a directory: {d}", file=sys.stderr)
            sys.exit(1)
    arms = [a for spec in args.arm for a in spec.split(",") if a] if args.arm else None
    try:
        summary = compare(dirs, args.baseline, arms, args.model, tuple(args.metric or ()))
    except CompareError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        sys.exit(1)
    print(json.dumps(summary, indent=2) if args.json else format_compare(summary))


def _usd(text: str) -> float:
    """A positive, finite USD amount (``0``, negatives, ``nan`` and ``inf`` would disable the cap)."""
    value = float(text)
    if not math.isfinite(value) or value <= 0:
        raise argparse.ArgumentTypeError(f"must be a positive number of USD, got {text!r}")
    return value


def _common(p: argparse.ArgumentParser, source_help: str, cache_help: str, positive_int) -> None:
    p.add_argument("source", help=source_help)
    p.add_argument("--manifest", default=".agent-eval/repo-eval.yaml",
                   help="Manifest path relative to the repository root")
    p.add_argument("--manifest-file", default=None, dest="manifest_file",
                   help="Read the manifest from this local file instead of from the tree")
    p.add_argument("--task", action="append", default=None, help="Only this task id; repeatable (default: all)")
    p.add_argument("--image", default=None, help="Use this image instead of building one from the eval tree")
    p.add_argument("--rebuild-image", action="store_true", dest="rebuild_image",
                   help="Build the image even if the tag exists")
    p.add_argument("--build-network", default=None, dest="build_network",
                   help="docker build --network (for example host, behind a local proxy)")
    # repos.container.BUILD_TIMEOUT_S, written out to keep dokimi.repos unimported at startup
    p.add_argument("--build-timeout", type=positive_int, default=3 * 3600, dest="build_timeout",
                   help="Seconds the image build may run before it fails (default 10800, 3 hours)")
    p.add_argument("--cache", action="append", default=None, metavar="NAME=PATH",
                   help="Host directory for the manifest cache NAME; repeatable")
    p.add_argument("--cache-root", default=None, dest="cache_root",
                   help="Parent of cache directories without --cache "
                   "(default: ~/.cache/dokimi/caches/<repo id>)")
    p.add_argument("--source-dir", default=None, dest="source_dir",
                   help="Full-history clone mounted as {source_dir} (default: the source's own git dir)")
    p.add_argument("--grade-timeout", type=positive_int, default=3600, dest="grade_timeout",
                   help="Seconds per grade/oracle run (default 3600)")
    p.add_argument("-j", "--jobs", type=positive_int, default=1, help="Parallel tasks or cells (default 1)")
    p.add_argument("--docker", default="docker", help="docker CLI (default: docker)")
    p.add_argument("--cache-dir", default=None, dest="cache_dir", help=cache_help)


def register(repo_sub, positive_int, source_help: str, cache_help: str) -> None:
    tasks_p = repo_sub.add_parser("tasks", help="Task self-checks (no LLM)")
    tasks_sub = tasks_p.add_subparsers(dest="repo_tasks_cmd", required=True)
    check_p = tasks_sub.add_parser(
        "check", help="Per task: grade on the untouched workspace must fail the gate, oracle then grade must "
        "pass it (score-only: reach score.max), and the agent's container must not see tasks or source")
    _common(check_p, source_help, cache_help, positive_int)
    check_p.add_argument("--warm", action="store_true",
                         help="Run null and oracle with network to fill the caches before offline runs")
    check_p.add_argument("--allow-unsupported", action="store_true", dest="allow_unsupported",
                         help="Exit 0 when the only tasks that are not ok are unsupported because this host "
                         "misses their task.json `host` requirements (still printed with the reason; "
                         "with --warm the host is judged too). Broken tasks, tasks that need egress "
                         "and tasks whose host probe failed still exit 1")
    check_p.add_argument("--out", default="repo-check", help="Work directory (default: repo-check)")
    check_p.set_defaults(func=cmd_repo_tasks_check)

    run_p = repo_sub.add_parser(
        "run", help="Run an agent per (task, arm, model, rep) in the image and grade each run; one "
        "result.json per cell, already finished cells skipped")
    _common(run_p, source_help, cache_help, positive_int)
    run_p.add_argument("--arm", action="append", default=None, required=True,
                       help="guidance:none | guidance:raw | guidance:bridge | guidance:rev:REV (the base with "
                       "the guidance files of revision REV of the source; the arm is named by REV's full SHA, "
                       "resolved once per batch) | patch:FILE (a unified diff on this host, applied with git "
                       "apply on top of guidance:raw; named patch:<file name>@<sha256 prefix>, path and "
                       "sha256 in meta.json); repeatable or comma-separated")
    run_p.add_argument("--model", action="append", required=True,
                       help="Model string passed to the harness as it is; repeatable")
    run_p.add_argument("--reps", type=positive_int, default=1)
    run_p.add_argument("--batch", required=True, help="Batch name: cells go to <out>/<batch>/")
    run_p.add_argument("--out", default="repo-runs", help="Parent of batch directories (default: repo-runs)")
    run_p.add_argument("--harness", default="claude-code", help="Built-in profile name or profile YAML path")
    run_p.add_argument("--bin", default=None,
                       help="Harness executable on this host, mounted read-only into the agent's container "
                       "(default: the profile's `bin` on PATH)")
    run_p.add_argument("--agent-network", choices=("host", "bridge"), default=None, dest="agent_network",
                       help="REQUIRED while the egress proxy is not implemented: the agent's container gets "
                       "this network, so its commands can reach any host. Recorded in meta.json")
    run_p.add_argument("--tools", default="all", help="Profile tool mode (default: all)")
    run_p.add_argument("--delegation", choices=DELEGATION_MODES, default="native",
                       help="native: the agent may start subagents (default); none: the profile's tool "
                            "arguments without the subagent tools, for a single-agent baseline")
    run_p.add_argument("--timeout", type=positive_int, default=2700,
                       help="Seconds per agent run unless task.json sets timeout_s (default 2700)")
    run_p.add_argument("--max-turns", type=positive_int, default=200, dest="max_turns",
                       help="Unless task.json sets max_turns (default 200)")
    run_p.add_argument("--budget", type=_usd, default=2.0,
                       help="USD cap per agent run unless task.json sets budget_usd (default 2.0)")
    run_p.add_argument("--max-batch-usd", type=_usd, default=None, dest="max_batch_usd",
                       help="Start no new cell once this invocation has spent this much")
    run_p.add_argument("--force", action="store_true", help="Rerun cells that already have result.json")
    run_p.set_defaults(func=cmd_repo_run)

    report_p = repo_sub.add_parser("report", help="Per (task, arm, model) table over a batch directory")
    report_p.add_argument("batch_dir", help="<out>/<batch> of `repo run`")
    report_p.add_argument("--json", action="store_true", help="Print the summary as JSON")
    report_p.set_defaults(func=cmd_repo_report)

    compare_p = repo_sub.add_parser(
        "compare", help="Treatment arms against a baseline arm, paired by task, over one or more batch directories")
    compare_p.add_argument("batch_dirs", nargs="+", help="<out>/<batch> directories of `repo run`")
    compare_p.add_argument("--baseline", required=True,
                           help="Baseline arm: ARM, or BATCH/ARM to take it from one batch only")
    compare_p.add_argument("--arm", action="append", default=None,
                           help="Treatment arm (ARM or BATCH/ARM); repeatable or comma-separated "
                           "(default: every other arm)")
    compare_p.add_argument("--model", action="append", default=None, help="Only this model; repeatable")
    compare_p.add_argument("--metric", action="append", default=None,
                           help="Also compare this numeric or boolean grade.json metric; repeatable")
    compare_p.add_argument("--json", action="store_true", help="Print the comparison as JSON")
    compare_p.set_defaults(func=cmd_repo_compare)
