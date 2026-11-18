# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""A repository's own scan settings, ``.dokimi/scan.toml``, and the baseline of a gate (#851).

The repository owns its noise: names it knows are external go in ``[[ignore]]``, with a reason,
and no dokimi release is needed to quiet them. A bare identifier the tree lacks is ``unresolved``
by default (a name may be an external API, a sibling package or a builtin); ``identifiers =
"fail"`` opts back in to failing on it.

```toml
identifiers = "unresolved"   # or "fail"
[[ignore]]
pattern = "mkl_*"            # fnmatch on the claim text
kind = "identifier"          # optional: only claims of this kind
reason = "sibling packages in other repositories"
```

A baseline is an earlier ``repo_readiness.v1`` report (``repo scan --out``). With it, a gate
fails only on a ``fail`` claim the baseline does not hold: drift a change introduced, not the
state the repository started from.
"""

from __future__ import annotations

import fnmatch
import tomllib
from dataclasses import dataclass, field
from collections.abc import Iterable
from typing import Any

from ..source import SourceError

CONFIG_PATH = ".dokimi/scan.toml"
IDENTIFIER_POLICIES = ("unresolved", "fail")
NOT_FOUND = "not found in tracked non-doc files"


@dataclass(frozen=True)
class Ignore:
    pattern: str
    reason: str
    kind: str | None = None

    def matches(self, claim: dict[str, Any]) -> bool:
        return (self.kind is None or claim.get("kind") == self.kind) and fnmatch.fnmatchcase(
            str(claim.get("claim")), self.pattern)


@dataclass(frozen=True)
class ScanConfig:
    identifiers: str = "unresolved"
    ignore: tuple[Ignore, ...] = ()
    source: str | None = None  # the file it was read from, None when the tree has none
    errors: tuple[str, ...] = field(default=())


def parse_config(text: str, source: str = CONFIG_PATH) -> ScanConfig:
    """The settings in ``text``; a file with any error keeps the defaults and says why in ``errors``."""
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        return ScanConfig(source=source, errors=(f"{source}: {exc}",))
    errors: list[str] = []
    identifiers = data.get("identifiers", "unresolved")
    if identifiers not in IDENTIFIER_POLICIES:
        errors.append(f"{source}: identifiers must be one of {', '.join(IDENTIFIER_POLICIES)}, not {identifiers!r}")
        identifiers = "unresolved"
    ignores: list[Ignore] = []
    raw = data.get("ignore", [])
    if not isinstance(raw, list):
        errors.append(f"{source}: ignore must be a list of [[ignore]] tables, not {type(raw).__name__}")
        raw = []
    for i, entry in enumerate(raw, 1):
        if not isinstance(entry, dict) or not isinstance(entry.get("pattern"), str) or not entry["pattern"]:
            errors.append(f"{source}: ignore entry {i} needs a non-empty pattern")
            continue
        if not isinstance(entry.get("reason"), str) or not entry["reason"].strip():
            errors.append(f"{source}: ignore entry {i} ({entry['pattern']!r}) needs a reason")
            continue
        kind = entry.get("kind")
        ignores.append(Ignore(entry["pattern"], entry["reason"].strip(), kind if isinstance(kind, str) else None))
    unknown = sorted(set(data) - {"identifiers", "ignore"})
    if unknown:
        errors.append(f"{source}: unknown key(s) {', '.join(unknown)}")
    if errors:  # one mistake discards every setting, so a half-read file never changes a verdict
        return ScanConfig(source=source, errors=tuple(errors))
    return ScanConfig(identifiers, tuple(ignores), source)


def load_config(tree: Any) -> ScanConfig:
    """``.dokimi/scan.toml`` of the tree, or the defaults when it has none."""
    if CONFIG_PATH not in set(tree.files()):
        return ScanConfig()
    try:
        return parse_config(tree.read_text(CONFIG_PATH))
    except (OSError, UnicodeDecodeError, SourceError) as exc:  # SourceError: a dangling or escaping symlink
        return ScanConfig(source=CONFIG_PATH, errors=(f"{CONFIG_PATH}: {exc}",))


def apply_config(claims: list[dict[str, Any]], config: ScanConfig, files: Iterable[str] = ()) -> dict[str, Any]:
    """Apply the identifier policy and the ignores to ``claims`` in place; the ``scan_config`` block of the report.

    A bare identifier the tree's code lacks but that names a tracked file (``bench_fftnd`` for ``bench_fftnd.py``)
    is ``ok_stem``. An ignored claim that is not ``ok`` becomes ``unresolved_ignored``: still listed, never a failure.
    """
    hits = [0] * len(config.ignore)
    demoted = 0
    stems = file_stems(list(files))
    for claim in claims:
        status = str(claim.get("status", ""))
        framework = claim.get("source", "framework") == "framework"  # a repository's own verdict is not re-read
        if framework and claim.get("kind") == "identifier" and not status.startswith("ok") \
                and (stem := stems.get(str(claim.get("claim")).split("::")[-1])):
            claim["status"], claim["why"] = "ok_stem", f"`{claim['claim']}` names {stem} (no extension)"
            continue
        if framework and config.identifiers == "unresolved" and claim.get("kind") == "identifier" and status == "fail" \
                and NOT_FOUND in str(claim.get("why")):
            claim["status"] = "unresolved_identifier"
            claim["why"] = (f"{claim['why']}; a bare name may be external (an API, a sibling package, a builtin), "
                            f"so it does not fail unless {CONFIG_PATH} sets identifiers = \"fail\"")
            demoted += 1
            status = claim["status"]
        if status.startswith("ok"):
            continue
        for i, rule in enumerate(config.ignore):
            if rule.matches(claim):
                hits[i] += 1
                claim["status"] = "unresolved_ignored"
                claim["why"] = f"ignored by {CONFIG_PATH} (`{rule.pattern}`: {rule.reason}); was: {claim.get('why')}"
                break
    return {"source": config.source, "identifiers": config.identifiers, "identifiers_demoted": demoted,
            "ignore": [{"pattern": r.pattern, "kind": r.kind, "reason": r.reason, "hits": n}
                       for r, n in zip(config.ignore, hits, strict=True)],
            "errors": list(config.errors)}


def file_stems(files: list[str]) -> dict[str, str]:
    """Each tracked file's name without its extension, to the first such file: `bench_fftnd` for `bench_fftnd.py`."""
    out: dict[str, str] = {}
    for f in files:
        stem = f.rsplit("/", 1)[-1].split(".", 1)[0]
        if stem:
            out.setdefault(stem, f)
    return out


