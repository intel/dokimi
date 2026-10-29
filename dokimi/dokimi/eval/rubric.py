# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The scoring rubric the single judge and the panel share.

One calibration scale for :mod:`dokimi.eval.judge` and :mod:`dokimi.eval.panel`,
so their numbers mean the same thing and can be compared: the score anchors, the
numeric-claims criterion with its enumeration step and output slot, the rule that
holds the prompt's wrong-figure cap in Python, and the reminder appended to a
re-drawn prompt. Both scorers import it; it imports neither.
"""

from __future__ import annotations

import re
from typing import Any, Callable, Dict, Optional

# ── Calibration anchors ───────────────────────────────────────────────────────

SCORE_ANCHORS = """
Score anchors — interpolate between these points:
  100 = perfect: complete, accurate, specific, immediately usable
   90 = excellent: tiny wording issues or one minor omission, no impact on usability
   80 = good: correct and useful, one noticeable gap or slight imprecision
   70 = acceptable: core answer is right, but missing some context or steps
   60 = partial: answers part of the question well, ignores another part
   50 = mediocre: significant gaps, or correct but too vague to act on
   40 = weak: some useful info but notable inaccuracies or missing key details
   30 = poor: mostly off-target or incorrect, one or two correct points
   20 = very poor: nearly all wrong or irrelevant, misleads the user
   10 = almost useless: one marginally correct detail in an otherwise wrong answer
    0 = completely wrong, irrelevant, or harmful to follow
