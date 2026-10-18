# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Deterministic answer degradations — ground truth for judging the judge.

A judge that reports 0-100 quality scores is only useful if its ordering is
right. Correlating it against another judge cannot show that: two judges can
agree and both be wrong. What can show it is a pair whose correct ordering we
know by construction — an answer and a mechanically damaged copy of the same
answer. The intact member must score higher (for the neutral ``padding``, the
same). If it does not, the judge is not measuring answer quality.

The degradations here are pure and seeded, so a validity run is reproducible
and costs no LLM calls to build. Each one targets a different failure the judge
must be able to see:

  truncate_20        missing content (the answer stops early)
  strip_code         missing executable detail
  shuffle_sentences  destroyed structure, same facts
  wrong_tools        fluent, confident, wrong technology  <- the hard one
  mismatched_answer  an answer to a different question entirely
  wrong_numbers      right tools, wrong magnitudes
  contradiction      the answer disowns its own recommendation
  hedge_concrete     strip_code with no marker and no length drop
  padding            longer, no more content           <- NEUTRAL, see below

``wrong_tools`` is the case that matters most in practice, because it is the
failure mode a documentation agent actually has: the prose stays polished while
the advice becomes unusable. It is also the case judges fail most often, since
they reward fluency.

The last four were added because the first five let a judge pass for the wrong
reason. Measured over 20 pairs: ``len(answer)`` alone — not a judge at all —
scores 1.00 on ``truncate_20``, ``strip_code`` and ``shuffle_sentences``, so
three of the five original variants cannot distinguish a judge from a character
counter. ``wrong_numbers`` and ``contradiction`` are length-neutral like
``wrong_tools``; ``hedge_concrete`` removes exactly what ``strip_code`` removes
and puts vague prose of the same length in its place, so neither a marker nor
the length drop gives it away; and ``padding`` is the control for the length
bias itself.
"""

from __future__ import annotations

import random
import re
from decimal import Context, Decimal
from typing import TYPE_CHECKING, Any, Callable, Dict, Iterable, List, Optional, Sequence

from dokimi.eval.question_text import question_text_of, require_question_text
from dokimi.utils import question_id

if TYPE_CHECKING:
    from dokimi.eval.judge_context import JudgeContextStore

#: Minimum answer length (characters) worth degrading. Short answers have too
#: little content for truncation or code-stripping to mean anything.
DEFAULT_MIN_CHARS = 1500


def truncate_20(answer: str, **_: Any) -> str:
    """Keep the first fifth of the answer (at least 200 characters)."""
    return answer[: max(200, len(answer) // 5)]


def strip_code(answer: str, **_: Any) -> str:
    """Remove every fenced code block, leaving a marker behind."""
    return _replace_code_blocks(answer, lambda _: "[code removed]")


def shuffle_sentences(answer: str, rng: Optional[random.Random] = None, **_: Any) -> str:
    """Keep the facts, destroy the order. Needs a seeded rng for reproducibility."""
    if rng is None:
        raise ValueError("shuffle_sentences requires a seeded rng")
    parts = re.split(r"(?<=[.!?])\s+", _replace_code_blocks(answer, lambda _: ""))
    rng.shuffle(parts)
    return " ".join(parts)


#: Substitutions that swap a correct technology for a plausible wrong one.
#: Domain-specific by nature — this table covers the Intel performance-tooling
#: domain. Pass ``substitutions=`` to :func:`wrong_tools` for another domain.
WRONG_TOOL_SUBSTITUTIONS: Sequence[tuple[str, str]] = (
    (r"\bperf\b", "gprof"),
    (r"toplev(\.py)?", "valgrind"),
    (r"\bIntel\b", "AMD"),
    (r"\bIPC\b", "MFLOPS"),
    (r"\bVTune\b", "Instruments"),
    (r"\bAVX-?512\b", "3DNow!"),
    (r"\buarch\b", "frontend"),
    (r"\boneAPI\b", "ROCm"),
    (r"\boneTBB\b", "pthreads"),
    (r"\bumask\b", "affinity"),
    (r"\bPMU\b", "sampler"),
    (r"\bx86-64\b", "i686"),
    (r"\bAVX2\b", "AVX"),
)


def wrong_tools(
    answer: str,
    substitutions: Optional[Sequence[tuple[str, str]]] = None,
    **_: Any,
) -> str:
    """Swap correct tool/flag/vendor names for plausible wrong ones."""
    out = answer
    for pattern, replacement in substitutions or WRONG_TOOL_SUBSTITUTIONS:
        out = re.sub(pattern, replacement, out)
    return out


def mismatched_answer(answer: str, other: Optional[str] = None, **_: Any) -> str:
    """Replace the answer with a real answer to a *different* question."""
    if not other:
        raise ValueError("mismatched_answer requires another answer to substitute")
    return other


#: Numeric sabotage, the counterpart of :data:`WRONG_TOOL_SUBSTITUTIONS`.
#: ``wrong_tools`` only ever breaks *names*, so a judge validated on it alone
#: cannot claim to catch factual error in general. This scales the magnitudes
#: the prose states and nothing else: no name, flag or unit is rewritten (ISA
#: names are ``wrong_tools``' job), and each figure is edited once, so a unit
#: swap and a x10 never stack on one number.
#: A figure is read whole (``1,500`` and ``5 000`` are one number) and never as
#: the tail of a name, a flag, a power or a label (``C++17``, ``AVX-512``,
#: ``x86-64``, ``10^9``, ``#1``, ``1:1``). A range (``10-15%``, ``2-4x``) is
#: skipped: scaling one end only garbles it.
_GROUP_GAP = " \u00a0\u202f\u2009"  # a space, or one a digit group sits across
_NOT_AFTER = rf"(?<![\w.,:^+#\-–—])(?<![-–—\d][{_GROUP_GAP}])"
_NOT_RANGE = rf"(?![{_GROUP_GAP}]?[-–—] ?\d|[{_GROUP_GAP}]\d)"
_FIGURE = _NOT_AFTER + r"(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)" + _NOT_RANGE
UNIT_MAGNITUDE = re.compile(
    _FIGURE + r"(\s*|-(?=[a-z]))"
    r"(%|x\b|×|ns\b|[uµ]s\b|ms\b|s\b|[KMGT]i?B(?:/s)?\b|[KMG]Hz\b|"
    r"cycles?\b|bytes?\b|threads?\b|cores?\b|iterations?\b|samples?\b)"
)

#: Last resort for prose that quotes a count with no unit ("a concurrency limit
#: of 2"). A list ordinal, a label, a year or a version number is not a claim,
#: and 10x one only invents a release that does not exist (C++170, TBB 20200);
#: a unitless decimal is nearly always a version (CMake 3.5), so it is skipped.
BARE_NUMBER = re.compile(_NOT_AFTER + r"(\d{1,3}(?:,\d{3})+|\d+)" + _NOT_RANGE + r"(?![\w^):]|[.,]\d|-\w)")
NOT_A_QUANTITY = re.compile(
    r"(?:^|\n)[\s#>*-]*\Z"
    r"|\b(?:step|option|method|stage|phase|part)\s*\Z"
    r"|\b(?:versions?|release|v|c\+\+|tbb|studio|gcc|clang|msvc|python|cmake|pep|nep|rfc)\b[^\n.;:]{0,20}\Z",
    re.I,
)
YEAR = re.compile(r"(?:19[89]|20[0-4])\d\Z")

#: Code is not where an answer states its figures: 10x a loop bound or a buffer
#: size changes the example, not the facts, so the intact copy is not better.
#: An answer with no figure in its prose is left unchanged and counted a no-op.
#: Blocks are found line by line as in ``padding``, so ``` mentioned mid-line
#: does not swap prose and code.

#: Identifier sources, in fallback order, for degradations that need to name
#: something the answer itself said.
INLINE_SPAN = re.compile(r"`[^`\n]+`")
#: Text a degradation must not edit: inline code and URLs (their paths and
#: query strings carry figures that are not claims).
_VERBATIM = re.compile(INLINE_SPAN.pattern + r"|https?://\S+")
BOLD_SPAN = re.compile(r"\*\*([^*\n]{2,40})\*\*")
SHAPED_BARE = re.compile(
    r"\b(?:[a-z][a-z0-9_]*[-_][a-z0-9_-]+|[A-Z][A-Za-z]+\d|AVX-?\d+|[a-z]+\d[a-z0-9]*)\b"
)
#: The name has to be a NAME. The first inline span in a real answer was
#: ``0.42``, and "0.42 is not applicable here" is not a contradiction, it is
#: gibberish — so a candidate must be a single identifier-shaped token starting
#: with a letter, which also rejects multi-word bold phrases.
NAME_TOKEN = re.compile(r"[A-Za-z](?:[\w.\-/+]*[\w+])?\Z")
#: Some answers inline-quote ordinary English (``and``, ``must``), and
#: "`and` is not applicable here" is the same gibberish as the numeric case,
#: and so are labels and literals (``Summary``, ``True``) and exception names.
COMMON_WORDS = frozenset(
    "and or not but must should may can will the this that these those with "
    "for from into than then when where which while critical important note "
    "yes no all any each both same other more less high low "
    "summary warning true false none".split()
)
EXCEPTION_NAME = re.compile(r"\w*(?:Error|Exception|Warning)\Z")


def _scale(figure: str, unit: str = "") -> str:
    """10x a figure, except a percentage over 9, which is shrunk instead."""
    digits = figure.replace(",", "")
    value = Decimal(digits)
    ctx = Context(prec=len(digits))
    scaled = ctx.divide(value, 10) if unit == "%" and value > 9 else ctx.multiply(value, 10)
    return format(ctx.normalize(scaled), ",f" if "," in figure else "f")


def _scale_magnitude(match: "re.Match[str]") -> str:
    figure, gap, unit = match.groups()
    return f"{_scale(figure, unit)}{gap}{unit}"


def _scale_bare(prose: str) -> str:
    def scale(match: "re.Match[str]") -> str:
        figure = match.group(1)
        if YEAR.match(figure) or NOT_A_QUANTITY.search(prose, 0, match.start()):
            return figure
        return _scale(figure)

    return BARE_NUMBER.sub(scale, prose)


def _in_prose(answer: str, edit: Callable[[str], str]) -> str:
    def prose(lines: List[str]) -> str:
        text = "\n".join(lines)
        out: List[str] = []
        end = 0
        for span in _VERBATIM.finditer(text):
            out += [edit(text[end : span.start()]), span.group()]
            end = span.end()
        out.append(edit(text[end:]))
        return "".join(out)

    out: List[str] = []
    chunk: List[str] = []
    fence: Optional[str] = None
    for line in answer.split("\n"):
        was, fence = fence, _fence_state([line], fence)
        if was is None and fence is None:
            chunk.append(line)
            continue
        if chunk:
            out.append(prose(chunk))
            chunk = []
        out.append(line)
    if chunk:
        out.append(prose(chunk))
    return "\n".join(out)


def wrong_numbers(answer: str, **_: Any) -> str:
    """Break the magnitudes the prose states, leaving names, units and code intact."""
    out = _in_prose(answer, lambda prose: UNIT_MAGNITUDE.sub(_scale_magnitude, prose))
    if out != answer:
        return out
    return _in_prose(answer, _scale_bare)


#: A claim that contradicts the answer's own recommendation, derived from the
#: answer itself so it works in any domain.
CONTRADICTION_SUFFIX = (
    "\n\nOne important caveat: {name} is not actually applicable here and should "
    "not be used for this task. Ignore the steps above that rely on it — they "
    "will give misleading results on any modern system."
)


#: A bold label or hyphenated compound that is plain English ("Summary",
#: "well-known"), as opposed to something identifier-shaped ("TaskArena", "tbb_x").
ENGLISH_WORD = re.compile(r"[A-Z]?[a-z]+(?:-[a-z]+)*\Z")
#: What comes before a name the answer itself warns against ("do not use `x`",
#: "avoid `x`", "there is no `x`"): disowning it would agree with the answer, not
#: contradict it. The reach is 40 characters inside one clause: it stops at a
#: sentence end (a "." before whitespace), ";", ":", an em dash, a comma that
#: does not lead on to another name, and a later use/call/link/install ("avoid
#: `x`, call `y`") or "have" ("do not have `x`").
_VERB = r"\b(?:use|call|link|install)"
NEGATION = re.compile(
    r"\b(?:not|never|neither|avoid|cannot|\w+n['\u2019]t|there (?:is|are) no|instead of|rather than)\b"
    rf"(?:\*\*)?(?:\s+(?:ever\s+)?{_VERB}\w*)?"
    rf"(?:(?!(?<!\bto ){_VERB}|\bhave\b|,(?=\s*[^\s`*])(?!\s*(?:and|or)\b))[^.;:\n\u2014]|\.(?=\S)){{0,40}}\Z",
    re.I,
)
#: What the answer says after a name (or a list of names) that disowns it.
UNAVAILABLE = re.compile(
    r"(?:\s*(?:,|and|or)\s*(?:`[^`\n]+`|\*\*[^*\n]+\*\*))*(?:\*\*)?"
    r"(?:\s+(?:is|are)\s+(?:\w+ly\s+)?(?:\*\*)?not\s+(?:re-?)?"
    r"(?:supported|available|implemented|present|covered)"
    r"|\s*\(?(?:(?:is|are)\s+)?deprecated\b|\s+should be avoided"
    r"|\s+(?:does|do)(?:\s+not|n['’]t)\s+apply(?=\s+(?:to|here)\b|\s*[.,;:)*]|\s*\Z)"
    r"|\s+(?:will\s+not|won['’]t)\s+work)",
    re.I,
)


def _first_identifier(answer: str) -> Optional[str]:
    """First identifier-shaped token the answer recommends, or None."""
    candidates = (
        [(m.start(), m.end(), m.group().strip("`"), True) for m in INLINE_SPAN.finditer(answer)]
        + [(m.start(1), m.end(), m.group(1), False) for m in BOLD_SPAN.finditer(answer)]
        + [(m.start(), m.end(), m.group(), False) for m in SHAPED_BARE.finditer(answer)]
    )
    rejected: set[str] = set()
    for pos, end, raw, quoted in candidates:
        name = raw.strip()
        if not (2 <= len(name) <= 30 and NAME_TOKEN.match(name) and name.lower() not in COMMON_WORDS):
            continue
        if EXCEPTION_NAME.match(name):
            continue
        if name in rejected or (not quoted and ENGLISH_WORD.match(name)):
            continue
        if NEGATION.search(answer, 0, pos) or UNAVAILABLE.match(answer, end):
            rejected.update((name, name.rsplit(".", 1)[-1]))  # and a later bare mention
            continue
        return name
    return None


def contradiction(answer: str, **_: Any) -> str:
    """Append a sentence disowning the answer's own recommendation."""
    name = _first_identifier(answer)
    if name is None:
        return answer
    return answer + CONTRADICTION_SUFFIX.format(name=f"`{name}`")


