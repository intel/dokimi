# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""``repo hits judge``: a judge model labels each credited pair of a batch's ``dokimi:review_replay`` cells.

The replay grader credits a human thread to the nearest finding on its file within ``window`` lines
(contract §4.5). Location is all it checks, so a finding that only sits near a thread is credited too.
This step reads each credited pair (:func:`.report.load_hits`) with its full texts: the thread's
``comment`` from the task's gold file and the finding from the cell's ``answer.txt`` at index ``finding``
(``matches`` cuts both to 200 characters and is used for the pairing only). A judge labels the pair
``same point``, ``related`` or ``unrelated``, and each fully judged cell gets ``hits_judged.json``
(``repo_hits_judged.v1``) with ``recall_judged``: the ``same point`` pairs over the task's threads.
Only credited pairs are rescored, so ``recall_judged <= recall``. The grade is not touched.

The judge may not run the model of any answer of the batch (:func:`check_judge`), and a figure is
uncalibrated until a person's labels are compared with the judge's (:mod:`.hits_calibration`).
"""

from __future__ import annotations

import json
import os
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from dokimi import caching
from dokimi.artifacts import save_artifact
from dokimi.judged import judged_reply
from dokimi.llm import LLM, Ask, Unparseable
from dokimi.llm_json import extract_json_object

from .graders.review import finding_fields, findings_of
from .report import REPLAY, load_cells, load_hits, resolved_models

LABELS = ("same point", "related", "unrelated")
UNJUDGED = "unjudged"
HITS_JUDGED = "hits_judged.json"
#: The reply's tokens in the dry-run estimate: one short JSON object.
OUTPUT_TOKENS = 100

PROMPT = """\
A pull request was reviewed twice: by a human reviewer, and later by an automated reviewer. A grader
paired the human's review thread below with the automated finding below because they sit on nearby
lines of the same file. Decide whether the finding makes the point the thread makes.

HUMAN THREAD at {thread_file}:{thread_line}
{comment}

FINDING at {file}:{line}
{text}

Labels:
- "same point": the finding raises the problem, or asks for the change, that the thread raises.
- "related": the finding is about the same code and touches the thread's concern, but makes another point.
- "unrelated": the finding is about something else.

Return ONLY JSON: {{"label": "same point" | "related" | "unrelated", "reason": "<one sentence>"}}
"""


class HitsInputError(ValueError):
    """An input ``repo hits`` cannot use: a judge that answered the batch, or a labels file entry."""


class _NotFound(Exception):
    """The full text of a pair is not where the grader read it."""


class BudgetSpent(Exception):
    """``--max-usd`` is spent: no new judge call starts."""


@dataclass
class Pair:
    """One credited (thread, finding) pair, with the full texts the judge reads.

    ``problem`` says why a text was not found; such a pair is reported and not judged."""

    hit: dict[str, Any]
    comment: Optional[str] = None
    text: Optional[str] = None
    problem: Optional[str] = None

    @property
    def cell(self) -> str:
        return str(self.hit["cell"])

    @property
    def arm(self) -> str:
        return str(self.hit["arm"])

    @property
    def key(self) -> tuple[str, str]:
        return pair_key(self.cell, self.hit.get("thread"))


def pair_key(cell: Any, thread: Any) -> tuple[str, str]:
    """A pair's identity: its cell and its thread id (JSON-encoded: an id may be a number or a string)."""
    return str(cell), json.dumps(thread, sort_keys=True)


def _json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise _NotFound(f"cannot read {path}: {exc.strerror or exc}") from exc
    except (ValueError, RecursionError) as exc:
        raise _NotFound(f"{path} is not valid JSON") from exc


def finding_text(finding: dict[str, Any]) -> str:
    """A finding as the judge reads it: its ``message``, then its ``rule`` and ``evidence`` when present."""
    lines = [str(finding.get("message") or "")]
    lines += [f"{k}: {finding[k]}" for k in ("rule", "evidence") if finding.get(k) not in (None, "")]
    return "\n".join(lines)


