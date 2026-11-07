# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Granularity hints for a skill catalog: which skills may overlap, and which may be split.

Tier 1 of intel/dokimi#424, printed by ``catalog check``. Free and static: no model call, no
embeddings, no verdict. Each hint is a shortlist entry for the paid ``catalog granularity``
run, which measures routing; the static signals do not decide anything.
"""

from __future__ import annotations

import math
import re
import shlex
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from itertools import combinations
from typing import Sequence

from dokimi.external.validate import STOP_WORDS
from dokimi.skills.loader import Skill

# Measured routing did not follow similarity (#424, 11-06): TF-IDF and embeddings predicted no
# confusion and no wording gain. These constants only shortlist, and they are deliberately strict.
DESCRIPTION_COSINE_STRONG = 0.40  # TF-IDF cosine of two descriptions: enough on its own
DESCRIPTION_COSINE_MIN = 0.20  # ... and this much counts only with a shared trigger vocabulary
TRIGGER_VOCAB_JACCARD_MIN = 0.15  # word overlap of the two skills' "when" clauses
TRIGGER_JACCARD_MIN = 0.60  # two single "when" clauses are shared at this word overlap
TRIGGER_MIN_WORDS = 3  # shorter clauses ("when asked") match too much to mean anything
BODY_SHINGLE_WORDS = 8  # words per shingle when comparing bodies and H2 sections
BODY_JACCARD_MIN = 0.15  # shingle Jaccard of two bodies
SECTION_MIN_WORDS = 40  # H2 sections shorter than this are not clustered or compared
SECTION_JACCARD_MIN = 0.50  # shingle Jaccard of two H2 sections
SECTION_LINK_COSINE = 0.10  # catalog-wide TF-IDF cosine that links two H2 sections of a skill
CODE_MIN_LINES = 3  # fenced blocks shorter than this are not compared
CODE_SHINGLE_TOKENS = 4  # tokens per shingle of a fenced block
CODE_JACCARD_MIN = 0.60  # shingle Jaccard of two fenced blocks
LONG_BODY_CHARS = 12_000  # Skill.as_context's cap: a harness truncates a body past this
DISJOINT_WHEN_MIN = 3  # "when" clauses with no word in common, counted greedily
SPLIT_MIN_SIGNALS = 2  # split signals one skill needs before it is a split candidate
MERGE_MIN_SIGNALS = 1  # overlap signals one pair needs before it is a merge candidate
DISPLAY_LIMIT = 20  # rows printed in text mode; JSON keeps every candidate
PAID_USD_PER_SKILL = 1.75  # #424 PR family: about $7 for 4 skills on haiku with 3 reps

_WORD_RE = re.compile(r"\w+")
_FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})(.*)")  # a fence: its run of backticks or tildes, then any info string
_TRIGGER_RE = re.compile(
    r"\b(?:use this skill when|use when|use for|triggers? on|whenever|when)\b", re.IGNORECASE
)
_CLAUSE_END_RE = re.compile(r"[;.!?]\s|\bor when\b|\band when\b", re.IGNORECASE)


@dataclass(frozen=True)
class PairHint:
    """Two skills whose descriptions, triggers, bodies or snippets overlap. ``a`` sorts before ``b``."""

    a: str
    b: str
    description_cosine: float
    trigger_jaccard: float  # word overlap of all the "when" clauses of the two skills
    shared_clauses: tuple[str, ...]
    body_jaccard: float
    duplicated_sections: tuple[tuple[str, str, float], ...]  # (heading in a, heading in b, Jaccard)
    duplicated_code_blocks: int
    signals: tuple[str, ...]


@dataclass(frozen=True)
class SplitHint:
    """One skill with two or more split signals."""

    skill: str
    body_chars: int
    disjoint_when_clauses: int
    section_clusters: tuple[tuple[str, ...], ...]  # H2 headings grouped by similarity or cross reference
    signals: tuple[str, ...]


@dataclass(frozen=True)
class PaidHint:
    """The paid command to run next. It does not exist yet; ``catalog check`` only names it."""

    argv: tuple[str, ...]
    est_usd: float

    @property
    def command(self) -> str:
        return shlex.join(self.argv)


@dataclass(frozen=True)
class GranularityHints:
    skills: int
    merge: tuple[PairHint, ...]
    split: tuple[SplitHint, ...]
    paid: PaidHint | None

    def to_dict(self) -> dict:
        """The JSON block of ``catalog check --format json``. Hints only: there is no verdict key."""
        paid = None
        if self.paid is not None:
            paid = {
                "status": "planned", "paid": True, "argv": list(self.paid.argv),
                "command": self.paid.command, "est_usd": self.paid.est_usd,
                "estimate": "rough: scaled from the #424 PR family (4 skills, about $7)",
            }
        return {
            "hints_only": True,
            "skills": self.skills,
            "thresholds": _thresholds(),
            "merge": [
                {
                    "a": p.a, "b": p.b, "signals": list(p.signals),
                    "description_cosine": round(p.description_cosine, 3),
                    "trigger_jaccard": round(p.trigger_jaccard, 3),
                    "shared_clauses": list(p.shared_clauses),
                    "body_jaccard": round(p.body_jaccard, 3),
                    "duplicated_sections": [
                        {"a": ha, "b": hb, "jaccard": round(j, 3)} for ha, hb, j in p.duplicated_sections
                    ],
                    "duplicated_code_blocks": p.duplicated_code_blocks,
                }
                for p in self.merge
            ],
            "split": [
                {
                    "skill": s.skill, "signals": list(s.signals), "body_chars": s.body_chars,
                    "disjoint_when_clauses": s.disjoint_when_clauses,
                    "section_clusters": [list(group) for group in s.section_clusters],
                }
                for s in self.split
            ],
            "next": paid,
        }


def _thresholds() -> dict:
    names = (
        "DESCRIPTION_COSINE_STRONG", "DESCRIPTION_COSINE_MIN", "TRIGGER_VOCAB_JACCARD_MIN",
        "TRIGGER_JACCARD_MIN", "TRIGGER_MIN_WORDS", "BODY_SHINGLE_WORDS", "BODY_JACCARD_MIN",
        "SECTION_MIN_WORDS", "SECTION_JACCARD_MIN", "SECTION_LINK_COSINE", "CODE_MIN_LINES",
        "CODE_SHINGLE_TOKENS", "CODE_JACCARD_MIN", "LONG_BODY_CHARS", "DISJOINT_WHEN_MIN",
        "SPLIT_MIN_SIGNALS", "MERGE_MIN_SIGNALS",
    )
    return {name.lower(): globals()[name] for name in names}


def hints(skills: Sequence[Skill], *, root: str) -> GranularityHints:
    """The granularity hints for loaded, model-visible skills. ``root`` is only echoed into the paid hint."""
    docs = [_describe(skill, label) for skill, label in zip(skills, _labels(skills, root), strict=True)]
    merge = _merge_hints(docs)
    vectors = _section_vectors(docs)
    split = tuple(h for h in (_split_hint(i, d, vectors) for i, d in enumerate(docs)) if h is not None)
    units = sorted({p.a for p in merge} | {p.b for p in merge} | {s.skill for s in split})
    return GranularityHints(
        skills=len(docs), merge=merge, split=split, paid=_paid_hint(root, units)
    )


def format_text(result: GranularityHints) -> list[str]:
    """The human table: one row per candidate, capped at DISPLAY_LIMIT per kind."""
    head = (
        f"# granularity: {len(result.merge)} merge and {len(result.split)} split candidate(s) among "
        f"{result.skills} skills (hints only, free; no verdict: similarity does not predict routing "
        "confusion, #424)"
    )
    if not result.merge and not result.split:
        return [head, "  no merge or split candidates; nothing to run"]
    lines = [head]
    for p in result.merge[:DISPLAY_LIMIT]:
        lines.append(
            f"  merge\t{p.a} + {p.b}\tdesc {p.description_cosine:.2f}\ttriggers {p.trigger_jaccard:.2f}\t"
            f"body {p.body_jaccard:.2f}\t"
            f"sections {len(p.duplicated_sections)}\tcode {p.duplicated_code_blocks}\t{', '.join(p.signals)}"
        )
    for s in result.split[:DISPLAY_LIMIT]:
        lines.append(
            f"  split\t{s.skill}\t{s.body_chars} chars\tdisjoint when {s.disjoint_when_clauses}\t"
            f"section clusters {len(s.section_clusters)}\t{', '.join(s.signals)}"
        )
    hidden = max(0, len(result.merge) - DISPLAY_LIMIT) + max(0, len(result.split) - DISPLAY_LIMIT)
    if hidden > 0:
        lines.append(f"  ... {hidden} more candidate row(s) (--format json lists all)")
    if result.paid is not None:
        lines.append(f"  next (planned, [paid]): {result.paid.command}")
        lines.append(f"  estimated ${result.paid.est_usd:.2f}, rough (see the #424 PR-family run)")
    return lines


# ---------------------------------------------------------------- text reading


@dataclass(frozen=True)
class _Section:
    heading: str
    tokens: tuple[str, ...]
    terms: tuple[str, ...]  # tokens without stop words
    raw: str


@dataclass(frozen=True)
class _Doc:
    label: str
    body_chars: int
    desc_words: tuple[str, ...]
    clauses: tuple[str, ...]
    clause_words: tuple[frozenset[str], ...]  # the words of each clause, in ``clauses`` order
    trigger_words: frozenset[str]  # the words of all the "when" clauses
    prose: tuple[str, ...]  # body words outside fenced code
    sections: tuple[_Section, ...]  # H2 sections of at least SECTION_MIN_WORDS words
    code: tuple[tuple[str, ...], ...]  # fenced blocks of at least CODE_MIN_LINES lines


def _words(text: str) -> tuple[str, ...]:
    return tuple(_WORD_RE.findall(text.lower()))


def _label(skill: Skill) -> str:
    return skill.path.parent.name or skill.name


def _labels(skills: Sequence[Skill], root: str) -> list[str]:
    """Each skill's directory name, or its directory relative to ``root`` when another skill has that name."""
    names = [_label(skill) for skill in skills]
    counts = Counter(names)
    labels: list[str] = []
    for skill, name in zip(skills, names, strict=True):
        if counts[name] > 1:
            parent = skill.path.parent
            try:
                name = parent.relative_to(root).as_posix()
            except ValueError:
                name = parent.as_posix()
        labels.append(name)
    return labels


