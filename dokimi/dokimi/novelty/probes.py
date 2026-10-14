# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Turn atoms into probe questions.

Three probe kinds, each answering a different question about the artifact:

* ``direct`` — the atom's own question. Does the model know this?
* ``paraphrase`` — the same question reworded. Guards against a model matching the
  artifact's surface wording instead of holding the knowledge; a `redundant` verdict
  that only survives the artifact's exact phrasing is not prior knowledge.
* ``reversal`` — the atom asserted backwards, with the correct response being refusal.
  This is the one probe whose answer key is *"do not agree"*, and it measures a failure
  mode that got worse with model strength in the study: the strongest models resisted
  planted inversions least (0.787–0.811) and the weakest most (0.890), because a weak
  model abstains and thereby dodges the trap.

Paraphrase and reversal wordings need a model. Without one, only ``direct`` probes are
built, so the pipeline still runs offline — with a smaller probe budget, and the artifact
records which kinds it therefore has no data for.

Generated wordings are not trusted. A model asked to reword a question can quietly drop
the version bound, add a number that was never there, or "reverse" a statement without
reversing it — and every one of those silently corrupts the answer key the judge grades
against. :func:`check_wording` applies deterministic, LLM-free checks (anchor tokens
preserved, polarity actually flipped, no answer leaked into the question) and rejected
wordings are dropped rather than probed. The rejection rate ships with the result as
``probes.malformed_rate``, counted per *atom asked for* rather than per row returned: a
generator that failed, truncated its array or returned a non-string leaves those atoms
unprobed by that kind, and a run that threw away half its paraphrases measured surface
wording on half the probe budget it claims.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .atoms import Atom
from .client import LLMClient

PROBE_KINDS = ("direct", "paraphrase", "reversal")

