# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Network checks of ``catalog check --online``: packages a skill installs, and links it names.

Opt-in and warnings only. A ``pip``, ``npm`` or ``npx`` package that PyPI or the npm registry answers
404 for is ``package-not-found``; an ``http(s)`` link in ``SKILL.md`` answering 404 or 410 is ``link-dead``,
and so is one whose host does not resolve, when another probe of the run got an HTTP answer (with none, the
resolver is not shown to work and the link is not checked). Any other answer, and any network error other
than a DNS failure, is no finding: a registry or site that refuses, rate-limits or times out is not gone.
The targets not checked are counted in one summary line, not reported one by one.

Each distinct package or URL is probed once per run, with at most :data:`MAX_WORKERS` probes in flight
and a :data:`TIMEOUT_SECONDS` timeout per request. A HEAD request is tried first and a GET only when
the HEAD answer is not a 2xx or 3xx.
"""

from __future__ import annotations

import html
import ipaddress
import re
import socket
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from html.entities import html5
from typing import TYPE_CHECKING, Iterator, Optional, Sequence
from urllib.parse import quote, urlsplit
from urllib.request import getproxies, proxy_bypass

import httpx

from dokimi.external.skill_markdown import outside_fences
from dokimi.external.validate import Finding, SkillReview
from dokimi.utils import InputError

if TYPE_CHECKING:
    from dokimi.skills import Skill

TIMEOUT_SECONDS = 5.0
MAX_WORKERS = 8
MAX_URLS_PER_SKILL = 20
PYPI_URL = "https://pypi.org/simple/{name}/"
NPM_URL = "https://registry.npmjs.org/{name}"
_REGISTRY = {"pypi": "PyPI", "npm": "the npm registry"}
_DEAD_LINK_STATUSES = (404, 410)

_PIP = {"pip", "pip3"}
_NPM_INSTALL = {"install", "i", "add"}
# Words that may come before a package manager name in a command line: a prompt, a wrapper, a verb, or a
# conjunction that joins a second command ("pip install x and npm i y").
_LEAD = {"$", "sudo", "run", "Run", "RUN", "use", "Use", "-m", "uv", "env", "and", "or", "but", "then"}
# Prose conjunctions: never package names (they are dropped from a name list, and they start a command when one
# follows them, see _LEAD).
_PROSE_CONJUNCTIONS = {"and", "or", "but", "then"}
# Common English words that end a package list on a prose line ("pip install foo for development"): the list stops
# before them. A real package with one of these names is missed, which is a warn-only miss, not a false warning.
_PROSE_STOP = {
    "a", "an", "the", "in", "on", "for", "to", "of", "at", "by", "from", "into", "with", "as", "is", "are",
    "it", "this", "that", "here", "there", "locally", "globally", "again",
    # Connectives that start a clause after a list: "pip install foo if needed". A package with one of these names
    # is not checked when it is a bare word of the list, which is the cost of not reporting prose as a package.
    "if", "when", "which", "before", "after", "you", "also", "first", "using", "via", "so", "while", "until", "want",
}
# The operators of a version spec written with spaces ("pip install foo == 1.0"): the version after one is not a name.
_SPEC_OPERATORS = {"==", "===", "!=", "~=", ">=", "<=", ">", "<"}
# A ``#`` that starts a word is a shell comment; one inside a word ("C#", "docs#setup") is not.
_COMMENT_RE = re.compile(r"(?:^|(?<=[\s;|&()<>]))#(?=\s|$).*$")  # "#" then a space or the end: "#12" is not a comment
# A ``#`` after a space that follows an install command starts a shell comment whatever comes after it ("#note;"),
# and it is cut before code spans are split off (the commented-out command in backticks is not a command).
_UNQUOTED_HASH_RE = re.compile(r"(?:^|(?<=\s))#(?!\d)")
_SHELL_COMMAND_RE = re.compile(r"\b(?:pip3? install|npm (?:install|i|add)|uv pip install|npx)\b")  # a command, not prose
_LINE_PREFIX_RE = re.compile(r"^\s*(?:(?:[-*+]|\d+[.)])\s+|>+ ?)")  # a list marker or a quote marker
# English words that follow ``npx`` in prose ("npx is great", "Use npx to run it"), not package names.
_NPX_PROSE = {"a", "an", "and", "are", "can", "could", "for", "get", "in", "is", "it", "may", "might", "must",
              "on", "or", "run", "runs", "should", "the", "to", "use", "will", "with", "would"}
_PIP_VALUE_OPTIONS = {
    "--python", "-r", "--requirement", "-c", "--constraint", "-e", "--editable", "-i", "--index-url",
    "--extra-index-url", "-f", "--find-links", "-t", "--target", "--prefix", "--root", "--cache-dir",
    "--proxy", "--timeout", "--trusted-host", "--python-version", "--platform", "--abi",
    "--implementation", "--upgrade-strategy", "--progress-bar", "--src", "--log", "--report",
    "--retries", "--use-feature", "--keyring-provider", "--resume-retries", "--default-timeout",
    "--exists-action", "--global-option", "--install-option", "--config-settings", "-C",
    "--only-binary", "--no-binary", "--cert", "--client-cert", "--root-user-action",
    # uv pip's value options ("uv pip install --prerelease allow foo")
    "--prerelease", "--resolution", "--link-mode", "--index-strategy", "--python-platform", "--extra", "--group",
    "--color", "-p", "--torch-backend", "--fork-strategy", "--directory", "--project",
}
_NPM_VALUE_OPTIONS = {
    "--prefix", "--registry", "--workspace", "-w", "--tag", "--cache", "--userconfig", "--omit", "--include",
    "--loglevel", "--otp", "--scope", "--location", "--install-strategy", "--before", "--access", "--color",
    "--shell",
}
_NON_PACKAGE_SUFFIXES = (".txt", ".whl", ".tar.gz", ".tgz", ".zip", ".cfg", ".toml", ".py")
_PYPI_NAME_RE = re.compile(
    r"([A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)(?:\[[^\]]*\])?(?:\s*(?:===|==|>=|<=|~=|!=|<|>).*)?")
_NPM_NAME_RE = re.compile(r"(?:@[A-Za-z0-9._~-]+/)?[A-Za-z0-9._~-]+")
_PLACEHOLDER_NAMES = {"package", "packages", "pkg", "name", "module", "mypackage", "my-package", "package-name"}
_URL_RE = re.compile(r"https?://(?:[^\s\"`<>'“”‘’—，。、；：！？（）「」]|'(?=\w))+", re.IGNORECASE)  # an apostrophe in a word is in a URL ("Moore's_law")
# Where a literal URL starts: after a markdown destination's ``](``, or a reference definition's ``[id]: ``.
_LITERAL_START_RE = re.compile(r"\]\(|^[ \t]*\[(?!\^)[^\]\n]*\]:[ \t]*", re.MULTILINE)  # not a footnote "[^1]:"
_ANGLE_REST_RE = re.compile(r"([^<>\n]*)>")  # a "<...>" link destination: the text up to its ">", blanks included
_ESCAPE_RE = re.compile(r"\\([!-/:-@\[-`{-~])")  # a backslash escape of ASCII punctuation, as CommonMark reads it
# An escape or a character reference of a destination, in one pass so that "\&amp;" is the text "&amp;".
_DECODE_RE = re.compile(r"\\([!-/:-@\[-`{-~])|&(#[xX][0-9a-fA-F]{1,6}|#[0-9]{1,7}|[A-Za-z][A-Za-z0-9]{1,31});")
_TEMPLATE_RE = re.compile(r"[{}$]")  # a template variable, such as ``{id}`` or ``${VERSION}``
_EXAMPLE_DOMAINS = ("example.com", "example.org", "example.net")
_RESERVED_SUFFIXES = (".localhost", ".test", ".invalid", ".example")
_PLACEHOLDER_HOST_RE = re.compile(r"(?:^|[./])your[-_]")  # a host or a path segment that starts with ``your-`` or ``your_``
_PLACEHOLDER_SEGMENTS = {"OWNER", "REPO", "USER", "USERNAME"}  # a URL path's upper-case placeholder segment, "OWNER/REPO.git"
_CODE_SPAN_RE = re.compile(r"`([^`\n]*)`")
# A code span as CommonMark closes it: a run of backticks ends at the next run of the same length. It may run over
# a line break, but not into the next block: a blank line, a list item, a heading, a table row, a quote or a rule.
_BLOCK_START = r"\n[ \t]*(?:\n|[-*+][ \t]|\d{1,9}[.)][ \t]|#{1,6}(?:[ \t]|$)|[|><]|(?:[-*_][ \t]*){3,}(?=\n|$))"
_URL_CODE_SPAN_RE = re.compile(r"(?<!`)(`+)(?!`)((?:(?!" + _BLOCK_START + r").)*?)(?<!`)\1(?!`)", re.DOTALL)
_COMMAND_START = _PIP | {"npm", "npx", "uv", "sudo", "$"}
_OPERATOR_RE = re.compile(r"[;|&()]+")  # a shell operator ends the words of a command; ``<`` and ``>`` do not (see below)
_DESCRIPTOR_RE = re.compile(r"\d*[<>]&(?:\d+|-)")  # a descriptor duplication (2>&1, >&2, 2>&-): not a file, not a split
_AMP_REDIRECT_RE = re.compile(r"(?<=[<>])[&|]|&(?=>)")  # the & of >& and <&, the | of >|, the & of &>: one operator
_REDIRECT_RE = re.compile(r"\d*(?:>>?(?!=)|<<?<?(?!=))")  # an operator at the start of a word: >, 2>>, <, <<<, 2>err.log
_ATTACHED_REDIRECT_RE = re.compile(r"[<>](?!=)")  # an operator after a word: "lodash>", "lodash>log" (not ">=" or "<=")
_PLACEHOLDER_WORD_RE = re.compile(r"<[^<>]*>")  # a template in a word: "eslint-plugin-<plugin-name>"
# (not >= or <=: those are version specs)


def make_client() -> httpx.Client:
    """The HTTP client of a run: :data:`TIMEOUT_SECONDS` per request, redirects followed by :func:`_final_status`."""
    try:
        return httpx.Client(follow_redirects=False, timeout=TIMEOUT_SECONDS,
                            headers={"User-Agent": "dokimi-catalog-check"})
    except ImportError as exc:  # a socks:// proxy in the environment needs the socksio package
        raise InputError(f"{exc} Unset the SOCKS proxy variable, or install httpx[socks], to run --online.") from exc


def _code_span(match: re.Match[str]) -> str:
    """A code span that is a command is a line of its own ("Run `pip install x`"); any other span is just its text."""
    text = match.group(1)
    words = text.split()
    is_command = bool(words) and (words[0] in _COMMAND_START or _SHELL_COMMAND_RE.search(text) is not None)
    return f"\n{text}\n" if is_command else f" {text} "  # "python -m pip install x" starts with python


def _before_comment(line: str) -> str:
    """``line`` up to a ``#`` comment that follows an install command on it, else ``line`` unchanged."""
    command = _SHELL_COMMAND_RE.search(line)
    if command is None:
        return line  # no install command, so no comment to cut: the common line, read in one pass
    spans = [span.span() for span in _CODE_SPAN_RE.finditer(line)]
    k = 0  # the first span that does not end before the hash: the hashes come in order, so the index only moves on
    for m in _UNQUOTED_HASH_RE.finditer(line):
        if m.start() < command.end():
            continue  # no install command ends before this hash
        while k < len(spans) and spans[k][1] <= m.start():
            k += 1
        if k < len(spans) and spans[k][0] <= m.start():
            continue  # inside a code span: the span is split off and its own comment is cut there
        return line[:m.start()]
    return line


