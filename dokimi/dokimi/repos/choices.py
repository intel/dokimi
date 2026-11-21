# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Choice events: an agent installs, imports, uses or removes a candidate the caller names (contract §4.1).

    {"t": 4.0, "kind": "choice", "candidate": "sklearn", "act": "install", "path": null}

A candidate is ``{id: {act: [names]}}``. ``install`` and ``remove`` name packages a package manager is given (``pip
install``, ``uv pip uninstall``, ``conda remove``); a removal matches the names of both lists. ``import`` names modules
an added ``import``/``from`` line names;
``use`` names dotted names an added line mentions (a comment too: the match is textual). Only added text counts: the new side of an edit, never what it
replaced. The events go to ``events.jsonl`` beside the trace's own events; the counters do not read them.
"""

from __future__ import annotations

import posixpath
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import yaml

from .probes import command_stages, command_words
from .shell_split import split_script

ACTS = ("install", "import", "use", "remove")
_PACKAGE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_DOTTED = re.compile(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*")
_INSTALL_VERBS = frozenset({"install", "add"})
_REMOVE_VERBS = frozenset({"uninstall", "remove"})
_PYTHON = re.compile(r"python\d*(?:\.\d+)?")
_PIP = re.compile(r"pip\d*(?:\.\d+)?")
_REQUIREMENT_END = re.compile(r"[<>=!~\[;@]")  # where a requirement's name ends: a version, extras, a URL
_UV_VALUE_OPTIONS = frozenset({"--directory", "--project", "--config-file", "--cache-dir", "--python", "-p", "--color"})
_INFO_FLAGS = frozenset({"-h", "--help", "-V", "--version"})  # the manager prints text and exits 0, changing nothing
_VALUE_OPTIONS = frozenset({
    "-r", "--requirement", "-c", "--constraint", "-e", "--editable", "-t", "--target", "-i", "--index-url",
    "--extra-index-url", "-f", "--find-links", "--prefix", "--root", "--python", "--name", "-n", "-p", "--platform",
    "--python-version", "--abi", "--implementation", "--src", "--cache-dir", "--log", "--proxy", "--retries",
    "--timeout", "--cert", "--client-cert", "--trusted-host", "--config-settings", "-C", "--channel", "--group",
    "--with", "--report", "--upgrade-strategy", "--progress-bar", "--keyring-provider", "--no-binary", "--only-binary",
    "--global-option", "--install-option", "--exists-action", "--use-feature"})  # options that take the next word, which is not a package


def validate_candidates(raw: Any, where: str = "--candidates") -> dict[str, dict[str, tuple[str, ...]]]:
    """A ``{id: {act: [names]}}`` mapping as ``{id: {act: (names,)}}``; ``ValueError`` names what is wrong."""
    if raw is None or raw == {}:
        raise ValueError(f"{where}: the file names no candidate")
    if not isinstance(raw, Mapping):
        raise ValueError(f"{where}: expected a mapping of candidate to acts, got {type(raw).__name__}")
    config: dict[str, dict[str, tuple[str, ...]]] = {}
    for cid, acts in raw.items():
        if not isinstance(cid, str) or not cid.strip():
            raise ValueError(f"{where}: a candidate id must be a non-empty string")
        if not isinstance(acts, Mapping) or not acts:
            raise ValueError(f"{where}: {cid} must map some of {', '.join(ACTS)} to lists of names")
        config[cid] = {act: _names(where, cid, act, names) for act, names in acts.items()}
    return config


def _names(where: str, cid: str, act: str, names: Any) -> tuple[str, ...]:
    if act not in ACTS:
        raise ValueError(f"{where}: {cid}: unknown act {act!r} (known: {', '.join(ACTS)})")
    if not isinstance(names, list) or not all(isinstance(n, str) and n.strip() for n in names):
        raise ValueError(f"{where}: {cid}.{act} must be a list of non-empty strings")
    shape = _PACKAGE if act in ("install", "remove") else _DOTTED
    bad = [n for n in names if not shape.fullmatch(n.strip())]
    if bad:
        kind = "a package name" if act in ("install", "remove") else "a dotted Python name"
        raise ValueError(f"{where}: {cid}.{act} takes {kind}: {bad[0]!r}")
    return tuple(n.strip() for n in names)


def load_candidate_file(path: str) -> dict[str, dict[str, tuple[str, ...]]]:
    """A run's ``--candidates`` file (YAML, or JSON, which is YAML) through :func:`validate_candidates`."""
    try:
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise ValueError(f"--candidates {path}: {exc}") from exc
    return validate_candidates(raw, f"--candidates {path}")