class _Texts:
    """The full texts of a batch's pairs, read once per cell and per task."""

    def __init__(self, batch_dir: Path):
        self.batch_dir = batch_dir
        self.content = batch_dir / "_eval" / "content"
        self._task_dirs: Optional[dict[str, list[Path]]] = None
        self._read: dict[tuple[str, str], Any] = {}

    def _once(self, kind: str, cell: str, read: Callable[[], Any]) -> Any:
        if (kind, cell) not in self._read:
            try:
                self._read[(kind, cell)] = read()
            except _NotFound as exc:
                self._read[(kind, cell)] = exc
        found = self._read[(kind, cell)]
        if isinstance(found, _NotFound):
            raise found
        return found

    def task_dir(self, task: str) -> Path:
        """The task's directory in the batch's exported eval dir, found by its name as ``repo run`` loads it."""
        if self._task_dirs is None:
            self._task_dirs = defaultdict(list)
            for path in sorted(self.content.rglob("task.json")):
                self._task_dirs[path.parent.name].append(path.parent)
        dirs = self._task_dirs.get(task, [])
        if len(dirs) != 1:
            where = "no" if not dirs else f"{len(dirs)}"
            raise _NotFound(f"{where} directory of task {task} with a task.json under {self.content}")
        return dirs[0]

    def gold(self, cell: str, task: str) -> tuple[Path, list[Any]]:
        """The cell's gold threads: ``task.json`` ``gold`` (default ``gold.json``) in the task's directory."""
        def read() -> tuple[Path, list[Any]]:
            spec = _json(self.batch_dir / cell / "task.json")
            name = spec.get("gold", "gold.json") if isinstance(spec, dict) else None
            if not isinstance(name, str):
                raise _NotFound(f"{self.batch_dir / cell / 'task.json'} names no gold file")
            path = self.task_dir(task) / name
            gold = _json(path)
            if not isinstance(gold, list):
                raise _NotFound(f"{path} is not a list of threads")
            return path, gold
        return self._once("gold", cell, read)

    def findings(self, cell: str) -> list[dict[str, Any]]:
        """The findings of the cell's ``answer.txt``, read as the grader reads them."""
        def read() -> list[dict[str, Any]]:
            path = self.batch_dir / cell / "answer.txt"
            try:
                answer = path.read_text(encoding="utf-8")
            except (OSError, ValueError) as exc:
                raise _NotFound(f"cannot read {path}: {exc}") from exc
            found = findings_of(answer)
            if found is None:
                raise _NotFound(f"{path} holds no findings JSON")
            return found
        return self._once("findings", cell, read)


def _comment(texts: _Texts, hit: dict[str, Any]) -> str:
    path, gold = texts.gold(hit["cell"], hit["task"])
    thread = hit.get("thread")
    found = [g for g in gold if isinstance(g, dict) and type(g.get("id")) is type(thread) and g.get("id") == thread]
    if not found:
        raise _NotFound(f"thread {thread!r} is not in {path}")
    if found[0].get("file") != hit.get("thread_file"):
        raise _NotFound(f"thread {thread!r} of {path} is on {found[0].get('file')!r}, "
                        f"not on {hit.get('thread_file')!r} as grade.json credits it")
    comment = found[0].get("comment")
    if not isinstance(comment, str):
        raise _NotFound(f"thread {thread!r} of {path} has no comment")
    return comment


def _finding(texts: _Texts, hit: dict[str, Any]) -> str:
    findings = texts.findings(hit["cell"])
    index = hit.get("finding")
    if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < len(findings):
        raise _NotFound(f"finding {index!r} is not among the {len(findings)} finding(s) of answer.txt")
    path, _, _ = finding_fields(findings[index])
    if path != hit.get("file"):
        raise _NotFound(f"finding {index} of answer.txt is on {path!r}, not on {hit.get('file')!r} as grade.json "
                        "credits it")
    return finding_text(findings[index])