#: Filler that adds length and no content. Free-text judges are widely held to
#: reward length, so a judge has to be shown NOT to. Everything here is true,
#: generic, and already implied by any answer, so a padded answer is not better
#: than its original and must not score higher.
PADDING_PARAGRAPHS: Sequence[str] = (
    "It is worth stepping back to consider the broader context before diving "
    "into specifics, since the right choice always depends on the particular "
    "workload, the hardware it runs on, and what the team is optimizing for.",
    "Keep in mind that measurement is essential: without data, any change is a "
    "guess, and it is easy to spend effort on something that turns out not to "
    "matter for the workload in front of you.",
    "As always, results will vary between environments, so it is a good idea to "
    "validate on the target system rather than assuming that what helps in one "
    "setting will help in another.",
    "Finally, remember that maintainability matters too — a change that is very "
    "fast but impossible for the rest of the team to reason about may not be "
    "the right tradeoff in the long run.",
)


#: A fence line: optional indentation, then 3+ backticks or tildes. Only a line
#: that *starts* with the run counts — ``` mentioned mid-line in prose (14 of
#: 1215 stored perf answers do this) is not a fence. Indentation is not capped
#: at CommonMark's 3 spaces because that limit is relative to the enclosing list
#: item: a fence inside a nested list item sits 4+ spaces in (9 of 1215 stored
#: answers have one) and is still a fence.
_FENCE_LINE = re.compile(r"^[ \t]*(`{3,}|~{3,})")
_PRE = "<pre>"
_PRE_OPEN = re.compile(r"\s*<pre[\s>]", re.I)
_PRE_CLOSE = re.compile(r"</pre\s*>", re.I)


