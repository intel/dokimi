# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Where each artifact kind keeps its subject, model and time.

No field is shared by every kind (a questions set names its ``product``, an arms run its
``library_name``, a novelty run its ``artifact.ref``), so :data:`FIELDS` names, per kind of
:data:`dokimi.artifacts.SCHEMA_VERSIONS`, the schema paths to read. A kind without such a field
reads as empty: nothing is guessed from the file's path.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from dokimi.artifacts import SCHEMA_VERSIONS, SUPERSEDED_VERSIONS


@dataclass(frozen=True)
class KindFields:
    """Schema paths of one kind; each tuple is tried in order, the first that holds a value wins.

    A path is ``.``-joined keys; a key ending in ``[]`` steps into every item of a list, and the
    distinct values found are all kept (several subjects of a plan, mixed products of answers).
    """

    subject: tuple[str, ...] = ()
    model: tuple[str, ...] = ()
    time: tuple[str, ...] = ()


FIELDS: dict[str, KindFields] = {
    "questions": KindFields(subject=("product", "library"), model=("model",),
                            time=("generated_at",)),
    "answers": KindFields(subject=("answers[].library_name",), model=("model",),
                          time=("generated_at",)),
    "eval": KindFields(model=("judge_model",), time=("evaluated_at",)),
    "arms": KindFields(subject=("library_name",), model=("model",), time=("generated_at",)),
    "matrix_rollup": KindFields(subject=("library_name",), time=("generated_at",)),
    "plugin_delta": KindFields(subject=("library_name",), model=("model",)),
    "subject_scorecard": KindFields(subject=("subject.id",), time=("generated_at",)),
    "novelty": KindFields(subject=("artifact.ref",), model=("model",), time=("generated_at",)),
    "task_runs": KindFields(model=("model",), time=("generated_at",)),
    "explore_runs": KindFields(model=("model",), time=("generated_at",)),
    "guidance_reach": KindFields(subject=("repo.id",), model=("model",), time=("generated_at",)),
    "repo_readiness": KindFields(subject=("repo.id",), time=("generated_at",)),
    "tool_preference": KindFields(model=("model",), time=("generated_at",)),
    "repo_environment": KindFields(subject=("repo.tree",), time=("generated_at",)),
    "plan": KindFields(subject=("subjects[].id",), model=("models[]",), time=("generated_at",)),
    "result": KindFields(time=("generated_at",)),
    "repo_budget": KindFields(subject=("tree",)),
    "task_inspect": KindFields(subject=("root",)),
}

#: Every schema_version an artifact may carry (current and superseded) -> its kind.
VERSION_KINDS: dict[str, str] = {
    **{v: kind for kind, versions in SUPERSEDED_VERSIONS.items() for v in versions},
    **{v: kind for kind, v in SCHEMA_VERSIONS.items()},
}


def _values(node: Any, keys: list[str]) -> list[str]:
    if not keys:
        return [node] if isinstance(node, str) and node else []
    key, rest = keys[0], keys[1:]
    if key.endswith("[]"):
        items = node.get(key[:-2]) if isinstance(node, dict) else None
        return [v for item in items for v in _values(item, rest)] if isinstance(items, list) else []
    return _values(node.get(key), rest) if isinstance(node, dict) else []


def read_field(data: dict, paths: tuple[str, ...]) -> tuple[str, ...]:
    """The distinct non-empty strings at the first of ``paths`` that holds any, in order."""
    for path in paths:
        found = tuple(dict.fromkeys(_values(data, path.split("."))))
        if found:
            return found
    return ()