def trigger_clauses(description: str) -> tuple[str, ...]:
    """The normalised "when" clauses of a description: "use when X, or when Y" gives two clauses."""
    text = " ".join(description.split())
    clauses: list[str] = []
    for piece in _TRIGGER_RE.split(text)[1:]:  # the text after each trigger keyword
        # Only up to the first sentence end or "or when": a later sentence needs a trigger keyword of its own.
        words = [w for w in _words(_CLAUSE_END_RE.split(piece, maxsplit=1)[0]) if w not in STOP_WORDS]
        if len(words) >= TRIGGER_MIN_WORDS:
            clauses.append(" ".join(words))
    return tuple(dict.fromkeys(clauses))


def _fence_closes(match: re.Match[str], fence: tuple[str, int]) -> bool:
    """Whether a fence line closes the open ``fence``: the same character, a run at least as long, no info string."""
    run, rest = match.group(1), match.group(2)
    return run[0] == fence[0] and len(run) >= fence[1] and not rest.strip()


def _outside_fences(lines: list[str]) -> list[bool]:
    """For each line, whether it is outside fenced code: a fence line and the lines inside it are not."""
    flags: list[bool] = []
    fence: tuple[str, int] | None = None
    for line in lines:
        match = _FENCE_RE.match(line)
        if fence is None:
            flags.append(match is None)
            if match:
                fence = (match.group(1)[0], len(match.group(1)))
        else:
            flags.append(False)
            if match and _fence_closes(match, fence):
                fence = None
    return flags


