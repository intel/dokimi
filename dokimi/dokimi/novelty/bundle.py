# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Resolve an artifact to the files it actually consists of, not just its entry point.

A skill is a bundle: a thin ``SKILL.md`` plus ``references/``, ``scripts/``, ``patterns/``
and sometimes ``evals/``. Measured over the 33-skill study corpus, **0.72 of all words
live outside ``SKILL.md``** (per skill: median 0.43, worst 0.99 — one skill carries 375
words in its body and 30,567 in 30 bundled files). Measuring the entry point alone
therefore says almost nothing about a progressive-disclosure skill, and its "dead tokens"
share is dominated by spans whose only job is to point at a file nobody read.

This module renders a bundle into one text with an offset→file map, so spans keep their
provenance and the 2×2 can be cut by *which file* carried the knowledge — the number an
author needs to decide what belongs in the body and what can stay a reference.

It also owns the bundle **record** (``Bundle.summary()``), which every later reader
consumes: ``atoms.json`` carries it so ``novelty probe`` can replay the selection, the
``novelty.v1`` artifact carries it so the gate and the report can say how much of the
artifact was read. Readers do not interpret the record themselves:

* :func:`exclusion_class` is the one rule for which ``excluded`` rows count as what;
* :class:`Coverage` (``Coverage.from_record``) is the one place the coverage figures are
  computed, for a live bundle and for a stored record of any age alike. Both live in
  :mod:`dokimi.metrics.bundle_coverage`, below the gate, and are re-exported here;
* :func:`check_answer_key` decides whether a saved answer key is still valid for the
  artifact as it is now, and returns a verdict instead of exiting.

One caveat rides along with every bundle run, and the analyzer states it: injecting the
whole bundle measures what the artifact *could* deliver if the agent read all of it. It is
an upper bound. Whether the agent chooses to load a given file at runtime is the agentic
track's question (``skill-agent:``), not this one.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# The record's reading side lives below the gate; these names stay importable from here.
from dokimi.metrics.bundle_coverage import (  # noqa: F401
    COVERAGE_BASES,
    COVERAGE_FIELDS,
    CURRENT_COVERAGE_BASIS,
    EXCLUSION_CLASS,
    EXCLUSION_CLASSES,
    LEGACY_COVERAGE_BASIS,
    MEASURED_SHARE_WARN_AT,
    Coverage,
    _safe_int,
    _share,
    _stored_basis,
    _stored_count,
    excluded_of,
    exclusion_class,
    exclusion_kind,
    named_exclusions,
)

from .artifact import load_artifact_text
from .spans import dangling_fence, protection_tags

#: Directory name → role. Anything else is ``other``; files directly beside ``SKILL.md``
#: are ``top``. The role is reported, never used to weight a result.
ROLE_BY_DIR = {
    "references": "reference",
    "reference": "reference",
    "docs": "reference",
    "patterns": "pattern",
    "scripts": "script",
    "tools": "script",
    "library": "script",
    "evals": "eval",
    "eval": "eval",
    "evaluation": "eval",
    "templates": "template",
    "triggers": "trigger",
    "guidelines": "reference",
    "data": "data",
}

#: Prose-ish files are rendered as-is; everything else is fenced so the segmenter keeps a
#: script whole instead of splitting it into "sentences". Rendering only: whether a file
#: is text at all is decided by its content in `_read`.
PROSE_SUFFIXES = frozenset({".md", ".markdown", ".txt", ".rst"})
_LANG_BY_SUFFIX = {".py": "python", ".sh": "bash", ".bash": "bash", ".yaml": "yaml",
                   ".yml": "yaml", ".json": "json", ".toml": "toml"}

#: Roles measured when the caller does not name any. `eval` is deliberately absent: a
#: skill's own `evals/` files are the question/answer keys for testing it, not knowledge the
#: agent reads at runtime, so extracting atoms from them and probing those atoms would
#: credit the skill with delivering its own test set. 10 of the 33 corpus skills ship
#: `evals/`. Opt in with `--resource-roles ... eval` when that is what you mean.
DEFAULT_ROLES = ("reference", "pattern", "script", "template", "trigger", "data", "top",
                 "other")

