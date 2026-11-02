# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The reach conformance suite (docs/reach-matrix.md): fixture repositories, what each harness's vendor
documentation says they load, and the matrix of what each harness run observed.

A run is one harness at one version and model over one fixture: ``repo reach --format receipt`` writes its
effective-instructions receipt, and each source's observed state is compared with the documented one. A
harness that cannot run here (no model, credential or CLI) gets a ``skipped`` run and no cells.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping, Sequence

import yaml

from dokimi import process
from dokimi.artifacts import load_artifact, save_artifact, schema_errors

SCHEMA_EXPECTATIONS = "reach_conformance.v1"
RECEIPT_NAME = "effective_instructions.json"
UNKNOWN = "unknown"
RUN_STATUSES = ("ran", "errors", "failed", "skipped")
AGREEMENTS = ("agrees", "disagrees", "inconclusive", "no_claim", "alias")
DEFAULT_TIMEOUT_S = 300
DEFAULT_BUDGET_USD = 0.10


class ConformanceError(RuntimeError):
    """An expectations file, a matrix or a merge that cannot be used."""


@dataclass(frozen=True)
class Harness:
    """A harness the suite knows: its CLI, and the variables one of which must be set for a run."""

    name: str
    bin: str
    credentials: tuple[str, ...]


# Credentials are the names the profile forwards (repos/profiles/*.yaml env_allow), so a key set here is one the run gets.
HARNESSES: dict[str, Harness] = {h.name: h for h in (
    Harness("claude-code", "claude", ("ANTHROPIC_API_KEY", "AWS_BEARER_TOKEN_BEDROCK")),
    Harness("codex", "codex", ("CODEX_API_KEY",)),
    Harness("copilot", "copilot", ("COPILOT_GITHUB_TOKEN",)),
)}


def unavailable(harness: Harness, model: str, env: Mapping[str, str],
                which: Callable[[str], str | None] = shutil.which) -> str | None:
    """Why ``harness`` cannot run here, or None when it can."""
    if not model:
        return "no model configured for this harness"
    if not any(env.get(name) for name in harness.credentials):
        return f"none of {', '.join(harness.credentials)} is set"
    if which(harness.bin) is None:
        return f"{harness.bin} is not on PATH"
    return None


def cases(fixtures: Path) -> list[str]:
    """The fixture repositories: the directories under ``fixtures``, by name."""
    return sorted(p.name for p in fixtures.iterdir() if p.is_dir())


def load_expectations(path: Path) -> dict[str, Any]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, yaml.YAMLError) as exc:
        raise ConformanceError(f"{path}: {exc}") from exc
    errors = schema_errors(SCHEMA_EXPECTATIONS, data)
    if errors:
        raise ConformanceError(f"{path}: " + "; ".join(errors))
    return data


