# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Spans from an opener to the first closer after it, in time linear in the text (#888).

``opener(.*?)closer`` tries every opener and scans to the end of the text for a closer that is
not there, so a text of n openers costs n squared steps. These helpers find the closers once, so an
opener with no closer after it costs no scan: no later opener of the same tag has one either, and
openers of other tags go on to their own closers.
"""

from __future__ import annotations

import re
from bisect import bisect_left
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from functools import lru_cache


def pairs(text: str, opener: re.Pattern[str], closer: re.Pattern[str]) -> Iterator[tuple[re.Match[str], re.Match[str]]]:
    """``(opener, closer)`` matches of ``opener.*?closer``, as ``finditer`` would give them.

    The closer for an opener is the first one after it with the same group 1 (any closer when the
    patterns have no group). Closer matches must not overlap one another or an opener.
    """
    found: dict[str, list[re.Match[str]]] = {}
    for m in closer.finditer(text):
        found.setdefault(m.group(1) if m.re.groups else "", []).append(m)
    cursor: dict[str, int] = {}  # per key: the closers before this index start before the last opener's end
    pos = 0
    while (o := opener.search(text, pos)) is not None:
        key = o.group(1) if o.re.groups else ""
        closers = found.get(key, [])
        k = cursor.get(key, 0)
        if k and closers[k - 1].start() >= o.end():  # an opener that ends before the last one did
            k = bisect_left(closers, o.end(), 0, k, key=lambda c: c.start())
        while k < len(closers) and closers[k].start() < o.end():
            k += 1
        cursor[key] = k
        if k == len(closers):
            pos = o.start() + 1  # a key with no closer after this opener has none after a later one
            continue
        yield o, closers[k]
        pos = closers[k].end()


def replace_pairs(text: str, opener: re.Pattern[str], closer: re.Pattern[str], repl: Callable[[str], str]) -> str:
    """``text`` with each span :func:`pairs` finds, opener to closer, replaced by ``repl(span)``."""
    out, pos = [], 0
    for o, c in pairs(text, opener, closer):
        out += [text[pos:o.start()], repl(text[o.start():c.end()])]
        pos = c.end()
    return "".join(out) + text[pos:]


_CASE_WORDS = r"|(?<![\w$.-])(?:case|esac)(?![\w.-])"  # a mark too: the ``)`` of a case pattern closes no substitution
_QUOTE_MARKS = re.compile(r"[\\'\"\n#$()`}]" + _CASE_WORDS)
_SCOPES = {"(": "(", "{": "{"}  # the character after a `$` that opens a scope
_CLOSERS = {")": "(", "}": "{["}  # `[` is a ``${`` inside double quotes, where a single quote is plain text
_COMMAND = ("(", "`", "C")  # the top of a stack where the shell reads commands: ``C`` is a ``case`` inside a ``$(``, whose patterns end in a ``)``
_COMMAND_KEYWORDS = ("then", "do", "else", "elif", "if", "while", "until")

#: The delimiter of a heredoc after ``<<``: group 1 is the quote of a word that is one quoted piece (group 2 is then its text), else empty and group 2 is the word as written, unquoted and quoted pieces side by side (``EOF"-END"``, ``'A''B'``).
#: A word with a backslash, a ``(`` after it, or a double-quoted piece with a backslash opens nothing (``<<EOF\-END``, ``<<$(printf EOF)``, ``<<"a\$b"``); a quote that never closes is no part of the word (``<<AB'``).
HEREDOC_WORD = (r"(['\"]?)(?!(?<=\")[^\"\n]*\\)((?<=['\"])(?:(?!\1)[^\n])+(?=\1(?![^\s;&|<>()\\]))"
                r"|(?<!['\"])(?!(?:''|\"\")+(?![^\s;&|<>()\\]))(?:'[^'\n]*'|\"[^\"\\\n]*\"|[^\s;&|<>()'\"\\-])(?:'[^'\n]*'|\"[^\"\\\n]*\"|[^\s;&|<>()'\"\\]++)*+)(?!['\"]?[\\(])\1")
_QUOTED_PIECE = re.compile(r"'([^']*)'|\"([^\"]*)\"")


def dequote_word(word: str) -> str:
    """``word`` as the shell reads it after quote removal: ``EOF"-END"`` is ``EOF-END``."""
    if "'" not in word and '"' not in word:
        return word
    return _QUOTED_PIECE.sub(lambda m: m.group(m.lastindex or 1), word)


def _at_command(text: str, at: int) -> bool:
    """Whether a word at ``at`` starts a command: at the start, after ``;&|(){`` a newline or a backtick, or after a keyword such as ``then``."""
    while at and text[at - 1] in " \t":
        at -= 1
    if not at or text[at - 1] in ";&|(){\n`":
        return True
    return any(text.endswith(k, 0, at) and not (at > len(k) and (text[at - len(k) - 1].isalnum() or text[at - len(k) - 1] in "_-.$")) for k in _COMMAND_KEYWORDS)


def _case_scope(text: str, m: re.Match[str], quote: str) -> str:
    """The quote stack after ``case`` or ``esac`` at the start of a command inside a ``$(``: ``C`` stands for the ``case``, where a ``)`` ends a pattern and pops nothing, until its ``esac``."""
    top = quote[-1:]
    if top not in ("(", "C") or not _at_command(text, m.start()):
        return quote
    if m.group() == "case":
        return quote + "C"
    return quote[:-1] if top == "C" else quote


def _starts_word(text: str, at: int) -> bool:
    """Whether the character at ``at`` begins a shell word: at the start, after a blank, or after a ``;&|()`` no backslash escapes; a line continuation before it is removed first."""
    while at:
        before = text[at - 1]
        if not (before.isspace() or before in ";&|()"):
            return False
        first = at - 1
        while first and text[first - 1] == "\\":
            first -= 1
        if (at - 1 - first) % 2 == 0:
            return True
        if before != "\n":
            return False
        at -= 2  # `a \<newline># c`: the shell joins the lines, so the `#` follows the blank
    return True


def _inert(quote: str) -> bool:
    """Whether a quote, a comment or a ``${...}`` is open at the end of the ``quote`` stack: ``<<`` and ``$((`` there open nothing."""
    return quote[-1:] in ("'", '"', "#", "{", "[", "A")


def _backtick(quote: str) -> str:
    """The quote stack after a backtick: it closes the open substitution, opens one, and is plain text in single quotes."""
    top = quote[-1:]
    return quote if top == "'" else quote[:-1] if top == "`" else quote + "`"


def _scope_after_dollar(text: str, at: int, quote: str) -> tuple[str, int]:
    """The quote stack after a ``$`` that ends at ``at``: a ``$(`` or ``${`` opens a scope, ``[`` for a ``${`` in double quotes; none in single quotes; a ``$'...'`` is read whole."""
    top = quote[-1:]
    if text.startswith("'", at) and top in ("", *_COMMAND):  # in ``$'...'`` a backslash escapes the next character, an apostrophe too
        end = at + 1
        while end < len(text) and text[end] != "'":
            end += 2 if text[end] == "\\" else 1
        return quote, min(end + 1, len(text))
    scope = _SCOPES.get(text[at:at + 1], "") if top != "'" else ""
    return quote + ("[" if scope == "{" and top in ('"', "[") else scope), at + len(scope)


def _bracket(quote: str, mark: str) -> str:
    """The quote stack after a ``(``, ``)`` or ``}``: a ``(`` inside a ``$(`` nests, a closer ends the scope it matches."""
    top = quote[-1:]
    if mark == "(":
        return quote + "(" if top == "(" else quote
    return quote[:-1] if top in _CLOSERS[mark] else quote


def _quote_step(text: str, m: re.Match[str], quote: str) -> tuple[str, int]:
    """The quote stack after the mark ``m`` (one of ``_QUOTE_MARKS``) where ``quote`` was open, and where the scan goes on.

    The stack holds ``'``, ``"``, ``#`` (a comment, which runs to the end of its line), ``(`` for each open ``$(`` (or ``(`` inside one), `` ` `` for a backtick substitution, ``{`` for a ``${`` and ``[`` for one inside double quotes, where a single quote is plain text:
    the quotes in a substitution or an expansion are its own, apart from the double quote around it, and a ``#`` in a ``${...}`` starts no comment.
    """
    mark, at = m.group(), m.end()
    top = quote[-1:]
    if mark == "\n":  # a comment ends here; a quote runs on to its closing mark, over lines
        return (quote[:-1] if top == "#" else quote), at
    if top == "#":
        return quote, at
    if mark == "#":  # `a#b`, `$#` and `\;#` start no comment: only a blank or an unescaped `;&|()` before it does
        return (quote + "#" if top in ("", *_COMMAND) and _starts_word(text, m.start()) else quote), at
    if mark == "\\":
        return quote, at + (top != "'")
    if mark == "$":
        return _scope_after_dollar(text, at, quote)
    if mark in ("(", ")", "}", "case", "esac"):
        return (_case_scope(text, m, quote) if len(mark) > 1 else _bracket(quote, mark)), at
    if mark == "`":
        return _backtick(quote), at
    if mark == "'" and top == "[":
        return quote, at
    if top in ("", *_COMMAND, "{", "[", "A"):
        return quote + mark, at
    return (quote[:-1] if top == mark else quote), at


def _quote_after(text: str, at: int, end: int, quote: str, step: Callable[[re.Match[str], str], tuple[str, int]] | None = None) -> tuple[str, int]:
    """The quote stack (see :func:`_quote_step`, or ``step``) open at ``end``, scanning ``text`` from ``at`` where ``quote`` was open."""
    while (m := _QUOTE_MARKS.search(text, at, end)) is not None:
        quote, at = step(m, quote) if step else _quote_step(text, m, quote)
    return quote, max(at, end)


quote_after, quote_inert = _quote_after, _inert  # for a reader of its own lines: the stack at a ``<<`` and whether it hides the ``<<``

_ARITHMETIC_MARKS = re.compile(r"[\\'\"\n#()$`}<]" + _CASE_WORDS)


class _ArithmeticReader:
    """The ``(start, end)`` of each ``$((...))`` and bare ``((...))`` in ``text`` (``spans``), closed by the parenthesis that balances its two; an opener with none gives no span.

    A ``$((`` inside single quotes or a comment opens nothing, as a backslash before the ``$`` does not either; a bare ``((`` opens nothing inside any quote or comment.
    The text is read as far as ``read`` is asked, and ``skip`` jumps over a heredoc body, which is data and leaves no quote open.
    """

    def __init__(self, text: str, heredoc: HeredocPattern):
        self.text, self.heredoc = text, heredoc
        self.spans: list[tuple[int, int]] = []
        self.quote, self.outer, self.depth, self.start, self.pos = "", "", 0, 0, 0
        self.line, self.line_quote = 0, ""  # where the line being read begins and the quotes open there
        self.substitutions: list[tuple[int, int]] = []  # inside a span: where a ``$(`` or backtick, not the arithmetic, is what a ``<<`` is in
        self.entered = -1  # where the substitution open now began, -1 when none
        self.pairs: dict[int, int] | None = None  # each ``(`` of the text and the ``)`` that closes it, found on the first ``((``

    def _opener(self, mark: str) -> int:
        """How many characters from ``self.pos`` (after ``mark``) complete a ``$((`` or a bare ``((``, such as ``((x=1<<3))`` or a ``for ((...))`` header; 0 when ``mark`` opens none.

        A bare ``((`` is a subshell, ``((cat <<EOF``, unless a ``))`` closes it.
        """
        text, at = self.text, self.pos
        if mark == "$":
            return 2 if self.quote[-1:] not in ("'", "#") and text.startswith("((", at) else 0
        return 1 if mark == "(" and self.quote[-1:] in ("", *_COMMAND) and text.startswith("(", at) and self._closes(at) else 0

    def _closes(self, at: int) -> bool:
        """Whether the ``((`` ending at ``at + 1`` is closed by a ``))``, on this line or a later one: the parenthesis that closes its inner ``(`` is followed at once by the one that closes its outer.

        The parentheses of the whole text are matched once, so many ``((`` read it once; ``((cat <<EOF`` ... ``) | cat)`` is a subshell, ``((x=1<<3`` and ``))`` on the next line is arithmetic.
        """
        if self.pairs is None:
            self.pairs, stack = {}, []
            for m in re.finditer(r"\\.|[()]", self.text, re.DOTALL):  # an escaped parenthesis is a plain character
                if m.group() == "(":
                    stack.append(m.start())
                elif m.group() == ")" and stack:
                    self.pairs[stack.pop()] = m.start()
        inner = self.pairs.get(at)
        return inner is not None and self.pairs.get(at - 1) == inner + 1

    def read(self, upto: int) -> None:
        """Read the marks that start at or before ``upto``, and on to the end of a span open there.

        A span open inside a ``$(`` or backtick of its own at ``upto`` is read no further: a ``<<`` there is a heredoc, whose body ``skip`` jumps over before the substitution's closer is read.
        """
        self._read_to(upto + 1)
        if self.depth and self.quote[-1:] not in _COMMAND:
            self._read_to(len(self.text), until_closed=True)
        if not self.depth:
            self.pos = max(self.pos, upto + 1)  # no mark before it: a later call does not look again

    def _read_to(self, limit: int, until_closed: bool = False) -> None:
        text = self.text
        while (m := _ARITHMETIC_MARKS.search(text, self.pos, limit)) is not None:
            mark, self.pos = m.group(), m.end()
            if mark == "<":
                if self.depth and self.quote[-1:] in _COMMAND and (o := self.heredoc.opener.match(text, m.start())):
                    self.pos = o.end()  # the delimiter is literal text: ``<<"${"`` opens no expansion
            elif self.depth:
                if mark in "()" and not self.quote:
                    self.depth += 1 if mark == "(" else -1
                    if not self.depth:
                        self.spans.append((self.start, self.pos))
                        self.quote = self.outer  # `"$((1<<3))"`: the quote around the span is still open after it
                        if until_closed:  # what follows is read as it comes, so a heredoc body is skipped before its quotes are read
                            return
                else:  # a `$(`, backtick or `${` inside has quotes of its own: `$(( $(echo "$(echo "))")") << 3 ))`
                    self._inner(m)
            elif taken := self._opener(mark):
                self.start, self.depth, self.outer, self.quote = m.start(), 2, self.quote, ""  # inside, quotes start afresh
                self.pos += taken
                self.line, self.line_quote = self.pos, ""
            else:
                self.quote, self.pos = _quote_step(text, m, self.quote)
        if until_closed:
            self.pos = max(self.pos, limit)  # no mark is left to read, so a later opener does not search the tail again

    def _inner(self, m: re.Match[str]) -> None:
        """Read the mark ``m`` inside the open span, and keep where a substitution begins and ends there; a heredoc opened on a line of a substitution has its body skipped, as its quotes are not read."""
        before = self.quote
        was = before[-1:] in _COMMAND
        self.quote, self.pos = self._inner_step(m, self.quote)
        if m.group() == "\n":
            if before.removesuffix("#")[-1:] in _COMMAND and (words := self.heredoc.line_words(self.text, self.line, m.start(), self.line_quote, self._inner_step)):  # a comment after the opener ends here
                self.pos = max(self.pos, self.heredoc.bodies_end(self.text, m.start(), words))
            self.line, self.line_quote = self.pos, self.quote
        now = self.quote[-1:] in _COMMAND
        if now and not was:
            self.entered = self.pos
            if len(self.quote) > len(before):  # a new substitution, not the return to the one a quote or comment was opened in: its line holds no opener before it
                self.line, self.line_quote = self.pos, self.quote
        elif was and not now:
            self.substitutions.append((self.entered, m.start()))
            self.entered = -1

    def in_substitution(self, at: int) -> bool:
        """Whether ``at``, inside a span, is read as the shell's own command line, in a ``$(`` or backtick, and not as arithmetic."""
        i = bisect_left(self.substitutions, (at + 1,))
        return (i > 0 and self.substitutions[i - 1][1] > at) or 0 <= self.entered <= at

    def _inner_step(self, m: re.Match[str], quote: str) -> tuple[str, int]:
        """The quote stack after a mark inside the open span where ``quote`` was open: a ``$((`` or a bare ``((`` in a ``$(`` nested there pushes two ``A``, the grouping parentheses of arithmetic, so a ``<<`` in it is told from one in a ``$(``."""
        top, mark = quote[-1:], m.group()
        if top == "A" and mark in "()":
            return (quote + "A" if mark == "(" else quote[:-1]), m.end()
        if mark == "$" and top not in ("'", "#") and self.text.startswith("((", m.end()):
            return quote + "AA", m.end() + 2
        if mark == "(" and top in _COMMAND and self.text.startswith("(", m.end()) and self._closes(m.end()):
            return quote + "AA", m.end() + 1
        return _quote_step(self.text, m, quote)

    def skip(self, to: int) -> None:
        """Go on from ``to``, past a heredoc body, with the quotes and substitutions open at its opener's line still open; a span left open has been read to the end of the text, so ``to`` is no further."""
        self.pos = max(self.pos, to)


@lru_cache(maxsize=256)
def _closed_by(full: re.Pattern[str], group: int, delimiter: str) -> re.Pattern[str]:
    """``full`` with its backreference ``\\<group>`` to the delimiter as written replaced by the text ``delimiter`` a closing line holds."""
    return re.compile(full.pattern.replace(f"\\{group}", re.escape(delimiter)), full.flags)


@dataclass(frozen=True)
class HeredocPattern:
    """A heredoc: ``opener``, the rest of its line, a body, then a line holding only the opener's delimiter (``word`` is its group).

    ``full`` is the one regex of the whole heredoc and ``lines`` finds each line (group 1), which closes a heredoc as it is or without the whitespace around it.
    ``opener`` is the part of ``full`` up to the delimiter, without the rest of the line (an opener inside a quote, which runs over lines
    to its closing mark, ``grep '<<EOF' f``, or inside ``$((...))``, ``$((1<<3))``, or a ``#`` comment, ``# usage: cat <<EOF``, is no opener, and a quoted delimiter is read up to its closing quote): the newline
    that ends the line is found once for the openers on it. The delimiter word is atomic in both
    regexes, so a line naming a prefix of it never closes the heredoc (#930). ``unclosed`` is the opener,
    the rest of its line and everything after it (same groups as ``full``): a heredoc no line closes
    runs to the end of the input, as in a shell. ``gap`` is the number of characters the closing line
    must start past the end of the opener's line (1 when ``full`` wants a body line first). ``before`` and ``after``
    are the characters ``full`` allows ahead of and after the delimiter on its closing line (all whitespace when None).
    """

    full: re.Pattern[str]
    opener: re.Pattern[str]
    lines: re.Pattern[str]
    unclosed: re.Pattern[str]
    word: int
    gap: int = 0
    before: str | None = None
    after: str | None = None
    quote: int = 0

    def delimiter(self, opener: re.Match[str]) -> str:
        """The line that closes the heredoc ``opener`` opens: its word after quote removal, ``EOF-END`` for ``EOF"-END"`` (``quote`` is the group of the quote of a word that is one quoted piece, read up to it already)."""
        word = opener.group(self.word)
        return word if self.quote and opener.group(self.quote) else dequote_word(word)

    def _full_for(self, opener: re.Match[str], delimiter: str) -> re.Pattern[str]:
        """``full``, or when the closing line is not the word as written, the pattern of ``full`` with its backreference to the word replaced by the closing line."""
        return self.full if delimiter == opener.group(self.word) else _closed_by(self.full, self.word, delimiter)

    def matches(self, text: str) -> Iterator[re.Match[str]]:
        """The matches of ``full``, then ``unclosed`` for the first opener that no line closes, which ends the scan.

        Only a line that is the opener's whole delimiter, starting ``gap`` past the end of the opener's line, closes it.
        A closing line right after the opener, where ``full`` wants a body line, leaves the opener uncut as it always was.
        The start of the last line of each delimiter is found once; a line that is no opener's delimiter is not kept.
        """
        openers = self._openers(text)
        if not openers:
            return
        last = dict.fromkeys((d for _, _, d in openers), -1)
        cores: dict[str, dict[int, set[str]]] = {}
        for delimiter in last:
            cores.setdefault(self._core(delimiter), {}).setdefault(len(delimiter), set()).add(delimiter)
        for line in self.lines.finditer(text):
            for delimiter in self._closed(line.group(1), cores.get(self._core(line.group(1)), {})):
                last[delimiter] = line.start()
        pos = 0
        for o, line_end, delimiter in openers:
            if o.start() < pos:
                continue
            closing = last[delimiter]
            if closing >= line_end + self.gap:
                m = self._full_for(o, delimiter).match(text, o.start())
                assert m is not None  # a closing line past the opener is what ``full`` ends on
                yield m
                pos = m.end()
            elif closing < line_end:
                m = self.unclosed.match(text, o.start())
                assert m is not None  # the opener's line has a newline, so ``unclosed`` reaches the end
                yield m
                return
            else:
                pos = o.start() + 1

    def _core(self, line: str) -> str:
        """``line`` without the characters ``full`` allows before and after a delimiter."""
        return line.lstrip(self.before).rstrip(self.after)

    def _closed(self, line: str, by_size: dict[int, set[str]]) -> Iterator[str]:
        """The delimiters (``by_size``: those of the core of ``line``, by length) that ``full`` reads ``line`` as: an allowed run, the delimiter, an allowed run.

        A delimiter may start or end in such characters (``<<' a'``). Only the positions it can start at are read, and for a length
        with fewer delimiters than positions, each delimiter is searched for in them at once, so neither the delimiters that share a core
        nor the lengths of a line of blanks are each tried against every position.
        """
        end = len(line.rstrip(self.after))
        lead = len(line) - len(line.lstrip(self.before))
        for size, delimiters in by_size.items():
            first, last = max(0, end - size), min(lead, len(line) - size)
            if len(delimiters) < last - first + 1:
                yield from (d for d in delimiters if line.find(d, first, last + size) >= 0)
                continue
            yield from delimiters.intersection(line[i:i + size] for i in range(first, last + 1))

    def _openers(self, text: str) -> list[tuple[re.Match[str], int, str]]:
        """Each opener with the start of the line after its own and its delimiter; an opener whose line has no end is left out."""
        openers, start, eol = [], 0, -1
        quote, scanned = "", 0
        reader, next_span = _ArithmeticReader(text, self), 0
        words: list[str] = []  # the delimiters opened on the line that ends at ``eol``: the lines after it are their bodies
        while (o := self.opener.search(text, start)) is not None:
            if words and o.start() > eol:
                quote, scanned = self._after_bodies(text, eol, scanned, quote, words, reader)
                words = []
            quote, scanned = _quote_after(text, scanned, o.start(), quote)
            reader.read(o.start())
            arithmetic = reader.spans
            while next_span < len(arithmetic) and arithmetic[next_span][1] <= o.start():
                next_span += 1
            in_arithmetic = next_span < len(arithmetic) and arithmetic[next_span][0] <= o.start() and not reader.in_substitution(o.start())
            escaped = scanned > o.start()  # a backslash before the first ``<`` made it a literal one: ``echo \<< f``
            if _inert(quote) or in_arithmetic or escaped:  # ``grep '<<EOF' f``, ``$((1<<3))``: ``<<`` opens nothing
                start = o.start() + 1
                continue
            if o.end() > eol:  # delimiters end in increasing order, so one newline serves every opener before it
                eol = text.find("\n", o.end())
                if eol < 0:  # no opener from here on has a line end
                    break
            delimiter = o.group(self.word)
            if "'" in delimiter or '"' in delimiter:  # quote removal: most words have none, and this loop runs per opener
                delimiter = self.delimiter(o)
            openers.append((o, eol + 1, delimiter))
            words.append(delimiter)
            scanned = max(scanned, o.end())  # the delimiter is literal text: ``<<"${"`` opens no expansion
            reader.skip(o.end())
            start = o.start() + 1
        return openers

    def _after_bodies(self, text: str, eol: int, scanned: int, quote: str, words: list[str], reader: _ArithmeticReader) -> tuple[str, int]:
        """The quote open and the position scanned to, once the bodies of ``words`` (opened on the line that ends at ``eol``) are passed.

        A quote still open at the end of that line leaves no body to pass. Otherwise each body runs to the first line that is its delimiter, or to the end.
        """
        quote, scanned = _quote_after(text, scanned, eol, quote)
        if quote[-1:] in ("'", '"'):
            return quote, scanned
        reader.read(eol)
        pos = self.bodies_end(text, eol, words)
        reader.skip(pos)
        return quote.removesuffix("#"), max(scanned, pos)

    def bodies_end(self, text: str, eol: int, words: list[str]) -> int:
        """Where the bodies of ``words``, opened on the line that ends at ``eol``, end: each runs to the first line that is its delimiter, or to the end."""
        pos = eol + 1
        for word in words:
            core = self._core(word)
            for line in self.lines.finditer(text, pos):
                pos = line.end() + 1
                if self._core(line.group(1)) == core and next(self._closed(line.group(1), {len(word): {word}}), None) is not None:
                    break
            else:
                pos = len(text) + 1
        return pos

    def line_words(self, text: str, start: int, end: int, quote: str, step: Callable[[re.Match[str], str], tuple[str, int]] | None = None) -> list[str]:
        """The delimiters of the openers on ``text[start:end]``, a line that begins with ``quote`` open, its marks read by ``step``; an opener in a quote, in arithmetic or after a backslash opens nothing."""
        words, pos, scanned = [], start, start
        while (o := self.opener.search(text, pos, end)) is not None:
            pos = o.start() + 1
            quote, scanned = _quote_after(text, scanned, o.start(), quote, step)
            if not _inert(quote) and scanned <= o.start():
                words.append(self.delimiter(o))
                scanned = max(scanned, o.end())
        return words

    def sub(self, repl: Callable[[re.Match[str]], str], text: str) -> str:
        """``full.sub(repl, text)``."""
        out, pos = [], 0
        for m in self.matches(text):
            out += [text[pos:m.start()], repl(m)]
            pos = m.end()
        return "".join(out) + text[pos:]
