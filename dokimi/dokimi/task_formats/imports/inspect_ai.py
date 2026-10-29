# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Inspect AI samples files and ``@task`` files as dokimi questions.

A samples file (JSON/JSONL rows of ``Sample`` fields: ``input``, ``target``, ``choices``, ``id``,
``metadata``) holds no scorer; ``scorer`` names one (default: ``choice`` for rows with
``choices``, else ``match``). A ``@task`` file is Python: it is parsed with :mod:`ast` and never
imported or run. What it says in literals is read -- ``Sample(...)`` lists,
``json_dataset("<file>", FieldSpec(...))``, ``scorer=<name>(<literal kwargs>)``,
``multiple_choice()`` / ``system_message("...")`` solvers -- and anything computed is refused
or counted as lossy. Scorer semantics follow ``inspect_ai/scorer`` (``match_str``, ``exact``,
``choice``, ``model_graded_qa``) at inspect-ai 0.3.283.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any, Mapping, Optional

from dokimi.eval.reference_checks import blank_value, squad_normalize
from dokimi.task_formats.imports.common import (
    Imported, as_list, choice_targets, judge, match, question, render_input, render_mcq, unsupported,
)
from dokimi.task_formats.source import FileSource

FORMAT_SAMPLES = "inspect-samples"
FORMAT_TASK = "inspect-task"

#: Scorers with a translation, and their default keyword arguments.
SCORERS = ("match", "includes", "exact", "choice", "pattern", "model_graded_qa", "model_graded_fact")

#: Inspect's ``FieldSpec`` defaults: a field the FieldSpec does not name is read from its own column.
_FIELD_DEFAULTS = {k: k for k in ("input", "target", "choices", "id", "metadata", "files", "setup", "sandbox")}


def _choices(value: Any) -> list[str]:
    """Inspect's ``read_choices``: a string splits on commas, else on whitespace; blanks are dropped."""
    if value is None:
        return []
    if isinstance(value, str):
        parts = value.split(",")
        if len(parts) == 1:
            parts = value.split()
        return [p.strip() for p in parts if p.strip()]
    if isinstance(value, (list, tuple)):
        return [str(c) for c in value if str(c).strip()]
    return [str(value)]


def _checks(scorer: str, kwargs: Mapping[str, Any], targets: list[str], imp: Imported) -> list[dict[str, Any]]:
    # A target the scorer normalises to nothing ("", or "..." for `match`) is in every answer, and
    # one `exact` SQuAD-normalises to nothing ("...", "the") equals none, so it is dropped; with none
    # left nothing is graded.
    norm = {"ignore_punctuation": scorer == "match"}
    targets = [t for t in targets if not blank_value("match", norm, t) and (scorer != "exact" or squad_normalize(t))]
    if not targets and scorer in ("match", "includes", "exact", "model_graded_qa", "model_graded_fact"):
        imp.note("sample without a usable target: not graded")
        return [unsupported(scorer)]
    if scorer == "match":
        return [match(targets, str(kwargs.get("location", "end")),
                      ignore_case=bool(kwargs.get("ignore_case", True)),
                      numeric=bool(kwargs.get("numeric", False)))]
    if scorer == "includes":
        return [match(targets, "any", ignore_case=bool(kwargs.get("ignore_case", True)),
                      ignore_punctuation=False)]
    if scorer == "exact":
        return [{"type": "exact", "values": targets}]
    if scorer == "pattern":
        # Inspect extracts the pattern's group and compares it with the target; a bare regex
        # search would pass `ANSWER: 5` for target 4.
        imp.note("pattern scorer: group-vs-target comparison not translated")
        return [unsupported("pattern")]
    if scorer in ("model_graded_qa", "model_graded_fact"):
        imp.note(f"{scorer}: C/P/I grade approximated by a yes/no judge")
        return [judge(reference=" | ".join(targets), native=scorer)]
    imp.note(f"scorer `{scorer}` has no translation")
    return [unsupported(scorer)]