def _fence_state(lines: Sequence[str], fence: Optional[str]) -> Optional[str]:
    """Advance the open-fence state over ``lines``.

    ``fence`` is the opening line's indentation plus its run (e.g. "```" or
    "    ~~~~") of the block we are inside, or None outside one. A block closes on
    a line starting with the same character, at least as many times, nothing
    else after it (a table cell's ``|`` aside), and indented at most 3 columns
    past the opener: deeper, it is content (a fence quoted inside a ```markdown block). A ``<pre>`` element is a
    block too, closed by ``</pre>``.
    """
    for line in lines:
        if fence == _PRE:
            if _PRE_CLOSE.search(line):
                fence = None
            continue
        if fence is None and _PRE_OPEN.match(line) and not _PRE_CLOSE.search(line):
            fence = _PRE
            continue
        m = _FENCE_LINE.match(line)
        if not m:
            continue
        run = m.group(1)
        indent = m.start(1)
        if fence is None:
            # A backtick fence's info string cannot contain a backtick, so a
            # line that merely starts with inline code ("```x``` is ...") does
            # not open a block.
            if run[0] == "`" and "`" in line.lstrip()[len(run):]:
                continue
            fence = line[:indent] + run
        else:
            opener = fence.lstrip()
            if (
                run[0] == opener[0]
                and len(run) >= len(opener)
                and indent <= len(fence) - len(opener) + 3
                and not line.strip()[len(run):].strip(" \t|")
            ):
                fence = None
    return fence