def _h2_sections(lines: list[str]) -> list[tuple[str, list[str]]]:
    """The body split at its ``## `` headings; text before the first heading has an empty heading."""
    sections: list[tuple[str, list[str]]] = [("", [])]
    for line, outside in zip(lines, _outside_fences(lines), strict=True):
        if outside and line.startswith("## "):
            sections.append((line[3:].strip(), []))
            continue
        sections[-1][1].append(line)
    return sections


def _fenced(lines: list[str]) -> tuple[str, list[list[str]]]:
    """The prose of ``lines`` (fenced code removed) and the fenced blocks, each a list of lines."""
    prose: list[str] = []
    blocks: list[list[str]] = []
    block: list[str] = []
    fence: tuple[str, int] | None = None
    for line in lines:
        match = _FENCE_RE.match(line)
        if fence is None:
            if match:
                fence = (match.group(1)[0], len(match.group(1)))
            else:
                prose.append(line)
        elif match and _fence_closes(match, fence):
            blocks.append(block)
            block, fence = [], None
        else:
            block.append(line)
    if fence is not None and block:  # an unclosed fence runs to the end of the body
        blocks.append(block)
    return "\n".join(prose), blocks


def _describe(skill: Skill, label: str | None = None) -> _Doc:
    description = " ".join(skill.description.split())
    prose_parts: list[str] = []
    sections: list[_Section] = []
    code: list[tuple[str, ...]] = []
    for heading, lines in _h2_sections(skill.body.splitlines()):
        prose, blocks = _fenced(lines)
        prose_parts.append(prose)
        code.extend(_words("\n".join(b)) for b in blocks if len(b) >= CODE_MIN_LINES)
        tokens = _words("\n".join(lines))
        if heading and len(tokens) >= SECTION_MIN_WORDS:
            terms = tuple(w for w in tokens if w not in STOP_WORDS)
            sections.append(_Section(heading, tokens, terms, "\n".join(lines)))
    clauses = trigger_clauses(description)
    return _Doc(
        label=_label(skill) if label is None else label,
        body_chars=len(skill.body),
        desc_words=tuple(w for w in _words(description) if len(w) >= 3 and w not in STOP_WORDS),
        clauses=clauses,
        clause_words=tuple(frozenset(c.split()) for c in clauses),
        trigger_words=frozenset(w for c in clauses for w in c.split()),
        prose=_words("\n".join(prose_parts)),
        sections=tuple(sections),
        code=tuple(code),
    )


