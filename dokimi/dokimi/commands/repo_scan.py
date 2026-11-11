# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`repo scan` helpers kept out of commands/repo.py, which is at its size cap: the `--typecheck` check and the text report."""

from __future__ import annotations

import argparse
import os
import shutil
import sys

from dokimi.utils import InputError


def _typecheck_or_exit(args: argparse.Namespace):
    """The ``--typecheck`` check, or None; ``--python-env`` needs it and must name an interpreter."""
    from dokimi.repos.l0 import Check

    if not args.typecheck:
        if args.python_env is not None:
            raise InputError("--python-env needs --typecheck")
        if args.typecheck_tool is not None:
            raise InputError("--typecheck-tool needs --typecheck")
        return None
    tool = args.typecheck_tool or "mypy"
    # a directory is X_OK too: only a regular file can be the interpreter
    if args.python_env is not None and not (os.path.isfile(args.python_env) and os.access(args.python_env, os.X_OK)):
        raise InputError(f"--python-env {args.python_env}: not an executable interpreter")
    if tool != "mypy" and shutil.which(tool) is None:
        raise InputError(f"--typecheck-tool {tool}: not on PATH")
    python = os.path.abspath(args.python_env) if args.python_env is not None else None  # the tool runs elsewhere
    return Check(tool, python)


def _loaded_with_default(tree, manifest) -> tuple[str, ...]:
    """The Markdown rules and ``@`` imports Claude Code loads with the default guidance; () for a written manifest.

    The default globs name CLAUDE.md but not what it pulls in: a stale path in a ``.claude/rules`` file or an
    imported file reaches the agent as surely as one in CLAUDE.md. They are named by path, not glob, so a repository
    without rules gets no warning for them. A written manifest scans what it says, so a gate on it does not change.
    """
    if manifest.path != "<default>":
        return ()
    from dokimi.repos.budget import budget
    from dokimi.repos.harness_profile import load_profile

    p = load_profile("claude-code")
    entries = budget(tree, p.guidance_names, p.import_max_depth, p.reads_claude_rules,
                     claude_displaces_agents=p.claude_displaces_agents, follow_imports=p.follows_imports)["entries"]
    # Only Markdown is read as guidance: an `@package.json` import is loaded, but its text is no guidance claims
    listed = set(tree.files())
    reached = (_listed(tree, listed, e["path"]) for e in entries if e["kind"] in ("rule", "import"))
    loaded = tuple(dict.fromkeys(p for p in reached if p is not None and _target(tree, p).endswith(".md")))
    if loaded:
        print(f"note: also checking {len(loaded)} .claude/rules and @ imported files Claude Code loads with the default guidance",
              file=sys.stderr)
    return loaded


def _target(tree, path: str) -> str:
    """The file a listed ``path`` links to, so a ``cfg.md`` link to ``package.json`` is not taken for Markdown."""
    from dokimi.repos import SourceError

    try:
        return tree.resolve(path)
    except (SourceError, OSError, ValueError):
        return path


def _listed(tree, listed: set[str], path: str) -> str | None:
    """``path`` as the tree lists it: an import through a linked directory (``@lnk/x.md``) is the file it reaches."""
    from dokimi.repos import SourceError

    if path in listed:
        return path
    try:
        target = tree.resolve(path)
    except (SourceError, OSError, ValueError):
        return None
    return target if target in listed else None


def _print_scan_text(manifest, payload: dict, out: str | None) -> None:
    """The text report of ``repo scan``: the counts, the warnings, the typecheck and static lines, the FAIL lines."""
    c = payload["counts"]
    detected = " (detected)" if payload["adapters_detected"] else ""
    print(f"{manifest.id}: {len(payload['files'])} guidance files in {payload['repo']['tree']}; "
          f"adapters: {', '.join(payload['adapters']) or 'none'}{detected}")
    print(f"  claims: {c['total']} (ok {c['ok']}, unresolved {c['unresolved']}, fail {c['fail']})")
    for warning in payload["warnings"]:
        print(f"  warning: {warning}")
    if payload["incomplete"]:
        print(f"  incomplete: {'; '.join(payload['incomplete_reasons'])} (repo scan --fail-on-fail exits 1)")
    tc = payload["typecheck"]
    if tc is not None:
        where = tc["python"] or "no --python-env: unknown"
        print(f"  typecheck {tc['tool']} {tc['tool_version'] or ''} ({where}): {tc['fences']} python fences, "
              f"{tc['checked']} checked, {tc['fail']} fail, {tc['unknown']} unknown, {tc['syntax']} not Python")
    static_info = payload["static"]
    if static_info is not None and not static_info["errors"]:
        r = payload["by_source"]["repository"]
        print(f"  repository static: {r['total']} claims, {r['fail']} fail; "
              f"{static_info['superseded']} framework claims replaced")
    for claim in payload["claims"]:
        if claim["status"].split("_", 1)[0] == "fail":
            print(f"  FAIL {claim['file']}:{claim['line']} [{claim.get('kind')}] "
                  f"{str(claim.get('claim'))[:90]} -- {claim.get('why')}")
    if out:
        print(f"wrote {out}")