#: Directories that are never artifact content: version control, build and tool output,
#: virtualenvs. Without this, a `.venv` or `.github` under a skill would be read as skill
#: text and would inflate `resource_words_on_disk` and therefore `outside_share`. Any name
#: starting with a dot is skipped as well, files included (`.notes.md`, `.env`).
SKIP_DIR_NAMES = frozenset({"__pycache__", "node_modules", "build", "dist", ".git",
                            "venv", "env", "virtualenv", "site-packages"})

#: Prefix of the heading `_stitch` injects before each bundled file. Those headings are
#: scaffolding, not artifact text, so they are reported as pointer spans rather than
#: counted as tokens that carry no knowledge.
BUNDLE_HEADER_PREFIX = "## "

#: Default budget. Without a cap, one 30k-word bundle would set the atom cap's meaning
#: adrift between artifacts; with it, exclusions are reported rather than silent.
DEFAULT_MAX_FILE_WORDS = 4_000
DEFAULT_MAX_TOTAL_WORDS = 20_000


@dataclass
class BundleFile:
    """One file of the artifact, with where its text landed in the rendered bundle."""

    path: str
    role: str
    text: str
    start: int = 0
    end: int = 0
    #: Word count of the file as it is on disk. ``text`` may carry a code fence added for
    #: segmentation, which must not count against the budget or the on-disk total.
    source_words: int | None = None
    #: Digest of the file's text *as decoded and read* (UTF-8), not of its raw bytes, so a
    #: line-ending change alone does not move it. For the entry point it covers the body
    #: the measurement used, with frontmatter already split off. Recorded so a later command
    #: can tell an edited reference from an unchanged one: a word count cannot -- changing
    #: `64` to `128` leaves it identical.
    sha256: str | None = None

    @property
    def words(self) -> int:
        """Whitespace-separated word count — comparable across files, not a token count."""
        return self.source_words if self.source_words is not None else len(self.text.split())

    def as_dict(self) -> dict:
        """Serializable form, without the file's text."""
        return {"path": self.path, "role": self.role, "words": self.words,
                "start": self.start, "end": self.end, "sha256": self.sha256}


