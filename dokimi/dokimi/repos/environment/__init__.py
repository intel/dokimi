# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""``repo inspect``: what a repository offers an agent and what may stop or endanger it.

Static, no LLM call, no command of the repository run. Observations (instruction files,
check surfaces, capability surfaces, risky scripts, file inventory) are kept apart from the
findings drawn from them; a finding carries a severity and, separately, a confidence. Stage
results say "no known blocker", never "ready": what the tree cannot show (CI secrets, the
network, whether a command passes) is listed under ``not_observed`` instead of passing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from dokimi.repos.l0.core import Scan
from dokimi.repos.source import RepoTree, SourceError

from .checks import KINDS, detect_checks
from .files import LARGE_BYTES, inventory, sizes
from .instructions import LARGE_INSTRUCTION_BYTES, detect_instructions
from .instructions import RULES as INSTRUCTION_RULES
from .risks import detect_capabilities, detect_hook_risks, detect_script_signals

# Only stages a warning or an error can reach: a stage no rule can affect would always read
# "no known blocker". Review and merge are not judged from the tree (see ``NOT_OBSERVED``).
STAGES = ("orient", "bootstrap", "navigate", "edit", "verify", "deploy")
SEVERITY_RANK = {"info": 1, "warning": 2, "error": 3}


@dataclass(frozen=True)
class RuleInfo:
    severity: str
    stages: tuple[str, ...]
    summary: str


RULES: dict[str, RuleInfo] = {
    "instructions.missing": RuleInfo("warning", ("orient", "navigate"),
                                     "no instruction file any harness loads at the root"),
    "instructions.local-private": RuleInfo("warning", ("orient",),
                                           "a personal instruction file is committed; others' sessions differ"),
    "instructions.activation-unknown": RuleInfo("info", ("orient",),
                                                "when the harness loads the file depends on its frontmatter"),
    "instructions.large": RuleInfo("warning", ("orient",), "instruction file large enough to crowd the context"),
    "checks.test.missing": RuleInfo("error", ("verify",), "no test files and no way to run tests found"),
    "checks.test.runner-unknown": RuleInfo("warning", ("verify",), "test files found but no runner is known"),
    "checks.test.toolchain-only": RuleInfo("info", ("verify",),
                                           "tests only through the toolchain default, and no test file found"),
    "checks.lint.missing": RuleInfo("warning", ("verify",), "no lint or format check found"),
    "checks.typecheck.missing": RuleInfo("info", ("verify",), "no type check found"),
    "safety.install-hook": RuleInfo("info", ("bootstrap",), "installing runs a lifecycle script"),
    "safety.destructive": RuleInfo("warning", ("edit", "verify"), "a script deletes the root, home, a glob or the whole tree"),
    "safety.network-exec": RuleInfo("warning", ("bootstrap", "edit"), "a script runs code it downloads"),
    "safety.deploy": RuleInfo("warning", ("deploy",), "a script publishes or deploys"),
    "safety.agent-hook.executes-repository-code": RuleInfo(
        "warning", ("bootstrap",),
        "a hook the harness runs on its own runs repository code: an install with lifecycle scripts on, "
        "a package script, make, or a file in the tree"),
    "safety.capability.high-risk": RuleInfo("info", ("edit",),
                                            "the harness gets tools or hooks from the repository"),
    "files.large": RuleInfo("info", ("navigate", "edit"), "a file large enough that reading it costs much context"),
    "files.minified": RuleInfo("warning", ("navigate", "edit"), "a minified file: unreadable and not to be edited"),
}

NOT_OBSERVED = (
    "whether the commands found run and pass",
    "CI secrets, runners and required checks",
    "network access and credentials in the agent's environment",
    "which instruction files a given harness version loads (measure with `repo reach`)",
    "how a change is reviewed and merged (branch protection, required reviews)",
)


def _finding(rule: str, why: str, path: str | None = None, confidence: str = "high",
             key: str | None = None) -> dict[str, Any]:
    info = RULES[rule]
    return {"id": f"{rule}:{key or path}" if (key or path) else rule, "rule": rule, "severity": info.severity,
            "confidence": confidence, "stages": list(info.stages), "path": path, "why": why}


def _read_sizes(tree: RepoTree, files: list[str], size_of: dict[str, int] | None) -> dict[str, int] | None:
    """``size_of`` with a linked instruction file sized by the file it names: the harness reads that."""
    if size_of is None:
        return None
    out = dict(size_of)
    for path in files:
        if not any(rule.match(path) for rule in INSTRUCTION_RULES):
            continue
        try:
            target = tree.resolve(path)
        except SourceError:
            continue
        if target != path and target in size_of:
            out[path] = size_of[target]
    return out


