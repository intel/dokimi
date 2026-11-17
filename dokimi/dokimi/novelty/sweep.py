# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The corpus novelty sweep's bookkeeping: one JSONL row per skill, resume, strata and the summary.

No model call and no CLI here: :mod:`dokimi.commands.catalog_novelty` runs the paid
``analyze`` per skill and writes the rows this module builds. The per-skill measure is the
share the model did not already know, ``1 - k0.lenient`` (the lenient K0 of the novelty
artifact, the ``new`` column of the S2/S3 summaries); a skill's CI is the bootstrap of that
share over the skills of a stratum (:func:`dokimi.eval.stats.bootstrap_ci`, the one resampler).
"""

from __future__ import annotations

import csv
import json
import statistics
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, Optional

from dokimi.utils import InputError

#: The statuses a row can have. Only ``ok`` rows are measured and kept by a resumed run.
STATUSES = ("ok", "partial", "error")
#: The stratum of a skill the strata file does not name.
UNASSIGNED = "(unassigned)"
#: The stratum name when no strata file is given.
ALL = "all"
#: Bootstrap resamples of a stratum interval; fixed so two summaries of one JSONL agree.
RESAMPLES = 2000


def new_share(known: Optional[float]) -> Optional[float]:
    """The share of the skill's content the model did not already know: ``1 - known``."""
    return None if known is None else round(1.0 - known, 6)


def measured_fields(artifact: dict) -> dict[str, Any]:
    """The per-skill numbers of a finished novelty artifact, read from its ``k0`` and ``paired`` blocks."""
    k0 = artifact.get("k0") or {}
    lenient = (artifact.get("paired") or {}).get("lenient") or {}
    known = k0.get("lenient")
    return {
        "tokens": (artifact.get("artifact") or {}).get("tokens"),
        "known_lenient": known,
        "new_lenient": new_share(known),
        "new_strict": new_share(k0.get("strict")),
        "n_paired": lenient.get("n_paired"),
    }


def make_row(*, key: str, name: str, path: str, skill_sha256: Optional[str], status: str,
             settings: dict, version: str, usd: float = 0.0, calls: int = 0,
             reason: Optional[str] = None, message: Optional[str] = None,
             measured: Optional[dict] = None, novelty_json: Optional[str] = None) -> dict:
    """One skill's row: its identity and digest, what the run paid, and its measure when it has one."""
    if status not in STATUSES:
        raise ValueError(f"unknown sweep status {status!r}")
    row: dict[str, Any] = {
        "key": key, "name": name, "path": path, "skill_sha256": skill_sha256, "status": status,
        "reason": reason, "message": message, "usd": round(usd, 6), "llm_calls": calls,
        "novelty_json": novelty_json, "settings": settings, "dokimi_version": version,
        "tokens": None, "known_lenient": None, "new_lenient": None, "new_strict": None,
        "n_paired": None,
    }
    if measured is not None:
        row.update(measured)
    return row