def read_pairs(batch_dir: Path) -> tuple[dict[str, Any], list[Pair]]:
    """:func:`.report.load_hits` of the batch, and its credited pairs with their full texts."""
    found = load_hits(batch_dir)
    texts = _Texts(batch_dir)
    pairs = []
    for hit in found["hits"]:
        pair = Pair(hit)
        try:
            pair.comment, pair.text = _comment(texts, hit), _finding(texts, hit)
        except _NotFound as exc:
            pair.problem = str(exc)
        pairs.append(pair)
    return found, pairs


def answer_models(batch_dir: Path) -> set[str]:
    """The answer models of every finished cell of the batch, from ``result.json`` and ``meta.json``: the
    ``model`` asked for, and the ``model_resolved`` the trace says answered (:func:`.report.resolved_models`);
    a harness alias can map one to another."""
    cells, _ = load_cells(batch_dir)
    models: set[str] = set()
    for rec in cells:
        models |= {rec["model"], *resolved_models(rec)}
        try:
            meta = _json(batch_dir / rec["cell"] / "meta.json")
        except _NotFound:
            continue
        if isinstance(meta, dict):
            models |= {*([meta["model"]] if isinstance(meta.get("model"), str) else []), *resolved_models(meta)}
    return models


def check_judge(judge_ref: tuple[str, str], answered_by: Iterable[str]) -> None:
    """Refuse a ``(provider, model)`` judge that runs the model of any answer, before any paid call.

    The rule of ``novelty`` (:func:`dokimi.novelty.client.same_client`): the same name, or a CLI alias and a
    full id of its family. An answer model is the name its harness CLI resolved, so it is read as a CLI ref."""
    from dokimi.llm_cli import CLAUDE_CLI
    from dokimi.novelty.client import LLMClient, same_client

    provider, model = judge_ref
    judge = LLMClient(model=model, provider=provider, role="judge")
    for answer in sorted(set(answered_by)):
        if same_client(judge, LLMClient(model=answer, provider=CLAUDE_CLI.name, role="answerer")):
            raise HitsInputError(
                f"judge {provider}/{model} runs the same model as the answer model {answer!r} of this batch; "
                "a judge must be of another family than every answer model (pass another --judge)")


def prompt_for(pair: Pair) -> str:
    hit = pair.hit
    return PROMPT.format(thread_file=hit.get("thread_file"), thread_line=hit.get("thread_line"),
                         comment=pair.comment, file=hit.get("file"), line=hit.get("line"), text=pair.text)


def parse_label(raw: str) -> Optional[tuple[str, str]]:
    """``(label, reason)`` of a judge reply, or ``None`` when it has no label of the three and no reason."""
    reply: Any = extract_json_object(raw)  # typed dict, but a reply of a JSON list parses to a list
    if not isinstance(reply, dict):
        return None
    label, reason = reply.get("label"), reply.get("reason")
    label = label.strip().lower() if isinstance(label, str) else label
    if label not in LABELS or not isinstance(reason, str):
        return None
    return str(label), reason.strip()


@dataclass
class Meter:
    """Live judge calls and their cost; with ``max_usd``, no new live call starts once that much is spent.

    A response-cache replay is free and not counted. A call with no known price counts as 0, as ``repo run``
    counts it."""

    max_usd: Optional[float] = None
    spent_usd: float = 0.0
    calls: int = 0

    def asking(self, llm: LLM) -> Ask:
        def ask(prompt: str, *, draw: int = 0, cache_if: Optional[Callable[[str], bool]] = None) -> str:
            if not caching.replaying_only() and self.max_usd is not None and self.spent_usd >= self.max_usd:
                raise BudgetSpent()
            reply = llm.complete(prompt, draw=draw, cache_if=cache_if)
            if not reply.usage.replayed:
                self.calls += 1
                self.spent_usd += reply.usage.cost_usd or 0.0
            return reply.text or ""
        return ask