_INLINE_FENCE = re.compile(r"(?<!`)(`{3,})(?!`)(.+?)(?<!`)\1(?!`)")
_QUOTE_MARK = re.compile(r"[ \t]*>[ \t]?")


def _unquote(line: str, limit: int) -> "tuple[int, str]":
    """Strip up to ``limit`` blockquote markers; return how many were stripped and the rest."""
    depth = pos = 0
    while depth < limit and (m := _QUOTE_MARK.match(line, pos)):
        depth, pos = depth + 1, m.end()
    return depth, line[pos:]


def _replace_code_blocks(answer: str, replace: Callable[[str], str]) -> str:
    """Replace each fenced block, found line by line as ``padding`` finds them.

    A triple-backtick span within one line (a table cell) is replaced too. An
    unclosed block is left as it is. The first line's indentation and what
    follows the closing fence (a ``|`` or a carriage return) are kept, so a block nested in
    a list item or a table row stays where it was. A block inside a blockquote
    keeps the opening line's ``>`` markers and ends where the quote does.
    """
    out: List[str] = []
    block: List[str] = []
    fence: Optional[str] = None
    depth = 0

    def replaced() -> str:
        quote = block[0][: len(block[0]) - len(_unquote(block[0], depth)[1])]
        text = "\n".join(_unquote(line, depth)[1] for line in block)
        body = text.lstrip(" \t")
        code = body.rstrip(" \t|\r")
        return quote + text[: len(text) - len(body)] + replace(code) + body[len(code):]

    for line in answer.split("\n"):
        if fence is not None and _unquote(line, depth)[0] < depth:
            out.append(replaced())
            block, fence = [], None
        level, rest = _unquote(line, depth if fence is not None else len(line))
        was, fence = fence, _fence_state([rest], fence)
        if was is None and fence is None:
            out.append(_INLINE_FENCE.sub(lambda m: replace(m.group()), line))
            continue
        if was is None:
            depth = level
        block.append(line)
        if fence is None:
            out.append(replaced())
            block = []
    return "\n".join(out + block)