@dataclass
class Bundle:
    """An artifact resolved to its files, rendered into one text."""

    kind: str
    name: str
    description: str
    frontmatter: str
    text: str
    files: list[BundleFile]
    #: Each row names the file, its role, why it was left out and a ``kind``; what a row
    #: *means* is its class (:data:`EXCLUSION_CLASS`), never a reader's own rule.
    excluded: list[dict] = field(default_factory=list)
    include_resources: bool = False
    #: Words outside the entry point in the bundle *on disk*, whether included or not.
    resource_words_on_disk: int = 0
    #: The budgets this bundle was selected under, recorded so a later command can
    #: reproduce the same selection instead of guessing it from the result.
    max_file_words: int = DEFAULT_MAX_FILE_WORDS
    max_total_words: int = DEFAULT_MAX_TOTAL_WORDS
    #: The roles the walk was asked to measure (the default set, or ``--resource-roles``),
    #: recorded because the roles of the files that happened to be selected are not the
    #: same thing: a role whose files were all over budget selects nothing.
    resource_roles: tuple[str, ...] = ()
    #: The reference the bundle was loaded from (``skill:<path>``), recorded with it.
    ref: str = ""

    @property
    def body(self) -> str:
        """The entry point's own text (``SKILL.md`` body), without bundled files."""
        return self.files[0].text if self.files else ""

    @property
    def unreadable_files(self) -> int:
        """Files in the ``unknown`` class: text whose words could not be counted."""
        return len(excluded_of(self.excluded, "unknown"))

    def source_of(self, offset: int) -> str:
        """Which file the character at ``offset`` came from."""
        for item in self.files:
            if item.start <= offset < item.end:
                return item.path
        return self.files[0].path if self.files else ""

    def coverage(self) -> Coverage:
        """The coverage figures, computed exactly as they will be read back from the record."""
        coverage = Coverage.from_record(self._counts())
        assert coverage is not None        # _counts always carries COVERAGE_FIELDS
        return coverage

    def coverage_note(self) -> str | None:
        """What a command should say before it measures, or ``None``.

        A bundle run names the files its budgets left out; an entry-point run says how much
        a bundle run would add -- counting readable words only, since a skill's own evals
        are measured in neither mode and a rerun would read nothing more.
        """
        coverage, gaps = self.coverage(), excluded_of(self.excluded, "gap")
        if self.include_resources and gaps:
            return (f"{len(gaps)} bundled files will not be measured "
                    f"({coverage.unmeasured_words} words over the budgets); see "
                    "bundle.excluded in the result for each reason")
        if not self.include_resources and coverage.unmeasured_words:
            return (f"{coverage.outside_share_on('readable'):.0%} of this artifact's words are in "
                    f"{len(gaps)} bundled files that will not be measured; pass "
                    "--include-resources to measure them")
        return None

    def coverage_warnings(self) -> list[str]:
        """The warnings a result measured on this bundle must carry."""
        coverage, gaps = self.coverage(), excluded_of(self.excluded, "gap")
        out: list[str] = []
        # The readable basis, not the on-disk one: a skill whose resources are all its own
        # evals has nothing a bundle run would add, so pointing at the flag would be advice
        # that cannot be followed. The gate judges the same basis (`CURRENT_COVERAGE_BASIS`).
        share = coverage.outside_share_on("readable")
        if not self.include_resources and share >= 1 - MEASURED_SHARE_WARN_AT:
            out.append(
                f"{share:.0%} of this artifact's words are in "
                "bundled files that were not measured (--include-resources); every rate "
                "here describes the entry point only"
            )
        if self.include_resources and gaps:
            out.append(
                f"{len(gaps)} bundled files were excluded from the measured text (see "
                "bundle.excluded for each reason); they are counted in "
                "resource_words_on_disk but carry no atoms"
            )
        return out

    def measured_digest(self) -> str:
        """Digest of exactly the files this bundle measured, and of nothing else.

        Hashing the artifact's directory cannot separate an entry-point run from a bundle
        run of the same skill, and for a file ref it would cover siblings that were never
        read. This covers each measured file's path and the digest of the text that was
        measured, in order -- so two results carry the same digest only when they measured
        the same bytes of the same files.
        """
        rows = "\n".join(f"{item.path}:{item.sha256}" for item in self.files)
        return "sha256:" + hashlib.sha256(rows.encode("utf-8")).hexdigest()

    def _counts(self) -> dict:
        """The record's raw counts, from which every coverage figure is derived."""
        return {
            "include_resources": self.include_resources,
            "n_files": len(self.files),
            "entry_point_words": len(self.body.split()),
            "resource_words_measured": sum(f.words for f in self.files[1:]),
            "resource_words_on_disk": self.resource_words_on_disk,
            "unreadable_files": self.unreadable_files,
            # A skill's own `evals/` are text in the tree but a decision, not a gap. The
            # role is decided when the tree is walked, so this is the same number whether
            # or not the resources were requested.
            "policy_excluded_words": sum(
                int(row.get("words") or 0) for row in excluded_of(self.excluded, "decision")),
            "excluded": self.excluded,
            # What the gate's share is a share of. Written so that a later change of basis
            # cannot re-read this record on another one (see `LEGACY_COVERAGE_BASIS`).
            "coverage_basis": CURRENT_COVERAGE_BASIS,
        }

    def summary(self) -> dict:
        """The bundle record: what was measured, what was left out, and its digest.

        Written into ``atoms.json``, ``probes.json`` and the ``novelty.v1`` artifact alike,
        so every reader sees one shape. The derived shares are written for a human reading
        the JSON; readers recompute them from the counts with :meth:`Coverage.from_record`.
        """
        roles: dict[str, int] = {}
        for item in self.files[1:]:
            roles[item.role] = roles.get(item.role, 0) + 1
        record = self._counts()
        coverage = self.coverage()
        record.update({
            "artifact_name": self.name,
            "artifact_ref": self.ref,
            "measured_digest": self.measured_digest(),
            "outside_share": coverage.outside_share,
            "measurable_outside_share": coverage.measurable_outside_share,
            "measured_share": coverage.measured_share,
            "unmeasured_words": coverage.unmeasured_words,
            "max_file_words": self.max_file_words,
            "max_total_words": self.max_total_words,
            "resource_roles": list(self.resource_roles),
            "roles": roles,
            "files": [f.as_dict() for f in self.files],
        })
        return record


