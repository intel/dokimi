# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""What every importer returns, and the pieces they share (chat rendering, check builders)."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

#: The answerer sends an imported question's text as the whole prompt: the source format's
#: prompt already says how to answer ("reply with the letter", "answer only with ..."), and
#: the stock "be specific and include code examples" wrapper defeats exact-match graders.
PROMPT_VERBATIM = "verbatim"


@dataclass
class Imported:
    """An answer-only eval translated into dokimi questions."""

    source_format: str
    path: Path
    questions: list[dict[str, Any]] = field(default_factory=list)
    #: What the translation dropped or approximated, counted per kind (the import report).
    lossy: Counter[str] = field(default_factory=Counter)
    native_scorer: Optional[str] = None

    def note(self, what: str, n: int = 1) -> None:
        self.lossy[what] += n

    def artifact(self) -> dict[str, Any]:
        """The questions.v2 payload (unsaved)."""
        return {
            "description": f"Imported from {self.source_format} {self.path.name}",
            "total_questions": len(self.questions),
            "questions": self.questions,
        }

    def report(self) -> dict[str, Any]:
        return {
            "source_format": self.source_format,
            "path": str(self.path),
            "questions": len(self.questions),
            "native_scorer": self.native_scorer,
            "checks": dict(sorted(Counter(
                c["type"] for q in self.questions for c in q["grading"]["checks"]).items())),
            "lossy": dict(sorted(self.lossy.items())),
        }


def question(qid: Any, text: str, grading: dict[str, Any], *, source_format: str, path: Path,
             references: Iterable[str] = (), metadata: Optional[Mapping[str, Any]] = None) -> dict[str, Any]:
    """One questions.v2 record for an imported item."""
    refs = [str(r) for r in references if str(r).strip()]
    record: dict[str, Any] = {
        "id": str(qid),
        "question": text,
        "prompt_style": PROMPT_VERBATIM,
        "source_type": "manual",
        "grading": {"source_format": source_format, **grading},
        "metadata": {"source_format": source_format, "source_path": str(path), **(metadata or {})},
    }
    if refs:
        # The stock judge and `eval score checklist` read reference points from here.
        record["expected_topics"] = refs
    return record


def as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return list(value) if isinstance(value, (list, tuple)) else [value]


def message_text(content: Any) -> str:
    """A chat message's text; non-text parts (images, files) are dropped by the caller's note."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(p.get("text", "")) for p in content if isinstance(p, Mapping) and p.get("type", "text") == "text")
    return str(content)


def render_chat(messages: Sequence[Mapping[str, Any]], imp: Imported) -> str:
    """A chat input as one prompt: a lone user turn verbatim, otherwise role-labelled turns.

    The answer track sends one user message, so a system prompt or few-shot turns are folded
    into it. That is the lossy step, counted as ``chat flattened to one prompt``.
    """
    if any(not isinstance(m.get("content"), str) for m in messages):
        imp.note("non-text message parts dropped")
    if len(messages) == 1 and messages[0].get("role") == "user":
        return message_text(messages[0].get("content"))
    imp.note("chat flattened to one prompt")
    return "\n\n".join(f"[{m.get('role', 'user')}]\n{message_text(m.get('content'))}" for m in messages)


def render_input(value: Any, imp: Imported) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list) and all(isinstance(m, Mapping) for m in value):
        return render_chat(list(value), imp)
    imp.note("input neither text nor chat")
    return str(value)


# --- check builders ---------------------------------------------------------------------------


def match(values: Iterable[Any], location: str, *, ignore_case: bool = True, ignore_punctuation: bool = True,
          strip: bool = True, numeric: bool = False, mode: str = "any", negate: bool = False) -> dict[str, Any]:
    check: dict[str, Any] = {"type": "match", "values": [str(v) for v in values], "location": location,
                             "ignore_case": ignore_case, "ignore_punctuation": ignore_punctuation,
                             "strip": strip}
    if numeric:
        check["numeric"] = True
    if mode != "any":
        check["mode"] = mode
    if negate:
        check["negate"] = True
    return check


def judge(*, criterion: str = "", reference: str = "", native: str = "") -> dict[str, Any]:
    check: dict[str, Any] = {"type": "judge"}
    if criterion:
        check["criterion"] = criterion
    if reference:
        check["reference"] = reference
    if native:
        check["native"] = native
    return check


def unsupported(native: str) -> dict[str, Any]:
    return {"type": "unsupported", "native": native}


#: Inspect's single-answer multiple-choice prompt (``inspect_ai.solver._multiple_choice``),
#: so an imported MCQ is asked the way the native solver asks it.
MCQ_TEMPLATE = (
    "Answer the following multiple choice question. The entire content of your response should "
    "be of the following format: 'ANSWER: $LETTER' (without quotes) where LETTER is one of "
    "{letters}.\n\n{question}\n\n{choices}"
)
#: Its ``multiple_choice(multiple_correct=True)`` prompt.
MCQ_MULTIPLE_TEMPLATE = (
    "Answer the following multiple choice question where multiple answers may be correct. The "
    "entire content of your response should be of the following format: 'ANSWER: $LETTERS' "
    "(without quotes) where LETTERS is one or more of {letters}.\n\n{question}\n\n{choices}"
)


def letters(n: int) -> list[str]:
    return [chr(ord("A") + i) for i in range(n)]


def render_mcq(text: str, choices: list[str], multiple: bool = False) -> str:
    keys = letters(len(choices))
    body = "\n".join(f"{k}) {c}" for k, c in zip(keys, choices, strict=True))
    template = MCQ_MULTIPLE_TEMPLATE if multiple else MCQ_TEMPLATE
    return template.format(letters=",".join(keys), question=text, choices=body)


def choice_targets(targets: list[str], choices: list[str]) -> list[str]:
    """Target letters: letters as given, a choice's text mapped to its letter."""
    keys = letters(len(choices))
    out = []
    for t in targets:
        t = str(t).strip()
        if t.upper() in keys:
            out.append(t.upper())
        elif t in choices:
            out.append(keys[choices.index(t)])
    return out


_VAR = re.compile(r"\{\{\s*([A-Za-z_][\w.]*)\s*(\|[^}]*)?\}\}")


def render_template(template: str, variables: Mapping[str, Any], imp: Imported) -> str:
    """``{{ var }}`` substitution: the Nunjucks/Jinja subset promptfoo prompts mostly use.

    Filters (``{{ x | upper }}``), tags (``{% ... %}``) and unknown variables are left as
    written and counted.
    """
    if "{%" in template:
        imp.note("template tags ({% %}) left unrendered")

    def sub(m: re.Match[str]) -> str:
        name, flt = m.group(1), m.group(2)
        value: Any = variables
        for part in name.split("."):
            value = value.get(part) if isinstance(value, Mapping) else None
        if value is None or flt:
            imp.note("template filter or unknown variable left unrendered")
            return m.group(0)
        return value if isinstance(value, str) else str(value)

    return _VAR.sub(sub, template)
