# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Research prototype: false claims — answer sentences that contradict documented atoms.

The novelty layer asks a model an atom's *question* and grades the reply against the
atom's *answer*. This module runs the other way round: it takes an answer an agent has
already given (to any user question) and asks, for each sentence of it, whether a
documented atom says the opposite. It is offline over stored inputs — a saved answer and
a saved atoms record — and calls a model only through an injected ``judge`` callable.

Two passes:

1. **Pairing** (lexical, no model). Each answer sentence is paired with the atoms whose
   statements share enough content words or a distinctive token (a flag, a version, an
   identifier). Unpaired sentences are never judged; they are counted as ``unchecked``,
   because "no documented atom about it" is not "true".
2. **Verdict.** Without a judge, a pair is flagged only on a lexical signal — the same
   anchor with a different number/version/flag, or one side negated and the other not —
   and labelled ``lexical``. With a judge, each pair gets ``contradicts`` / ``supports`` /
   ``unrelated`` and a verbatim quote; a quote that is not a substring of the sentence is
   dropped and the pair is reported ``unverified_quote`` rather than trusted.

See ``docs/proposals/false-claims.md`` for the method and its validity risks.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field

from .atoms import Atom

JUDGE_VERDICTS = ("contradicts", "supports", "unrelated")

JUDGE_PROMPT = """\
A DOCUMENTED statement about a product and one SENTENCE from an assistant's answer follow.
Does the sentence contradict the documented statement? Only a direct conflict counts: a
different value, version, flag or default, or the opposite polarity. Omission, vagueness
or a different topic is not a contradiction.

DOCUMENTED: {statement}
SENTENCE: {sentence}

Return ONLY JSON: {{"verdict": "contradicts|supports|unrelated",
"quote": "<the exact words of SENTENCE that conflict, or empty>"}}
"""

_WORD = re.compile(r"--?[A-Za-z][A-Za-z0-9_-]*|[A-Za-z0-9_][A-Za-z0-9_.\-/]*")
# A value: a version, a number with optional unit, or a CLI flag.
_VALUE = re.compile(r"(?<![\w.-])(?:--?[a-z][\w-]*|[+-]?v?\d+(?:\.\d+)*%?)(?![\w])", re.I)
_NEGATION = re.compile(
    r"\b(not|never|no|cannot|can't|don't|doesn't|isn't|unsupported|deprecated|without)\b", re.I)
# A sentence ends at . ! ? followed by whitespace or the end, or at a newline; "3.0" and
# "a.b" do not end one.
_SENTENCE = re.compile(r"[^\n]+?(?:[.!?](?=\s|$)|$)", re.M)
_STOP = frozenset(
    "a an the is are was were be been to of in on for and or with by as at it this that "
    "these those you your can will may must should use uses used from into not no do does "
    "if when then than also only any all its which what how".split())


@dataclass(frozen=True)
class Sentence:
    """One answer sentence and its character offsets in the answer."""

    text: str
    start: int
    end: int


@dataclass(frozen=True)
class FalseClaim:
    """An answer sentence flagged against one atom."""

    atom_id: str
    statement: str
    span_ids: tuple[str, ...]
    sentence: str
    quote: str
    how: str  # "judge", "lexical:value", "lexical:polarity"

    def as_dict(self) -> dict:
        return {"atom_id": self.atom_id, "statement": self.statement,
                "span_ids": list(self.span_ids), "sentence": self.sentence,
                "quote": self.quote, "how": self.how}


@dataclass
class FalseClaimsReport:
    """What was checked, what was flagged, and what could not be checked."""

    claims: list[FalseClaim] = field(default_factory=list)
    n_sentences: int = 0
    n_unchecked: int = 0  # sentences no atom paired with
    n_pairs: int = 0
    n_unverified_quote: int = 0
    n_judge_errors: int = 0

    def as_dict(self) -> dict:
        return {"claims": [c.as_dict() for c in self.claims], "n_sentences": self.n_sentences,
                "n_unchecked": self.n_unchecked, "n_pairs": self.n_pairs,
                "n_unverified_quote": self.n_unverified_quote,
                "n_judge_errors": self.n_judge_errors}


def split_sentences(answer: str) -> list[Sentence]:
    """Sentences of ``answer`` with offsets; a final line ``confidence: x`` is not a claim."""
    out = []
    for m in _SENTENCE.finditer(answer or ""):
        text = m.group(0).strip()
        if not text or text.lower().startswith("confidence:") or not _WORD.search(text):
            continue
        start = m.start() + m.group(0).index(text)
        out.append(Sentence(text, start, start + len(text)))
    return out


def _content(text: str) -> set[str]:
    """Alphabetic words, with flags and identifiers split into their parts.

    ``--use-accel`` and ``--enable-accel`` share ``accel``: a sentence that names the wrong
    flag must still pair with the atom that names the right one. Numbers are left out;
    they are what a contradiction changes, so they cannot be what pairs it.
    """
    parts = {p.lower() for w in _WORD.findall(text) for p in re.split(r"[-/._]+", w)}
    return {p for p in parts if p.isalpha()} - _STOP


