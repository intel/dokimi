# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`repo scan` helpers kept out of commands/repo.py, which is at its size cap: the `--typecheck` check, the text
report, and the reason and gate of a failed --json or --fail-on-fail run."""

from __future__ import annotations

import argparse
import os
import sys
import shutil

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


def _loaded_with_default(tree, manifest) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """The rules and the Markdown ``@`` imports Claude Code loads with the default guidance, and the warnings of the rules
    budget does not count (a linked ``.claude/rules``); both () for a written manifest.

    The default globs name CLAUDE.md but not what it pulls in: a stale path in a ``.claude/rules`` file or an
    imported file reaches the agent as surely as one in CLAUDE.md. They are named by path, not glob, so a repository
    without rules gets no warning for them. A written manifest scans what it says, so a gate on it does not change.
    """
    if manifest.path != "<default>":
        return (), ()
    from dokimi.repos.budget import RULE_GLOB, budget
    from dokimi.repos.harness_profile import load_profile

    p = load_profile("claude-code")
    inventory = budget(tree, p.guidance_names, p.import_max_depth, p.reads_claude_rules,
                       claude_displaces_agents=p.claude_displaces_agents, follow_imports=p.follows_imports)
    entries = inventory["entries"]
    # a rule the tree cannot list (a link to a directory, a dangling link) is not checked, so the scan is incomplete
    not_counted = tuple(w for w in inventory["warnings"] if "are not counted" in w)
    # Only Markdown is read as guidance: an `@package.json` import is loaded, but its text is no guidance claims
    listed = set(tree.files())
    # budget lists a rule or import that is also a guidance name (CLAUDE.local.md, or a rule linked to it) as guidance,
    # not as a rule or import: such a guidance entry that loads and that the default globs do not match is checked too
    from dokimi.repos.manifest import match_glob

    def _unmatched_guidance(e) -> bool:
        return (e["kind"] == "guidance" and e["when"] != "off"
                and not any(match_glob(g, e["path"]) for g in manifest.guidance_globs))

    reached = ((e["kind"], _listed(tree, listed, e["path"])) for e in entries
               if e["kind"] in ("rule", "import") or _unmatched_guidance(e))
    # a rule or a guidance name is checked whatever it links to; an import only when it is Markdown
    # budget folds a rule linked to a guidance name into that name, so the rule path is named here too
    linked = (_listed(tree, listed, f) for f in sorted(listed) if p.reads_claude_rules and RULE_GLOB.match(f))
    loaded = tuple(dict.fromkeys([p for kind, p in reached if p is not None
                                  and (kind != "import" or _target(tree, p).endswith(".md"))]
                                 + [t for t in linked if t is not None]))
    # a file the default globs match is checked anyway, so the note counts only the files they do not match
    new = [p for p in loaded if not any(match_glob(g, p) for g in manifest.guidance_globs)]
    if new:
        print(f"note: also checking {len(new)} files Claude Code loads beyond the default globs: rules, @ imports, "
              "and guidance names such as CLAUDE.local.md", file=sys.stderr)
    return loaded, not_counted


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


def _scan_reason(payload: dict, static_errors: list, fail_on_fail: bool) -> str:
    """Why a --json scan failed: every cause the gate tripped on, joined by ``; `` (a scan can fail on more than
    one). The --static problems always fail the run; the failed claims and the incomplete scan only under
    --fail-on-fail."""
    parts = []
    if static_errors:
        parts.append(f"{len(static_errors)} --static problem(s) not merged")
    if fail_on_fail and payload["counts"]["fail"]:
        parts.append(f"{payload['counts']['fail']} claim(s) failed")
    if fail_on_fail and payload["incomplete"]:
        reasons = "; ".join(payload.get("incomplete_reasons") or [])
        parts.append(f"scan incomplete: {reasons}" if reasons else "scan incomplete")
    return "; ".join(parts)


def _scan_gate(payload: dict, fail_on_fail: bool) -> None:
    """--fail-on-fail: exit 1 on a failed claim or an incomplete scan, saying on stderr what the scan missed."""
    if not fail_on_fail or not (payload["counts"]["fail"] or payload["incomplete"]):
        return
    if payload["incomplete"]:
        print(f"✗ scan incomplete: {'; '.join(payload['incomplete_reasons'])}", file=sys.stderr)
    sys.exit(1)