def expectations_digest(expect: Mapping[str, Any]) -> str:
    """The sha256 of the expectations as canonical JSON: matrices built from different documented states differ in it."""
    text = json.dumps(expect, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def check_expectations(expect: Mapping[str, Any], fixtures: Path) -> list[str]:
    """What makes the expectations disagree with the fixture tree; empty when they agree."""
    declared, found = set(expect["cases"]), set(cases(fixtures))
    problems = [f"no expectations for fixture {c}" for c in sorted(found - declared)]
    problems += [f"expectations name no fixture {c}" for c in sorted(declared - found)]
    for case in sorted(declared & found):
        problems += _case_problems(case, expect["cases"][case], fixtures / case)
    return problems


def _case_problems(case: str, block: Mapping[str, Any], root: Path) -> list[str]:
    out = []
    for harness, body in block.items():
        if harness == "summary":
            continue
        for path, states in body["files"].items():
            if not (root / path).is_file() and not (root / path).is_symlink():
                out.append(f"{case}: {harness} names {path}, which the fixture does not hold")
            for scenario in states:
                kind, _, where = scenario.partition("=")
                if kind not in ("cwd", "read") or not where or not (root / where).is_dir():
                    out.append(f"{case}: {harness} names scenario {scenario}, which the fixture does not have")
    return out


def documented(expect: Mapping[str, Any], case: str, harness: str, path: str, scenario: str) -> str:
    """The state the harness's vendor documentation gives this file in this scenario; unknown when it says nothing."""
    block = expect["cases"].get(case, {}).get(harness)
    if not block:
        return UNKNOWN
    return block["files"].get(path, {}).get(scenario, UNKNOWN)


def scenario_id(run: Mapping[str, Any]) -> str:
    """The reach scenario id of one receipt run: ``read=<dir>`` for a run told to read a file, else ``cwd=<dir>``."""
    if run.get("target"):
        return "read=" + str(PurePosixPath(run["target"]).parent)
    return "cwd=" + (run.get("cwd") or ".")


def agreement(observed: str, documented_state: str, alias: bool) -> str:
    if alias:
        return "alias"
    if documented_state == UNKNOWN:
        return "no_claim"
    if observed in (UNKNOWN, "fetched"):
        return "inconclusive"
    return "agrees" if observed == documented_state else "disagrees"


def cells_of(receipt: Mapping[str, Any], case: str, expect: Mapping[str, Any]) -> list[dict[str, Any]]:
    """One cell per source per scenario of one receipt (an effective-instructions receipt of one fixture)."""
    harness, version = receipt["runtime"]["name"], receipt["runtime"]["version"]
    out = []
    for run in receipt["receipts"]:
        scenario = scenario_id(run)
        for src in run["sources"]:
            alias = src.get("alias_of")
            state = documented(expect, case, harness, src["path"], scenario)
            cell = {"harness": harness, "version": version, "case": case, "scenario": scenario,
                    "path": src["path"], "observed": src["observed"], "documented": state,
                    "agreement": agreement(src["observed"], state, alias is not None)}
            if alias:
                cell["alias_of"] = alias
            out.append(cell)
    return out


def run_record(harness: str, case: str, status: str, reason: str | None, version: str, model: str,
               scenarios: int) -> dict[str, Any]:
    return {"harness": harness, "version": version, "model": model, "case": case, "status": status,
            "reason": reason, "scenarios": scenarios}


def summary(runs: Sequence[Mapping[str, Any]], cells: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    counts = Counter(c["agreement"] for c in cells)
    status = Counter(r["status"] for r in runs)
    return {**{a: counts.get(a, 0) for a in AGREEMENTS}, "runs": {s: status.get(s, 0) for s in RUN_STATUSES}}


def build_matrix(runs: Sequence[Mapping[str, Any]], cells: Sequence[Mapping[str, Any]], digest: str,
                 generated_at: str | None = None) -> dict[str, Any]:
    """The ``reach_matrix.v1`` payload: runs and cells in a stable order, with the counts."""
    return {
        "schema_version": "reach_matrix.v1",
        "generated_at": generated_at or datetime.now(timezone.utc).isoformat(),
        "expectations": SCHEMA_EXPECTATIONS,
        "expectations_digest": digest,
        "runs": sorted((dict(r) for r in runs), key=lambda r: (r["case"], r["harness"], r["version"])),
        "cells": sorted((dict(c) for c in cells),
                        key=lambda c: (c["case"], c["harness"], c["version"], c["path"], c["scenario"])),
        "summary": summary(runs, cells),
    }


def _reason(proc: process.Completed) -> str:
    if proc.timed_out:
        return "timed out"
    tail = proc.stderr_bytes.decode("utf-8", "replace").strip()[-300:]
    return f"exit {proc.returncode}" + (f": {tail}" if tail else "")


def run_case(harness: Harness, model: str, fixture: Path, guidance: Path, out: Path,
             expect: Mapping[str, Any], *, timeout: int, budget: float) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Run ``repo reach`` over one fixture; its run record and cells. The receipt is read from ``out``."""
    case = fixture.name
    shutil.rmtree(out, ignore_errors=True)  # a receipt left by an earlier run is not this run's
    argv = [sys.executable, "-m", "dokimi", "repo", "reach", str(fixture), "--manifest-file", str(guidance),
            "--harness", harness.name, "--model", model, "--out", str(out), "--format", "receipt",
            "--timeout", str(timeout), "--budget", f"{budget:g}"]
    proc = process.run(argv, timeout=timeout * 16 + 300, env=process.Env.inherit())
    receipt_path = out / RECEIPT_NAME
    if proc.timed_out or not receipt_path.is_file():
        return run_record(harness.name, case, "failed", _reason(proc), UNKNOWN, model, 0), []
    try:
        receipt = load_artifact("effective_instructions", receipt_path)
    except (OSError, ValueError) as exc:  # one bad receipt fails its run, not the whole harness
        return run_record(harness.name, case, "failed", f"invalid receipt: {exc}", UNKNOWN, model, 0), []
    status = "ran" if proc.returncode == 0 else "errors"
    reason = None if status == "ran" else _reason(proc)
    record = run_record(harness.name, case, status, reason, receipt["runtime"]["version"], model,
                        len(receipt["receipts"]))
    return record, cells_of(receipt, case, expect)


def run_harness(harness: Harness, model: str, fixtures: Path, expect: Mapping[str, Any], out_dir: Path, *,
                timeout: int = DEFAULT_TIMEOUT_S, budget: float = DEFAULT_BUDGET_USD,
                env: Mapping[str, str], which: Callable[[str], str | None] = shutil.which) -> dict[str, Any]:
    """One harness over every fixture, as a matrix. A harness that cannot run here gets a skipped run per fixture.

    ``env`` only decides availability; each run inherits the process environment."""
    reason = unavailable(harness, model, env, which)
    guidance = fixtures / "guidance.yaml"
    runs: list[dict[str, Any]] = []
    cells: list[dict[str, Any]] = []
    for case in cases(fixtures):
        if reason:
            runs.append(run_record(harness.name, case, "skipped", reason, UNKNOWN, model, 0))
            continue
        record, found = run_case(harness, model, fixtures / case, guidance, out_dir / case, expect,
                                 timeout=timeout, budget=budget)
        runs.append(record)
        cells += found
    return build_matrix(runs, cells, expectations_digest(expect))


def merge(matrices: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """One matrix from several: the runs and cells of each, and no run of the same known harness version twice.

    The matrices must come from one revision of the expectations, else their documented states differ."""
    if not matrices:
        raise ConformanceError("nothing to merge")
    digests = sorted({m["expectations_digest"] for m in matrices})
    if len(digests) > 1:
        raise ConformanceError(f"the matrices come from {len(digests)} revisions of the expectations: "
                               + ", ".join(d[:12] for d in digests))
    runs: list[dict[str, Any]] = []
    cells: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for matrix in matrices:
        for run in matrix["runs"]:
            if run["status"] in ("failed", "skipped"):  # no receipt, so no cells to count twice and no version to key on
                continue
            key = (run["harness"], run["version"], run["case"])
            if key in seen:
                raise ConformanceError(f"two matrices hold {run['harness']} {run['version']} on {run['case']}")
            seen.add(key)
        runs += matrix["runs"]
        cells += matrix["cells"]
    return build_matrix(runs, cells, digests[0], generated_at=max(m["generated_at"] for m in matrices))


def write_matrix(matrix: Mapping[str, Any], path: Path) -> None:
    save_artifact("reach_matrix", dict(matrix), path)


def _table_text(text: str) -> str:
    """One table cell: a stderr reason can run over several lines, and a pipe would end the cell early."""
    return " ".join(text.split()).replace("|", "\\|")


def _cell_text(cell: Mapping[str, Any] | None) -> str:
    if cell is None:
        return "-"
    text = cell["observed"]
    if cell["agreement"] == "alias":
        text = f"link, {text}"
    elif cell["agreement"] == "disagrees":
        text += f" (docs: {cell['documented']})"
    return _table_text(text)


def render_markdown(matrix: Mapping[str, Any], summaries: Mapping[str, str] | None = None) -> str:
    """The matrix as a markdown page: the runs, then one table per fixture with a column per harness version."""
    summaries = summaries or {}
    counts = matrix["summary"]
    columns = sorted({(r["harness"], r["version"]) for r in matrix["runs"] if r["status"] not in ("skipped", "failed")})
    lines = [
        "# Reach matrix", "",
        f"Generated {matrix['generated_at']}. Observed states are what each run's model reported seeing "
        "(`repo reach`); documented states are the vendor documentation's, in `data/reach-conformance/expectations.yaml`.",
        "",
        f"Cells: {counts['agrees']} agree with the documentation, {counts['disagrees']} disagree, "
        f"{counts['inconclusive']} inconclusive, {counts['no_claim']} with no documented claim, "
        f"{counts['alias']} links. Runs: {counts['runs']['ran']} ran, {counts['runs']['errors']} with errors, "
        f"{counts['runs']['failed']} failed, {counts['runs']['skipped']} skipped.", "",
        "## Runs", "", "| fixture | harness | version | model | status | reason |", "|---|---|---|---|---|---|",
    ]
    lines += [f"| {r['case']} | {r['harness']} | {r['version']} | {r['model'] or '-'} | {r['status']} | "
              f"{_table_text(r['reason'] or '-')} |" for r in matrix["runs"]]
    lines += ["", "Cell legend: `loaded` or `not_loaded` as observed; `fetched` is read with a tool first; "
              "`unknown` is no usable answer. `(docs: X)` marks a disagreement with the documentation; a "
              "cell with no `(docs: ...)` agrees, or the documentation makes no claim.", ""]
    for case in sorted({c["case"] for c in matrix["cells"]}):
        lines += _case_section(matrix, case, columns, summaries.get(case, ""))
    return "\n".join(lines).rstrip() + "\n"


def _case_section(matrix: Mapping[str, Any], case: str, columns: list[tuple[str, str]], summary_text: str) -> list[str]:
    cells = [c for c in matrix["cells"] if c["case"] == case]
    index = {(c["harness"], c["version"], c["path"], c["scenario"]): c for c in cells}
    rows = sorted({(c["path"], c["scenario"]) for c in cells})
    header = ["file", "scenario"] + [f"{h} {v}" for h, v in columns]
    out = [f"## {case}", ""] + ([summary_text, ""] if summary_text else [])
    out += ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    for path, scenario in rows:
        texts = [_cell_text(index.get((h, v, path, scenario))) for h, v in columns]
        out.append("| " + " | ".join([path, scenario, *texts]) + " |")
    return out + [""]
