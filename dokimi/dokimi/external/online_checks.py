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
_PIP_VALUE_OPTIONS = {
    "-r", "--requirement", "-c", "--constraint", "-e", "--editable", "-i", "--index-url",
    "--extra-index-url", "-f", "--find-links", "-t", "--target", "--prefix", "--root", "--cache-dir",
    "--proxy", "--timeout", "--trusted-host", "--python-version", "--platform", "--abi",
    "--implementation", "--upgrade-strategy", "--progress-bar", "--src", "--log", "--report",
    "--exists-action", "--global-option", "--install-option", "--config-settings", "-C",
    "--only-binary", "--no-binary", "--cert", "--client-cert", "--root-user-action",
}
_NPM_VALUE_OPTIONS = {"--prefix", "--registry", "--workspace", "-w", "--tag", "--cache", "--userconfig"}
_NON_PACKAGE_SUFFIXES = (".txt", ".whl", ".tar.gz", ".tgz", ".zip", ".cfg", ".toml", ".py")
_PYPI_NAME_RE = re.compile(r"([A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[^\]]*\])?(?:(?:===|==|>=|<=|~=|!=|<|>).*)?")
_NPM_NAME_RE = re.compile(r"(?:@[A-Za-z0-9._~-]+/)?[A-Za-z0-9._~-]+")
_PLACEHOLDER_NAMES = {"package", "packages", "pkg", "name", "module", "mypackage", "my-package"}
_URL_RE = re.compile(r"https?://[^\s\"'`<>]+", re.IGNORECASE)
_TEMPLATE_RE = re.compile(r"[{}$]")  # a template variable, such as ``{id}`` or ``${VERSION}``
_EXAMPLE_DOMAINS = ("example.com", "example.org", "example.net")
_RESERVED_SUFFIXES = (".localhost", ".test", ".invalid", ".example")
_PLACEHOLDER_HOST_RE = re.compile(r"(?<![a-z0-9])your-")


def make_client() -> httpx.Client:
    """The HTTP client of a run: redirects followed, :data:`TIMEOUT_SECONDS` per request."""
    return httpx.Client(follow_redirects=True, timeout=TIMEOUT_SECONDS,
                        headers={"User-Agent": "dokimi-catalog-check"})


def _split(text: str) -> list[str]:
    try:
        return shlex.split(text)
    except ValueError:  # an unbalanced quote: read it as whitespace-separated words
        return text.split()


def _command_lines(body: str) -> Iterator[list[str]]:
    for line in body.splitlines():
        for part in re.split(r"&&|\|\||[;|`]", line):
            tokens = _split(part)
            if tokens:
                yield tokens


def _is_package_word(tok: str) -> bool:
    return not tok.startswith(("-", ".", "/", "~", "$", "<", "{")) and "://" not in tok


def _pip_names(args: list[str]) -> Iterator[str]:
    skip = False
    for tok in args:
        if skip:
            skip = False
        elif tok in _PIP_VALUE_OPTIONS:
            skip = True
        elif _is_package_word(tok) and not tok.lower().endswith(_NON_PACKAGE_SUFFIXES) and "@" not in tok:
            match = _PYPI_NAME_RE.fullmatch(tok)
            if match:
                yield match.group(1)


def _npm_name(tok: str) -> Optional[str]:
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
    """The package ``npx`` runs: the first word after its flags, or the one after ``-p``."""
    take_next = False
    for tok in args:
        if tok in {"-c", "--call"}:
            return  # a shell command follows, not a package
        if tok.startswith("--package="):
            yield from _npm_names([tok[len("--package="):]])
            return
        if tok in {"-p", "--package"}:
            take_next = True
            continue
        if take_next or not tok.startswith("-"):
            yield from _npm_names([tok])
            return


def _is_placeholder(name: str) -> bool:
    return name.lower() in _PLACEHOLDER_NAMES or name.lower().startswith("your-")


def _command_packages(tokens: list[str]) -> Iterator[tuple[str, str]]:
    for i, tok in enumerate(tokens):
        if i > 0 and tokens[i - 1] not in _LEAD:
            continue
        nxt = tokens[i + 1] if i + 1 < len(tokens) else ""
        if tok in _PIP and nxt == "install":
            names, eco = _pip_names(tokens[i + 2:]), "pypi"
        elif tok == "npm" and nxt in _NPM_INSTALL:
            names, eco = _npm_names(tokens[i + 2:]), "npm"
        elif tok == "npx":
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
    url = raw.rstrip(".,;:!?*")
    while url[-1:] in (")", "]") and url.count(url[-1]) > url.count("(" if url[-1] == ")" else "["):
        url = url[:-1].rstrip(".,;:!?*")
    return url or None


def _placeholder_url(url: str) -> bool:
    try:
        host = (urlsplit(url).hostname or "").lower()
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
    return bool(_PLACEHOLDER_HOST_RE.search(host))


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


def _is_dns_failure(exc: BaseException) -> bool:
    seen: Optional[BaseException] = exc
    for _ in range(6):
        if seen is None:
            return False
        if isinstance(seen, socket.gaierror):
            return True
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
