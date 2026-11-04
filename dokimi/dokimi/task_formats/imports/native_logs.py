# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""A native tool's run log as dokimi records: the delegation path.

When a format cannot be translated (a computed dataset, a custom scorer, a tool-using solver),
run the tool itself -- ``inspect eval <task> --log-format json``, ``promptfoo eval -o out.json``
-- and read its log here. The completions become an ``answers.v1`` artifact (one condition,
``without_docs``: the native run had no dokimi doc context) and the tool's own verdicts an
``eval.v1`` artifact, so the native score sits next to dokimi's on the same records and
``eval score reference`` can re-grade the same completions. Question ids follow the importers': the
Inspect sample id, ``<testIdx>`` / ``<testIdx>.p<promptIdx>`` for promptfoo.

Read for Inspect AI log format ``json`` (inspect-ai 0.3.283; ``samples[].output.completion``,
``samples[].scores``) and promptfoo ``-o *.json`` output (0.125.0; ``results.results[]``).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional

NATIVE_CONDITION = "without_docs"

#: Inspect's score letters (CORRECT, PARTIAL, INCORRECT, NOANSWER) as numbers.
_INSPECT_VALUES = {"C": 1.0, "P": 0.5, "I": 0.0, "N": 0.0}


@dataclass
class NativeRun:
    tool: str
    model: Optional[str]
    answers: list[dict[str, Any]] = field(default_factory=list)
    evaluations: list[dict[str, Any]] = field(default_factory=list)

    def answers_artifact(self) -> dict[str, Any]:
        return {"model": self.model, "provider": self.tool, "total_questions": len(self.answers),
                "answers": self.answers}

    def eval_artifact(self) -> dict[str, Any]:
        return {"judge_model": None, "judge_provider": self.tool, "total_evaluations": len(self.evaluations),
                "evaluations": self.evaluations, "run_metadata": {"scorer": f"native:{self.tool}"}}


def _block(score: Optional[float], passed: Optional[bool], tool: str, detail: Any = None) -> dict[str, Any]:
    out: dict[str, Any] = {"aggregate": None if score is None else round(100 * score, 1), "passed": passed,
                           "scorer": f"native:{tool}"}
    if detail is not None:
        out["native"] = detail
    return out


def _add(run: NativeRun, qid: Any, text: str, completion: Optional[str], score: Optional[float],
         passed: Optional[bool], detail: Any = None, error: Optional[str] = None) -> None:
    answer = None if completion is None else {"answer": completion, "model": run.model}
    if answer is not None and error:
        answer["error"] = error  # a failed sample is counted under `errors` in `eval score reference`
    run.answers.append({"question_id": str(qid), "question_text": text, "without_docs": answer})
    run.evaluations.append({"question_id": str(qid), "question_text": text,
                            NATIVE_CONDITION: _block(score, passed, run.tool, detail)})


def _inspect_value(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    return _INSPECT_VALUES.get(str(value)) if value is not None else None


def _inspect_input(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n\n".join(str(m.get("content", "")) for m in value if isinstance(m, Mapping))
    return str(value)


def read_inspect(data: Mapping[str, Any]) -> NativeRun:
    """An Inspect ``json`` log; the first scorer's value is the native verdict."""
    model = (data.get("eval") or {}).get("model")
    run = NativeRun("inspect", model)
    for s in data.get("samples") or []:
        scores = s.get("scores") or {}
        first = next(iter(scores.values()), None) if isinstance(scores, Mapping) else None
        value = _inspect_value(first.get("value")) if isinstance(first, Mapping) else None
        completion = (s.get("output") or {}).get("completion")
        detail = {"scorer": next(iter(scores), None), "value": first.get("value") if first else None}
        err = s.get("error")
        message = str(err.get("message") or "error") if isinstance(err, Mapping) else (str(err) if err else None)
        _add(run, s.get("id"), _inspect_input(s.get("input")), completion, value,
             None if value is None else value >= 1.0, detail, error=message)
    return run


def read_promptfoo(data: Mapping[str, Any]) -> NativeRun:
    """promptfoo ``-o out.json``; one provider per file (several ids would collide)."""
    results = (data.get("results") or {}).get("results") or []
    providers = {str((r.get("provider") or {}).get("id")) for r in results}
    if len(providers) > 1:
        raise ValueError(f"promptfoo output has {len(providers)} providers; filter to one ({sorted(providers)})")
    run = NativeRun("promptfoo", next(iter(providers), None))
    prompts = {r.get("promptIdx", 0) for r in results}
    for r in results:
        ti, pi = r.get("testIdx", 0), r.get("promptIdx", 0)
        qid = str(ti) if len(prompts) == 1 else f"{ti}.p{pi}"
        output = (r.get("response") or {}).get("output")
        completion = output if isinstance(output, str) else (None if output is None else json.dumps(output))
        score = r.get("score")
        _add(run, qid, str((r.get("prompt") or {}).get("raw", "")), completion,
             float(score) if isinstance(score, (int, float)) else None, r.get("success"),
             {"error": r.get("error")} if r.get("error") else None)
    return run


def read_log(path: Path) -> NativeRun:
    """A native log, by shape: Inspect (``eval`` + ``samples``) or promptfoo (``results.results``)."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, Mapping) and "samples" in data and "eval" in data:
        return read_inspect(data)
    if isinstance(data, Mapping) and isinstance((data.get("results") or {}).get("results"), list):
        return read_promptfoo(data)
    raise ValueError(f"{path}: neither an Inspect json log nor a promptfoo output file")