def load_bundle(
    ref: str,
    *,
    include_resources: bool = False,
    roles: tuple[str, ...] | None = None,
    max_file_words: int = DEFAULT_MAX_FILE_WORDS,
    max_total_words: int = DEFAULT_MAX_TOTAL_WORDS,
) -> Bundle:
    """Resolve ``skill:<path>`` / ``text:<path>`` to a :class:`Bundle`.

    With ``include_resources=False`` the bundle is the entry point alone — the historical
    behaviour — but ``resource_words_on_disk`` is still counted, so a run can say how much
    of the artifact it did not look at.

    Args:
        ref: Artifact reference, as accepted by ``load_artifact_text``.
        include_resources: Read the bundled files too.
        roles: Restrict to these roles (see :data:`ROLE_BY_DIR`); ``None`` means
            :data:`DEFAULT_ROLES`, which is every role except ``eval``.
        max_file_words: Per-file cap; a larger file is excluded whole rather than cut
            mid-sentence, because a truncated reference would be attributed as if complete.
        max_total_words: Cap across all resources. Files are taken in role order
            (reference, pattern, eval, then the rest) and alphabetically inside a role, so
            the selection is deterministic and recorded.
    """
    kind, name, description, frontmatter, body = load_artifact_text(ref)
    entry = Path(ref.removeprefix("skill:").removeprefix("text:"))
    root = entry.parent if entry.is_file() else entry
    entry_name = "SKILL.md" if kind == "skill" else (entry.name or str(entry))

    files = [BundleFile(path=entry_name, role="entry_point", text=body,
                        sha256=_digest_text(body))]
    excluded: list[dict] = []
    on_disk = 0
    candidates: list[tuple[int, str, Path, str, str]] = []

    selected_roles = DEFAULT_ROLES if roles is None else roles
    if kind == "skill" and root.is_dir():
        order = {"reference": 0, "pattern": 1, "eval": 2}
        entry_file = (entry if entry.is_file() else root / entry_name).resolve()
        for path in sorted(root.rglob("*")):
            # A directory or dangling symlink is not a file, but it is still recorded below.
            if not path.is_file() and not path.is_symlink():
                continue
            if path.resolve() == entry_file:
                continue        # the entry point itself, not a second copy of it
            parts = path.relative_to(root).parts
            if path.name.startswith(".") or any(
                part.startswith(".") or part in SKIP_DIR_NAMES for part in parts[:-1]
            ):
                continue  # not artifact content, and not counted as any of it
            relative = path.relative_to(root).as_posix()
            role = _role_of(path.relative_to(root))
            if path.is_symlink():
                # A symlink can point outside the artifact entirely. Recorded as excluded
                # rather than followed, so the measurement describes the skill's own files.
                excluded.append({"path": relative, "role": role, "reason": "symlink",
                                 "kind": "symlink"})
                continue
            text = _read(path)
            if text is None:
                if _is_binary(path) or (
                    path.suffix.lower() not in PROSE_SUFFIXES and not _looks_like_text(path)
                ):
                    excluded.append({"path": relative, "role": role, "reason": "not text",
                                     "kind": "not_text"})
                else:
                    # A prose file that does not decode (latin-1, say) is content this
                    # run did not read, not a binary: the gate reports coverage unmeasured.
                    excluded.append({"path": relative, "role": role, "reason": "unreadable",
                                     "kind": "unreadable"})
                continue
            words = len(text.split())
            on_disk += words
            if role not in selected_roles:
                # Decided here, before the mode is consulted, so an entry-point-only run
                # reports the same policy words as a bundle run and never tells the user
                # that --include-resources would measure them.
                reason = (
                    "role filtered" if roles is not None
                    else "role not measured by default (pass --resource-roles "
                         f"{role} to include it)"
                )
                excluded.append({"path": relative, "role": role, "words": words,
                                 "reason": reason, "kind": "policy"})
                continue
            candidates.append((order.get(role, 3), relative, path, role, text))

        budget = max_total_words
        for _rank, relative, path, role, text in sorted(candidates, key=lambda c: (c[0], c[1])):
            words = len(text.split())
            if not include_resources:
                excluded.append({"path": relative, "role": role, "words": words,
                                 "reason": "resources not requested", "kind": "not_requested"})
                continue
            if words > max_file_words:
                excluded.append({"path": relative, "role": role, "words": words,
                                 "reason": f"over max_file_words={max_file_words}",
                                 "kind": "budget"})
                continue
            if words > budget:
                excluded.append({"path": relative, "role": role, "words": words,
                                 "reason": f"over max_total_words={max_total_words}",
                                 "kind": "budget"})
                continue
            budget -= words
            files.append(BundleFile(path=relative, role=role, text=_render(path, text),
                                    source_words=words, sha256=_digest_text(text)))

    text = _stitch(files)
    return Bundle(kind=kind, name=name, description=description, frontmatter=frontmatter,
                  text=text, files=files, excluded=excluded,
                  include_resources=include_resources, resource_words_on_disk=on_disk,
                  max_file_words=max_file_words, max_total_words=max_total_words,
                  resource_roles=tuple(selected_roles), ref=ref)