def with_choices(events: list[dict[str, Any]], candidates: Mapping[str, Mapping[str, Sequence[str]]],
                 added: Sequence[tuple[dict[str, Any], str]]) -> list[dict[str, Any]]:
    """``events`` with the ``choice`` events each command or edit gives, after it. ``added`` pairs an edit event with
    the text it adds (``Transcript.added_text``); an edit without one gives no import or use."""
    if not candidates:
        return events
    texts = {id(event): text for event, text in added}
    out: list[dict[str, Any]] = []
    for event in events:
        out.append(event)
        if not _ran(event):
            continue
        if event.get("kind") == "command":
            out.extend(_command_choices(event, candidates))
        elif event.get("kind") == "edit" and id(event) in texts:
            out.extend(_text_choices(event, texts[id(event)], candidates))
    return out


def _ran(event: dict[str, Any]) -> bool:
    """Whether the call took effect: a denied one, one that failed, or one with no exit code (rc None: a cut-off call,
    or a shell command whose result carries none) did not install, import, use or remove anything."""
    return not event.get("denied") and event.get("rc") == 0


def choice_timeline(events: Sequence[dict[str, Any]]) -> list[tuple[float | None, str, str]]:
    """The ``(t, candidate, act)`` of each choice event, in the order the trace gives them."""
    return [(e.get("t"), str(e["candidate"]), str(e["act"])) for e in events if e.get("kind") == "choice"]


def _command_choices(event: dict[str, Any], candidates: Mapping[str, Mapping[str, Sequence[str]]]) -> list[dict]:
    """An install or removal counts only where the command's exit code is its own: every part joined to the next by
    ``&&`` (rc 0 then means each ran), and not in a pipeline (the last stage's code). A ``;``, ``||`` or ``&`` part may
    have failed or never run, so its install gives no choice."""
    parts = split_script(str(event.get("text", "")))
    if any(p.after not in ("", "&&") for p in parts[:-1]) or any(p.after not in ("", "&&", ";") for p in parts[-1:]):
        return []
    found: dict[tuple[str, str], None] = {}
    for part, *_ in parts:
        if part.startswith("("):  # a subshell: its exit code is not the command's own
            continue
        stages = command_stages(part)
        if len(stages) > 1:  # a pipeline reports its last stage's exit code
            continue
        words = stages[0] if stages else []  # ``exec >log`` has no program, ``> scipy`` no operand
        views = list(command_words(words))
        if any(view[:1] == ["exit"] for view in views):  # no later part runs
            break
        call = next(filter(None, map(_package_call, views)), None)
        if call is not None:
            act, packages = call
            for cid, acts in candidates.items():
                if _wanted(acts, act) & set(packages):
                    found[(cid, act)] = None
        if any(view[:1] == ["exec"] for view in views[:-1]):  # exec with a program replaces the shell; ``exec -l`` has none
            break
    return [_choice(event, cid, act, None) for cid, act in found]


def _text_choices(event: dict[str, Any], text: str,
                  candidates: Mapping[str, Mapping[str, Sequence[str]]]) -> list[dict]:
    out: list[dict] = []
    for cid, acts in candidates.items():
        if any(_imports(name, text) for name in acts.get("import", ())):
            out.append(_choice(event, cid, "import", event.get("path")))
        if any(_calls(name, text) for name in acts.get("use", ())):
            out.append(_choice(event, cid, "use", event.get("path")))
    return out


def _wanted(acts: Mapping[str, Sequence[str]], act: str) -> set[str]:
    """The normalised package names an install or a removal of the candidate names; a removal also takes ``install``."""
    names = [*acts.get("install", ()), *acts.get("remove", ())] if act == "remove" else list(acts.get("install", ()))
    return {_package(n) for n in names}