# ---------------------------------------------------------------- similarity


def _shingles(tokens: Sequence[str], size: int) -> frozenset[tuple[str, ...]]:
    return frozenset(tuple(tokens[i:i + size]) for i in range(len(tokens) - size + 1))


def _jaccard(a, b) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def _tfidf(docs: list[tuple[str, ...]]) -> list[dict[str, float]]:
    """L2-normalised TF-IDF vectors (smoothed idf) of the token lists."""
    n = len(docs)
    df: dict[str, int] = defaultdict(int)
    for tokens in docs:
        for token in set(tokens):
            df[token] += 1
    vectors: list[dict[str, float]] = []
    for tokens in docs:
        counts: dict[str, int] = defaultdict(int)
        for token in tokens:
            counts[token] += 1
        weights = {t: (1 + math.log(c)) * (math.log((1 + n) / (1 + df[t])) + 1) for t, c in counts.items()}
        norm = math.sqrt(sum(w * w for w in weights.values())) or 1.0
        vectors.append({t: w / norm for t, w in weights.items()})
    return vectors


def _cosine(a: dict[str, float], b: dict[str, float]) -> float:
    if len(b) < len(a):
        a, b = b, a
    return sum(w * b[t] for t, w in a.items() if t in b)


_EPS = 1e-9  # float slack: a longer prefix or a looser size bound only costs time, never a pair


def _similar(items: list[tuple[int, frozenset]], minimum: float) -> list[tuple[int, int, float]]:
    """Index pairs (k < m) of items from different units whose shingle Jaccard reaches ``minimum``.

    Each item is (unit, shingles). Prefix filtering keeps the join small when every item shares
    some boilerplate: with each set ordered rarest shingle first, two sets with Jaccard >= t share
    a shingle among the first ``|x| - ceil(t * |x|) + 1`` of each, so only those are indexed. A
    size bound (Jaccard <= smaller / larger) drops the rest before any set is intersected.
    """
    freq: Counter = Counter(gram for _, shingles in items for gram in shingles)
    prefixes: list[list[tuple]] = []
    for _, shingles in items:
        ordered = sorted(shingles, key=lambda gram: (freq[gram], gram))
        prefixes.append(ordered[: len(ordered) - math.ceil(minimum * len(ordered) - _EPS) + 1])
    index: dict[tuple, list[int]] = defaultdict(list)
    for k, prefix in enumerate(prefixes):
        for gram in prefix:
            index[gram].append(k)
    found: list[tuple[int, int, float]] = []
    for k, (unit, shingles) in enumerate(items):
        later = {m for gram in prefixes[k] for m in index[gram] if m > k}
        for m in sorted(later):
            other_unit, other = items[m]
            if other_unit == unit:
                continue
            small, large = sorted((len(shingles), len(other)))
            if small < minimum * large - _EPS:
                continue
            score = _jaccard(shingles, other)
            if score >= minimum:
                found.append((k, m, score))
    return found