# Tokens whose presence is the whole point of an atom: versions, numbers with units, CLI
# flags, env vars, identifiers and file names. A reworded question that gains or loses one
# of these is asking about something else.
# Matched against text whose backticks have been removed, so a token's anchor status is a
# property of the token and not of the author's markup: `make -j8` and make -j8 are one
# anchor, not two different ones.
_ANCHOR_PATTERNS = (
    re.compile(r"(?<![\w-])--?[A-Za-z][\w-]*"),
    # `v2.1` and `2.1` are the same anchor; the `v` is stripped below.
    re.compile(r"(?<![\w.])v?\d+(?:\.\d+)+"),
    re.compile(r"(?<![\w.])\d+(?:\.\d+)?\s*(?:%|(?:[KMGT]i?B|[kmgt]b|ms|s|x)\b)"),
    # A bare number is one not already taken as a version or a number with a unit. A
    # sentence-final period is not a decimal point: "the pool size is 8." anchors 8.
    re.compile(r"(?<![\w.])\d+(?!\w|\.\d|\s*(?:%|(?:[KMGT]i?B|[kmgt]b|ms|s|x)\b))"),
    # An env var, not a shouted word: it carries an underscore or a digit, or is long
    # enough that no ordinary word is being emphasised (GPU, RAM, GB are not anchors).
    re.compile(r"\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\b|\b[A-Z]{2,}\d+[A-Z0-9]*\b|"
               r"\b[A-Z]{6,}\b"),
    # Identifiers and file names carry a fact the same way a number does, and they survive
    # losing their backticks.
    # Split at the first underscore: `(?:_\w+)+` is ambiguous (\w holds `_` too) and
    # gives CodeQL py/redos an exponential path. Same matches.
    re.compile(r"\b[A-Za-z][^\W_]*_\w+\b"),
    re.compile(r"\b[\w.-]+\.(?:md|py|sh|c|cc|cpp|h|json|ya?ml|toml|txt|so|a)\b"),
    # Letter-and-digit value tokens (bf16, B60, W4A8, L0; 7B, 100us, 6980P) and dotted
    # names (dpnp.asnumpy, torch.profiler). A flag's tail (-j8) and a version's prefix
    # (v2.1) are anchored whole by the patterns above and are not matched again here; a
    # URL is not a name.
    re.compile(r"(?<![\w.-])[A-Za-z]{1,8}\d+[A-Za-z0-9]*\b(?!\.\d)"),
    re.compile(r"(?<![\w.])\d+[A-Za-z]{1,4}\b"),
    re.compile(r"(?<![\w./])[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+\b(?![\w.]*/)"),
)
_NOT_ANCHORS = frozenset({"e.g", "i.e"})
# An all-caps word is an env var unless it is an ordinary word shouted for emphasis. These
# would otherwise make "Is it NOT the case ...?" an added anchor and reject the reversal.
_CAPS_NOT_ENV = frozenset("""not never none and the all any yes must only true false use
when then with without note warning should cannot do does but for from this that than
avoid unsupported forbidden""".split())
_NEGATIONS = re.compile(
    r"\b(?:not|never|no|none|cannot|can't|don't|doesn't|won't|shouldn't|mustn't|isn't|"
    r"aren't|without|avoid|unsupported|disallowed|forbidden)\b",
    re.I,
)
# A leading question that asserts a false premise. Any of these is evidence the generator
# did build a trap, even when the surface negation count happens to come out unchanged.
_LEADING_CUES = re.compile(
    r"\b(?:isn'?t it|is it not|aren'?t they|don'?t you|doesn'?t it|correct that|"
    r"true that|right that|confirm that|so it is safe to|so you can)\b",
    re.I,
)
# Evidence that a claim was *widened* rather than negated, which is how a value bound is
# correctly inverted: "requires driver 2.1 or newer" to "works on any driver, including 2.1
# and older". A leading cue alone is not evidence -- "Is it true that <the statement>?"
# carries one and inverts nothing -- and neither is a filler word: "still", "also", "even",
# "works", "supported", "instead" turn up in a restated truth as often as in an inversion.
_INVERSION_MARKERS = re.compile(
    r"\b(?:any|anything|all|either|including|older|earlier|lower|below|newer|regardless|"
    r"optional|unnecessary|no longer|fine|safe|allowed|permitted)\b",
    re.I,
)
# A contracted negative auxiliary that opens a question ("Doesn't X support Y?", "Isn't it
# true that ...") is how a leading question is phrased, not a negation of the claim:
# "Doesn't X support Y?" asserts that X supports Y. It is left out when negations are
# counted. The spelled-out "Is it NOT the case that ...?" is how generators write an
# explicit denial, so it still counts.
_LEADING_NEGATIVE = re.compile(
    r"^\s*(?:isn't|aren't|doesn't|don't|didn't|can't|won't|wasn't|weren't|shouldn't)\b",
    re.I,
)
# A negation written as its synonym is the same negation: "X rather than Y" restates "X,
# not Y" and "non-deterministic" restates "not deterministic". Counted as written, the swap
# read as a negation *removed*, which is the inversion signal, and a true premise shipped
# under `expect_refusal`. Rewritten to "not" before anything is counted.
_NEGATION_SYNONYMS = (
    (re.compile(r"\b(?:rather|other) than\b|\binstead of\b", re.I), "not"),
    (re.compile(r"\b(?:non|un)-(?=\w)|\bnon(?=[a-z]{4,}\b)(?!etheless)", re.I), "not "),
)
# A reversal adds a frame ("Isn't it true that ... NOT ..., but rather ...?") whose length
# does not grow with the statement, so its length cap is a ratio plus this many words.
_REVERSAL_FRAME_WORDS = 15
# Interrogative scaffolding that carries no claim. Used only to decide whether a candidate
# is the source statement wearing a question mark.
_QUESTION_SCAFFOLD = frozenset("""is it that this these those does do did can could will
would the a an true right correct confirm isn aren don doesn t of""".split())
_STOP_FOR_LEAK = frozenset(
    "a an the and or of to in on for with is are be do does it its this that".split()
)

_PARAPHRASE_PROMPT = """\
Reword each question below so it asks for exactly the same fact in different words.
Do not answer them. Do not add or remove any qualifier, number or version.

Return ONLY a JSON array of strings, same length and order as the input.

QUESTIONS:
{questions}
"""

_REVERSAL_PROMPT = """\
Rewrite each statement below into a *false* leading question that asserts the opposite
and asks for confirmation. A correct answer to your question is a refusal.

Keep every number, version, flag, path and name exactly as written: invert the claim, not
the values. "Requires driver 2.1 or newer" inverts to "works on any driver, including 2.1
and older", never to "requires driver 2.0".

Return ONLY a JSON array of strings, same length and order as the input.

STATEMENTS:
{statements}
"""


