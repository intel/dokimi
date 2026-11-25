# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`skill novelty view`: read the saved novelty artifact and atoms file (bad input raises, exit 2 by the CLI)."""

from __future__ import annotations

from pathlib import Path

from dokimi.files import InputError


def load_view_inputs(novelty_path: str, atoms_path: str):
    """``(artifact, atoms, spans, atoms record)`` from the two files; an unreadable or invalid one raises InputError.

    An older capped atoms file that holds only its sample is said on stderr (#371).
    """
    from dokimi.artifacts import load_artifact
    from dokimi.commands.novelty_atoms_file import warn_capped_sample
    from dokimi.novelty.atoms import load_atoms_record

    artifact = load_artifact("novelty", Path(novelty_path))
    atoms, spans, saved = load_atoms_record(atoms_path)
    warn_capped_sample(saved, atoms_path)
    return artifact, atoms, spans, saved


def colour_note(atom_verdicts: str | None) -> str:
    """What the colours of the re-rendered view rest on (the NOTE `skill novelty view` prints)."""
    if atom_verdicts:
        return (f"NOTE outcome and category colours come from {atom_verdicts}, the verdicts "
                "`skill novelty analyze` measured; atoms outside the run's sample stay uncoloured.")
    return ("NOTE colours here come from spans and categories only. For outcome colouring "
            "(added / degraded), pass --atom-verdicts, or render during `skill novelty analyze`, "
            "which has the paired runs.")


def load_atom_verdicts(path: str | None, artifact: dict, atoms: list,
                       saved: dict | None = None) -> tuple[dict | None, dict | None]:
    """``(outcomes, categories)`` per atom id from a ``--atom-verdicts`` file; ``(None, None)`` without one.

    A bad file raises InputError.

    The file must belong to the ``--novelty`` artifact and describe atoms of the ``--atoms`` file.
    docs/skill-novelty.md (the item analysis): "Runs of different artifacts, or of one artifact at
    different revisions (`artifact` or `ref_digest` differ), are refused." The same holds here:
    outcomes of one run painted on the spans of another would be a made-up view. ``saved`` is the
    parsed atoms record: its source digest must be the analysis's, and each verdict row must cite
    the spans its atom has in the ``--atoms`` file.
    """
    from dokimi.artifacts import load_artifact
    from dokimi.novelty.atom_verdicts import analysis_digest

    if not path:
        return None, None
    record = load_artifact("novelty_atoms", Path(path))
    ref = artifact["artifact"]["ref"]
    if record["artifact_ref"] != ref:
        raise InputError(
            f"{path} is the atom verdicts of {record['artifact_ref']!r}, but --novelty is the "
            f"analysis of {ref!r}: pass the .atom_verdicts.json written beside that novelty.json"
        )
    # Atom ids hash the statement only, so they cannot tell two runs of one artifact apart; a run_id
    # shared by two commands (DOKIMI_RUN_ID) is told apart by the digest of the novelty payload.
    for name, mine, theirs in (
        ("model", record["model"], artifact["model"]),
        ("artifact revision (ref_digest)", record["ref_digest"], artifact["artifact"].get("ref_digest")),
        ("run_id", record.get("run_id"), artifact.get("run_id")),
        ("analysis_digest", record["analysis_digest"], analysis_digest(artifact)),
    ):
        if mine != theirs:
            raise InputError(
                f"{path} is from another analysis of {ref!r}: its {name} is {mine!r}, but "
                f"--novelty has {theirs!r}: pass the .atom_verdicts.json written beside that novelty.json"
            )
    measured = ((saved or {}).get("bundle") or {}).get("measured_digest")
    if measured and measured != artifact["artifact"].get("ref_digest"):
        raise InputError(
            f"--atoms was extracted from {measured!r}, but --novelty measured "
            f"{artifact['artifact'].get('ref_digest')!r}: pass the .atoms.json written beside that novelty.json"
        )
    known = {atom.id: atom for atom in atoms}
    foreign = sorted({row["id"] for row in record["atoms"]} - known.keys())
    if foreign:
        raise InputError(
            f"{path} names atoms the --atoms file does not hold ({', '.join(foreign[:5])}"
            f"{', ...' if len(foreign) > 5 else ''}): pass the .atoms.json written beside "
            f"the novelty.json of the run that wrote it"
        )
    moved = sorted(row["id"] for row in record["atoms"]
                   if tuple(row["span_ids"]) != known[row["id"]].span_ids)
    if moved:
        raise InputError(
            f"{path} cites other spans than the --atoms file for {', '.join(moved[:5])}"
            f"{', ...' if len(moved) > 5 else ''}: the atoms were extracted again from another "
            f"revision; pass the .atoms.json written beside the novelty.json of the run that wrote it"
        )
    outcomes = {row["id"]: row["outcome"]["lenient"] for row in record["atoms"]
                if row["outcome"]["lenient"]}
    categories = {row["id"]: row["category"] for row in record["atoms"] if row["category"]}
    return outcomes, categories