def _lines(body: str) -> Iterator[str]:
    """The lines of ``body``: shell comments after a command cut, code spans split off, stray backticks and list or
    quote markers removed."""
    body = "\n".join(_before_comment(line) for line in body.splitlines())
    body = _CODE_SPAN_RE.sub(_code_span, body)
    body = body.replace("`", " ")
    for line in body.splitlines():
        yield _LINE_PREFIX_RE.sub("", line)


def _without_redirects(words: list[str]) -> list[str]:
    """``words`` without their redirections: a bare operator (``>``, ``2>>``) and the word after it, or an operator
    whose target is in the same word (``2>err.log``). A redirection names no package, and the words after it do."""
    kept: list[str] = []
    target = False
    for tok in words:
        if target:
            target = False  # the word after a bare operator is its target
        elif _REDIRECT_RE.fullmatch(tok):
            target = True
        elif _REDIRECT_RE.match(tok):
            continue
        elif (attached := _ATTACHED_REDIRECT_RE.search(tok)) and not _PLACEHOLDER_WORD_RE.search(tok):
            # "lodash>" or "lodash>build-log": the name, then the operator; "eslint-<name>" is a template, not a name
            if attached.start():
                kept.append(tok[:attached.start()])
            target = attached.end() == len(tok)
        else:
            kept.append(tok)
    return kept


