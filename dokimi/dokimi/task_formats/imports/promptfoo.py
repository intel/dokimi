# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""promptfoo configs (``prompts`` x ``tests`` with ``assert``) as dokimi questions.

One question per (test, prompt) pair, the prompt rendered with the test's ``vars``
(``{{ var }}`` only; see :func:`~dokimi.task_formats.imports.common.render_template`).
``providers`` are ignored: the model under test is the answer track's. Assertion types follow
promptfoo.dev/docs/configuration/expected-outputs (promptfoo 0.125.0); code asserts
(``javascript``, ``python``, ``webhook``) are never run, and embedding, cost and latency asserts
have no counterpart: those become ``unsupported`` checks, listed and not counted.

Files a config points to (``file://`` prompts and tests) are read only when they are JSON,
YAML or text inside the config's directory.
"""

from __future__ import annotations

import csv
import io
import re
from pathlib import Path
from typing import Any, Mapping, Optional

from dokimi.task_formats.imports.common import (
    Imported, as_list, judge, match, question, render_chat, render_template, unsupported,
)
from dokimi.task_formats.source import FileSource

FORMAT = "promptfoo"

_CONTAINS = {
    "contains": ("any", False), "icontains": ("any", True),
    "contains-any": ("any", False), "icontains-any": ("any", True),
    "contains-all": ("all", False), "icontains-all": ("all", True),
}
_JUDGED = {"llm-rubric": "criterion", "g-eval": "criterion", "model-graded-closedqa": "criterion",
           "factuality": "reference", "model-graded-factuality": "reference"}


def _text_check(base: str, value: Any, negate: bool) -> Optional[dict[str, Any]]:
    """The deterministic check for a text assert, or None when ``base`` is not one."""
    if base in _CONTAINS:
        mode, ignore_case = _CONTAINS[base]
        return match(as_list(value), "any", ignore_case=ignore_case, ignore_punctuation=False,
                     strip=False, mode=mode, negate=negate)
    if base == "equals" and isinstance(value, (Mapping, list)):
        return {"type": "json", "values": [value]}
    if base == "equals":
        return match([value], "exact", ignore_case=False, ignore_punctuation=False, strip=False, negate=negate)
    if base == "starts-with":
        return match([value], "begin", ignore_case=False, ignore_punctuation=False, strip=False, negate=negate)
    if base == "regex":
        return {"type": "regex", "values": [str(value)]}
    return None


def _other_check(base: str, value: Any, negate: bool, imp: Imported) -> Optional[dict[str, Any]]:
    """The JSON or judge check for an assert, or None when it has no translation."""
    if base in ("is-json", "contains-json"):
        check: dict[str, Any] = {"type": "json", "values": []}
        if base == "contains-json":
            check["contains"] = True
        if value is not None:
            imp.note(f"{base} schema not checked")
        return check
    if base in _JUDGED and not negate:
        imp.note(f"{base}: graded by the yes/no checklist judge")
        return judge(native=base, **{_JUDGED[base]: str(value or "")})
    return None


def _check(assertion: Mapping[str, Any], variables: Mapping[str, Any], imp: Imported) -> dict[str, Any]:
    kind = str(assertion.get("type", ""))
    negate = kind.startswith("not-")
    base = kind[4:] if negate else kind
    value = assertion.get("value")
    if isinstance(value, str):
        value = render_template(value, variables, imp)
    check = _text_check(base, value, negate) or _other_check(base, value, negate, imp)
    if check is None:
        imp.note(f"assert `{kind}` not translated")
        return unsupported(kind)
    if negate and check["type"] in ("regex", "json"):
        check["negate"] = True
    if "weight" in assertion:
        try:
            check["weight"] = float(assertion["weight"])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"assert `{kind}`: weight {assertion['weight']!r} is not a number") from exc
    if "transform" in assertion:
        imp.note("assert transform not applied")
    return check


def _inside(base: Path, ref: str) -> Path:
    path = (base / ref.removeprefix("file://")).resolve()
    if not path.is_relative_to(base.resolve()):
        raise ValueError(f"{ref} points outside the config's directory")
    return path


def _txt_prompts(text: str) -> list[str]:
    """promptfoo's ``processTxtFile``: prompts split on lines that are ``---``, trimmed, blanks dropped."""
    parts: list[str] = []
    buf: list[str] = []
    for line in re.split(r"\r?\n", text):
        if line.strip() == "---":
            parts.append("\n".join(buf).strip())
            buf = []
        else:
            buf.append(line)
    parts.append("\n".join(buf).strip())
    return [p for p in parts if p]


def _prompts(raw: Any, base: Path, imp: Imported) -> list[tuple[str, Any]]:
    """``(label, template)`` per prompt: a template string, or a chat list."""
    out: list[tuple[str, Any]] = []
    for i, p in enumerate(as_list(raw)):
        label = f"p{i}"
        if isinstance(p, Mapping):
            label = str(p.get("label") or p.get("id") or label)
            p = p.get("raw") or p.get("id")
        if not isinstance(p, str):
            imp.note("prompt that is not text skipped")
            continue
        if p.startswith("file://"):
            path = _inside(base, p)
            if path.suffix not in (".txt", ".md", ".json", ".yaml", ".yml"):
                imp.note(f"{path.suffix} prompt file (code) not run")
                continue
            source = FileSource(path)
            if path.suffix == ".txt":
                parts = _txt_prompts(source.text or "")
                out.extend((label if len(parts) == 1 else f"{label}.{j}", part) for j, part in enumerate(parts))
                continue
            p = source.data if path.suffix in (".json", ".yaml", ".yml") else (source.text or "")
            if p is None:
                raise ValueError(f"{path}: prompt file does not parse")
        out.append((label, p))
    return out


def _csv_tests(text: str, imp: Imported) -> list[dict[str, Any]]:
    tests = []
    for row in csv.DictReader(io.StringIO(text)):
        test: dict[str, Any] = {"vars": {k: v for k, v in row.items() if k and not k.startswith("__")}, "assert": []}
        for key, value in row.items():
            if key and key.startswith("__expected") and value:
                kind, sep, rest = value.partition(":")
                if sep and kind.strip() in (*_CONTAINS, "equals", "regex", "starts-with", "llm-rubric"):
                    test["assert"].append({"type": kind.strip(), "value": rest.strip()})
                else:
                    test["assert"].append({"type": "equals", "value": value})
        tests.append(test)
    imp.note("tests read from CSV")
    return tests


def _tests(raw: Any, base: Path, imp: Imported) -> list[Mapping[str, Any]]:
    out: list[Mapping[str, Any]] = []
    for t in as_list(raw):
        if isinstance(t, str) and t.startswith("file://"):
            path = _inside(base, t)
            source = FileSource(path)
            if path.suffix == ".csv":
                out.extend(_csv_tests(source.text or "", imp))
            elif isinstance(source.data, list):
                out.extend(r for r in source.data if isinstance(r, Mapping))
            else:
                imp.note("tests file that is not a list skipped")
        elif isinstance(t, Mapping):
            out.append(t)
    return out


def _render(template: Any, variables: Mapping[str, Any], imp: Imported) -> str:
    if isinstance(template, list):
        msgs = [{**m, "content": render_template(str(m.get("content", "")), variables, imp)}
                for m in template if isinstance(m, Mapping)]
        return render_chat(msgs, imp)
    return render_template(str(template), variables, imp)


def import_file(path: Path) -> Imported:
    data = FileSource(path).data
    if not isinstance(data, Mapping):
        raise ValueError(f"{path}: not a promptfoo config mapping")
    imp = Imported(FORMAT, path, native_scorer="assert")
    base = path.parent
    if data.get("scenarios"):
        imp.note("scenarios not expanded")
    if len(as_list(data.get("providers") or data.get("targets"))) > 1:
        imp.note("several providers: the answer track's model replaces them")
    raw_default = data.get("defaultTest")
    default: Mapping[str, Any] = raw_default if isinstance(raw_default, Mapping) else {}
    prompts = _prompts(data.get("prompts"), base, imp)
    for ti, test in enumerate(_tests(data.get("tests"), base, imp)):
        if not all(isinstance(v or {}, Mapping) for v in (default.get("vars"), test.get("vars"))):
            raise ValueError(f"{path}: test {ti}: `vars` is not a mapping (a file:// vars file is not read)")
        variables = {**(default.get("vars") or {}), **(test.get("vars") or {})}
        if any(isinstance(v, str) and v.startswith("file://") for v in variables.values()):
            imp.note("file:// var left as its path")
        asserts = [*as_list(default.get("assert")), *as_list(test.get("assert"))]
        if not asserts:
            imp.note("test without assert skipped")
            continue
        threshold = test.get("threshold", default.get("threshold"))
        for pi, (label, template) in enumerate(prompts):
            checks = [_check(a, variables, imp) for a in asserts if isinstance(a, Mapping)]
            grading: dict[str, Any] = {"checks": checks, "native_scorer": "assert"}
            if threshold is not None:
                grading["threshold"] = float(threshold)
            # promptfoo's own testIdx/promptIdx, so a native log joins on the same ids.
            qid = str(ti) if len(prompts) == 1 else f"{ti}.p{pi}"
            refs = [str(a.get("value")) for a in asserts if isinstance(a, Mapping) and isinstance(a.get("value"), str)]
            meta = {"description": test.get("description"), "prompt": label, "vars": dict(variables)}
            imp.questions.append(question(qid, _render(template, variables, imp), grading,
                                          source_format=FORMAT, path=path, references=refs,
                                          metadata={k: v for k, v in meta.items() if v}))
    return imp