LIST_ITEM = re.compile(r"\s*(?:[-*+]|\d+[.)])\s")


def _can_pad_between(para: str, following: str) -> bool:
    """False where filler would cut a lead-in from what it introduces, or a list apart.

    An indented paragraph continues the list item above it: unindented filler
    in between ends the list, and a fence nested in the item becomes literal text.
    """
    in_list = bool(LIST_ITEM.match(para)) or para[:1].isspace()
    return not (
        para.rstrip().endswith(":")
        or following[:1].isspace()
        or (in_list and LIST_ITEM.match(following))
    )


def padding(answer: str, **_: Any) -> str:
    """Same content, ~4 paragraphs longer. A correct judge does not move."""
    paragraphs = answer.split("\n\n")
    out: List[str] = []
    used = 0
    fence: Optional[str] = None
    opened_at = 0  # index in `out` of the paragraph that opened the current block
    for i, para in enumerate(paragraphs):
        if fence is None:
            opened_at = len(out)
        out.append(para)
        # A blank line inside a fenced block is not a paragraph break: filler
        # inserted there would corrupt the code and make the copy worse.
        fence = _fence_state(para.split("\n"), fence)
        following = paragraphs[i + 1] if i + 1 < len(paragraphs) else ""
        if fence is None and used < len(PADDING_PARAGRAPHS) and _can_pad_between(para, following):
            out.append(PADDING_PARAGRAPHS[used])
            used += 1
    rest = list(PADDING_PARAGRAPHS[used:])
    if fence is not None and rest:
        # An unclosed block runs to the end of the document, so filler appended
        # after it would render as code. Put it in front of the block instead.
        out[opened_at:opened_at] = rest
    else:
        out.extend(rest)
    return "\n\n".join(out)