def _segments(line: str) -> Iterator[list[str]]:
    """The words of ``line`` between shell operators; a ``#`` comment (see ``_COMMENT_RE``) ends the line."""
    line = _AMP_REDIRECT_RE.sub("", _DESCRIPTOR_RE.sub(" ", _COMMENT_RE.sub("", line)))
    for part in _OPERATOR_RE.split(line):
        words = _without_redirects(part.split())
        if words:
            yield words


def _is_package_word(tok: str) -> bool:
    return not tok.startswith(("-", ".", "/", "~", "$", "<", "{")) and "://" not in tok


def _requirement(tok: str) -> str:
    """``tok`` without the environment marker after ``;`` and the sentence punctuation after it."""
    return tok.split(";", 1)[0].strip().rstrip(".,:!?")


def _is_prose(tok: str) -> bool:
    """A word of ``_PROSE_STOP``, with the sentence punctuation after it ("locally.")."""
    return tok.rstrip(".,:!?").lower() in _PROSE_STOP


def _pip_names(args: list[str]) -> Iterator[str]:
    skip = False
    for tok in args:
        if skip:
            skip = False
        elif tok in _PIP_VALUE_OPTIONS or tok in _SPEC_OPERATORS:
            skip = True
        elif _is_prose(tok):
            return  # prose after the list: "pip install foo for development"
        elif _is_package_word(tok) and "@" not in tok:
            requirement = _requirement(tok)
            if requirement.endswith(tuple(_SPEC_OPERATORS)):
                skip = True  # "foo>= 1.0": the version is the next word
            match = _PYPI_NAME_RE.fullmatch(requirement)
            if match and any(c.isalpha() for c in match.group(1)) and not requirement.lower().endswith(_NON_PACKAGE_SUFFIXES):
                yield match.group(1)