def _values(text: str) -> set[str]:
    return {v.lower() for v in _VALUE.findall(text)}


def _distinctive(text: str) -> set[str]:
    """Tokens that name one thing: flags, dotted/underscored identifiers, CamelCase."""
    return {w.lower() for w in (token.rstrip(".") for token in _WORD.findall(text))
            if w.startswith("-") or "_" in w or re.search(r"[a-z][A-Z]", w)
            or re.fullmatch(r"[A-Za-z]+\.[A-Za-z_.]+", w)}


def pair_atoms(sentence: str, atoms: list[Atom], *, min_overlap: float = 0.3) -> list[Atom]:
    """Atoms the sentence is plausibly *about*: a shared distinctive token, or word overlap.

    Overlap is the share of the atom's content words the sentence contains, so a long
    answer sentence is not penalised for saying more; it must also be at least two words
    (one for a one-word atom), or a short atom pairs with every sentence naming the product.
    """
    words, ident = _content(sentence), _distinctive(sentence)
    paired = []
    for atom in atoms:
        anchor = _content(atom.statement)
        if not anchor:
            continue
        common = len(anchor & words)
        if (_distinctive(atom.statement) & ident
                or (common >= min(2, len(anchor)) and common / len(anchor) >= min_overlap)):
            paired.append(atom)
    return paired


def lexical_conflict(statement: str, sentence: str) -> tuple[str, str] | None:
    """``(how, quote)`` when the pair shows a cheap conflict signal, else ``None``.

    * value: both name values, share none, and the sentence's value is of the same shape
      (both versions/numbers, or both flags) — "requires 2.1" vs "requires 3.0".
    * polarity: exactly one side carries a negation.
    These are candidates for a judge, not findings; precision is low by construction.
    """
    a, b = _values(statement), _values(sentence)
    if a and b and not (a & b):
        flags = tuple(any(re.match(r"--?[a-z]", value) for value in values) for values in (a, b))
        if flags[0] == flags[1]:
            shapes = {("." in value, bool(re.match(r"--?[a-z]", value))) for value in a}
            quotes = [m.group(0) for m in _VALUE.finditer(sentence)]
            quote = next((raw for raw in quotes
                          if ("." in raw, bool(re.match(r"--?[a-z]", raw, re.I))) in shapes), quotes[0])
            return "lexical:value", quote
    neg_a, neg_b = _NEGATION.search(statement), _NEGATION.search(sentence)
    if bool(neg_a) != bool(neg_b):
        # When the sentence drops the atom's negation there is no word to quote: the
        # whole sentence is the quote.
        return "lexical:polarity", (neg_b.group(0) if neg_b else sentence)
    return None


def find_false_claims(
    answer: str,
    atoms: list[Atom],
    *,
    judge: Callable[[str], dict] | None = None,
    min_overlap: float = 0.3,
) -> FalseClaimsReport:
    """Flag answer sentences that contradict documented atoms.

    Args:
        answer: The agent's answer as stored.
        atoms: Atoms of the product's documentation (``load_atoms``). Atoms whose
            faithfulness check found them ``distorted`` or ``unsupported`` are skipped:
            a contradiction with a wrong atom is not a false claim.
        judge: ``prompt -> {"verdict", "quote"}``, e.g. ``LLMClient.json_object``. When
            ``None`` only lexical signals are reported.
        min_overlap: Share of an atom's content words a sentence must contain to pair.
    """
    usable = [a for a in atoms if a.faithfulness in ("supported", "unchecked")]
    report = FalseClaimsReport()
    for sent in split_sentences(answer):
        report.n_sentences += 1
        paired = pair_atoms(sent.text, usable, min_overlap=min_overlap)
        if not paired:
            report.n_unchecked += 1
            continue
        for atom in paired:
            report.n_pairs += 1
            claim = _judged(atom, sent.text, judge, report) if judge else _lexical(atom, sent.text)
            if claim:
                report.claims.append(claim)
    return report


def _claim(atom: Atom, sentence: str, quote: str, how: str) -> FalseClaim:
    return FalseClaim(atom.id, atom.statement, atom.span_ids, sentence, quote, how)


def _lexical(atom: Atom, sentence: str) -> FalseClaim | None:
    hit = lexical_conflict(atom.statement, sentence)
    return _claim(atom, sentence, hit[1], hit[0]) if hit else None


def _judged(atom: Atom, sentence: str, judge: Callable[[str], dict],
            report: FalseClaimsReport) -> FalseClaim | None:
    try:
        reply = judge(JUDGE_PROMPT.format(statement=atom.statement, sentence=sentence))
    except Exception:  # noqa: BLE001 - a failed call is counted, never read as "no conflict"
        report.n_judge_errors += 1
        return None
    verdict = reply.get("verdict") if isinstance(reply, dict) else None
    if verdict not in JUDGE_VERDICTS:
        report.n_judge_errors += 1
        return None
    if verdict != "contradicts":
        return None
    raw = reply.get("quote")
    quote = raw.strip() if isinstance(raw, str) else ""
    if not quote or quote not in sentence:
        report.n_unverified_quote += 1
        return None
    return _claim(atom, sentence, quote, "judge")