def judge_pair(ask: Ask, pair: Pair) -> dict[str, Any]:
    """The pair's record in ``hits_judged.json``; ``label`` null when its texts were not found or no reply parsed."""
    out: dict[str, Any] = {"thread": pair.hit.get("thread"), "finding": pair.hit.get("finding"),
                           "label": None, "reason": None}
    if pair.problem is not None:
        return {**out, "error": f"not judged: {pair.problem}"}
    try:
        (label, reason), _ = judged_reply(ask, prompt_for(pair), parse_label, pinned=False, label="hits judge")
    except Unparseable as exc:
        return {**out, "error": f"no reply parsed: {exc}"}
    return {**out, "label": label, "reason": reason}


def _count(value: Any) -> Optional[int]:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def cell_doc(cell: dict[str, Any], judge: tuple[str, str], judged: list[dict[str, Any]]) -> dict[str, Any]:
    """The ``repo_hits_judged.v1`` document of one cell of :func:`.report.load_hits` ``cells``."""
    counts = {label: 0 for label in (*LABELS, UNJUDGED)}
    for record in judged:
        counts[record["label"] or UNJUDGED] += 1
    n_threads = _count(cell.get("n_threads"))
    recall = cell.get("recall")
    return {"cell": cell["cell"], "task": cell["task"], "arm": cell["arm"], "model": cell["model"],
            "rep": cell["rep"], "judge": {"provider": judge[0], "model": judge[1]}, "pairs": judged,
            "counts": counts, "n_threads": n_threads,
            "recall": recall if isinstance(recall, (int, float)) and not isinstance(recall, bool) else None,
            "recall_judged": round(counts["same point"] / n_threads, 3) if n_threads else None}


@dataclass
class Judged:
    """What :func:`judge_cells` did: the documents written, the cells the budget stopped, the calls made, and the
    cells whose earlier file it removed because their grade no longer gives credited pairs."""

    docs: list[dict[str, Any]] = field(default_factory=list)
    stopped: list[str] = field(default_factory=list)
    meter: Meter = field(default_factory=Meter)
    stale: list[str] = field(default_factory=list)

    def labels(self) -> dict[tuple[str, str], str]:
        """The judge's label of each judged pair, by :func:`pair_key`."""
        return {pair_key(d["cell"], p["thread"]): p["label"] for d in self.docs for p in d["pairs"] if p["label"]}


class HitsWriteError(Exception):
    """A cell's ``hits_judged.json`` could not be written, or an earlier one removed: :func:`judge_cells` stopped
    there. ``done`` is what it did before (the files written stay), ``error`` the ``OSError``."""

    def __init__(self, action: str, path: Path, error: OSError, done: Judged):
        super().__init__(f"cannot {action} {path}: {error.strerror or error}")
        self.path, self.error, self.done = path, error, done


def judge_cells(batch_dir: Path, found: dict[str, Any], pairs: list[Pair], judge: tuple[str, str],
                max_usd: Optional[float] = None) -> Judged:
    """Judge every pair, one call each through :func:`dokimi.judged.judged_reply`, and write each fully judged
    cell's ``hits_judged.json``. A cell the budget stopped gets no file (one left by an earlier run goes).

    First, before any call, it removes the ``hits_judged.json`` an earlier run left in every other finished cell
    of the batch (:func:`.report.load_cells`): one regraded since into a state ``found["cells"]`` leaves out (a
    grader failure, a grade with no ``matches``, another grader). ``Judged.stale`` names those cells.

    Raises :class:`HitsWriteError` at the first file it cannot write or remove, before any further call."""
    by_cell: dict[str, list[Pair]] = defaultdict(list)
    for pair in pairs:
        by_cell[pair.cell].append(pair)
    done = Judged(meter=Meter(max_usd))
    read = {cell["cell"] for cell in found["cells"]}
    for rec in load_cells(batch_dir)[0]:
        path = batch_dir / rec["cell"] / HITS_JUDGED
        if rec["cell"] in read or not os.path.lexists(path):
            continue
        try:  # before the first call: a file that cannot go stops the run before it spends anything
            path.unlink()
        except OSError as exc:
            raise HitsWriteError("remove", path, exc, done) from exc
        done.stale.append(rec["cell"])
    ask = done.meter.asking(LLM(judge[1], judge[0]))
    for cell in found["cells"]:
        path = batch_dir / cell["cell"] / HITS_JUDGED
        judged: Optional[list[dict[str, Any]]]
        try:
            judged = [judge_pair(ask, p) for p in by_cell.get(cell["cell"], [])]
        except BudgetSpent:
            done.stopped.append(cell["cell"])
            judged = None
        try:
            if judged is None:
                path.unlink(missing_ok=True)
            else:
                done.docs.append(save_artifact("repo_hits_judged", cell_doc(cell, judge, judged), path))
        except OSError as exc:  # no paid call follows a file that cannot be kept in step with the run
            raise HitsWriteError("write" if judged is not None else "remove", path, exc, done) from exc
    return done