def _npm_name(tok: str) -> Optional[str]:
    tok = tok.rstrip(".,:!?")
    if not _is_package_word(tok) or ":" in tok or tok.lower().endswith(_NON_PACKAGE_SUFFIXES):
        return None
    at = tok.rfind("@")
    name = tok[:at] if at > 0 else tok  # ``pkg@1.2`` and ``@scope/pkg@1.2``, not ``@scope/pkg``
    if "/" in name and not name.startswith("@"):
        return None  # ``user/repo``: a GitHub shorthand, not the registry
    if len(name) > 214 or not _NPM_NAME_RE.fullmatch(name) or not any(c.isalpha() for c in name):
        return None
    return name


def _npm_names(args: list[str]) -> Iterator[str]:
    skip = False
    for tok in args:
        if skip:
            skip = False
        elif tok in _NPM_VALUE_OPTIONS:
            skip = True
        elif tok == "--" or _is_prose(tok):
            return
        elif not tok.startswith("-") and (name := _npm_name(tok)):
            yield name


def _npx_names(args: list[str]) -> Iterator[str]:
    """The packages ``npx`` runs: every ``-p`` or ``--package`` value, or else the first word after its flags
    when there is none. The command that follows the packages is not a package."""
    packages: list[str] = []
    take_next = skip = explicit = False  # explicit: a -p or --package was given, whether or not its value is a name
    for tok in args:
        if skip:
            skip = False
        elif take_next:
            take_next = False
            packages.extend(_npm_names([tok]))
        elif tok in {"-c", "--call"}:
            break  # a shell command follows, not a package
        elif tok.startswith("--package="):
            explicit = True
            packages.extend(_npm_names([tok[len("--package="):]]))
        elif tok in {"-p", "--package"}:
            take_next = explicit = True
        elif tok in _NPM_VALUE_OPTIONS:
            skip = True
        elif not tok.startswith("-"):
            if not explicit:
                packages.extend(name for name in _npm_names([tok]) if name not in _NPX_PROSE)
            break
    yield from packages


def _is_placeholder(name: str) -> bool:
    folded = name.lower().replace("_", "-")  # "your_package", "YOUR_PACKAGE" and "your-package" are one name
    return folded in _PLACEHOLDER_NAMES or folded.startswith("your-")


def _command_kind(tokens: list[str], i: int) -> Optional[str]:
    """``pip``, ``npm`` or ``npx`` when an install command starts at ``tokens[i]``: the word starts the segment
    or follows a word in ``_LEAD``, and ``install`` follows pip and npm."""
    if i and tokens[i - 1] not in _LEAD:
        return None
    tok = tokens[i]
    nxt = tokens[i + 1] if i + 1 < len(tokens) else ""
    if tok in _PIP and nxt == "install":
        return "pip"
    if tok == "npm" and nxt in _NPM_INSTALL:
        return "npm"
    if tok == "npx":
        return "npx"
    return None