#: Why a saved answer key was refused. ``unloadable``: the artifact cannot be read now;
#: ``no_file_rows``: the record names no usable files, so its provenance cannot be checked;
#: ``wrong_artifact``: the atoms were extracted from another artifact; ``files_changed``:
#: a bundled file was removed, edited or added since extraction.
ANSWER_KEY_PROBLEMS = ("unloadable", "no_file_rows", "wrong_artifact", "files_changed")


@dataclass(frozen=True)
class AnswerKeyVerdict:
    """Whether a saved answer key may be probed against an artifact as it is now.

    ``problem`` is ``None`` for a valid key; ``text`` and ``bundle`` are then the context to
    inject (``bundle`` is ``None`` for a record that predates bundle provenance, which is
    probed against the entry point alone). ``notes`` are the things the caller must say
    even when the key is valid: a legacy assumption, an edited entry point.
    """

    problem: str | None = None
    message: str = ""
    text: str = ""
    bundle: Bundle | None = None
    notes: tuple[str, ...] = ()
    #: The file sets that were compared (``files_changed`` explains itself with these).
    recorded: tuple[str, ...] = ()
    rebuilt: tuple[str, ...] = ()
    edited: tuple[str, ...] = ()
    added: tuple[str, ...] = ()
    entry_point_changed: bool = False

    @property
    def ok(self) -> bool:
        """True when the key is valid for the artifact as it is now."""
        return self.problem is None


def _replay_selection(record: dict) -> dict[str, Any]:
    """The ``load_bundle`` arguments that reproduce a recorded selection."""
    rows = record["files"]
    # The roles the extraction asked for, not the roles of the files it happened to select:
    # a role whose files were all over budget selects nothing, and a rebuild narrowed to the
    # selected roles would call those files policy and lose the gap.
    requested = record.get("resource_roles")
    if requested is None:       # a record from before the roles were recorded
        requested = sorted({row["role"] for row in rows[1:]}) or None
    # Prefer the budgets the extraction actually used. Older records do not carry them, and
    # for those the selection is reproduced from its result: capping at the largest
    # selected file and at the selected total makes the same greedy pick.
    budgets: dict[str, int] = {}
    if record.get("max_file_words") and record.get("max_total_words"):
        budgets = {"max_file_words": int(record["max_file_words"]),
                   "max_total_words": int(record["max_total_words"])}
    elif record.get("include_resources") and all("words" in r for r in rows[1:]):
        selected = [int(row["words"]) for row in rows[1:]]
        budgets = {"max_file_words": max(selected, default=0),
                   "max_total_words": sum(selected)}
    include = bool(record.get("include_resources"))
    return {"include_resources": include,
            "roles": tuple(requested) if requested is not None and include else None,
            **budgets}


def _changes_answer_key(row: dict, asked_roles: list | None) -> bool:
    """Whether a file that appeared since extraction invalidates the answer key.

    ``not_content`` never does -- a new image or symlink is not text any mode would read.
    A ``decision`` row does only when the record does not say which roles were asked for:
    then the rebuild was narrowed to the selected roles, and a file in a default role that
    simply had no file at extraction time (a first ``scripts/run.sh``) would be read by a
    re-extraction. Every other new row is text a re-extraction would measure.
    """
    cls = exclusion_class(row)
    if cls == "not_content":
        return False
    if cls == "decision":
        return asked_roles is None
    return True