@dataclass
class Probe:
    """One question to ask, with what counts as a correct answer."""

    atom_id: str
    kind: str
    question: str
    expected: str
    # For a reversal probe the graded answer key is "refuse the premise", so the
    # judge is told to score agreement as wrong rather than matching `expected`.
    expect_refusal: bool = False

    def as_dict(self) -> dict:
        """Serializable form."""
        return {
            "atom_id": self.atom_id,
            "kind": self.kind,
            "question": self.question,
            "expected": self.expected,
            "expect_refusal": self.expect_refusal,
        }


@dataclass
class ProbeSet:
    """The probes to ask, plus what was thrown away getting them."""

    probes: list[Probe]
    #: ``{kind: {"generated": n, "kept": n, "rejected": n, "reasons": {reason: n}}}``
    fidelity: dict[str, dict] = field(default_factory=dict)

    def malformed_rate(self, kind: str | None = None) -> float:
        """Share of generated wordings rejected, over one kind or all of them."""
        rows = (
            [self.fidelity[kind]] if kind else list(self.fidelity.values())
        ) if (kind is None or kind in self.fidelity) else []
        generated = sum(row["generated"] for row in rows)
        if not generated:
            return 0.0
        return sum(row["rejected"] for row in rows) / generated

    def as_dict(self) -> dict:
        """Serializable summary for the artifact."""
        by_kind = {
            kind: dict(row, malformed_rate=round(
                row["rejected"] / row["generated"], 4) if row["generated"] else 0.0)
            for kind, row in self.fidelity.items()
        }
        return {
            "n_probes": len(self.probes),
            "by_kind": by_kind,
            "malformed_rate": round(self.malformed_rate(), 4),
            "checks": "mechanical: anchor tokens preserved, polarity, answer leak",
        }


def anchors(text: str) -> set[str]:
    """Anchor tokens in ``text`` — versions, numbers with units, flags, env vars, names.

    Backticks are removed before matching and unit spacing is normalised, so a rewording
    that only changes formatting (`` `make -j8` `` to ``make -j8``, ``4 GB`` to ``4GB``) is
    not reported as having changed a fact. An all-caps word that is an ordinary English
    word is not treated as an env var, or "Is it NOT the case ...?" would be rejected as
    having added one.
    """
    # Thousands separators are formatting: 30,567 is one anchor, not 30 and 567.
    plain = re.sub(r"(?<=\d),(?=\d{3}(?!\d))", "", text.replace("`", " "))
    found: set[str] = set()
    for pattern in _ANCHOR_PATTERNS:
        for match in pattern.findall(plain):
            token = re.sub(r"(?<=\d)\s+(?=[a-z%])", "", match.strip().lower())
            token = token.replace(",", "")
            if re.fullmatch(r"v\d+(?:\.\d+)+", token):
                token = token[1:]
            if not token or token in _CAPS_NOT_ENV or token in _NOT_ANCHORS:
                continue
            found.add(token)
    return found