def _list_end(tokens: list[str], start: int, end: int) -> int:
    """Where the package list that starts at ``tokens[start]`` stops, at most at ``end``: before a ``then``, or after
    the first word that ends in ``:`` (the prose after a colon is not a list). A common English word ends the list
    in the name readers (see ``_PROSE_STOP``), where an option's value is known."""
    for j in range(start, end):
        if tokens[j].lower() == "then":
            return j
        if tokens[j].endswith(":") and not (j > start and tokens[j - 1] in _PIP_VALUE_OPTIONS | _NPM_VALUE_OPTIONS):
            return j + 1  # "--only-binary :all:" is an option and its value, not prose
    return end


_PIP_OTHER_INDEX = {"-i", "--index-url", "--extra-index-url", "--no-index", "-f", "--find-links",
                    "--index", "--default-index"}  # uv pip's index options
_NPM_OTHER_REGISTRY = {"--registry"}


def _from_another_index(words: list[str], kind: str) -> bool:
    """Whether a pip command names an index other than PyPI, or an npm one a registry other than npm's: the names of
    that command are not on the public registry, so they are not looked up there."""
    options = _PIP_OTHER_INDEX if kind == "pip" else _NPM_OTHER_REGISTRY
    return any(tok.split("=", 1)[0] in options or (kind == "pip" and _attached_index(tok)) for tok in words)


def _attached_index(tok: str) -> bool:
    """``-ihttps://corp/simple`` and ``-fDIR``: pip's short options with their value attached."""
    return len(tok) > 2 and tok[:2] in {"-i", "-f"} and not tok.startswith("--")


def _command_packages(tokens: list[str]) -> Iterator[tuple[str, str]]:
    starts = [(i, kind) for i in range(len(tokens)) if (kind := _command_kind(tokens, i))]
    for k, (i, kind) in enumerate(starts):
        end = starts[k + 1][0] if k + 1 < len(starts) else len(tokens)  # the words run up to the next command
        if kind == "npx":
            args = tokens[i + 1:_list_end(tokens, i + 1, end)]
            if _from_another_index(args, "npm"):
                continue
            eco, names = "npm", _npx_names(args)
        else:
            words = tokens[i + 2:_list_end(tokens, i + 2, end)]
            if _from_another_index(words, kind):
                continue
            if kind == "pip":
                eco, names = "pypi", _pip_names(words)
            else:
                eco, names = "npm", _npm_names(words)
        for name in names:
            if name.lower() not in _PROSE_CONJUNCTIONS and not _is_placeholder(name):
                yield eco, name


def extract_packages(body: str) -> list[tuple[str, str]]:
    """The ``(ecosystem, name)`` pairs that ``pip``, ``npm`` and ``npx`` lines in ``body`` install, in order.

    Line-based: after an install command, the words up to the next shell operator, a ``#`` comment or the end of
    the line are read; words that are not valid package names, option flags and option values are dropped.
    Known limits, warn-only: quotes, parentheses and continuation lines are not read (a package there is missed);
    a prose word that is not in ``_PROSE_STOP`` after an install on the same line is read as a name; a command
    inside prose is read when it follows a word in ``_LEAD``.
    """
    found: dict[tuple[str, str], None] = {}
    for line in _lines(body):
        for tokens in _segments(line):
            for pair in _command_packages(tokens):
                found.setdefault(pair)
    return list(found)


def _mask_escapes(raw: str, decode: bool) -> str:
    """``raw`` with each backslash escape of a destination replaced by two NULs: the same length, and no character of
    the escape ends the URL or counts as a bracket ("\\)", "\\(", "\\|")."""
    return _ESCAPE_RE.sub("\0\0", raw) if decode else raw


def _decode(match: re.Match[str]) -> str:
    """The character of a backslash escape, or the text of a character reference (``&amp;`` is ``&``); an unknown
    name stays as written."""
    if match.group(1):
        return match.group(1)
    text = match.group()
    # html.unescape also takes legacy names without a ";" ("&not" in "&notanentity;"): only a name with one counts
    return html.unescape(text) if text[1] == "#" or text[1:] in html5 else text


