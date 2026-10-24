# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""OpenAI evals ``samples.jsonl`` (``input`` + ``ideal``) as dokimi questions.

The samples do not say how they are graded; the registry eval YAML does
(``evals/registry/evals/<name>.yaml``: ``class`` and ``args``). Pass it as ``grader`` (a path),
or name the template (``match``, ``includes``, ``fuzzy_match``, ``json_match``,
``modelgraded:<spec>``). Templates follow ``docs/eval-templates.md`` and ``evals/api.py``:

- ``Match``: the completion starts with an ideal, case- and whitespace-sensitive.
- ``Includes``: an ideal is a substring (``ignore_case`` from the eval args).
- ``FuzzyMatch``: normalised completion and ideal contain each other.
- ``JsonMatch``: the completion parses to JSON equal to an ideal.
- ``ModelBasedClassify``: the judge, against ``ideal`` as reference (approximated: the native
  spec's multi-choice grade, e.g. ``fact``'s A-E, becomes yes/no).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Optional

from dokimi.task_formats.imports.common import (
    Imported, as_list, judge, match, question, render_input,
)
from dokimi.task_formats.source import FileSource

FORMAT = "openai-evals"

_CLASSES = {
    "basic.match:Match": "match",
    "basic.includes:Includes": "includes",
    "basic.fuzzy_match:FuzzyMatch": "fuzzy_match",
    "basic.json_match:JsonMatch": "json_match",
    "modelgraded.classify:ModelBasedClassify": "modelgraded",
}


def _registry_grader(path: Path) -> tuple[str, dict[str, Any]]:
    """``(template, args)`` from a registry eval YAML: the first entry that names a ``class``."""
    data = FileSource(path).data
    if not isinstance(data, Mapping):
        raise ValueError(f"{path}: not a registry eval YAML mapping")
    for entry in data.values():
        if isinstance(entry, Mapping) and isinstance(entry.get("class"), str):
            cls = entry["class"].removeprefix("evals.elsuite.")
            raw_args = entry.get("args")
            args: Mapping[str, Any] = raw_args if isinstance(raw_args, Mapping) else {}
            if cls not in _CLASSES:
                raise ValueError(f"{path}: eval class {entry['class']} is custom code; "
                                 f"only {', '.join(sorted(_CLASSES))} translate")
            template = _CLASSES[cls]
            if template == "modelgraded":
                template = f"modelgraded:{args.get('modelgraded_spec', '')}"
            return template, dict(args)
    raise ValueError(f"{path}: no eval entry with a `class`")


def resolve_grader(grader: Optional[str]) -> tuple[str, dict[str, Any], bool]:
    """``(template, args, defaulted)``."""
    if not grader:
        return "match", {}, True
    if grader.endswith((".yaml", ".yml")):
        template, args = _registry_grader(Path(grader))
        return template, args, False
    if grader in ("match", "includes", "fuzzy_match", "json_match") or grader.startswith("modelgraded:"):
        return grader, {}, False
    raise ValueError(f"unknown OpenAI evals grader {grader!r}: a registry YAML path, match, includes, "
                     "fuzzy_match, json_match or modelgraded:<spec>")


def _checks(template: str, args: Mapping[str, Any], ideals: list[Any], imp: Imported) -> list[dict[str, Any]]:
    texts = [str(i) for i in ideals]
    if template == "match":
        return [match(texts, "begin", ignore_case=False, ignore_punctuation=False, strip=False)]
    if template == "includes":
        return [match(texts, "any", ignore_case=bool(args.get("ignore_case", False)),
                      ignore_punctuation=False, strip=False)]
    if template == "fuzzy_match":
        return [{"type": "fuzzy", "values": texts}]
    if template == "json_match":
        values = []
        for t in texts:
            try:
                values.append(json.loads(t))
            except ValueError:
                imp.note("ideal that is not JSON dropped")
        return [{"type": "json", "values": values}]
    spec = template.split(":", 1)[1] or "unnamed"
    imp.note(f"modelgraded spec `{spec}` approximated by a yes/no judge")
    spec_args = args.get("modelgraded_spec_args")
    criteria = spec_args.get("criteria") if isinstance(spec_args, Mapping) else None
    reference = " | ".join(texts)
    return [judge(criterion=str(criteria or ""), reference=reference, native=f"modelgraded:{spec}")]


def import_file(path: Path, *, grader: Optional[str] = None) -> Imported:
    source = FileSource(path)
    rows = source.rows if source.rows is not None else []
    template, args, defaulted = resolve_grader(grader)
    imp = Imported(FORMAT, path, native_scorer=template)
    if defaulted:
        imp.note("no registry eval given: graded as Match")
    if args.get("num_few_shot"):
        imp.note("few-shot samples (num_few_shot) not added")
    for i, row in enumerate(rows):
        if "input" not in row or "ideal" not in row:
            imp.note("row without input or ideal skipped")
            continue
        ideals = as_list(row["ideal"])
        text = render_input(row["input"], imp)
        checks = _checks(template, args, ideals, imp)
        imp.questions.append(question(
            row.get("id", i), text, {"checks": checks, "native_scorer": template},
            source_format=FORMAT, path=path, references=[str(x) for x in ideals]))
    return imp
