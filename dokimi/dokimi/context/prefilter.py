# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Which guidance claims the sessions ever brought up, acted on, or were corrected on: a prefilter for ablation.

A guidance file (AGENTS.md, CLAUDE.md) is split into claims with the novelty layer's span
segmentation. Each claim gets three deterministic measures, read against session logs:

* **trigger**: an episode where the situation the claim describes came up. A prompt or a tool
  call shares half the claim's stemmed keywords (rounded up) with it.
* **acted**: within ``window`` events either side of the trigger, a tool call ran a command or
  touched a path the claim names in backticks. A claim naming neither is not measurable.
* **corrected**: within ``window`` events after the trigger, the user's next prompt pushes back
  on the same subject.

The output is a ranked list of **candidates for ablation**, never a verdict: an ablation run
decides. A claim with a protection tag (negation, version gate, ordering, threshold) or with
few triggers is listed but not ranked, since a rare safety rule looks like waste on a count.
"""

from __future__ import annotations

import functools
import re
from dataclasses import dataclass
from typing import Any

from dokimi.context.session_log import Event, Session
from dokimi.novelty.spans import segment

CANDIDATE = "candidate_for_ablation"
IN_USE = "in_use"
RARE = "rare_not_ranked"
KEPT = "kept_protected"
INSUFFICIENT = "insufficient_corpus"
STATUSES = (CANDIDATE, IN_USE, RARE, KEPT, INSUFFICIENT)
NOTE = ("Candidates for ablation, not verdicts: a candidate is a rule the sessions rarely brought up. "
        "Run an ablation before changing any rule. Protected and rare rules are listed, not ranked.")

_RULE_KINDS = frozenset({"sentence", "list_item", "table_row"})
_BACKTICK = re.compile(r"`([^`\n]+)`")
_WORD = re.compile(r"[a-z][a-z0-9_-]{2,}")
_PATH = re.compile(r"[/\\]|\.[A-Za-z0-9]{1,6}$")
# a shell command's runner words before the command it runs (``uv run pytest``, ``sudo make``, ``A=1 make``)
_RUNNER = r"(?:(?:[A-Za-z_]\w*=\S*|sudo|env|time|nohup|exec|npx|uv\s+run|poetry\s+run|timeout\s+\d+)\s+)*"
_QUOTED = re.compile(r"'[^']*'|\"[^\"]*\"")  # a quoted argument is text, not a command
# a heredoc body is data for the command, not commands: from the line after ``<<WORD`` to a line that is WORD alone
_HEREDOC = re.compile(r"(?m)((?<!<)<<-?[ \t]*['\"]?(\w+)['\"]?[^\n]*\n)(?:.*?\n)??[ \t]*\2[ \t]*$", re.S)
_COMMENT = re.compile(r"(?m)(?:^|\s)#.*$")
_SEGMENT = re.compile(r"&&|\|\||[;|&()\n]")
_CUE = re.compile(r"\b(no|not|don't|do not|stop|instead|wrong|forgot|should have|revert|undo|why did you)\b", re.I)
_STOP = frozenset("""
able about above after again against all also always and any are because been before being below
between both but by can could did does doing done down during each either else ensure even ever every
file files code for from further had has have having her here hers him his how however into its just
keep last like make may might more most must myself need neither new nor not now off once only onto
other ought our out over own run same see set should since some such than that the their theirs them
then there these they this those through to too under until upon use used using very via was way we
were what when where whether which while who whom why will with within without would yet you your
""".split())


@dataclass(frozen=True)
class Claim:
    """One checkable rule of a guidance file, with the words and names its triggers are matched on."""

    id: str
    text: str
    heading: str
    keywords: frozenset[str]
    commands: tuple[str, ...]
    paths: tuple[str, ...]
    protection: tuple[str, ...]

    @property
    def has_keys(self) -> bool:
        return bool(self.commands or self.paths)


@dataclass
class Tally:
    """How one claim's triggers, actions and corrections fell across a corpus."""

    sessions_triggered: int = 0
    episodes: int = 0
    acted: int = 0
    corrected: int = 0