def _key(claim: dict[str, Any]) -> tuple[str, str, str]:
    """A claim's identity across commits: file, kind and text. The line is left out, so an edit above it does not
    make an old failure new."""
    return str(claim.get("file")), str(claim.get("kind")), str(claim.get("claim"))


def apply_baseline(payload: dict[str, Any], baseline: dict[str, Any], source: str) -> dict[str, Any]:
    """Mark each ``fail`` claim the baseline report also fails ``baselined``; the ``baseline`` block of the report."""
    old = {_key(c) for c in baseline.get("claims") or [] if str(c.get("status", "")).startswith("fail")}
    now_fail = [c for c in payload["claims"] if str(c.get("status", "")).startswith("fail")]
    for claim in now_fail:
        claim["baselined"] = _key(claim) in old
    still = {_key(c) for c in now_fail}
    block = {"source": source, "known": sum(c["baselined"] for c in now_fail),
             "new": sum(not c["baselined"] for c in now_fail), "fixed": len(old - still)}
    payload["baseline"] = block
    return block


def new_failures(payload: dict[str, Any]) -> int:
    """The ``fail`` claims a gate counts: every one, or with a baseline only those it does not hold."""
    if "claims" not in payload:
        return int((payload.get("counts") or {}).get("fail") or 0)
    return sum(1 for c in payload["claims"] if str(c.get("status", "")).startswith("fail") and not c.get("baselined"))
