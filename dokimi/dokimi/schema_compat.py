# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Refuse in-place schema edits that would reject artifacts already written.

An artifact schema file is a version: ``task_runs.v1.json`` is what every ``task_runs.v1``
artifact on disk was validated against. Editing it in place is fine while the edit only
*widens* what is accepted -- a new enum value, a looser bound or type, a new optional field
on a closed object (``additionalProperties: false``). An edit that narrows it (a new required
field, a tightened type, enum or bound, a property removed from a closed object) makes
artifacts that were valid when written fail to load, and is a new version instead: add
``<kind>.v<N+1>.json``, point :data:`dokimi.artifacts.SCHEMA_VERSIONS` at it and
list the old one in ``SUPERSEDED_VERSIONS``.

An open object (``additionalProperties`` absent, ``true`` or a schema) already accepted any
key, so a new typed property on it narrows too: ``"errored": "n/a"`` was valid and is not
any more. Almost every object in these schemas is open, so failing on each such addition
would fail most schema PRs and make the override label routine. The check therefore fails
only when an existing producer is known to write the key -- it appears at that path in the
base revision's fixture for the schema (``tests/fixtures/artifacts/<schema>``) -- and prints
a WARNING otherwise. Removing a property from an open object leaves its key accepted by
``additionalProperties``; that is compared against the old property schema, and warned about.

A changed ``description`` is reported as a note, not a failure: no validator can tell
whether a field kept its meaning, and a field whose meaning changed under the same version
is the break this check cannot see.

Usage::

    python -m dokimi.schema_compat --base origin/main
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dokimi import process

SCHEMA_DIR = Path(__file__).resolve().parent / "schemas"

# Lower bounds a newer schema may not raise, upper bounds it may not lower.
_LOWER = ("minimum", "exclusiveMinimum", "minLength", "minItems", "minProperties")
_UPPER = ("maximum", "exclusiveMaximum", "maxLength", "maxItems", "maxProperties")
# Keywords whose value must not change (or appear) in place.
_FIXED = ("const", "pattern", "$ref", "propertyNames")
# Keywords compare() reasons about, and annotations that never reject anything (``format``
# included: validate_artifact runs without a format checker). Any other keyword that appears
# or changes (if/then/else, not, dependentSchemas, patternProperties, unevaluatedProperties,
# prefixItems, contains, multipleOf, ...) is reported as breaking: fail closed, not open.
_MODELLED = frozenset((
    "type", "enum", "required", "properties", "additionalProperties", "items", "$defs",
    "definitions", "anyOf", "allOf", "uniqueItems", "description", "dependentRequired",
    *_LOWER, *_UPPER, *_FIXED,
))
_ANNOTATIONS = frozenset((
    "title", "$comment", "examples", "default", "deprecated", "readOnly", "writeOnly",
    "$schema", "$id", "format", "contentMediaType", "contentEncoding",
))


@dataclass
class Diff:
    breaking: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    # Narrowings no known artifact hits: printed, never fail the check.
    warnings: list[str] = field(default_factory=list)


_NO_FIXTURE = object()


def _types(schema: dict) -> set[str] | None:
    t = schema.get("type")
    if t is None:
        return None
    types = {t} if isinstance(t, str) else set(t)
    return types | {"integer"} if "number" in types else types


def _unconstrained(schema: Any) -> bool:
    return schema is True or (isinstance(schema, dict) and set(schema) <= _ANNOTATIONS
                              | {"description"})


def compare(old: Any, new: Any, path: str = "", diff: Diff | None = None, *,
            fixture: Any = _NO_FIXTURE) -> Diff:
    """Every way ``new`` rejects something ``old`` accepted, plus description notes.

    ``fixture`` is an artifact written under ``old`` (the base revision's fixture). It
    decides whether a new typed property on an open object is breaking (the fixture writes
    that key) or a warning (it does not, or there is no fixture).
    """
    diff = diff if diff is not None else Diff()
    _compare(old, new, path, diff, [] if fixture is _NO_FIXTURE else [fixture], {})
    return diff


