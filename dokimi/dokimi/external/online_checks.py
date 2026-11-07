# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Network checks of ``catalog check --online``: packages a skill installs, and links it names.

Opt-in and warnings only. A ``pip``, ``npm`` or ``npx`` package that PyPI or the npm registry answers
404 for is ``package-not-found``; an ``http(s)`` link in ``SKILL.md`` answering 404 or 410, or whose
host does not resolve, is ``link-dead``. Any other answer, and any network error other than a DNS
failure, is no finding: a registry or site that refuses, rate-limits or times out is not gone.

Each distinct package or URL is probed once per run, with at most :data:`MAX_WORKERS` probes in flight
and a :data:`TIMEOUT_SECONDS` timeout per request. A HEAD request is tried first and a GET only when
the HEAD answer is not a 2xx or 3xx.
"""

from __future__ import annotations

import ipaddress
import re
import shlex
import socket
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Iterator, Optional, Sequence
from urllib.parse import quote, urlsplit

import httpx

from dokimi.external.validate import Finding, SkillReview

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
# Words that may come before a package manager name in a command line: a prompt, a wrapper or a verb.
_LEAD = {"$", "sudo", "run", "Run", "RUN", "use", "Use", "then", "and", "-m", "uv", "env"}
# Words after which ``npx`` is a command. ``Use npx to run things`` is prose, so ``use`` is not one.
_NPX_LEAD = {"$", "sudo", "env", "run", "Run", "RUN", "then", "and"}
_PIP_VALUE_OPTIONS = {
    "--python", "-r", "--requirement", "-c", "--constraint", "-e", "--editable", "-i", "--index-url",
    "--extra-index-url", "-f", "--find-links", "-t", "--target", "--prefix", "--root", "--cache-dir",
    "--proxy", "--timeout", "--trusted-host", "--python-version", "--platform", "--abi",
    "--implementation", "--upgrade-strategy", "--progress-bar", "--src", "--log", "--report",
    "--retries", "--use-feature", "--keyring-provider", "--resume-retries", "--default-timeout",
    "--exists-action", "--global-option", "--install-option", "--config-settings", "-C",
    "--only-binary", "--no-binary", "--cert", "--client-cert", "--root-user-action",
}
_NPM_VALUE_OPTIONS = {
    "--prefix", "--registry", "--workspace", "-w", "--tag", "--cache", "--userconfig", "--omit", "--include",
    "--loglevel", "--otp", "--scope", "--location", "--install-strategy", "--before",
}
_NON_PACKAGE_SUFFIXES = (".txt", ".whl", ".tar.gz", ".tgz", ".zip", ".cfg", ".toml", ".py")
_PYPI_NAME_RE = re.compile(
    r"([A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)(?:\[[^\]]*\])?(?:\s*(?:===|==|>=|<=|~=|!=|<|>).*)?")
_NPM_NAME_RE = re.compile(r"(?:@[A-Za-z0-9._~-]+/)?[A-Za-z0-9._~-]+")
_PLACEHOLDER_NAMES = {"package", "packages", "pkg", "name", "module", "mypackage", "my-package"}
_URL_RE = re.compile(r"https?://[^\s\"'`<>]+", re.IGNORECASE)
_TEMPLATE_RE = re.compile(r"[{}$]")  # a template variable, such as ``{id}`` or ``${VERSION}``
_EXAMPLE_DOMAINS = ("example.com", "example.org", "example.net")
_RESERVED_SUFFIXES = (".localhost", ".test", ".invalid", ".example")
_PLACEHOLDER_HOST_RE = re.compile(r"(?:^|[./])your-")  # a host or a path segment that starts with ``your-``
_CONTINUATION_RE = re.compile(r"\\[ \t]*\r?\n")  # a backslash at the end of a line continues the command
_CODE_SPAN_RE = re.compile(r"`([^`\n]*)`")
_SEPARATOR_RE = re.compile(r"[;&|()]+")  # a token of these operators ends one command
_REDIRECT_RE = re.compile(r"&?[<>]+[&|]?")  # an operator token of a redirection, such as ``>`` or ``2>&1``'s ``>&``


def make_client() -> httpx.Client:
    """The HTTP client of a run: redirects followed, :data:`TIMEOUT_SECONDS` per request."""
    return httpx.Client(follow_redirects=True, timeout=TIMEOUT_SECONDS,
                        headers={"User-Agent": "dokimi-catalog-check"})


def _words(line: str) -> list[str]:
    """The tokens of one line, read as the shell reads them: quotes keep their text, a ``#`` starts a comment,
    and ``; & | ( )`` and ``< >`` are tokens of their own. An unbalanced quote reads as whitespace."""
    try:
        return list(_lexer(line))
    except ValueError:  # an unbalanced quote: read the line unquoted, so its operators still split
        lexer = _lexer(line)
        lexer.quotes = ""
        return list(lexer)


def _lexer(line: str) -> shlex.shlex:
    lexer = shlex.shlex(line, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    lexer.commenters = "#"
    return lexer


def _commands(line: str) -> Iterator[list[str]]:
    """The commands of one line, each as its words. A redirection and its target are not words of the command."""
    words: list[str] = []
    skip_target = False
    for tok in _words(line):
        if skip_target:
            skip_target = False
        elif _REDIRECT_RE.fullmatch(tok):
            if words and words[-1].isdigit():  # the file descriptor of ``2>&1`` or ``2>/dev/null``
                words.pop()
            skip_target = True
        elif _SEPARATOR_RE.fullmatch(tok):
            if words:
                yield words
            words = []
        else:
            words.append(tok)
    if words:
        yield words


def _command_lines(body: str) -> Iterator[list[str]]:
    body = _CONTINUATION_RE.sub(" ", body)
    body = _CODE_SPAN_RE.sub(lambda m: f"\n{m.group(1)}\n", body)  # an inline code span is a line of its own
    for line in body.splitlines():
        yield from _commands(line)


def _is_package_word(tok: str) -> bool:
    return not tok.startswith(("-", ".", "/", "~", "$", "<", "{")) and "://" not in tok


def _requirement(tok: str) -> str:
    """``tok`` without the environment marker after ``;`` and the sentence punctuation after it."""
    return tok.split(";", 1)[0].strip().rstrip(".,:!?")


def _pip_names(args: list[str]) -> Iterator[str]:
    skip = False
    for tok in args:
        if skip:
            skip = False
        elif tok in _PIP_VALUE_OPTIONS:
            skip = True
        elif _is_package_word(tok) and "@" not in tok:
            requirement = _requirement(tok)
            match = _PYPI_NAME_RE.fullmatch(requirement)
            if match and not requirement.lower().endswith(_NON_PACKAGE_SUFFIXES):
                yield match.group(1)


def _npm_name(tok: str) -> Optional[str]:
    tok = tok.rstrip(".,:!?")
    if not _is_package_word(tok) or ":" in tok or tok.lower().endswith(_NON_PACKAGE_SUFFIXES):
        return None
    at = tok.rfind("@")
    name = tok[:at] if at > 0 else tok  # ``pkg@1.2`` and ``@scope/pkg@1.2``, not ``@scope/pkg``
    if "/" in name and not name.startswith("@"):
        return None  # ``user/repo``: a GitHub shorthand, not the registry
    if len(name) > 214 or not _NPM_NAME_RE.fullmatch(name):
        return None
    return name


def _npm_names(args: list[str]) -> Iterator[str]:
    skip = False
    for tok in args:
        if skip:
            skip = False
        elif tok in _NPM_VALUE_OPTIONS:
            skip = True
        elif tok == "--":
            return
        elif not tok.startswith("-") and (name := _npm_name(tok)):
            yield name


def _npx_names(args: list[str]) -> Iterator[str]:
    """The packages ``npx`` runs: every ``-p`` or ``--package`` value, or else the first word after its flags
    when there is none. The command that follows the packages is not a package."""
    packages: list[str] = []
    take_next = False
    for tok in args:
        if take_next:
            take_next = False
            packages.extend(_npm_names([tok]))
        elif tok in {"-c", "--call"}:
            break  # a shell command follows, not a package
        elif tok.startswith("--package="):
            packages.extend(_npm_names([tok[len("--package="):]]))
        elif tok in {"-p", "--package"}:
            take_next = True
        elif not tok.startswith("-"):
            if not packages:
                packages.extend(_npm_names([tok]))
            break
    yield from packages


def _is_placeholder(name: str) -> bool:
    return name.lower() in _PLACEHOLDER_NAMES or name.lower().startswith("your-")


def _command_packages(tokens: list[str]) -> Iterator[tuple[str, str]]:
    for i, tok in enumerate(tokens):
        nxt = tokens[i + 1] if i + 1 < len(tokens) else ""
        if tok in _PIP and nxt == "install" and (i == 0 or tokens[i - 1] in _LEAD):
            names, eco = _pip_names(tokens[i + 2:]), "pypi"
        elif tok == "npm" and nxt in _NPM_INSTALL and (i == 0 or tokens[i - 1] in _LEAD):
            names, eco = _npm_names(tokens[i + 2:]), "npm"
        elif tok == "npx" and (i == 0 or tokens[i - 1] in _NPX_LEAD):
            names, eco = _npx_names(tokens[i + 1:]), "npm"
        else:
            continue
        for name in names:
            if not _is_placeholder(name):
                yield eco, name


def extract_packages(body: str) -> list[tuple[str, str]]:
    """The ``(ecosystem, name)`` pairs that ``pip``, ``npm`` and ``npx`` lines in ``body`` install, in order.

    A command counts at the start of a line or after one of the words in ``_LEAD``, so prose such as
    "pip install is slow" names nothing. Flags, local paths, URLs, git and ``file:`` specs, and
    placeholders are skipped.
    """
    found: dict[tuple[str, str], None] = {}
    for tokens in _command_lines(body):
        for pair in _command_packages(tokens):
            found.setdefault(pair)
    return list(found)


def _clean_url(raw: str) -> Optional[str]:
    """``raw`` without the punctuation a sentence or a markdown link put after it, or None for a template."""
    url = raw.split("](", 1)[0].rstrip(".,;:!?*")  # a markdown link ends at ``](``
    while url[-1:] in (")", "]") and url.count(url[-1]) > url.count("(" if url[-1] == ")" else "["):
        url = url[:-1].rstrip(".,;:!?*")
    return url or None


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
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None and (address.is_loopback or address.is_unspecified):
        return True
    return bool(_PLACEHOLDER_HOST_RE.search(host + parts.path.lower()))


def extract_urls(body: str) -> list[str]:
    """The distinct ``http(s)`` URLs in ``body``, in order, without placeholders, at most :data:`MAX_URLS_PER_SKILL`.

    A URL followed by ``<``, ``{`` or ``$`` is a template (``https://api.x.com/<id>``), not a link.
    """
    urls: dict[str, None] = {}
    for m in _URL_RE.finditer(body):
        if _TEMPLATE_RE.search(m.group()) or body[m.end():m.end() + 1] in ("<", "{", "$"):
            continue
        url = _clean_url(m.group())
        if url is not None and not _placeholder_url(url):
            urls.setdefault(url)
    return list(urls)[:MAX_URLS_PER_SKILL]


def _status(client: httpx.Client, url: str) -> int:
    """The final status of ``url``: HEAD, then GET when HEAD is not a 2xx or 3xx."""
    with client.stream("HEAD", url) as response:
        if response.status_code < 400:
            return response.status_code
    with client.stream("GET", url) as response:
        return response.status_code


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


def _package_finding(client: httpx.Client, eco: str, name: str) -> Optional[Finding]:
    url = PYPI_URL.format(name=quote(name)) if eco == "pypi" else NPM_URL.format(name=quote(name, safe="@"))
    try:
        status = _status(client, url)
    except _PROBE_ERRORS:
        return None
    if status != 404:
        return None
    return Finding("warn", "package-not-found",
                   f"package {name!r} is not on {_REGISTRY[eco]} ({url} returned 404)")


def _link_finding(client: httpx.Client, url: str) -> Optional[Finding]:
    try:
        status = _status(client, url)
    except _PROBE_ERRORS as exc:
        if _is_dns_failure(exc):
            return Finding("warn", "link-dead", f"link {url} does not resolve (DNS failure)")
        return None
    if status not in _DEAD_LINK_STATUSES:
        return None
    return Finding("warn", "link-dead", f"link {url} returned {status}")


def _probe(client: httpx.Client, key: tuple) -> Optional[Finding]:
    if key[0] == "package":
        return _package_finding(client, key[1], key[2])
    return _link_finding(client, key[1])


def _probe_all(http: httpx.Client, keys: list[tuple]) -> dict[tuple, Optional[Finding]]:
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        return dict(zip(keys, pool.map(lambda key: _probe(http, key), keys), strict=True))


def _keys(body: str) -> list[tuple]:
    packages = [("package", eco, name) for eco, name in extract_packages(body)]
    return packages + [("link", url) for url in extract_urls(body)]


def add_online_findings(reviewed: Sequence[tuple["Skill", SkillReview]], *,
                        client: Optional[httpx.Client] = None) -> None:
    """Append the network findings to each review in ``reviewed``, probing each distinct target once.

    ``client`` is for tests; without it a :func:`make_client` client is opened and closed here.
    """
    plans = [(review, _keys(skill.body)) for skill, review in reviewed]
    unique = list(dict.fromkeys(key for _, keys in plans for key in keys))
    if not unique:
        return
    if client is None:
        with make_client() as own:
            results = _probe_all(own, unique)
    else:
        results = _probe_all(client, unique)
    for review, keys in plans:
        review.findings.extend(f for f in (results[key] for key in keys) if f is not None)