def _clean_url(raw: str, literal: bool = False, decode: bool = False, delimited: bool = False,
               table_row: bool = False) -> Optional[str]:
    """``raw`` without what a sentence, a table cell, emphasis or a markdown link puts after it: the URL ends before
    the first ``)`` or ``]`` that closes no ``(`` or ``[`` of the URL, and before a ``|``; then trailing punctuation
    and ``*`` emphasis go. A balanced bracket stays ("f[0]=1", "[2001:db8::1]"). None for an empty result.

    ``literal`` is a markdown destination, a reference definition or an autolink: its punctuation is part of the
    URL. ``decode`` (a destination or a reference definition, not an autolink, where CommonMark has no escapes) also
    decodes its backslash escapes (``guide\\_notes`` is ``guide_notes``) and its character references (``a&amp;b``
    is ``a&b``), and an escaped character never ends it
    (``a\\)b`` is ``a)b``). ``delimited`` is a destination or autolink in ``<...>``: it ends at ``>``, so an
    unmatched ``)`` or ``]`` and a ``|`` are part of it, unless ``table_row``: a GFM table splits its row at an
    unescaped ``|`` before it reads the inline text, so the pipe ends the URL there.
    """
    parens = brackets = 0
    end = len(raw)
    for i, ch in enumerate(_mask_escapes(raw, decode)):
        if ch == "(":
            parens += 1
        elif ch == "[":
            brackets += 1
        elif (ch == "|" and (table_row or not delimited)) or (
                not delimited and ((ch == ")" and parens == 0) or (ch == "]" and brackets == 0))):
            end = i
            break
        elif ch == ")":
            parens -= 1
        elif ch == "]":
            brackets -= 1
    url = raw[:end]
    if decode:
        url = _DECODE_RE.sub(_decode, url)
    if literal:
        return url or None
    url = url.rstrip(".,;:!?*")
    return url or None


def _internal_url(url: str) -> bool:
    """Whether ``url`` names a host of the operator's network, not a public one: a loopback, private, link-local,
    reserved or multicast address, a name with no dot (``my-api:8000``), or a ``.local`` or ``.internal`` name. An
    untrusted catalog must not make ``--online`` send requests there, nor a redirect of a public link."""
    try:  # the host httpx connects to: it maps U+3002 and U+FF0E to "." (urlsplit keeps them)
        host = (httpx.URL(url).host or "").lower().rstrip(".")  # "localhost." is "localhost"
    except (httpx.InvalidURL, ValueError):
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        try:
            address = ipaddress.IPv4Address(socket.inet_aton(host))  # "127.1", "0177.0.0.1": the resolver's forms too
        except (OSError, ValueError):
            return "." not in host or host.endswith((".local", ".internal", ".localhost"))
    return (address.is_loopback or address.is_private or address.is_link_local or address.is_reserved
            or address.is_multicast or address.is_unspecified or not address.is_global)  # 100.64.0.0/10 is not global


def _placeholder_url(url: str) -> bool:
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
    except ValueError:  # a malformed bracketed host, such as ``http://[x/y``: not a link to check
        return True
    if not host or host == "localhost" or host.endswith(_RESERVED_SUFFIXES):
        return True
    if host in _EXAMPLE_DOMAINS or host.endswith(tuple("." + d for d in _EXAMPLE_DOMAINS)):
        return True
    if _internal_url(url):
        return True
    if any(segment.removesuffix(".git") in _PLACEHOLDER_SEGMENTS for segment in parts.path.split("/")):
        return True
    return bool(_PLACEHOLDER_HOST_RE.search(host + parts.path.lower()))


def extract_urls(body: str) -> list[str]:
    """The distinct ``http(s)`` URLs in ``body``, in order, without placeholders, at most :data:`MAX_URLS_PER_SKILL`.

    A URL followed by ``<``, ``{`` or ``$`` is a template (``https://api.x.com/<id>``), not a link.
    """
    return _distinct_urls(body)[0]


def _starts_destination(body: str, start: int, literal: set[int]) -> bool:
    """Whether the URL at ``start`` is a destination: only blanks and an optional ``<`` lie between it and where a
    ``](`` or a ``[id]:`` ended ("](  <url>)")."""
    end = start
    while start not in literal:
        if start == 0 or body[start - 1] not in " \t\n<":
            return False
        start -= 1
    return not re.search(r"\n[ \t]*\n", body[start:end])  # a blank line ends the definition: the URL is prose