def _compare(old: Any, new: Any, path: str, diff: Diff, seen: list[Any],
             refs: dict[str, list[Any]]) -> None:
    """``seen``: the fixture's values at this node; ``refs``: values reaching each $defs
    entry through a ``$ref``, so a definition is checked against what flows into it."""
    where = path or "<root>"
    if isinstance(old, bool) or isinstance(new, bool):
        if new is False and old is not False:
            diff.breaking.append(f"{where}: now rejects everything")
        elif old is True and isinstance(new, dict) and new:
            diff.breaking.append(f"{where}: was unconstrained, now constrained")
        return
    if not isinstance(old, dict) or not isinstance(new, dict):
        return
    ref = new.get("$ref")
    if isinstance(ref, str):
        for prefix in ("#/$defs/", "#/definitions/"):
            if ref.startswith(prefix):
                refs.setdefault(ref[len(prefix):], []).extend(seen)

    if old.get("description") != new.get("description") and "description" in new:
        diff.notes.append(f"{where}: description {'changed' if 'description' in old else 'added'}")

    old_types, new_types = _types(old), _types(new)
    if new_types is not None and (old_types is None or not old_types <= new_types):
        diff.breaking.append(
            f"{where}: type narrowed from {sorted(old_types) if old_types else 'any'} "
            f"to {sorted(new_types)}"
        )
    if "enum" in new:
        dropped = (
            ["(was unrestricted)"] if "enum" not in old
            else [v for v in old["enum"] if v not in new["enum"]]
        )
        if dropped:
            diff.breaking.append(f"{where}: enum no longer accepts {dropped}")
    for key in _FIXED:
        if key in new and old.get(key) != new[key]:
            diff.breaking.append(f"{where}: {key} changed from {old.get(key)!r} to {new[key]!r}")
    for keys, tighter, verb in ((_LOWER, lambda n, o: n > o, "raised"),
                                (_UPPER, lambda n, o: n < o, "lowered")):
        for key in keys:
            if key not in new:
                continue
            if key not in old:
                diff.breaking.append(f"{where}: {key} {new[key]} added")
            elif tighter(new[key], old[key]):
                diff.breaking.append(f"{where}: {key} {verb} from {old[key]} to {new[key]}")
    if new.get("uniqueItems") and not old.get("uniqueItems"):
        diff.breaking.append(f"{where}: uniqueItems now required")

    for name in sorted(set(new.get("required", [])) - set(old.get("required", []))):
        diff.breaking.append(f"{where}: {name!r} is now required")
    # `key` present requires `deps`: it rejects nothing the old schema accepted when each dep
    # was required outright there, or already required by the same key.
    old_required, old_deps = set(old.get("required", [])), old.get("dependentRequired", {})
    for key, deps in sorted(new.get("dependentRequired", {}).items()):
        added = sorted(set(deps) - old_required - set(old_deps.get(key, [])))
        if added:
            diff.breaking.append(f"{where}: {key!r} now requires {added}")
    old_props, new_props = old.get("properties", {}), new.get("properties", {})
    old_extra = old.get("additionalProperties", True)
    new_extra = new.get("additionalProperties", True)
    objs = [v for v in seen if isinstance(v, dict)]

    def values(name: str) -> list[Any]:
        return [o[name] for o in objs if name in o]

    for name in sorted(old_props):
        if name in new_props:
            _compare(old_props[name], new_props[name], f"{path}/{name}", diff, values(name), refs)
        elif new_extra is False:
            diff.breaking.append(f"{where}: property {name!r} removed")
        else:
            # Still accepted, now as an additional property.
            _compare(old_props[name], new_extra, f"{path}/{name}", diff, values(name), refs)
            diff.warnings.append(f"{where}: property {name!r} removed from an open object; "
                                 "artifacts may still carry it")
    for name in sorted(set(new_props) - set(old_props)):
        if old_extra is False or _unconstrained(new_props[name]):
            continue  # a closed object never accepted the key; an empty schema rejects nothing
        sub = Diff()
        _compare(old_extra, new_props[name], f"{path}/{name}", sub, values(name), {})
        for line in sub.breaking:
            if values(name):
                diff.breaking.append(f"{line} (new property on an open object, and the base "
                                     f"fixture writes {name!r})")
            else:
                diff.warnings.append(f"{line} (new property on an open object; no base "
                                     f"fixture writes {name!r})")
    extra_seen = [v for o in objs for k, v in o.items() if k not in old_props]
    _compare(old_extra, new_extra, f"{path}/<additionalProperties>", diff, extra_seen, refs)
    if "items" in old or "items" in new:
        items_seen = [x for v in seen if isinstance(v, list) for x in v]
        _compare(old.get("items", True), new.get("items", True), f"{path}[]", diff,
                 items_seen, refs)

    for defs in ("$defs", "definitions"):
        old_defs, new_defs = old.get(defs, {}), new.get(defs, {})
        for name in sorted(old_defs):
            if name not in new_defs:
                diff.breaking.append(f"{where}: {defs}/{name} removed")
            else:
                _compare(old_defs[name], new_defs[name], f"{path}/{defs}/{name}", diff,
                         refs.get(name, []), refs)
    # anyOf only: widening a oneOf branch, or adding one, can make a value match two and so
    # reject it. oneOf is left unmodelled, so any change to it fails closed.
    old_opts, new_opts = old.get("anyOf", []), new.get("anyOf", [])
    if len(new_opts) < len(old_opts) or (new_opts and not old_opts):
        diff.breaking.append(f"{where}: anyOf has fewer alternatives")
    for i, (o, n) in enumerate(zip(old_opts, new_opts, strict=False)):
        _compare(o, n, f"{path}/anyOf[{i}]", diff, seen, refs)
    old_all, new_all = old.get("allOf", []), new.get("allOf", [])
    if len(new_all) > len(old_all):
        diff.breaking.append(f"{where}: allOf gained a constraint")
    for i, (o, n) in enumerate(zip(old_all, new_all, strict=False)):
        _compare(o, n, f"{path}/allOf[{i}]", diff, seen, refs)
    for key in sorted(set(new) - _MODELLED - _ANNOTATIONS):
        if old.get(key) != new[key]:
            diff.breaking.append(f"{where}: {key} {'changed' if key in old else 'added'} "
                                 "(not modelled here, so it cannot be shown to widen)")


