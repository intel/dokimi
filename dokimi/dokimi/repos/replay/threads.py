# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Merged pull requests and their review threads, read from ``gh api graphql`` output.

The input is any JSON that holds pull request objects, each one an object with ``number``
and ``reviewThreads``: one response, the list of pages ``gh api graphql --paginate --slurp``
writes, or a plain list of pull requests. Connections are read as ``{"nodes": [...]}`` or
as a list. A pull request that two pages both hold is read once.

A thread is gold when its first comment was written by someone other than the pull
request's author, not by a bot (``__typename`` ``Bot``, or a login ending in ``[bot]``),
on a line of the new file (``subjectType`` ``LINE``, ``diffSide`` ``RIGHT``, an
``originalLine`` and the comment's ``originalCommit``). :func:`gold_problem` says why
another thread is not.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_SHA = re.compile(r"[0-9a-fA-F]{40}|[0-9a-fA-F]{64}")


class ReplayError(ValueError):
    """Input the command cannot use, or a git question it could not ask."""


@dataclass(frozen=True)
class Thread:
    """One review thread, as its first comment opened it."""

    id: str  # pr<number>-t<position>: the position among all the PR's threads, so an id survives a rule change
    path: str
    line: int | None  # ``originalLine``: a line of ``path`` at ``commit``
    commit: str | None  # the first comment's ``originalCommit``: the revision it was written on
    side: str  # ``diffSide``: RIGHT (the new file) or LEFT (a removed line)
    on_file: bool  # ``subjectType`` FILE: a comment on the whole file
    reviewer: str | None
    bot: bool
    comment: str
    created: str  # the first comment's ``createdAt``
    url: str | None
    reply: str | None  # the PR author's first later comment in the thread
    has_comment: bool


@dataclass(frozen=True)
class PullRequest:
    number: int
    title: str
    url: str | None
    author: str | None
    head: str | None  # ``headRefOid``
    merge: str | None  # ``mergeCommit``
    threads: tuple[Thread, ...]
    reviews: tuple[tuple[str | None, bool, str], ...]  # (author, bot, body) of each review


def read_threads(path: Path) -> list[PullRequest]:
    """The pull requests of the threads file at ``path``."""
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError) as exc:  # RecursionError: nested past the parser's limit
        raise ReplayError(f"cannot read the threads file {path}: {exc}") from exc
    return load_pull_requests(doc)


def load_pull_requests(doc: Any) -> list[PullRequest]:
    """The pull requests in ``doc``, by number; :class:`ReplayError` when it holds none."""
    found: dict[int, PullRequest] = {}
    for node in _pr_nodes(doc):
        pr = _pull_request(node)
        found.setdefault(pr.number, pr)
    if not found:
        raise ReplayError("the threads file holds no pull request: no object with `number` and `reviewThreads`")
    return [found[n] for n in sorted(found)]


def gold_problem(pr: PullRequest, t: Thread) -> str | None:
    """Why ``t`` is not a gold thread of ``pr``, or ``None`` when it is."""
    if not t.has_comment:
        return "no comment"
    if t.reviewer is None:
        return "author unknown (a deleted account)"
    if t.bot:
        return f"by a bot (@{t.reviewer})"
    if t.reviewer == pr.author:
        return "opened by the PR author"
    if t.on_file or t.line is None or not t.path:
        return "not on a line"
    if t.side == "LEFT":
        return "on a removed line"
    if t.commit is None:
        return "no originalCommit in the input"
    return None


def review_bodies(pr: PullRequest) -> list[str]:
    """The non-empty review bodies by someone other than the author or a bot: listed, never gold."""
    return [f"@{who}: {_first_line(body)}" for who, bot, body in pr.reviews
            if body.strip() and who is not None and who != pr.author and not bot]


def _pr_nodes(doc: Any) -> Iterator[dict[str, Any]]:
    """Every object with ``number`` and ``reviewThreads``, in document order; none is searched inside."""
    stack = [doc]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            if "number" in item and "reviewThreads" in item:
                yield item
                continue
            stack.extend(reversed(list(item.values())))
        elif isinstance(item, list):
            stack.extend(reversed(item))


def _pull_request(node: dict[str, Any]) -> PullRequest:
    number = node["number"]
    if not isinstance(number, int) or isinstance(number, bool):
        raise ReplayError(f"a pull request's number must be an integer, not {number!r}")
    author, _ = _actor(node.get("author"))
    threads = tuple(_thread(number, i, t, author)
                    for i, t in enumerate(_nodes(node.get("reviewThreads"), "reviewThreads", number), 1))
    reviews = tuple((*_actor(r.get("author")), _text(r.get("body")))
                    for r in _nodes(node.get("reviews"), "reviews", number))
    return PullRequest(number=number, title=_text(node.get("title")), url=_str(node.get("url")), author=author,
                       head=_oid(node.get("headRefOid")), merge=_oid(node.get("mergeCommit")),
                       threads=threads, reviews=reviews)


def _thread(number: int, index: int, node: dict[str, Any], pr_author: str | None) -> Thread:
    comments = _nodes(node.get("comments"), "a thread's comments", number)
    first = comments[0] if comments else {}
    reviewer, bot = _actor(first.get("author"))
    replies = (_text(c.get("body")) for c in comments[1:] if pr_author and _actor(c.get("author"))[0] == pr_author)
    line = node.get("originalLine")
    return Thread(id=f"pr{number}-t{index}", path=_text(node.get("path")),
                  line=line if isinstance(line, int) and not isinstance(line, bool) and line > 0 else None,
                  commit=_oid(first.get("originalCommit")), side=_text(node.get("diffSide")) or "RIGHT",
                  on_file=node.get("subjectType") == "FILE", reviewer=reviewer, bot=bot,
                  comment=_text(first.get("body")), created=_text(first.get("createdAt")),
                  url=_str(first.get("url")), reply=next(replies, None), has_comment=bool(comments))


def _nodes(value: Any, what: str, number: int) -> list[dict[str, Any]]:
    if value is None:
        return []
    if isinstance(value, dict):
        value = value.get("nodes")
    if not isinstance(value, list):
        raise ReplayError(f"pull request {number}: {what} is not a list of nodes")
    return [v for v in value if isinstance(v, dict)]


def _actor(actor: Any) -> tuple[str | None, bool]:
    """``(login, is a bot)``; a deleted account (``null``) has no login."""
    if not isinstance(actor, dict):
        return None, False
    login = _str(actor.get("login"))
    return login, actor.get("__typename") == "Bot" or bool(login and login.endswith("[bot]"))


def _oid(value: Any) -> str | None:
    """A full commit SHA given as ``{"oid": ...}`` or as a string; anything else is none, so no
    value from the file reaches git as an option or a revision expression."""
    if isinstance(value, dict):
        value = value.get("oid")
    return value.lower() if isinstance(value, str) and _SHA.fullmatch(value) else None


def _str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _first_line(text: str, width: int = 100) -> str:
    line = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
    return line if len(line) <= width else line[:width - 3] + "..."
