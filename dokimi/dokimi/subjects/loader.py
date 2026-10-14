# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Load evaluation subject descriptors."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import Any

from .models import NON_ARM_KINDS, SUBJECT_KINDS, SubjectArtifact, SubjectDescriptor, SubjectMember, SubjectSuite


def load_subject(path: str | Path) -> SubjectDescriptor:
    """Load and validate a JSON/TOML subject descriptor."""
    path = Path(path)
    raw = _load_mapping(path)
    subject_raw = _mapping(raw.get("subject"), "subject")
    repo_kind = str(subject_raw.get("kind") or "").strip() == "repo"
    suite_raw = _mapping(raw.get("suite", {} if repo_kind else None), "suite")
    matrix_declared = "matrix" in raw
    matrix_raw = raw.get("matrix", {})
    if matrix_declared and not matrix_raw:
        raise ValueError("matrix must declare cells when present")

    if "baseline" in subject_raw:
        raise ValueError("baseline must be top-level, not under [subject]")
    subject = _parse_subject(subject_raw)
    suite = _parse_suite(suite_raw, repo=subject.kind == "repo")
    if "baseline" in suite_raw:
        raise ValueError("baseline must be top-level, not under [suite]")
    baseline = str(raw["baseline"] if "baseline" in raw else "baseline").strip()
    if not baseline:
        raise ValueError("baseline must not be empty")

    cells = _extract_matrix_cells(matrix_raw)
    matrix_cells_explicit = bool(cells)
    if not cells:
        cells = ({
            "id": "arms-runner",
            "model": str(raw.get("model") or "gpt-4o-mini"),
            "provider": str(raw.get("provider") or "openai"),
            "harness": str(raw.get("harness") or "arms-runner"),
            "plugins": [],
        },)

    return SubjectDescriptor(
        path=path,
        subject=subject,
        suite=suite,
        baseline=baseline,
        matrix_cells=tuple(cells),
        matrix_cells_explicit=matrix_cells_explicit,
    )


def awareness_arm_specs(descriptor: SubjectDescriptor) -> list[str]:
    """Return arm specs for awareness/Q&A runs."""
    specs = [descriptor.baseline]
    specs.extend(_subject_specs(descriptor.subject, agentic=False))
    return _dedupe(specs)


def work_arm_specs(descriptor: SubjectDescriptor) -> list[str]:
    """Return arm specs for executable/task runs."""
    specs = [descriptor.baseline]
    specs.extend(_subject_specs(descriptor.subject, agentic=True))
    return _dedupe(specs)


def matrix_config(descriptor: SubjectDescriptor) -> dict[str, Any]:
    """Return a matrix.cells config suitable for arms matrix-run."""
    return {"matrix": {"cells": list(descriptor.matrix_cells)}}