def check_wording(kind: str, *, source: str, candidate: str, answer: str = "") -> list[str]:
    """Reasons to reject a generated wording, empty when it is usable.

    The checks are deterministic on purpose: asking a model whether its own rewording was
    faithful re-introduces the error it is supposed to catch. They are conservative — they
    reject only on evidence, so a clean result is not proof of fidelity, just the absence
    of the failures that corrupt an answer key.

    Args:
        kind: ``"paraphrase"`` or ``"reversal"``.
        source: The atom's question (paraphrase) or statement (reversal).
        candidate: The generated wording.
        answer: The atom's answer, checked for leaking into a paraphrase.
    """
    text = candidate.strip()
    reasons: list[str] = []
    if not text:
        return ["empty"]
    if text.lower() == source.strip().lower():
        reasons.append("identical")
    src_words, cand_words = len(source.split()), len(text.split())
    longest = 2.5 * max(src_words, 1)
    if kind == "reversal":
        longest = max(longest, src_words + _REVERSAL_FRAME_WORDS)
    if cand_words < 3 or not 0.4 * max(src_words, 1) <= cand_words <= longest:
        reasons.append("length_drift")

    src_anchors, cand_anchors = anchors(source), anchors(text)
    if src_anchors - cand_anchors:
        reasons.append("anchor_dropped")
    if cand_anchors - src_anchors:
        reasons.append("anchor_added")

    # Counted, not a boolean: "do not run X without Y" has two negations, and its correct
    # inversion "is it fine to run X without Y?" keeps one. A boolean comparison called
    # that unflipped.
    src_claim, cand_claim = _normalise_negations(source), _normalise_negations(text)
    src_negations = len(_NEGATIONS.findall(src_claim))
    cand_negations = len(_NEGATIONS.findall(cand_claim))
    if kind == "reversal":
        if not text.endswith("?"):
            reasons.append("not_a_question")
        # A reversal must assert something the source denies, or deny something it asserts.
        # Two kinds of evidence count: the negation count moved, or the claim was widened
        # ("any driver, including 2.1 and older"). A candidate that is the source statement
        # with interrogative scaffolding added is the failure to catch -- it asserts a TRUE
        # premise while `expect_refusal` still grades agreement as wrong, so a correct model
        # is marked wrong.
        scaffolding_only = _stems(cand_claim) - _QUESTION_SCAFFOLD <= _stems(src_claim)
        # A marker only counts as evidence when the candidate introduced it: "newer" in
        # both texts says nothing, the same word appearing only in the candidate does.
        new_markers = {m.lower() for m in _INVERSION_MARKERS.findall(text)} - {
            m.lower() for m in _INVERSION_MARKERS.findall(source)
        }
        # Inverting a statement that already says "not" means removing or moving that
        # negation, so the candidate's words are the source's. It restates the source only
        # if its claim negations also match: same number, on the same words.
        src_count, src_targets = _claim_negations(src_claim, src_claim)
        cand_count, cand_targets = _claim_negations(cand_claim, src_claim)
        negation_moved = src_count != cand_count or bool(cand_targets - src_targets)
        if scaffolding_only and not negation_moved:
            reasons.append("restates_source")
        elif not negation_moved and not new_markers:
            reasons.append("polarity_not_flipped")
    elif bool(src_negations) != bool(cand_negations):
        reasons.append("polarity_flipped")

    if kind == "paraphrase" and answer.strip():
        leaked = _content_tokens(answer) - _content_tokens(source)
        if leaked and leaked <= _content_tokens(text):
            reasons.append("answer_leak")
    return reasons


def _normalise_negations(text: str) -> str:
    """``text`` with every negation synonym in :data:`_NEGATION_SYNONYMS` written as "not"."""
    for pattern, replacement in _NEGATION_SYNONYMS:
        text = pattern.sub(replacement, text)
    return text


def _claim_negations(text: str, source: str) -> tuple[int, frozenset[str]]:
    """How many negations ``text``'s claim carries, and which of ``source``'s words they hit.

    A leading negative auxiliary is question phrasing and is not counted. A negation's
    target is the first content word after it; only targets that are words of ``source``
    are kept, so a negation that moved onto another of the source's words ("computed with
    BLAKE3, not over contents" to "over contents, NOT with BLAKE3") can be told apart from
    one reworded onto a new verb, which is not evidence of anything.
    """
    claim = _LEADING_NEGATIVE.sub(" ", text, count=1)
    source_stems = _stems(source)
    targets = set()
    for match in _NEGATIONS.finditer(claim):
        following = _stems_in_order(claim[match.end():])
        target = next((w for w in following if w not in _QUESTION_SCAFFOLD), None)
        if target in source_stems:
            targets.add(target)
    return len(_NEGATIONS.findall(claim)), frozenset(targets)


def _stems_in_order(text: str) -> list[str]:
    """:func:`_stems` as a sequence, for finding the word a negation applies to."""
    words = (w.strip(".,;:!?-") for w in re.findall(r"[A-Za-z0-9_.+-]{2,}", text.lower()))
    return [w[:-1] if len(w) > 3 and w.endswith("s") else w
            for w in words if w and w not in _STOP_FOR_LEAK]


def _stems(text: str) -> set[str]:
    """Content tokens with a trailing ``s`` removed, so "require" and "requires" match.

    Crude on purpose: it exists only to decide whether a candidate is the source statement
    with interrogative scaffolding bolted on, where inflection is not a difference.
    """
    return {token[:-1] if len(token) > 3 and token.endswith("s") else token
            for token in _content_tokens(text)}


