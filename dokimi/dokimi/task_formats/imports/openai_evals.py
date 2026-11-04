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
  spec's multi-choice grade, e.g. ``fact``'s A-E, becomes yes/no). With no ideal and no ``criteria``
  the spec's own prompt (``registry/modelgraded/<spec>.yaml``, beside the grader's ``registry/evals``)
  is the criterion; without that file the row is unsupported.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Optional

from dokimi.task_formats.imports.common import (
    Imported, as_list, judge, match, question, render_input, unsupported,
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


#: A registry spec's prompt names its inputs in braces; a judge reads them as these words.
_SPEC_SLOTS = {"{input}": "the question", "{completion}": "the answer", "{ideal}": "the reference answer"}


def _spec_prompt(grader: Optional[str], template: str) -> Optional[str]:
    """The prompt of the model-graded spec a grader YAML names, from ``registry/modelgraded/<spec>.yaml``."""
    spec = template.split(":", 1)[1] if template.startswith("modelgraded:") else ""
    if not spec or "/" in spec or ".." in spec or not grader or not grader.endswith((".yaml", ".yml")):
        return None
    spec_file = Path(grader).resolve().parents[1] / "modelgraded" / f"{spec}.yaml"
    if not spec_file.is_file():
        return None
    data = FileSource(spec_file).data
    body = data.get(spec) if isinstance(data, Mapping) else None
    prompt = body.get("prompt") if isinstance(body, Mapping) else None
    if not isinstance(prompt, str) or not prompt.strip():
        return None
    for slot, words in _SPEC_SLOTS.items():
        prompt = prompt.replace(slot, words)
    return " ".join(prompt.split())


def _checks(template: str, args: Mapping[str, Any], ideals: list[Any], imp: Imported,
            spec_prompt: Optional[str] = None) -> list[dict[str, Any]]:
    # A blank ideal is contained in every answer, so it is dropped; with none left nothing is graded.
    texts = [t for t in (json.dumps(i) if isinstance(i, (dict, list)) else str(i) for i in ideals) if t.strip()]
    if not texts and not template.startswith("modelgraded"):
        imp.note("row without a usable ideal: not graded")
        return [unsupported(template)]
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
        # An empty `values` means "any JSON" to the grader; with no ideal left nothing is compared.
        return [{"type": "json", "values": values}] if values else [unsupported("json_match")]
    spec = template.split(":", 1)[1] or "unnamed"
    imp.note(f"modelgraded spec `{spec}` approximated by a yes/no judge")
    spec_args = args.get("modelgraded_spec_args")
    criteria = spec_args.get("criteria") if isinstance(spec_args, Mapping) else None
    reference = " | ".join(texts)
    if not (criteria or reference) and spec_prompt:
        imp.note(f"modelgraded spec `{spec}` with no ideal: its registry prompt used as the criterion")
        criteria = spec_prompt
    if not (criteria or reference):
        # The spec's own prompt lives in registry/modelgraded/<spec>.yaml, which is not read.
        imp.note(f"modelgraded spec `{spec}` with no ideal and no criteria: not graded")
        return [unsupported(f"modelgraded:{spec}")]
    return [judge(criterion=str(criteria or ""), reference=reference, native=f"modelgraded:{spec}")]


def import_file(path: Path, *, grader: Optional[str] = None) -> Imported:
    source = FileSource(path)
    rows = source.rows if source.rows is not None else []
    template, args, defaulted = resolve_grader(grader)
    spec_prompt = _spec_prompt(grader, template)
    imp = Imported(FORMAT, path, native_scorer=template)
    if defaulted:
        imp.note("no registry eval given: graded as Match")
    if args.get("num_few_shot"):
        imp.note("few-shot samples (num_few_shot) not added")
    for i, row in enumerate(rows):
        # ModelBasedClassify rows often carry only `input`: the spec grades the completion alone.
        if "input" not in row or ("ideal" not in row and not template.startswith("modelgraded")):
            imp.note("row without input or ideal skipped")
            continue
        ideals = as_list(row.get("ideal"))
        text = render_input(row["input"], imp)
        checks = _checks(template, args, ideals, imp, spec_prompt)
        imp.questions.append(question(
            row.get("id", i), text, {"checks": checks, "native_scorer": template},
            source_format=FORMAT, path=path, references=[str(x) for x in ideals]))
    return imp