def _shared_clauses(a: _Doc, b: _Doc) -> tuple[str, ...]:
    shared: list[str] = []
    for ca, words_a in zip(a.clauses, a.clause_words, strict=True):
        if any(_jaccard(words_a, words_b) >= TRIGGER_JACCARD_MIN for words_b in b.clause_words):
            shared.append(ca)
    return tuple(shared)


@dataclass
class _Acc:
    clauses: tuple[str, ...] = ()
    body: float = 0.0
    sections: list[tuple[str, str, float]] = field(default_factory=list)
    code: int = 0


_NO_OVERLAP = _Acc()  # read-only stand-in for a pair with no overlap entry


def _ordered(ua: int, ub: int) -> tuple[int, int, bool]:
    return (ua, ub, False) if ua < ub else (ub, ua, True)


def _add_body_and_sections(docs: list[_Doc], accs: dict[tuple[int, int], _Acc]) -> None:
    body_items = [(i, _shingles(d.prose, BODY_SHINGLE_WORDS)) for i, d in enumerate(docs)]
    for ia, ib, score in _similar(body_items, BODY_JACCARD_MIN):
        accs[(ia, ib)].body = score
    secs = [(i, s) for i, d in enumerate(docs) for s in d.sections]
    sec_items = [(i, _shingles(s.tokens, BODY_SHINGLE_WORDS)) for i, s in secs]
    for k, m, score in _similar(sec_items, SECTION_JACCARD_MIN):
        (ua, sa), (ub, sb) = secs[k], secs[m]
        lo, hi, swapped = _ordered(ua, ub)
        first, second = (sb, sa) if swapped else (sa, sb)
        accs[(lo, hi)].sections.append((first.heading, second.heading, score))


def _add_code(docs: list[_Doc], accs: dict[tuple[int, int], _Acc]) -> None:
    blocks = [(i, block) for i, d in enumerate(docs) for block in d.code]
    items = [(i, _shingles(block, CODE_SHINGLE_TOKENS)) for i, block in blocks]
    for k, m, _ in _similar(items, CODE_JACCARD_MIN):
        lo, hi, _ = _ordered(blocks[k][0], blocks[m][0])
        accs[(lo, hi)].code += 1


def _signals(cosine: float, vocab: float, acc: _Acc) -> tuple[str, ...]:
    """Overlap signals of one pair. A description cosine alone must be strong; a weaker one counts with a shared trigger vocabulary."""
    names: list[str] = []
    if cosine >= DESCRIPTION_COSINE_STRONG:
        names.append("description-overlap")
    elif cosine >= DESCRIPTION_COSINE_MIN and vocab >= TRIGGER_VOCAB_JACCARD_MIN:
        names.append("description-and-trigger-overlap")
    if acc.clauses:
        names.append("trigger-clause")
    if acc.body:
        names.append("body-overlap")
    if acc.sections:
        names.append("section-duplicate")
    if acc.code:
        names.append("code-duplicate")
    return tuple(names)


def _merge_hints(docs: list[_Doc]) -> tuple[PairHint, ...]:
    vectors = _tfidf([d.desc_words for d in docs])
    # Only pairs with a body, section, code or clause overlap get an entry; the rest share _NO_OVERLAP.
    accs: dict[tuple[int, int], _Acc] = defaultdict(_Acc)
    _add_body_and_sections(docs, accs)
    _add_code(docs, accs)
    out: list[PairHint] = []
    for i, j in combinations(range(len(docs)), 2):
        cosine = _cosine(vectors[i], vectors[j])
        ti, tj = docs[i].trigger_words, docs[j].trigger_words
        shares = not ti.isdisjoint(tj)  # no shared word: no shared clause and a vocabulary Jaccard of 0
        vocab = _jaccard(ti, tj) if shares else 0.0
        clauses = _shared_clauses(docs[i], docs[j]) if shares else ()
        acc = accs.get((i, j), _NO_OVERLAP)
        if clauses:
            acc = accs[(i, j)]
            acc.clauses = clauses
        signals = _signals(cosine, vocab, acc)
        if len(signals) < MERGE_MIN_SIGNALS:
            continue
        out.append(PairHint(
            a=docs[i].label, b=docs[j].label, description_cosine=cosine, trigger_jaccard=vocab,
            shared_clauses=acc.clauses, body_jaccard=acc.body,
            duplicated_sections=tuple(acc.sections), duplicated_code_blocks=acc.code,
            signals=signals,
        ))
    out.sort(key=lambda p: (-len(p.signals), -p.description_cosine, p.a, p.b))
    return tuple(out)