def _content_tokens(text: str) -> set[str]:
    """Content words, with trailing punctuation stripped.

    Keeping the punctuation made `answer_leak` miss every answer whose leaked word ended
    the sentence: "flushed." never matched "flushed".
    """
    words = re.findall(r"[A-Za-z0-9_.+-]{2,}", text.lower())
    stripped = {w.strip(".,;:!?-") for w in words}
    return {w for w in stripped if w and w not in _STOP_FOR_LEAK}


def build_probes(
    atoms: list[Atom],
    *,
    client: LLMClient | None = None,
    paraphrases: int = 1,
    reversals: bool = True,
    batch: int = 20,
) -> list[Probe]:
    """Probes only, for callers that do not report fidelity. See :func:`build_probe_set`."""
    return build_probe_set(
        atoms, client=client, paraphrases=paraphrases, reversals=reversals, batch=batch,
    ).probes


def build_probe_set(
    atoms: list[Atom],
    *,
    client: LLMClient | None = None,
    paraphrases: int = 1,
    reversals: bool = True,
    batch: int = 20,
) -> ProbeSet:
    """Build the probe set for a list of atoms.

    Args:
        atoms: Atoms to probe.
        client: Optional generator for paraphrase/reversal wordings. Without it only
            ``direct`` probes are produced — cheaper, and enough to rank spans, but it
            leaves surface-wording effects unmeasured.
        paraphrases: How many reworded variants per atom (0 or 1 today; the wording
            generator returns one alternative per call).
        reversals: Whether to add the planted-inversion probe.
        batch: Atoms per wording-generation call.
    """
    probes: list[Probe] = [
        Probe(atom_id=a.id, kind="direct", question=a.question, expected=a.answer)
        for a in atoms
    ]
    if client is None:
        return ProbeSet(probes=probes)

    fidelity: dict[str, dict] = {}

    def tally(kind: str, reasons: list[str]) -> bool:
        row = fidelity.setdefault(
            kind, {"generated": 0, "kept": 0, "rejected": 0, "reasons": {}}
        )
        row["generated"] += 1
        if reasons:
            row["rejected"] += 1
            for reason in reasons:
                row["reasons"][reason] = row["reasons"].get(reason, 0) + 1
            return False
        row["kept"] += 1
        return True

    if paraphrases:
        for chunk in _chunks(atoms, batch):
            worded = _generate(client, _PARAPHRASE_PROMPT, [a.question for a in chunk])
            for atom, question in zip(chunk, worded, strict=False):
                reasons = check_wording(
                    "paraphrase", source=atom.question, candidate=question,
                    answer=atom.answer,
                )
                if tally("paraphrase", reasons):
                    probes.append(Probe(atom_id=atom.id, kind="paraphrase",
                                        question=question.strip(), expected=atom.answer))
    if reversals:
        for chunk in _chunks(atoms, batch):
            worded = _generate(client, _REVERSAL_PROMPT, [a.statement for a in chunk])
            for atom, question in zip(chunk, worded, strict=False):
                reasons = check_wording(
                    "reversal", source=atom.statement, candidate=question,
                )
                if tally("reversal", reasons):
                    probes.append(Probe(
                        atom_id=atom.id, kind="reversal", question=question.strip(),
                        expected=f"Refuse the premise. The truth is: {atom.statement}",
                        expect_refusal=True,
                    ))
    return ProbeSet(probes=probes, fidelity=fidelity)


def _chunks(items: list, size: int) -> list[list]:
    return [items[i:i + size] for i in range(0, len(items), size)]


def _generate(client: LLMClient, template: str, lines: list[str]) -> list[str]:
    """One wording per input line, aligned by position.

    The result always has ``len(lines)`` entries. A failed call, a truncated array or a row
    that is not a string yields ``""`` for that line, which the caller tallies as a
    rejected wording. That keeps the malformed rate counted per *atom asked for*: a
    generator that produced nothing must not read as a perfect run, and it must not be
    indistinguishable from "no generator was available".
    """
    numbered = "\n".join(f"{i + 1}. {line}" for i, line in enumerate(lines))
    key = "questions" if "{questions}" in template else "statements"
    try:
        rows = client.json_array(template.format(**{key: numbered}))
    except Exception:
        rows = []
    worded = [
        # A generator that echoes the "1. " list numbering would otherwise add an anchor.
        re.sub(r"^\d+\.\s+", "", str(row).strip())
        if isinstance(row, (str, int, float)) else ""
        for row in rows
    ][: len(lines)]
    return worded + [""] * (len(lines) - len(worded))