def _choice(event: dict[str, Any], cid: str, act: str, path: str | None) -> dict[str, Any]:
    out: dict[str, Any] = {"t": event.get("t"), "kind": "choice", "candidate": cid, "act": act, "path": path}
    if "agent" in event:  # a subagent's choice names its agent, as its probe and command events do
        out["agent"] = event["agent"]
    return out


def _imports(name: str, text: str) -> bool:
    """``from name import ...``, or ``import name`` among the comma-separated modules of an ``import`` line; a
    statement may follow another on its line after a ``;`` or a ``:``, a ``\\`` continues a line, a comment is no code."""
    text = re.sub(r"\\\n[ \t]*", " ", _code(text))
    if re.search(rf"(?:^|[;:])[ \t]*from\s+{re.escape(name)}(?!\w)", text, re.M):
        return True
    for line in re.finditer(r"(?:^|[;:])[ \t]*import\s+([^\n;]+)", text, re.M):
        for part in line.group(1).split(","):
            module = re.split(r"\s+as\s+", part.strip())[0]
            if module == name or module.startswith(name + "."):
                return True
    return False


def _code(text: str) -> str:
    """The text without its comments and the insides of its string literals (a quote left open runs to its line's end,
    or to the end of a triple-quoted one: an edit may add only part of a file)."""
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == "#":
            while i < n and text[i] != "\n":
                i += 1
        elif c in "'\"":
            quote = text[i:i + 3] if text[i:i + 3] == c * 3 else c
            i += len(quote)
            while i < n and not text.startswith(quote, i) and not (len(quote) == 1 and text[i] == "\n"):
                i += 2 if text[i] == "\\" else 1
            i += len(quote) if text.startswith(quote, i) else 0
            out.append(" ")
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _calls(name: str, text: str) -> bool:
    return re.search(rf"(?<![\w.]){re.escape(name)}(?!\w)", text) is not None


def _package_call(words: list[str]) -> tuple[str, list[str]] | None:
    """The act and the package names of a package manager's install, add, uninstall or remove call."""
    if {"--dry-run", "--download-only"} & set(words) or _INFO_FLAGS & set(words):  # no install: a dry run, a download, a help request
        return None
    rest = _manager_rest(words)
    while rest and rest[0].startswith("-"):  # ``pip --quiet install``: the options before the verb
        rest = rest[2:] if rest[0] in _VALUE_OPTIONS else rest[1:]
    if not rest:
        return None
    verb = rest[0].casefold()
    act = "install" if verb in _INSTALL_VERBS else "remove" if verb in _REMOVE_VERBS else None
    if act is None:
        return None
    names: list[str] = []
    skip = False
    for word in rest[1:]:  # ``> scipy`` names an output file, not a package
        if skip:
            skip = False
        elif word.startswith("-"):
            skip = word in _VALUE_OPTIONS
        elif _package(word):
            names.append(_package(word))
    return act, names


def _manager_rest(words: list[str]) -> list[str]:
    """The words after the package manager's program (``pip``, ``python -m pip``, ``uv pip``, ``conda``...)."""
    if not words:
        return []
    head = posixpath.basename(words[0].replace("\\", "/")).casefold().removesuffix(".exe")
    if _PYTHON.fullmatch(head) and words[1:3] == ["-m", "pip"]:
        return words[3:]
    if head == "uv":
        rest = words[1:]
        while rest and rest[0].startswith("-"):  # ``uv --quiet add``, ``uv --directory /tmp/p add``
            rest = rest[2:] if rest[0] in _UV_VALUE_OPTIONS else rest[1:]
        if rest[:1] == ["pip"]:
            return rest[1:]
        if "--no-sync" in rest:
            return []
        return rest if rest[:1] in (["add"], ["remove"]) else []  # uv's project verbs: ``uv add`` installs a requirement
    if _PIP.fullmatch(head) or head in ("conda", "mamba", "poetry"):
        return words[1:]
    return []


def _package(word: str) -> str:
    """A requirement's name as the package manager compares it: no channel, version, extras or URL; case and ``-_.`` folded."""
    name = _REQUIREMENT_END.split(word.rpartition("::")[2], maxsplit=1)[0].strip()
    return re.sub(r"[-_.]+", "-", name).casefold()