def check_answer_key(ref: str, record: dict | None, *,
                     source: str = "atoms.json") -> AnswerKeyVerdict:
    """Is the answer key extracted under ``record`` still valid for ``ref`` as it is now?

    The bundle mode is taken from the record, not from flags: if atoms came from a bundle
    and the probe injected only ``SKILL.md``, every atom extracted from a reference file
    would score ``still_missing`` because the context never contained it -- a measurement
    error that looks like a result. So the selection is replayed and any difference in the
    *bundled* files is refused rather than reconciled. The entry point alone may change:
    re-measuring a shortened ``SKILL.md`` against the original atoms is the authoring
    path's last step, so that is a note, not a refusal.

    Args:
        ref: The artifact reference to probe (``skill:<path>``).
        record: The ``bundle`` block of the saved atoms (empty or ``None`` for a file
            written before bundle provenance).
        source: How to name the record in messages (the atoms file's path).

    Returns:
        A verdict; never raises for a stale or foreign key. ``problem`` is one of
        :data:`ANSWER_KEY_PROBLEMS`.
    """
    record = record or {}
    if not record:
        try:
            body = load_artifact_text(ref)[4]
        except (OSError, ValueError, RecursionError) as exc:
            return AnswerKeyVerdict(problem="unloadable", message=f"cannot load {ref}: {exc}")
        return AnswerKeyVerdict(text=body, notes=(
            f"WARNING: {source} predates bundle provenance; assuming the entry point only. "
            "Re-run `novelty atoms` if the atoms came from bundled files.",))

    rows = record.get("files") or []
    if not rows or any("role" not in row or "path" not in row for row in rows):
        return AnswerKeyVerdict(problem="no_file_rows", message=(
            f"{source} records a bundle without usable file rows; re-run `novelty atoms` "
            "rather than probing against an answer key whose provenance cannot be checked"))
    try:
        now = load_bundle(ref, **_replay_selection(record))
    except (OSError, ValueError) as exc:
        return AnswerKeyVerdict(problem="unloadable", message=f"cannot load {ref}: {exc}")

    # An answer key is for one artifact. Without this, skill A's atoms were accepted
    # against skill B whenever the relative paths lined up -- unconditionally so for
    # entry-point-only recordings, where both sides are just ["SKILL.md"].
    recorded_name = record.get("artifact_name")
    if recorded_name and recorded_name != now.name:
        return AnswerKeyVerdict(problem="wrong_artifact", message=(
            f"these atoms were extracted from `{recorded_name}` and --artifact is "
            f"`{now.name}`: an answer key belongs to one artifact. Extract atoms for this "
            "one instead."))

    recorded = [row["path"] for row in rows]
    rebuilt = [item.path for item in now.files]
    # Same path, different bytes: still a stale key, and a word count cannot see it (`64`
    # to `128` keeps the count). The digest does; the word count is the fallback for a
    # record written before digests. The entry point is exempt (see the docstring).
    edited = [
        row["path"]
        for row, item in zip(rows[1:], now.files[1:], strict=False)
        if (row.get("sha256") and item.sha256 and row["sha256"] != item.sha256)
        or (not row.get("sha256") and "words" in row and row["words"] != item.words)
    ]
    # The rebuild is filtered to the roles the extraction measured, so a file added since
    # in a role that had no file then would be filtered out of `rebuilt` and pass
    # unnoticed. Compare every path the walk saw against every path the extraction saw.
    added: list[str] = []
    if record.get("include_resources") and "excluded" in record:
        seen = set(recorded) | {row.get("path") for row in record.get("excluded") or []}
        asked = record.get("resource_roles")
        added = sorted(
            {row["path"] for row in now.excluded
             if row["path"] not in seen and _changes_answer_key(row, asked)}
            | (set(rebuilt) - set(recorded)))
    if rebuilt != recorded or edited or added:
        return AnswerKeyVerdict(
            problem="files_changed",
            message=(
                "the artifact's files changed since these atoms were extracted "
                f"({source}: {recorded}; now: {rebuilt}; edited: {edited}; "
                f"added: {added}). Re-run `novelty atoms`; probing a different file set "
                "against this answer key would mis-score every moved atom."),
            recorded=tuple(recorded), rebuilt=tuple(rebuilt), edited=tuple(edited),
            added=tuple(added))

    notes: list[str] = []
    # Allowed to differ, not allowed to differ silently: atoms extracted from text that is
    # gone will read as `still_missing`, a result about the edit that must be recognisable.
    entry_recorded = rows[0].get("sha256")
    entry_changed = bool(entry_recorded and entry_recorded != now.files[0].sha256)
    if entry_changed:
        notes.append(
            f"NOTE: {now.files[0].path} has changed since these atoms were extracted. That "
            "is the re-measure step, so the run proceeds; atoms whose text was removed will "
            "score `still_missing` because of the edit, not because of the model.")
    if record.get("include_resources"):
        notes.append(f"Measuring the bundle recorded in {source}: {len(recorded)} files, "
                     f"{record.get('resource_words_measured', 0)} bundled words")
    return AnswerKeyVerdict(text=now.text, bundle=now, notes=tuple(notes),
                            recorded=tuple(recorded), rebuilt=tuple(rebuilt),
                            entry_point_changed=entry_changed)