def _distinct_urls(body: str) -> tuple[list[str], list[str]]:
    """The URLs :func:`extract_urls` keeps, and the distinct ones past :data:`MAX_URLS_PER_SKILL` that it drops."""
    urls: dict[str, None] = {}
    body = "\n".join(outside_fences(body))  # a fenced block is code: its URLs are an example, not a link to follow
    literal = {m.end() for m in _LITERAL_START_RE.finditer(body)}
    body = body.replace("](", "  ")  # a markdown link's text and its target are both URLs; the length is kept
    code = [m.span() for m in _URL_CODE_SPAN_RE.finditer(body)]  # a URL in a code span is an example, as in a fence
    for m in _URL_RE.finditer(body):
        if _TEMPLATE_RE.search(m.group()) or body[m.end():m.end() + 1] in ("<", "{", "$"):
            continue
        if any(start <= m.start() < end for start, end in code):
            continue
        destination = _starts_destination(body, m.start(), literal)
        autolink = m.start() > 0 and body[m.start() - 1] == "<" and body[m.end():m.end() + 1] == ">"
        raw, angled = m.group(), False
        if destination and body[m.start() - 1] == "<":  # a "<...>" destination may hold spaces; it ends at ">"
            close = _ANGLE_REST_RE.match(body, m.start())
            if close:
                raw, angled = close.group(1).rstrip(), True
        line_start = body.rfind("\n", 0, m.start()) + 1
        url = _clean_url(raw, literal=destination or autolink, decode=destination, delimited=autolink or angled,
                         table_row=body[line_start:m.start()].lstrip().startswith("|"))
        if url is not None:
            url = url.partition("#")[0]  # the fragment is never sent: anchors of one page are one request
        if url is not None and not _placeholder_url(url):
            urls.setdefault(url)
    return list(urls)[:MAX_URLS_PER_SKILL], list(urls)[MAX_URLS_PER_SKILL:]


def _status(client: httpx.Client, url: str) -> int:
    """The final status of ``url``: HEAD, then GET when HEAD is not a 2xx or 3xx."""
    status = _final_status(client, "HEAD", url)
    return status if status < 400 else _final_status(client, "GET", url)


_MAX_REDIRECTS = 20  # httpx's own limit


class _InternalHop(httpx.HTTPError):
    """A redirect into a host of the operator's network: the probe stops there and the link gets no finding."""



def _final_status(client: httpx.Client, method: str, url: str) -> int:
    """The status of ``url`` after its redirects, which are followed here and not by httpx: httpx reads the body of
    a redirect before it follows it, so an endless body would hold the probe. No body is read here at all."""
    start, hops = url, 0
    while True:
        if _internal_url(url):
            raise _InternalHop(f"redirect from {start} into {url}, which is not a public host: not requested")
        with client.stream(method, url, follow_redirects=False) as response:
            if not response.has_redirect_location:  # a 3xx with no Location is an answer, not a hop
                return response.status_code
            url = str(response.url.join(response.headers["location"]))
        hops += 1
        if hops > _MAX_REDIRECTS:
            raise httpx.TooManyRedirects(f"more than {_MAX_REDIRECTS} redirects from {start}")


# The resolver answers that mean the host does not exist. EAI_AGAIN and the rest are a broken or
# busy resolver, not a dead link.
_NO_SUCH_HOST = {code for name in ("EAI_NONAME", "EAI_NODATA") if (code := getattr(socket, name, None)) is not None}


def _is_dns_failure(exc: BaseException) -> bool:
    seen: Optional[BaseException] = exc
    for _ in range(6):
        if seen is None:
            return False
        if isinstance(seen, socket.gaierror):
            return seen.errno in _NO_SUCH_HOST
        seen = seen.__cause__ or seen.__context__
    return False


_PROBE_ERRORS = (httpx.HTTPError, httpx.InvalidURL, ValueError)


@dataclass(frozen=True)
class _Outcome:
    """What one probe found. ``status`` is ``answer`` (an HTTP answer; ``finding`` when it is one), ``dns`` (the host
    did not resolve; ``finding`` is the dead link), ``internal`` (a redirect into a non-public host: not requested)
    or ``network`` (any other error: not checked)."""

    status: str
    finding: Optional[Finding] = None


def _package_finding(client: httpx.Client, eco: str, name: str) -> _Outcome:
    url = PYPI_URL.format(name=quote(name)) if eco == "pypi" else NPM_URL.format(name=quote(name, safe="@"))
    try:
        status = _status(client, url)
    except _InternalHop:
        return _Outcome("internal")
    except _PROBE_ERRORS:
        return _Outcome("network")
    if status != 404:
        return _Outcome("answer")
    return _Outcome("answer", Finding("warn", "package-not-found",
                                      f"package {name!r} is not on {_REGISTRY[eco]} ({url} returned 404)"))


def _proxied(url: str) -> bool:
    """Whether an environment proxy carries ``url``: its resolver failure then names the proxy, not the link."""
    parts = urlsplit(url)
    proxies = getproxies()
    return (parts.scheme in proxies or "all" in proxies) and not proxy_bypass(parts.hostname or "")