#: Stand-ins for a code block: fluent, true of any answer, and naming nothing
#: that could be run.
HEDGE_SENTENCES: Sequence[str] = (
    "The exact commands depend on your setup, so adapt the usual steps for this "
    "to your own build environment.",
    "The details here follow the standard pattern for the library and are easy "
    "to fill in once the overall approach is clear.",
    "In practice this comes down to a handful of routine calls, wired together "
    "in the obvious order.",
    "Most projects already have something similar in place, and it can be "
    "reused with only minor adjustments.",
    "The specific values matter less than getting the overall structure right, "
    "so start simple and refine from there.",
    "Consult the reference documentation for the precise names and arguments, "
    "which vary between versions.",
    "Once this part is set up it rarely needs to change, so it is worth taking "
    "the time to get it right.",
    "Any reasonable implementation of this step will work, as long as it "
    "follows the conventions used elsewhere in the code.",
    "The rest is boilerplate that a build system or an editor template will "
    "typically generate for you.",
    "Keep this step small and self-contained so that it is easy to test in "
    "isolation before moving on.",
    "The same idea carries over directly to larger programs, with the "
    "structure staying essentially unchanged.",
    "Error handling and cleanup follow the usual conventions and do not need "
    "anything specific to this case.",
)

#: Short stand-ins, so a one-line block is not replaced by three times its length.
SHORT_HEDGES: Sequence[str] = (
    "Adapt this to your own setup.",
    "The usual form works here.",
    "Fill in the details as needed.",
    "Any standard variant will do.",
    "Use whatever your project already has.",
    "The exact form depends on your build.",
)


