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
import shlex
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import yaml

from .shell_split import split_script

ACTS = ("install", "import", "use", "remove")
_PACKAGE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_DOTTED = re.compile(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*")
_INSTALL_VERBS = frozenset({"install", "add"})
_REMOVE_VERBS = frozenset({"uninstall", "remove"})
_PYTHON = re.compile(r"python\d*(?:\.\d+)?")
_PIP = re.compile(r"pip\d*(?:\.\d+)?")
_REQUIREMENT_END = re.compile(r"[<>=!~\[;@]")  # where a requirement's name ends: a version, extras, a URL


def validate_candidates(raw: Any, where: str = "--candidates") -> dict[str, dict[str, tuple[str, ...]]]:
    """A ``{id: {act: [names]}}`` mapping as ``{id: {act: (names,)}}``; ``ValueError`` names what is wrong."""
    if raw is None:
        return {}
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
    """Whether the call took effect: a denied one or one that failed did not install, import, use or remove anything."""
    return not event.get("denied") and event.get("rc") in (None, 0)


def choice_timeline(events: Sequence[dict[str, Any]]) -> list[tuple[float | None, str, str]]:
    """The ``(t, candidate, act)`` of each choice event, in the order the trace gives them."""
    return [(e.get("t"), str(e["candidate"]), str(e["act"])) for e in events if e.get("kind") == "choice"]


def _command_choices(event: dict[str, Any], candidates: Mapping[str, Mapping[str, Sequence[str]]]) -> list[dict]:
    """An install or removal counts only where the command's exit code is its own: every part joined to the next by
    ``&&`` (rc 0 then means each ran), and not in a pipeline (the last stage's code). A ``;``, ``||`` or ``&`` part may
    have failed or never run, so its install gives no choice."""
    parts = split_script(str(event.get("text", "")))
    if any(part.after not in ("", "&&") for part in parts):
        return []
    found: dict[tuple[str, str], None] = {}
    for part, *_ in parts:
        words = _words(part)
        call = None if "|" in words else _package_call(words)
        if call is None:
            continue
        act, packages = call
        for cid, acts in candidates.items():
            if _wanted(acts, act) & set(packages):
                found[(cid, act)] = None
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
    statement may follow another on its line after a ``;``."""
    if re.search(rf"(?:^|;)[ \t]*from\s+{re.escape(name)}(?!\w)", text, re.M):
        return True
    for line in re.finditer(r"(?:^|;)[ \t]*import\s+([^\n#;]+)", text, re.M):
        for part in line.group(1).split(","):
            module = re.split(r"\s+as\s+", part.strip())[0]
            if module == name or module.startswith(name + "."):
                return True
    return False


def _calls(name: str, text: str) -> bool:
    return re.search(rf"(?<![\w.]){re.escape(name)}(?!\w)", text) is not None


def _words(part: str) -> list[str]:
    try:
        return shlex.split(part)
    except ValueError:  # an unbalanced quote: the words as they are typed
        return part.split()


def _package_call(words: list[str]) -> tuple[str, list[str]] | None:
    """The act and the package names of a package manager's install, add, uninstall or remove call."""
    if "--dry-run" in words:  # a dry run installs nothing
        return None
    rest = _manager_rest(words)
    while rest and rest[0].startswith("-"):  # ``pip --quiet install``: the options before the verb
        rest = rest[1:]
    if not rest:
        return None
    verb = rest[0].casefold()
    act = "install" if verb in _INSTALL_VERBS else "remove" if verb in _REMOVE_VERBS else None
    if act is None:
        return None
    return act, [_package(w) for w in rest[1:] if not w.startswith("-") and _package(w)]


def _manager_rest(words: list[str]) -> list[str]:
    """The words after the package manager's program (``pip``, ``python -m pip``, ``uv pip``, ``conda``...)."""
    if not words:
        return []
    head = posixpath.basename(words[0].replace("\\", "/")).casefold()
    if _PYTHON.fullmatch(head) and words[1:3] == ["-m", "pip"]:
        return words[3:]
    if head == "uv" and words[1:2] == ["pip"]:
        return words[2:]
    if head == "uv" and words[1:2] in (["add"], ["remove"]):  # uv's project verbs: ``uv add`` installs a requirement
        return words[1:]
    if _PIP.fullmatch(head) or head in ("conda", "mamba", "poetry"):
        return words[1:]
    return []


def _package(word: str) -> str:
    """A requirement's name as the package manager compares it: no version, extras or URL; case and ``-_.`` folded."""
    name = _REQUIREMENT_END.split(word, maxsplit=1)[0]
    return re.sub(r"[-_.]+", "-", name).casefold()