def entry_point_only(record: dict | None, spans: list, atoms: list) -> tuple[list, list, str | None]:
    """Restrict spans and atoms to the entry point when they came from a bundle.

    Shortening rewrites one file. A candidate built over bundle spans would propose
    dropping sentences that live in `references/`, and a rendered candidate would carry
    them into the shortened `SKILL.md`. So bundle spans are dropped, loudly: shortening a
    bundle is a different question than shortening a file. An atom must have every cited
    span inside the entry point, since half its evidence cannot be scored.

    Returns:
        ``(spans, atoms, note)``; ``note`` is ``None`` when nothing was dropped.

    Raises:
        ValueError: A bundle record without the file rows or the entry point's range.
    """
    record = record or {}
    if not record.get("include_resources"):
        return spans, atoms, None
    if not record.get("files"):
        raise ValueError("the atoms record a bundle run without file rows; re-run "
                         "`novelty atoms`")
    entry = record["files"][0]
    if "end" not in entry:
        raise ValueError(f"the atoms record no span range for {entry.get('path')}")
    kept = [span for span in spans if span.start < entry["end"]]
    kept_ids = {span.id for span in kept}
    kept_atoms = [atom for atom in atoms
                  if atom.span_ids and all(sid in kept_ids for sid in atom.span_ids)]
    note = (
        f"NOTE these atoms were extracted from a {record.get('n_files')}-file bundle. "
        f"Shortening rewrites one file, so only the {len(kept)} spans of {entry['path']} and "
        f"the {len(kept_atoms)} atoms they carry are scored ({len(spans) - len(kept)} bundle "
        "spans and their atoms dropped); knowledge scores here say nothing about the "
        "bundled files.")
    return kept, kept_atoms, note


def pointer_spans(spans: list, bundle: Bundle) -> set[str]:
    """Span ids whose content is a reference to a bundled file or directory.

    19 of the 33 study skills contain such spans (53 of 2,600), plus the per-file headings
    this module injects. They carry no knowledge *by design* — "see `references/tuning.md`" is a
    loading instruction — so counting them as dead weight overstates waste. Identified
    lexically: the span names a bundled path and contains nothing else checkable.
    """
    names: set[str] = set()
    for path in [item.path for item in bundle.files[1:]] + [
        str(row.get("path", "")) for row in bundle.excluded
    ]:
        if not path:
            continue
        names.add(path)
        names.add(Path(path).name)
        parts = Path(path).parts
        if len(parts) > 1:
            # A bare directory name is too weak on its own -- "Performance patterns" is
            # not a pointer at `patterns/` -- so the separator is part of the token.
            names.add(f"{parts[0]}/")
    if not names:
        return set()
    # Bare directory names must not match inside a word: `cache-references` is a perf
    # event, not a pointer at `references/`. A full relative path may be preceded by a
    # longer path, though, so `/` is allowed before it.
    alternatives = "|".join(sorted((re.escape(n) for n in names), key=len, reverse=True))
    pattern = re.compile(rf"(?<![\w-])(?:{alternatives})(?![\w-])")
    # The per-file headings `_stitch` injects are scaffolding this module wrote, not text
    # the author shipped, so they must not be charged to the artifact as dead tokens.
    injected = {
        f"{BUNDLE_HEADER_PREFIX}{item.role}: {item.path}".strip()
        for item in bundle.files[1:]
    }

    found: set[str] = set()
    for span in spans:
        if span.text.strip() in injected:
            found.add(span.id)
            continue
        if span.start >= bundle.files[0].end:
            continue  # only the entry point points at the bundle
        if not pattern.search(span.text):
            continue
        # Drop the path *and* the backticks that quoted it, or the empty code span left
        # behind would read as checkable content.
        remainder = re.sub(r"`\s*`", " ", pattern.sub("", span.text))
        # Ordering words are excluded from this test on purpose: "read `references/x.md`
        # before editing" orders the *loading*, not the work, and almost every pointer span
        # is phrased that way. A negation, version gate or threshold does carry a fact.
        tags = set(protection_tags(remainder)) - {"ordering"}
        if not _CHECKABLE.search(remainder) and not tags:
            found.add(span.id)
    return found


