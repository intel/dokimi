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
   statements share enough content words or a distinctive flag or identifier.
   Unpaired sentences are never judged; they are counted as ``unchecked``,
   because "no documented atom about it" is not "true".
2. **Verdict.** Without a judge, a pair is flagged only on a lexical signal — the same
   anchor with a different number/version/flag, or one side negated and the other not —
   and labelled ``lexical``. With a judge, each pair gets ``contradicts`` / ``supports`` /
   ``unrelated`` and a verbatim quote; a quote that is not a substring of the sentence is
   dropped and the pair is reported ``unverified_quote`` rather than trusted.

"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal

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
# Common compact duration and storage units; arbitrary alphanumeric words are identifiers.
_DURATION_EXPONENT = {
    "ns": -9, "nanosecond": -9, "nanoseconds": -9,
    "us": -6, "µs": -6, "μs": -6, "microsecond": -6, "microseconds": -6,
    "ms": -3, "millisecond": -3, "milliseconds": -3,
    "s": 0, "second": 0, "seconds": 0,
}
_STORAGE_UNITS = r"(?:[KMGT]i?B|kB)(?:/s)?"
_UNITS = "|".join((*_DURATION_EXPONENT, _STORAGE_UNITS, r"(?:[kMG])?Hz", "%"))
_QUANTITY = r"[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
_VALUE = re.compile(
    r"(?<![\w.-])(?:"
    r"(?P<flag>--?[A-Za-z][\w-]*)"
    rf"|(?P<quantity>{_QUANTITY})[ \t]*(?P<unit>{_UNITS})(?!/)"
    r"|(?P<number>[+-]?[vV]?\d+(?:\.\d+)*))(?!\w)"
)
_DOTTED_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+")
_NEGATION = re.compile(
    r"\b(not|never|no|cannot|can't|don't|doesn't|isn't|unsupported|deprecated|without)\b", re.I)
# A sentence ends at . ! ? followed by whitespace or the end, or at a newline; "3.0" and
# "a.b" do not end one.
_SENTENCE = re.compile(r"[^\n]+?(?:(?:(?<!(?i:e\.g|i\.e))\.|[!?])(?=\s|$)|$)", re.M)
_ABBREVIATION = re.compile(r"(?<!\w)(?:e\.g|i\.e)\.(?!\w)", re.I)
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
    parts = {p.lower() for w in _WORD.findall(_ABBREVIATION.sub(" ", text)) for p in re.split(r"[-/._]+", w)}
    return {p for p in parts if p.isalpha()} - _STOP


def _value_key(match: re.Match[str]) -> str | tuple[Decimal, str]:
    if flag := match["flag"]:
        return flag
    if unit := match["unit"]:
        quantity = Decimal(match["quantity"].replace(",", ""))
        if unit in _DURATION_EXPONENT:
            sign, digits, exponent = quantity.as_tuple()
            assert isinstance(exponent, int)
            return Decimal((sign, digits, exponent + _DURATION_EXPONENT[unit])), "s"
        return quantity, unit
    return re.sub(r"^([+-]?)v(?=\d)", r"\1", match["number"].lower()).removeprefix("+")


def _value_shape(match: re.Match[str]) -> tuple[bool, str | None]:
    unit = match["unit"]
    if unit:
        return False, "s" if unit in _DURATION_EXPONENT else unit
    return bool(match["number"] and "." in match["number"]), "flag" if match["flag"] else None


def _distinctive(text: str) -> set[str]:
    """Flags and identifiers that can anchor pairing, even when their case changes."""
    words = (token.rstrip(".") for token in _WORD.findall(_ABBREVIATION.sub(" ", text)))
    return {w.lower() for w in words
            if w.startswith("-") or "_" in w or _DOTTED_IDENTIFIER.fullmatch(w)}

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
    documented, proposed = list(_VALUE.finditer(statement)), list(_VALUE.finditer(sentence))
    if documented and proposed and not ({_value_key(m) for m in documented} & {_value_key(m) for m in proposed}):
        matching_shapes = {_value_shape(m) for m in documented}
        for match in proposed:
            if _value_shape(match) in matching_shapes:
                return "lexical:value", match.group(0)
        # Retain number-to-version comparisons when neither side uses flags or units.
        if all(match["number"] for match in documented + proposed):
            return "lexical:value", proposed[0].group(0)
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