def _load_mapping(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(path)
    suffix = path.suffix.lower()
    if suffix == ".json":
        raw = json.loads(path.read_text(encoding="utf-8"))
    elif suffix == ".toml":
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    else:
        raise ValueError(f"subject descriptor must be .json or .toml, got {path}")
    return _mapping(raw, "descriptor")


def _mapping(raw: Any, name: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError(f"{name} must be an object")
    return raw


def _parse_subject(raw: dict[str, Any]) -> SubjectArtifact:
    subject_id = str(raw.get("id") or "").strip()
    kind = str(raw.get("kind") or "").strip()
    ref = raw.get("ref")
    if not subject_id:
        raise ValueError("subject.id is required")
    if kind not in SUBJECT_KINDS:
        raise ValueError(f"subject.kind must be one of {sorted(SUBJECT_KINDS)}, got {kind!r}")

    members_raw = raw.get("members", [])
    members: list[SubjectMember] = []
    if members_raw:
        if kind != "bundle":
            raise ValueError("subject.members is only valid for kind='bundle'")
        if not isinstance(members_raw, list):
            raise ValueError("subject.members must be a list")
        for idx, item in enumerate(members_raw):
            item_raw = _mapping(item, f"subject.members[{idx}]")
            member_kind = str(item_raw.get("kind") or "").strip()
            member_ref = str(item_raw.get("ref") or "").strip()
            if member_kind not in SUBJECT_KINDS - NON_ARM_KINDS:
                raise ValueError(f"subject.members[{idx}].kind is invalid: {member_kind!r}")
            if not member_ref:
                raise ValueError(f"subject.members[{idx}].ref is required")
            members.append(SubjectMember(kind=member_kind, ref=member_ref))

    ref_s = None if ref is None else str(ref).strip()
    manifest = raw.get("manifest")
    if manifest is not None and kind != "repo":
        raise ValueError("subject.manifest is only valid for kind='repo'")
    manifest_s = None if manifest is None else str(manifest).strip() or None
    if kind != "bundle" and not ref_s:
        raise ValueError(f"subject.ref is required for kind={kind!r}")
    if kind == "bundle" and not members:
        raise ValueError("bundle subjects require subject.members")
    if kind == "bundle" and all(member.kind == "mcp" for member in members):
        raise ValueError("all-MCP bundles are not supported until MCP arm names can be made unique")
    return SubjectArtifact(
        id=subject_id, kind=kind, ref=ref_s, members=tuple(members), manifest=manifest_s
    )


def _parse_suite(raw: dict[str, Any], *, repo: bool = False) -> SubjectSuite:
    # A repository brings its own tasks through its manifest, so a repo subject
    # needs no product or question set.
    products = _string_list(raw.get("products", [] if repo else None), "suite.products")
    questions = _string_list(raw.get("questions", [] if repo else None), "suite.questions")
    tasks = _string_list(raw.get("tasks", []), "suite.tasks")
    if repo:
        return SubjectSuite(products=tuple(products), questions=tuple(questions), tasks=tuple(tasks))
    if not products:
        raise ValueError("suite.products must contain at least one product")
    if not questions:
        raise ValueError("suite.questions must contain at least one questions file")
    return SubjectSuite(products=tuple(products), questions=tuple(questions), tasks=tuple(tasks))


def _extract_matrix_cells(raw: Any) -> tuple[dict[str, Any], ...]:
    if not raw:
        return ()
    raw = _mapping(raw, "matrix")
    cells = raw.get("cells", [])
    if not isinstance(cells, list):
        raise ValueError("matrix.cells must be a list")
    if not cells:
        raise ValueError("matrix.cells must contain at least one cell when declared")
    out: list[dict[str, Any]] = []
    for idx, cell in enumerate(cells):
        cell_raw = _mapping(cell, f"matrix.cells[{idx}]")
        out.append(dict(cell_raw))
    return tuple(out)


def _string_list(raw: Any, name: str) -> list[str]:
    if isinstance(raw, str):
        values = [raw]
    elif isinstance(raw, list) and all(isinstance(item, str) for item in raw):
        values = raw
    else:
        raise ValueError(f"{name} must be a string or string list")
    return [item.strip() for item in values if item.strip()]


def _subject_specs(subject: SubjectArtifact, *, agentic: bool) -> list[str]:
    if subject.kind == "bundle":
        parts = []
        for member in subject.members:
            parts.extend(_member_specs(member.kind, member.ref, agentic=agentic))
        if len(parts) > 1 and parts[0].startswith("mcp:"):
            first_non_mcp = next((idx for idx, part in enumerate(parts) if not part.startswith("mcp:")), None)
            if first_non_mcp is not None:
                parts.insert(0, parts.pop(first_non_mcp))
        return ["+".join(parts)]
    return _member_specs(subject.kind, subject.ref or "", agentic=agentic)


def _member_specs(kind: str, ref: str, *, agentic: bool) -> list[str]:
    if kind == "skill":
        return [f"skill-agent:{ref}" if agentic else f"skill:{ref}"]
    if kind == "profile":
        return [f"profile:{ref}"]
    if kind == "mcp":
        normalized = ref.removeprefix("mcp:")
        return [f"agent:mcp:{normalized}" if agentic else f"mcp:{normalized}"]
    if kind == "doc-source":
        return [f"agent:{ref}" if agentic else f"docs:{ref}"]
    if kind == "repo":
        raise ValueError(
            "repo subjects have no prompt arm; validate them with `repo validate` "
            "(repository-mode runs are not implemented yet)"
        )
    raise ValueError(f"unsupported subject kind {kind!r}")


def _dedupe(values: list[str]) -> list[str]:
    out = []
    for value in values:
        if value not in out:
            out.append(value)
    return out