# A pointer span that also pins a value, a flag or an ordering is carrying knowledge as
# well as a path, so it stays a normal span. Protection tags (negation, version gate,
# ordering, threshold) come from `spans.protection_tags`, which is the same lexical rule
# the shortening axis uses, so the two cannot disagree about what is load-bearing.
_CHECKABLE = re.compile(
    r"(?<![\w.])\d+(?:\.\d+)*|(?<![\w-])-{1,2}[A-Za-z][\w-]*|`[^`]+`"
)


def _digest_text(text: str) -> str:
    """Digest of a file's text, as read."""
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _role_of(relative: Path) -> str:
    parts = relative.parts
    if len(parts) == 1:
        return "top"
    return ROLE_BY_DIR.get(parts[0].lower(), "other")


_UTF16_BOMS = (b"\xff\xfe", b"\xfe\xff")


def _is_binary(path: Path) -> bool:
    """A NUL byte in the head of the file, unless it is UTF-16 text (BOM, NULs throughout).

    PowerShell's `>` and Notepad's "Unicode" write UTF-16: a document in an encoding this
    run does not read, so it is reported unreadable rather than dropped as a binary.
    """
    try:
        with path.open("rb") as handle:
            head = handle.read(8192)
    except OSError:
        return False
    return b"\0" in head and not head.startswith(_UTF16_BOMS)


def _looks_like_text(path: Path) -> bool:
    """Whether a file that failed UTF-8 is still text (latin-1, cp1252) rather than a blob.

    Without this a latin-1 `scripts/x.py` or an extension-less guide was dropped from the
    words on disk with no warning, while the same bytes in a `.md` were reported. Mostly
    printable ASCII (or a UTF-16 BOM) is text; a blob without NULs in its head is not.
    """
    try:
        with path.open("rb") as handle:
            head = handle.read(8192)
    except OSError:
        return False
    if head.startswith(_UTF16_BOMS):
        return True
    printable = sum(1 for byte in head if byte in (9, 10, 13) or 32 <= byte < 127)
    return bool(head) and printable / len(head) >= 0.9


def _read(path: Path) -> str | None:
    """The file's text, or ``None`` when it is not UTF-8 text.

    Decided by content, not by suffix: real bundles ship extension-less guides and scripts,
    `.mjs`, `Makefile`, `.xml`. A suffix allowlist labelled all of those "not text", dropped
    their words from `resource_words_on_disk` and so under-reported `outside_share` with no
    warning. A NUL byte in the head of the file marks a binary; anything else must decode.
    A leading BOM is stripped, as `load_artifact_text` does for the entry point, so saving
    a reference as "UTF-8 with BOM" is not reported as an edit.
    """
    try:
        with path.open("rb") as handle:
            if b"\0" in handle.read(8192):
                return None
        return path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError):
        return None


def _render(path: Path, text: str) -> str:
    """Prose as-is; code fenced, so the segmenter keeps a script whole."""
    if path.suffix.lower() in PROSE_SUFFIXES:
        return text
    lang = _LANG_BY_SUFFIX.get(path.suffix.lower(), "")
    return f"```{lang}\n{text.rstrip()}\n```"


def _stitch(files: list[BundleFile]) -> str:
    """Concatenate with a heading per file, recording each file's offset range.

    Every chunk's code fences are closed before the next one starts, by the segmenter's own
    rule (``spans.dangling_fence``). A reference that quotes an opening fence and never
    closes it would otherwise swallow every following file into one code span, and the
    spans of those files would be attributed to the wrong source.
    """
    chunks: list[str] = []
    offset = 0
    for index, item in enumerate(files):
        header = "" if index == 0 else f"\n\n{BUNDLE_HEADER_PREFIX}{item.role}: {item.path}\n\n"
        body = item.text.strip()
        marker = dangling_fence(body)
        if marker:
            body += f"\n{marker}"
        chunk = header + body + "\n"
        item.start = offset
        item.end = offset + len(chunk)
        offset = item.end
        chunks.append(chunk)
    return "".join(chunks)