def load_strata(path: Path, column: str) -> dict[str, str]:
    """The skill -> stratum map of a CSV with a ``skill`` column and a *column* column.

    ``skill`` is the skill's key under the catalog root (its directory, relative, with ``/``) or
    its name; a skill is matched by key first, then by name. A skill named twice with two strata is an error.
    """
    if column == "skill":
        raise InputError("--stratum-column must not be `skill`: that column names the skill")
    try:
        with path.open(newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            fields = reader.fieldnames or []
            if "skill" not in fields or column not in fields:
                raise InputError(f"--strata {path} needs a `skill` column and a `{column}` column; "
                                 f"found {', '.join(fields) or 'none'}")
            out: dict[str, str] = {}
            for lineno, rec in enumerate(reader, start=2):
                skill = (rec.get("skill") or "").strip()
                stratum = (rec.get(column) or "").strip()
                if not skill or not stratum:
                    raise InputError(f"--strata {path} line {lineno}: `skill` and `{column}` must both be set")
                if out.get(skill, stratum) != stratum:
                    raise InputError(f"--strata {path} line {lineno}: {skill!r} is in two strata "
                                     f"({out[skill]!r} and {stratum!r})")
                out[skill] = stratum
    except (OSError, UnicodeDecodeError, csv.Error) as exc:
        raise InputError(f"cannot read --strata {path}: {exc}") from exc
    return out


def stratum_of(row: Mapping[str, Any], strata: Optional[Mapping[str, str]]) -> str:
    """The stratum of a row: ``all`` without a strata map, else the mapped stratum (by key, then name)."""
    if strata is None:
        return ALL
    return strata.get(row["key"]) or strata.get(row["name"]) or UNASSIGNED


def read_rows(path: Path) -> list[dict]:
    """The rows of a sweep JSONL file. A last line a killed run left half-written is dropped."""
    rows: list[dict] = []
    try:
        with path.open("rb") as fh:
            lines = fh.readlines()
    except FileNotFoundError:
        return rows
    except OSError as exc:
        raise InputError(f"cannot read --jsonl {path}: {exc}") from exc
    for lineno, raw in enumerate(lines, 1):
        if not raw.strip():
            continue
        try:
            row = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            if lineno == len(lines) and not raw.endswith(b"\n"):
                break
            raise InputError(f"--jsonl {path} line {lineno} is not a sweep row: {exc}") from exc
        if not isinstance(row, dict) or row.get("status") not in STATUSES or "key" not in row:
            raise InputError(f"--jsonl {path} line {lineno} is not a sweep row; fix or remove it before --resume")
        needed = ("usd", "new_lenient", "tokens") if row["status"] == "ok" else ("usd",)
        missing = [k for k in needed if k not in row]
        if missing:
            raise InputError(f"--jsonl {path} line {lineno} has no {', '.join(missing)}; fix or remove it before --resume")
        rows.append(row)
    return rows


def resumable(prior: Iterable[dict], selected: Mapping[str, Optional[str]], settings: dict,
              version: str) -> list[dict]:
    """The prior rows a resumed run keeps: measured, for a skill still selected with the same
    directory digest, under the same settings and dokimi version. Every other row is run again."""
    kept = []
    for row in prior:
        if (row["status"] == "ok" and row["key"] in selected and selected[row["key"]] is not None
                and row.get("skill_sha256") == selected[row["key"]]
                and row.get("settings") == settings and row.get("dokimi_version") == version):
            kept.append(row)
    return kept


def group_stats(rows: list[dict], *, resamples: int = RESAMPLES) -> dict[str, Any]:
    """The n, the new-share and already-known-share intervals (bootstrap), the spend and the median size of *rows*."""
    from dokimi.eval.stats import bootstrap_ci

    new = bootstrap_ci([r["new_lenient"] for r in rows], n_resamples=resamples)
    strict_values = [r["new_strict"] for r in rows if r.get("new_strict") is not None]
    strict = bootstrap_ci(strict_values, n_resamples=resamples) if strict_values else None
    known = {"mean": round(1 - new["mean"], 4), "lo": round(1 - new["hi"], 4),
             "hi": round(1 - new["lo"], 4)}
    tokens = [r["tokens"] for r in rows if r.get("tokens")]
    return {
        "n": len(rows),
        "new_lenient": new,
        "known_lenient": known,
        "new_strict": strict,
        "usd": round(sum(r["usd"] for r in rows), 6),
        "median_tokens": statistics.median(tokens) if tokens else None,
    }


#: How many skills the text summary lists in the ranking; the JSON summary has them all.
RANK_LINES = 10


def ranking(rows: list[dict]) -> list[dict]:
    """The measured skills with a token count, most new content per 1k tokens first, one entry per key.

    A skill measured by more than one sweep into the same JSONL is ranked from its latest ``ok`` row
    (the last in file order); the overall figures are not affected. ``new_per_1k_tokens`` is the
    skill's new share divided by its size in thousands of tokens: what a model learns from reading
    the skill, per token it must read. Ties keep the key order.
    """
    latest: dict[str, dict] = {}
    for r in rows:
        if r["status"] == "ok":
            latest[r["key"]] = r
    scored = [
        {"key": r["key"], "name": r["name"], "new_lenient": r["new_lenient"], "tokens": r["tokens"],
         "new_per_1k_tokens": round(r["new_lenient"] * 1000 / r["tokens"], 6)}
        for r in latest.values()
        if r["new_lenient"] is not None and r.get("tokens")
    ]
    scored.sort(key=lambda s: (-s["new_per_1k_tokens"], s["key"]))
    return scored


def summarize(rows: list[dict], strata: Optional[Mapping[str, str]] = None, *,
              resamples: int = RESAMPLES) -> dict[str, Any]:
    """Counts by status, the spend, and the measure with bootstrap CIs overall and per stratum.

    Only ``ok`` rows with a measure are summarised; ``partial`` and ``error`` rows are counted and
    their spend is in ``usd``. With *strata* the per-stratum table is given, else only ``overall``.
    ``ranking`` lists every measured skill with a token count by new share per 1k tokens, once per skill.
    """
    counts = {status: 0 for status in STATUSES}
    for row in rows:
        counts[row["status"]] += 1
    measured = [r for r in rows if r["status"] == "ok" and r["new_lenient"] is not None]
    summary: dict[str, Any] = {
        "counts": counts,
        "usd": round(sum(r["usd"] for r in rows), 6),
        "overall": group_stats(measured, resamples=resamples) if measured else None,
        "strata": None,
        "ranking": ranking(rows),
    }
    if strata is not None:
        groups: dict[str, list[dict]] = {}
        for row in measured:
            groups.setdefault(stratum_of(row, strata), []).append(row)
        summary["strata"] = {name: group_stats(groups[name], resamples=resamples)
                             for name in sorted(groups)}
    return summary


def _interval(block: Optional[dict]) -> str:
    if not block or block["mean"] is None:
        return "-"
    return f"{block['mean']:.2f} [{block['lo']:.2f}, {block['hi']:.2f}]"


def summary_lines(summary: dict[str, Any]) -> list[str]:
    """The summary as text: the already-known share first, then one line per stratum."""
    counts = summary["counts"]
    head = (f"# novelty sweep: {counts['ok']} measured, {counts['partial']} partial, "
            f"{counts['error']} error; ${summary['usd']:.2f} spent")
    lines = [head]
    overall = summary["overall"]
    if overall is None:
        lines.append("no skill was measured: no share to report")
        return lines
    lines.append(f"already known by the model: {_interval(overall['known_lenient'])} "
                 f"(new to it: {_interval(overall['new_lenient'])}; 95% bootstrap CI, n={overall['n']})")
    if summary["strata"] is not None:
        lines.append(f"{'stratum':<16} {'n':>4}  {'new (lenient)':<22} {'already known':<22} {'strict new':<22} {'$':>7}")
        for name, block in summary["strata"].items():
            lines.append(f"{name[:16]:<16} {block['n']:>4}  {_interval(block['new_lenient']):<22} "
                         f"{_interval(block['known_lenient']):<22} {_interval(block['new_strict']):<22} "
                         f"{block['usd']:>7.2f}")
    ranked = summary.get("ranking") or []
    if ranked:
        lines.append(f"ranking by new share per 1k tokens (top {min(RANK_LINES, len(ranked))} of {len(ranked)}):")
        for pos, item in enumerate(ranked[:RANK_LINES], 1):
            lines.append(f"  {pos:>2}. {item['key']}: {item['new_per_1k_tokens']:.4f} "
                         f"(new {item['new_lenient']:.2f}, {item['tokens']} tokens)")
    return lines