def _mean(values: list[Any]) -> Optional[float]:
    nums = [float(v) for v in values if isinstance(v, (int, float)) and not isinstance(v, bool)]
    return round(sum(nums) / len(nums), 4) if nums else None


def rows(docs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per (arm, model): cells, pairs, label counts, mean ``recall`` and mean ``recall_judged`` of the written cells."""
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for doc in docs:
        groups[(doc["arm"], doc["model"])].append(doc)
    return [{"arm": arm, "model": model, "cells": len(group), "pairs": sum(len(d["pairs"]) for d in group),
             "counts": {k: sum(d["counts"][k] for d in group) for k in (*LABELS, UNJUDGED)},
             "recall": _mean([d["recall"] for d in group]),
             "recall_judged": _mean([d["recall_judged"] for d in group])}
            for (arm, model), group in sorted(groups.items())]


def summary(found: dict[str, Any], pairs: list[Pair], judge: tuple[str, str], done: Judged,
            calibration: Optional[list[dict[str, Any]]]) -> dict[str, Any]:
    """What ``repo hits judge`` prints (``--json``: this dict).

    ``uncalibrated_arms``: the arms of ``rows`` with no pair labelled by both the person and the judge (every
    arm without ``--labels``), whose ``recall_judged`` no calibration covers. ``stale_removed``: the cells whose
    earlier ``hits_judged.json`` :func:`judge_cells` removed (``Judged.stale``)."""
    table = rows(done.docs)
    calibrated = {c["arm"] for c in calibration or [] if c["labelled"]}
    return {"batch": found["batch"], "judge": {"provider": judge[0], "model": judge[1]},
            "replay_cells": found["replay_cells"], "without_matches": found["without_matches"],
            "grader_failures": found["grader_failures"], "pairs": len(pairs),
            "not_found": [{"cell": p.cell, "thread": p.hit.get("thread"), "problem": p.problem}
                          for p in pairs if p.problem is not None],
            "rows": table, "written": len(done.docs), "budget_stopped": done.stopped, "stale_removed": done.stale,
            "calls": done.meter.calls, "spent_usd": round(done.meter.spent_usd, 6), "calibration": calibration,
            "uncalibrated_arms": sorted({r["arm"] for r in table} - calibrated)}


def _fmt(value: Optional[float]) -> str:
    return "-" if value is None else f"{value:.3f}"


def _row_lines(table: list[dict[str, Any]]) -> list[str]:
    arm_w = max([len(r["arm"]) for r in table] + [3])
    model_w = max([len(r["model"]) for r in table] + [5])
    lines = [f"{'arm':<{arm_w}}  {'model':<{model_w}}  {'cells':>5}  {'pairs':>5}  {'same point':>10}  "
             f"{'related':>7}  {'unrelated':>9}  {'unjudged':>8}  {'recall':>6}  {'recall_judged':>13}"]
    for r in table:
        c = r["counts"]
        lines.append(f"{r['arm']:<{arm_w}}  {r['model']:<{model_w}}  {r['cells']:>5}  {r['pairs']:>5}  "
                     f"{c['same point']:>10}  {c['related']:>7}  {c['unrelated']:>9}  {c[UNJUDGED]:>8}  "
                     f"{_fmt(r['recall']):>6}  {_fmt(r['recall_judged']):>13}")
    return lines


def _calibration_lines(calibration: Optional[list[dict[str, Any]]], uncalibrated: list[str]) -> list[str]:
    """No pair labelled by both (no ``--labels``, an unfilled sheet, labels only on unjudged pairs) is no
    calibration; with some, the arms that have none are named."""
    if not any(c["labelled"] for c in calibration or []):
        why = "" if calibration is None else " (no pair carries both a person's and the judge's label)"
        return [f"recall_judged is uncalibrated{why}: label a sample of pairs (`repo hits sheet`) and pass it "
                "with --labels before quoting it"]
    lines = ["calibration against the labels (person vs judge):"]
    for c in calibration or []:
        lines.append(f"  {c['arm']}: {c['labelled']} labelled pair(s), agreement {_fmt(c['agreement'])}, "
                     f"kappa {_fmt(c['kappa'])}")
    if uncalibrated:
        lines.append("recall_judged is uncalibrated in " + ", ".join(uncalibrated)
                     + ": no pair of it carries both a person's and the judge's label")
    return lines


def format_summary(s: dict[str, Any]) -> str:
    judge = f"{s['judge']['provider']}/{s['judge']['model']}"
    lines = [f"{s['batch']}: {s['pairs']} credited pair(s) in {s['replay_cells']} {REPLAY} cell(s), judge {judge}; "
             f"{s['written']} cell(s) written ({HITS_JUDGED})", *_row_lines(s["rows"])]
    if s["without_matches"] or s["grader_failures"]:
        lines.append(f"not judged: {s['without_matches']} cell(s) graded without hit details, "
                     f"{s['grader_failures']} grader failure(s)")
    for p in s["not_found"]:
        lines.append(f"not judged, full text not found: {p['cell']} thread {p['thread']!r}: {p['problem']}")
    if s["budget_stopped"]:
        lines.append(f"{len(s['budget_stopped'])} cell(s) not fully judged (--max-usd spent), no {HITS_JUDGED}: "
                     + ", ".join(s["budget_stopped"]))
    if s["stale_removed"]:
        shown = s["stale_removed"][:10]
        lines.append(f"removed the earlier {HITS_JUDGED} of {len(s['stale_removed'])} cell(s) not judged now "
                     "(a grader failure, a grade without hit details, another grader): " + ", ".join(shown)
                     + (f" and {len(s['stale_removed']) - len(shown)} more" if len(s["stale_removed"]) > 10 else ""))
    lines += _calibration_lines(s["calibration"], s["uncalibrated_arms"])
    lines.append(f"{s['calls']} judge call(s), ${s['spent_usd']:.4f}")
    return "\n".join(lines)


def estimate(found: dict[str, Any], pairs: list[Pair], judge: tuple[str, str]) -> dict[str, Any]:
    """``--dry-run``: the pairs a run would judge and their list-price cost at one call each (``None``: unpriced).

    Prompt tokens are the prompt's characters over 4; the reply is :data:`OUTPUT_TOKENS`."""
    from dokimi.llm_pricing import list_cost_usd

    todo = [p for p in pairs if p.problem is None]
    costs = [list_cost_usd(judge[1], judge[0], len(prompt_for(p)) // 4 + 1, OUTPUT_TOKENS) for p in todo]
    usd = None if None in costs else round(sum(c for c in costs if c is not None), 6)
    return {"batch": found["batch"], "judge": {"provider": judge[0], "model": judge[1]},
            "cells": len(found["cells"]), "pairs": len(pairs), "to_judge": len(todo), "estimated_usd": usd}


def format_estimate(e: dict[str, Any]) -> str:
    judge = f"{e['judge']['provider']}/{e['judge']['model']}"
    cost = ("unavailable (no list price for the judge)" if e["estimated_usd"] is None
            else f"${e['estimated_usd']:.4f} at list price, one call per pair (a redraw adds a call)")
    return (f"{e['batch']}: {e['to_judge']} of {e['pairs']} credited pair(s) to judge in {e['cells']} cell(s) "
            f"with {judge}; estimated cost {cost}; dry run, no call made")
