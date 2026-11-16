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
import os
import shutil
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Collection, Mapping, Sequence

import yaml

from dokimi import process
from dokimi.artifacts import load_artifact, save_artifact, schema_errors
from dokimi.repos.reach import PROSE_LIMIT

SCHEMA_EXPECTATIONS = "reach_conformance.v1"
RECEIPT_NAME = "effective_instructions.json"
UNKNOWN = "unknown"
RUN_STATUSES = ("ran", "errors", "failed", "skipped")
AGREEMENTS = ("agrees", "disagrees", "inconclusive", "no_claim", "alias")
DEFAULT_TIMEOUT_S = 300
DEFAULT_BUDGET_USD = 0.10
# The guidance names data/reach-conformance/guidance.yaml globs, so every harness probes them in each fixture.
MANIFEST_NAMES = ("AGENTS.md", "AGENTS.override.md", "CLAUDE.md", "CLAUDE.local.md")


class ConformanceError(RuntimeError):
    """An expectations file, a matrix or a merge that cannot be used."""


@dataclass(frozen=True)
class Harness:
    """A harness the suite knows: its CLI, the variables one of which must be set for a run, and the profile
    ``repo reach`` runs it with (a built-in name, or a profile YAML path; the harness name when ``profile`` is empty)."""

    name: str
    bin: str
    credentials: tuple[str, ...]
    profile: str = ""

    @property
    def profile_ref(self) -> str:
        return self.profile or self.name


# The codex profile with a provider that reads CODEX_API_KEY: the built-in profile sends no bearer (see the file).
# Package data, so an installed wheel finds it next to this module.
CODEX_PROFILE = Path(__file__).resolve().parent / "conformance_profiles" / "codex.yaml"

# Credentials are the names the profile forwards (repos/profiles/*.yaml env_allow), so a key set here is one the run gets.
HARNESSES: dict[str, Harness] = {h.name: h for h in (
    Harness("claude-code", "claude", ("ANTHROPIC_API_KEY", "AWS_BEARER_TOKEN_BEDROCK")),
    Harness("codex", "codex", ("CODEX_API_KEY",), profile=str(CODEX_PROFILE)),
    Harness("copilot", "copilot", ("COPILOT_GITHUB_TOKEN",)),
)}


def _which_on(cmd: str, search: str) -> str | None:
    """``cmd`` on ``search``, a PATH value: the search a run's child process makes, not this process's."""
    return shutil.which(cmd, path=search)


def unavailable(harness: Harness, model: str, env: Mapping[str, str],
                which: Callable[[str, str], str | None] = _which_on) -> str | None:
    """Why ``harness`` cannot run here, or None when it can. The CLI is looked up on the PATH of ``env``."""
    if not model:
        return "no model configured for this harness"
    if not any(env.get(name) for name in harness.credentials):
        return f"none of {', '.join(harness.credentials)} is set"
    if which(harness.bin, env.get("PATH", os.defpath)) is None:
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
    return hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()


def fixtures_digest(fixtures: Path) -> str:
    """The sha256 of everything under the fixtures root (each file's path and bytes, a link's target), including its guidance file and, by default, its expectations file."""
    h = hashlib.sha256()
    for p in sorted(fixtures.rglob("*"), key=lambda p: p.relative_to(fixtures).as_posix()):
        if not (p.is_symlink() or p.is_file()):
            continue
        data = b"L" + os.fsencode(os.readlink(p)) if p.is_symlink() else b"F" + p.read_bytes()
        for part in (os.fsencode(p.relative_to(fixtures).as_posix()), data):
            h.update(len(part).to_bytes(8, "big") + part)
    return h.hexdigest()


def check_expectations(expect: Mapping[str, Any], fixtures: Path) -> list[str]:
    """What makes the expectations disagree with the fixture tree; empty when they agree."""
    declared, found = set(expect["cases"]), set(cases(fixtures))
    problems = [f"no expectations for fixture {c}" for c in sorted(found - declared)]
    problems += [f"expectations name no fixture {c}" for c in sorted(declared - found)]
    for case in sorted(declared & found):
        problems += _case_problems(case, expect["cases"][case], fixtures / case)
    return problems


def scenarios_of(root: Path) -> set[str]:
    """The scenario ids reach runs over one fixture: ``cwd=.``, and ``cwd=<dir>`` and ``read=<dir>`` for each
    directory below the root that holds a guidance file the conformance manifest globs."""
    dirs = {p.relative_to(root).parent.as_posix() for p in root.rglob("*") if p.name in MANIFEST_NAMES}
    dirs.discard(".")
    return {"cwd=."} | {f"cwd={d}" for d in dirs} | {f"read={d}" for d in dirs}