def hedge_concrete(answer: str, rng: Optional[random.Random] = None, **_: Any) -> str:
    """Remove what ``strip_code`` removes, with nothing to pattern-match.

    ``strip_code`` leaves "[code removed]" behind and cuts the answer by about
    a fifth, so a judge could be scoring the marker or the length rather than
    noticing the absence. Here each block becomes hedge sentences, long or
    short, whichever keeps the running length closest to the original, so
    ``len(answer)`` is no better than a coin flip. Needs a seeded rng; the
    sentence order is drawn per answer, so a run's outputs do not all open alike.
    """
    if rng is None:
        raise ValueError("hedge_concrete requires a seeded rng")
    order = random.Random(f"{rng.random()}:{answer}")
    decks = [list(HEDGE_SENTENCES), list(SHORT_HEDGES)]
    for deck in decks:
        order.shuffle(deck)
    used = [0, 0]
    gap = 0

    def fill(block: str) -> str:
        nonlocal gap
        gap += len(block)
        out: List[str] = []
        while True:
            options = [decks[i][used[i] % len(decks[i])] for i in (0, 1)]
            k = min((0, 1), key=lambda i: abs(gap - len(options[i]) - 1))
            if out and len(options[k]) / 2 >= gap:
                return " ".join(out)
            out.append(options[k])
            used[k] += 1
            gap -= len(options[k]) + 1

    return _replace_code_blocks(answer, fill)


Degradation = Callable[..., str]

DEGRADATIONS: Dict[str, Degradation] = {
    "truncate_20": truncate_20,
    "strip_code": strip_code,
    "shuffle_sentences": shuffle_sentences,
    "wrong_tools": wrong_tools,
    "mismatched_answer": mismatched_answer,
    "wrong_numbers": wrong_numbers,
    "contradiction": contradiction,
    "hedge_concrete": hedge_concrete,
    "padding": padding,
}

#: ``padding`` is the one variant whose correct pairwise outcome is NOT "intact
#: scores higher". It adds no content and removes none, so the honest target is
#: no movement; scoring it as a win would credit a length-insensitive judge for
#: the wrong reason. Graded separately, and excluded from the gate's pass/fail.
NEUTRAL_VARIANTS = frozenset({"padding"})


def degrade(
    answer: str,
    variant: str,
    *,
    seed: int = 0,
    other: Optional[str] = None,
) -> str:
    """Apply one named degradation. Pure given ``seed``."""
    if variant not in DEGRADATIONS:
        raise ValueError(f"Unknown degradation '{variant}'; known: {sorted(DEGRADATIONS)}")
    return DEGRADATIONS[variant](answer, rng=random.Random(seed), other=other)