def _sample_question(row: Mapping[str, Any], idx: int, *, scorer: Optional[str], kwargs: Mapping[str, Any],
                     mcq: bool, system: Optional[str], fmt: str, path: Path, imp: Imported,
                     multiple: bool = False) -> Optional[dict[str, Any]]:
    text = render_input(row.get("input", ""), imp)
    targets = [str(t) for t in as_list(row.get("target"))]
    choices = _choices(row.get("choices"))
    chosen = scorer or ("choice" if choices else "match")
    if choices and (mcq or chosen == "choice"):
        text = render_mcq(text, choices, multiple)
        letters = choice_targets(targets, choices)
        if not letters:
            imp.note("sample whose target matches no choice skipped")
            return None
        checks = [{"type": "choice", "values": letters, "choices": choices}]
        if len(letters) != len(targets):
            imp.note("choice target matching no choice")
    else:
        checks = _checks(chosen, kwargs, targets, imp)
    if system:
        text = f"{system}\n\n{text}"
    if row.get("files") or row.get("setup") or row.get("sandbox"):
        imp.note("sample files/setup/sandbox not provided (answer-only)")
    meta: dict[str, Any] = {"native_scorer": chosen}
    if isinstance(row.get("metadata"), Mapping):
        meta["sample_metadata"] = dict(row["metadata"])
    refs = targets
    if checks[0]["type"] == "choice":
        refs = [f"{t}) {choices[ord(t) - ord('A')]}" for t in checks[0]["values"]]
    # Inspect numbers id-less samples from 1; so do we, so a native log joins on the same ids.
    return question(row.get("id", idx + 1), text, {"checks": checks, "native_scorer": chosen},
                    source_format=fmt, path=path, references=refs, metadata=meta)


def import_samples(path: Path, *, scorer: Optional[str] = None, fields: Optional[Mapping[str, str]] = None,
                   kwargs: Optional[Mapping[str, Any]] = None, mcq: bool = False, system: Optional[str] = None,
                   fmt: str = FORMAT_SAMPLES, imp: Optional[Imported] = None, multiple: bool = False) -> Imported:
    """Rows of ``path`` as questions; ``fields`` maps Sample field -> column (Inspect ``FieldSpec``)."""
    if fmt == FORMAT_SAMPLES and scorer is not None and scorer not in SCORERS:
        raise ValueError(f"unknown Inspect scorer {scorer!r}: one of {', '.join(SCORERS)}")
    if fmt == FORMAT_SAMPLES and scorer == "pattern":
        raise ValueError("--scorer pattern needs the pattern its @task passes; import the @task file")
    imp = imp or Imported(fmt, path, native_scorer=scorer)
    if scorer is None and fmt == FORMAT_SAMPLES:
        imp.note("samples file names no scorer: default used")
    source = FileSource(path)
    rows = source.rows if source.rows is not None else ([source.data] if isinstance(source.data, dict) else [])
    spec = {**_FIELD_DEFAULTS, **fields} if fields else {}
    for i, raw in enumerate(rows):
        row = {k: raw.get(v) for k, v in spec.items() if v in raw} if spec else raw
        if "input" not in row:
            imp.note("sample without input skipped")
            continue
        q = _sample_question(row, i, scorer=scorer, kwargs=kwargs or {}, mcq=mcq, system=system, fmt=fmt,
                             path=path, imp=imp, multiple=multiple)
        if q is not None:
            imp.questions.append(q)
    return imp


# --- @task files ------------------------------------------------------------------------------


def _name(node: ast.AST) -> str:
    if isinstance(node, ast.Call):
        return _name(node.func)
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return ""


def _literal(node: ast.AST) -> Any:
    try:
        return ast.literal_eval(node)
    except (ValueError, SyntaxError, TypeError):
        return None


def _kwargs(call: ast.Call) -> dict[str, Any]:
    return {k.arg: _literal(k.value) for k in call.keywords if k.arg}


def _task_call(fn: ast.FunctionDef) -> Optional[ast.Call]:
    for node in ast.walk(fn):
        if isinstance(node, ast.Call) and _name(node) == "Task":
            return node
    return None


def _is_task(fn: ast.AST) -> bool:
    return isinstance(fn, ast.FunctionDef) and any(_name(d) == "task" for d in fn.decorator_list)


def _solvers(node: Optional[ast.AST]) -> list[ast.AST]:
    if node is None:
        return []
    return list(node.elts) if isinstance(node, (ast.List, ast.Tuple)) else [node]


def _read_solver(nodes: list[ast.AST], imp: Imported) -> tuple[bool, bool, Optional[str]]:
    """``(multiple_choice, multiple_correct, system_message)``; other solvers are counted as not reproduced."""
    mcq, multiple, system = False, False, None
    for s in nodes:
        name = _name(s)
        if name == "multiple_choice":
            mcq = True
            if isinstance(s, ast.Call) and _kwargs(s).get("cot"):
                imp.note("multiple_choice(cot=True) asked without the CoT template")
            multiple = isinstance(s, ast.Call) and bool(_kwargs(s).get("multiple_correct"))
        elif name == "system_message" and isinstance(s, ast.Call) and s.args:
            system = _literal(s.args[0]) if isinstance(_literal(s.args[0]), str) else None
            if system is None:
                imp.note("system_message with a computed text dropped")
        elif name != "generate":
            imp.note(f"solver `{name or '?'}` not reproduced")
    return mcq, multiple, system