def inspect_repo(tree: RepoTree, large: int = LARGE_BYTES) -> dict[str, Any]:
    """The ``repo_environment.v1`` payload (without ``schema_version``) for ``tree``."""
    scan = Scan(tree, frozenset())
    size_of = sizes(tree)
    instructions = detect_instructions(scan.files, _read_sizes(tree, scan.files, size_of))
    checks = detect_checks(scan)
    capabilities = detect_capabilities(scan)
    scripts = detect_script_signals(scan)
    hooks = detect_hook_risks(scan)
    files = inventory(scan.files, size_of, large)

    findings: list[dict[str, Any]] = []
    if not any(i.scope == "root" for i in instructions):
        findings.append(_finding("instructions.missing", "no AGENTS.md, CLAUDE.md, GEMINI.md, "
                                 ".github/copilot-instructions.md or other root instruction file"))
    seen: set[tuple[str, str]] = set()  # a file that matches two rules has two records

    def once(rule: str, path: str) -> bool:
        if (rule, path) in seen:
            return False
        seen.add((rule, path))
        return True

    for i in instructions:
        if i.local_private and once("instructions.local-private", i.path):
            findings.append(_finding("instructions.local-private", f"{i.path} is in the tree", i.path))
        if i.activation == "unknown" and once("instructions.activation-unknown", i.path):
            findings.append(_finding("instructions.activation-unknown", "; ".join(i.notes) or
                                     f"{', '.join(i.harnesses)} decide when to load it", i.path, "medium"))
        if (i.size_bytes is not None and i.size_bytes > LARGE_INSTRUCTION_BYTES
                and once("instructions.large", i.path)):
            findings.append(_finding("instructions.large", f"{i.size_bytes} bytes", i.path))

    by_kind = {k: [c for c in checks if c.kind == k] for k in KINDS}
    if not by_kind["test"]:
        if files["test_files"] > 0:
            findings.append(_finding("checks.test.runner-unknown", f"{files['test_files']} test files found but no runner is known",
                                     confidence="low"))
        else:
            findings.append(_finding("checks.test.missing", "no test files and no way to run tests found",
                                     confidence="medium"))
    elif all(c.basis == "toolchain" for c in by_kind["test"]) and not files["test_files"]:
        tools = ", ".join(sorted({c.ecosystem for c in by_kind["test"]}))
        findings.append(_finding("checks.test.toolchain-only", f"{tools} can run tests, but no test file was found",
                                 confidence="low"))
    if not by_kind["lint"]:
        findings.append(_finding("checks.lint.missing", "no lint script, target, config or pre-commit hook found",
                                 confidence="medium"))
    typed = {"node", "python"} & {c.ecosystem for c in checks}  # where type checking is a separate step
    if typed and not by_kind["typecheck"]:
        findings.append(_finding("checks.typecheck.missing", f"{', '.join(sorted(typed))} code, no type-check script or config found",
                                 confidence="low"))

    for s in scripts:
        findings.append(_finding(f"safety.{s.category}", s.command, "package.json", key=f"{s.source}"))
    for h in hooks:
        findings.append(_finding("safety.agent-hook.executes-repository-code",
                                 f"{h.event} hook runs `{h.command}`", h.path, key=f"{h.path}:{h.event}:{h.command}"))
    for c in capabilities:
        if c.risk == "high":
            findings.append(_finding("safety.capability.high-risk",
                                     f"{c.kind} for {c.harness}" + (f": {c.note}" if c.note else ""),
                                     c.path, "medium", key=f"{c.path}:{c.note}" if c.note else None))
    for f in files["large"]:
        findings.append(_finding("files.large", f"{f['bytes']} bytes", f["path"]))
    for path in files["minified"]:
        findings.append(_finding("files.minified", "minified bundle", path))
    findings.sort(key=lambda f: (-SEVERITY_RANK[f["severity"]], f["rule"], f["path"] or "", f["id"]))

    stages = []
    for stage in STAGES:
        hit = [f for f in findings if stage in f["stages"] and f["severity"] != "info"]
        status = ("blocked" if any(f["severity"] == "error" for f in hit) else
                  "attention" if hit else "no_known_blocker")
        stages.append({"stage": stage, "status": status, "findings": [f["id"] for f in hit]})

    risk = ("high" if hooks or any(c.risk == "high" for c in capabilities) else
            "medium" if scripts or any(c.risk == "medium" for c in capabilities) else "low")
    not_observed = list(NOT_OBSERVED)
    if size_of is None:
        not_observed.append("file sizes (a partial clone: listing them would fetch every blob)")
    return {
        "repo": {"tree": tree.label, "sha": getattr(tree, "sha", None)},
        "instructions": [i.to_dict() for i in instructions],
        "checks": [c.to_dict() for c in checks],
        "capabilities": [c.to_dict() for c in capabilities],
        "scripts": [s.to_dict() for s in scripts],
        "hook_risks": [h.to_dict() for h in hooks],
        "files": files,
        "findings": findings,
        "counts": {s: sum(f["severity"] == s for f in findings) for s in ("error", "warning", "info")},
        "stages": stages,
        "risk": risk,
        "not_observed": not_observed,
    }