def _answer_pool(answers: Iterable[object]) -> List[Dict[str, Any]]:
    """Flatten a run's answers into (question_id, question_text, arm, answer) rows.

    Accepts both the arm-runner shape (``arms: {name: {answer: ...}}``) and the
    legacy two-condition shape (``with_docs``/``without_docs``).
    """
    pool: List[Dict[str, Any]] = []
    for i, item in enumerate(answers):
        if not isinstance(item, dict):
            continue
        qid = question_id(item)
        if qid is None:
            continue  # no id: never paired, so skipped (not refused)
        # Checked by build_pairs for the rows that can become a pair; the others are never judged.
        qtext = question_text_of(item)
        arms = item.get("arms")
        candidates: List[tuple[str, Any]] = []
        if isinstance(arms, dict):
            candidates = list(arms.items())
        else:
            for legacy in ("with_docs", "without_docs"):
                if legacy in item:
                    candidates.append((legacy, item[legacy]))
        for arm, payload in candidates:
            text = (payload or {}).get("answer") if isinstance(payload, dict) else payload
            if isinstance(text, str) and text.strip():
                pool.append({"question_id": qid, "question_text": qtext, "arm": arm, "answer": text,
                             "_record": (item, i)})
    return pool


def build_pairs(
    answers: Iterable[Dict[str, Any]],
    *,
    n_bases: int = 20,
    seed: int = 0,
    min_chars: int = DEFAULT_MIN_CHARS,
    variants: Optional[Sequence[str]] = None,
    library: str = "the library",
    noop: Optional[Dict[str, int]] = None,
    store: Optional["JudgeContextStore"] = None,
    dropped: Optional[Dict[str, int]] = None,
    by_arm: Optional[Dict[str, Dict[str, int]]] = None,
) -> List[Dict[str, Any]]:
    """Build (intact, degraded) pairs with a known correct ordering.

    One pair per (base answer x variant); ``intact`` must score higher. A
    degradation that left the text unchanged is dropped and counted in ``noop``.
    On a neutral variant (``padding``) the target is the same score instead.

    If ``store`` is provided, only answers with material in the store are used as bases.
    """
    for name in variants or ():
        if name not in DEGRADATIONS:
            raise ValueError(f"Unknown degradation '{name}'; known: {sorted(DEGRADATIONS)}")
    pool = _answer_pool(answers)
    rng = random.Random(seed)
    rng.shuffle(pool)
    long_answers = [row for row in pool if len(row["answer"]) > min_chars]
    long_answers_before_store = len(long_answers)
    # Filter by material before sampling if store is provided
    if store is not None:
        # One pass: decide kept or dropped per row and count it there.
        has_material = []
        for row in long_answers:
            ok = store.get(row["question_id"], row["arm"]) is not None
            if ok:
                has_material.append(row)
            if by_arm is not None:
                counts = by_arm.setdefault(row["arm"], {"kept": 0, "dropped": 0, "sampled": 0})
                counts["kept" if ok else "dropped"] += 1
        long_answers = has_material
        if dropped is not None:
            dropped["total"] = long_answers_before_store - len(long_answers)
    # Refused, not skipped: a pair built without its question is graded against nothing. Every
    # row that can become a base is checked (not only the drawn bases), so the outcome does not
    # depend on the seed; a row dropped for missing judge material is never judged.
    for row in long_answers:
        if not row["question_text"].strip():
            require_question_text(*row["_record"])
    bases = long_answers[:n_bases]
    if store is not None and by_arm is not None:
        for row in bases:
            by_arm[row["arm"]]["sampled"] += 1

    pairs: List[Dict[str, Any]] = []
    for base in bases:
        # An answer to some other question, for the mismatched_answer variant. Drawn per base:
        # taking the first one gave every base the same substitute.
        others = [r["answer"] for r in pool if r["question_id"] != base["question_id"]]
        other = rng.choice(others) if others else None
        for name in variants or DEGRADATIONS:
            if name == "mismatched_answer" and not other:
                continue
            degraded = DEGRADATIONS[name](base["answer"], rng=random.Random(seed), other=other)
            if degraded == base["answer"]:
                if noop is not None:
                    noop[name] = noop.get(name, 0) + 1
                continue
            pairs.append(
                {
                    "base_id": base["question_id"],
                    "arm": base["arm"],
                    "question": base["question_text"],
                    "library": library,
                    "variant": name,
                    "intact": base["answer"],
                    "degraded": degraded,
                }
            )
    return pairs