class GitError(RuntimeError):
    """A git command that failed or timed out."""


class GitPathMissing(GitError):
    """``git show <rev>:<path>`` for a path the revision does not have. Only this failure
    means "not there": a timeout or any other error says nothing about the revision."""


# git's messages for a <rev>:<path> it does not have (outside the repository, it cannot);
# LC_ALL=C keeps them untranslated.
_PATH_MISSING = ("' does not exist in '", "' exists on disk, but not in '", "' is outside repository at '")
_GIT_ENV = process.Env.inherit({"GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"})


def _git(*args: str) -> str:
    proc = process.git(*args, cwd=SCHEMA_DIR, timeout=60, env=_GIT_ENV)
    if proc.timed_out:
        raise GitError(f"git {' '.join(args)}: timed out")
    if not proc.ok:
        error = GitPathMissing if any(m in proc.stderr for m in _PATH_MISSING) else GitError
        raise error(f"git {' '.join(args)}: {proc.stderr.strip()}")
    return proc.stdout


FIXTURE_DIR = "../../tests/fixtures/artifacts"


def _renamed_from(base: str, rel: str) -> str | None:
    """Where the directory ``rel`` (relative to the schemas) lived at ``base``, if it was moved.

    A change that moves the schemas (renaming the project directory or package) leaves nothing
    at the current path in ``base``, and listing that path would compare zero schemas and pass.
    git's rename detection says where the files came from: the first renamed file under the
    directory gives the old directory, as a path from the repository root with a trailing ``/``.
    """
    top = Path(_git("rev-parse", "--show-toplevel").strip()).resolve()
    prefix = (SCHEMA_DIR / rel).resolve().relative_to(top).as_posix() + "/"
    fields = _git("-C", str(top), "diff", "--name-status", "-M", "-z", base).split("\0")
    i = 0
    while i < len(fields) - 2:
        status = fields[i]
        if status.startswith(("R", "C")):
            old, new = fields[i + 1], fields[i + 2]
            tail = new[len(prefix):]
            if new.startswith(prefix) and old.endswith("/" + tail):
                return old[: len(old) - len(tail)]
            i += 3
        else:
            i += 2
    return None