def _failed_url(exc: BaseException, url: str) -> str:
    """The URL of the request that failed: the redirect hop it was on, else ``url``."""
    try:
        return str(exc.request.url)  # type: ignore[attr-defined]
    except (AttributeError, RuntimeError):
        return url


def _link_outcome(client: httpx.Client, url: str) -> _Outcome:
    try:
        status = _status(client, url)
    except _InternalHop:
        return _Outcome("internal")
    except _PROBE_ERRORS as exc:
        failed = _failed_url(exc, url)  # the redirect hop that did not resolve, or the link itself
        if _is_dns_failure(exc) and not _proxied(failed):
            if failed == url:
                message = f"link {url} does not resolve (DNS failure)"
            else:
                message = f"link {url} redirects to {failed}, which does not resolve (DNS failure)"
            return _Outcome("dns", Finding("warn", "link-dead", message))
        return _Outcome("network")
    if status not in _DEAD_LINK_STATUSES:
        return _Outcome("answer")
    return _Outcome("answer", Finding("warn", "link-dead", f"link {url} returned {status}"))


def _probe(client: httpx.Client, key: tuple) -> _Outcome:
    if key[0] == "package":
        return _package_finding(client, key[1], key[2])
    return _link_outcome(client, key[1])


def _probe_all(http: httpx.Client, keys: list[tuple]) -> dict[tuple, _Outcome]:
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        return dict(zip(keys, pool.map(lambda key: _probe(http, key), keys), strict=True))


def _keys(body: str) -> tuple[list[tuple], list[str]]:
    """The targets of ``body``, and the URLs it names past the cap (not probed)."""
    urls, dropped = _distinct_urls(body)
    packages = [("package", eco, name) for eco, name in extract_packages(body)]
    return packages + [("link", url) for url in urls], dropped


# The not-checked outcomes of a run, in the order the summary line names them.
_NOT_CHECKED = ("network", "dns", "internal", "over_cap")
_NOT_CHECKED_LABELS = {
    "network": "network error",
    "dns": "DNS failure, no answer in the run",
    "internal": "redirect into a non-public host",
    "over_cap": f"over the {MAX_URLS_PER_SKILL}-URL cap",
}


def add_online_findings(reviewed: Sequence[tuple["Skill", SkillReview]], *,
                        client: Optional[httpx.Client] = None) -> dict:
    """Append the network findings to each review in ``reviewed``, probing each distinct target once.

    Returns the run's summary: ``checked`` targets that were checked, and ``not_checked`` counts by outcome
    (see :data:`_NOT_CHECKED`). A target that is not checked gets no finding and no line of its own.

    ``client`` is for tests; without it a :func:`make_client` client is opened and closed here.
    """
    plans = []
    dropped: dict[str, None] = {}
    for skill, review in reviewed:
        keys, over = _keys(skill.body)
        plans.append((review, keys))
        dropped.update(dict.fromkeys(over))
    unique = list(dict.fromkeys(key for _, keys in plans for key in keys))
    results: dict[tuple, _Outcome] = {}
    if unique:
        if client is None:
            with make_client() as own:
                results = _probe_all(own, unique)
        else:
            results = _probe_all(client, unique)
    answered = any(outcome.status == "answer" for outcome in results.values())
    not_checked = dict.fromkeys(_NOT_CHECKED, 0)
    checked = 0
    for key in unique:
        status = results[key].status
        if status == "answer" or (status == "dns" and answered):
            checked += 1
        else:
            not_checked[status] += 1
    not_checked["over_cap"] = sum(1 for url in dropped if ("link", url) not in results)
    for review, keys in plans:
        review.findings.extend(f for f in (_resolved(results[key], answered) for key in keys) if f is not None)
    return {"checked": checked, "not_checked": not_checked}


def _resolved(outcome: _Outcome, answered: bool) -> Optional[Finding]:
    """The finding of a probe, when the run may report it: a DNS failure only when some probe got an HTTP answer."""
    if outcome.status == "answer" or (outcome.status == "dns" and answered):
        return outcome.finding
    return None


def format_summary(summary: dict) -> str:
    """The one line ``catalog check --online`` prints: the checked targets, and the not-checked ones by outcome."""
    not_checked = summary["not_checked"]
    shown = [f"{_NOT_CHECKED_LABELS[name]}: {count}" for name, count in not_checked.items() if count]
    line = f"# online: {summary['checked']} checked, {sum(not_checked.values())} not checked"
    return f"{line} ({', '.join(shown)})" if shown else line