def _field_spec(call: ast.Call) -> dict[str, str]:
    for node in [*call.args[1:], *(k.value for k in call.keywords if k.arg == "sample_fields")]:
        if isinstance(node, ast.Call) and _name(node) == "FieldSpec":
            return {k: v for k, v in _kwargs(node).items() if isinstance(v, str)}
    return {}


def _scorer(node: Optional[ast.AST], imp: Imported) -> tuple[str, dict[str, Any]]:
    nodes = _solvers(node)
    if not nodes:
        imp.note("task names no scorer: match used")
        return "match", {}
    if len(nodes) > 1:
        imp.note("several scorers: only the first translated")
    first = nodes[0]
    kwargs = _kwargs(first) if isinstance(first, ast.Call) else {}
    if isinstance(first, ast.Call) and first.args and _name(first) == "pattern":
        kwargs["pattern"] = _literal(first.args[0])
    return _name(first), kwargs


def _dataset(node: Optional[ast.AST], base: Path, imp: Imported) -> tuple[Optional[Path], dict[str, str], list[dict[str, Any]]]:
    """``(file, field spec, inline rows)`` of a Task's ``dataset=``."""
    if isinstance(node, (ast.List, ast.Tuple)):
        rows = []
        for el in node.elts:
            if isinstance(el, ast.Call) and _name(el) == "Sample":
                row = _kwargs(el)
                if any(v is None for v in row.values()):
                    imp.note("Sample field that is not a literal dropped")
                rows.append({k: v for k, v in row.items() if v is not None})
        return None, {}, rows
    if isinstance(node, ast.Call) and _name(node) in ("json_dataset", "csv_dataset") and node.args:
        rel = _literal(node.args[0])
        if not isinstance(rel, str):
            raise ValueError("dataset path is computed, not a literal")
        if _name(node) == "csv_dataset":
            raise ValueError("csv_dataset is not translated yet; convert the CSV to JSONL")
        return (base / rel), _field_spec(node), []
    raise ValueError(f"dataset `{_name(node) if node else 'missing'}` is not a literal Sample list or json_dataset; "
                     "run it natively with `inspect eval` and import the log")


def import_task(path: Path, *, task: Optional[str] = None) -> Imported:
    """The ``@task`` function ``task`` (the only one, when not named) of ``path``, statically."""
    text = FileSource(path).text or ""
    try:
        tree = ast.parse(text, filename=str(path))
    except (SyntaxError, RecursionError) as exc:
        raise ValueError(f"{path}: does not parse as Python ({exc})") from exc
    tasks = [n for n in ast.walk(tree) if _is_task(n)]
    chosen = [t for t in tasks if task is None or t.name == task]  # type: ignore[attr-defined]
    if len(chosen) != 1:
        names = ", ".join(t.name for t in tasks) or "none"  # type: ignore[attr-defined]
        raise ValueError(f"{path}: pick one @task with --task (found: {names})")
    call = _task_call(chosen[0])  # type: ignore[arg-type]
    if call is None:
        raise ValueError(f"{path}: @task {chosen[0].name} builds no Task(...) literally")  # type: ignore[attr-defined]
    kw = {k.arg: k.value for k in call.keywords if k.arg}
    imp = Imported(FORMAT_TASK, path)
    if "sandbox" in kw:
        imp.note("sandbox not provided (answer-only)")
    mcq, multiple, system = _read_solver(_solvers(kw.get("solver") or kw.get("plan")), imp)
    scorer, kwargs = _scorer(kw.get("scorer"), imp)
    imp.native_scorer = scorer
    file, spec, rows = _dataset(kw.get("dataset"), path.parent, imp)
    if file is not None:
        if not file.resolve().is_relative_to(path.parent.resolve()):
            raise ValueError(f"{path}: dataset {file} is outside the task's directory")
        return import_samples(file, scorer=scorer, fields=spec, kwargs=kwargs,
                              mcq=mcq, system=system, fmt=FORMAT_TASK, imp=imp, multiple=multiple)
    for i, row in enumerate(rows):
        q = _sample_question(row, i, scorer=scorer, kwargs=kwargs, mcq=mcq, system=system, fmt=FORMAT_TASK,
                             path=path, imp=imp, multiple=multiple)
        if q is not None:
            imp.questions.append(q)
    return imp