def _case_problems(case: str, block: Mapping[str, Any], root: Path) -> list[str]:
    out = []
    produced = scenarios_of(root)
    for harness, body in block.items():
        if harness == "summary":
            continue
        if harness not in HARNESSES:
            out.append(f"{case}: {harness} is not a harness")
        for path, states in body["files"].items():
            if not (root / path).is_file() and not (root / path).is_symlink():
                out.append(f"{case}: {harness} names {path}, which the fixture does not hold")
            for scenario in states:
                if scenario not in produced:
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


def cells_of(receipt: Mapping[str, Any], case: str, expect: Mapping[str, Any],
             prose: Collection[str] = ()) -> list[dict[str, Any]]:
    """One cell per source per scenario of one receipt (an effective-instructions receipt of one fixture).
    A ``not_loaded`` in a scenario of ``prose`` (it answered in prose, so it may under-report) is observed unknown."""
    harness, version = receipt["runtime"]["name"], receipt["runtime"]["version"]
    out = []
    for run in receipt["receipts"]:
        scenario = scenario_id(run)
        for src in run["sources"]:
            alias = src.get("alias_of")
            state = documented(expect, case, harness, src["path"], scenario)
            observed = UNKNOWN if scenario in prose and src["observed"] == "not_loaded" else src["observed"]
            cell = {"harness": harness, "version": version, "case": case, "scenario": scenario,
                    "path": src["path"], "observed": observed, "documented": state,
                    "agreement": agreement(observed, state, alias is not None)}
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
                 fixtures: str, generated_at: str | None = None) -> dict[str, Any]:
    """The ``reach_matrix.v1`` payload: runs and cells in a stable order, with the counts."""
    return {
        "schema_version": "reach_matrix.v1",
        "generated_at": generated_at or datetime.now(timezone.utc).isoformat(),
        "expectations": SCHEMA_EXPECTATIONS,
        "expectations_digest": digest,
        "fixtures_digest": fixtures,
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


def _scenario_rows(out: Path) -> list[dict[str, Any]]:
    """The scenario rows of the run's ``reach.json``; none when it is missing or unreadable."""
    try:
        rows = json.loads((out / "reach.json").read_text(encoding="utf-8"))["scenarios"]
    except (OSError, ValueError, KeyError, TypeError):
        return []
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def run_case(harness: Harness, model: str, fixture: Path, guidance: Path, out: Path,
             expect: Mapping[str, Any], *, timeout: int, budget: float,
             env: Mapping[str, str]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Run ``repo reach`` over one fixture with exactly ``env`` as its environment; its run record and cells.
    The receipt is read from ``out``."""
    case = fixture.name
    try:
        shutil.rmtree(out)  # a receipt left by an earlier run is not this run's
    except FileNotFoundError:
        pass
    except OSError as exc:  # a receipt that stays would be read as this run's: fail the run instead
        return run_record(harness.name, case, "failed", f"cannot clear {out}: {exc}", UNKNOWN, model, 0), []
    argv = [sys.executable, "-m", "dokimi", "repo", "reach", str(fixture), "--manifest-file", str(guidance),
            "--harness", harness.profile_ref, "--model", model, "--out-dir", str(out), "--format", "receipt",
            "--timeout", str(timeout), "--budget", f"{budget:g}"]
    proc = process.run(argv, timeout=timeout * 16 + 300, env=process.Env.only((), env))
    receipt_path = out / RECEIPT_NAME
    if proc.timed_out or not receipt_path.is_file():
        return run_record(harness.name, case, "failed", _reason(proc), UNKNOWN, model, 0), []
    try:
        receipt = load_artifact("effective_instructions", receipt_path)
    except (OSError, ValueError) as exc:  # one bad receipt fails its run, not the whole harness
        return run_record(harness.name, case, "failed", f"invalid receipt: {exc}", UNKNOWN, model, 0), []
    rows = _scenario_rows(out)
    errors = [str(r["error"]) for r in rows if r.get("error")]
    if rows and len(errors) == len(rows):  # no scenario answered (a revoked key, a hung CLI): nothing was observed
        return run_record(harness.name, case, "failed", errors[0], receipt["runtime"]["version"], model, 0), []
    status = "ran" if proc.returncode == 0 else "errors"
    reason = None if status == "ran" else (errors[0] if errors else _reason(proc))
    record = run_record(harness.name, case, status, reason, receipt["runtime"]["version"], model,
                        len(receipt["receipts"]))
    prose = {str(r.get("id")) for r in rows if (r.get("prose") or 0) > PROSE_LIMIT}
    return record, cells_of(receipt, case, expect, prose)


def check_out_dir(out: Path, fixtures: Path) -> None:
    """Refuse an output directory that is, holds or sits in the fixtures: a run clears ``<out>/<case>`` first."""
    a, b = out.resolve(), fixtures.resolve()
    if a == b or a.is_relative_to(b) or b.is_relative_to(a):
        raise ConformanceError(f"{out} and the fixtures {fixtures} overlap: a run would clear a fixture's directory")


def run_harness(harness: Harness, model: str, fixtures: Path, expect: Mapping[str, Any], out_dir: Path, *,
                timeout: int = DEFAULT_TIMEOUT_S, budget: float = DEFAULT_BUDGET_USD,
                env: Mapping[str, str], which: Callable[[str, str], str | None] = _which_on) -> dict[str, Any]:
    """One harness over every fixture, as a matrix. A harness that cannot run here gets a skipped run per fixture.

    ``env`` decides availability and is the whole environment each run gets (nothing else is inherited)."""
    check_out_dir(out_dir, fixtures)
    reason = unavailable(harness, model, env, which)
    guidance = fixtures / "guidance.yaml"
    runs: list[dict[str, Any]] = []
    cells: list[dict[str, Any]] = []
    for case in cases(fixtures):
        if reason:
            runs.append(run_record(harness.name, case, "skipped", reason, UNKNOWN, model, 0))
            continue
        record, found = run_case(harness, model, fixtures / case, guidance, out_dir / case, expect,
                                 timeout=timeout, budget=budget, env=env)
        runs.append(record)
        cells += found
    return build_matrix(runs, cells, expectations_digest(expect), fixtures_digest(fixtures))


def merge(matrices: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """One matrix from several: the runs and cells of each, and no run of the same known harness version twice.

    The matrices must come from one revision of the expectations and of the fixtures, else their states differ."""
    if not matrices:
        raise ConformanceError("nothing to merge")
    digests = sorted({m["expectations_digest"] for m in matrices})
    if len(digests) > 1:
        raise ConformanceError(f"the matrices come from {len(digests)} revisions of the expectations: "
                               + ", ".join(d[:12] for d in digests))
    trees = sorted({m["fixtures_digest"] for m in matrices})
    if len(trees) > 1:
        raise ConformanceError(f"the matrices come from {len(trees)} fixture trees: " + ", ".join(d[:12] for d in trees))
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
    latest = max((m["generated_at"] for m in matrices), key=_instant)
    return build_matrix(runs, cells, digests[0], trees[0], generated_at=latest)


def _instant(text: str) -> datetime:
    """The moment a ``generated_at`` names: offsets differ between matrices, so text does not order them."""
    try:
        when = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ConformanceError(f"generated_at {text!r} is not an ISO 8601 timestamp") from exc
    if when.tzinfo is None:
        raise ConformanceError(f"generated_at {text!r} has no time zone")
    return when


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
    # every dynamic table field goes through _table_text: a pipe or newline in any of them breaks the row
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
    lines += [f"| {_table_text(r['case'])} | {_table_text(r['harness'])} | {_table_text(r['version'])} | "
              f"{_table_text(r['model'] or '-')} | {_table_text(r['status'])} | {_table_text(r['reason'] or '-')} |"
              for r in matrix["runs"]]
    lines += ["", "Cell legend: `loaded` or `not_loaded` as observed; `fetched` is read with a tool first; "
              "`unknown` is no usable answer. `(docs: X)` marks a disagreement with the documentation; `link, X` "
              "marks a symlink; `inconclusive` is an observed `unknown` or `fetched` where the documentation names a "
              "state; a cell with none of these agrees, or the documentation makes no claim.", ""]
    for case in sorted({c["case"] for c in matrix["cells"]}):
        lines += _case_section(matrix, case, columns, summaries.get(case, ""))
    return "\n".join(lines).rstrip() + "\n"


def _case_section(matrix: Mapping[str, Any], case: str, columns: list[tuple[str, str]], summary_text: str) -> list[str]:
    cells = [c for c in matrix["cells"] if c["case"] == case]
    index = {(c["harness"], c["version"], c["path"], c["scenario"]): c for c in cells}
    rows = sorted({(c["path"], c["scenario"]) for c in cells})
    header = ["file", "scenario"] + [_table_text(f"{h} {v}") for h, v in columns]
    out = [f"## {case}", ""] + ([summary_text, ""] if summary_text else [])
    out += ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    for path, scenario in rows:
        texts = [_cell_text(index.get((h, v, path, scenario))) for h, v in columns]
        out.append("| " + " | ".join([_table_text(path), _table_text(scenario), *texts]) + " |")
    return out + [""]