def _base_has_schemas(base: str) -> bool:
    """Whether ``base`` has schemas here, or at a path git says they were moved from."""
    if any(n.endswith(".json") for n in _git("ls-tree", "--name-only", base, "./").split()):
        return True
    return _renamed_from(base, ".") is not None


def _base_fixture(base: str, name: str, fixture_dir: str = f"./{FIXTURE_DIR}/") -> Any:
    """The artifact fixture for schema ``name`` as of ``base``, if it had one."""
    try:
        return json.loads(_git("show", f"{base}:{fixture_dir}{name}"))
    except (GitPathMissing, json.JSONDecodeError):
        return _NO_FIXTURE


def compare_against(base: str) -> dict[str, Diff]:
    """Diff every schema that exists at ``base`` against the working tree.

    If the schemas were moved since ``base``, the base side is read from where they were.
    """
    schema_dir, fixture_dir = "./", f"./{FIXTURE_DIR}/"
    names = [n for n in _git("ls-tree", "--name-only", base, "./").split() if n.endswith(".json")]
    if not names and (moved := _renamed_from(base, ".")):
        schema_dir = moved
        fixture_dir = _renamed_from(base, FIXTURE_DIR) or fixture_dir
        names = [n for n in _git("ls-tree", "--full-tree", "--name-only", f"{base}:{moved}").split()
                 if n.endswith(".json")]
    results: dict[str, Diff] = {}
    for name in names:
        old = json.loads(_git("show", f"{base}:{schema_dir}{name}"))
        current = SCHEMA_DIR / name
        if not current.exists():
            results[name] = Diff(breaking=["schema file removed: artifacts written under it "
                                           "can no longer be validated"])
            continue
        diff = compare(old, json.loads(current.read_text(encoding="utf-8")),
                       fixture=_base_fixture(base, name, fixture_dir))
        if diff.breaking or diff.notes or diff.warnings:
            results[name] = diff
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m dokimi.schema_compat",
                                     description=__doc__.split("\n\n")[0])
    parser.add_argument("--base", required=True, help="git revision to compare against")
    args = parser.parse_args(argv)

    results = compare_against(args.base)
    n_breaking = 0
    if not results and not _base_has_schemas(args.base) and any(SCHEMA_DIR.glob("*.json")):
        print(f"WARNING  - no schemas found at {args.base} to compare against: nothing was "
              "checked. If the schemas moved, git did not detect the move.")
    for name, diff in sorted(results.items()):
        for line in diff.breaking:
            print(f"BREAKING {name} {line}")
        for line in diff.warnings:
            print(f"WARNING  {name} {line}")
        for line in diff.notes:
            print(f"note     {name} {line}")
        n_breaking += len(diff.breaking)
    if n_breaking:
        print(f"\n{n_breaking} breaking change(s) to existing schema versions. Add "
              "<kind>.v<N+1>.json instead of editing a version in place (see this "
              "module's docstring).", file=sys.stderr)
        return 1
    if any(d.warnings for d in results.values()):
        print("\nWarnings are narrowings no known artifact hits. If some producer does write "
              "such a key, it is a new version.")
    if any(d.notes for d in results.values()):
        print("\nDescriptions changed. If an existing field changed meaning, that is a new "
              "version even though every old artifact still validates.")
    print(f"schema compatibility vs {args.base}: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