"""

# ── Numeric claims ────────────────────────────────────────────────────────────
#
# Wrong *magnitudes* were invisible to every judge measured: the judge-validity
# gate's `wrong_numbers` degradation — which scales figures by 10x and swaps
# units, leaving the prose intact and the length unchanged — scored 0.60 on the
# shipped prompt (Haiku, 4 seeds, 156 pairs; 27% ties, mean score drop 1.9
# points), i.e. the judge barely saw the edit. A single 20-base run of the full
# 9-variant gate put it at 0.71; run-to-run spread on this gate is wide. It is attached to `correctness` rather than
# given its own dimension because that is where a wrong number belongs, and
# because `correctness` already carries double weight in the aggregate; a sixth
# dimension would both re-dilute the weighting and break score comparability
# with stored runs a second time.
#
# The wording names the *class* of error and deliberately no example of it. An
# earlier version listed "ms where ns is meant, MB/s where GB/s, Hz where MHz"
# and "off by an order of magnitude" — which are, verbatim, the substitutions and
# the 10x scaling the gate's `wrong_numbers` generator applies, so its measured
# gain was taken on the same perturbations the prompt described.
NUMERIC_CLAIMS = """   Check the answer's *numbers*, not only its prose: every quoted magnitude,
   unit, size, width, latency, rate or ratio is a factual claim. A figure that
   is wrong for this question — wrong value or wrong unit — is a factual error
   even when the sentence around it is fluent and confident, and caps
   correctness at 40."""

# Listed first in the output so the figures are written out before any score
# is. Criterion + enumeration moved `wrong_numbers` significantly where the
# criterion alone did not; the two were not compared head-to-head, so this is
# the best reading, not an isolated effect. Putting it in the JSON makes the
# enumeration visible in every stored judgement instead of asserted.
NUMERIC_INSTRUCTION = """- First fill `numeric_claims`: copy out every figure the answer quotes, with
  its unit (at most 20), and mark each right or wrong for this question. Then
  score correctness; a single wrong figure caps it at 40"""

# A JSON array, one entry per figure. A semicolon-separated string was tried to
# cut malformed replies (with an array first, the model sometimes closes the
# later `reasoning` object with `]`): it did — 9 extra calls per 247 judgements
# instead of 26, against 5 for the stock prompt — but it scored lower on every
# numeric variant in two independent runs, and in the replication it no longer
# beat the old wording anywhere while the array did on all three (paired
# McNemar p = 0.016 / 0.035 / 0.039). The malformed replies are retried by
# `Judge._judge_answer` and `JudgePanel._call_judge`; 0-1 judgements per 247 still fail all three attempts.
NUMERIC_OUTPUT = """  "numeric_claims": ["<figure with unit>: right|wrong", ...],"""

#: Appended to the prompt of a re-draw at pinned sampling, where the plain prompt
#: would return the same malformed reply. Numbered so every re-draw differs.
FORMAT_REMINDER = (
    "\n\nReminder (re-draw {n} of 2): your previous reply could not be parsed. "
    "Reply with the JSON object only, in exactly the output format above, with "
    "every listed key and an integer for every score."
)


def reminder_reprompt(pinned: bool) -> Optional[Callable[[str, int], str]]:
    """The ``reprompt`` for :func:`dokimi.llm.ask_parsed` at a judge's sampling.

    At pinned sampling a re-draw appends :data:`FORMAT_REMINDER`; unpinned, the
    re-draw is a new sample of the plain prompt, so there is no reprompt.
    """
    if not pinned:
        return None
    return lambda prompt, n: prompt + FORMAT_REMINDER.format(n=n)

#: A verdict after a colon, a dash or an opening parenthesis, optionally
#: qualified: "64 B: wrong", "64 B - wrong", "64 B (wrong)", "64 B: partially wrong".
#: It is tried first, so every claim it reads keeps its reading.
_VERDICT = re.compile(
    r"(?::|\s[-\u2013\u2014]|\()\s*(?:[a-z]+ly\s+)?(wrong|incorrect|right|correct)\b", re.I
)
#: Gate for the fallback: the claim must contain a ``:``, a dash after whitespace (``-``,
#: en or em dash) or ``(`` somewhere (it may be inside the figure). The fallback searches
#: the whole claim; its end-anchor, not a start offset, is what keeps a ``/`` or ``,`` in
#: the figure from being read as the verdict.
_VERDICT_PART = re.compile(r":|\s[-\u2013\u2014]|\(")
#: Fallback for a claim ``_VERDICT`` cannot read. The verdict word must end the claim (only
#: whitespace, ``.`` and ``)`` may follow) and come after a separator or a ``,``, either
#: directly (``reasonable, not wrong``) or behind single-token ``/`` qualifiers that start
#: right there (``misleading/wrong``, ``L2/wrong``). A ``/`` deeper in prose or a path
#: (``see docs/wrong``, ``see section 4.2/wrong``) is not a verdict, so the claim stays
#: unread. A path right after the separator (``ref: tests/wrong``) reads like ``L2/wrong``;
#: only a path after a word, as in ``see docs/wrong``, stays unread. ``not`` flips it.
#: A ``/`` or ``,`` right after another verdict word (``right/wrong``, ``correct, wrong``)
#: is an alternative, not a verdict, so the claim stays unread; ``(?<!correct)`` also
#: covers ``incorrect``. Only the intensifiers in ``_INTENSIFIERS`` may qualify the word
#: here; any other ``-ly`` adverb (``hardly``, ``possibly``, ``formerly``) hedges or
#: dates the verdict and leaves the claim unread. A negated intensifier hedges in both
#: directions (``not entirely correct``, ``not entirely wrong``) and stays unread, while a
#: bare ``not correct`` is wrong. A verdict word with no separator in front stays unread on
#: purpose: ``correctly marked as WRONG to claim`` says the answer flagged a bad claim,
#: not that its figure is wrong.
_QUALIFIED_VERDICT = re.compile(
    r"(?:(?::|\s[-\u2013\u2014]|\(|(?<!wrong)(?<!right)(?<!correct),)\s*"
    r"(?:(?!(?:wrong|right|correct|incorrect)/)[\w.+-]+/)*)"
    r"(not\s+)?(?:([a-z]+ly)\s+)?(wrong|incorrect|right|correct)[\s.)]*$",
    re.I,
)
#: Adverbs that strengthen a fallback verdict instead of hedging it.
_INTENSIFIERS = frozenset(
    {
        "partially",
        "mostly",
        "largely",
        "completely",
        "totally",
        "entirely",
    }
)


def is_wrong_figure(claim: Any) -> Optional[bool]:
    """True for a claim judged wrong, None for one with no readable verdict."""
    text = f": {claim.get('verdict')}" if isinstance(claim, dict) else claim
    if not isinstance(text, str):
        return None
    match = _VERDICT.search(text)
    if match is not None:
        return match.group(1).lower() in ("wrong", "incorrect")
    match = _QUALIFIED_VERDICT.search(text) if _VERDICT_PART.search(text) else None
    if match is None:
        return None
    negated, adverb, word = match.groups()
    if adverb and adverb.lower() not in _INTENSIFIERS:
        return None
    is_wrong_word = word.lower() in ("wrong", "incorrect")
    if adverb and negated:
        return None  # "not entirely correct" and "not entirely wrong" both hedge
    if negated:
        return not is_wrong_word
    return is_wrong_word


def cap_for_wrong_figures(scores: Dict[str, Any]) -> None:
    """Hold the prompt's promise: one wrong figure in `numeric_claims` caps correctness at 40.

    The judged score is kept under `caps`, so a capped 40 can be told from a judged 40,
    and the indexes of claims with no readable verdict under `unread_claims`.
    """
    claims = scores.get("numeric_claims")
    if not isinstance(claims, list):
        return
    verdicts = [is_wrong_figure(c) for c in claims]
    unread = [i for i, wrong in enumerate(verdicts) if wrong is None]
    if unread:
        scores["unread_claims"] = unread
    if any(verdicts):
        judged = float(scores["correctness"])
        if judged > 40.0:
            scores["correctness"] = 40.0
            scores["caps"] = {"correctness": {"judged": judged, "rule": "wrong_figure"}}