def _stem(word: str) -> str:
    for suffix in ("ing", "ed", "es", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 4:
            return word[: -len(suffix)]
    return word


@functools.lru_cache(maxsize=8192)  # every claim is matched against the same event texts
def _stems(text: str) -> frozenset[str]:
    return frozenset(_stem(w) for w in _WORD.findall(text.lower()) if w not in _STOP)


def _is_path(key: str) -> bool:
    """A bare file name or path; a key with a space is a command line that names a file."""
    return " " not in key and bool(_PATH.search(key))


def claims_from_guidance(text: str) -> list[Claim]:
    """The rules of one guidance file: each sentence, list item or table row with a keyword."""
    claims = []
    for span in segment(text):
        if span.kind not in _RULE_KINDS:
            continue
        keywords = _stems(_BACKTICK.sub(" ", span.text))
        keys = [k.strip() for k in _BACKTICK.findall(span.text) if k.strip()]
        if not (keywords or keys or span.protection):  # a rule with nothing to match on is no claim
            continue
        claims.append(Claim(
            id=span.id, text=span.text.strip(), heading=" > ".join(span.heading_path), keywords=keywords,
            commands=tuple(k for k in keys if not _is_path(k)),
            paths=tuple(k for k in keys if _is_path(k)), protection=tuple(span.protection),
        ))
    return claims


def _names(key: str, text: str) -> bool:
    """Whether ``key`` occurs in ``text`` as a whole command, not inside a longer word."""
    words = " ".join(key.split())
    return bool(words) and re.search(rf"(?<![\w-]){re.escape(words)}(?![\w-])", " ".join(text.split())) is not None


def _unquote(text: str) -> str:
    return text.replace('"', "").replace("'", "")


# A separator, a ``#`` or a redirection sign inside quotes is text, not shell syntax: it is swapped for a
# private-use character while the command is split and cut, and swapped back where the text is used.
_SPECIAL = ";|&()\n#<>"
_TO_MASK = str.maketrans(_SPECIAL, "".join(chr(0xE000 + i) for i in range(len(_SPECIAL))))
_FROM_MASK = str.maketrans("".join(chr(0xE000 + i) for i in range(len(_SPECIAL))), _SPECIAL)
_HIDDEN = " "  # stands in for a quoted sentence in a path text: none of its words count as a path


def _mask(match: re.Match[str]) -> str:
    return match.group(0).translate(_TO_MASK)


def _hide_sentence(match: re.Match[str]) -> str:
    """A quoted argument with a space in it is a sentence, not a path; one word is kept."""
    return _HIDDEN if any(c.isspace() for c in match.group(0)[1:-1]) else match.group(0)


def _masked_segments(command: str, *, hide_spaced: bool = False) -> list[str]:
    """The command's segments with their quotes removed, still masked. A heredoc body is dropped; with
    ``hide_spaced`` a quoted argument with a space in it is replaced by a placeholder."""
    text = _HEREDOC.sub(r"\1", command or "")
    if hide_spaced:
        text = _QUOTED.sub(_hide_sentence, text)  # the same pairing of quotes as _mask
    masked = _COMMENT.sub("", _QUOTED.sub(_mask, text))
    return [_unquote(seg) for seg in _SEGMENT.split(masked)]


def _words(seg: str) -> str:
    return " ".join(seg.translate(_FROM_MASK).split())


def _segments(command: str) -> list[str]:
    """The command's segments, each with its quotes removed and its spacing normalized."""
    return [_words(seg) for seg in _masked_segments(command)]


def _invokes(key: str, command: str) -> bool:
    """Whether a shell command runs ``key``: at the start of one of its command segments (after runner words),
    so an ``echo``, a ``grep`` argument or a ``#`` comment that names it does not count. The key ends where
    its last word ends: ``make ci-local`` does not run ``make ci-local.debug``."""
    words = " ".join(_unquote(key).split())
    if not words:
        return False
    run = re.compile(rf"{_RUNNER}{re.escape(words)}(?=\s|$)")
    return any(run.match(seg) for seg in _segments(command) if seg)


# the arguments of echo and printf are printed, not read or written; a redirection target is written
_PRINTS = re.compile(rf"{_RUNNER}(?:echo|printf)(?=\s|$)")
_REDIRECT = re.compile(r">>?\s*([^\s;&|<>]+)")


def _shell_paths(command: str) -> str:
    """The text of a shell command that can name a file it reads or writes: its redirection targets and the
    segments that are not printing commands. Comments, heredoc bodies, printed text and quoted sentences are
    left out; a redirection sign inside quotes is not a redirection."""
    parts: list[str] = []
    for seg in _masked_segments(command, hide_spaced=True):
        parts.extend(target.translate(_FROM_MASK) for target in _REDIRECT.findall(seg))
        text = _words(seg)
        if not _PRINTS.match(text):
            parts.append(text)
    return " ".join(parts)


def _triggers(claim: Claim, event: Event) -> bool:
    """Half the claim's keywords (rounded up) in the event: one word of a two-word rule is enough.

    A rule with no prose keywords triggers on a prompt that names one.
    """
    if not claim.keywords:
        return event.kind == "prompt" and _named(claim, event.text)
    return len(claim.keywords & _stems(event.text)) >= (len(claim.keywords) + 1) // 2


def _slashes(text: str) -> str:
    return text.replace("\\", "/")  # a Windows path is the same path with backslashes


def _at_path(key: str, text: str) -> bool:
    """Whether ``key`` occurs in ``text`` from a path-segment start, so ``a.md`` does not match ``data.md``."""
    name = _slashes(key).removeprefix("./")
    text = _slashes(text)
    # a file name ends where the name ends (``a.md`` is not ``a.mdx`` or ``a.md.bak``); a directory
    # (``docs/``) is followed by its entries, so it has no end boundary
    end = "" if name.endswith("/") else r"(?![\w-]|\.\w)"
    return bool(name) and re.search(rf"(?<![\w.-]){re.escape(name)}{end}", text) is not None


def _path_named(claim: Claim, texts: tuple[str, ...]) -> bool:
    return any(_at_path(k, t) for k in claim.paths for t in texts)


def _named(claim: Claim, text: str) -> bool:
    """Whether a prompt's text names one of the claim's commands or paths."""
    return any(_names(k, text) for k in claim.commands) or _path_named(claim, (text,))


def _acts(claim: Claim, event: Event) -> bool:
    if event.kind != "tool":
        return False
    return (any(_invokes(k, event.command) for k in claim.commands)
            or any(_file_named(k, event.paths) for k in claim.commands)
            or _path_named(claim, (*event.paths, _shell_paths(event.command))))


def _file_named(key: str, paths: tuple[str, ...]) -> bool:
    """Whether a touched file's name is ``key``: a bare file name such as ``Makefile`` is a command key, and an
    edit of that file is the named file being acted on."""
    return any(_slashes(p).rsplit("/", 1)[-1] == key for p in paths)


def _corrects(claim: Claim, event: Event) -> bool:
    if event.kind != "prompt" or not _CUE.search(event.text):
        return False
    return (bool(claim.keywords & _stems(event.text))
            or any(_names(k, event.text) for k in claim.commands)
            or any(_at_path(k, event.text) for k in claim.paths))


def _scan(claim: Claim, events: list[Event], window: int) -> tuple[int, int, int]:
    """Episodes, acted and corrected counts of one session; one episode spans its trigger's window."""
    episodes = acted = corrected = 0
    i = 0
    while i < len(events):
        if not _triggers(claim, events[i]):
            i += 1
            continue
        episodes += 1
        around = events[max(0, i - window): i + window + 1]
        after = events[i + 1: i + window + 1]
        acted += int(claim.has_keys and any(_acts(claim, e) for e in around))
        next_prompt = next((e for e in after if e.kind == "prompt"), None)  # the user's next word, only
        corrected += int(next_prompt is not None and _corrects(claim, next_prompt))
        i += window + 1
    return episodes, acted, corrected


def tally(claim: Claim, sessions: list[Session], window: int) -> Tally:
    out = Tally()
    for session in sessions:
        episodes, acted, corrected = _scan(claim, session.events, window)
        out.episodes += episodes
        out.acted += acted
        out.corrected += corrected
        out.sessions_triggered += int(episodes > 0)
    return out


def status_of(claim: Claim, tally_: Tally, sessions_total: int, *, min_sessions: int,
              rare_max: int, in_use_rate: float) -> str:
    if claim.protection:
        return KEPT
    if sessions_total < min_sessions:
        return INSUFFICIENT
    if tally_.sessions_triggered == 0:
        return CANDIDATE
    if tally_.sessions_triggered <= rare_max:
        return RARE
    if tally_.sessions_triggered / sessions_total >= in_use_rate:
        return IN_USE
    return CANDIDATE


def _flags(claim: Claim, tally_: Tally) -> list[str]:
    flags = []
    if not claim.has_keys:
        flags.append("acted_not_measurable")
    if tally_.episodes >= 2 and tally_.corrected * 2 >= tally_.episodes:
        flags.append("user_overrides: the user pushed back on half or more of the triggers; check the wording")
    return flags


def _ratio(part: int, whole: int) -> float | None:
    return round(part / whole, 3) if whole else None


def _row(claim: Claim, t: Tally, status: str, sessions_total: int) -> dict[str, Any]:
    return {
        "id": claim.id,
        "rank": None,
        "status": status,
        "text": claim.text if len(claim.text) <= 200 else claim.text[:197] + "...",
        "heading": claim.heading,
        "protection": list(claim.protection),
        "keys": {"commands": list(claim.commands), "paths": list(claim.paths), "keywords": sorted(claim.keywords)},
        "sessions_triggered": t.sessions_triggered,
        "trigger_rate": _ratio(t.sessions_triggered, sessions_total),
        "episodes": t.episodes,
        "acted": t.acted if claim.has_keys else None,
        "acted_rate": _ratio(t.acted, t.episodes) if claim.has_keys else None,
        "corrected": t.corrected,
        "correction_rate": _ratio(t.corrected, t.episodes),
        "flags": _flags(claim, t),
    }


def _rank_key(row: dict[str, Any]) -> tuple[int, float]:
    """Fewest triggered sessions first (an exact count: the rates share one session total), then the lowest acted share."""
    acted = row["acted"]
    share = 0.5 if acted is None or not row["episodes"] else acted / row["episodes"]
    return (row["sessions_triggered"], share)


def build_report(guidance_name: str, guidance_text: str, sessions: list[Session], *, window: int,
                 min_sessions: int, rare_max: int, in_use_rate: float) -> dict[str, Any]:
    """The prefilter report: every claim with its status, the candidates ranked first."""
    total = len(sessions)
    rows = []
    for claim in claims_from_guidance(guidance_text):
        t = tally(claim, sessions, window)
        status = status_of(claim, t, total, min_sessions=min_sessions, rare_max=rare_max, in_use_rate=in_use_rate)
        rows.append(_row(claim, t, status, total))
    candidates = sorted((r for r in rows if r["status"] == CANDIDATE), key=_rank_key)
    for rank, row in enumerate(candidates, 1):
        row["rank"] = rank
    others = sorted((r for r in rows if r["status"] != CANDIDATE), key=lambda r: STATUSES.index(r["status"]))
    return {
        "format": "session_prefilter",
        "format_version": 1,
        "guidance": guidance_name,
        "sessions": total,
        "parameters": {"window": window, "min_sessions": min_sessions, "rare_max": rare_max,
                       "in_use_rate": in_use_rate},
        "note": NOTE,
        "claims": candidates + others,
    }


def render_table(report: dict[str, Any]) -> str:
    head = f"{'rank':>4}  {'status':<20} {'trig.sess':>10} {'episodes':>8} {'acted':>7} {'corr.':>6}  claim"
    lines = [head]
    for row in report["claims"]:
        acted = "n/a" if row["acted"] is None else f"{row['acted']}/{row['episodes']}"
        rank = "-" if row["rank"] is None else str(row["rank"])
        trig = f"{row['sessions_triggered']}/{report['sessions']}"
        lines.append(f"{rank:>4}  {row['status']:<20} {trig:>10} {row['episodes']:>8} {acted:>7} "
                     f"{row['corrected']:>6}  {row['text'][:70]}")
    counts = {s: sum(1 for r in report["claims"] if r["status"] == s) for s in STATUSES}
    lines.append("")
    lines.append(f"{report['sessions']} session(s), {len(report['claims'])} claim(s): "
                 + ", ".join(f"{s} {n}" for s, n in counts.items() if n))
    lines.append(report["note"])
    return "\n".join(lines)