# ---------------------------------------------------------------- splitting


def _disjoint_when(clauses: tuple[str, ...]) -> int:
    """How many clauses can be picked greedily with no word in common."""
    chosen: set[str] = set()
    count = 0
    for clause in clauses:
        words = set(clause.split())
        if words.isdisjoint(chosen):
            count += 1
            chosen |= words
    return count


def _slug(heading: str) -> str:
    return re.sub(r"\W+", "-", heading.strip().lower()).strip("-")


def _has_run(tokens: tuple[str, ...], run: tuple[str, ...]) -> bool:
    k = len(run)
    return k > 0 and any(tokens[i:i + k] == run for i in range(len(tokens) - k + 1))


def _cross_referenced(a: _Section, b: _Section) -> bool:
    """Either section names the other's heading, or links to its anchor."""
    for x, y in ((a, b), (b, a)):
        if f"(#{_slug(y.heading)})" in x.raw or _has_run(x.tokens, _words(y.heading)):
            return True
    return False


def _section_vectors(docs: list[_Doc]) -> dict[tuple[int, int], dict[str, float]]:
    """TF-IDF of every H2 section, idf taken over the whole catalog: the words all skills share count for little."""
    keys = [(i, k) for i, d in enumerate(docs) for k in range(len(d.sections))]
    vectors = _tfidf([docs[i].sections[k].terms for i, k in keys])
    return dict(zip(keys, vectors, strict=True))


def _section_clusters(index: int, doc: _Doc, vectors) -> list[tuple[str, ...]]:
    """The H2 headings of a skill grouped where sections link (similar, or cross-referenced)."""
    count = len(doc.sections)
    adjacent: dict[int, set[int]] = {k: set() for k in range(count)}
    for a, b in combinations(range(count), 2):
        linked = _cosine(vectors[(index, a)], vectors[(index, b)]) >= SECTION_LINK_COSINE
        if linked or _cross_referenced(doc.sections[a], doc.sections[b]):
            adjacent[a].add(b)
            adjacent[b].add(a)
    seen: set[int] = set()
    groups: list[tuple[str, ...]] = []
    for start in range(count):
        if start in seen:
            continue
        seen.add(start)
        stack, members = [start], []
        while stack:
            k = stack.pop()
            members.append(doc.sections[k].heading)
            for m in adjacent[k] - seen:
                seen.add(m)
                stack.append(m)
        groups.append(tuple(members))
    return groups


def _split_hint(index: int, doc: _Doc, vectors) -> SplitHint | None:
    clusters = _section_clusters(index, doc, vectors) if len(doc.sections) > 1 else []
    disjoint = _disjoint_when(doc.clauses)
    signals: list[str] = []
    if doc.body_chars >= LONG_BODY_CHARS:
        signals.append("long-body")
    if disjoint >= DISJOINT_WHEN_MIN:
        signals.append("disjoint-when")
    if len(clusters) >= 2:
        signals.append("section-clusters")
    if len(signals) < SPLIT_MIN_SIGNALS:
        return None
    return SplitHint(doc.label, doc.body_chars, disjoint, tuple(clusters), tuple(signals))


def _paid_hint(root: str, units: list[str]) -> PaidHint | None:
    if not units:
        return None
    est = round(PAID_USD_PER_SKILL * len(units), 2)
    budget = max(1, math.ceil(2 * est))
    argv = ("dokimi", "catalog", "granularity", "--root", root, "--from-check",
            "--model", "haiku", "--reps", "3", "--budget", str(budget))
    return PaidHint(argv=argv, est_usd=est)
