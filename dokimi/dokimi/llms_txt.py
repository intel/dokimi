# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Static checks of an ``llms.txt`` file: the agent-facing index of a docs site.

This check uses no network or LLM. It checks the structure the llms.txt convention asks for and the
positioning sections agents use to decide when a tool applies and when it does not.
"""

from __future__ import annotations

import re
from bisect import bisect_left
from collections.abc import Iterator
from dataclasses import asdict, dataclass

from markdown_it import MarkdownIt
from markdown_it.rules_block import StateBlock, html_block
from markdown_it.rules_inline import StateInline, link
from markdown_it.token import Token

from dokimi.code_spans import CodeSpanScanner

#: Above this an index stops being a cheap first read; the llms.txt convention says compact.
TOKEN_BUDGET = 2000

_H1 = re.compile(r"^ {0,3}#\s+\S")
_H2 = re.compile(r"^ {0,3}##\s+(.+?)\s*$")
_DESCRIPTION = re.compile(r"[^\S\n]*:[^\S\n]*\S")
_WHEN_NOT = re.compile(r"\bwhen\s+not\b|\bnot\s+for\b|\bavoid\b|\balternatives?\b", re.I)
_WHEN = re.compile(r"\bwhen\s+to\s+use\b|\buse\s+(it\s+)?(for|when)\b|\buse\s+cases?\b", re.I)


@dataclass(frozen=True)
class Finding:
    """One problem: a stable check id, its severity, where, what is wrong and the fix."""

    check: str
    severity: str  # "fail" | "warn"
    line: int
    message: str
    fix: str
    identity: str = ""

    def as_dict(self) -> dict:
        fields = asdict(self)
        del fields["identity"]
        return fields

def estimate_tokens(text: str) -> int:
    """Rough token count, four characters a token, enough for a budget check."""
    return (len(text) + 3) // 4


def _title_findings(lines: list[str]) -> list[Finding]:
    h1 = [i for i, line in enumerate(lines, 1) if _H1.match(line)]
    if not h1:
        return [Finding("title", "fail", 1, "no H1 title",
                        "Open the file with `# <Product name>`.")]
    found = []
    if len(h1) > 1:
        found.append(Finding("title", "warn", h1[1], f"{len(h1)} H1 titles",
                             "Keep one H1; make the others `##` sections."))
    after = [line for line in lines[h1[0]:] if line.strip()]
    if not after or not re.match(r"^ {0,3}>\s*\S", after[0]):
        found.append(Finding("summary", "fail", h1[0] + 1, "no blockquote summary under the title",
                             "Add `> <one line: what it does, for whom>` after the H1."))
    return found


def _section_findings(lines: list[str]) -> list[Finding]:
    heads = [m.group(1) for line in lines if (m := _H2.match(line))]
    found = []
    if not any(_WHEN.search(h) and not _WHEN_NOT.search(h) for h in heads):
        found.append(Finding("when_to_use", "warn", 0, "no 'when to use' section",
                             "Add `## When to use` listing the concrete tasks it fits."))
    if not any(_WHEN_NOT.search(h) for h in heads):
        found.append(Finding("when_not_to_use", "warn", 0, "no 'when not to use' section",
                             "Add `## When not to use` naming the cases and the alternative."))
    return found


def _mark_link(state: StateInline, silent: bool) -> bool:
    """Keep the source offsets, label, and destination of each parsed Markdown link."""
    start, first = state.pos, len(state.tokens)
    if not link(state, silent):
        return False
    if not silent:
        opening = next(token for token in state.tokens[first:] if token.type == "link_open")
        label_end = state.md.helpers.parseLinkLabel(state, start, True)
        target = opening.attrGet("href")
        if target and "label" not in opening.meta:
            destination = label_end + 2  # Skip `](` and optional whitespace.
            while state.src[destination] in " \t\n":
                destination += 1
            parsed = state.md.helpers.parseLinkDestination(state.src, destination, state.pos)
            target = state.src[destination:parsed.pos]
        state.tokens[-1].meta["llms_txt_link"] = (
            start, state.pos, state.src[start + 1:label_end], target,
        )
    return True


def _html_outside_code(state: StateBlock, start: int, end: int, silent: bool) -> bool:
    # A block opener across a soft break can occur inside a code span.
    return start not in state.env["llms_txt_code_lines"] and html_block(state, start, end, silent)


_LINK_PARSER = MarkdownIt("commonmark", {"store_labels": True})
_LINK_PARSER.block.ruler.at("html_block", _html_outside_code, {"alt": ["paragraph", "reference", "blockquote"]})
_LINK_PARSER.inline.ruler.at("link", _mark_link)


def _inline_links(token: Token, in_list: bool) -> Iterator[tuple[int, str, str, bool]]:
    if token.map is None:
        return
    children = token.children or []
    newlines = [m.start() for m in re.finditer("\n", token.content)]
    for child in children:
        mark = child.meta.get("llms_txt_link")
        if mark is None:
            continue
        start, end, title, target = mark
        if target:
            line = token.map[0] + bisect_left(newlines, start) + 1
            yield line, title, target, in_list and _DESCRIPTION.match(token.content, end) is None


def _links(text: str, code_lines: set[int]) -> Iterator[tuple[int, str, str, bool]]:
    lists = 0
    for token in _LINK_PARSER.parse(text, {"llms_txt_code_lines": code_lines}):
        if token.type in {"bullet_list_open", "ordered_list_open"}:
            lists += 1
        elif token.type in {"bullet_list_close", "ordered_list_close"}:
            lists -= 1
        elif token.type == "inline":
            yield from _inline_links(token, lists > 0)


def _link_findings(text: str, code_lines: set[int]) -> list[Finding]:
    links = list(_links(text, code_lines))
    if not links:
        return [Finding("links", "fail", 0, "no Markdown links to the documentation",
                        "List key pages as `- [Title](url): one-line description`.")]
    return [Finding("links", "warn", line, f"link to {target} has no description",
                    "Add `: <what the page answers>` after the link.", f"{title}|{target}")
            for line, title, target, missing_description in links if missing_description]


_BLOCK = re.compile(r"\n(?=[ \t]*(?:\n|$)| {0,3}(?:#{1,6}(?:\s|$)|>|[-+*] |\d+[.)] |`{3,}|~{3,}))")
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")


class _InlineText:
    """Track code spans and HTML comments across soft line breaks."""

    def __init__(self, text: str):
        self.text = text
        self.scanner = CodeSpanScanner(text, stop=_BLOCK)
        self.span = self.scanner.find(0)
        self.comment = text.find("<!--")
        self.in_comment = False

    def _code(self, pos: int, end: int, parts: list[str], keep_code: bool) -> int:
        span = self.span
        assert span is not None
        if span.start() >= pos:
            back = span.start()
            while back > pos and self.text[back - 1] == "\\":
                back -= 1
            if (span.start() - back) % 2:
                parts.append(self.text[pos:span.body_begin])
                self.span = self.scanner.find(span.body_begin)
                return span.body_begin
            parts.append(self.text[pos:span.start()])
        stop = min(span.end(), end)
        parts.append(self.text[max(pos, span.start()):stop] if keep_code else "x")
        return stop

    def visible(self, pos: int, end: int, *, keep_code: bool) -> str:
        parts = []
        while pos < end:
            if self.in_comment:
                close = self.text.find("-->", pos, end)
                if close < 0:
                    break
                self.in_comment = False
                pos = close + 3
                self.span = self.scanner.find(pos)
                continue
            if self.span and self.span.end() <= pos:
                self.span = self.scanner.find(pos)
            stop = min(self.span.start(), end) if self.span else end
            if 0 <= self.comment < pos:
                self.comment = self.text.find("<!--", pos)
            if pos <= self.comment < stop:
                parts.append(self.text[pos:self.comment])
                self.in_comment = True
                pos = self.comment + 4
            elif self.span and self.span.start() < end:
                pos = self._code(pos, end, parts, keep_code)
            else:
                parts.append(self.text[pos:end])
                break
        return "".join(parts)


def _structure_lines(text: str, *, keep_code: bool = False) -> tuple[list[str], set[int]]:
    """Skip fenced examples and retain physical line numbers for diagnostics."""
    text = text.removeprefix("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    inline = _InlineText(text)
    fence = ""
    start = 0
    lines = []
    code_lines: set[int] = set()
    for line in text.split("\n"):
        end = start + len(line)
        marker = _FENCE.match(line)
        if fence:
            if marker and marker[1].startswith(fence) and not marker[2].strip():
                fence = ""
                inline.span = inline.scanner.find(end + 1)
            lines.append("")
        elif marker and not inline.in_comment and (marker[1][0] == "~" or "`" not in marker[2]):
            fence = marker[1]
            lines.append("")
        else:
            if keep_code and inline.span and inline.span.start() < start < inline.span.end():
                code_lines.add(len(lines))
            lines.append(inline.visible(start, end, keep_code=keep_code))
        start = end + 1
    return lines, code_lines


def check(text: str, *, token_budget: int = TOKEN_BUDGET) -> list[Finding]:
    """Every finding for one llms.txt text, in check order; empty when it passes."""
    lines, _ = _structure_lines(text)
    links, code_lines = _structure_lines(text, keep_code=True)
    found = _title_findings(lines) + _section_findings(lines) + _link_findings("\n".join(links), code_lines)
    tokens = estimate_tokens(text)
    if tokens > token_budget:
        found.append(Finding("size", "warn", 0, f"~{tokens} tokens, over {token_budget}",
                             "Move detail into linked pages or an llms-full.txt."))
    return found
